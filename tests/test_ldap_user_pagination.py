import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import openhpc_webui.application as main


PROJECT_ROOT = Path(__file__).parents[1]
ADMIN = {"username": "admin", "is_admin": True}


class LDAPUserPaginationApiTests(unittest.TestCase):
    def test_users_include_allowed_partitions_from_slurm_associations(self):
        directory_users = [
            {"username": "alice"},
            {"username": "bob"},
            {"username": "carol"},
        ]
        slurm_users = [
            {"username": "alice", "associations": [
                {"account": "research", "partition": "gpu"},
                {"account": "other", "partition": "cpu"},
                {"account": "research", "partition": "gpu"},
            ]},
            {"username": "bob", "associations": [
                {"account": "research", "partition": "gpu"},
                {"account": "other", "partition": ""},
            ]},
        ]
        with patch.object(main.ldap_mgr, "list_users", return_value=directory_users), \
            patch.object(main.admin_mgr, "get_admin_list", return_value=[]), \
            patch.object(main.slurm_mgr, "get_users_tres_limits", return_value={}), \
            patch.object(main.slurm_mgr, "list_slurm_users", return_value=slurm_users), \
            patch.object(main, "quota_mgr", None):
            result = asyncio.run(main.get_users(ADMIN, page=1, page_size=20))

        by_name = {user["username"]: user for user in result["users"]}
        self.assertEqual(by_name["alice"]["allowed_partitions"], ["cpu", "gpu"])
        self.assertEqual(by_name["bob"]["allowed_partitions"], ["*"])
        self.assertEqual(by_name["carol"]["allowed_partitions"], [])

    def test_slurm_user_lookup_failure_does_not_hide_ldap_users(self):
        with patch.object(main.ldap_mgr, "list_users", return_value=[{"username": "alice"}]), \
            patch.object(main.admin_mgr, "get_admin_list", return_value=[]), \
            patch.object(main.slurm_mgr, "get_users_tres_limits", return_value={}), \
            patch.object(main.slurm_mgr, "list_slurm_users", side_effect=RuntimeError("unavailable")), \
            patch.object(main, "quota_mgr", None):
            result = asyncio.run(main.get_users(ADMIN, page=1, page_size=20))

        self.assertIsNone(result["users"][0]["allowed_partitions"])

    def test_users_are_sorted_paginated_and_only_current_page_is_enriched(self):
        directory_users = [
            {"username": "zoe", "sn": "Zoe"},
            {"username": "alice", "sn": "Alice"},
            {"username": "bob", "sn": "Bob"},
        ]
        tres_values = {
            "alice": {"cpu_minutes": 60},
            "bob": {"cpu_minutes": 120},
            "zoe": {"cpu_minutes": 180},
        }

        with patch.object(
            main.ldap_mgr, "list_users", return_value=directory_users
        ), patch.object(
            main.admin_mgr, "get_admin_list", return_value=["bob"]
        ), patch.object(
            main.slurm_mgr, "get_users_tres_limits", return_value=tres_values
        ), patch.object(
            main.quota_mgr,
            "get_user_quota",
            side_effect=lambda username: {"used_gb": 1, "limit_gb": 10},
        ) as get_quota:
            result = asyncio.run(main.get_users(ADMIN, page=2, page_size=2))

        self.assertEqual([user["username"] for user in result["users"]], ["zoe"])
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["page_size"], 2)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["total_pages"], 2)
        get_quota.assert_called_once_with("zoe")

    def test_search_matches_ldap_fields_before_paginating(self):
        directory_users = [
            {"username": "alice", "sn": "王晓明", "uid": "1001", "home": "/home/alice"},
            {"username": "bob", "sn": "Bob", "uid": "1002", "home": "/research/team"},
            {"username": "carol", "sn": "Carol", "uid": "1003", "home": "/home/carol"},
        ]

        with patch.object(
            main.ldap_mgr, "list_users", return_value=directory_users
        ), patch.object(
            main.admin_mgr, "get_admin_list", return_value=[]
        ), patch.object(
            main.slurm_mgr, "get_users_tres_limits", return_value={}
        ), patch.object(main, "quota_mgr", None):
            result = asyncio.run(
                main.get_users(ADMIN, page=1, page_size=20, search="research")
            )

        self.assertEqual([user["username"] for user in result["users"]], ["bob"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["total_pages"], 1)
        self.assertEqual(result["search"], "research")

    def test_invalid_pagination_is_rejected(self):
        with self.assertRaises(HTTPException) as context:
            asyncio.run(main.get_users(ADMIN, page=0, page_size=20))

        self.assertEqual(context.exception.status_code, 422)


class LDAPUserPaginationFrontendTests(unittest.TestCase):
    def test_users_page_displays_allowed_partition_column(self):
        template = (PROJECT_ROOT / "templates/users.html").read_text(encoding="utf-8")
        self.assertIn("允许的 Partition</th>", template)
        self.assertIn('user.allowed_partitions', template)

    def test_users_page_has_server_side_search_and_pagination_controls(self):
        template = (PROJECT_ROOT / "templates/users.html").read_text(encoding="utf-8")

        self.assertIn('id="userPageSize"', template)
        self.assertIn('id="userPagination"', template)
        self.assertIn('id="userPreviousPage"', template)
        self.assertIn('id="userNextPage"', template)
        self.assertIn("function renderUserPagination", template)
        self.assertIn("window.setTimeout", template)
        self.assertNotIn('setupTableSearch("userSearch", "usersTable")', template)

    def test_fetch_users_serializes_pagination_query(self):
        script = (PROJECT_ROOT / "static/main.js").read_text(encoding="utf-8")

        self.assertIn("async function fetchUsers(params", script)
        self.assertIn("new URLSearchParams", script)
        self.assertIn("page_size", script)


if __name__ == "__main__":
    unittest.main()
