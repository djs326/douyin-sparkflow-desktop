import asyncio
import hmac
import os
import shutil
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
import uvicorn
from playwright.async_api import async_playwright

from core.browser import configure_playwright_environment
from core.login import collect_login_result
from utils.config import Environment, data_dir, get_environment, login_desktop_auth_token
from utils.web_middleware import localhost_only_middleware

# 打包版必须指向 exe 旁的 chrome\ 目录，否则 Playwright 找不到内置 Chromium
configure_playwright_environment()


REMOTE_LOGIN_URL = "https://creator.douyin.com/"
WWW_SELF_URL = "https://www.douyin.com/user/self"


def _default_profile_dir():
    # 打包版 → 数据目录 login-profile；开发版 → 仓库 state/login-profile
    if get_environment() == Environment.PACKED:
        return data_dir() / "login-profile"
    return (
        Path(__file__).resolve().parents[1] / "state" / "login-profile"
        if os.name == "nt"
        else Path("/data/login-profile")
    )


DEFAULT_PROFILE_DIR = _default_profile_dir()
PROFILE_DIR = Path(os.getenv("LOGIN_PROFILE_DIR", str(DEFAULT_PROFILE_DIR))).expanduser()
LOGIN_DESKTOP_MODE = str(
    os.getenv("LOGIN_DESKTOP_MODE", "native" if os.name == "nt" else "novnc")
).strip().lower()
if LOGIN_DESKTOP_MODE not in {"native", "novnc"}:
    LOGIN_DESKTOP_MODE = "native" if os.name == "nt" else "novnc"
