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

    def test_sidebar_navigation_is_grouped_in_task_order(self):
        sidebar = (PROJECT_ROOT / "templates/components/sidebar.html").read_text(
            encoding="utf-8"
        )

        labels = [
            "工作台",
            "集群资源",
            "用户与配额",
            "系统管理",
            "使用帮助",
            "外部系统",
        ]
        positions = [sidebar.index(label) for label in labels]

        self.assertEqual(positions, sorted(positions))
        self.assertIn('aria-label="主导航"', sidebar)
        self.assertEqual(sidebar.count('href="/jobs"'), 1)
        self.assertLess(sidebar.index("节点管理"), sidebar.index("分区管理"))
        self.assertLess(sidebar.index("用户管理"), sidebar.index("组管理"))
        self.assertLess(sidebar.index("组管理"), sidebar.index("集群账户"))

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
        self.assertIn("myCpuUsed", template)
        self.assertIn("myCpuRemaining", template)
        self.assertIn("myCpuAvailable", template)
        self.assertIn("myGpuUsed", template)
        self.assertIn("个人总览", sidebar)

    def test_personal_dashboard_only_returns_current_users_active_jobs(self):
        active_jobs = [
            {"job_id": "1", "user": "alice", "state": "RUNNING"},
            {"job_id": "2", "user": "bob", "state": "RUNNING"},
        ]
        report = {"jobs": [], "totals": {"cpu_hours": 2.5, "gpu_hours": 1.0}}
        with patch.object(main.slurm_mgr, "list_jobs", return_value=active_jobs), patch.object(
            main.slurm_mgr, "get_user_job_report", return_value=report
        ), patch.object(
            main.slurm_mgr,
            "get_users_tres_limits",
            return_value={"alice": {"cpu_minutes": 600, "gpu_minutes": None}},
        ), patch.object(main.slurm_mgr, "list_partitions", return_value=[]):
            data = asyncio.run(
                main.get_my_dashboard({"username": "alice", "is_admin": False})
            )

        self.assertEqual([job["job_id"] for job in data["active_jobs"]], ["1"])
        self.assertEqual(data["totals"]["cpu_hours"], 2.5)
        self.assertEqual(data["resources"]["cpu"], {
            "used_hours": 2.5,
            "remaining_hours": 7.5,
            "available_hours": 10.0,
        })
        self.assertIsNone(data["resources"]["gpu"]["available_hours"])


if __name__ == "__main__":
    unittest.main()
