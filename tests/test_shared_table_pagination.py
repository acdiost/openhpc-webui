import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


class SharedTablePaginationTests(unittest.TestCase):
    def test_base_loads_shared_pagination_component(self):
        base = (PROJECT_ROOT / "templates/base.html").read_text(encoding="utf-8")

        self.assertIn('/static/table_pagination.js?v={{ app_version }}', base)

    def test_operational_list_pages_opt_in_to_shared_pagination(self):
        expected_tables = {
            "accounts.html": 1,
            "admin.html": 1,
            "cluster_users.html": 1,
            "groups.html": 1,
            "jobs.html": 2,
            "nodes.html": 1,
            "partitions.html": 1,
            "qos.html": 1,
            "slurm_users.html": 1,
            "user_dashboard.html": 2,
        }

        for template_name, expected_count in expected_tables.items():
            with self.subTest(template=template_name):
                source = (PROJECT_ROOT / "templates" / template_name).read_text(
                    encoding="utf-8"
                )
                self.assertEqual(source.count('data-paginate="true"'), expected_count)

    def test_existing_specialized_pagination_is_not_replaced(self):
        files = (PROJECT_ROOT / "templates/files.html").read_text(encoding="utf-8")
        users = (PROJECT_ROOT / "templates/users.html").read_text(encoding="utf-8")

        self.assertNotIn('data-paginate="true"', files)
        self.assertNotIn('id="usersTable" data-paginate="true"', users)

    def test_shared_styles_cover_controls_and_mobile_layout(self):
        styles = (PROJECT_ROOT / "static/compat.css").read_text(encoding="utf-8")

        self.assertIn(".table-pagination", styles)
        self.assertIn(".table-pagination__controls", styles)
        self.assertIn("@media (max-width: 640px)", styles)


if __name__ == "__main__":
    unittest.main()
