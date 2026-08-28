"""DouYin SparkFlow 桌面版启动器（pywebview 原生窗口）。

双击 exe（或开发模式 ``python launcher.py``）后依次执行：

1. 初始化运行时数据目录（打包版: ``%APPDATA%\\DouYinSparkFlow\\``，开发版: 仓库 ``state/``）
2. 单实例检查（重复启动时提示并退出）
3. 后台线程启动 Web 控制台服务（``127.0.0.1:8787``）
4. 后台线程启动登录桌面服务（``127.0.0.1:18090``，native Chromium 扫码登录）
5. 打开 pywebview 原生窗口加载控制台界面
6. 窗口关闭后优雅停止所有服务并退出

打包版会将 exe 旁的 ``node/`` 目录加入 PATH，协议发送功能可直接调用内置 Node。
"""

from __future__ import annotations

import errno
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from utils.config import Environment, data_dir, get_environment
from utils.process import pid_is_alive

APP_TITLE = "DouYin SparkFlow"
WEB_HOST = "127.0.0.1"
WEB_PORT = 8787
LOGIN_DESKTOP_HOST = "127.0.0.1"
LOGIN_DESKTOP_PORT = 18090
STARTUP_TIMEOUT_SECONDS = 60
LOGIN_DESKTOP_START_TIMEOUT_SECONDS = 45
LOGIN_DESKTOP_START_DELAY_SECONDS = 0.5

logger = logging.getLogger("launcher")
_running_servers: list = []


def _ensure_stdio():
    """PyInstaller windowed 模式下 sys.stdout/stderr 为 None，替换为 devnull，
    否则 rich / uvicorn 等库访问 .isatty() 会崩溃。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))


def _setup_logging():
    root = data_dir()
    (root / "logs").mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(root / "logs" / "launcher.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _pid_is_alive(pid: int) -> bool:
    """探测进程是否存活（跨平台安全实现，见 utils/process.py）。

    无法探测时返回 False（宁可误启动，最终由 8787 端口占用兜底，
    也绝不让残留锁导致程序静默无法启动）。
    """
    return pid_is_alive(pid)


def ensure_data_dirs() -> Path:
    root = data_dir()
    for sub in ("logs", "state", "browser-profiles", "login-profile"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def acquire_single_instance(root: Path):
    """数据目录 ``state/instance.lock`` 原子创建（O_CREAT|O_EXCL）；已有存活实例则退出。

    锁文件异常（残留、损坏、不可写）一律不阻止程序启动——
    宁可放行，靠 8787 端口占用兜底防双实例。
    """
    lock = root / "state" / "instance.lock"
    for _attempt in range(3):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            return lock
        except FileExistsError:
            pass
        except OSError as exc:  # noqa: BLE001 - 锁失败不应导致程序无法启动
            logger.warning("Instance lock handling failed (%s), continuing anyway", exc)
            return lock

        old_pid = None
        try:
            raw = lock.read_text(encoding="utf-8").strip()
            if raw:
                old_pid = int(raw)
        except (ValueError, OSError):
            old_pid = None

        if old_pid is None:
            # 空内容：另一实例刚原子创建锁但尚未写入 PID，视为正在启动
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0
            if age < 10:
                logger.info("Another instance is starting (fresh lock), exiting.")
                sys.exit(0)
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
            continue

        if old_pid != os.getpid() and _pid_is_alive(old_pid):
            logger.info("Another instance is already running (pid=%s), exiting.", old_pid)
            sys.exit(0)
        logger.info("Removing stale instance lock owned by dead pid=%s", old_pid)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass
    return lock


def release_single_instance(lock: Path):
    try:
        if lock.exists() and lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock.unlink()
    except OSError:
        pass


def port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(host, port):
            return True
        time.sleep(0.2)
    return False


def _thread_guard(name):
    """让后台服务线程的异常落盘而不是静默消失（windowed 模式无控制台）。"""

    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception("Service thread '%s' crashed", name)

        return wrapper

    return decorator


def start_web_server():
    from webui.app import app as web_app

    config = uvicorn.Config(
        web_app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    _running_servers.append(server)
    logger.info("Web console listening on http://%s:%s", WEB_HOST, WEB_PORT)
    server.run()


@_thread_guard("web-server")
def run_web_thread():
    start_web_server()


def start_login_desktop_server():
    time.sleep(LOGIN_DESKTOP_START_DELAY_SECONDS)
    # 桌面版固定 native 模式：用户残留的 LOGIN_DESKTOP_MODE=novnc 会让
    # 登录页走容器 noVNC 路径而白屏，这里必须覆盖而非 setdefault。
    os.environ["LOGIN_DESKTOP_MODE"] = "native"
    os.environ.setdefault("LOGIN_DESKTOP_API_PORT", str(LOGIN_DESKTOP_PORT))
    # 内嵌二维码模式：登录 Chromium 后台隐藏运行，应用窗口内展示二维码扫码
    os.environ.setdefault("LOGIN_DESKTOP_HIDDEN_WINDOW", "1")
    # 确保认证 token 已生成（webui 与登录服务共享同一数据目录自动互通）
    from utils.config import login_desktop_auth_token

    login_desktop_auth_token()
    import login_desktop_server

    config = uvicorn.Config(
        login_desktop_server.app,
        host=LOGIN_DESKTOP_HOST,
        port=LOGIN_DESKTOP_PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    _running_servers.append(server)
    logger.info("Login desktop service listening on %s:%s", LOGIN_DESKTOP_HOST, LOGIN_DESKTOP_PORT)
    server.run()


@_thread_guard("login-desktop-server")
def run_login_thread():
    start_login_desktop_server()


def prepend_bundled_node_to_path():
    """打包版：把 exe 旁的 ``node/`` 目录加入 PATH，协议发送可直接找到 node。"""
    if get_environment() != Environment.PACKED:
        return
    exe_dir = Path(sys.executable).resolve().parent
    for candidate in (exe_dir / "node", exe_dir / "node" / "bin"):
        if (candidate / "node.exe").exists():
            os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
            logger.info("Bundled Node added to PATH: %s", candidate)
            return


def stop_all_servers():
    for server in _running_servers:
        server.should_exit = True


def _native_save_file_dialog(default_name: str):
    """Windows 原生"另存为"对话框（GetSaveFileNameW）。

    js_api 方法在 HTTP 桥接线程执行，跨线程调 pywebview/WinForms 对话框不可靠，
    因此直接用 comdlg32（自带消息循环，任意线程可调）。
    返回选中的完整路径；取消时返回 None。
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    comdlg32 = ctypes.windll.comdlg32
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
        ]

    buffer = ctypes.create_unicode_buffer(4096)
    buffer.value = default_name or "douyin-sparkflow.log"
    filter_spec = "日志文件 (*.log)\0*.log\0所有文件 (*.*)\0*.*\0\0"
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.lpstrFilter = filter_spec
    ofn.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    ofn.nMaxFile = len(buffer)
    ofn.lpstrDefExt = "log"
    ofn.Flags = 0x00000002 | 0x00000004  # OFN_OVERWRITEPROMPT | OFN_HIDEREADONLY
    if not comdlg32.GetSaveFileNameW(ctypes.byref(ofn)):
        return None
    return buffer.value


