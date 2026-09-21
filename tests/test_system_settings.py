import os
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
            system_settings, "PROJECT_ROOT", Path(temp_dir)
        ), patch.dict(os.environ, {}, clear=True):
            result = system_settings.complete_setup(
                {
                    "ldap_uri": "ldap://ldap.internal",
                    "ldap_base_dn": "dc=example,dc=com",
                    "ldap_bind_dn": "cn=admin,dc=example,dc=com",
                    "ldap_bind_password": "bind-password",
                    "ldap_port": 389,
                    "ldap_use_ssl": False,
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
            self.assertGreaterEqual(len(os.environ["SECRET_KEY"]), 32)
            self.assertIn('SETUP_COMPLETED="True"', content)
            self.assertNotIn("bind-password", str(result))

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
            system_settings, "PROJECT_ROOT", Path(temp_dir)
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
            system_settings, "PROJECT_ROOT", Path(temp_dir)
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
            system_settings, "PROJECT_ROOT", Path(temp_dir)
        ):
            for update in invalid_updates:
                with self.subTest(update=update), self.assertRaises(
                    system_settings.SystemSettingsError
                ):
                    system_settings.save_config(update)
            self.assertFalse((Path(temp_dir) / ".env").exists())


if __name__ == "__main__":
    unittest.main()
