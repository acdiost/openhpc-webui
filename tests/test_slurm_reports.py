import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from openhpc_webui.services.slurm_manager import SlurmManager


class SlurmReportTests(unittest.TestCase):
    def setUp(self):
        self.manager = SlurmManager.__new__(SlurmManager)

    def test_month_period_uses_calendar_month_and_current_time(self):
        start, end = self.manager._resolve_report_period(
            "month", now=datetime(2026, 8, 14, 11, 25, 30)
        )

        self.assertEqual(start, "2026-08-01T00:00:00")
        self.assertEqual(end, "2026-08-14T11:25:30")

    def test_custom_end_date_includes_the_whole_selected_day(self):
        start, end = self.manager._resolve_report_period(
            "custom",
            start_date="2026-08-01",
            end_date="2026-08-14",
            now=datetime(2026, 8, 20, 9, 0, 0),
        )

        self.assertEqual(start, "2026-08-01T00:00:00")
        self.assertEqual(end, "2026-08-15T00:00:00")

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_tres_hours_match_sreport_allocated_seconds(self, run):
        run.side_effect = [
            Mock(stdout="lanjun11125|81208748\n"),
            Mock(stdout="lanjun11125|5067695\n"),
        ]

        totals = self.manager._get_user_tres_hours(
            "lanjun11125",
            "2026-08-01T00:00:00",
            "2026-08-14T11:25:30",
        )

        self.assertEqual(totals, {"cpu_hours": 22557.99, "gpu_hours": 1407.69})
        self.assertEqual(run.call_count, 2)
        self.assertIn("cpu", run.call_args_list[0].args[0])
        self.assertIn("gres/gpu", run.call_args_list[1].args[0])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_job_report_reads_allocated_gpu_tres(self, run):
        payload = {
            "jobs": [
                {
                    "job_id": 34015,
                    "name": "Emilia",
                    "state": {"current": ["RUNNING"]},
                    "time": {
                        "elapsed": 3600,
                        "submission": 1786674756,
                        "start": 1786674756,
                        "end": 0,
                    },
                    "required": {"CPUs": 16},
                    "tres": {
                        "allocated": [
                            {"type": "cpu", "name": "", "count": 16},
                            {"type": "gres", "name": "gpu", "count": 1},
                        ]
                    },
                    "partition": "GPU",
                }
            ]
        }
        run.return_value = Mock(stdout=json.dumps(payload))

        report = self.manager._get_user_job_report_json(
            "liyuxiang",
            "2026-08-14T00:00:00",
            "2026-08-14T12:00:00",
        )

        self.assertEqual(report["jobs"][0]["alloc_gpus"], 1)
        self.assertEqual(report["jobs"][0]["gpu_hours"], 1.0)

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_job_hours_are_clipped_to_the_report_period(self, run):
        start = int(datetime(2026, 7, 31, 23, 0, 0).timestamp())
        end = int(datetime(2026, 8, 1, 1, 0, 0).timestamp())
        payload = {
            "jobs": [
                {
                    "job_id": 33229,
                    "state": {"current": ["COMPLETED"]},
                    "time": {
                        "elapsed": 7200,
                        "submission": start,
                        "start": start,
                        "end": end,
                    },
                    "required": {"CPUs": 16},
                    "tres": {
                        "allocated": [
                            {"type": "gres", "name": "gpu", "count": 1}
                        ]
                    },
                }
            ]
        }
        run.return_value = Mock(stdout=json.dumps(payload))

        report = self.manager._get_user_job_report_json(
            "yuanhongen11123",
            "2026-08-01T00:00:00",
            "2026-08-02T00:00:00",
        )

        self.assertEqual(report["jobs"][0]["elapsed_hours"], 1.0)
        self.assertEqual(report["jobs"][0]["cpu_hours"], 16.0)
        self.assertEqual(report["jobs"][0]["gpu_hours"], 1.0)

    def test_report_totals_use_sreport_values(self):
        job_report = {
            "jobs": [],
            "totals": {
                "total_jobs": 4,
                "completed_jobs": 3,
                "failed_jobs": 0,
                "cancelled_jobs": 1,
                "cpu_hours": 999999.0,
                "gpu_hours": 999999.0,
                "elapsed_hours": 120.0,
            },
        }
        self.manager._get_user_job_report_json = Mock(return_value=job_report)
        self.manager._get_user_tres_hours = Mock(
            return_value={"cpu_hours": 22557.99, "gpu_hours": 1407.69}
        )

        with patch.object(
            self.manager,
            "_resolve_report_period",
            return_value=("2026-08-01T00:00:00", "2026-08-14T11:25:30"),
        ):
            report = self.manager.get_user_job_report("lanjun11125", "month")

        self.assertEqual(report["totals"]["cpu_hours"], 22557.99)
        self.assertEqual(report["totals"]["gpu_hours"], 1407.69)
        self.manager._get_user_tres_hours.assert_called_once_with(
            "lanjun11125",
            "2026-08-01T00:00:00",
            "2026-08-14T11:25:30",
        )


if __name__ == "__main__":
    unittest.main()
