"""杂项修复测试：IPv6 Host 解析与空 Host 拒绝（L1）、节日窗口可配置（L27）、mjs 间隔上限。"""

import unittest
from datetime import date
from pathlib import Path

from core import msg_builder
from utils.web_middleware import _hostname_allowed

MJS_PATH = Path(__file__).resolve().parents[1] / "core" / "protocol_sender.mjs"


class HostnameAllowedTests(unittest.TestCase):
    """L1：IPv6 Host 先剥离 [] 再解析；空 Host 直接拒绝。"""

    def test_empty_host_is_rejected(self):
        self.assertFalse(_hostname_allowed(""))
        self.assertFalse(_hostname_allowed(None))

    def test_ipv4_with_port_allowed(self):
        self.assertTrue(_hostname_allowed("127.0.0.1:8787"))
        self.assertTrue(_hostname_allowed("localhost:8787"))

    def test_ipv6_literal_allowed(self):
        # 原实现 split(":") 把 "[" 当主机名 → 白名单 ::1 永远走不到
        self.assertTrue(_hostname_allowed("[::1]:8787"))
        self.assertTrue(_hostname_allowed("[::1]"))

    def test_evil_host_rejected(self):
        self.assertFalse(_hostname_allowed("evil.example.com"))
        self.assertFalse(_hostname_allowed("evil.example.com:80"))


class FestivalWindowTests(unittest.TestCase):
    """L27：节日窗口可从 config 覆盖（2027 年后默认窗口失效问题）。"""

    def test_default_window_still_works(self):
        config = {"happyNewYear": {"enabled": True}}
        self.assertTrue(msg_builder._is_holiday_mode_enabled(config, date(2026, 2, 20)))
        self.assertFalse(msg_builder._is_holiday_mode_enabled(config, date(2026, 4, 1)))

    def test_config_override_window(self):
        config = {
            "happyNewYear": {
                "enabled": True,
                "festivalWindow": {"start": "2027-01-28", "end": "2027-02-15"},
            }
        }
        # 默认窗口外但覆盖窗口内：2027 年节日生效
        self.assertTrue(msg_builder._is_holiday_mode_enabled(config, date(2027, 2, 1)))
        self.assertFalse(msg_builder._is_holiday_mode_enabled(config, date(2026, 2, 20)))

    def test_invalid_override_falls_back_to_default(self):
        config = {
            "happyNewYear": {"enabled": True, "festivalWindow": {"start": "not-a-date", "end": "2027-02-15"}}
        }
        self.assertTrue(msg_builder._is_holiday_mode_enabled(config, date(2026, 2, 20)))


class ProtocolSenderCapTests(unittest.TestCase):
    """Nit：mjs 消息间隔上限契约。"""

    def test_interval_cap_present(self):
        source = MJS_PATH.read_text(encoding="utf-8")
        self.assertIn("MESSAGE_INTERVAL_MAX_CAP_SECONDS", source)


if __name__ == "__main__":
    unittest.main()
