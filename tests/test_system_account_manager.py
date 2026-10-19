import os
import pwd
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openhpc_webui.services.auth_manager import AuthManager
from openhpc_webui.services.system_account_manager import SystemAccountManager


class SystemAccountManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = SystemAccountManager()

    def test_get_user_maps_passwd_fields_without_shell_commands(self):
        record = SimpleNamespace(
            pw_name="alice",
            pw_uid=1200,
            pw_gid=1300,
            pw_dir="/home/alice",
            pw_shell="/bin/bash",
            pw_gecos="Alice Zhang,Lab,1001,,alice@example.com",
        )
        group = SimpleNamespace(gr_name="research", gr_gid=1300, gr_mem=[])
        with patch("pwd.getpwnam", return_value=record), patch(
            "grp.getgrall", return_value=[group]
        ):
            user = self.manager.get_user("alice")

        self.assertEqual(user["username"], "alice")
        self.assertEqual(user["uid"], "1200")
        self.assertEqual(user["groups"], ["research"])
        self.assertEqual(user["sn"], "Alice Zhang")
        self.assertEqual(user["phone"], "1001")
        self.assertEqual(user["email"], "alice@example.com")

    def test_create_user_passes_password_over_stdin_not_argv(self):
        completed = SimpleNamespace(returncode=0, stderr="")
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return completed

        with patch("subprocess.run", side_effect=run):
            created = self.manager.create_user(
                username="alice",
                uid=1200,
                gid=1300,
                home="/home/alice",
                shell="/bin/bash",
                password="top-secret",
                sn="Alice",
            )

        self.assertTrue(created)
        self.assertEqual(calls[0][0][0], "useradd")
        self.assertNotIn("top-secret", " ".join(calls[0][0]))
        self.assertEqual(calls[1][0], ["chpasswd"])
        self.assertEqual(calls[1][1]["input"], "alice:top-secret\n")

    def test_failed_password_initialization_rolls_back_user(self):
        results = [
            SimpleNamespace(returncode=0, stderr=""),
            SimpleNamespace(returncode=1, stderr="rejected"),
            SimpleNamespace(returncode=0, stderr=""),
        ]
        with patch("subprocess.run", side_effect=results) as run:
            created = self.manager.create_user(
                "alice", 1200, 1300, "/home/alice", password="bad"
            )

        self.assertFalse(created)
        self.assertEqual(run.call_args_list[-1].args[0], ["userdel", "--remove", "alice"])

    def test_password_with_chpasswd_line_break_is_rejected_before_useradd(self):
        with patch("subprocess.run") as run:
            created = self.manager.create_user(
                "alice", 1200, 1300, "/home/alice",
                password="secret\nroot:changed",
            )

        self.assertFalse(created)
        run.assert_not_called()

    def test_set_ssh_public_key_rejects_symlinked_ssh_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            target = Path(temp_dir) / "target"
            home.mkdir()
            target.mkdir()
            (home / ".ssh").symlink_to(target, target_is_directory=True)
            record = SimpleNamespace(
                pw_name="alice", pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(home)
            )
            with patch("pwd.getpwnam", return_value=record):
                saved = self.manager.set_ssh_public_key("alice", "ssh-ed25519 AAAA test")

        self.assertFalse(saved)

    def test_set_ssh_public_key_writes_atomically_with_secure_modes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            home.mkdir()
            record = SimpleNamespace(
                pw_name="alice", pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(home)
            )
            with patch("pwd.getpwnam", return_value=record):
                saved = self.manager.set_ssh_public_key(
                    "alice", "ssh-ed25519 AAAA test"
                )
            key_path = home / ".ssh" / "authorized_keys"

            self.assertTrue(saved)
            self.assertEqual(key_path.read_text(encoding="utf-8"), "ssh-ed25519 AAAA test\n")
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual((home / ".ssh").stat().st_mode & 0o777, 0o700)


class SystemAuthenticationTests(unittest.TestCase):
    def test_system_backend_returns_nss_identity_after_pam_success(self):
        record = SimpleNamespace(
            pw_name="alice",
            pw_uid=1200,
            pw_gid=1300,
            pw_dir="/home/alice",
            pw_shell="/bin/bash",
            pw_gecos="Alice Zhang,Lab,1001,",
        )
        with patch.dict(os.environ, {"IDENTITY_BACKEND": "system"}), patch(
            "pwd.getpwnam", return_value=record
        ), patch(
            "openhpc_webui.services.auth_manager._authenticate_with_pam",
            return_value=True,
        ):
            result = AuthManager().authenticate_user("alice", "secret")

        self.assertEqual(result["username"], "alice")
        self.assertEqual(result["cn"], "Alice Zhang")
        self.assertEqual(result["shell"], "/bin/bash")

    def test_system_backend_rejects_invalid_pam_credentials(self):
        record = SimpleNamespace(
            pw_name="alice", pw_uid=1200, pw_gid=1300, pw_dir="/home/alice",
            pw_shell="/bin/bash", pw_gecos="Alice",
        )
        with patch.dict(os.environ, {"IDENTITY_BACKEND": "system"}), patch(
            "pwd.getpwnam", return_value=record
        ), patch(
            "openhpc_webui.services.auth_manager._authenticate_with_pam",
            return_value=False,
        ):
            result = AuthManager().authenticate_user("alice", "wrong")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
