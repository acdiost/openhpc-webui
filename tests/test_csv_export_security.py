import asyncio
import csv
import io
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import openhpc_webui.application as main


ADMIN = {"username": "admin", "is_admin": True}


class CsvExportSecurityTests(unittest.TestCase):
    def _export(self, jobs):
        with patch.object(
            main.slurm_mgr,
            "get_user_job_report",
            return_value={"jobs": jobs},
        ):
            response = asyncio.run(
                main.export_user_report_csv("alice", user=ADMIN)
            )
        return list(csv.reader(io.StringIO(response.body.decode("utf-8"))))

    def test_formula_like_cells_are_exported_as_text(self):
        dangerous_names = [
            '=HYPERLINK("https://attacker.invalid/","x")',
            "+SUM(1,1)",
            "-2+3",
            "@SUM(1,1)",
            "\t=1+1",
            "\r=1+1",
            "\n=1+1",
            "  =1+1",
        ]

        rows = self._export([{"name": value} for value in dangerous_names])

        self.assertEqual(
            [row[1] for row in rows[1:]],
            [f"'{value}" for value in dangerous_names],
        )

    def test_standard_csv_writer_preserves_special_characters(self):
        value = 'safe, "quoted"\nsecond line'

        rows = self._export([{"name": value}])

        self.assertEqual(rows[1][1], value)
        self.assertEqual(len(rows[1]), 12)

    def test_formula_protection_applies_to_every_exported_column(self):
        jobs = [{"job_id": "=1+1", "partition": "@SUM(1,1)"}]

        rows = self._export(jobs)

        self.assertEqual(rows[1][0], "'=1+1")
        self.assertEqual(rows[1][3], "'@SUM(1,1)")


if __name__ == "__main__":
    unittest.main()
