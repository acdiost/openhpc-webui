import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
NGINX_EXAMPLE = PROJECT_ROOT / "deploy/nginx/openhpc_webui.conf.example"
DEPLOYMENT_GUIDE = PROJECT_ROOT / "docs/DEPLOYMENT.md"


class DeploymentConfigTests(unittest.TestCase):
    def test_nginx_accepts_the_default_application_upload_limit(self):
        config = NGINX_EXAMPLE.read_text(encoding="utf-8")

        match = re.search(r"^\s*client_max_body_size\s+(\d+)m;", config, re.MULTILINE)

        self.assertIsNotNone(match)
        self.assertGreater(int(match.group(1)), 1024)

    def test_deployment_guide_keeps_proxy_and_application_limits_in_sync(self):
        guide = DEPLOYMENT_GUIDE.read_text(encoding="utf-8")

        self.assertIn("client_max_body_size 1025m;", guide)
        self.assertIn("FILE_UPLOAD_MAX_MB", guide)
        self.assertRegex(guide, r"同步.{0,20}(调整|修改)")


if __name__ == "__main__":
    unittest.main()
