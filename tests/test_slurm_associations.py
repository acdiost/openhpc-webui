import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError
from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from openhpc_webui.schemas import AssocUpdate
import openhpc_webui.application as main
from openhpc_webui.services.slurm_manager import SlurmManager


PROJECT_ROOT = Path(__file__).parents[1]


class SlurmAssociationUpdateTests(unittest.TestCase):
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
