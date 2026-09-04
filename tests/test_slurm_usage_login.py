import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "slurm_usage_login.sh"


class SlurmUsageLoginTestCase(unittest.TestCase):
    def _run_script(self, user_tres: str, account_tres: str) -> str:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bin_directory = Path(temporary_directory)
            commands = {
                "id": "printf '%s\\n' alice",
                "date": """
                    case "$*" in
                        *%Y-%m-01T00:00:00*) printf '%s\\n' 2026-08-01T00:00:00 ;;
                        *%Y-%m-%dT%H:%M:%S*) printf '%s\\n' 2026-08-26T12:00:00 ;;
                        *%Y-%m*) printf '%s\\n' 2026-08 ;;
                    esac
                """,
                "sacctmgr": "printf '%s\\n' 'alice|research|'",
                "sreport": "printf '%s\\n' 'alice|3600|'",
                "scontrol": f"""
                    case "$*" in
                        *users=alice*)
                            printf '%s\\n' \\
                                'ClusterName=cluster Account=research UserName=alice(1001) Partition=' \\
                                '    GrpTRESMins={user_tres}'
                            ;;
                        *accounts=research*)
                            printf '%s\\n' \\
                                'ClusterName=cluster Account=research UserName= Partition=' \\
                                '    GrpTRESMins={account_tres}'
                            ;;
                    esac
                """,
            }
            for command_name, body in commands.items():
                command_path = bin_directory / command_name
                command_path.write_text(
                    "#!/usr/bin/env bash\n" + textwrap.dedent(body), encoding="utf-8"
                )
                command_path.chmod(0o755)

            environment = {
                **os.environ,
                "HOME": temporary_directory,
                "NO_COLOR": "1",
                "PATH": f"{bin_directory}:{os.environ['PATH']}",
            }
            result = subprocess.run(
                ["bash", str(SCRIPT_PATH)],
                capture_output=True,
                check=True,
                env=environment,
                text=True,
            )
            return result.stdout

    def test_displays_user_and_account_remaining_resources(self):
        output = self._run_script(
            "cpu=600(120),gres/gpu=120(30)",
            "cpu=6000(1800),gres/gpu=600(150)",
        )

        self.assertIn("用户额度", output)
        self.assertIn("账户共享额度", output)
        self.assertIn("剩余      70.00 h", output)
        self.assertIn("剩余       7.50 h", output)

    def test_displays_unlimited_account_resources(self):
        output = self._run_script(
            "cpu=600(120),gres/gpu=120(30)",
            "cpu=N(1800),gres/gpu=N(150)",
        )

        account_section = output.split("账户共享额度", maxsplit=1)[1]
        self.assertEqual(2, account_section.count("额度 无限"))

    def test_marks_account_resource_data_unavailable(self):
        output = self._run_script(
            "cpu=600(120),gres/gpu=120(30)",
            "",
        )

        account_section = output.split("账户共享额度", maxsplit=1)[1]
        self.assertEqual(2, account_section.count("额度数据 不可用"))


if __name__ == "__main__":
    unittest.main()
