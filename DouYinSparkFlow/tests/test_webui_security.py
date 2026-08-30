"""Web 控制台安全收敛测试：安全头（M14）、WS Host/Origin 校验（H3）、ui_port 容错（M16）、
ops_log_file 路径约束（L19）、asset 路径穿越（L20）。"""

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from webui import app as app_module


class SecurityHeadersTests(unittest.TestCase):
    """M14：纵深防御安全头存在且不覆盖既有 Cache-Control。"""

    def test_security_headers_present_on_pages(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        with patch.object(app_module, "current_user", return_value="admin"):
            response = client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertEqual("nosniff", response.headers["x-content-type-options"])
        self.assertEqual("SAMEORIGIN", response.headers["x-frame-options"])
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        # 不破坏既有断言：cache-control 仍为 no-store
        self.assertEqual("no-store", response.headers["cache-control"])


class WebSocketOriginGuardTests(unittest.TestCase):
    """H3：WebSocket 握手手工校验 Host/Origin（不经过 BaseHTTPMiddleware）。"""

    def test_websocket_rejects_non_local_origin(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with client.websocket_connect(
                "/login-desktop/proxy/websockify",
                headers={"Origin": "https://evil.example.com"},
            ):
                pass
        self.assertEqual(4403, ctx.exception.code)

    def test_websocket_rejects_non_local_host(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with client.websocket_connect(
                "/login-desktop/proxy/websockify",
                headers={"Host": "evil.example.com"},
            ):
                pass
        self.assertEqual(4403, ctx.exception.code)


class UiPortCoerceTests(unittest.TestCase):
    """M16：/settings 保存时 ui_port 非数字不再 500。"""

    def test_non_numeric_ui_port_falls_back_to_default(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        saved = {}

        def fake_save(settings):
            saved.update(settings)

        with (
            patch.object(app_module, "current_user", return_value="admin"),
            patch.object(app_module, "validate_csrf", return_value=True),
            patch.object(app_module, "get_app_settings", return_value={"ui_port": 8787, "ops_log_file": "", "proxy_refresh_script": "", "login_desktop_api_url": "http://127.0.0.1:18090"}),
            patch.object(app_module, "save_app_settings", side_effect=fake_save),
        ):
            response = client.post("/settings", data={"csrf_token": "test", "ui_port": "abc"})

        self.assertEqual(200, response.status_code)
        self.assertEqual(8787, saved["ui_port"])

    def test_zero_ui_port_gets_minimum_one(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        saved = {}

        def fake_save(settings):
            saved.update(settings)

        with (
            patch.object(app_module, "current_user", return_value="admin"),
            patch.object(app_module, "validate_csrf", return_value=True),
            patch.object(app_module, "get_app_settings", return_value={"ui_port": 8787, "ops_log_file": "", "proxy_refresh_script": "", "login_desktop_api_url": "http://127.0.0.1:18090"}),
            patch.object(app_module, "save_app_settings", side_effect=fake_save),
        ):
            response = client.post("/settings", data={"csrf_token": "test", "ui_port": "0"})

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, saved["ui_port"])


class OpsLogFileConstraintTests(unittest.TestCase):
    """L19：ops_log_file 必须位于 data_dir()/logs/ 内。"""

    def test_save_rejects_path_outside_logs_dir(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        with (
            patch.object(app_module, "current_user", return_value="admin"),
            patch.object(app_module, "validate_csrf", return_value=True),
            patch.object(app_module, "get_app_settings", return_value={"ui_port": 8787, "ops_log_file": "", "proxy_refresh_script": "", "login_desktop_api_url": "http://127.0.0.1:18090"}),
            patch.object(app_module, "data_dir", return_value=Path("C:/appdata/DouYinSparkFlow")),
            patch.object(app_module, "save_app_settings") as save_mock,
        ):
            response = client.post(
                "/settings",
                data={"csrf_token": "test", "ops_log_file": "C:/Windows/System32/evil.log"},
            )

        self.assertEqual(200, response.status_code)
        save_mock.assert_not_called()

    def test_read_rejects_path_outside_logs_dir(self):
        # _ops_log_content 是 app 工厂内部函数，经 /ops/logs/download 端点验证
        client = TestClient(app_module.app, raise_server_exceptions=False)
        with (
            patch.object(app_module, "current_user", return_value="admin"),
            patch.object(app_module, "get_app_settings", return_value={"ops_log_file": "C:/Windows/System32/evil.log"}),
            patch.object(app_module, "data_dir", return_value=Path("C:/appdata/DouYinSparkFlow")),
        ):
            response = client.get("/ops/logs/download")
        self.assertEqual(200, response.status_code)
        self.assertEqual(b"", response.content)


class AssetPathTraversalTests(unittest.TestCase):
    """L20：fetch_login_desktop_asset 拒绝含 .. 的路径段。"""

    def test_rejects_parent_segments(self):
        for bad in ("../secret", "a/../b"):
            with self.assertRaisesRegex(RuntimeError, "invalid asset path"):
                app_module.fetch_login_desktop_asset(bad)

    def test_url_encoded_dots_are_escaped_not_traversed(self):
        # "..%2Fetc" 不含字面 ".." 段：通过检查后 % 被 quote 编码为 %25，
        # 上游收到字面 "..%252Fetc"，不会路径穿越
        with patch.object(app_module, "login_desktop_novnc_http_url", return_value="http://127.0.0.1:6080"):
            with patch.object(app_module.urllib.request, "urlopen", side_effect=RuntimeError("upstream")):
                with self.assertRaisesRegex(RuntimeError, "upstream"):
                    app_module.fetch_login_desktop_asset("..%2Fetc")

    def test_allows_normal_asset(self):
        with patch.object(app_module, "login_desktop_novnc_http_url", return_value="http://127.0.0.1:6080"):
            with patch.object(app_module.urllib.request, "urlopen", side_effect=RuntimeError("upstream")):
                with self.assertRaisesRegex(RuntimeError, "upstream"):
                    app_module.fetch_login_desktop_asset("vnc.html")


if __name__ == "__main__":
    unittest.main()
