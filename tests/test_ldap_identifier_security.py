import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import openhpc_webui.application as main
from openhpc_webui.schemas import GroupCreate, GroupMemberUpdate, UserCreate
from openhpc_webui.services.ldap_manager import LDAPManager


ADMIN = {"username": "admin", "is_admin": True}


class LDAPIdentifierSchemaTests(unittest.TestCase):
    def test_user_and_group_payloads_reject_ldap_metacharacters(self):
        invalid_payloads = (
            lambda: UserCreate(
                username="alice)(uid=*)",
                uid=1001,
                gid=1001,
                home="/home/alice",
            ),
            lambda: GroupCreate(name="ops,ou=People", gid=1001),
            lambda: GroupMemberUpdate(username="alice*", group_name="ops"),
            lambda: GroupMemberUpdate(username="alice", group_name="ops+admins"),
        )

        for build_payload in invalid_payloads:
            with self.subTest(payload=build_payload), self.assertRaises(ValidationError):
                build_payload()

    def test_valid_posix_identifiers_remain_accepted(self):
        user = UserCreate(
            username="research.user-01",
            uid=1001,
            gid=1001,
            home="/home/research.user-01",
        )
        group = GroupCreate(name="gpu_users-01", gid=1001)

        self.assertEqual(user.username, "research.user-01")
        self.assertEqual(group.name, "gpu_users-01")


class LDAPManagerEscapingTests(unittest.TestCase):
    def setUp(self):
        self.manager = LDAPManager()
        self.connection = MagicMock()
        self.connection.entries = []
        self.connection.search.return_value = True
        self.connection.result = {"result": 0}

    def test_user_lookup_escapes_filter_metacharacters(self):
        with patch.object(self.manager, "connect", return_value=self.connection):
            self.manager.get_user("alice*)(uid=*)")

        search_filter = self.connection.search.call_args.args[1]
        self.assertEqual(search_filter, r"(uid=alice\2a\29\28uid=\2a\29)")

    def test_group_lookup_escapes_filter_metacharacters(self):
        with patch.object(self.manager, "connect", return_value=self.connection):
            self.manager.get_group("ops*)(cn=*)")

        search_filter = self.connection.search.call_args.args[1]
        self.assertEqual(search_filter, r"(cn=ops\2a\29\28cn=\2a\29)")

    def test_user_and_group_dns_escape_rdn_metacharacters(self):
        with patch.object(self.manager, "connect", return_value=self.connection):
            self.manager.delete_user("alice,ou=Admins")
            user_dn = self.connection.delete.call_args.args[0]
            self.manager.delete_group("ops+admins")
            group_dn = self.connection.delete.call_args.args[0]

        self.assertEqual(
            user_dn,
            r"uid=alice\,ou\=Admins,ou=People," + self.manager.base_dn,
        )
        self.assertEqual(group_dn, r"cn=ops\+admins,ou=Groups," + self.manager.base_dn)

    def test_group_membership_escapes_group_dn(self):
        with patch.object(self.manager, "connect", return_value=self.connection):
            self.manager.add_user_to_group("alice", "ops,ou=Admins")

        group_dn = self.connection.modify.call_args.args[0]
        self.assertEqual(
            group_dn,
            r"cn=ops\,ou\=Admins,ou=Groups," + self.manager.base_dn,
        )


class LDAPRouteValidationTests(unittest.TestCase):
    def test_user_read_path_rejects_invalid_identifier_before_lookup(self):
        with patch.object(main.ldap_mgr, "get_user") as get_user:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.get_user("alice)(uid=*)", ADMIN))

        self.assertEqual(context.exception.status_code, 400)
        get_user.assert_not_called()

    def test_user_path_rejects_invalid_identifier_before_lookup(self):
        with patch.object(main.ldap_mgr, "get_user") as get_user:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.delete_user("alice)(uid=*)", ADMIN))

        self.assertEqual(context.exception.status_code, 400)
        get_user.assert_not_called()

    def test_group_path_rejects_invalid_identifier_before_lookup(self):
        with patch.object(main.ldap_mgr, "get_group") as get_group:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.get_group("ops)(cn=*)", ADMIN))

        self.assertEqual(context.exception.status_code, 400)
        get_group.assert_not_called()


if __name__ == "__main__":
    unittest.main()
