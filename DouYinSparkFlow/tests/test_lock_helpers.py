import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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


class TaskRunLockIntegrationTests(unittest.TestCase):
    """task_run_lock 的集成路径：死 pid 锁删除重抢 / 短龄空锁不删除。"""

    def _lock_path(self, root):
        lock_path = Path(root) / "logs" / "task.run.lock"
        lock_path.parent.mkdir(parents=True)
        return lock_path

    def test_removes_dead_pid_lock_and_acquires(self):
        # 死 pid 残留锁（"99999999\n"，年龄 120s）：首轮 open 抛 FileExistsError，
        # 应删除残留锁并重抢成功，最终锁内容为当前进程 pid。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = self._lock_path(root)
            lock_path.write_text("99999999\n", encoding="utf-8")
            old = time.time() - 120
            os.utime(lock_path, (old, old))

            real_open = Path.open
            calls = {"count": 0}

            def fake_open(path, *args, **kwargs):
                if str(path) == str(lock_path) and calls["count"] == 0:
                    calls["count"] += 1
                    raise FileExistsError
                return real_open(path, *args, **kwargs)

            with (
                patch.object(Path, "open", fake_open),
                patch.object(tasks, "data_dir", return_value=root),
            ):
                with tasks.task_run_lock():
                    self.assertTrue(lock_path.exists())
                    self.assertEqual(
                        lock_path.read_text(encoding="utf-8").strip(),
                        str(os.getpid()),
                    )

    def test_fresh_empty_lock_is_not_removed_and_times_out(self):
        # 短龄空锁（创建中，age<60s）：不得删除；open 持续失败时应按超时抛出，
        # 而不是无限忙循环或误删正在创建中的锁。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = self._lock_path(root)
            lock_path.write_text("", encoding="utf-8")

            real_open = Path.open

            def fake_open(path, *args, **kwargs):
                if str(path) == str(lock_path):
                    raise FileExistsError
                return real_open(path, *args, **kwargs)

            # monotonic 序列：started_at=0；首次超时检查 0.1（未超时，进入 sleep）；
            # 第二次检查 2000（2000-0 > 1800 → 超时抛出）
            with (
                patch.object(Path, "open", fake_open),
                patch.object(tasks, "data_dir", return_value=root),
                patch.object(tasks.time, "monotonic", side_effect=[0, 0.1, 2000]),
                patch.object(tasks.time, "sleep", return_value=None),
            ):
                with self.assertRaises(tasks.TaskRunAlreadyInProgress):
                    with tasks.task_run_lock():
                        self.fail("不应成功获取锁")

            # 空锁必须保留（未被删除）
            self.assertTrue(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
