import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from fastapi.testclient import TestClient
from openhpc_webui import application as main
from openhpc_webui.services.slurm_manager import SlurmManager, SlurmServiceUnavailable


class SlurmQosManagerTests(unittest.TestCase):
    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_list_qos_normalizes_json(self, run):
        run.return_value = Mock(
            stdout=json.dumps({"qos": [{
                "name": "gpu", "priority": {"set": True, "number": 50},
                "flags": ["DenyOnLimit"],
                "limits": {"max": {
                    "wall_clock": {"per": {"qos": {"infinite": True}}},
                    "jobs": {"active_jobs": {"per": {"user": {"set": True, "number": 1}}}},
                    "tres": {"per": {"user": [{"type": "cpu", "count": 1}]}},
                }},
            }]})
        )
        result = SlurmManager().list_qos()
        self.assertEqual(result[0]["name"], "gpu")
        self.assertEqual(result[0]["priority"], 50)
        self.assertIsNone(result[0]["max_wall"])
        self.assertEqual(result[0]["max_jobs_pu"], 1)
        self.assertEqual(result[0]["max_tres_pu"], "cpu=1")
        self.assertEqual(run.call_args.args[0], ["sacctmgr", "show", "qos", "--json"])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_list_distinguishes_zero_limits_from_unlimited_resources(self, run):
        run.return_value = Mock(stdout=json.dumps({"qos": [{
            "name": "gpu", "limits": {"max": {
                "jobs": {"active_jobs": {"per": {"user": 0}}},
                "tres": {"per": {"user": [
                    {"type": "cpu", "count": {"set": False, "number": 0}},
                    {"type": "mem", "count": {"infinite": True}},
                    {"type": "gres", "name": "gpu", "count": 0},
                ]}},
            }},
        }]}))
        qos = SlurmManager().list_qos()[0]
        self.assertEqual(qos["max_jobs_pu"], 0)
        self.assertEqual(qos["max_tres_pu"], "gres/gpu=0")

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_create_qos_uses_argument_list(self, run):
        self.assertTrue(SlurmManager().create_qos(
            "gpu", priority=50, max_wall="2-00:00:00", max_jobs_pu=4
        ))
        self.assertEqual(run.call_args.args[0], [
            "sacctmgr", "-i", "add", "qos", "name=gpu",
            "Priority=50", "MaxWall=2-00:00:00", "MaxJobsPU=4",
        ])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_create_qos_writes_both_account_and_user_limits(self, run):
        self.assertTrue(SlurmManager().create_qos(
            "mixed", max_jobs_pu=1, max_jobs_pa=4,
            max_submit_jobs_pu=2, max_submit_jobs_pa=8,
            max_tres_pu="cpu=1", max_tres_pa="cpu=32",
        ))
        self.assertEqual(run.call_args.args[0], [
            "sacctmgr", "-i", "add", "qos", "name=mixed",
            "MaxJobsPU=1", "MaxSubmitJobsPU=2", "MaxTRESPU=cpu=1",
            "MaxJobsPA=4", "MaxSubmitJobsPA=8", "MaxTRESPA=cpu=32",
        ])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_update_explicit_null_clears_scalar_limits(self, run):
        self.assertTrue(SlurmManager().update_qos(
            "gpu", max_wall=None, max_jobs_pu=None, max_submit_jobs_pu=None,
            max_jobs_pa=None, max_submit_jobs_pa=None,
        ))
        self.assertEqual(run.call_args.args[0], [
            "sacctmgr", "-i", "modify", "qos", "name=gpu", "set",
            "MaxWall=-1", "MaxJobsPU=-1", "MaxSubmitJobsPU=-1",
            "MaxJobsPA=-1", "MaxSubmitJobsPA=-1",
        ])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_update_omitted_limits_are_unchanged_and_zero_is_preserved(self, run):
        self.assertTrue(SlurmManager().update_qos("gpu", priority=0, max_jobs_pu=0))
        self.assertEqual(run.call_args.args[0][6:], ["Priority=0", "MaxJobsPU=0"])
        run.reset_mock()
        self.assertTrue(SlurmManager().update_qos("gpu"))
        run.assert_not_called()

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_update_clears_each_configured_user_and_account_tres(self, run):
        run.return_value = Mock(stdout=json.dumps({"qos": [{
            "name": "gpu", "limits": {"max": {"tres": {"per": {
                "user": [{"type": "cpu", "count": 4},
                         {"type": "gres", "name": "gpu", "count": 2}],
                "account": [{"type": "mem", "count": 8192},
                            {"type": "gres", "name": "gpu:a100", "count": 8}],
            }}}},
        }]}))
        self.assertTrue(SlurmManager().update_qos("gpu", max_tres_pu=None, max_tres_pa=None))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][6:], [
            "MaxTRESPU=cpu=-1,gres/gpu=-1", "MaxTRESPA=mem=-1,gres/gpu:a100=-1",
        ])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_clearing_unset_tres_needs_no_modify_command(self, run):
        run.return_value = Mock(stdout='{"qos": [{"name": "gpu"}]}')
        self.assertTrue(SlurmManager().update_qos("gpu", max_tres_pu=None, max_tres_pa=None))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0:3], ["sacctmgr", "show", "qos"])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_clear_tres_aborts_when_current_limits_cannot_be_read(self, run):
        for output in ('{"qos": []}', 'invalid json'):
            with self.subTest(output=output):
                run.reset_mock()
                run.return_value = Mock(stdout=output)
                self.assertFalse(SlurmManager().update_qos("gpu", priority=10, max_tres_pu=None))
                self.assertEqual(run.call_count, 1)
        run.side_effect = subprocess.TimeoutExpired("sacctmgr", 1)
        with self.assertRaises(SlurmServiceUnavailable):
            SlurmManager().update_qos("gpu", max_tres_pu=None)

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_create_null_limits_remain_unspecified(self, run):
        self.assertTrue(SlurmManager().create_qos(
            "gpu", max_wall=None, max_jobs_pu=None, max_tres_pu=None,
        ))
        self.assertEqual(run.call_args.args[0], ["sacctmgr", "-i", "add", "qos", "name=gpu"])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_delete_qos_returns_false_on_command_failure(self, run):
        run.side_effect = RuntimeError("sacctmgr unavailable")
        self.assertFalse(SlurmManager().delete_qos("gpu"))


class SlurmQosUpdateApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.user = {"username": "admin", "is_admin": True}
        main.app.dependency_overrides[main.get_current_user] = lambda: self.user
        self.addCleanup(main.app.dependency_overrides.pop, main.get_current_user, None)

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_null_limit_in_update_reaches_slurm_as_clear(self, run):
        response = self.client.put("/api/slurm/qos/gpu", json={"max_jobs_pu": None})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(run.call_args.args[0][6:], ["MaxJobsPU=-1"])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_priority_update_does_not_clear_omitted_limits(self, run):
        response = self.client.put("/api/slurm/qos/gpu", json={"priority": 20})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(run.call_args.args[0][6:], ["Priority=20"])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_regular_user_cannot_remove_limits(self, run):
        self.user = {"username": "alice", "is_admin": False}
        response = self.client.put("/api/slurm/qos/gpu", json={"max_jobs_pu": None})
        self.assertEqual(response.status_code, 403)
        run.assert_not_called()

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_negative_limit_is_not_a_public_api_clear_value(self, run):
        response = self.client.put("/api/slurm/qos/gpu", json={"max_jobs_pu": -1})
        self.assertEqual(response.status_code, 422)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
