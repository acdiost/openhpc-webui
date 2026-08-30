import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

import openhpc_webui.application as main
from fastapi import HTTPException

from openhpc_webui.services.slurm_manager import SlurmManager


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

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_account_tres_limit_reads_pipe_format(self, run):
        run.return_value = Mock(stdout="Account||cpu=60000000,gres/gpu=120\n")
        self.assertEqual(
            self.manager.get_account_tres_minutes("Account"),
            {"cpu": 60000000, "gres/gpu": 120},
        )

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_account_tres_limit_writes_account_association(self, run):
        self.assertTrue(self.manager.set_account_tres_minutes("Account", 600, 120, "annual grant"))
        self.assertEqual(run.call_args.args[0], [
            "sacctmgr", "-i", "modify", "account", "name=Account", "set",
            "GrpTRESMins=cpu=600,gres/gpu=120", "Comment=annual grant",
        ])

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

    def test_grant_negative_hours_deducts_without_crossing_usage(self):
        self.manager.get_user_default_account = Mock(return_value="dawn")
        self.manager.get_association_tres_minutes = Mock(
            side_effect=[
                {"cpu": 600, "gres/gpu": None},
                {"cpu": 500, "gres/gpu": None},
            ]
        )
        self.manager.get_user_tres_usage_minutes = Mock(
            return_value={"cpu": 500, "gres/gpu": 0}
        )
        self.manager.set_association_tres_minutes = Mock(return_value=True)

        result = self.manager.grant_user_tres_hours("dawn", cpu_hours=-2)

        self.manager.set_association_tres_minutes.assert_called_once_with(
            username="dawn", account="dawn", cpu_minutes=500, gpu_minutes=None
        )
        self.assertEqual(result["cpu_granted_minutes"], -120)
        self.assertEqual(result["remaining_cpu_minutes"], 0)

    def test_grant_rejects_invalid_slurm_names_before_commands(self):
        with patch("openhpc_webui.services.slurm_manager.subprocess.run") as run:
            result = self.manager.grant_user_tres_hours(
                "dawn account=root", cpu_hours=1
            )

        self.assertIsNone(result)
        run.assert_not_called()

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_reads_zero_and_unlimited_tres_limits_distinctly(self, run):
        run.return_value = Mock(
            stdout="dawn|dawn||cpu=120,gres/gpu=0\n"
        )

        limits = self.manager.get_association_tres_minutes("dawn", "dawn")

        self.assertEqual(limits, {"cpu": 120, "gres/gpu": 0})

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
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

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_limit_write_persists_comment_on_association(self, run):
        run.return_value = Mock(stdout="updated\n")

        success = self.manager.set_association_tres_minutes(
            "alice",
            "research",
            cpu_minutes=600,
            comment="project P-2026-08 allocation",
        )

        self.assertTrue(success)
        modify_args = run.call_args.args[0]
        self.assertIn("GrpTRESMins=cpu=600", modify_args)
        self.assertIn("Comment=project P-2026-08 allocation", modify_args)

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_usage_minutes_uses_controller_enforcement_values(self, run):
        run.return_value = Mock(
            stdout="""
ClusterName=cluster Account=dawn UserName=dawn(1000) Partition= Priority=0 ID=17
    GrpTRESMins=cpu=8488(1529),mem=N(0),gres/gpu=0(0)
ClusterName=cluster Account=dawn UserName= Partition=CPU Priority=0 ID=16
    GrpTRESMins=cpu=5(0),mem=N(0),gres/gpu=N(0)
"""
        )

        usage = self.manager.get_user_tres_usage_minutes("dawn", "dawn")

        self.assertEqual(usage, {"cpu": 1529, "gres/gpu": 0})
        self.assertEqual(
            run.call_args.args[0],
            ["scontrol", "show", "assoc_mgr", "flags=assoc", "users=dawn"],
        )

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_usage_minutes_selects_partition_association(self, run):
        run.return_value = Mock(
            stdout="""
ClusterName=cluster Account=research UserName=alice(1001) Partition= Priority=0 ID=18
    GrpTRESMins=cpu=600(200),gres/gpu=60(10)
ClusterName=cluster Account=research UserName=alice(1001) Partition=GPU Priority=0 ID=19
    GrpTRESMins=cpu=300(125),gres/gpu=30(7)
"""
        )

        usage = self.manager.get_user_tres_usage_minutes(
            "alice", "research", partition="GPU"
        )

        self.assertEqual(usage, {"cpu": 125, "gres/gpu": 7})

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_lists_user_tres_limits_preferring_global_association(self, run):
        run.return_value = Mock(
            stdout="""
ClusterName=cluster Account=dawn UserName=dawn(1000) Partition=GPU Priority=0 ID=17
    GrpTRESMins=cpu=600(250),gres/gpu=0(0)
ClusterName=cluster Account=dawn UserName=dawn(1000) Partition= Priority=0 ID=18
    GrpTRESMins=cpu=1200(300),gres/gpu=N(5)
ClusterName=cluster Account=research UserName=alice(1001) Partition= Priority=0 ID=19
    GrpTRESMins=cpu=0(0),gres/gpu=30(7)
"""
        )

        limits = self.manager.get_users_tres_limits()

        self.assertEqual(
            limits["dawn"],
            {
                "cpu_minutes": 1200,
                "gpu_minutes": None,
                "cpu_used_minutes": 300,
                "gpu_used_minutes": 5,
                "cpu_remaining_minutes": 900,
                "gpu_remaining_minutes": None,
            },
        )
        self.assertEqual(limits["alice"]["cpu_remaining_minutes"], 0)
        self.assertEqual(limits["alice"]["gpu_remaining_minutes"], 23)
        self.assertEqual(
            run.call_args.args[0],
            ["scontrol", "show", "assoc_mgr", "flags=assoc"],
        )

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_lists_account_tres_usage_preferring_global_association(self, run):
        run.return_value = Mock(
            stdout="""
ClusterName=cluster Account=research UserName= Partition=GPU Priority=0 ID=20
    GrpTRESMins=cpu=600(250),gres/gpu=60(10)
ClusterName=cluster Account=research UserName= Partition= Priority=0 ID=21
    GrpTRESMins=cpu=1200(300),gres/gpu=N(5)
ClusterName=cluster Account=research UserName=alice(1001) Partition= Priority=0 ID=22
    GrpTRESMins=cpu=300(125),gres/gpu=30(7)
"""
        )

        usage = self.manager.get_accounts_tres_usage_minutes()

        self.assertEqual(
            usage["research"],
            {"cpu_used_minutes": 300, "gpu_used_minutes": 5},
        )
        self.assertEqual(
            run.call_args.args[0],
            ["scontrol", "show", "assoc_mgr", "flags=assoc"],
        )

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_absolute_limit_write_rejects_invalid_names(self, run):
        success = self.manager.set_association_tres_minutes(
            "dawn where account=root", "dawn", cpu_minutes=60
        )

        self.assertFalse(success)
        run.assert_not_called()


