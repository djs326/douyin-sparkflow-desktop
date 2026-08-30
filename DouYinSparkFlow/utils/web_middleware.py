"""仅本机服务的 FastAPI 中间件：校验 Host 头，防 DNS rebinding 跨域访问。

服务虽绑定 127.0.0.1，但浏览器对「攻击者域名 → 127.0.0.1」的 DNS
rebinding 请求会携带 Host: attacker.com，且按域名判定同源。拒绝非本机
Host 头可让恶意网页无法读取响应（Firefox/Safari 无 Private Network
Access 防护，此校验对它们尤其必要）。
"""

from fastapi.responses import PlainTextResponse

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
# "testserver" 仅为 Starlette TestClient 的默认 Host（真实 uvicorn 服务不会收到该 Host）


def _hostname_allowed(host_header):
    if not host_header:
        # L1：HTTP/1.1 起 Host 头必填；无 Host 头直接拒绝（原实现放行是
        # DNS rebinding 的边界妥协，改为拒绝收窄攻击面）
        return False
    raw = str(host_header).strip().lower()
    if raw.startswith("["):
        # IPv6 字面量如 [::1]:8787：先剥离 [] 再取主机部分，
        # 否则 split(":") 会把 "[" 当主机名，白名单里的 ::1 永远走不到
        end = raw.find("]")
        host = raw[1:end] if end > 0 else raw
    else:
        host = raw.split(":", 1)[0]
    return host in _ALLOWED_HOSTS


async def localhost_only_middleware(request, call_next):
    if not _hostname_allowed(request.headers.get("host", "")):
        return PlainTextResponse("Forbidden: invalid host header", status_code=403)
    return await call_next(request)
