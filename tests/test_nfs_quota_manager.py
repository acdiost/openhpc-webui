import os
import subprocess
import unittest
from unittest.mock import mock_open, patch

from openhpc_webui.services.nfs_quota_manager import NFSQuotaManager


XFS_NO_WRAP_OUTPUT = """\
Disk quotas for user dawn (uid 10001):
     Filesystem  blocks   quota   limit   grace   files   quota   limit   grace
/dev/mapper/rlm-root      20  1048576 1048576               6       0       0
"""

XFS_WRAPPED_OUTPUT = """\
Disk quotas for user dawn (uid 10001):
     Filesystem  blocks   quota   limit   grace   files   quota   limit   grace
/dev/mapper/rlm-root
                  2048*  1048576 1048576  6days        6       0       0
"""

MOUNTINFO = """\
30 1 8:1 / / rw - ext4 /dev/mapper/rlm-root rw
31 30 8:2 / /home rw - xfs /dev/mapper/rlm-home rw
32 30 8:3 / /data rw - xfs /dev/mapper/rlm-data rw
"""

HOME_OUTPUT = """\
Disk quotas for user dawn (uid 10001):
     Filesystem  blocks   quota   limit   grace   files   quota   limit   grace
/dev/mapper/rlm-root 2048 1048576 1048576 6 0 0
/dev/mapper/rlm-home 5120 2097152 2097152 6 0 0
"""


