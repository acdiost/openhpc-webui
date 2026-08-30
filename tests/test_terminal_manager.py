import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from openhpc_webui.services.terminal_manager import (
    TerminalError,
    TerminalIdentity,
    TerminalManager,
)


class TerminalIdentityTests(unittest.TestCase):
    def test_resolves_system_identity(self):
        record = SimpleNamespace(
            pw_uid=1001,
            pw_gid=1002,
            pw_dir="/home/alice",
            pw_shell="/bin/bash",
        )
        with patch(
            "openhpc_webui.services.terminal_manager.pwd.getpwnam",
            return_value=record,
        ), patch(
            "openhpc_webui.services.terminal_manager.Path.is_dir",
            return_value=True,
        ), patch(
            "openhpc_webui.services.terminal_manager.os.access",
            return_value=True,
        ), patch(
            "openhpc_webui.services.terminal_manager.os.geteuid",
            return_value=0,
        ):
            identity = TerminalManager.resolve_identity("alice")

        self.assertEqual(identity.username, "alice")
        self.assertEqual(identity.uid, 1001)
        self.assertEqual(identity.gid, 1002)
        self.assertEqual(identity.home, "/home/alice")

    def test_rejects_account_with_disabled_shell(self):
        record = SimpleNamespace(
            pw_uid=1001,
            pw_gid=1001,
            pw_dir="/home/alice",
            pw_shell="/sbin/nologin",
        )
        with patch(
            "openhpc_webui.services.terminal_manager.pwd.getpwnam",
            return_value=record,
        ), patch(
            "openhpc_webui.services.terminal_manager.Path.is_dir",
            return_value=True,
        ):
            with self.assertRaisesRegex(TerminalError, "禁用"):
                TerminalManager.resolve_identity("alice")

    def test_rejects_service_that_cannot_switch_user(self):
        record = SimpleNamespace(
            pw_uid=1001,
            pw_gid=1001,
            pw_dir="/home/alice",
            pw_shell="/bin/bash",
        )
        with patch(
            "openhpc_webui.services.terminal_manager.pwd.getpwnam",
            return_value=record,
        ), patch(
            "openhpc_webui.services.terminal_manager.Path.is_dir",
            return_value=True,
        ), patch(
            "openhpc_webui.services.terminal_manager.os.access",
            return_value=True,
        ), patch(
            "openhpc_webui.services.terminal_manager.os.geteuid",
            return_value=999,
        ):
            with self.assertRaisesRegex(TerminalError, "无权切换"):
                TerminalManager.resolve_identity("alice")


class TerminalLimitTests(unittest.TestCase):
    def test_enforces_and_releases_per_user_session_limit(self):
        identity = TerminalIdentity(
            "alice", 1001, 1001, "/home/alice", "/bin/bash"
        )
        first = Mock(identity=identity)
        second = Mock(identity=identity)
        manager = TerminalManager(max_sessions_per_user=2)

        with patch.object(manager, "resolve_identity", return_value=identity), patch.object(
            manager, "_spawn", side_effect=[first, second]
        ):
            manager.open("alice")
            manager.open("alice")
            with self.assertRaisesRegex(TerminalError, "数量上限"):
                manager.open("alice")
            manager.close(first)

        self.assertEqual(manager._active["alice"], 1)
        first.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