class SlurmCreditApiTests(unittest.TestCase):
    def test_accounts_endpoint_includes_remaining_cpu_minutes(self):
        with patch.object(
            main.slurm_mgr, "list_accounts", return_value=[{"name": "research"}]
        ), patch.object(
            main.slurm_mgr,
            "get_account_tres_minutes",
            return_value={"cpu": 1200, "gres/gpu": 60},
        ), patch.object(
            main.slurm_mgr,
            "get_accounts_tres_usage_minutes",
            return_value={
                "research": {"cpu_used_minutes": 300, "gpu_used_minutes": 5}
            },
        ):
            result = asyncio.run(
                main.get_accounts({"username": "admin", "is_admin": True})
            )

        account = result["accounts"][0]
        self.assertEqual(account["cpu_used_minutes"], 300)
        self.assertEqual(account["cpu_remaining_minutes"], 900)
        self.assertEqual(account["gpu_used_minutes"], 5)
        self.assertEqual(account["gpu_remaining_minutes"], 55)

    def test_users_endpoint_includes_tres_usage_and_remaining(self):
        tres_values = {
            "dawn": {
                "cpu_minutes": 8488,
                "gpu_minutes": 0,
                "cpu_used_minutes": 1529,
                "gpu_used_minutes": 0,
                "cpu_remaining_minutes": 6959,
                "gpu_remaining_minutes": 0,
            }
        }
        with patch.object(
            main.ldap_mgr, "list_users", return_value=[{"username": "dawn"}]
        ), patch.object(main.admin_mgr, "get_admin_list", return_value=[]), patch.object(
            main.slurm_mgr, "get_users_tres_limits", return_value=tres_values
        ), patch.object(main, "quota_mgr", None):
            result = asyncio.run(
                main.get_users({"username": "admin", "is_admin": True})
            )

        user = result["users"][0]
        self.assertTrue(user["has_tres_association"])
        self.assertEqual(user["cpu_used_minutes"], 1529)
        self.assertEqual(user["cpu_remaining_minutes"], 6959)
        self.assertEqual(user["gpu_remaining_minutes"], 0)

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
        ) as grant, patch.object(
            main,
            "_timestamp_credit_comment",
            return_value="[2026-08-15 14:30:00] test allocation",
        ):
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
            comment="[2026-08-15 14:30:00] test allocation",
        )
        self.assertEqual(result["remaining_gpu_hours"], 1.5)
        self.assertEqual(
            result["comment"], "[2026-08-15 14:30:00] test allocation"
        )

    def test_credit_endpoint_rejects_all_zero_amounts(self):
        payload = main.UserCreditRequest(cpu_hours=0, gpu_hours=0)

        with self.assertRaises(HTTPException) as context:
            asyncio.run(
                main.allocate_user_credits(
                    "dawn", payload, {"username": "admin", "is_admin": True}
                )
            )

        self.assertEqual(context.exception.status_code, 400)

    def test_credit_endpoint_accepts_negative_deduction(self):
        payload = main.UserCreditRequest(account="dawn", cpu_hours=-1)
        grant_result = {
            "account": "dawn",
            "cpu_granted_minutes": -60,
            "gpu_granted_minutes": 0,
            "cpu_limit_minutes": 540,
            "gpu_limit_minutes": None,
            "remaining_cpu_minutes": 0,
            "remaining_gpu_minutes": 0,
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
            username="dawn", account="dawn", cpu_hours=-1, gpu_hours=None
        )
        self.assertEqual(result["cpu_granted_hours"], -1.0)


