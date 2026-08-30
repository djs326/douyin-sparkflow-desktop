"""任务运行收敛测试：发送窗口防御（M11）、targets 规范化（L8）、确认超时配置化（L10）。"""

import os
import unittest
from datetime import datetime, timezone
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

    def test_negative_start_hour_disables_window(self):
        # 显式负值保持"禁用窗口"语义，不被 clamp 成 0 点开始发
        window = tasks._normalize_send_window({"dailySendWindow": {"enabled": True, "startHour": -5, "endHour": 18}})
        self.assertFalse(window["enabled"])


class NormalizedTargetMapTests(unittest.TestCase):
    """L8：target 映射值统一规范化，发送/写入/查询口径一致。"""

    def test_map_values_are_normalized_names(self):
        mapping = tasks._build_normalized_target_map(["  张三  ", "李四"])
        self.assertEqual("张三", mapping["张三"])
        self.assertEqual("李四", mapping["李四"])

    def test_fullwidth_space_target_is_normalized(self):
        mapping = tasks._build_normalized_target_map(["　王五　"])
        self.assertIn("王五", mapping)


class SchedulerNormalizationClosureTests(unittest.TestCase):
    """L8 闭环：调度层查询（已发送判定/补发）与 history key 同走规范化名。

    targets 原始串含空白/全角空格时，已发送目标不得被判未发送而重复重发。
    """

    def setUp(self):
        self.now = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)
        self.window = {
            "enabled": True,
            "startHour": 10,
            "endHour": 18,
            "scheduleIntervalMinutes": 20,
        }

    def _user_with_strong_history(self, raw_target, history_key):
        return {
            "username": "demo",
            "unique_id": "demo",
            "targets": [raw_target],
            "message_history": {
                history_key: {
                    "sentAt": self.now.isoformat(),
                    "status": "confirmed",
                    "confirmationLevel": "strong",
                    "needsVerification": False,
                }
            },
        }

    def test_pending_unsent_matches_normalized_history_key(self):
        # 原始 targets 含首尾空白，history key 为规范化名：不得重复补发
        user = self._user_with_strong_history("  张三  ", "张三")
        retry_targets, _ = tasks._pending_unsent_targets(user, self.now)
        self.assertEqual([], retry_targets)

    def test_pending_failed_matches_normalized_history_key(self):
        # 全角空格场景：main 分支此处会 miss 并重复重发（L8 引入的回归修复）
        user = self._user_with_strong_history("\u3000王五\u3000", "王五")
        self.assertEqual([], tasks._pending_failed_targets(user, self.now))

    def test_select_due_targets_treats_normalized_history_as_sent(self):
        user = self._user_with_strong_history("  张三  ", "张三")
        due, already_sent, _, _ = tasks._select_due_targets(user, self.window, self.now)
        self.assertEqual([], due)
        self.assertEqual(["张三"], already_sent)

    def test_clean_targets_are_unaffected(self):
        user = self._user_with_strong_history("target", "target")
        retry_targets, _ = tasks._pending_unsent_targets(user, self.now)
        self.assertEqual([], retry_targets)


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
