import json
import logging
import os
import secrets
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from enum import Enum
from pathlib import Path

from utils.process import hidden_startupinfo


# 使用 "app" 命名空间而非在此调用 setup_logger：
# utils/logger.py 的 _app_log_path 依赖 utils.config.data_dir，模块初始化期
# 调用 setup_logger 会形成循环导入。config 与 core.tasks 共用 "app" logger，
# 由首个 import 完成的模块（tasks.py）统一配置 handler。
logger = logging.getLogger("app")

DEBUG = False
CONFIGFILE = "config.json"
USERDATAFILE = "usersData.json"
APPSETTINGSFILE = "webui_settings.json"

DEFAULT_CONFIG = {
    "multiTask": True,
    "taskCount": 1,
    "proxyAddress": "",
    "messageTemplate": "🤩今日火花+1\r\n",
    "saveDebugArtifacts": False,
    "useProtocolSender": False,
    "protocolDryRun": False,
    "browserSenderAccounts": [],
    "sendStrategy": {
        "shuffleTargets": True,
        "accountStartDelaySecondsMin": 15,
        "accountStartDelaySecondsMax": 60,
        "messageIntervalSecondsMin": 25,
        "messageIntervalSecondsMax": 70,
        "messageVariants": [
            "🤩今日火花+1",
            "今天来补个火花",
            "给你续一下今天的火花",
            "路过给你加个小火花"
        ]
    },
    "dailySendWindow": {
        "enabled": True,
        "startHour": 10,
        "endHour": 18,
        "scheduleIntervalMinutes": 20
    },
    "hitokotoTypes": [
        "文学",
        "影视",
        "诗词",
        "哲学"
    ],
    "happyNewYear": {
        "enabled": False,
        "messageTemplate": "\r\n"
    },
    "friendListScan": {
        "maxScanSeconds": 300,
        "idleScanSeconds": 120,
        "scrollStepPx": 400,
        "scrollDelaySeconds": 0.8
    },
    "persistentBrowserProfiles": {
        "enabled": True,
        "root": "",
        "seedCookiesWhenEmpty": True,
        "syncStoredCookiesBeforeRun": True,
        "refreshStoredCookiesAfterLogin": True
    }
}

DEFAULT_APP_SETTINGS = {
    "admin_username": "admin",
    "admin_password_hash": "",
    "session_secret": "",
    "session_max_age_seconds": 8 * 60 * 60,
    "compose_root": "",
    "ui_host": "127.0.0.1",
    "ui_port": 8787,
    "login_poll_interval_seconds": 1,
    "ops_log_file": "",
    "proxy_refresh_script": "",
    "local_login_helper_url": "http://127.0.0.1:18765",
    "login_desktop_api_url": "http://127.0.0.1:18090",
    "login_desktop_public_url": "",
    "login_desktop_public_scheme": "http",
    "login_desktop_public_port": 8788,
    "server_host": "",
    "server_username": "",
    "server_password": "",
}

config = None
userData = None
appSettings = None


class Environment(Enum):
    GITHUBACTION = "GITHUB_ACTION"
    LOCAL = "LOCAL"
    PACKED = "PACKED"

    def __str__(self):
        return self.value


def get_environment():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Environment.PACKED
    if os.getenv("GITHUB_ACTIONS") == "true":
        return Environment.GITHUBACTION
    return Environment.LOCAL


def repo_root():
    return Path(__file__).resolve().parents[1]


def data_dir():
    """运行时数据目录。

    - 打包版（PACKED）：%APPDATA%\\DouYinSparkFlow（可用 SPARKFLOW_DATA_DIR 环境变量覆盖）
    - 开发版（LOCAL）：仓库 DouYinSparkFlow/state/
    """
    if get_environment() == Environment.PACKED:
        override = os.getenv("SPARKFLOW_DATA_DIR")
        if override:
            return Path(override).expanduser()
        base = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "DouYinSparkFlow"
    return repo_root() / "state"


def default_ops_log_path():
    """任务运行日志默认位置（按环境解析）。"""
    return data_dir() / "logs" / "douyin-sparkflow.log"


