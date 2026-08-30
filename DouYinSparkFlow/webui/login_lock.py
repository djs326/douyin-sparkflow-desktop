"""登录工作区租约（单机语义，无多用户排队）。

本地单机应用：只有一个用户使用登录工作区，直接激活/续期/释放，
不再有 FIFO 队列与 resetting 过渡态。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path

from utils.config import data_dir


def _default_lock_path() -> Path:
    # L3：所有环境统一经 data_dir()（LOCAL=DouYinSparkFlow/state/，PACKED=%APPDATA%）。
    # 原实现 LOCAL 分支用 repo_root().parent/state（仓库根 state/），与 data_dir() 不一致，
    # 锁文件与数据分离在另一个目录里。
    return data_dir() / "state" / "login-workspace.lock.json"


LOCK_PATH = _default_lock_path()
LOCK_TTL_SECONDS = 180
_MUTEX = threading.Lock()


def _empty_state() -> dict:
    return {"version": 2, "phase": "idle", "active": None}


def _read_raw() -> dict | None:
    try:
        data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _normalize_state(data: dict | None) -> dict:
    if not data:
        return _empty_state()
    if "active" in data:
        state = _empty_state()
        state.update({key: data.get(key) for key in ("version", "phase", "active")})
        state["version"] = 2
        state["phase"] = str(state.get("phase") or ("active" if state.get("active") else "idle"))
        return state
    # Backward compatibility with the original single-lock file.
    legacy = dict(data)
    now = time.time()
    legacy.setdefault("ticket", "legacy-" + uuid.uuid4().hex)
    legacy.setdefault("requested_at", now)
    legacy.setdefault("started_at", legacy.get("acquired_at", now))
    legacy.setdefault("last_heartbeat_at", legacy.get("acquired_at", now))
    return {"version": 2, "phase": "active", "active": legacy}


def _write_state(state: dict) -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".login-workspace.", suffix=".tmp", dir=str(LOCK_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, LOCK_PATH)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _delete_state() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def _now() -> float:
    return time.time()


def _expired(active: dict | None, now: float | None = None) -> bool:
    if not active:
        return False
    now = _now() if now is None else now
    try:
        last_heartbeat = float(active.get("last_heartbeat_at") or 0)
    except (TypeError, ValueError):
        last_heartbeat = 0
    return (now - last_heartbeat) > LOCK_TTL_SECONDS


def _new_item(username: str, session_id: str, account_ref: str, now: float, mode: str = "add") -> dict:
    return {
        "ticket": uuid.uuid4().hex,
        "username": username,
        "session_id": session_id,
        "account_ref": account_ref or "",
        "mode": mode,
        "requested_at": now,
        "started_at": now,
        "last_heartbeat_at": now,
    }


def owns(active: dict | None, *, username=None, session_id=None, account_ref=None, ticket=None) -> bool:
    if not active:
        return False
    if username is not None and str(active.get("username")) != str(username):
        return False
    if session_id is not None and str(active.get("session_id")) != str(session_id):
        return False
    if account_ref is not None and str(active.get("account_ref")) != str(account_ref):
        return False
    if ticket is not None and str(active.get("ticket")) != str(ticket):
        return False
    return True


def request_workspace(*, username: str, session_id: str, account_ref: str = "", mode: str = "relogin") -> dict:
    # 单机：直接激活（本机使用中则续期，否则创建新租约）
    with _MUTEX:
        state = _normalize_state(_read_raw())
        now = _now()
        active = state.get("active")
        if owns(active, username=username, session_id=session_id):
            active["last_heartbeat_at"] = now
            _write_state(state)
            return {"state": "active", "position": 0, "request": deepcopy(active), "workspace": state}
        item = _new_item(username, session_id, account_ref, now, mode=mode)
        state = {"version": 2, "phase": "active", "active": item}
        _write_state(state)
        return {"state": "active", "position": 0, "request": deepcopy(item), "workspace": state}


def heartbeat(*, username: str, session_id: str, ticket: str = "", account_ref: str = "") -> bool:
    with _MUTEX:
        state = _normalize_state(_read_raw())
        active = state.get("active")
        if not owns(active, username=username, session_id=session_id, account_ref=account_ref or None, ticket=ticket or None):
            return False
        active["last_heartbeat_at"] = _now()
        _write_state(state)
        return True


def begin_expiration() -> dict | None:
    # 过期清理：直接清空状态
    with _MUTEX:
        state = _normalize_state(_read_raw())
        if state.get("phase") != "active" or not _expired(state.get("active")):
            return None
        old = deepcopy(state.get("active"))
        _delete_state()
        return old


def begin_force_reset(*, clear_queue: bool = False) -> dict | None:
    # 单机强制重置：无条件清空当前工作区
    with _MUTEX:
        state = _normalize_state(_read_raw())
        active = state.get("active")
        if not active:
            return None
        old = deepcopy(active)
        _delete_state()
        return old


def begin_release(*, username: str, session_id: str, ticket: str = "", account_ref: str = "") -> dict | None:
    with _MUTEX:
        state = _normalize_state(_read_raw())
        active = state.get("active")
        if not owns(active, username=username, session_id=session_id, account_ref=account_ref or None, ticket=ticket or None):
            return None
        old = deepcopy(active)
        _delete_state()
        return old


def cancel_request(*, username: str, session_id: str, ticket: str = "") -> tuple[str, dict | None]:
    old = begin_release(username=username, session_id=session_id, ticket=ticket)
    if old:
        return "cancelled", old
    return "none", None


def finish_transition() -> dict | None:
    # 单机无排队：直接清空状态
    with _MUTEX:
        _delete_state()
        return None


def get_workspace_state() -> dict:
    return _normalize_state(_read_raw())


def get_lock() -> dict | None:
    return get_workspace_state().get("active")


def workspace_status(*, username: str, session_id: str) -> dict:
    state = get_workspace_state()
    active = state.get("active")
    if owns(active, username=username, session_id=session_id):
        remaining = max(0, int(LOCK_TTL_SECONDS - (_now() - float(active.get("last_heartbeat_at", _now())))))
        return {"state": "active", "position": 0, "ticket": active.get("ticket", ""), "remaining_seconds": remaining, "workspace": state}
    if active and not _expired(active):
        return {"state": "active", "position": 0, "ticket": "", "remaining_seconds": 0, "workspace": state}
    return {"state": "closed", "position": 0, "ticket": "", "remaining_seconds": 0, "workspace": state}
