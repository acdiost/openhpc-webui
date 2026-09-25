import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
NGINX_EXAMPLE = PROJECT_ROOT / "deploy/nginx/openhpc_webui.conf.example"
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts/deploy.sh"


class DeploymentConfigTests(unittest.TestCase):
    def test_nginx_accepts_the_default_application_upload_limit(self):
        config = NGINX_EXAMPLE.read_text(encoding="utf-8")

        match = re.search(r"^\s*client_max_body_size\s+(\d+)m;", config, re.MULTILINE)

        self.assertIsNotNone(match)
        self.assertGreater(int(match.group(1)), 1024)

    def test_deploy_script_generates_websocket_and_upload_proxy_config(self):
        with tempfile.TemporaryDirectory() as workspace:
            workspace = Path(workspace)
            script = workspace / "deploy.sh"
            script.write_bytes(DEPLOY_SCRIPT.read_bytes())
            executable_dir = workspace / "bin"
            executable_dir.mkdir()
            openssl = executable_dir / "openssl"
            openssl.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = req ]; then\n'
                '    while [ "$#" -gt 0 ]; do\n'
                '        case "$1" in\n'
                '            -keyout|-out) touch "$2"; shift 2 ;;\n'
                '            *) shift ;;\n'
                '        esac\n'
                '    done\n'
                'fi\n',
                encoding="utf-8",
            )
            openssl.chmod(0o755)
            docker = executable_dir / "docker"
            docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            docker.chmod(0o755)
            subprocess.run(
                ["bash", str(script)], cwd=workspace, check=True,
                env={**os.environ, "PATH": f"{executable_dir}{os.pathsep}{os.environ['PATH']}"},
                capture_output=True, text=True,
            )

            http_config = (workspace / "deploy/nginx/nginx.conf").read_text(encoding="utf-8")
            proxy_config = (workspace / "deploy/nginx/default.conf").read_text(encoding="utf-8")
            self.assertRegex(
                http_config,
                r'(?s)http\s*\{.*map\s+\$http_upgrade\s+\$connection_upgrade\s*\{'
                r'\s*default\s+upgrade;\s*""\s+close;',
            )
            self.assertRegex(proxy_config, r"proxy_set_header\s+Upgrade\s+\$http_upgrade;")
            self.assertRegex(proxy_config, r"proxy_set_header\s+Connection\s+\$connection_upgrade;")
            self.assertRegex(proxy_config, r"proxy_read_timeout\s+3600s;")
            self.assertRegex(proxy_config, r"proxy_send_timeout\s+3600s;")
            match = re.search(r"client_max_body_size\s+(\d+)m;", proxy_config)
            self.assertIsNotNone(match)
            self.assertGreater(int(match.group(1)), 1024)


if __name__ == "__main__":
    unittest.main()