class Api:
    """pywebview js_api：供窗口内前端调用的本地能力。"""

    def save_log_file(self, default_name=""):
        try:
            path = _native_save_file_dialog(default_name or "douyin-sparkflow.log")
        except Exception as exc:  # noqa: BLE001 - 对话框失败不应带崩前端调用
            logger.exception("Native save dialog failed")
            return {"ok": False, "error": f"对话框打开失败：{exc}"}
        if not path:
            return {"ok": False, "cancelled": True}
        try:
            from utils.config import default_ops_log_path, get_app_settings
            from utils.logger import read_text_autodetect

            log_path = Path(get_app_settings().get("ops_log_file") or default_ops_log_path())
            content = read_text_autodetect(log_path) if log_path.exists() else ""
            Path(path).write_text(content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to save log file")
            return {"ok": False, "error": f"写入失败：{exc}"}
        return {"ok": True, "path": path}


def _tray_icon_path():
    """托盘图标：打包版取 _internal/app.ico，开发版取 packaging/windows/app.ico。"""
    if get_environment() == Environment.PACKED:
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "app.ico"
        if bundled.exists():
            return bundled
    local = Path(__file__).resolve().parent.parent / "packaging" / "windows" / "app.ico"
    return local if local.exists() else None


class TrayController:
    """系统托盘（pystray）：关窗口后常驻后台，托盘菜单可显示窗口/退出。"""

    def __init__(self, icon_path, on_show, on_quit):
        self._icon_path = icon_path
        self._on_show = on_show
        self._on_quit = on_quit
        self._icon = None
        self._thread = None

    def start(self):
        def _run():
            try:
                import pystray
                from PIL import Image
            except Exception:
                logger.exception("Tray dependencies unavailable, skipping tray")
                return
            try:
                if self._icon_path:
                    image = Image.open(self._icon_path)
                else:
                    image = Image.new("RGBA", (64, 64), (94, 155, 196, 255))
                menu = pystray.Menu(
                    pystray.MenuItem("显示窗口", lambda: self._on_show(), default=True),
                    pystray.MenuItem("退出", lambda: self._on_quit()),
                )
                self._icon = pystray.Icon("DouYinSparkFlow", image, APP_TITLE, menu)
                self._icon.run()
            except Exception:
                logger.exception("Tray icon failed to start")

        self._thread = threading.Thread(target=_run, name="tray", daemon=True)
        self._thread.start()

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass


def set_autostart(enabled: bool) -> bool:
    """开机自启：写/删 HKCU Run 注册表项（仅 Windows）。"""
    if os.name != "nt":
        return False
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    name = "DouYinSparkFlow"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                if get_environment() == Environment.PACKED:
                    command = f'"{Path(sys.executable).resolve()}"'
                else:
                    launcher = Path(__file__).resolve()
                    command = f'"{Path(sys.executable).resolve()}" "{launcher}"'
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        logger.exception("Failed to update autostart registry")
        return False


def autostart_enabled() -> bool:
    """读取开机自启注册表项当前状态。"""
    if os.name != "nt":
        return False
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, "DouYinSparkFlow")
            return True
    except FileNotFoundError:
        return False
    except Exception:
        logger.exception("Failed to read autostart registry")
        return False


