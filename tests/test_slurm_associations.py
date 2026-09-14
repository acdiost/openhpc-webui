import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError
from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from openhpc_webui.schemas import AssocCreate, AssocUpdate
import openhpc_webui.application as main
from openhpc_webui.services.slurm_manager import SlurmManager


PROJECT_ROOT = Path(__file__).parents[1]


@patch.dict(os.environ, {"SLURM_CLUSTER_NAME": "cluster"})
class SlurmAssociationUpdateTests(unittest.TestCase):
    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_account_crud_is_scoped_to_configured_cluster(self, run):
        run.return_value = Mock(stdout="updated")
        manager = SlurmManager()

        self.assertTrue(manager.create_account("research"))
        self.assertIn("cluster=cluster", run.call_args.args[0])

        self.assertTrue(manager.update_account("research", description="Research"))
        self.assertIn("cluster=cluster", run.call_args.args[0])

        self.assertTrue(manager.delete_account("research"))
        self.assertIn("cluster=cluster", run.call_args.args[0])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_ldap_user_sync_is_scoped_to_configured_cluster(self, run):
        run.return_value = Mock(stdout="updated")
        manager = SlurmManager()

        self.assertTrue(manager.add_user_account("alice", "research"))
        self.assertEqual(
            run.call_args.args[0],
            [
                "sacctmgr",
                "-i",
                "add",
                "user",
                "name=alice",
                "cluster=cluster",
                "account=research",
            ],
        )

        self.assertTrue(manager.remove_user_account("alice"))
        self.assertEqual(
            run.call_args.args[0],
            [
                "sacctmgr",
                "-i",
                "delete",
                "user",
                "name=alice",
                "cluster=cluster",
            ],
        )

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_list_associations_is_scoped_to_configured_cluster(self, run):
        run.return_value = Mock(stdout='{"associations": []}')

        with patch.dict(os.environ, {"SLURM_CLUSTER_NAME": "production"}):
            associations = SlurmManager().list_associations("research")

        self.assertEqual(associations, [])
        self.assertIn("cluster=production", run.call_args.args[0])

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_list_associations_reports_effective_partition_scope(self, run):
        run.return_value = Mock(
            stdout=(
                '{"associations": ['
                '{"cluster":"cluster","account":"root","user":"dawn",'
                '"partition":""},'
                '{"cluster":"cluster","account":"root","user":"dawn",'
                '"partition":"G5"},'
                '{"cluster":"cluster","account":"root","user":"alice",'
                '"partition":"G4"}'
                "]}"
            )
        )

        associations = SlurmManager().list_associations("root")

        by_identity = {
            (item["user"], item["partition"]): item for item in associations
        }
        self.assertEqual(
            by_identity[("dawn", "")]["partition_access_status"], "global"
        )
        self.assertEqual(
            by_identity[("dawn", "G5")]["partition_access_status"],
            "overridden_by_global",
        )
        self.assertEqual(
            by_identity[("alice", "G4")]["partition_access_status"],
            "restricted",
        )

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_invalid_cluster_name_blocks_association_mutation(self, run):
        with patch.dict(os.environ, {"SLURM_CLUSTER_NAME": "bad cluster"}):
            success = SlurmManager().delete_association(
                username="dawn", account="dawn", partition=""
            )

        self.assertFalse(success)
        run.assert_not_called()

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_missing_cluster_name_blocks_account_mutation(self, run):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SLURM_CLUSTER_NAME", None)
            success = SlurmManager().delete_account("research")

        self.assertFalse(success)
        run.assert_not_called()

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_list_accounts_only_returns_accounts_associated_with_cluster(self, run):
        run.side_effect = [
            Mock(stdout="research\nroot\n"),
            Mock(
                stdout=(
                    '{"accounts": ['
                    '{"name": "research", "description": "local"},'
                    '{"name": "other", "description": "remote"},'
                    '{"name": "root", "description": "root"}'
                    "]}"
                )
            ),
        ]

        accounts = SlurmManager().list_accounts()

        self.assertEqual([item["name"] for item in accounts], ["research", "root"])
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "sacctmgr",
                "show",
                "assoc",
                "where",
                "cluster=cluster",
                "format=Account",
                "-n",
                "-P",
            ],
        )

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_global_association_partition_is_a_selector_not_a_change(self, run):
        run.return_value = Mock(stdout="updated")

        success = SlurmManager().update_association(
            username="dawn",
            account="dawn",
            partition="",
            qos="normal",
        )

        self.assertTrue(success)
        self.assertEqual(
            run.call_args.args[0],
            [
                "sacctmgr",
                "-i",
                "modify",
                "user",
                "name=dawn",
                "cluster=cluster",
                "account=dawn",
                'partition=""',
                "set",
                "Qos=normal",
            ],
        )

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_empty_default_qos_uses_slurm_clear_value(self, run):
        run.return_value = Mock(stdout="updated")

        success = SlurmManager().update_association(
            username="alice",
            account="research",
            partition="gpu",
            default_qos="",
        )

        self.assertTrue(success)
        self.assertEqual(
            run.call_args.args[0],
            [
                "sacctmgr",
                "-i",
                "modify",
                "user",
                "name=alice",
                "cluster=cluster",
                "account=research",
                "partition=gpu",
                "set",
                "DefaultQOS=-1",
            ],
        )

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_empty_qos_list_uses_slurm_inheritance_value(self, run):
        run.return_value = Mock(stdout="updated")

        success = SlurmManager().update_association(
            username="alice",
            account="research",
            partition="gpu",
            qos="",
        )

        self.assertTrue(success)
        self.assertEqual(run.call_args.args[0][-1], "Qos=''")

    @patch("openhpc_webui.services.slurm_manager.subprocess.run")
    def test_delete_global_association_has_cluster_and_empty_partition_scope(self, run):
        run.return_value = Mock(stdout="deleted")

        success = SlurmManager().delete_association(
            username="dawn", account="dawn", partition=""
        )

        self.assertTrue(success)
        self.assertEqual(
            run.call_args.args[0],
            [
                "sacctmgr",
                "-i",
                "delete",
                "user",
                "name=dawn",
                "cluster=cluster",
                "account=dawn",
                'partition=""',
            ],
        )

    def test_update_payload_requires_partition_selector(self):
        with self.assertRaises(ValidationError):
            AssocUpdate(qos="normal")

    def test_edit_form_preserves_partition_as_immutable_identity(self):
        template = (PROJECT_ROOT / "templates/cluster_users.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="edit_assoc_partition" name="partition"', template)
        self.assertIn('id="edit_assoc_partition_display"', template)
        self.assertIn("showEditAssocModal('${assoc.account}', '${assoc.user}', '${assoc.partition || \"\"}')", template)
        self.assertIn("a.partition || \"\"", template)

    def test_create_form_explains_and_selects_partition_scope(self):
        template = (PROJECT_ROOT / "templates/cluster_users.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('<select id="create_assoc_partition"', template)
        self.assertIn("全局（不限制分区）", template)
        self.assertIn("限制未生效（存在全局关联）", template)
        self.assertIn("删除全局关联可能取消该关联下正在运行或排队的作业", template)

    def test_association_form_explains_and_links_qos_fields(self):
        template = (PROJECT_ROOT / "templates/cluster_users.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("允许使用的 QoS（可多选）", template)
        self.assertIn("用户通过 --qos 选择", template)
        self.assertIn("默认 QoS（未指定 --qos 时）", template)
        self.assertIn("继承账户默认 QoS", template)
        self.assertIn("syncDefaultQosOptions", template)
        self.assertIn('addEventListener("change", () =>', template)

    def test_create_api_rejects_default_qos_outside_explicit_allowlist(self):
        payload = AssocCreate(
            username="dawn",
            account="root",
            partition="G5",
            qos="normal",
            default_qos="qos_test",
        )

        with patch.object(main.slurm_mgr, "create_association") as create:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.create_association(
                        payload, {"username": "admin", "is_admin": True}
                    )
                )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("默认 QoS 必须包含在允许使用的 QoS 中", context.exception.detail)
        create.assert_not_called()

    def test_create_api_accepts_default_qos_in_explicit_allowlist(self):
        payload = AssocCreate(
            username="dawn",
            account="root",
            partition="G5",
            qos="normal,qos_test",
            default_qos="qos_test",
        )

        with patch.object(
            main.slurm_mgr, "create_association", return_value=True
        ) as create:
            result = asyncio.run(
                main.create_association(
                    payload, {"username": "admin", "is_admin": True}
                )
            )

        self.assertIn("创建成功", result["message"])
        create.assert_called_once_with(
            username="dawn",
            account="root",
            partition="G5",
            qos="normal,qos_test",
            default_qos="qos_test",
        )

    def test_update_api_rejects_default_qos_outside_explicit_allowlist(self):
        payload = AssocUpdate(
            partition="G5", qos="normal", default_qos="qos_test"
        )

        with patch.object(main.slurm_mgr, "update_association") as update:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.update_association(
                        "root",
                        "dawn",
                        payload,
                        {"username": "admin", "is_admin": True},
                    )
                )

        self.assertEqual(context.exception.status_code, 400)
        update.assert_not_called()

    def test_update_api_rejects_invalid_qos_name(self):
        payload = AssocUpdate(partition="", qos="normal,bad qos")

        with patch.object(main.slurm_mgr, "update_association") as update:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    main.update_association(
                        "dawn",
                        "dawn",
                        payload,
                        {"username": "admin", "is_admin": True},
                    )
                )

        self.assertEqual(context.exception.status_code, 400)
        update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