def login_desktop_auth_token():
    """登录桌面服务（18090）的共享认证 token。

    环境变量 ``LOGIN_DESKTOP_AUTH_TOKEN`` 优先（容器/多机场景两服务可用同一 env）；
    否则落盘到 ``data_dir()/state/login_desktop_auth.token``，同机多进程共享同一数据目录即可互通。
    """
    env_token = os.getenv("LOGIN_DESKTOP_AUTH_TOKEN", "").strip()
    if env_token:
        return env_token
    token_path = data_dir() / "state" / "login_desktop_auth.token"
    try:
        if token_path.exists():
            token = token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".token.", dir=str(token_path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
        os.replace(temp_name, token_path)
        # M4：token 等同密码，落盘后收紧 ACL（仅当前用户可读）
        _restrict_file_permissions(token_path)
    except OSError:
        pass
    return token


def _runtime_root():
    env = get_environment()
    if env == Environment.PACKED:
        return data_dir()
    return repo_root()


def config_path():
    return _runtime_root() / CONFIGFILE


def users_data_path():
    return _runtime_root() / USERDATAFILE


def app_settings_path():
    return _runtime_root() / APPSETTINGSFILE


def default_compose_root():
    root = repo_root()
    parent = root.parent
    if (parent / "docker-compose.yml").exists():
        return str(parent)
    return str(root)


def _merge_defaults(data, defaults):
    # L5：深合并——旧 config.json 已存在的嵌套键（如 sendStrategy）按子键递归合并，
    # DEFAULT_CONFIG 新增的子键在升级后也能生效（原实现仅一层 dict.update 会整体覆盖）
    merged = deepcopy(defaults)
    for key, value in data.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_defaults(value, merged[key])
        else:
            merged[key] = value
    return merged


def _load_json_file(path, defaults=None):
    if not path.exists():
        if defaults is None:
            raise FileNotFoundError(path)
        path.write_text(json.dumps(defaults, ensure_ascii=False, indent=2), encoding="utf-8")
        return deepcopy(defaults)

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return deepcopy(defaults) if defaults is not None else None

    data = json.loads(text)
    if defaults is None:
        return data
    # _merge_defaults only works with dicts; for list-shaped data
    # (e.g. usersData.json) just return the parsed data as-is.
    if not isinstance(data, dict) or not isinstance(defaults, dict):
        return data
    return _merge_defaults(data, defaults)


# 已收紧 ACL 的文件缓存：键为 (st_dev, st_ino)——
# _save_json_file 用 mkstemp+os.replace 每次换新 inode（ACL 回父目录继承），
# 路径字符串缓存会漏掉换 inode 后的重新收紧，故按 inode 键控（每次写盘收紧一次，~14ms 可忽略）
_ACL_RESTRICTED_PATHS = set()


def _restrict_file_permissions(path):
    """收紧敏感数据文件 ACL（纵深防御，失败仅告警）。

    - POSIX：chmod 600（仅属主可读写）
    - Windows：icacls 移除继承并仅授予当前用户读/写
    usersData.json（含全部抖音 cookies）与 login_desktop_auth.token 等同机敏感文件
    均应经此收紧，避免同机其他用户可读。每个文件 inode 仅执行一次。
    """
    try:
        stat_result = path.stat()
    except OSError:
        return
    key = (stat_result.st_dev, stat_result.st_ino)
    if key in _ACL_RESTRICTED_PATHS:
        return
    try:
        if os.name == "nt":
            import subprocess as _subprocess

            username = None
            try:
                username = os.getlogin()
            except (OSError, ValueError):
                pass
            if not username:
                username = os.environ.get("USERNAME", "")
            if not username:
                logger.warning("Cannot determine current user for icacls on %s", path)
                return
            result = _subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:(R,W)"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                startupinfo=hidden_startupinfo(),
            )
            if result.returncode != 0:
                logger.warning(
                    "icacls failed for %s: %s",
                    path,
                    (result.stderr or result.stdout or "").strip()[:200],
                )
        else:
            os.chmod(path, 0o600)
        _ACL_RESTRICTED_PATHS.add(key)
    except Exception as exc:
        logger.warning("Failed to restrict permissions on %s: %s", path, exc)


def _save_json_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    # 所有落盘的数据文件统一收紧 ACL（usersData 含 cookies、settings 含 session_secret）
    _restrict_file_permissions(path)


_FILE_LOCK_GUARD = threading.Lock()


