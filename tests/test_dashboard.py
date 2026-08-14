import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
