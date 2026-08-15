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