class NFSQuotaManagerTests(unittest.TestCase):
    def setUp(self):
        mountinfo = patch("builtins.open", mock_open(read_data=MOUNTINFO))
        mountinfo.start()
        self.addCleanup(mountinfo.stop)

    def _manager(self, quota_fs: str = "/") -> NFSQuotaManager:
        with patch.dict(os.environ, {"NFS_QUOTA_FS": quota_fs}):
            return NFSQuotaManager()

    def test_is_disabled_without_configured_filesystem(self):
        with patch.dict(os.environ, {}, clear=False):
            previous = os.environ.pop("NFS_QUOTA_FS", None)
            try:
                manager = NFSQuotaManager()
            finally:
                if previous is not None:
                    os.environ["NFS_QUOTA_FS"] = previous

        with patch("subprocess.run") as run:
            self.assertFalse(manager.is_enabled())
        run.assert_not_called()

    def test_is_enabled_probes_and_caches_quota_state(self):
        manager = self._manager()
        completed = subprocess.CompletedProcess([], 0, XFS_NO_WRAP_OUTPUT, "")

        with patch("subprocess.run", return_value=completed) as run:
            self.assertTrue(manager.is_enabled())
            self.assertTrue(manager.is_enabled())

        run.assert_called_once_with(
            ["quota", "-w", "-v", "-u", "root"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_is_enabled_rejects_successful_probe_on_other_mount(self):
        manager = self._manager("/home/")
        unrelated = subprocess.CompletedProcess([], 0, XFS_NO_WRAP_OUTPUT, "")
        with patch("subprocess.run", return_value=unrelated) as run:
            self.assertFalse(manager.is_enabled())
            self.assertFalse(manager.is_enabled())
        run.assert_called_once()

    def test_is_enabled_rejects_unknown_mount_mapping(self):
        manager = self._manager("/home")
        with patch("builtins.open", side_effect=OSError("mountinfo unavailable")), patch(
            "subprocess.run"
        ) as run:
            self.assertFalse(manager.is_enabled())
        run.assert_not_called()

    def test_is_enabled_rejects_filesystem_without_user_quota(self):
        manager = self._manager("/home")
        completed = subprocess.CompletedProcess(
            [], 1, "", "quota: Mountpoint has no quota enabled."
        )

        with patch("subprocess.run", return_value=completed):
            self.assertFalse(manager.is_enabled())

    def test_get_user_quota_uses_no_wrap_verbose_filtered_output(self):
        manager = self._manager()
        completed = subprocess.CompletedProcess([], 0, XFS_NO_WRAP_OUTPUT, "")

        with patch.object(manager, "is_enabled", return_value=True), patch(
            "subprocess.run", return_value=completed
        ) as run:
            quota = manager.get_user_quota("dawn")

        self.assertEqual(quota, {"used_gb": 0.0, "limit_gb": 1.0})
        run.assert_called_once_with(
            ["quota", "-w", "-v", "-u", "dawn"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

    def test_parser_accepts_wrapped_xfs_device_and_over_limit_marker(self):
        manager = self._manager()

        quota_row = manager._parse_quota_output(XFS_WRAPPED_OUTPUT)

        self.assertEqual(
            quota_row,
            {
                "blocks_used_kb": 2048,
                "blocks_soft_kb": 1048576,
                "blocks_hard_kb": 1048576,
            },
        )

    def test_home_quota_selects_its_backing_device_not_unrelated_root(self):
        manager = self._manager("/home/./")
        root_row = subprocess.CompletedProcess([], 0, XFS_NO_WRAP_OUTPUT, "")
        with patch("subprocess.run", return_value=root_row):
            self.assertFalse(manager.is_enabled())
            self.assertIsNone(manager.get_user_quota("dawn"))

        manager = self._manager("/home/")
        response = subprocess.CompletedProcess([], 0, HOME_OUTPUT, "")
        with patch("subprocess.run", return_value=response):
            self.assertTrue(manager.is_enabled())
            self.assertEqual(
                manager.get_user_quota("dawn"),
                {"used_gb": 0.0, "limit_gb": 2.0},
            )

    def test_user_read_rejects_unrelated_row_after_successful_probe(self):
        manager = self._manager("/home")
        with patch("subprocess.run", side_effect=[
            subprocess.CompletedProcess([], 0, HOME_OUTPUT, ""),
            subprocess.CompletedProcess([], 0, XFS_NO_WRAP_OUTPUT, ""),
        ]):
            self.assertIsNone(manager.get_user_quota("dawn"))

    def test_mountpoint_row_matches_normalized_config_path(self):
        manager = self._manager("/home/../home/")
        row = "Filesystem blocks quota limit\n/home 20 1048576 1048576\n"
        self.assertEqual(manager._parse_quota_output(row)["blocks_hard_kb"], 1048576)
        unrelated = "Filesystem blocks quota limit\n/mnt/home 20 1048576 1048576\n"
        self.assertIsNone(manager._parse_quota_output(unrelated))

    def test_nested_directory_resolves_longest_mountpoint(self):
        manager = self._manager("/home/alice")
        self.assertEqual(
            manager._parse_quota_output(HOME_OUTPUT)["blocks_hard_kb"], 2097152
        )

    def test_missing_configured_mount_row_never_uses_sole_unrelated_row(self):
        manager = self._manager("/home")
        self.assertIsNone(manager._parse_quota_output(XFS_NO_WRAP_OUTPUT))

    def test_quota_command_is_compatible_with_centos_7_quota_tools(self):
        manager = self._manager("/home")

        command = manager._quota_command("dawn")

        self.assertEqual(command, ["quota", "-w", "-v", "-u", "dawn"])
        self.assertFalse(any(arg.startswith("--filesystem") for arg in command))

    def test_get_user_quota_returns_none_on_command_failure(self):
        manager = self._manager()
        error = subprocess.CalledProcessError(
            1,
            ["quota"],
            stderr="quota is not enabled",
        )

        with patch.object(manager, "is_enabled", return_value=True), patch(
            "subprocess.run", side_effect=error
        ):
            self.assertIsNone(manager.get_user_quota("dawn"))

    def test_set_user_quota_converts_gib_to_kib(self):
        manager = self._manager()
        completed = subprocess.CompletedProcess([], 0, "", "")

        with patch.object(manager, "is_enabled", return_value=True), patch(
            "subprocess.run", return_value=completed
        ) as run:
            self.assertTrue(manager.set_user_quota("dawn", 1.5))

        run.assert_called_once_with(
            [
                "setquota",
                "-u",
                "dawn",
                "1572864",
                "1572864",
                "0",
                "0",
                "/",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
