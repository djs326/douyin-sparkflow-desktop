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

APP_TITLE = "DouYin SparkFlow"
WEB_HOST = "127.0.0.1"
WEB_PORT = 8787
LOGIN_DESKTOP_HOST = "127.0.0.1"
LOGIN_DESKTOP_PORT = 18090
STARTUP_TIMEOUT_SECONDS = 60
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
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if getattr(exc, "winerror", None) == 87 or exc.errno == errno.ESRCH:
            return False
        if exc.errno in (errno.EPERM, errno.EACCES):
            return True
        raise
    return True


def ensure_data_dirs() -> Path:
    root = data_dir()
    for sub in ("logs", "state", "browser-profiles", "login-profile"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def acquire_single_instance(root: Path):
    """数据目录 ``state/instance.lock`` 记录 PID；已有存活实例则直接退出。"""
    lock = root / "state" / "instance.lock"
    try:
        if lock.exists():
            try:
                old_pid = int(lock.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                old_pid = None
            if old_pid and old_pid != os.getpid() and _pid_is_alive(old_pid):
                logger.info("Another instance is already running (pid=%s), exiting.", old_pid)
                sys.exit(0)
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        logger.warning("Cannot write instance lock at %s", lock)
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
    os.environ.setdefault("LOGIN_DESKTOP_MODE", "native")
    os.environ.setdefault("LOGIN_DESKTOP_API_PORT", str(LOGIN_DESKTOP_PORT))
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


def main():
    _ensure_stdio()
    _setup_logging()
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
        release_single_instance(lock)
        sys.exit(1)
    logger.info("Web console ready, opening window…")

    import webview  # pywebview 延迟导入，避免无窗口环境（CI/测试）启动失败

    webview.create_window(
        APP_TITLE,
        f"http://{WEB_HOST}:{WEB_PORT}",
        width=1280,
        height=820,
        min_size=(960, 620),
    )
    webview.start()

    logger.info("Window closed, shutting down services…")
    stop_all_servers()
    release_single_instance(lock)
    sys.exit(0)


if __name__ == "__main__":
    main()