class SlurmCreditTemplateTests(unittest.TestCase):
    def test_user_credit_form_supports_cpu_and_gpu_hours(self):
        template = (
            Path(__file__).parents[1] / "templates/users.html"
        ).read_text(encoding="utf-8")
        styles = (
            Path(__file__).parents[1] / "static/compat.css"
        ).read_text(encoding="utf-8")

        self.assertIn('name="cpu_hours"', template)
        self.assertIn('name="gpu_hours"', template)
        self.assertIn('name="comment"', template)
        self.assertIn('maxlength="478"', template)
        self.assertIn('class="credit-comment-textarea"', template)
        self.assertIn('rows="5"', template)
        self.assertIn('wrap="soft"', template)
        self.assertIn('class="credit-comment-hint"', template)
        self.assertIn(".credit-comment-textarea", styles)
        self.assertIn("min-height: 132px", styles)
        self.assertIn("resize: vertical", styles)
        self.assertIn("核时/卡时拨付", template)
        self.assertIn("核时限额 (h)", template)
        self.assertIn("卡时限额 (h)", template)
        self.assertIn("user.cpu_minutes", template)
        self.assertIn("user.gpu_minutes", template)
        self.assertIn("user.cpu_used_minutes", template)
        self.assertIn("user.gpu_used_minutes", template)
        self.assertIn("user.cpu_remaining_minutes", template)
        self.assertIn("user.gpu_remaining_minutes", template)
        self.assertIn("核时已用 (h)", template)
        self.assertIn("核时剩余 (h)", template)
        self.assertIn("卡时已用 (h)", template)
        self.assertIn("卡时剩余 (h)", template)
        self.assertIn('class="users-table-scroll"', template)
        self.assertIn("#usersTable thead th", template)
        self.assertIn("position: sticky", template)
        self.assertIn("table-layout: fixed", template)
        self.assertIn('min="-1000000"', template)
        self.assertIn("负数表示扣除", template)

    def test_association_credit_form_submits_incremental_hours(self):
        template = (
            Path(__file__).parents[1] / "templates/cluster_users.html"
        ).read_text(encoding="utf-8")

        self.assertIn('class="credit-comment-textarea"', template)
        self.assertIn('rows="5"', template)
        self.assertIn('wrap="soft"', template)
        self.assertIn('class="credit-comment-hint"', template)

        self.assertIn('name="cpu_hours"', template)
        self.assertIn('name="gpu_hours"', template)
        self.assertIn('name="partition"', template)
        self.assertIn("partition: partition || null", template)
        self.assertIn("grantUserCredits", template)

    def test_account_table_shows_remaining_cpu_hours(self):
        template = (
            Path(__file__).parents[1] / "templates/accounts.html"
        ).read_text(encoding="utf-8")

        self.assertIn("剩余核时", template)
        self.assertIn("account.cpu_remaining_minutes", template)
        self.assertIn("剩余卡时", template)
        self.assertIn("account.gpu_remaining_minutes", template)


if __name__ == "__main__":
    unittest.main()
