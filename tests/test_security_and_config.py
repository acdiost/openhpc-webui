import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import openhpc_webui.application as main
from openhpc_webui.services import auth_manager
from openhpc_webui.services.auth_manager import AuthManager
from fastapi import HTTPException
from fastapi.testclient import TestClient

from openhpc_webui.services.node_config import NodeConfigManager
from openhpc_webui.services.partition_config import PartitionConfigManager
from openhpc_webui.services.slurm_manager import SlurmManager


class SessionSecurityTests(unittest.TestCase):
    def test_disabled_shell_variants_are_recognized(self):
        for shell in ("/sbin/nologin", "/usr/sbin/nologin", "/bin/false"):
            with self.subTest(shell=shell):
                self.assertTrue(main._is_disabled_login_shell(shell))

        self.assertFalse(main._is_disabled_login_shell("/bin/bash"))

    def test_ldap_authentication_returns_login_shell(self):
        entry = SimpleNamespace(
            uid="alice",
            cn="Alice",
            mail="alice@example.test",
            uidNumber="1001",
            gidNumber="1001",
            loginShell=SimpleNamespace(value="/sbin/nologin"),
        )
        connection = Mock(bound=True, entries=[entry])
        with patch.object(auth_manager, "Server"), patch.object(
            auth_manager, "Connection", return_value=connection
        ):
            result = AuthManager().authenticate_user("alice", "secret")

        self.assertEqual(result["shell"], "/sbin/nologin")
        connection.unbind.assert_called_once()

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
        ), patch.object(
            main.ldap_mgr, "get_user_login_shell", return_value="/bin/bash"
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

    def test_login_rejects_user_with_nologin_shell(self):
        request = Mock()
        request.session = {}
        with patch.object(
            main.auth_mgr,
            "authenticate_user",
            return_value={
                "username": "alice",
                "cn": "Alice",
                "shell": "/sbin/nologin",
            },
        ):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.login(
                        request,
                        main.LoginRequest(username="alice", password="secret"),
                    )
                )

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(request.session, {})

    def test_existing_session_is_cleared_after_user_is_disabled(self):
        request = Mock()
        request.session = {"user": {"username": "alice", "cn": "Alice"}}
        with patch.object(main, "AUTH_ENABLED", True), patch.object(
            main.ldap_mgr,
            "get_user_login_shell",
            return_value="/usr/sbin/nologin",
        ):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.get_current_user(request))

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(request.session, {})


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
    def test_config_managers_use_slurm_config_dir(self):
        with patch.dict(os.environ, {"SLURM_CONFIG_DIR": "/srv/slurm"}):
            self.assertEqual(NodeConfigManager().config_file, "/srv/slurm/node.conf")
            self.assertEqual(
                PartitionConfigManager().config_file, "/srv/slurm/partition.conf"
            )

    def test_config_managers_default_to_etc_slurm(self):
        with patch.dict(os.environ, {}, clear=False):
            previous = os.environ.pop("SLURM_CONFIG_DIR", None)
            try:
                self.assertEqual(NodeConfigManager().config_file, "/etc/slurm/node.conf")
                self.assertEqual(
                    PartitionConfigManager().config_file, "/etc/slurm/partition.conf"
                )
            finally:
                if previous is not None:
                    os.environ["SLURM_CONFIG_DIR"] = previous

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


class UserDisableTests(unittest.TestCase):
    def test_disable_user_sets_nologin_shell(self):
        with patch.object(
            main.ldap_mgr,
            "get_user",
            return_value={"username": "alice", "shell": "/bin/bash"},
        ), patch.object(main.ldap_mgr, "update_user", return_value=True) as update_user:
            result = asyncio.run(
                main.disable_user(
                    "alice",
                    {"username": "admin", "is_admin": True},
                )
            )

        update_user.assert_called_once_with(
            username="alice", shell="/sbin/nologin"
        )
        self.assertEqual(result["message"], "用户 alice 已禁用")

    def test_disable_missing_user_returns_not_found_without_ldap_write(self):
        with patch.object(main.ldap_mgr, "get_user", return_value=None), patch.object(
            main.ldap_mgr, "update_user"
        ) as update_user:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.disable_user(
                        "missing",
                        {"username": "admin", "is_admin": True},
                    )
                )

        self.assertEqual(context.exception.status_code, 404)
        update_user.assert_not_called()

    def test_disable_user_requires_admin(self):
        with patch.object(main.ldap_mgr, "get_user") as get_user:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.disable_user(
                        "alice",
                        {"username": "bob", "is_admin": False},
                    )
                )

        self.assertEqual(context.exception.status_code, 403)
        get_user.assert_not_called()

    def test_disable_user_rejects_invalid_username_before_ldap_query(self):
        with patch.object(main.ldap_mgr, "get_user") as get_user:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.disable_user(
                        "alice)(uid=*)",
                        {"username": "admin", "is_admin": True},
                    )
                )

        self.assertEqual(context.exception.status_code, 400)
        get_user.assert_not_called()

    def test_disable_user_reports_ldap_failure(self):
        with patch.object(
            main.ldap_mgr, "get_user", return_value={"username": "alice"}
        ), patch.object(main.ldap_mgr, "update_user", return_value=False):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.disable_user(
                        "alice",
                        {"username": "admin", "is_admin": True},
                    )
                )

        self.assertEqual(context.exception.status_code, 500)


class UserDisableFrontendTests(unittest.TestCase):
    def test_users_page_groups_secondary_and_dangerous_actions_in_more_menu(self):
        template = (
            Path(__file__).parents[1] / "templates" / "users.html"
        ).read_text(encoding="utf-8")
        script = (
            Path(__file__).parents[1] / "static" / "main.js"
        ).read_text(encoding="utf-8")

        self.assertIn('aria-label="更多操作"', template)
        self.assertIn("function openUserActionsMenu(", template)
        self.assertIn('"user-action-menu-item danger"', template)
        self.assertNotIn('onclick="deleteUser(\'${user.username}\')"', template)
        self.assertIn("isDisabledShell(user.shell)", template)
        self.assertIn("async function disableUserAPI(username)", script)
        self.assertIn("/disable`", script)


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
            "https://github.com/acdiost/openhpc-webui", base_template
        )
        self.assertIn('onclick="openAboutModal()"', sidebar_template)
        self.assertIn('class="sidebar-actions"', sidebar_template)


if __name__ == "__main__":
    unittest.main()
