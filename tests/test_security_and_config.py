import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import main
from fastapi import HTTPException
from fastapi.testclient import TestClient

from node_config import NodeConfigManager
from partition_config import PartitionConfigManager
from slurm_manager import SlurmManager


class SessionSecurityTests(unittest.TestCase):
    def test_authenticated_mode_rejects_short_secret(self):
        with patch.object(main, "AUTH_ENABLED", True), patch.dict(
            os.environ, {"SECRET_KEY": "too-short"}
        ):
            with self.assertRaises(RuntimeError):
                main._get_session_secret()

    def test_http_session_cookie_is_the_default(self):
        session_middleware = next(
            middleware
            for middleware in main.app.user_middleware
            if middleware.cls.__name__ == "SessionMiddleware"
        )

        self.assertFalse(session_middleware.kwargs["https_only"])

    def test_login_session_is_available_after_http_navigation(self):
        with patch.object(
            main.auth_mgr,
            "authenticate_user",
            return_value={"username": "alice", "cn": "Alice"},
        ), patch.object(main.admin_mgr, "is_admin", return_value=True):
            client = TestClient(main.app, base_url="http://testserver")
            response = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "secret"},
            )
            dashboard = client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("secure", response.headers["set-cookie"].lower())
        self.assertEqual(dashboard.status_code, 200)


class JobOutputSecurityTests(unittest.TestCase):
    def setUp(self):
        self.manager = SlurmManager()

    def test_rejects_output_outside_allowed_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            allowed = base / "home"
            allowed.mkdir()
            secret = base / "secret"
            secret.write_text("sensitive\n", encoding="utf-8")

            result = self.manager.read_job_output(
                "123",
                "stdout",
                allowed_roots=[str(allowed)],
                job_detail={"StdOut": str(secret)},
            )

        self.assertFalse(result["success"])
        self.assertTrue(result["forbidden"])

    def test_rejects_symlink_that_escapes_allowed_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            allowed = base / "home"
            allowed.mkdir()
            secret = base / "secret"
            secret.write_text("sensitive\n", encoding="utf-8")
            link = allowed / "job.out"
            link.symlink_to(secret)

            result = self.manager.read_job_output(
                "123",
                "stdout",
                allowed_roots=[str(allowed)],
                job_detail={"StdOut": str(link)},
            )

        self.assertFalse(result["success"])
        self.assertTrue(result["forbidden"])

    def test_reads_output_inside_allowed_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            allowed = Path(temp_dir)
            output = allowed / "job.out"
            output.write_text("line 1\nline 2\n", encoding="utf-8")

            result = self.manager.read_job_output(
                "123",
                "stdout",
                allowed_roots=[str(allowed)],
                job_detail={"StdOut": str(output)},
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "line 1\nline 2\n")

    def test_route_rejects_another_users_job(self):
        with patch.object(
            main.slurm_mgr,
            "get_job_detail",
            return_value={"UserId": "alice(1001)", "StdOut": "/home/alice/job.out"},
        ), patch.object(main.slurm_mgr, "read_job_output") as read_output:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.get_job_output(
                        "123",
                        "stdout",
                        {"username": "bob", "is_admin": False},
                    )
                )

        self.assertEqual(context.exception.status_code, 403)
        read_output.assert_not_called()


class ConfigPreservationTests(unittest.TestCase):
    def test_partition_update_preserves_comments_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "partition.conf"
            original = (
                "# managed with local policy\n"
                "# example PartitionName=cpu State=INACTIVE\n"
                "PartitionName=cpu Nodes=n[01-04] Default=NO State=UP "
                "PriorityTier=20 OverSubscribe=NO\n"
                "PartitionName=gpu Nodes=g[01-02] Default=YES State=UP\n"
            )
            config_path.write_text(original, encoding="utf-8")
            manager = PartitionConfigManager(str(config_path))
            manager._reconfigure_slurm = Mock(return_value=True)

            success = manager.update_partition("cpu", state="DOWN", max_time="2:00:00")
            updated = config_path.read_text(encoding="utf-8")

        self.assertTrue(success)
        self.assertIn("# managed with local policy\n", updated)
        self.assertIn("# example PartitionName=cpu State=INACTIVE\n", updated)
        self.assertIn("PriorityTier=20 OverSubscribe=NO", updated)
        self.assertIn("State=DOWN", updated)
        self.assertIn("MaxTime=2:00:00", updated)
        self.assertIn("PartitionName=gpu Nodes=g[01-02] Default=YES State=UP\n", updated)

    def test_node_update_preserves_comments_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "node.conf"
            original = (
                "# keep this comment\n"
                "NodeName=n01 CPUs=32 RealMemory=64000 Feature=avx512 Weight=5\n"
                "NodeName=n02 CPUs=32 RealMemory=64000\n"
            )
            config_path.write_text(original, encoding="utf-8")
            manager = NodeConfigManager(str(config_path))
            manager._reconfigure_slurm = Mock(return_value=True)

            success = manager.update_node("n01", cpus=64)
            updated = config_path.read_text(encoding="utf-8")

        self.assertTrue(success)
        self.assertIn("# keep this comment\n", updated)
        self.assertIn("CPUs=64", updated)
        self.assertIn("Feature=avx512 Weight=5", updated)
        self.assertIn("NodeName=n02 CPUs=32 RealMemory=64000\n", updated)


class UserUpdateValidationTests(unittest.TestCase):
    def test_self_admin_revocation_is_rejected_before_ldap_write(self):
        payload = main.UserUpdate(cn="Changed", is_admin=False)
        with patch.object(main.ldap_mgr, "get_user", return_value={"username": "admin"}), patch.object(
            main.ldap_mgr, "update_user"
        ) as update_user:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.update_user(
                        "admin",
                        payload,
                        {"username": "admin", "is_admin": True},
                    )
                )

        self.assertEqual(context.exception.status_code, 400)
        update_user.assert_not_called()


class FrontendSecurityTests(unittest.TestCase):
    def test_job_rows_do_not_interpolate_slurm_values_into_inner_html(self):
        template = (
            Path(__file__).parents[1] / "templates" / "jobs.html"
        ).read_text(encoding="utf-8")

        self.assertNotIn("row.innerHTML", template)
        self.assertNotIn("${job.name}", template)
        self.assertNotIn("${job.user}", template)
        self.assertIn("appendTextCell(row, job.name)", template)
        self.assertIn("safe(job.Command)", template)

    def test_sidebar_includes_about_dialog_and_source_link(self):
        project_root = Path(__file__).parents[1]
        base_template = (project_root / "templates" / "base.html").read_text(
            encoding="utf-8"
        )
        sidebar_template = (
            project_root / "templates" / "components" / "sidebar.html"
        ).read_text(encoding="utf-8")

        self.assertIn('id="aboutModal"', base_template)
        self.assertIn("function openAboutModal()", base_template)
        self.assertIn(
            "https://github.com/acdiost/openhpc-web-python", base_template
        )
        self.assertIn('onclick="openAboutModal()"', sidebar_template)
        self.assertIn('class="sidebar-actions"', sidebar_template)


if __name__ == "__main__":
    unittest.main()
