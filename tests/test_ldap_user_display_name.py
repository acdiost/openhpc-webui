import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import openhpc_webui.application as main
from openhpc_webui.schemas import UserCreate
from openhpc_webui.services.ldap_manager import LDAPManager


PROJECT_ROOT = Path(__file__).parents[1]


class LDAPUserDisplayNameTests(unittest.TestCase):
    def test_create_user_writes_chinese_display_name_to_ldap(self):
        manager = LDAPManager()
        connection = MagicMock()
        connection.result = {"result": 0}

        with patch.object(manager, "connect", return_value=connection):
            created = manager.create_user(
                username="zhangsan",
                uid=1001,
                gid=1001,
                home="/home/zhangsan",
                sn="  张三  ",
            )

        self.assertTrue(created)
        attributes = connection.add.call_args.kwargs["attributes"]
        self.assertEqual(attributes["cn"], "zhangsan")
        self.assertEqual(attributes["sn"], "张三")

    def test_create_payload_rejects_an_overlong_display_name(self):
        with self.assertRaises(ValidationError):
            UserCreate(
                username="longname",
                sn="名" * 129,
                uid=1004,
                gid=1004,
                home="/home/longname",
            )

    def test_create_user_uses_username_when_display_name_is_blank(self):
        manager = LDAPManager()
        connection = MagicMock()
        connection.result = {"result": 0}

        with patch.object(manager, "connect", return_value=connection):
            manager.create_user(
                username="lisi",
                uid=1002,
                gid=1002,
                home="/home/lisi",
                sn="   ",
            )

        attributes = connection.add.call_args.kwargs["attributes"]
        self.assertEqual(attributes["cn"], "lisi")
        self.assertEqual(attributes["sn"], "lisi")

    def test_create_api_forwards_display_name_to_ldap(self):
        payload = UserCreate(
            username="wangwu",
            sn="王五",
            uid=1003,
            gid=1003,
            home="/home/wangwu",
        )

        with patch.object(main.ldap_mgr, "create_user", return_value=True) as create, patch.object(
            main.slurm_mgr, "add_user_account", return_value=True
        ):
            asyncio.run(main.create_user(payload, {"username": "admin", "is_admin": True}))

        self.assertEqual(create.call_args.kwargs["sn"], "王五")

    def test_update_user_changes_surname_without_changing_common_name(self):
        manager = LDAPManager()
        connection = MagicMock()
        connection.result = {"result": 0}

        with patch.object(manager, "connect", return_value=connection):
            updated = manager.update_user(username="wangwu", sn="王五")

        self.assertTrue(updated)
        changes = connection.modify.call_args.args[1]
        self.assertIn("sn", changes)
        self.assertNotIn("cn", changes)

    def test_users_page_collects_and_displays_display_name(self):
        template = (PROJECT_ROOT / "templates/users.html").read_text(encoding="utf-8")

        self.assertIn('id="create_sn"', template)
        self.assertIn('name="sn"', template)
        self.assertIn('sn: fd.get("sn")', template)
        self.assertIn('escapeHtml(user.sn || "—")', template)


if __name__ == "__main__":
    unittest.main()
