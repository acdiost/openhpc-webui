import json
import unittest
from unittest.mock import Mock, patch

from openhpc_webui.services.slurm_manager import SlurmManager


class SlurmQosManagerTests(unittest.TestCase):
    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_list_qos_normalizes_json(self, run):
        run.return_value = Mock(
            stdout=json.dumps({"qos": [{"name": "gpu", "priority": 50, "flags": ["DenyOnLimit"]}]})
        )
        result = SlurmManager().list_qos()
        self.assertEqual(result[0]["name"], "gpu")
        self.assertEqual(result[0]["priority"], 50)
        self.assertEqual(run.call_args.args[0], ["sacctmgr", "show", "qos", "--json"])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_create_qos_uses_argument_list(self, run):
        self.assertTrue(SlurmManager().create_qos(
            "gpu", priority=50, max_wall="2-00:00:00", max_jobs_pa=4
        ))
        self.assertEqual(run.call_args.args[0], [
            "sacctmgr", "-i", "add", "qos", "name=gpu",
            "Priority=50", "MaxWall=2-00:00:00", "MaxJobsPA=4",
        ])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_delete_qos_returns_false_on_command_failure(self, run):
        run.side_effect = RuntimeError("sacctmgr unavailable")
        self.assertFalse(SlurmManager().delete_qos("gpu"))


if __name__ == "__main__":
    unittest.main()
