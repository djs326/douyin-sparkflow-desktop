import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from playwright.async_api import async_playwright
from rich.console import Console

from utils.config import DEBUG, Environment, data_dir, get_app_settings, get_environment, repo_root
from utils.process import hidden_startupinfo


console = Console()
# 与 config/tasks 共用 "app" logger（打包版 stderr 是 devnull，console.print 用户不可见）
logger = logging.getLogger("app")
PLAYWRIGHT_BROWSERS_PATH = "../chrome"


def _local_browser_bundle_path():
    return Path(__file__).resolve().parent / PLAYWRIGHT_BROWSERS_PATH


def system_browser_executable():
    """探测用户本机的 Chromium 内核浏览器（Edge → Chrome），供 Playwright 直接调用。

    桌面版不再内置浏览器，优先用 Windows 自带的 Edge，其次 Chrome。
    """
    candidates = []
    if os.name == "nt":
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        for name in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def configure_playwright_environment():
    # 桌面版使用用户本机浏览器（system_browser_executable 探测），
    # 不再需要内置浏览器路径；保留空实现以兼容既有调用点。
    return


def _headless_for(GUI=False):
    headful_env = str(os.getenv("SPARKFLOW_BROWSER_HEADFUL") or "").strip().lower()
    if headful_env in {"1", "true", "yes", "on"}:
        return False

    headless = not GUI
    if get_environment() == Environment.LOCAL and DEBUG:
        headless = False
    return headless


def _browser_args():
    return [
        "--disable-dev-shm-usage",
        "--no-sandbox",
    ]


def _douyin_network_mode():
    settings = get_app_settings(force_reload=True)
    return str(
        os.getenv("SPARKFLOW_DOUYIN_NETWORK_MODE")
        or settings.get("douyin_network_mode", "direct")
    ).strip().lower()


def douyin_network_modes():
    # Direct is the default; Mihomo is the fallback unless explicitly selected.
    mode = _douyin_network_mode()
    if mode == "mihomo":
        return ("mihomo",)
    return ("direct", "mihomo")


def _douyin_browser_proxy(network_mode=None):
    # Return an explicit proxy URL for Douyin traffic, or None for direct.
    settings = get_app_settings(force_reload=True)
    mode = str(network_mode or _douyin_network_mode()).strip().lower()
    if mode != "mihomo":
        return None
    return str(
        os.getenv("SPARKFLOW_DOUYIN_PROXY_URL")
        or settings.get("douyin_proxy_url", "http://proxy:7890")
    ).strip() or None


def _browser_launch_options(GUI=False, network_mode=None):
    args = _browser_args()
    proxy = _douyin_browser_proxy(network_mode=network_mode)
    options = {}
    executable = system_browser_executable()
    if executable:
        options["executable_path"] = executable
    if proxy:
        options.update(
            {
                "headless": _headless_for(GUI),
                "args": args,
                "proxy": {"server": proxy},
            }
        )
    else:
        args.append("--no-proxy-server")
        options.update(
            {
                "headless": _headless_for(GUI),
                "args": args,
            }
        )
    return options


async def select_douyin_network_mode(target_url):
    # Select the first route that can load the target before a task starts.
    failures = []
    for network_mode in douyin_network_modes():
        playwright = browser = page = None
        try:
            playwright, browser = await get_browser(network_mode=network_mode)
            page = await browser.new_page()
            response = await page.goto(target_url, wait_until="commit", timeout=30000)
            status = response.status if response is not None else None
            if status is not None and status < 500:
                return network_mode
            failures.append(f"{network_mode}: HTTP {status}")
        except Exception as exc:
            failures.append(f"{network_mode}: {exc}")
        finally:
            if page:
                await page.close()
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()
    raise RuntimeError(f"Douyin network preflight failed: {'; '.join(failures)}")


def sanitize_profile_name(value):
    raw = str(value or "").strip()
    if not raw:
        raw = "unknown"
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", raw)
    safe = safe.strip("._-") or "unknown"
    return safe[:80]


def browser_profile_root(root=None):
    configured = (
        root
        or os.getenv("SPARKFLOW_BROWSER_PROFILE_ROOT")
        or ""
    )
    if configured:
        return Path(configured)
    # 未显式指定时按环境解析：
    # - 打包版 → 数据目录 browser-profiles
    # - 开发版 → 仓库 state/browser-profiles
    if get_environment() == Environment.PACKED:
        return data_dir() / "browser-profiles"
    return repo_root() / "state" / "browser-profiles"


async def install_browser():
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            startupinfo=hidden_startupinfo(),
        )
        console.print("[bold green]Browser install completed. Please run the command again.[/bold green]")
    except subprocess.CalledProcessError as exc:
        console.print(f"[bold red]Browser install failed: {exc}[/bold red]")


async def get_browser(GUI=False, network_mode=None):
    configure_playwright_environment()

    try:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(**_browser_launch_options(GUI, network_mode=network_mode))
        except Exception:
            try:
                await playwright.stop()
            except Exception:
                pass
            raise
        return playwright, browser
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) and get_environment() != Environment.GITHUBACTION:
            if get_environment() == Environment.PACKED:
                # L6：打包版不内置浏览器（用系统 Edge/Chrome），sys.executable -m playwright install
                # 在 exe 中无效——直接报错，不再尝试 install 后静默退出。
                # 打包版 stderr 是 devnull，console.print 用户不可见，必须同时写日志。
                logger.error(
                    "Playwright browser executable missing in PACKED build; system Edge/Chrome not found: %s",
                    exc,
                )
                console.print("[bold red]未检测到系统 Edge/Chrome 浏览器，无法启动发送浏览器。[/bold red]")
                sys.exit(1)
            console.print("[bold red]Playwright browser is missing.[/bold red]")
            await install_browser()
            sys.exit(1)
        traceback.print_exc()
        raise


async def get_persistent_browser_context(profile_name, GUI=False, root=None, network_mode=None):
    configure_playwright_environment()

    profile_dir = browser_profile_root(root) / sanitize_profile_name(profile_name)
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        playwright = await async_playwright().start()
        launch_options = _browser_launch_options(GUI, network_mode=network_mode)
        launch_options["viewport"] = {"width": 1600, "height": 1000}
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                **launch_options,
            )
        except Exception:
            # launch 失败（如 profile 被残留 Chromium 占用）时回收 driver 实例，避免泄漏
            try:
                await playwright.stop()
            except Exception:
                pass
            raise
        return playwright, context, profile_dir
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) and get_environment() != Environment.GITHUBACTION:
            if get_environment() == Environment.PACKED:
                # L6：打包版不内置浏览器（用系统 Edge/Chrome），sys.executable -m playwright install
                # 在 exe 中无效——直接报错，不再尝试 install 后静默退出。
                # 打包版 stderr 是 devnull，console.print 用户不可见，必须同时写日志。
                logger.error(
                    "Playwright browser executable missing in PACKED build; system Edge/Chrome not found: %s",
                    exc,
                )
                console.print("[bold red]未检测到系统 Edge/Chrome 浏览器，无法启动发送浏览器。[/bold red]")
                sys.exit(1)
            console.print("[bold red]Playwright browser is missing.[/bold red]")
            await install_browser()
            sys.exit(1)
        traceback.print_exc()
        raise
