"""数据层收敛测试：原子读-改-写（M5）、深合并（L5）、锁文件不增长（L2）、ACL 收紧（M4/L24）。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import config


class UpdateUserDataTests(unittest.TestCase):
    """M5：update_user_data 在锁内完成读-改-写，mutator 返回 None 时不写盘。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_root = Path(self.temp_dir.name) / "data"
        self.users_path = Path(self.temp_dir.name) / "usersData.json"
        self.data_root.mkdir(parents=True)
        self.users_path.write_text(
            json.dumps([{"unique_id": "123", "account_ref": "acc-1", "username": "demo", "targets": []}]),
            encoding="utf-8",
        )
        config.userData = None
        self.patches = [
            patch.object(config, "data_dir", return_value=self.data_root),
            patch.object(config, "users_data_path", return_value=self.users_path),
            patch.object(config, "_restrict_file_permissions"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for p in reversed(self.patches):
            p.stop()

    def test_mutator_change_is_persisted(self):
        def mutate(accounts):
            accounts[0]["username"] = "updated"
            return accounts

        config.update_user_data(mutate)

        stored = json.loads(self.users_path.read_text(encoding="utf-8"))
        self.assertEqual("updated", stored[0]["username"])

    def test_mutator_returning_none_does_not_write(self):
        before = self.users_path.read_text(encoding="utf-8")

        def mutate(accounts):
            return None

        config.update_user_data(mutate)
        self.assertEqual(before, self.users_path.read_text(encoding="utf-8"))


class MergeDefaultsTests(unittest.TestCase):
    """L5：_merge_defaults 深合并嵌套 dict，DEFAULT_CONFIG 新增子键可生效。"""

    def test_nested_keys_are_merged_recursively(self):
        defaults = {"sendStrategy": {"shuffleTargets": True, "messageVariants": ["a"]}}
        data = {"sendStrategy": {"shuffleTargets": False, "newOption": 42}}
        merged = config._merge_defaults(data, defaults)
        self.assertFalse(merged["sendStrategy"]["shuffleTargets"])
        self.assertEqual(42, merged["sendStrategy"]["newOption"])
        self.assertEqual(["a"], merged["sendStrategy"]["messageVariants"])

    def test_non_dict_values_replace_entirely(self):
        defaults = {"a": {"x": 1}, "b": 2}
        merged = config._merge_defaults({"a": "string", "b": 3}, defaults)
        self.assertEqual("string", merged["a"])
        self.assertEqual(3, merged["b"])


class JsonFileLockGrowthTests(unittest.TestCase):
    """L2：r+b 锁文件不再随每次获取追加字节。"""

    def test_lock_file_size_stays_constant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "data"
            with patch.object(config, "data_dir", return_value=data_root):
                for _ in range(10):
                    with config.json_file_lock("test"):
                        pass
                lock_path = data_root / "state" / "test.lock"
                self.assertTrue(lock_path.exists())
                self.assertLessEqual(lock_path.stat().st_size, 1)


class RestrictFilePermissionsTests(unittest.TestCase):
    """M4/L24：ACL 收紧执行一次且带缓存。"""

    def test_windows_icacls_called_once_per_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "secret.json"
            target.write_text("x", encoding="utf-8")
            with (
                patch.object(config.os, "name", "nt"),
                patch.object(config.os, "getlogin", return_value="tester"),
                patch.object(config.subprocess if hasattr(config, "subprocess") else __import__("subprocess"), "run") as run_mock,
            ):
                config._restrict_file_permissions(target)
                config._restrict_file_permissions(target)
                config._restrict_file_permissions(target)

            self.assertEqual(1, run_mock.call_count)
            args = run_mock.call_args.args[0]
            self.assertEqual("icacls", args[0])
            self.assertIn("/inheritance:r", args)

    def test_posix_chmod_600(self):
        if os.name == "nt":
            self.skipTest("POSIX only")
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "secret.json"
            target.write_text("x", encoding="utf-8")
            config._restrict_file_permissions(target)
            self.assertEqual(0o600, target.stat().st_mode & 0o777)


class SaveJsonFileRestrictsPermissionsTests(unittest.TestCase):
    """L24：_save_json_file 落盘后统一收紧 ACL。"""

    def test_save_json_file_restricts_acl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "usersData.json"
            with patch.object(config, "_restrict_file_permissions") as restrict_mock:
                config._save_json_file(target, [{"username": "demo"}])
            restrict_mock.assert_called_once_with(target)


if __name__ == "__main__":
    unittest.main()
