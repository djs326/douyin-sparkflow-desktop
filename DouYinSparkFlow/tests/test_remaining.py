"""M15/L22 修复测试：日志尾部读取、JSON tail 端点、时区口径统一。"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from zoneinfo import ZoneInfo

from webui import app as app_module
from webui import ops


class ReadLogTailTests(unittest.TestCase):
    """M15：read_log_tail 只读尾部，不随文件增长全量读。"""

    def _make_log(self, directory, lines):
        log_path = Path(directory) / "douyin-sparkflow.log"
        log_path.write_text("\n".join(f"line-{i:05d}" for i in range(lines)) + "\n", encoding="utf-8")
        return log_path

    def test_tail_returns_last_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = self._make_log(temp_dir, 500)
            with patch.object(ops, "get_app_settings", return_value={"ops_log_file": str(log_path)}):
                text = ops.read_log_tail(50)
            self.assertEqual(50, len(text.splitlines()))
            self.assertTrue(text.endswith("line-00499"))

    def test_small_file_returns_all_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = self._make_log(temp_dir, 5)
            with patch.object(ops, "get_app_settings", return_value={"ops_log_file": str(log_path)}):
                text = ops.read_log_tail(200)
            self.assertEqual(5, len(text.splitlines()))

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "nope.log"
            with patch.object(ops, "get_app_settings", return_value={"ops_log_file": str(missing)}):
                self.assertEqual("", ops.read_log_tail())


class LogsTailEndpointTests(unittest.TestCase):
    """M15：/ops/logs/tail 返回 JSON。"""

    def test_tail_endpoint_returns_json(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        with (
            patch.object(app_module, "current_user", return_value="admin"),
            patch.object(app_module, "read_log_tail", return_value="hello\nworld"),
        ):
            response = client.get("/ops/logs/tail")
        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertEqual("hello\nworld", data["text"])
        self.assertEqual(2, data["lines"])


class ScheduleTimezoneConsistencyTests(unittest.TestCase):
    """L22：app.py 与 ops.py 的时区实现一致（SPARKFLOW_TIMEZONE 生效）。"""

    def test_app_schedule_timezone_is_ops_implementation(self):
        self.assertIs(app_module._schedule_timezone, ops._schedule_timezone)

    def test_timezone_env_honored_by_shared_impl(self):
        with patch.dict(os.environ, {"SPARKFLOW_TIMEZONE": "Asia/Tokyo"}, clear=False):
            tz = ops._schedule_timezone()
        self.assertEqual("Asia/Tokyo", getattr(tz, "key", str(tz)))

    def test_default_is_asia_shanghai(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in ("SPARKFLOW_TIMEZONE", "TZ"):
                os.environ.pop(key, None)
            tz = ops._schedule_timezone()
        self.assertEqual("Asia/Shanghai", getattr(tz, "key", str(tz)))


class LogsClearEndpointTests(unittest.TestCase):
    """日志清除端点：POST + CSRF + 路径校验 + 截断。"""

    def test_clear_truncates_log_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = Path(temp_dir) / "logs"
            logs_dir.mkdir(parents=True)
            log_file = logs_dir / "douyin-sparkflow.log"
            log_file.write_text("line1\nline2\n", encoding="utf-8")
            client = TestClient(app_module.app, raise_server_exceptions=False)
            with (
                patch.object(app_module, "current_user", return_value="admin"),
                patch.object(app_module, "validate_csrf", return_value=True),
                patch.object(app_module, "get_app_settings", return_value={"ops_log_file": str(log_file)}),
                patch.object(app_module, "data_dir", return_value=Path(temp_dir)),
            ):
                response = client.post("/ops/logs/clear", data={"csrf_token": "test"})
            self.assertEqual(200, response.status_code)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(0, log_file.stat().st_size)

    def test_clear_rejects_bad_csrf(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        with (
            patch.object(app_module, "current_user", return_value="admin"),
            patch.object(app_module, "validate_csrf", return_value=False),
        ):
            response = client.post("/ops/logs/clear", data={"csrf_token": "bad"})
        self.assertEqual(403, response.status_code)

    def test_clear_rejects_path_outside_logs_dir(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        with (
            patch.object(app_module, "current_user", return_value="admin"),
            patch.object(app_module, "validate_csrf", return_value=True),
            patch.object(app_module, "get_app_settings", return_value={"ops_log_file": "C:/Windows/System32/evil.log"}),
            patch.object(app_module, "data_dir", return_value=Path("C:/appdata/DouYinSparkFlow")),
        ):
            response = client.post("/ops/logs/clear", data={"csrf_token": "test"})
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.json()["ok"])


if __name__ == "__main__":
    unittest.main()
