import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from openhpc_webui import __version__
from openhpc_webui.application import app, create_app, templates
from openhpc_webui.config import STATIC_DIR, TEMPLATES_DIR


class ApplicationFactoryTests(unittest.TestCase):
    def test_factory_creates_independent_app_with_registered_routes(self):
        created_app = create_app()

        self.assertIsNot(created_app, app)
        route_paths = {route.path for route in created_app.routes}
        self.assertIn("/", route_paths)
        self.assertIn("/api/auth/login", route_paths)
        self.assertIn("/files", route_paths)
        self.assertIn("/api/files", route_paths)
        self.assertIn("/api/files/download", route_paths)
        self.assertIn("/terminal", route_paths)
        self.assertIn("/ws/terminal", route_paths)
        self.assertIn("/static", route_paths)

    def test_runtime_resource_directories_exist(self):
        self.assertTrue(STATIC_DIR.is_dir())
        self.assertTrue(TEMPLATES_DIR.is_dir())

    def test_template_version_matches_package_version(self):
        self.assertEqual(__version__, "0.3.0")
        self.assertEqual(templates.env.globals["app_version"], __version__)


if __name__ == "__main__":
    unittest.main()