def _run_cli_mode(argv):
    """打包版子进程调用形式（``exe main.py --doTask``）：复用 main.py 的 CLI 分支，
    避免再拉起一个完整桌面实例。"""
    import asyncio

    from main import build_parser

    args = build_parser().parse_args(argv)
    if args.doTask:
        from core.tasks import runTasks

        asyncio.run(runTasks())
    elif args.web:
        from webui.app import run_web_app

        run_web_app(host=args.host, port=args.port)
    else:
        from main import interactive_cli

        interactive_cli()


def main():
    _ensure_stdio()
    _setup_logging()
    raw_args = list(sys.argv[1:])
    if raw_args and raw_args[0] == "main.py":
        try:
            _run_cli_mode(raw_args[1:])
        except SystemExit:
            raise
        except Exception:
            logger.exception("Unhandled error in CLI mode, exiting")
            sys.exit(1)
        return
    try:
        _main()
    except SystemExit:
        raise
    except Exception:
        logger.exception("Unhandled error, exiting")
        sys.exit(1)


def _main():
    root = ensure_data_dirs()
    lock = acquire_single_instance(root)
    prepend_bundled_node_to_path()

    logger.info("Starting DouYin SparkFlow desktop build (env=%s)", get_environment())

    web_thread = threading.Thread(target=run_web_thread, name="web-server", daemon=True)
    web_thread.start()
    login_thread = threading.Thread(
        target=run_login_thread, name="login-desktop-server", daemon=True
    )
    login_thread.start()

    if not wait_for_port(WEB_HOST, WEB_PORT, STARTUP_TIMEOUT_SECONDS):
        logger.error("Web console failed to start within %ss", STARTUP_TIMEOUT_SECONDS)
        stop_all_servers()
        release_single_instance(lock)
        sys.exit(1)
    logger.info("Web console ready on http://%s:%s", WEB_HOST, WEB_PORT)

    if not wait_for_port(LOGIN_DESKTOP_HOST, LOGIN_DESKTOP_PORT, LOGIN_DESKTOP_START_TIMEOUT_SECONDS):
        logger.error(
            "Login desktop service failed to start within %ss — QR login will be unavailable",
            LOGIN_DESKTOP_START_TIMEOUT_SECONDS,
        )

    try:
        import webview  # pywebview 延迟导入，避免无窗口环境（CI/测试）启动失败
    except Exception:
        logger.exception("pywebview unavailable, falling back to browser mode")
        _fallback_browser_mode(lock)
        return

    window = None
    quit_requested = threading.Event()
    tray = None
    try:
        window = webview.create_window(
            APP_TITLE,
            f"http://{WEB_HOST}:{WEB_PORT}",
            width=1280,
            height=820,
            min_size=(960, 620),
            js_api=Api(),
        )

        def on_closing():
            # 常驻：点 ✕ 只隐藏窗口，服务继续运行；托盘"退出"才真正关闭
            if quit_requested.is_set():
                return True
            try:
                window.hide()
            except Exception:
                pass
            logger.info("Window hidden to tray; services keep running")
            return False

        def show_window():
            try:
                window.show()
                window.restore()
            except Exception:
                pass

        def quit_app():
            quit_requested.set()
            try:
                window.destroy()
            except Exception:
                pass

        window.events.closing += on_closing
        tray = TrayController(_tray_icon_path(), show_window, quit_app)
        tray.start()
        webview.start()
    except Exception:
        logger.exception("pywebview window failed, falling back to browser mode")
        tray = None
        _fallback_browser_mode(lock)
        return

    logger.info("Quit requested, shutting down services…")
    if tray:
        tray.stop()
    stop_all_servers()
    _join_server_threads(web_thread, login_thread)
    release_single_instance(lock)
    sys.exit(0)


def _fallback_browser_mode(lock: Path):
    """pywebview 不可用（如缺 WebView2 运行时）时打开系统浏览器，保持双服务运行。"""
    import webbrowser

    logger.info("Browser fallback: open http://%s:%s", WEB_HOST, WEB_PORT)
    try:
        webbrowser.open(f"http://{WEB_HOST}:{WEB_PORT}")
    except Exception:
        pass
    stop_event = threading.Event()
    try:
        while not stop_event.wait(3600):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        stop_all_servers()
        release_single_instance(lock)


def _join_server_threads(web_thread: threading.Thread, login_thread: threading.Thread, timeout: float = 10):
    """等待 uvicorn 线程退出，让 lifespan finally（关闭 Chromium）有机会执行。"""
    for thread in (web_thread, login_thread):
        if thread.is_alive():
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("Service thread '%s' did not stop within %ss", thread.name, timeout)


if __name__ == "__main__":
    main()