# 内嵌二维码模式：Chromium 窗口启动在屏幕外（不打扰用户），
# 应用窗口内展示二维码完成扫码；需要处理验证码时可 focus 把窗口移回屏幕。
LOGIN_DESKTOP_HIDDEN_WINDOW = str(os.getenv("LOGIN_DESKTOP_HIDDEN_WINDOW", "")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LOGIN_HIDDEN_POSITION = "-32000,-32000"
IDLE_TIMEOUT_SECONDS = max(300, int(os.getenv("LOGIN_DESKTOP_IDLE_TIMEOUT_SECONDS", "1800")))
STOP_AFTER_EXPORT_SECONDS = max(0, int(os.getenv("LOGIN_DESKTOP_STOP_AFTER_EXPORT_SECONDS", "60")))
STATUS_CACHE_SECONDS = max(1, int(os.getenv("LOGIN_DESKTOP_STATUS_CACHE_SECONDS", "15")))
LOGIN_NETWORK_MODE = str(os.getenv("LOGIN_DESKTOP_PROXY_MODE", "auto")).strip().lower()
if LOGIN_NETWORK_MODE not in {"auto", "direct", "proxy"}:
    LOGIN_NETWORK_MODE = "auto"


def _default_login_proxy_server():
    # 桌面版没有 Mihomo 编排：默认不配置代理（直连）；仅容器/服务器环境使用 proxy:7890
    if get_environment() == Environment.PACKED or os.name == "nt":
        return ""
    return "http://proxy:7890"


LOGIN_PROXY_SERVER = str(
    os.getenv("LOGIN_DESKTOP_PROXY") or _default_login_proxy_server()
).strip()
LOGIN_PREFLIGHT_TIMEOUT_SECONDS = max(
    3, int(os.getenv("LOGIN_DESKTOP_PREFLIGHT_TIMEOUT_SECONDS", "15"))
)
LOGIN_NETWORK_CACHE_SECONDS = max(
    0, int(os.getenv("LOGIN_DESKTOP_NETWORK_CACHE_SECONDS", "30"))
)
GENERIC_WWW_NAMES = {
    "",
    "我的",
    "我",
    "抖音官网账号",
    "精选",
    "推荐",
    "搜索",
    "关注",
    "朋友",
    "直播",
    "放映厅",
    "短剧",
    "小游戏",
    "客户端",
    "通知",
    "私信",
    "投稿",
    "海量优质视频内容",
    "抖音精选电脑版",
}


def _move_hidden_login_window_back():
    """把屏幕外的 Chromium 登录窗口移回屏幕中央（内嵌二维码模式的兜底）。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004

        def _enum_callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.left > -30000 or rect.top > -30000:
                return True
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if "Chrome_WidgetWin_1" not in class_name.value:
                return True
            user32.SetWindowPos(hwnd, None, 100, 60, 0, 0, SWP_NOSIZE | SWP_NOZORDER)
            return False

        user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_enum_callback), 0)
    except Exception:
        pass


class LoginNetworkError(RuntimeError):
    """Raised when the login browser cannot reach Douyin."""

    def __init__(self, message, *, checks=None):
        super().__init__(message)
        self.checks = checks or {}


def _safe_proxy_label(proxy_server):
    if not proxy_server:
        return ""
    try:
        parsed = urlsplit(proxy_server)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{host}{port}" if host else "configured proxy"
    except ValueError:
        return "configured proxy"


def _probe_login_target(proxy_server=None, timeout_seconds=15):
    """Probe Douyin without inheriting the process proxy environment."""
    if proxy_server:
        handlers = [
            urllib.request.ProxyHandler(
                {"http": proxy_server, "https": proxy_server}
            )
        ]
    else:
        handlers = [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        REMOTE_LOGIN_URL,
        headers={"User-Agent": "DouYinSparkFlow-login-preflight/1"},
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            response.read(256)
            status = int(getattr(response, "status", 200))
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
        return {
            "ok": True,
            "status": status,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__
        if proxy_server:
            error = error.replace(proxy_server, _safe_proxy_label(proxy_server))
        return {
            "ok": False,
            "error": error[:240],
            "latency_ms": round((time.monotonic() - started) * 1000),
        }


class LoginDesktopManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._page_operation_lock = asyncio.Lock()
        self.playwright = None
        self.context = None
        self.page = None
        self._last_activity = time.monotonic()
        self._status_cache = None
        self._status_checked_at = 0.0
        self._idle_monitor_task = None
        self._scheduled_stop_task = None
        self._network_route = None
        self._network_checks = {}
        self._network_checked_at = 0.0

    def _network_payload(self):
        route = dict(self._network_route or {})
        return {
            "mode": LOGIN_NETWORK_MODE,
            "selected": route.get("mode", ""),
            "proxy": _safe_proxy_label(LOGIN_PROXY_SERVER) if route.get("mode") == "proxy" else "",
            "checked_at": route.get("checked_at", ""),
            "checks": dict(self._network_checks),
        }

    def _invalidate_network_route(self):
        self._network_route = None
        self._network_checked_at = 0.0

    async def _select_network_route(self, *, force=False):
        now = time.monotonic()
        if (
            not force
            and self._network_route
            and now - self._network_checked_at < LOGIN_NETWORK_CACHE_SECONDS
        ):
            return dict(self._network_route)

        checks = {}
        candidates = []
        if LOGIN_NETWORK_MODE in {"auto", "direct"}:
            candidates.append(("direct", None))
        if LOGIN_NETWORK_MODE in {"auto", "proxy"} and LOGIN_PROXY_SERVER:
            candidates.append(("proxy", LOGIN_PROXY_SERVER))

        for mode, proxy_server in candidates:
            result = await asyncio.to_thread(
                _probe_login_target,
                proxy_server,
                LOGIN_PREFLIGHT_TIMEOUT_SECONDS,
            )
            checks[mode] = result
            if result.get("ok"):
                route = {
                    "mode": mode,
                    "proxy": _safe_proxy_label(proxy_server),
                    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                self._network_checks = checks
                self._network_route = route
                self._network_checked_at = now
                return dict(route)

        self._network_checks = checks
        self._network_route = None
        self._network_checked_at = now
        if LOGIN_NETWORK_MODE == "direct":
            message = "无法直连抖音创作者中心，请检查服务器网络出口"
        elif LOGIN_NETWORK_MODE == "proxy":
            message = f"代理 {_safe_proxy_label(LOGIN_PROXY_SERVER)} 无法访问抖音创作者中心"
        else:
            message = "直连和代理都无法访问抖音创作者中心"
        raise LoginNetworkError(message, checks=checks)

    async def network_preflight(self, *, force=True):
        try:
            route = await self._select_network_route(force=force)
            return {
                "ok": True,
                "route": route,
                "network": self._network_payload(),
            }
        except LoginNetworkError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "network": self._network_payload(),
            }

    def mark_activity(self):
        self._last_activity = time.monotonic()
        self._status_cache = None
        self._status_checked_at = 0.0
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()
        self._scheduled_stop_task = None

    async def start_idle_monitor(self):
        if self._idle_monitor_task and not self._idle_monitor_task.done():
            return
        self._idle_monitor_task = asyncio.create_task(self._idle_monitor())

    async def stop_idle_monitor(self):
        if self._idle_monitor_task and not self._idle_monitor_task.done():
            self._idle_monitor_task.cancel()
            await asyncio.gather(self._idle_monitor_task, return_exceptions=True)
        self._idle_monitor_task = None
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()
            await asyncio.gather(self._scheduled_stop_task, return_exceptions=True)
        self._scheduled_stop_task = None

    async def _idle_monitor(self):
        while True:
            await asyncio.sleep(60)
            if self.context and time.monotonic() - self._last_activity >= IDLE_TIMEOUT_SECONDS:
                await self.stop(clear_profile=False)

    def schedule_stop_after_export(self):
        if STOP_AFTER_EXPORT_SECONDS <= 0:
            return
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()

        async def stop_later():
            await asyncio.sleep(STOP_AFTER_EXPORT_SECONDS)
            await self.stop(clear_profile=False)

        self._scheduled_stop_task = asyncio.create_task(stop_later())

    async def reduce_page_activity(self, page):
        try:
            await page.emulate_media(reduced_motion="reduce")
            await page.add_style_tag(
                content="""
                *, *::before, *::after {
                  animation-duration: 0s !important;
                  animation-iteration-count: 1 !important;
                  transition-duration: 0s !important;
                  scroll-behavior: auto !important;
                }
                video, canvas[class*="main-animation"] {
                  visibility: hidden !important;
                }
                """
            )
        except Exception:
            pass

    async def start(self):
        self.mark_activity()
        async with self._lock:
            if self.context and not self._context_is_closed():
                return
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            route = await self._select_network_route()
            self.playwright = await async_playwright().start()
            if LOGIN_DESKTOP_HIDDEN_WINDOW and os.name == "nt":
                launch_args = [
                    f"--window-position={LOGIN_HIDDEN_POSITION}",
                    "--window-size=1280,900",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication",
                ]
            else:
                launch_args = [
                    "--start-maximized",
                    "--window-position=0,0",
                    "--window-size=1600,1000",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication",
                ]
            if os.name != "nt":
                launch_args.extend(
                    [
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-gpu-compositing",
                        "--disable-software-rasterizer",
                        "--disable-accelerated-2d-canvas",
                        "--disable-accelerated-video-decode",
                        "--renderer-process-limit=2",
                    ]
                )
            launch_options = {
                "headless": False,
                "viewport": {"width": 1600, "height": 1000},
                "args": launch_args,
            }
            if route["mode"] == "proxy":
                launch_options["proxy"] = {"server": LOGIN_PROXY_SERVER}
            else:
                launch_args.append("--no-proxy-server")
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                **launch_options,
            )
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    def _context_is_closed(self):
        return not self.context or getattr(self.context, "_impl_obj", None) is None

    async def _get_active_page(self):
        await self.ensure_running()
        try:
            if self.page and not self.page.is_closed():
                return self.page
        except Exception:
            pass
        try:
            for candidate in self.context.pages:
                if not candidate.is_closed():
                    self.page = candidate
                    return candidate
        except Exception:
            pass
        self.page = await self.context.new_page()
        return self.page

    async def stop(self, clear_profile=False):
        async with self._lock:
            if self.page:
                try:
                    await self.page.close()
                except Exception:
                    pass
                self.page = None
            if self.context:
                try:
                    await self.context.close()
                except Exception:
                    pass
                self.context = None
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            if clear_profile and PROFILE_DIR.exists():
                shutil.rmtree(PROFILE_DIR, ignore_errors=True)
            self._status_cache = None
            self._status_checked_at = 0.0

    async def reset(self):
        self.mark_activity()
        await self.stop(clear_profile=True)
        await self.start()

    async def ensure_running(self):
        if not self.context or self._context_is_closed():
            await self.start()

    async def focus_browser(self):
        self.mark_activity()
        page = await self._get_active_page()
        if LOGIN_DESKTOP_HIDDEN_WINDOW and os.name == "nt":
            # 内嵌模式：把屏幕外的登录窗口移回屏幕中央，便于处理验证码等场景
            _move_hidden_login_window_back()
        try:
            await page.bring_to_front()
        except Exception:
            pass
        return {
            "ok": True,
            "url": page.url,
            "mode": LOGIN_DESKTOP_MODE,
            "network": self._network_payload(),
        }

    async def status(self):
        now = time.monotonic()
        if self._status_cache is not None and now - self._status_checked_at < STATUS_CACHE_SECONDS:
            return dict(self._status_cache)

        logged_in = False
        username = ""
        unique_id = ""
        current_url = ""

        if not self.context or self._context_is_closed():
            payload = {
                "running": False,
                "logged_in": False,
                "username": "",
                "unique_id": "",
                "current_url": "",
                "profile_dir": str(PROFILE_DIR),
                "network": self._network_payload(),
            }
            self._status_cache = payload
            self._status_checked_at = now
            return dict(payload)

        page = None
        try:
            if self.page and not self.page.is_closed():
                page = self.page
            else:
                for candidate in self.context.pages:
                    if not candidate.is_closed():
                        self.page = candidate
                        page = candidate
                        break
        except Exception:
            self.page = None
            self.context = None
            payload = {
                "running": False,
                "logged_in": False,
                "username": "",
                "unique_id": "",
                "current_url": "",
                "profile_dir": str(PROFILE_DIR),
                "network": self._network_payload(),
            }
            self._status_cache = payload
            self._status_checked_at = now
            return dict(payload)

        if page:
            current_url = page.url
            if not self._page_operation_lock.locked():
                try:
                    result = await collect_login_result(page, self.context, timeout_ms=1000)
                    logged_in = True
                    username = result["username"]
                    unique_id = result["unique_id"]
                except Exception:
                    pass

        payload = {
            "running": True,
            "logged_in": logged_in,
            "username": username,
            "unique_id": unique_id,
            "current_url": current_url,
            "profile_dir": str(PROFILE_DIR),
            "network": self._network_payload(),
        }
        self._status_cache = payload
        self._status_checked_at = now
        return dict(payload)

    async def open_login(self):
        self.mark_activity()
        try:
            await asyncio.wait_for(self._page_operation_lock.acquire(), timeout=5)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("登录页正忙，请稍后重试") from exc
        try:
            page = await self._get_active_page()
            if page.url.startswith(REMOTE_LOGIN_URL):
                return {"ok": True, "url": page.url, "network": self._network_payload()}
            refresh_url = f"{REMOTE_LOGIN_URL}?qr_refresh={int(time.time() * 1000)}"
            try:
                await page.goto(refresh_url, wait_until="commit", timeout=30000)
            except Exception:
                await self.stop(clear_profile=False)
                await self.start()
                page = await self._get_active_page()
                await page.goto(refresh_url, wait_until="commit", timeout=30000)
            return {"ok": True, "url": page.url, "network": self._network_payload()}
        finally:
            self._page_operation_lock.release()

    async def refresh_login_qr(self):
        self.mark_activity()
        try:
            await asyncio.wait_for(self._page_operation_lock.acquire(), timeout=5)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("登录页正忙，请稍后重试") from exc
        try:
            return await self._refresh_login_qr_locked()
        finally:
            self._page_operation_lock.release()

    async def _refresh_login_qr_locked(self):
        refresh_url = f"{REMOTE_LOGIN_URL}?qr_refresh={int(time.time() * 1000)}"
        try:
            page = await self._get_active_page()
            await page.goto(refresh_url, wait_until="commit", timeout=30000)
        except Exception:
            await self.reset()
            page = await self._get_active_page()
            await page.goto(refresh_url, wait_until="commit", timeout=30000)

        deadline = asyncio.get_running_loop().time() + 45
        logged_in = False
        qr_ready = False
        while asyncio.get_running_loop().time() < deadline:
            for selector in (
                'img[class*="qrcode"]',
                'img[src^="data:image/png;base64"]',
            ):
                candidates = page.locator(selector)
                for index in range(await candidates.count()):
                    qr = candidates.nth(index)
                    try:
                        if not await qr.is_visible():
                            continue
                        box = await qr.bounding_box()
                        if not box or box["width"] < 120 or box["height"] < 120:
                            continue
                        ratio = box["width"] / max(1, box["height"])
                        if 0.8 <= ratio <= 1.25:
                            qr_ready = True
                            break
                    except Exception:
                        pass
                if qr_ready:
                    break

            if "/creator-micro/" in page.url:
                logged_in = True
                break
            try:
                await collect_login_result(page, self.context, timeout_ms=800)
                logged_in = True
                break
            except Exception:
                pass
            await asyncio.sleep(0.75)

        if not qr_ready and not logged_in:
            raise RuntimeError("Douyin login page did not expose a QR code or a logged-in session")

        await self.reduce_page_activity(page)
        if qr_ready:
            try:
                await page.wait_for_function(
                    "() => !/\u4e8c\u7ef4\u7801\u5931\u6548|\u4e8c\u7ef4\u7801\u8fc7\u671f/.test(document.body?.innerText || '')",
                    timeout=30000,
                )
            except Exception:
                pass
        return {"ok": True, "url": page.url, "logged_in": logged_in, "qr_ready": qr_ready}

    async def export(self):
        self.mark_activity()
        page = await self._get_active_page()
        result = await collect_login_result(page, self.context, timeout_ms=5000)
        self.schedule_stop_after_export()
        return result


def _clean_www_display_name(value):
    raw = str(value or "").replace("\u200b", "").replace("\ufeff", "")
    name = " ".join(raw.split()).strip(" -_｜|·•")
    if not name or name in GENERIC_WWW_NAMES:
        return ""
    if len(name) > 40:
        return ""
    if any(token in name for token in ("登录", "注册", "关注", "粉丝", "获赞", "作品", "喜欢", "收藏", "观看历史", "海量优质视频", "抖音旗下")):
        return ""
    return name


async def collect_www_identity_from_page(page):
    return await page.evaluate(
        r"""() => {
            const normalize = (value) => String(value || "")
                .replace(/[\u200b\u200c\u200d\ufeff]/g, "")
                .replace(/\s+/g, " ")
                .trim();
            const bad = new Set(["", "我的", "我", "抖音官网账号", "精选", "推荐", "搜索", "关注", "朋友", "直播", "放映厅", "短剧", "小游戏", "客户端", "通知", "私信", "投稿"]);
            const candidates = [];
            const add = (value, source) => {
                const text = normalize(value).replace(/^@+/, "").trim();
                if (!text || bad.has(text) || text.length > 40) return;
                if (/登录|注册|关注|粉丝|获赞|作品|喜欢|收藏|观看历史/.test(text)) return;
                candidates.push({ text, source });
            };

            add(document.querySelector('[data-e2e="user-title"]')?.innerText, "data-e2e=user-title");
            add(document.querySelector('[class*="userName"], [class*="UserName"], [class*="nickname"], [class*="Nickname"], h1')?.innerText, "profile-name-selector");
            add(document.title.split(/[｜|\-]/)[0], "document-title");
            add(document.querySelector('meta[property="og:title"]')?.content?.split(/[｜|\-]/)[0], "og:title");

            const selfLink = document.querySelector('a[href*="/user/self"]');
            if (selfLink) {
                const root = selfLink.closest('div')?.parentElement?.parentElement || selfLink;
                const lines = normalize(root.innerText).split(/关注|粉丝|获赞|我的喜欢|我的收藏|观看历史|稍后再看|我的作品|我的预约|我的订单|退出登录/);
                add(lines[0], "self-link-root");
            }

            if (location.pathname.includes('/user/')) {
                add(document.querySelector('meta[name="description"]')?.content?.split(/[，,。|｜-]/)[0], "description");
            }

            return {
                url: location.href,
                title: document.title,
                profileHref: selfLink?.href || "",
                candidates,
            };
        }"""
    )


async def collect_www_login_result(page, context):
    cookies = await context.cookies()
    try:
        await page.goto(WWW_SELF_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    identity = await collect_www_identity_from_page(page)
    username = ""
    for item in identity.get("candidates") or []:
        username = _clean_www_display_name(item.get("text"))
        if username:
            break

    if not username:
        identity = await collect_www_identity_from_page(page)
        for item in identity.get("candidates") or []:
            username = _clean_www_display_name(item.get("text"))
            if username:
                break

    if not username:
        username = "抖音官网账号"
    uid_cookie = ""
    for cookie in cookies:
        if cookie.get("name") in {"uid_tt", "uid_tt_ss", "sid_uid", "passport_csrf_token"}:
            uid_cookie = str(cookie.get("value") or "")
            if uid_cookie:
                break
    suffix = "".join(ch for ch in uid_cookie if ch.isalnum())[:24]
    unique_id = f"web-self-{suffix or username}"
    return {
        "unique_id": unique_id,
        "username": username,
        "cookies": cookies,
    }


manager = LoginDesktopManager()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await manager.start_idle_monitor()
    try:
        yield
    finally:
        await manager.stop_idle_monitor()
        await manager.stop(clear_profile=False)


app = FastAPI(title="Douyin Login Desktop", lifespan=lifespan)

# 本服务可导出完整抖音 cookies 并远程操控登录浏览器，必须做访问控制：
# 1) 所有端点要求携带共享 token（launcher 与 webui 经同一 data_dir 自动互通）
# 2) 拒绝非本机 Host 头（防 DNS rebinding）
AUTH_TOKEN = login_desktop_auth_token()


@app.middleware("http")
async def _auth_and_host_guard(request: Request, call_next):
    if request.url.path != "/health" and not _request_authorized(request):
        return Response("Forbidden: missing or invalid auth token", status_code=403)
    return await localhost_only_middleware(request, call_next)


def _request_authorized(request: Request) -> bool:
    supplied = request.headers.get("X-Login-Desktop-Token", "")
    return bool(supplied) and hmac.compare_digest(supplied, AUTH_TOKEN)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/preflight")
async def preflight():
    result = await manager.network_preflight(force=True)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@app.get("/status")
async def status():
    return await manager.status()


@app.post("/open-login")
async def open_login():
    try:
        return await manager.open_login()
    except LoginNetworkError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "LOGIN_NETWORK_UNAVAILABLE", "message": str(exc), "checks": exc.checks},
        ) from exc


@app.post("/reset")
async def reset():
    await manager.reset()
    return {"ok": True}


@app.post("/close")
async def close():
    await manager.stop(clear_profile=True)
    return {"ok": True}


@app.post("/focus")
async def focus():
    return await manager.focus_browser()


@app.post("/refresh-qr")
async def refresh_qr():
    try:
        return await manager.refresh_login_qr()
    except LoginNetworkError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "LOGIN_NETWORK_UNAVAILABLE", "message": str(exc), "checks": exc.checks},
        ) from exc


@app.post("/export")
async def export():
    page = await manager._get_active_page()
    try:
        result = await manager.export()
    except Exception as creator_exc:
        try:
            result = await collect_www_login_result(page, manager.context)
        except Exception as www_exc:
            raise HTTPException(status_code=400, detail=f"创作者导出失败：{creator_exc}；www 导出失败：{www_exc}")
    return {"ok": True, "result": result}


@app.get("/qr")
async def login_qr():
    if manager._page_operation_lock.locked():
        raise HTTPException(status_code=503, detail="登录页正忙，请稍后重试")
    try:
        page = await manager._get_active_page()
    except LoginNetworkError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "LOGIN_NETWORK_UNAVAILABLE", "message": str(exc), "checks": exc.checks},
        ) from exc
    expired = await page.locator('[class*="qrcode_expired"]').count()
    if expired and await page.locator('[class*="qrcode_expired"]').first.is_visible():
        raise HTTPException(status_code=409, detail="登录二维码已过期")
    selectors = (
        'img[class*="qrcode"]',
        'img[src^="data:image/png;base64"]',
    )
    for selector in selectors:
        candidates = page.locator(selector)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                box = await candidate.bounding_box()
                if not box or box["width"] < 120 or box["height"] < 120:
                    continue
                ratio = box["width"] / max(1, box["height"])
                if not 0.8 <= ratio <= 1.25:
                    continue
                data = await candidate.screenshot(type="png")
                return Response(
                    content=data,
                    media_type="image/png",
                    headers={"Cache-Control": "no-store, max-age=0"},
                )
            except Exception:
                continue
    raise HTTPException(status_code=202, detail="登录二维码正在生成中", headers={"Retry-After": "2"})


@app.get("/debug/screenshot")
async def debug_screenshot():
    page = await manager._get_active_page()
    data = await page.screenshot(full_page=False, type="png")
    return Response(content=data, media_type="image/png")


@app.get("/debug/snapshot")
async def debug_snapshot():
    page = await manager._get_active_page()
    items = await page.evaluate(
        r"""() => {
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            };
            const textOf = (el) => String(el.innerText || el.textContent || el.getAttribute('aria-label') || el.title || '').replace(/\s+/g, ' ').trim();
            const nodes = [...document.querySelectorAll('button, a, [role="button"], [aria-label], input, textarea, [contenteditable="true"], [class*="message"], [class*="chat"], [class*="im"]')];
            return nodes.filter(visible).slice(0, 300).map((el, i) => {
                const r = el.getBoundingClientRect();
                return {
                    i,
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    aria: el.getAttribute('aria-label') || '',
                    title: el.title || '',
                    text: textOf(el).slice(0, 120),
                    cls: String(el.className || '').slice(0, 120),
                    contenteditable: el.getAttribute('contenteditable') || '',
                    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}
                };
            });
        }"""
    )
    return {"url": page.url, "title": await page.title(), "items": items}


@app.post("/debug/action")
async def debug_action(request: Request):
    page = await manager._get_active_page()
    payload = await request.json()
    action = payload.get("action")
    if action == "click_text":
        text = str(payload.get("text") or "")
        exact = bool(payload.get("exact", False))
        await page.get_by_text(text, exact=exact).first.click(timeout=int(payload.get("timeout", 5000)))
    elif action == "click_at":
        await page.mouse.click(float(payload["x"]), float(payload["y"]))
    elif action == "wheel":
        await page.mouse.wheel(float(payload.get("dx", 0)), float(payload.get("dy", 0)))
    elif action == "type":
        await page.keyboard.type(str(payload.get("text") or ""), delay=int(payload.get("delay", 20)))
    elif action == "press":
        await page.keyboard.press(str(payload.get("key") or "Enter"))
    elif action == "goto":
        await page.goto(str(payload.get("url") or REMOTE_LOGIN_URL), wait_until="commit", timeout=15000)
    elif action == "eval":
        result = await page.evaluate(str(payload.get("script") or "undefined"))
        return {"ok": True, "result": result, "url": page.url}
    elif action == "list_frames":
        frames = []
        for fr in page.frames:
            frames.append({"url": fr.url, "name": fr.name})
        return {"ok": True, "frames": frames, "url": page.url}
    elif action == "eval_in_frame":
        frame_url_match = str(payload.get("frame_url") or "")
        script = str(payload.get("script") or "undefined")
        target_frame = None
        for fr in page.frames:
            if frame_url_match and frame_url_match in fr.url:
                target_frame = fr
                break
        if not target_frame:
            return {"ok": False, "error": f"no frame matching '{frame_url_match}' found", "url": page.url}
        result = await target_frame.evaluate(script)
        return {"ok": True, "result": result, "url": page.url, "frame_url": target_frame.url}
    elif action == "click_in_frame":
        frame_url_match = str(payload.get("frame_url") or "")
        selector = str(payload.get("selector") or "")
        target_frame = None
        for fr in page.frames:
            if frame_url_match and frame_url_match in fr.url:
                target_frame = fr
                break
        if not target_frame:
            return {"ok": False, "error": f"no frame matching '{frame_url_match}' found"}
        await target_frame.click(selector, timeout=int(payload.get("timeout", 5000)))
        return {"ok": True, "url": page.url, "frame_url": target_frame.url}
    elif action == "type_in_frame":
        frame_url_match = str(payload.get("frame_url") or "")
        selector = str(payload.get("selector") or "")
        text = str(payload.get("text") or "")
        target_frame = None
        for fr in page.frames:
            if frame_url_match and frame_url_match in fr.url:
                target_frame = fr
                break
        if not target_frame:
            return {"ok": False, "error": "no frame found"}
        await target_frame.fill(selector, text, timeout=int(payload.get("timeout", 5000)))
        return {"ok": True, "url": page.url}
    else:
        raise HTTPException(status_code=400, detail=f"未知操作 {action!r}")
    return {"ok": True, "url": page.url}


# ---- Network capture (codex-added observability) ----
_net_log = []
_net_capturing = False
_net_max = 500

@app.post("/debug/net_capture")
async def net_capture(request: Request):
    global _net_capturing
    payload = await request.json()
    action_type = payload.get("type", "start")
    page = await manager._get_active_page()
    if action_type == "start":
        _net_log.clear()
        _net_capturing = True

        async def on_request(req):
            if not _net_capturing:
                return
            if len(_net_log) >= _net_max:
                return
            try:
                _net_log.append({
                    "ts": __import__("time").time(),
                    "phase": "request",
                    "method": req.method,
                    "url": req.url,
                    "headers": dict(req.headers),
                })
            except Exception:
                pass

        async def on_response(resp):
            if not _net_capturing:
                return
            if len(_net_log) >= _net_max:
                return
            try:
                body_preview = None
                try:
                    body_preview = (await resp.text())[:300]
                except Exception:
                    pass
                _net_log.append({
                    "ts": __import__("time").time(),
                    "phase": "response",
                    "url": resp.url,
                    "status": resp.status,
                    "headers": dict(resp.headers),
                    "body": body_preview,
                })
            except Exception:
                pass

        page.on("request", lambda req: __import__("asyncio").ensure_future(on_request(req)))
        page.on("response", lambda resp: __import__("asyncio").ensure_future(on_response(resp)))
        return {"ok": True, "msg": "capture started"}
    elif action_type == "stop":
        _net_capturing = False
        return {"ok": True, "count": len(_net_log)}
    elif action_type == "get":
        return {"ok": True, "count": len(_net_log), "log": _net_log.copy()}
    elif action_type == "clear":
        _net_log.clear()
        return {"ok": True}
    return {"ok": False, "error": "unknown type"}

@app.get("/debug/net_log")
async def get_net_log():
    return {"count": len(_net_log), "capturing": _net_capturing, "log": _net_log.copy()}

if __name__ == "__main__":
    # 只绑定本机回环地址：本服务可导出 cookies，绝不可暴露到局域网。
    # token 落在 data_dir()/state/login_desktop_auth.token，webui 自动读取。
    print(
        f"[login_desktop_server] auth token: {AUTH_TOKEN}\n"
        f"[login_desktop_server] webui 通过共享数据目录自动携带该 token；"
        f"外部调用需请求头 X-Login-Desktop-Token。",
        flush=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("LOGIN_DESKTOP_API_PORT", "18090")), reload=False)
