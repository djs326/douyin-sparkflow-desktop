"""登录桌面服务（18090）安全收敛测试：/debug/* 开关、goto 白名单、敏感头剥离。"""

import unittest

from fastapi.testclient import TestClient
from unittest.mock import patch

import login_desktop_server as lds


class DebugEndpointGateTests(unittest.TestCase):
    """H6：/debug/* 默认关闭；仅 LOGIN_DESKTOP_DEBUG=1 时可用。"""

    def setUp(self):
        self.client = TestClient(lds.app)

    def test_debug_endpoints_are_disabled_by_default(self):
        # 未设 LOGIN_DESKTOP_DEBUG：带合法 token 访问 /debug/* 也必须 403
        with patch.object(lds, "LOGIN_DESKTOP_DEBUG", False):
            for method, path, kwargs in (
                ("get", "/debug/screenshot", {}),
                ("get", "/debug/snapshot", {}),
                ("post", "/debug/action", {"json": {"action": "list_frames"}}),
                ("post", "/debug/net_capture", {"json": {"type": "get"}}),
                ("get", "/debug/net_log", {}),
            ):
                response = getattr(self.client, method)(
                    path,
                    headers={"X-Login-Desktop-Token": lds.AUTH_TOKEN},
                    **kwargs,
                )
                self.assertEqual(
                    403,
                    response.status_code,
                    f"expected 403 for disabled debug {method.upper()} {path}, got {response.status_code}",
                )

    def test_debug_gate_raises_when_disabled(self):
        with patch.object(lds, "LOGIN_DESKTOP_DEBUG", False):
            with self.assertRaises(Exception) as ctx:
                lds._debug_enabled()
            self.assertEqual(403, ctx.exception.status_code)

    def test_debug_gate_passes_when_enabled(self):
        with patch.object(lds, "LOGIN_DESKTOP_DEBUG", True):
            self.assertIsNone(lds._debug_enabled())


class DebugGotoWhitelistTests(unittest.TestCase):
    """H6：/debug/action 的 goto 仅允许抖音官方域名。"""

    def test_allows_douyin_official_hosts(self):
        self.assertTrue(lds._debug_goto_allowed("https://creator.douyin.com/"))
        self.assertTrue(lds._debug_goto_allowed("https://www.douyin.com/user/self"))
        self.assertTrue(lds._debug_goto_allowed("http://creator.douyin.com/"))

    def test_rejects_file_and_arbitrary_urls(self):
        self.assertFalse(lds._debug_goto_allowed("file:///C:/Windows/win.ini"))
        self.assertFalse(lds._debug_goto_allowed("https://evil.example.com/"))
        self.assertFalse(lds._debug_goto_allowed("http://127.0.0.1:18090/"))
        self.assertFalse(lds._debug_goto_allowed("javascript:alert(1)"))
        self.assertFalse(lds._debug_goto_allowed(""))

    def test_rejects_douyin_lookalike_subdomains(self):
        # 仅精确白名单；子域名/同站仿冒一律拒绝
        self.assertFalse(lds._debug_goto_allowed("https://creator.douyin.com.evil.com/"))


class SanitizeHeadersTests(unittest.TestCase):
    """M13：网络捕获落库前剥离敏感头。"""

    def test_sensitive_headers_are_redacted(self):
        headers = {
            "Cookie": "sessionid=abc",
            "Authorization": "Bearer secret",
            "X-Tt-Token": "tok123",
            "User-Agent": "Mozilla/5.0",
        }
        sanitized = lds._sanitize_headers(headers)
        self.assertEqual("[redacted]", sanitized["Cookie"])
        self.assertEqual("[redacted]", sanitized["Authorization"])
        self.assertEqual("[redacted]", sanitized["X-Tt-Token"])
        self.assertEqual("Mozilla/5.0", sanitized["User-Agent"])
        self.assertNotIn("sessionid=abc", str(sanitized))
        self.assertNotIn("Bearer secret", str(sanitized))

    def test_empty_headers(self):
        self.assertEqual({}, lds._sanitize_headers(None))
        self.assertEqual({}, lds._sanitize_headers({}))


if __name__ == "__main__":
    unittest.main()
