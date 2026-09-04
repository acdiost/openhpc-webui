import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldap3 import MODIFY_REPLACE
from pydantic import ValidationError

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import openhpc_webui.application as main
from openhpc_webui.schemas import UserCreate, UserUpdate
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


class LDAPUserContactTests(unittest.TestCase):
    def test_list_users_reads_contact_fields_from_ldap(self):
        class Attribute:
            def __init__(self, value):
                self.value = value

            def __bool__(self):
                return self.value is not None

        entry = MagicMock()
        entry.entry_dn = "uid=contact-user,ou=People,dc=example,dc=com"
        entry.uid = Attribute("contact-user")
        entry.uidNumber = Attribute("1101")
        entry.gidNumber = Attribute(None)
        entry.homeDirectory = Attribute("/home/contact-user")
        entry.loginShell = Attribute("/bin/bash")
        entry.cn = Attribute("contact-user")
        entry.sn = Attribute("联系人")
        entry.mail = Attribute("contact.user@example.com")
        entry.telephoneNumber = Attribute("13800000000")

        manager = LDAPManager()
        connection = MagicMock()
        connection.entries = [entry]
        connection.search.return_value = True

        with patch.object(manager, "connect", return_value=connection):
            users = manager.list_users()

        self.assertEqual(users[0]["phone"], "13800000000")
        self.assertEqual(users[0]["email"], "contact.user@example.com")
        requested_attributes = connection.search.call_args.kwargs["attributes"]
        self.assertIn("telephoneNumber", requested_attributes)
        self.assertIn("mail", requested_attributes)

    def test_create_payload_accepts_optional_contact_fields(self):
        payload = UserCreate(
            username="contact-user",
            uid=1101,
            gid=1101,
            home="/home/contact-user",
            phone="+86 138-0000-0000",
            email="contact.user@example.com",
        )

        self.assertEqual(payload.phone, "+86 138-0000-0000")
        self.assertEqual(payload.email, "contact.user@example.com")

    def test_payload_rejects_invalid_email_and_contact_characters(self):
        with self.assertRaises(ValidationError):
            UserCreate(
                username="bad-email",
                uid=1102,
                gid=1102,
                home="/home/bad-email",
                email="not-an-email",
            )

        with self.assertRaises(ValidationError):
            UserUpdate(phone="13800000000\nmalicious")

        with self.assertRaises(ValidationError):
            UserUpdate(phone="13800000000\n")

    def test_create_user_writes_optional_contact_fields_to_ldap(self):
        manager = LDAPManager()
        connection = MagicMock()
        connection.result = {"result": 0}

        with patch.object(manager, "connect", return_value=connection):
            created = manager.create_user(
                username="contact-user",
                uid=1101,
                gid=1101,
                home="/home/contact-user",
                phone=" +86 138-0000-0000 ",
                email=" contact.user@example.com ",
            )

        self.assertTrue(created)
        attributes = connection.add.call_args.kwargs["attributes"]
        self.assertEqual(attributes["telephoneNumber"], "+86 138-0000-0000")
        self.assertEqual(attributes["mail"], "contact.user@example.com")

    def test_create_user_omits_blank_contact_fields(self):
        manager = LDAPManager()
        connection = MagicMock()
        connection.result = {"result": 0}

        with patch.object(manager, "connect", return_value=connection):
            manager.create_user(
                username="no-contact",
                uid=1103,
                gid=1103,
                home="/home/no-contact",
                phone=" ",
                email=None,
            )

        attributes = connection.add.call_args.kwargs["attributes"]
        self.assertNotIn("telephoneNumber", attributes)
        self.assertNotIn("mail", attributes)

    def test_update_user_replaces_or_clears_contact_fields(self):
        manager = LDAPManager()
        connection = MagicMock()
        connection.result = {"result": 0}

        with patch.object(manager, "connect", return_value=connection):
            updated = manager.update_user(
                username="contact-user",
                phone="13800000000",
                email="",
            )

        self.assertTrue(updated)
        changes = connection.modify.call_args.args[1]
        self.assertEqual(changes["telephoneNumber"], [(MODIFY_REPLACE, ["13800000000"])])
        self.assertEqual(changes["mail"], [(MODIFY_REPLACE, [])])

    def test_create_and_update_apis_forward_contact_fields(self):
        create_payload = UserCreate(
            username="api-contact",
            uid=1104,
            gid=1104,
            home="/home/api-contact",
            phone="13800000000",
            email="api@example.com",
        )

        with patch.object(main.ldap_mgr, "create_user", return_value=True) as create, patch.object(
            main.slurm_mgr, "add_user_account", return_value=True
        ):
            asyncio.run(main.create_user(create_payload, {"username": "admin", "is_admin": True}))

        self.assertEqual(create.call_args.kwargs["phone"], "13800000000")
        self.assertEqual(create.call_args.kwargs["email"], "api@example.com")

        update_payload = UserUpdate(phone="", email="new@example.com")
        with patch.object(main.ldap_mgr, "get_user", return_value={"username": "api-contact"}), patch.object(
            main.ldap_mgr, "update_user", return_value=True
        ) as update:
            asyncio.run(
                main.update_user(
                    "api-contact",
                    update_payload,
                    {"username": "admin", "is_admin": True},
                )
            )

        self.assertEqual(update.call_args.kwargs["phone"], "")
        self.assertEqual(update.call_args.kwargs["email"], "new@example.com")

    def test_users_page_shows_contact_only_in_more_details(self):
        template = (PROJECT_ROOT / "templates/users.html").read_text(encoding="utf-8")
        table_head = template.split('<table class="table data-table has-actions"', 1)[1].split("</table>", 1)[0]

        self.assertNotIn("<th>联系方式</th>", table_head)
        self.assertNotIn("<th>邮箱</th>", table_head)
        self.assertIn('appendUserAction(menu, "详情"', template)
        self.assertIn('id="userDetailModal"', template)
        self.assertIn('id="user_detail_phone"', template)
        self.assertIn('id="user_detail_email"', template)
        self.assertIn('detailPhone.textContent = user.phone || "—"', template)
        self.assertIn('detailEmail.textContent = user.email || "—"', template)
        self.assertIn('name="phone"', template)
        self.assertIn('name="email"', template)


if __name__ == "__main__":
    unittest.main()
