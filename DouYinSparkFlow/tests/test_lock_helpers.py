import os
import tempfile
import time
import unittest
from pathlib import Path

from core import tasks


class SafeUnlinkLockTests(unittest.TestCase):
    """_safe_unlink_lock：删除前重读校验内容未变（TOCTOU 防护）。"""

    def test_removes_lock_when_content_matches_with_trailing_newline(self):
        # 锁文件实际写入带换行（"123\n"），expected 已 strip —— 两侧统一 strip 后必须正常删除
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "task.run.lock"
            lock_path.write_text("123\n", encoding="utf-8")
            self.assertTrue(tasks._safe_unlink_lock(lock_path, "123"))
            self.assertFalse(lock_path.exists())

    def test_removes_empty_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "task.run.lock"
            lock_path.write_text("", encoding="utf-8")
            self.assertTrue(tasks._safe_unlink_lock(lock_path, ""))
            self.assertFalse(lock_path.exists())

    def test_does_not_remove_when_content_changed(self):
        # 他人已重建锁（内容变化）：不得删除
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "task.run.lock"
            lock_path.write_text("456\n", encoding="utf-8")
            self.assertFalse(tasks._safe_unlink_lock(lock_path, "123"))
            self.assertTrue(lock_path.exists())

    def test_missing_file_returns_false(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "nope.lock"
            self.assertFalse(tasks._safe_unlink_lock(lock_path, "123"))


class BrowserAccountLockStaleTests(unittest.TestCase):
    """_browser_account_lock_is_stale：不可解析内容 + 短年龄阈值判 stale（H2 修复）。"""

    def _make_lock(self, temp_dir, content, age_seconds):
        lock_path = Path(temp_dir) / "account.lock"
        lock_path.write_text(content, encoding="utf-8")
        old = time.time() - age_seconds
        os.utime(lock_path, (old, old))
        return lock_path

    def test_unreadable_lock_older_than_short_threshold_is_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = self._make_lock(temp_dir, "", 120)
            is_stale, reason = tasks._browser_account_lock_is_stale(lock_path, "")
            self.assertTrue(is_stale)
            self.assertIn("unreadable", reason)

    def test_unreadable_fresh_lock_is_not_stale(self):
        # 刚创建未写入 PID 的锁（age < 60s）：不得判 stale，避免误删并发创建中的锁
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = self._make_lock(temp_dir, "", 10)
            is_stale, _ = tasks._browser_account_lock_is_stale(lock_path, "")
            self.assertFalse(is_stale)

    def test_missing_pid_lock_is_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = self._make_lock(temp_dir, "pid=99999999\n", 10)
            is_stale, reason = tasks._browser_account_lock_is_stale(
                lock_path, "pid=99999999\n"
            )
            self.assertTrue(is_stale)
            self.assertIn("missing pid", reason)

    def test_live_pid_fresh_lock_is_not_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content = f"pid={os.getpid()}\n"
            lock_path = self._make_lock(temp_dir, content, 10)
            is_stale, _ = tasks._browser_account_lock_is_stale(lock_path, content)
            self.assertFalse(is_stale)


if __name__ == "__main__":
    unittest.main()
