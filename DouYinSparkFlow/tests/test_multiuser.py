import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webui import login_lock, users
from webui.auth import hash_password


class MultiUserTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.users_path = Path(self.temp_dir.name) / "webui_users.json"
        self.lock_path = Path(self.temp_dir.name) / "login-workspace.lock.json"
        self.accounts = [
            {"account_ref": "acc-1", "username": "头像是本人", "unique_id": "111", "targets": [], "enabled": True},
            {"account_ref": "acc-2", "username": "你成功捕捉一只野生妖孽", "unique_id": "222", "targets": [], "enabled": True},
            {"account_ref": "acc-3", "username": "管理员账号", "unique_id": "333", "targets": [], "enabled": True},
        ]
        self.user_file_patch = patch.object(users, "_users_file", return_value=self.users_path)
        self.user_file_patch.start()
        self.ensure_patch = patch.object(users, "get_userData", return_value=self.accounts)
        self.ensure_patch.start()
        self.save_accounts_patch = patch.object(users, "save_userData")
        self.save_accounts_patch.start()
        self.addCleanup(self.ensure_patch.stop)
        self.addCleanup(self.save_accounts_patch.stop)
        self.addCleanup(self.user_file_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def test_user_creation_auth_and_unique_assignment(self):
        a, changed = users.ensure_account_refs(self.accounts)
        self.assertFalse(changed)
        ref = a[0]["account_ref"]
        created = users.create_web_user("zxb", "zxb123456", account_refs=[ref])
        self.assertEqual([ref], created["account_refs"])
        identity = users.authenticate("zxb", "zxb123456")
        self.assertEqual("user", identity["role"])
        self.assertEqual([ref], identity["account_refs"])
        self.assertIsNone(users.authenticate("zxb", "wrong"))
        with self.assertRaises(users.UserStoreError):
            users.create_web_user("zcf", "zcf123456", account_refs=[ref])

    def test_visible_accounts_and_admin_reassignment(self):
        accounts, _ = users.ensure_account_refs(self.accounts)
        first_ref = accounts[0]["account_ref"]
        second_ref = accounts[1]["account_ref"]
        users.create_web_user("zxb", "secret", account_refs=[first_ref])
        principal = {"role": "user", "account_refs": [first_ref]}
        self.assertEqual([first_ref], [a["account_ref"] for a in users.get_visible_accounts(principal, accounts)])
        users.update_web_user("zxb", account_refs=[second_ref])
        self.assertEqual([second_ref], users.find_web_user("zxb")["account_refs"])
        self.assertTrue(users.delete_web_user("zxb"))
        self.assertEqual([], users.get_web_users())

    def test_workspace_activates_heartbeats_and_releases(self):
        # 单机语义：直接激活，心跳续期，释放清空
        with patch.object(login_lock, "LOCK_PATH", self.lock_path), patch.object(login_lock, "LOCK_TTL_SECONDS", 60):
            first = login_lock.request_workspace(username="zxb", session_id="s1", account_ref="a1", mode="add")
            self.assertEqual("active", first["state"])
            # 再次请求（本人）续期并保持 active
            again = login_lock.request_workspace(username="zxb", session_id="s1", account_ref="a1", mode="add")
            self.assertEqual("active", again["state"])
            self.assertTrue(login_lock.heartbeat(username="zxb", session_id="s1", ticket=first["request"]["ticket"], account_ref="a1"))
            status = login_lock.workspace_status(username="zxb", session_id="s1")
            self.assertEqual("active", status["state"])
            self.assertGreater(status["remaining_seconds"], 0)
            released = login_lock.begin_release(username="zxb", session_id="s1", ticket=first["request"]["ticket"], account_ref="a1")
            self.assertIsNotNone(released)
            self.assertIsNone(login_lock.get_lock())
            # 释放后可重新激活
            reopened = login_lock.request_workspace(username="zxb", session_id="s1", account_ref="a1", mode="add")
            self.assertEqual("active", reopened["state"])

    def test_workspace_expires_and_force_reset_clears(self):
        with patch.object(login_lock, "LOCK_PATH", self.lock_path), patch.object(login_lock, "LOCK_TTL_SECONDS", 1):
            login_lock.request_workspace(username="zxb", session_id="s1", account_ref="a1", mode="add")
            self.assertIsNotNone(login_lock.get_lock())
            import time as _time
            _time.sleep(1.1)
            # 过期后可被新请求接管
            reopened = login_lock.request_workspace(username="zxb", session_id="s1", account_ref="a1", mode="add")
            self.assertEqual("active", reopened["state"])
            # 强制重置清空
            reset = login_lock.begin_force_reset()
            self.assertIsNotNone(reset)
            self.assertIsNone(login_lock.get_lock())


if __name__ == "__main__":
    unittest.main()
