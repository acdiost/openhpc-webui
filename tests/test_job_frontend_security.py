import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


class JobFrontendSecurityTests(unittest.TestCase):
    def test_foreign_job_detail_and_monitor_actions_are_disabled(self):
        source = (PROJECT_ROOT / "templates/jobs.html").read_text(encoding="utf-8")

        self.assertIn("const canAccess =", source)
        self.assertIn("__USER__.isAdmin || job.user === __USER__.username", source)
        self.assertIn('canAccess ? "secondary" : "disabled"', source)
        self.assertIn('canAccess ? "monitor" : "disabled"', source)
        self.assertIn("只能查看自己的作业详情和监控", source)


if __name__ == "__main__":
    unittest.main()
