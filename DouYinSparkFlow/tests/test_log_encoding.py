"""日志编码修复测试：GBK/UTF-8 混合日志正确解码显示（用户可见乱码问题）。

背景：douyin-sparkflow.log 由任务子进程 stdout 重定向写入，实测为 GBK 编码；
app.log（FileHandler utf-8）为 UTF-8。read_log_tail 曾硬编码 UTF-8 解码导致
GBK 中文乱码（M15 回归），现改为编码自动探测。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.logger import decode_bytes_autodetect
from webui import ops


class DecodeBytesAutodetectTests(unittest.TestCase):
    """decode_bytes_autodetect：UTF-8 优先，GBK 回退。"""

    def test_utf8_bytes_decode_as_utf8(self):
        data = "账号：吃饺子不吃饺子，状态：已发送\n".encode("utf-8")
        self.assertEqual("账号：吃饺子不吃饺子，状态：已发送\n", decode_bytes_autodetect(data))

    def test_gbk_bytes_fallback_to_gbk(self):
        text = "账号：吃饺子不吃饺子，状态：已发送\n"
        data = text.encode("gbk")
        self.assertEqual(text, decode_bytes_autodetect(data))

    def test_ascii_only_passes_both(self):
        self.assertEqual("hello world", decode_bytes_autodetect(b"hello world"))

    def test_invalid_bytes_falls_back_to_replace(self):
        # 混合损坏字节：不抛异常，replace 兜底
        result = decode_bytes_autodetect(b"\xff\xfe\x00\x81")
        self.assertIsInstance(result, str)


class ReadLogTailEncodingTests(unittest.TestCase):
    """read_log_tail 对 GBK 日志文件必须正确显示中文（M15 回归修复）。"""

    def _make_gbk_log(self, directory, line_count=20):
        log_path = Path(directory) / "douyin-sparkflow.log"
        lines = [f"账号：吃饺子不吃饺子 目标：测试目标{i} 状态：已发送" for i in range(line_count)]
        log_path.write_bytes(("\n".join(lines) + "\n").encode("gbk"))
        return log_path

    def test_gbk_log_tail_shows_chinese(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = self._make_gbk_log(temp_dir)
            with patch.object(ops, "get_app_settings", return_value={"ops_log_file": str(log_path)}):
                text = ops.read_log_tail(50)
            self.assertIn("吃饺子不吃饺子", text)
            self.assertIn("测试目标19", text)
            self.assertNotIn("\ufffd", text)  # 无替换符（乱码）

    def test_utf8_log_tail_shows_chinese(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "douyin-sparkflow.log"
            log_path.write_text("账号：测试号 状态：正常\n", encoding="utf-8")
            with patch.object(ops, "get_app_settings", return_value={"ops_log_file": str(log_path)}):
                text = ops.read_log_tail(50)
            self.assertIn("测试号", text)

    def test_large_gbk_log_truncation_does_not_mangle(self):
        # >64KB 的 GBK 日志：截断起点可能落在汉字中间（43% 概率整段错位乱码）。
        # 用 "汉"*20+"\n"（GBK 字节长 41，截断偏移 (-65536) mod 41 = 23 恰好落在
        # 汉字第二字节——修复前必出 \ufffd 的构造）验证回退到最近换行后整段无替换符。
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "douyin-sparkflow.log"
            line = "汉" * 20 + "\n"
            log_path.write_bytes((line * 3000).encode("gbk"))  # 约 120KB，远超 64KB 窗口
            with patch.object(ops, "get_app_settings", return_value={"ops_log_file": str(log_path)}):
                text = ops.read_log_tail(200)
            self.assertNotIn("\ufffd", text)
            self.assertIn("汉汉汉", text)
            # 首行必须完整（截断起点回退到行首）
            self.assertEqual(line.rstrip("\n"), text.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
