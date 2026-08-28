import hashlib
import hmac
import secrets

from utils.config import get_app_settings, save_app_settings


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 480000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password, stored_hash):
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        algorithm, salt, digest = stored_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hash_password(password, salt=salt)
    return hmac.compare_digest(candidate, stored_hash)


def is_bootstrapped():
    return bool(get_app_settings().get("admin_password_hash"))


def bootstrap_admin_password(password, username="admin"):
    settings = get_app_settings(force_reload=True)
    settings["admin_username"] = username.strip() or "admin"
    settings["admin_password_hash"] = hash_password(password)
    return save_app_settings(settings)


def update_admin_password(password):
    settings = get_app_settings(force_reload=True)
    settings["admin_password_hash"] = hash_password(password)
    return save_app_settings(settings)


def issue_session(request, username, *, role="admin", account_refs=None):
    request.session.clear()
    request.session["user"] = username
    request.session["role"] = role
    request.session["account_refs"] = list(account_refs or [])
    request.session["session_id"] = secrets.token_urlsafe(24)
    request.session["csrf_token"] = secrets.token_urlsafe(24)


def clear_session(request):
    request.session.clear()


def current_user(request):
    # 本地单机应用：免登录，始终视为本机管理员
    return str(get_app_settings().get("admin_username", "admin")).strip() or "admin"


def current_principal(request):
    """本地单机应用：始终返回本机管理员身份。"""
    return {
        "username": str(get_app_settings().get("admin_username", "admin")).strip() or "admin",
        "role": "admin",
        "account_refs": [],
        "session_id": str(request.session.get("session_id", "")),
        "enabled": True,
    }


def is_admin(request):
    return True


def csrf_token(request):
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request, submitted_token):
    stored_token = request.session.get("csrf_token")
    return bool(stored_token and submitted_token and hmac.compare_digest(stored_token, submitted_token))


def is_https_request(request):
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.lower() == "https"
    return request.url.scheme == "https"
