import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from webui import ops


class TaskLockStatusSuspiciousTests(unittest.TestCase):
    """M7：假活锁告警——pid 存活但锁龄超上限时标记 suspicious（不自动删锁）。"""

    def test_live_pid_but_very_old_lock_is_suspicious(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = root / "logs" / "task.run.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
            old = time.time() - 7 * 3600  # 7 小时前
            os.utime(lock_path, (old, old))

            with patch.object(ops, "data_dir", return_value=root):
                status = ops.task_run_lock_status()

            self.assertTrue(status["running"])
            self.assertTrue(status["suspicious"])
            self.assertIn("lock_older_than", status["suspiciousReason"])
            self.assertTrue(lock_path.exists())  # 只告警，不删锁


class RunTaskNowTargetRefsTests(unittest.TestCase):
    """H4.1：run_task_now 将 target_refs 传递给子进程环境变量。"""

    def test_target_refs_are_forwarded_to_child_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "task.log"
            captured = {}

            with (
                patch.object(ops, "task_run_lock_status", return_value={"running": False}),
                patch.object(ops, "get_app_settings", return_value={"ops_log_file": str(log_file)}),
                patch.object(ops, "build_task_run_spec", return_value=(["python", "main.py", "--doTask"], Path(temp_dir))),
                patch.object(ops, "run_background_command", side_effect=lambda args, log, cwd=None, env=None: captured.update(env=env) or 12345),
            ):
                pid = ops.run_task_now(account_refs=["uid-1"], target_refs=["目标A"])

            self.assertEqual(12345, pid)
            self.assertEqual("uid-1", captured["env"]["SPARKFLOW_ACCOUNT_REFS"])
            self.assertEqual("目标A", captured["env"]["SPARKFLOW_TARGET_REFS"])


class StopRunningTaskTests(unittest.TestCase):
    """H4.2：stop_running_task 在 taskkill 后轮询确认进程消失，删锁前重读比对，失败保留锁。"""

    def _lock_with_pid(self, root, pid):
        lock_path = root / "logs" / "task.run.lock"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text(f"{pid}\n", encoding="utf-8")
        return lock_path

    def test_stops_task_and_removes_lock_when_process_dies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = self._lock_with_pid(root, 12345)

            with (
                patch.object(ops, "data_dir", return_value=root),
                patch.object(ops, "task_run_lock_status", return_value={"running": True, "pid": 12345}),
                patch.object(ops, "_pid_is_alive", return_value=False),  # taskkill 后进程已消失
                patch.object(ops.subprocess, "run"),
            ):
                ok, message = ops.stop_running_task()

            self.assertTrue(ok)
            self.assertFalse(lock_path.exists())

    def test_keeps_lock_when_process_survives_taskkill(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = self._lock_with_pid(root, 12345)

            with (
                patch.object(ops, "data_dir", return_value=root),
                patch.object(ops, "task_run_lock_status", return_value={"running": True, "pid": 12345}),
                patch.object(ops, "_pid_is_alive", return_value=True),  # 进程仍存活
                patch.object(ops.subprocess, "run"),
                patch.object(ops.time, "monotonic", side_effect=[0, 16]),  # 首次取 deadline，轮询立即超时
                patch.object(ops.time, "sleep"),
            ):
                ok, message = ops.stop_running_task()

            self.assertFalse(ok)
            self.assertIn("仍在运行", message)
            self.assertTrue(lock_path.exists())  # 锁保留，防止新任务并发

    def test_keeps_lock_when_owner_changed(self):
        # taskkill 后进程消失，但锁已被其他进程接管（内容 pid 变化）：不删除
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = self._lock_with_pid(root, 99999)  # 锁内容已是别的 pid

            with (
                patch.object(ops, "data_dir", return_value=root),
                patch.object(ops, "task_run_lock_status", return_value={"running": True, "pid": 12345}),
                patch.object(ops, "_pid_is_alive", return_value=False),
                patch.object(ops.subprocess, "run"),
            ):
                ok, message = ops.stop_running_task()

            self.assertFalse(ok)
            self.assertIn("接管", message)
            self.assertTrue(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
