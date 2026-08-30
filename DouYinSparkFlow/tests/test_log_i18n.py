"""日志中文化契约测试：面向用户的发送流程日志必须为中文。

背景：用户反馈"发送记录里的日志有些是英文看不懂"。日志页显示 douyin-sparkflow.log
（任务子进程 tasks.py 写入）。核心发送流程（启动/选中好友/生成消息/发送确认/失败
入队/暂停/手动补发/窗口调度）的 INFO/WARNING 日志已中文化；技术细节（IM observer
事件、CDP、锁、网络探测、env 解析）保留英文。
"""

import unittest
from pathlib import Path

TASKS_PATH = Path(__file__).resolve().parents[1] / "core" / "tasks.py"


class LogI18nContractTests(unittest.TestCase):
    """用户可见的关键流程日志必须为中文（英文残留会被契约拦下）。"""

    def setUp(self):
        self.content = TASKS_PATH.read_text(encoding="utf-8")

    def test_send_flow_key_logs_are_chinese(self):
        # 核心发送流程日志中文化
        for chinese_text in (
            "账号 %s 开始发送消息流程",
            "消息发送确认成功",
            "发送失败已入队",
            "已记录发送成功历史",
            "已选中目标好友",
            "按 Enter 发送消息",
            "开始执行发送任务",
            "已有发送任务正在运行",
            "手动补发",
            "账号 %s 已暂停浏览器发送",
            "已选定抖音网络路由",
            "定位聊天输入框完成",
        ):
            self.assertIn(chinese_text, self.content, f"缺少中文化日志：{chinese_text}")

    def test_user_visible_english_phrases_removed(self):
        # 用户可见路径不应再出现这些英文文案
        for english in (
            '"Message send confirmed for %s/%s by server receipt',
            '"Starting tasks with config"',
            '"Queued failed browser send for %s/%s',
            '"Pressing Enter to send message for %s/%s"',
            '"Account %s started the message flow"',
            '"Skipping task run because another task run',
        ):
            self.assertNotIn(english, self.content, f"残留英文文案：{english}")


if __name__ == "__main__":
    unittest.main()
