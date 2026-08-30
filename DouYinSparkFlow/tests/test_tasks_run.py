"""任务运行收敛测试：发送窗口防御（M11）、targets 规范化（L8）、确认超时配置化（L10）。"""

import os
import unittest
from unittest.mock import patch

from core import tasks


class NormalizeSendWindowTests(unittest.TestCase):
    """M11：config.json 中 null/非数字不再崩溃。"""

    def test_invalid_start_hour_falls_back(self):
        window = tasks._normalize_send_window({"dailySendWindow": {"enabled": True, "startHour": None, "endHour": "18"}})
        self.assertEqual(10, window["startHour"])
        self.assertEqual(18, window["endHour"])

    def test_non_numeric_values_do_not_crash(self):
        window = tasks._normalize_send_window(
            {"dailySendWindow": {"enabled": True, "startHour": "abc", "endHour": "xyz", "scheduleIntervalMinutes": None}}
        )
        self.assertEqual(10, window["startHour"])
        self.assertEqual(18, window["endHour"])
        self.assertEqual(10, window["scheduleIntervalMinutes"])

    def test_valid_values_preserved(self):
        window = tasks._normalize_send_window(
            {"dailySendWindow": {"enabled": True, "startHour": 9, "endHour": 20, "scheduleIntervalMinutes": 15}}
        )
        self.assertEqual(9, window["startHour"])
        self.assertEqual(20, window["endHour"])
        self.assertEqual(15, window["scheduleIntervalMinutes"])


class NormalizedTargetMapTests(unittest.TestCase):
    """L8：target 映射值统一规范化，发送/写入/查询口径一致。"""

    def test_map_values_are_normalized_names(self):
        mapping = tasks._build_normalized_target_map(["  张三  ", "李四"])
        self.assertEqual("张三", mapping["张三"])
        self.assertEqual("李四", mapping["李四"])

    def test_fullwidth_space_target_is_normalized(self):
        mapping = tasks._build_normalized_target_map(["　王五　"])
        self.assertIn("王五", mapping)


class ConfirmDeadlineTests(unittest.TestCase):
    """L10：发送确认超时配置化。"""

    def test_default_is_eight_seconds(self):
        with patch.dict(os.environ, {}, clear=False):
            if "SPARKFLOW_CONFIRM_DEADLINE_SECONDS" in os.environ:
                del os.environ["SPARKFLOW_CONFIRM_DEADLINE_SECONDS"]
            self.assertEqual(8, tasks._confirm_message_sent_deadline_seconds())

    def test_env_override(self):
        with patch.dict(os.environ, {"SPARKFLOW_CONFIRM_DEADLINE_SECONDS": "15"}, clear=False):
            self.assertEqual(15, tasks._confirm_message_sent_deadline_seconds())

    def test_invalid_env_falls_back(self):
        with patch.dict(os.environ, {"SPARKFLOW_CONFIRM_DEADLINE_SECONDS": "abc"}, clear=False):
            self.assertEqual(8, tasks._confirm_message_sent_deadline_seconds())


class NetworkContractPreservedTests(unittest.TestCase):
    """契约：网络模式选择相关断言字符串必须原样保留（test_network_fallback 依赖）。"""

    def test_network_mode_contract_strings_present(self):
        source = tasks.__file__
        with open(source, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("select_douyin_network_mode(CREATOR_HOME_URL)", content)
        self.assertIn("get_browser(network_mode=network_mode)", content)


if __name__ == "__main__":
    unittest.main()
