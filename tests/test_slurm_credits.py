import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import main
from fastapi import HTTPException

from slurm_manager import SlurmManager


class SlurmCreditManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = SlurmManager.__new__(SlurmManager)

    def test_grant_adds_minutes_above_current_limit_or_usage(self):
        self.manager.get_user_default_account = Mock(return_value="dawn")
        self.manager.get_association_tres_minutes = Mock(
            side_effect=[
                {"cpu": 600, "gres/gpu": None},
                {"cpu": 720, "gres/gpu": 280},
            ]
        )
        self.manager.get_user_tres_usage_minutes = Mock(
            return_value={"cpu": 550, "gres/gpu": 100}
        )
        self.manager.set_association_tres_minutes = Mock(return_value=True)

        result = self.manager.grant_user_tres_hours(
            "dawn", cpu_hours=2, gpu_hours=3
        )

        self.manager.set_association_tres_minutes.assert_called_once_with(
            username="dawn",
            account="dawn",
            cpu_minutes=720,
            gpu_minutes=280,
        )
        self.assertEqual(result["remaining_cpu_minutes"], 170)
        self.assertEqual(result["remaining_gpu_minutes"], 180)

    def test_grant_repairs_an_already_exceeded_limit(self):
        self.manager.get_user_default_account = Mock(return_value="phadcloud")
        self.manager.get_association_tres_minutes = Mock(
            side_effect=[
                {"cpu": 3, "gres/gpu": 0},
                {"cpu": 7493, "gres/gpu": 0},
            ]
        )
        self.manager.get_user_tres_usage_minutes = Mock(
            return_value={"cpu": 7433, "gres/gpu": 467}
        )
        self.manager.set_association_tres_minutes = Mock(return_value=True)

        result = self.manager.grant_user_tres_hours(
            "dawn11139", account="phadcloud", cpu_hours=1
        )

        self.assertEqual(result["cpu_limit_minutes"], 7493)
        self.assertEqual(result["remaining_cpu_minutes"], 60)

    def test_grant_rejects_invalid_slurm_names_before_commands(self):
        with patch("slurm_manager.subprocess.run") as run:
            result = self.manager.grant_user_tres_hours(
                "dawn account=root", cpu_hours=1
            )

        self.assertIsNone(result)
        run.assert_not_called()

    @patch("slurm_manager.subprocess.run")
    def test_reads_zero_and_unlimited_tres_limits_distinctly(self, run):
        run.return_value = Mock(
            stdout="dawn|dawn||cpu=120,gres/gpu=0\n"
        )

        limits = self.manager.get_association_tres_minutes("dawn", "dawn")

        self.assertEqual(limits, {"cpu": 120, "gres/gpu": 0})

    @patch("slurm_manager.subprocess.run")
    def test_partition_grant_targets_only_the_selected_association(self, run):
        run.side_effect = [
            Mock(
                stdout=(
                    "dawn|dawn||cpu=120,gres/gpu=60\n"
                    "dawn|dawn|CPU|cpu=240,gres/gpu=0\n"
                )
            ),
            Mock(stdout="updated\n"),
        ]

        limits = self.manager.get_association_tres_minutes(
            "dawn", "dawn", partition="CPU"
        )
        success = self.manager.set_association_tres_minutes(
            "dawn", "dawn", cpu_minutes=300, partition="CPU"
        )

        self.assertEqual(limits, {"cpu": 240, "gres/gpu": 0})
        self.assertTrue(success)
        modify_args = run.call_args_list[1].args[0]
        self.assertIn("partition=CPU", modify_args)

    @patch("slurm_manager.subprocess.run")
    def test_usage_minutes_rounds_up_partial_minutes(self, run):
        run.side_effect = [
            Mock(stdout="dawn|dawn|61\n"),
            Mock(stdout="dawn|dawn|60\n"),
        ]

        usage = self.manager.get_user_tres_usage_minutes("dawn", "dawn")

        self.assertEqual(usage, {"cpu": 2, "gres/gpu": 1})

    @patch("slurm_manager.subprocess.run")
    def test_lists_user_tres_limits_preferring_global_association(self, run):
        run.return_value = Mock(
            stdout=(
                "dawn|dawn|CPU|cpu=600,gres/gpu=0\n"
                "dawn|dawn||cpu=1200\n"
                "alice|research||cpu=0,gres/gpu=30\n"
            )
        )

        limits = self.manager.get_users_tres_limits()

        self.assertEqual(limits["dawn"], {"cpu_minutes": 1200, "gpu_minutes": None})
        self.assertEqual(limits["alice"], {"cpu_minutes": 0, "gpu_minutes": 30})

    @patch("slurm_manager.subprocess.run")
    def test_absolute_limit_write_rejects_invalid_names(self, run):
        success = self.manager.set_association_tres_minutes(
            "dawn where account=root", "dawn", cpu_minutes=60
        )

        self.assertFalse(success)
        run.assert_not_called()


class SlurmCreditApiTests(unittest.TestCase):
    def test_credit_endpoint_grants_cpu_and_gpu_hours(self):
        payload = main.UserCreditRequest(
            account="dawn",
            cpu_hours=2.5,
            gpu_hours=1.5,
            reason="grant",
            note="test allocation",
        )
        grant_result = {
            "account": "dawn",
            "cpu_granted_minutes": 150,
            "gpu_granted_minutes": 90,
            "cpu_limit_minutes": 150,
            "gpu_limit_minutes": 90,
            "remaining_cpu_minutes": 150,
            "remaining_gpu_minutes": 90,
        }

        with patch.object(
            main.slurm_mgr, "grant_user_tres_hours", return_value=grant_result
        ) as grant:
            result = asyncio.run(
                main.allocate_user_credits(
                    "dawn", payload, {"username": "admin", "is_admin": True}
                )
            )

        grant.assert_called_once_with(
            username="dawn",
            account="dawn",
            cpu_hours=2.5,
            gpu_hours=1.5,
        )
        self.assertEqual(result["remaining_gpu_hours"], 1.5)

    def test_credit_endpoint_requires_at_least_one_positive_amount(self):
        payload = main.UserCreditRequest(cpu_hours=0, gpu_hours=0)

        with self.assertRaises(HTTPException) as context:
            asyncio.run(
                main.allocate_user_credits(
                    "dawn", payload, {"username": "admin", "is_admin": True}
                )
            )

        self.assertEqual(context.exception.status_code, 400)


class SlurmCreditTemplateTests(unittest.TestCase):
    def test_user_credit_form_supports_cpu_and_gpu_hours(self):
        template = (
            Path(__file__).parents[1] / "templates/users.html"
        ).read_text(encoding="utf-8")

        self.assertIn('name="cpu_hours"', template)
        self.assertIn('name="gpu_hours"', template)
        self.assertIn("核时/卡时拨付", template)
        self.assertIn("核时限额 (h)", template)
        self.assertIn("卡时限额 (h)", template)
        self.assertIn("user.cpu_minutes", template)
        self.assertIn("user.gpu_minutes", template)

    def test_association_credit_form_submits_incremental_hours(self):
        template = (
            Path(__file__).parents[1] / "templates/cluster_users.html"
        ).read_text(encoding="utf-8")

        self.assertIn('name="cpu_hours"', template)
        self.assertIn('name="gpu_hours"', template)
        self.assertIn('name="partition"', template)
        self.assertIn("partition: partition || null", template)
        self.assertIn("grantUserCredits", template)


if __name__ == "__main__":
    unittest.main()
