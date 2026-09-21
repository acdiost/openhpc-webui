import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from openhpc_webui import application as main


class SetupRouteTests(unittest.TestCase):
    def test_unconfigured_install_redirects_pages_to_setup(self):
        with patch.object(main, "setup_required", return_value=True):
            client = TestClient(main.create_app())
            response = client.get("/login", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/setup")

    def test_setup_submission_requires_token_from_setup_session(self):
        payload = {
            "setup_token": "x" * 32,
            "ldap_uri": "ldap://localhost",
            "ldap_base_dn": "dc=example,dc=com",
            "ldap_bind_dn": "cn=admin,dc=example,dc=com",
            "ldap_bind_password": "password",
            "ldap_port": 389,
            "ldap_use_ssl": False,
            "slurm_cluster_name": "cluster",
            "slurm_default_account": "research",
            "slurm_config_dir": "/etc/slurm",
            "admin_username": "admin",
            "session_https_only": False,
        }
        with patch.object(main, "setup_required", return_value=True):
            client = TestClient(main.create_app())
            client.get("/setup")
            response = client.post("/api/setup", json=payload)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("password", response.text)

    def test_installed_application_locks_setup_page(self):
        with patch.object(main, "setup_required", return_value=False):
            client = TestClient(main.create_app())
            response = client.get("/setup", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login")


if __name__ == "__main__":
    unittest.main()
