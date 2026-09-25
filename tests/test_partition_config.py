import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from openhpc_webui.services.partition_config import PartitionConfigManager


class PartitionConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'partition.conf'
        self.manager = PartitionConfigManager(str(self.path))

    def test_update_and_delete_restore_exact_file_and_mode_on_reload_failure(self):
        original = b'# untouched\r\nPartitionName=compute Nodes=n1 Default=NO\r\n'
        for action in ('update', 'delete'):
            with self.subTest(action=action):
                self.path.write_bytes(original)
                self.path.chmod(0o640)
                attempted = []

                def reject_reload():
                    attempted.append(self.path.read_bytes())
                    return False

                self.manager._reconfigure_slurm = reject_reload
                if action == 'update':
                    result = self.manager.update_partition('compute', nodes='n2')
                else:
                    result = self.manager.delete_partition('compute')
                self.assertFalse(result)
                self.assertEqual(len(attempted), 1)
                self.assertNotEqual(attempted[0], original)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_add_failure_restores_existing_file_or_removes_new_file(self):
        self.manager._reconfigure_slurm = Mock(return_value=False)
        self.path.write_bytes(b'# original\r\n')
        self.path.chmod(0o600)
        self.assertFalse(self.manager.add_partition('compute', 'n1'))
        self.assertEqual(self.path.read_bytes(), b'# original\r\n')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

        self.path.unlink()
        self.assertFalse(self.manager.add_partition('compute', 'n1'))
        self.assertFalse(self.path.exists())

    def test_successful_update_and_delete(self):
        self.path.write_text('PartitionName=compute Nodes=n1 Default=NO\n')
        self.manager._reconfigure_slurm = Mock(return_value=True)
        self.assertTrue(self.manager.update_partition('compute', nodes='n2'))
        self.assertIn('Nodes=n2', self.path.read_text())
        self.assertTrue(self.manager.delete_partition('compute'))
        self.assertEqual(self.path.read_text(), '')


if __name__ == '__main__':
    unittest.main()
