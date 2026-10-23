import os
import subprocess
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from fastapi.testclient import TestClient
from starlette.requests import Request

from openhpc_webui import application as main
from openhpc_webui.services.slurm_manager import SlurmManager, SlurmServiceUnavailable


class JobPriorityManagerTests(unittest.TestCase):
    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_list_and_detail_expose_unscaled_priority(self, run):
        run.return_value = Mock(stdout=(
            "header\n123|train|alice|cpu|PENDING|1|4|0:00|Unknown|(Priority)|123456\n"
        ))
        manager = SlurmManager.__new__(SlurmManager)
        self.assertEqual(manager.list_jobs()[0]["priority"], 123456)
        self.assertIn("%Q", run.call_args.args[0][-1])
        run.return_value = Mock(stdout="JobId=123 Priority=0 JobState=PENDING")
        self.assertEqual(manager.get_job_detail("123")["Priority"], "0")

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_update_uses_validated_command_arguments(self, run):
        manager = SlurmManager.__new__(SlurmManager)
        for job_id in ("123", "123_4", "123+1"):
            for priority in (0, 500, 4294967294):
                with self.subTest(job_id=job_id, priority=priority):
                    self.assertTrue(manager.update_job_priority(job_id, priority))
                    self.assertEqual(run.call_args.args[0], [
                        "scontrol", "update", f"JobId={job_id}", f"Priority={priority}",
                    ])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_update_rejects_invalid_input_without_running_commands(self, run):
        manager = SlurmManager.__new__(SlurmManager)
        for job_id, priority in (
            ("--help", 5), ("123,124", 5), ("123", -1),
            ("123", 4294967295), ("123", True), ("123", 1.5),
            ("123", "5 Nice=-100"),
        ):
            self.assertFalse(manager.update_job_priority(job_id, priority))
        run.assert_not_called()

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_command_failure_and_timeout(self, run):
        manager = SlurmManager.__new__(SlurmManager)
        run.side_effect = subprocess.CalledProcessError(1, "scontrol")
        self.assertFalse(manager.update_job_priority("123", 5))
        run.side_effect = subprocess.TimeoutExpired("scontrol", 10)
        with self.assertRaises(SlurmServiceUnavailable):
            manager.update_job_priority("123", 5)


class JobPriorityApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.user = {"username": "admin", "is_admin": True}
        main.app.dependency_overrides[main.get_current_user] = lambda: self.user
        self.addCleanup(main.app.dependency_overrides.pop, main.get_current_user, None)
        self.detail_patch = patch.object(main.slurm_mgr, "get_job_detail", return_value={
            "JobId": "123", "UserId": "alice(1001)", "Priority": "100",
        })
        self.detail = self.detail_patch.start()
        self.addCleanup(self.detail_patch.stop)
        self.update_patch = patch.object(main.slurm_mgr, "update_job_priority", return_value=True)
        self.update = self.update_patch.start()
        self.addCleanup(self.update_patch.stop)

    def test_admin_can_update_priority(self):
        for priority in (0, 500, 4294967294):
            with self.subTest(priority=priority):
                response = self.client.patch("/api/slurm/jobs/123/priority", json={"priority": priority})
                self.assertEqual(response.status_code, 200)
                self.update.assert_called_with("123", priority)

    def test_unauthenticated_request_is_rejected(self):
        main.app.dependency_overrides.pop(main.get_current_user)
        with patch.object(main, "AUTH_ENABLED", True):
            response = self.client.patch("/api/slurm/jobs/123/priority", json={"priority": 5})
        self.assertEqual(response.status_code, 401)
        self.update.assert_not_called()

    def test_array_task_priority_update_targets_only_the_requested_task(self):
        self.detail.return_value = {
            "JobId": "3741", "UserId": "alice(1001)", "Priority": "100",
        }
        response = self.client.patch(
            "/api/slurm/jobs/3738_3/priority", json={"priority": 500},
        )
        self.assertEqual(response.status_code, 200)
        self.detail.assert_called_with("3738_3")
        self.update.assert_called_once_with("3738_3", 500)

    def test_audit_snapshot_includes_priority(self):
        request = Request({
            "type": "http", "method": "PATCH",
            "path": "/api/slurm/jobs/123/priority",
            "headers": [], "session": {"user": self.user},
        })
        snapshot = main._audit_snapshot_sync(request, {"priority": 500})
        self.assertEqual(snapshot["Priority"], "100")

    def test_regular_user_cannot_update_even_own_job(self):
        self.user = {"username": "alice", "is_admin": False}
        response = self.client.patch("/api/slurm/jobs/123/priority", json={"priority": 0})
        self.assertEqual(response.status_code, 403)
        self.update.assert_not_called()
        self.detail.assert_not_called()

    def test_invalid_priority_is_rejected(self):
        for value in (-1, 4294967295, 1.5, True, "100", None):
            with self.subTest(value=value):
                response = self.client.patch("/api/slurm/jobs/123/priority", json={"priority": value})
                self.assertEqual(response.status_code, 422)
        self.update.assert_not_called()

    def test_invalid_job_id_is_rejected(self):
        response = self.client.patch("/api/slurm/jobs/--help/priority", json={"priority": 10})
        self.assertEqual(response.status_code, 400)
        self.update.assert_not_called()

    def test_missing_job_and_command_failure(self):
        self.detail.return_value = None
        response = self.client.patch("/api/slurm/jobs/123/priority", json={"priority": 10})
        self.assertEqual(response.status_code, 404)
        self.update.assert_not_called()
        self.detail.return_value = {"JobId": "123"}
        self.update.return_value = False
        response = self.client.patch("/api/slurm/jobs/123/priority", json={"priority": 10})
        self.assertEqual(response.status_code, 500)
