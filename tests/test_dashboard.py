import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

import openhpc_webui.application as main


PROJECT_ROOT = Path(__file__).parents[1]


class DashboardTemplateTests(unittest.TestCase):
    def test_admin_sidebar_contains_active_overview_link(self):
        sidebar = (PROJECT_ROOT / "templates/components/sidebar.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('href="/"', sidebar)
        self.assertIn("request.url.path == '/'", sidebar)
        self.assertIn("总览", sidebar)

    def test_partition_status_uses_api_field_names(self):
        dashboard = (PROJECT_ROOT / "templates/index.html").read_text(
            encoding="utf-8"
        )

        for field in ("total_nodes", "alloc_nodes", "idle_nodes", "offline_nodes"):
            self.assertIn(f"p.{field}", dashboard)

        for stale_field in ("nodes_alloc", "nodes_idle", "nodes_other"):
            self.assertNotIn(f"p.{stale_field}", dashboard)

    def test_personal_dashboard_template_and_navigation_are_present(self):
        template = (PROJECT_ROOT / "templates/user_dashboard.html").read_text(
            encoding="utf-8"
        )
        sidebar = (PROJECT_ROOT / "templates/components/sidebar.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("/api/slurm/my/dashboard", template)
        self.assertIn("myCpuHours", template)
        self.assertIn("myGpuHours", template)
        self.assertIn("个人总览", sidebar)

    def test_personal_dashboard_only_returns_current_users_active_jobs(self):
        active_jobs = [
            {"job_id": "1", "user": "alice", "state": "RUNNING"},
            {"job_id": "2", "user": "bob", "state": "RUNNING"},
        ]
        report = {"jobs": [], "totals": {"cpu_hours": 2.5, "gpu_hours": 1.0}}
        with patch.object(main.slurm_mgr, "list_jobs", return_value=active_jobs), patch.object(
            main.slurm_mgr, "get_user_job_report", return_value=report
        ), patch.object(main.slurm_mgr, "list_partitions", return_value=[]):
            data = asyncio.run(
                main.get_my_dashboard({"username": "alice", "is_admin": False})
            )

        self.assertEqual([job["job_id"] for job in data["active_jobs"]], ["1"])
        self.assertEqual(data["totals"]["cpu_hours"], 2.5)


if __name__ == "__main__":
    unittest.main()
