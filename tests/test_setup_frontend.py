import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SetupFrontendTests(unittest.TestCase):
    def test_setup_page_contains_guided_installation_steps(self):
        template = (PROJECT_ROOT / "templates/setup.html").read_text(encoding="utf-8")

        self.assertIn("安装向导", template)
        self.assertIn('id="setupForm"', template)
        self.assertIn('data-step="1"', template)
        self.assertIn('data-step="2"', template)
        self.assertIn('data-step="3"', template)
        self.assertIn('/api/setup', template)
        self.assertIn("选择身份集成方式", template)
        self.assertIn('name="identity_backend"', template)
        self.assertIn('value="system"', template)
        self.assertIn('value="ldap"', template)
        self.assertIn('id="setup_system_options"', template)
        self.assertIn('id="setup_ldap_options"', template)
        self.assertIn('id="setup_ldap_bind_password"', template)
        self.assertIn('id="setup_ldap_uri"', template)
        self.assertIn("updateIdentityBackend", template)


if __name__ == "__main__":
    unittest.main()
