"""登录桌面服务（18090）访问控制测试：token 认证与 Host 头校验。

服务可导出完整抖音 cookies 并可远程操控登录浏览器（/debug/action 可 eval
任意 JS），必须验证未授权请求被拒绝。
"""

import unittest

from fastapi.testclient import TestClient

import login_desktop_server as lds


class LoginDesktopAuthTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(lds.app)

    def test_health_is_public(self):
        # 健康探测端点豁免 token（仅做 TCP/HTTP 级探测，无敏感数据）
        response = self.client.get("/health")
        self.assertEqual(200, response.status_code)

    def test_endpoints_require_token(self):
        for method, path, kwargs in (
            ("get", "/status", {}),
            ("get", "/preflight", {}),
            ("get", "/qr", {}),
            ("post", "/open-login", {}),
            ("post", "/export", {}),
            ("post", "/reset", {}),
            ("post", "/close", {}),
            ("post", "/focus", {}),
            ("post", "/refresh-qr", {}),
            ("get", "/debug/screenshot", {}),
            ("get", "/debug/snapshot", {}),
            ("post", "/debug/action", {"json": {"action": "list_frames"}}),
            ("post", "/debug/net_capture", {"json": {"type": "get"}}),
            ("get", "/debug/net_log", {}),
        ):
            response = getattr(self.client, method)(path, **kwargs)
            self.assertEqual(
                403,
                response.status_code,
                f"expected 403 for unauthenticated {method.upper()} {path}, got {response.status_code}",
            )

    def test_wrong_token_is_rejected(self):
        response = self.client.get(
            "/status",
            headers={"X-Login-Desktop-Token": "wrong-token"},
        )
        self.assertEqual(403, response.status_code)

    def test_valid_token_is_accepted(self):
        response = self.client.get(
            "/status",
            headers={"X-Login-Desktop-Token": lds.AUTH_TOKEN},
        )
        self.assertNotEqual(403, response.status_code)

    def test_non_local_host_header_is_rejected(self):
        # DNS rebinding 防护：攻击者域名 Host 即使带合法 token 也必须被拒
        response = self.client.get(
            "/health",
            headers={
                "X-Login-Desktop-Token": lds.AUTH_TOKEN,
                "Host": "evil.example.com",
            },
        )
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
