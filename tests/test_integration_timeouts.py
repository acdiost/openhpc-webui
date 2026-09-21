import asyncio
import os
import subprocess
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import openhpc_webui.application as main
from fastapi import HTTPException
from openhpc_webui.services.auth_manager import AuthManager
from openhpc_webui.services.ldap_manager import LDAPManager, LDAPServiceUnavailable
from openhpc_webui.services.slurm_manager import (
    SlurmManager,
    SlurmServiceUnavailable,
)
from openhpc_webui.schemas import LoginRequest


ADMIN = {"username": "admin", "is_admin": True}


async def heartbeat_delay(awaitable) -> float:
    started = time.monotonic()
    heartbeat_at = None

    async def heartbeat():
        nonlocal heartbeat_at
        await asyncio.sleep(0.01)
        heartbeat_at = time.monotonic()

    await asyncio.gather(awaitable, heartbeat())
    return heartbeat_at - started


class IntegrationThreadpoolTests(unittest.TestCase):
    def test_login_ldap_call_does_not_block_the_event_loop(self):
        request = MagicMock()
        request.client.host = "192.0.2.10"
        request.session = {}

        def slow_authentication(_username, _password):
            time.sleep(0.12)
            return {"username": "alice", "shell": "/bin/bash"}

        with patch.object(
            main.auth_mgr,
            "authenticate_user",
            side_effect=slow_authentication,
        ), patch.object(main.admin_mgr, "is_admin", return_value=False):
            delay = asyncio.run(
                heartbeat_delay(
                    main.login(
                        request,
                        LoginRequest(username="alice", password="secret"),
                    )
                )
            )

        self.assertLess(delay, 0.06)

    def test_slurm_api_route_does_not_block_the_event_loop(self):
        route = next(
            item for item in main.router.routes if item.path == "/api/slurm/partitions"
        )

        def slow_partitions():
            time.sleep(0.12)
            return []

        with patch.object(main.slurm_mgr, "list_partitions", side_effect=slow_partitions):
            delay = asyncio.run(heartbeat_delay(route.endpoint(user=ADMIN)))

        self.assertLess(delay, 0.06)

    def test_slurm_timeout_is_exposed_as_service_unavailable(self):
        route = next(
            item for item in main.router.routes if item.path == "/api/slurm/partitions"
        )
        with patch.object(
            main.slurm_mgr,
            "list_partitions",
            side_effect=SlurmServiceUnavailable("timed out"),
        ):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(route.endpoint(user=ADMIN))

        self.assertEqual(context.exception.status_code, 503)

    def test_authentication_dependency_does_not_block_the_event_loop(self):
        request = MagicMock()
        request.session = {"user": {"username": "alice"}}

        def slow_shell(_username):
            time.sleep(0.12)
            return "/bin/bash"

        with patch.object(main, "AUTH_ENABLED", True), patch.object(
            main.ldap_mgr, "get_user_login_shell", side_effect=slow_shell
        ), patch.object(main.admin_mgr, "is_admin", return_value=False):
            delay = asyncio.run(heartbeat_delay(main.get_current_user(request)))

        self.assertLess(delay, 0.06)

    def test_authentication_timeout_is_exposed_as_service_unavailable(self):
        request = MagicMock()
        request.session = {"user": {"username": "alice"}}
        with patch.object(main, "AUTH_ENABLED", True), patch.object(
            main.ldap_mgr,
            "get_user_login_shell",
            side_effect=LDAPServiceUnavailable("timed out"),
        ):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.get_current_user(request))

        self.assertEqual(context.exception.status_code, 503)

    def test_audit_snapshot_does_not_block_the_event_loop(self):
        request = MagicMock()
        request.url.path = "/api/ldap/users/alice"

        def slow_user(_username):
            time.sleep(0.12)
            return {"username": "alice"}

        with patch.object(main, "AUTH_ENABLED", False), patch.object(
            main.ldap_mgr, "get_user", side_effect=slow_user
        ), patch.object(main.admin_mgr, "is_admin", return_value=False):
            delay = asyncio.run(
                heartbeat_delay(main._audit_snapshot(request, {}))
            )

        self.assertLess(delay, 0.06)


class LDAPTimeoutTests(unittest.TestCase):
    def test_ldap_manager_configures_connect_and_receive_timeouts(self):
        server = MagicMock()
        with patch.dict(
            os.environ,
            {"LDAP_CONNECT_TIMEOUT_SECONDS": "4", "LDAP_RECEIVE_TIMEOUT_SECONDS": "7"},
            clear=False,
        ), patch("openhpc_webui.services.ldap_manager.Server", return_value=server) as make_server, patch(
            "openhpc_webui.services.ldap_manager.Connection", return_value=MagicMock()
        ) as make_connection:
            manager = LDAPManager()
            manager.connect()

        self.assertEqual(make_server.call_args.kwargs["connect_timeout"], 4)
        self.assertEqual(make_connection.call_args.kwargs["receive_timeout"], 7)

    def test_ldap_connection_failure_is_not_reported_as_empty_data(self):
        with patch(
            "openhpc_webui.services.ldap_manager.Connection",
            side_effect=TimeoutError("ldap timed out"),
        ):
            manager = LDAPManager()
            with self.assertRaises(LDAPServiceUnavailable):
                manager.connect()

    def test_auth_manager_configures_ldap_timeouts(self):
        server = MagicMock()
        connection = MagicMock(bound=False)
        with patch.dict(
            os.environ,
            {"LDAP_CONNECT_TIMEOUT_SECONDS": "4", "LDAP_RECEIVE_TIMEOUT_SECONDS": "7"},
            clear=False,
        ), patch("openhpc_webui.services.auth_manager.Server", return_value=server) as make_server, patch(
            "openhpc_webui.services.auth_manager.Connection", return_value=connection
        ) as make_connection:
            manager = AuthManager()
            manager.authenticate_user("alice", "secret")

        self.assertEqual(make_server.call_args.kwargs["connect_timeout"], 4)
        self.assertEqual(make_connection.call_args.kwargs["receive_timeout"], 7)


class SlurmTimeoutTests(unittest.TestCase):
    def test_slurm_commands_have_a_hard_timeout(self):
        manager = SlurmManager()
        with patch.dict(
            os.environ, {"SLURM_COMMAND_TIMEOUT_SECONDS": "9"}, clear=False
        ), patch(
            "openhpc_webui.services.slurm_manager.subprocess.run",
            side_effect=subprocess.TimeoutExpired("sinfo", 9),
        ) as run:
            with self.assertRaises(SlurmServiceUnavailable):
                manager._get_runtime_status()

        self.assertEqual(run.call_args.kwargs["timeout"], 9)

    def test_timeout_setting_is_bounded(self):
        with patch.dict(
            os.environ, {"SLURM_COMMAND_TIMEOUT_SECONDS": "9999"}, clear=False
        ):
            self.assertEqual(main.slurm_mgr.command_timeout_seconds(), 300)


if __name__ == "__main__":
    unittest.main()