@contextmanager
def json_file_lock(name):
    """针对某一运行时数据文件的跨进程排他锁。

    webui 进程与任务子进程（``main.py --doTask``）会并发读-改-写
    usersData.json / config.json，单次写入虽原子（mkstemp+os.replace），
    但读-改-写窗口无互斥会 last-writer-wins 丢更新。对同一文件的
    读取与写入持有本锁可串行化跨进程访问。

    Windows 用 msvcrt.locking，其他平台用 fcntl.flock；
    同一进程内的并发调用由 threading.Lock 串行化（避免句柄自锁）。
    """
    lock_path = data_dir() / "state" / f"{name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _FILE_LOCK_GUARD:
        # r+b 而非 a+b：a 模式写永远追加（即使 seek 到 0），会导致锁文件每次 +1 字节无限增长
        if not lock_path.exists():
            lock_path.write_bytes(b"x")
        handle = open(lock_path, "r+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"x")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def get_config(force_reload=False):
    global config
    if config is None or force_reload:
        # L4：读-改-写窗口纳入跨进程锁（config.json 可能被 webui 与子进程并发读写）
        with json_file_lock(CONFIGFILE):
            config = _load_json_file(config_path(), DEFAULT_CONFIG)
    return deepcopy(config)


def save_config(new_config):
    global config
    config = _merge_defaults(new_config, DEFAULT_CONFIG)
    with json_file_lock(CONFIGFILE):
        _save_json_file(config_path(), config)
    return deepcopy(config)


def update_user_data(mutator):
    """原子读-改-写 usersData.json（锁内完成整个 RMW 窗口）。

    M5：webui 与任务子进程并发写 usersData 时，分开的 get_userData + save_userData
    存在 last-writer-wins 窗口——任务写入的发送账本（message_history/failure_queue）
    可被覆盖，导致重复发送/漏发。所有"读-改-写"路径应改走本封装：

        def mutate(accounts):
            ... 修改 accounts ...
            return accounts   # 返回 None 表示不写入

        update_user_data(mutate)

    约束：mutator 内不得调用任何会获取 json_file_lock 的函数
    （get_userData/save_userData/update_user_data/get_config(force_reload=True)），
    _FILE_LOCK_GUARD 为不可重入 threading.Lock，重入会自锁死锁。
    """
    global userData
    with json_file_lock(USERDATAFILE):
        accounts = _load_json_file(users_data_path(), [])
        updated = mutator(accounts)
        if updated is None:
            return deepcopy(accounts)
        userData = updated
        _save_json_file(users_data_path(), updated)
    return deepcopy(userData)


def get_userData(force_reload=False):
    global userData
    if userData is not None and not force_reload:
        return deepcopy(userData)

    env = get_environment()
    if env == Environment.GITHUBACTION:
        raw = os.getenv("USER_DATA", "")
        if not raw:
            logger.error("Environment variable USER_DATA is not set")
            raise RuntimeError("USER_DATA is required in GITHUB_ACTIONS mode")
        userData = json.loads(raw)
    else:
        with json_file_lock(USERDATAFILE):
            userData = _load_json_file(users_data_path(), [])

    return deepcopy(userData)


def save_userData(accounts):
    global userData
    normalized = list(accounts)
    userData = normalized
    with json_file_lock(USERDATAFILE):
        _save_json_file(users_data_path(), normalized)
    return deepcopy(userData)


def normalize_unique_id(unique_id):
    if not unique_id:
        return ""
    digits = "".join(ch for ch in str(unique_id) if ch.isdigit())
    return digits or str(unique_id).strip()


def upsert_user_account(unique_id, username, cookies, targets, extra=None):
    unique_id = normalize_unique_id(unique_id)
    accounts = get_userData(force_reload=True)
    payload = {
        "account_ref": f"acc-{uuid.uuid4().hex}",
        "unique_id": unique_id,
        "username": username,
        "cookies": cookies,
        "targets": list(targets),
    }
    if extra:
        payload.update(extra)

    for account in accounts:
        if normalize_unique_id(account.get("unique_id")) == unique_id:
            payload["account_ref"] = account.get("account_ref") or payload["account_ref"]
            if "enabled" not in payload:
                payload["enabled"] = account.get("enabled", True)
            account.update(payload)
            save_userData(accounts)
            return account

    if "enabled" not in payload:
        payload["enabled"] = True
    accounts.append(payload)
    save_userData(accounts)
    return payload


def delete_user_account(unique_id):
    normalized_id = normalize_unique_id(unique_id)
    accounts = get_userData(force_reload=True)
    remaining = [item for item in accounts if normalize_unique_id(item.get("unique_id")) != normalized_id]
    removed = len(accounts) != len(remaining)
    if removed:
        save_userData(remaining)
    return removed


def get_app_settings(force_reload=False):
    global appSettings
    if appSettings is None or force_reload:
        appSettings = _load_json_file(app_settings_path(), DEFAULT_APP_SETTINGS)
        if not appSettings.get("session_secret"):
            appSettings["session_secret"] = secrets.token_urlsafe(32)
        _save_json_file(app_settings_path(), appSettings)
    return deepcopy(appSettings)


def save_app_settings(new_settings):
    global appSettings
    appSettings = _merge_defaults(new_settings, DEFAULT_APP_SETTINGS)
    if not appSettings.get("session_secret"):
        appSettings["session_secret"] = secrets.token_urlsafe(32)
    _save_json_file(app_settings_path(), appSettings)
    return deepcopy(appSettings)
