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
        # HTTP/1.0 无 Host 头：uvicorn 仅按监听地址路由，视为本机直连
        return True
    host = host_header.split(":", 1)[0].strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host in _ALLOWED_HOSTS


async def localhost_only_middleware(request, call_next):
    if not _hostname_allowed(request.headers.get("host", "")):
        return PlainTextResponse("Forbidden: invalid host header", status_code=403)
    return await call_next(request)
