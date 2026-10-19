import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openhpc_webui.services import system_settings


class SystemSettingsTests(unittest.TestCase):
    def test_setup_is_required_only_for_an_unconfigured_installation(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(system_settings.setup_required())
        with patch.dict(
            os.environ,
            {
                "SECRET_KEY": "x" * 32,
                "LDAP_BASE_DN": "dc=example,dc=com",
                "LDAP_DEFAULT_BIND_DN": "cn=admin,dc=example,dc=com",
                "SLURM_CLUSTER_NAME": "cluster",
            },
            clear=True,
        ):
            self.assertFalse(system_settings.setup_required())

    def test_complete_setup_generates_secret_and_locks_installer(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            system_settings, "ENV_FILE", Path(temp_dir) / ".env"
        ), patch.dict(os.environ, {}, clear=True), patch(
            "pwd.getpwnam", return_value=object()
        ):
            result = system_settings.complete_setup(
                {
                    "identity_backend": "system",
                    "ldap_bind_password": "must-not-be-saved",
                    "slurm_cluster_name": "cluster",
                    "slurm_default_account": "research",
                    "slurm_config_dir": "/etc/slurm",
                    "admin_username": "admin",
                    "session_https_only": False,
                }
            )
            content = (Path(temp_dir) / ".env").read_text(encoding="utf-8")

            self.assertFalse(system_settings.setup_required())
            self.assertEqual(os.environ["ADMIN_USERS"], "admin")
            self.assertEqual(os.environ["IDENTITY_BACKEND"], "system")
            self.assertGreaterEqual(len(os.environ["SECRET_KEY"]), 32)
            self.assertIn('SETUP_COMPLETED="True"', content)
            self.assertIn('IDENTITY_BACKEND="system"', content)
            self.assertNotIn("must-not-be-saved", content)

    def test_complete_setup_rejects_a_missing_system_admin(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            system_settings, "ENV_FILE", Path(temp_dir) / ".env"
        ), patch.dict(os.environ, {}, clear=True), patch(
            "pwd.getpwnam", side_effect=KeyError("missing")
        ):
            with self.assertRaisesRegex(
                system_settings.SystemSettingsError, "本机系统账户"
            ):
                system_settings.complete_setup(
                    {
                        "slurm_cluster_name": "cluster",
                        "identity_backend": "system",
                        "slurm_default_account": "research",
                        "slurm_config_dir": "/etc/slurm",
                        "admin_username": "missing",
                        "session_https_only": False,
                    }
                )

    def test_complete_setup_can_select_ldap_without_a_local_admin_account(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            system_settings, "ENV_FILE", Path(temp_dir) / ".env"
        ), patch.dict(os.environ, {}, clear=True), patch(
            "pwd.getpwnam", side_effect=AssertionError("must not query local account")
        ):
            result = system_settings.complete_setup(
                {
                    "identity_backend": "ldap",
                    "ldap_uri": "ldaps://ldap.internal",
                    "ldap_base_dn": "dc=example,dc=com",
                    "ldap_bind_dn": "cn=admin,dc=example,dc=com",
                    "ldap_bind_password": "bind-password",
                    "ldap_port": 636,
                    "ldap_use_ssl": True,
                    "slurm_cluster_name": "cluster",
                    "slurm_default_account": "research",
                    "slurm_config_dir": "/etc/slurm",
                    "admin_username": "ldap-admin",
                    "session_https_only": True,
                }
            )
            content = (Path(temp_dir) / ".env").read_text(encoding="utf-8")

        self.assertEqual(result["config"]["identity_backend"], "ldap")
        self.assertIn('IDENTITY_BACKEND="ldap"', content)
        self.assertIn('LDAP_URI="ldaps://ldap.internal"', content)
        self.assertNotIn("bind-password", str(result))

    def test_complete_setup_requires_ldap_credentials_only_for_ldap(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            system_settings, "ENV_FILE", Path(temp_dir) / ".env"
        ), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                system_settings.SystemSettingsError, "LDAP"
            ):
                system_settings.complete_setup(
                    {
                        "identity_backend": "ldap",
                        "slurm_cluster_name": "cluster",
                        "slurm_default_account": "research",
                        "slurm_config_dir": "/etc/slurm",
                        "admin_username": "admin",
                    }
                )

    def test_public_settings_never_expose_secrets(self):
        with patch.dict(
            os.environ,
            {
                "LDAP_DEFAULT_AUTHTOK": "ldap-password",
                "SECRET_KEY": "s" * 32,
                "LDAP_URI": "ldap://ldap.internal",
            },
            clear=True,
        ):
            result = system_settings.public_config()

        self.assertNotIn("ldap-password", str(result))
        self.assertNotIn("s" * 32, str(result))
        self.assertTrue(result["ldap"]["bind_password_configured"])
        self.assertTrue(result["security"]["secret_key_configured"])
        self.assertEqual(result["ldap"]["uri"], "ldap://ldap.internal")

    def test_save_persists_values_atomically_and_preserves_omitted_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            system_settings, "ENV_FILE", Path(temp_dir) / ".env"
        ), patch.dict(
            os.environ,
            {
                "LDAP_DEFAULT_AUTHTOK": "keep-me",
                "SECRET_KEY": "k" * 32,
            },
            clear=True,
        ):
            result = system_settings.save_config(
                {
                    "ldap_uri": "ldaps://ldap.internal",
                    "ldap_base_dn": "dc=example,dc=com",
                    "ldap_bind_dn": "cn=admin,dc=example,dc=com",
                    "ldap_port": 636,
                    "ldap_use_ssl": True,
                    "slurm_cluster_name": "hpc-cluster_1",
                    "slurm_default_account": "research",
                    "slurm_config_dir": "/etc/slurm",
                    "login_max_failed_attempts": 8,
                    "login_lockout_minutes": 20,
                    "file_upload_max_mb": 2048,
                    "file_edit_max_kb": 4096,
                    "terminal_enabled": True,
                    "terminal_idle_minutes": 45,
                    "terminal_max_sessions": 3,
                    "session_https_only": True,
                    "authorized": True,
                    "log_level": "WARNING",
                    "job_output_allowed_roots": "/data:/scratch",
                    "nfs_quota_fs": "/home",
                }
            )
            content = (Path(temp_dir) / ".env").read_text(encoding="utf-8")

            self.assertIn('LDAP_URI="ldaps://ldap.internal"', content)
            self.assertIn('LDAP_DEFAULT_AUTHTOK="keep-me"', content)
            self.assertIn('SECRET_KEY="kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk"', content)
            self.assertEqual((Path(temp_dir) / ".env").stat().st_mode & 0o777, 0o600)
            self.assertEqual(os.environ["SLURM_CLUSTER_NAME"], "hpc-cluster_1")
            self.assertTrue(result["restart_required"])

    def test_save_can_replace_secrets_without_returning_them(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            system_settings, "ENV_FILE", Path(temp_dir) / ".env"
        ), patch.dict(os.environ, {}, clear=True):
            result = system_settings.save_config(
                {
                    "ldap_bind_password": "new-password",
                    "secret_key": "z" * 32,
                }
            )

        self.assertNotIn("new-password", str(result))
        self.assertNotIn("z" * 32, str(result))
        self.assertTrue(result["config"]["ldap"]["bind_password_configured"])

    def test_rejects_invalid_values_before_writing(self):
        invalid_updates = (
            {"ldap_uri": "https://not-ldap.example"},
            {"ldap_port": 70000},
            {"slurm_cluster_name": "bad cluster"},
            {"secret_key": "too-short"},
            {"log_level": "VERBOSE"},
            {"slurm_config_dir": "relative/path"},
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            system_settings, "ENV_FILE", Path(temp_dir) / ".env"
        ):
            for update in invalid_updates:
                with self.subTest(update=update), self.assertRaises(
                    system_settings.SystemSettingsError
                ):
                    system_settings.save_config(update)
            self.assertFalse((Path(temp_dir) / ".env").exists())

    def test_packaged_process_persists_to_working_directory_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("LDAP_URI=ldap://old\n", encoding="utf-8")
            environment = {
                key: value for key, value in os.environ.items()
                if key not in {"LDAP_URI", "OPENHPC_WEBUI_ENV_FILE"}
            }
            project = str(Path(__file__).resolve().parents[1])
            environment["PYTHONPATH"] = os.pathsep.join(
                filter(None, (project, environment.get("PYTHONPATH", "")))
            )
            saved = subprocess.run(
                [sys.executable, "-c",
                 "from openhpc_webui.config import ENV_FILE, PACKAGE_ROOT; "
                 "from openhpc_webui.services.system_settings import save_config; "
                 "assert ENV_FILE.parent != PACKAGE_ROOT.parent; "
                 "save_config({'ldap_uri': 'ldaps://new'})"],
                cwd=temp_dir, env=environment, capture_output=True, text=True,
            )
            self.assertEqual(saved.returncode, 0, saved.stderr)
            restarted = subprocess.run(
                [sys.executable, "-c",
                 "import os; import openhpc_webui.config; "
                 "print(os.getenv('LDAP_URI'))"],
                cwd=temp_dir, env=environment, capture_output=True, text=True,
            )
            self.assertEqual(restarted.returncode, 0, restarted.stderr)
            self.assertEqual(restarted.stdout.strip(), "ldaps://new")
            self.assertIn('LDAP_URI="ldaps://new"', env_path.read_text())


if __name__ == "__main__":
    unittest.main()
