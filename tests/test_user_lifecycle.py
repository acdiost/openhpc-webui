import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import openhpc_webui.application as main
from openhpc_webui.schemas import UserCreate


ADMIN = {"username": "admin", "is_admin": True}


def user_payload(**changes):
    values = {
        "username": "alice",
        "uid": 1001,
        "gid": 1001,
        "home": "/home/alice",
    }
    values.update(changes)
    return UserCreate(**values)


class UserCreationLifecycleTests(unittest.TestCase):
    def test_successful_creation_commits_every_requested_system(self):
        payload = user_payload(is_admin=True, storage_quota_gb=10)
        with patch.object(main.quota_mgr, "is_enabled", return_value=True), patch.object(
            main.ldap_mgr, "create_user", return_value=True
        ), patch.object(
            main.slurm_mgr, "add_user_account", return_value=True
        ), patch.object(main.admin_mgr, "add_admin", return_value=True), patch.object(
            main.quota_mgr, "set_user_quota", return_value=True
        ) as set_quota, patch.object(
            main.ldap_mgr, "delete_user"
        ) as rollback_ldap:
            result = asyncio.run(main.create_user(payload, ADMIN))

        self.assertEqual(result["is_admin"], True)
        set_quota.assert_called_once_with("alice", 10)
        rollback_ldap.assert_not_called()

    def test_quota_is_preflighted_before_any_user_is_created(self):
        payload = user_payload(storage_quota_gb=10)
        with patch.object(main.quota_mgr, "is_enabled", return_value=False), patch.object(
            main.ldap_mgr, "create_user"
        ) as create_ldap, patch.object(
            main.slurm_mgr, "add_user_account"
        ) as create_slurm:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.create_user(payload, ADMIN))

        self.assertEqual(context.exception.status_code, 503)
        create_ldap.assert_not_called()
        create_slurm.assert_not_called()

    def test_slurm_failure_rolls_back_the_ldap_user(self):
        payload = user_payload()
        with patch.object(main.ldap_mgr, "create_user", return_value=True), patch.object(
            main.slurm_mgr, "add_user_account", return_value=False
        ), patch.object(main.ldap_mgr, "delete_user", return_value=True) as rollback:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.create_user(payload, ADMIN))

        self.assertEqual(context.exception.status_code, 500)
        rollback.assert_called_once_with("alice")
        self.assertIn("已回滚", context.exception.detail)

    def test_admin_failure_rolls_back_slurm_and_ldap(self):
        payload = user_payload(is_admin=True)
        events = []

        with patch.object(main.ldap_mgr, "create_user", return_value=True), patch.object(
            main.slurm_mgr, "add_user_account", return_value=True
        ), patch.object(main.admin_mgr, "add_admin", return_value=False), patch.object(
            main.slurm_mgr,
            "remove_user_account",
            side_effect=lambda username: events.append(("slurm", username)) or True,
        ), patch.object(
            main.ldap_mgr,
            "delete_user",
            side_effect=lambda username: events.append(("ldap", username)) or True,
        ):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.create_user(payload, ADMIN))

        self.assertEqual(events, [("slurm", "alice"), ("ldap", "alice")])
        self.assertIn("已回滚", context.exception.detail)

    def test_quota_failure_rolls_back_admin_slurm_and_ldap(self):
        payload = user_payload(is_admin=True, storage_quota_gb=10)
        events = []

        with patch.object(main.quota_mgr, "is_enabled", return_value=True), patch.object(
            main.ldap_mgr, "create_user", return_value=True
        ), patch.object(
            main.slurm_mgr, "add_user_account", return_value=True
        ), patch.object(main.admin_mgr, "add_admin", return_value=True), patch.object(
            main.quota_mgr, "set_user_quota", return_value=False
        ), patch.object(
            main.admin_mgr,
            "remove_admin",
            side_effect=lambda username: events.append(("admin", username)) or True,
        ), patch.object(
            main.slurm_mgr,
            "remove_user_account",
            side_effect=lambda username: events.append(("slurm", username)) or True,
        ), patch.object(
            main.ldap_mgr,
            "delete_user",
            side_effect=lambda username: events.append(("ldap", username)) or True,
        ):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.create_user(payload, ADMIN))

        self.assertEqual(
            events,
            [("admin", "alice"), ("slurm", "alice"), ("ldap", "alice")],
        )
        self.assertIn("已回滚", context.exception.detail)

    def test_failed_creation_reports_incomplete_rollback(self):
        payload = user_payload()
        with patch.object(main.ldap_mgr, "create_user", return_value=True), patch.object(
            main.slurm_mgr, "add_user_account", return_value=False
        ), patch.object(main.ldap_mgr, "delete_user", return_value=False):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.create_user(payload, ADMIN))

        self.assertIn("人工处理", context.exception.detail)


