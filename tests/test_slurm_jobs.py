import unittest
from unittest.mock import Mock, patch

from openhpc_webui.services.slurm_manager import SlurmManager


class SlurmJobListTests(unittest.TestCase):
    def setUp(self):
        self.manager = SlurmManager.__new__(SlurmManager)

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_active_jobs_include_allocated_cpu_count(self, run):
        run.return_value = Mock(
            stdout="header\n123|train|alice|cpu|RUNNING|2|16|01:00|2026-08-15T10:00:00|node01\n"
        )

        jobs = self.manager.list_jobs()

        self.assertEqual(jobs[0]["cpus"], 16)
        self.assertEqual(jobs[0]["alloc_cpus"], 16)

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_completed_jobs_include_allocated_cpu_count(self, run):
        run.return_value = Mock(
            stdout="123|train|alice|COMPLETED|32|00:10:00|2026-08-15T10:00:00|0:0\n"
        )

        jobs = self.manager.list_completed_jobs()

        self.assertEqual(jobs[0]["cpus"], 32)
        self.assertEqual(jobs[0]["alloc_cpus"], 32)

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_job_resource_usage_parses_sstat_metrics(self, run):
        run.side_effect = [
            Mock(
                stdout=(
                    "JobId=123 JobState=RUNNING NumNodes=1 NumCPUs=4 "
                    "RunTime=00:10:00 ReqTRES=cpu=4,mem=8G,gres/gpu=1 "
                    "AllocTRES=cpu=4,mem=8G,gres/gpu=1\n"
                )
            ),
            Mock(
                stdout=(
                    "123.batch|1|00:20:00|512M|1G|2G|512M|"
                    "cpu=00:05:00,gres/gpuutil=72|gres/gpumem=4G|cpu=4\n"
                )
            ),
        ]

        usage = self.manager.get_job_resource_usage("123")

        self.assertTrue(usage["available"])
        self.assertEqual(usage["allocation"]["cpus"], 4)
        self.assertEqual(usage["allocation"]["gpus"], 1)
        self.assertEqual(usage["summary"]["cpu_percent"], 50.0)
        self.assertEqual(usage["summary"]["memory_bytes"], 512 * 1024 ** 2)
        self.assertEqual(usage["summary"]["peak_rss_bytes"], 1024 ** 3)
        self.assertEqual(usage["summary"]["gpu_percent"], 72.0)
        self.assertEqual(usage["summary"]["gpu_memory_bytes"], 4 * 1024 ** 3)
        self.assertEqual(run.call_args_list[1].args[0][0], "sstat")

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_job_resource_usage_handles_pending_job_without_steps(self, run):
        run.side_effect = [
            Mock(stdout="JobId=124 JobState=PENDING NumNodes=1 NumCPUs=2\n"),
            Mock(stdout=""),
        ]

        usage = self.manager.get_job_resource_usage("124")

        self.assertFalse(usage["available"])
        self.assertEqual(usage["steps"], [])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_job_resource_usage_rejects_invalid_job_id(self, run):
        self.assertIsNone(self.manager.get_job_resource_usage("--help"))
        run.assert_not_called()