class UserDeletionLifecycleTests(unittest.TestCase):
    def setUp(self):
        lookup = patch.object(
            main.ldap_mgr, "get_user", return_value={"username": "alice"}
        )
        lookup.start()
        self.addCleanup(lookup.stop)

    def test_missing_ldap_user_does_not_change_other_systems(self):
        with patch.object(main.ldap_mgr, "get_user", return_value=None), patch.object(
            main.slurm_mgr, "remove_user_account"
        ) as remove_slurm, patch.object(main.admin_mgr, "remove_admin") as remove_admin:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.delete_user("missing", ADMIN))

        self.assertEqual(context.exception.status_code, 404)
        remove_slurm.assert_not_called()
        remove_admin.assert_not_called()

    def test_successful_deletion_commits_in_recoverable_order(self):
        events = []
        with patch.object(main.admin_mgr, "is_admin", return_value=True), patch.object(
            main.slurm_mgr,
            "remove_user_account",
            side_effect=lambda username: events.append(("slurm", username)) or True,
        ), patch.object(
            main.admin_mgr,
            "remove_admin",
            side_effect=lambda username: events.append(("admin", username)) or True,
        ), patch.object(
            main.ldap_mgr,
            "delete_user",
            side_effect=lambda username: events.append(("ldap", username)) or True,
        ):
            result = asyncio.run(main.delete_user("alice", ADMIN))

        self.assertEqual(
            events,
            [("slurm", "alice"), ("admin", "alice"), ("ldap", "alice")],
        )
        self.assertEqual(result["message"], "用户 alice 已删除")

    def test_slurm_failure_keeps_ldap_and_admin_unchanged(self):
        with patch.object(main.admin_mgr, "is_admin", return_value=True), patch.object(
            main.slurm_mgr, "remove_user_account", return_value=False
        ), patch.object(main.admin_mgr, "remove_admin") as remove_admin, patch.object(
            main.ldap_mgr, "delete_user"
        ) as delete_ldap:
            with self.assertRaises(HTTPException):
                asyncio.run(main.delete_user("alice", ADMIN))

        remove_admin.assert_not_called()
        delete_ldap.assert_not_called()

    def test_admin_failure_restores_slurm_before_stopping(self):
        with patch.object(main.admin_mgr, "is_admin", return_value=True), patch.object(
            main.slurm_mgr, "remove_user_account", return_value=True
        ), patch.object(main.admin_mgr, "remove_admin", return_value=False), patch.object(
            main.slurm_mgr, "add_user_account", return_value=True
        ) as restore_slurm, patch.object(main.ldap_mgr, "delete_user") as delete_ldap:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.delete_user("alice", ADMIN))

        restore_slurm.assert_called_once_with("alice")
        delete_ldap.assert_not_called()
        self.assertIn("已回滚", context.exception.detail)

    def test_ldap_failure_restores_admin_and_slurm(self):
        events = []
        with patch.object(main.admin_mgr, "is_admin", return_value=True), patch.object(
            main.slurm_mgr, "remove_user_account", return_value=True
        ), patch.object(main.admin_mgr, "remove_admin", return_value=True), patch.object(
            main.ldap_mgr, "delete_user", return_value=False
        ), patch.object(
            main.admin_mgr,
            "add_admin",
            side_effect=lambda username: events.append(("admin", username)) or True,
        ), patch.object(
            main.slurm_mgr,
            "add_user_account",
            side_effect=lambda username: events.append(("slurm", username)) or True,
        ):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.delete_user("alice", ADMIN))

        self.assertEqual(events, [("admin", "alice"), ("slurm", "alice")])
        self.assertIn("已回滚", context.exception.detail)

    def test_ldap_failure_reports_incomplete_restoration(self):
        with patch.object(main.admin_mgr, "is_admin", return_value=False), patch.object(
            main.slurm_mgr, "remove_user_account", return_value=True
        ), patch.object(main.ldap_mgr, "delete_user", return_value=False), patch.object(
            main.slurm_mgr, "add_user_account", return_value=False
        ):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.delete_user("alice", ADMIN))

        self.assertIn("人工处理", context.exception.detail)


if __name__ == "__main__":
    unittest.main()
