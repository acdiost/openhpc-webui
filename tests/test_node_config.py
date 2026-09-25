import asyncio
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from openhpc_webui import application as app
from openhpc_webui.schemas import NodeCreate, NodeUpdate
from openhpc_webui.services.node_config import NodeConfigManager


EXAMPLE = (
    'NodeName=g50r061 NodeAddr=172.90.50.6 CPUs=128 Boards=1 '
    'SocketsPerBoard=4 CoresPerSocket=16 ThreadsPerCore=2 RealMemory=500000 '
    'Gres=gpu:rtx_5090:8 Parameters=numa_node_as_socket State=UNKNOWN'
)
PAYLOAD = dict(
    name='g50r061', node_addr='172.90.50.6', cpus=128, boards=1,
    sockets_per_board=4, cores_per_socket=16, threads_per_core=2,
    real_memory=500000, gres='gpu:rtx_5090:8',
    parameters='numa_node_as_socket', state='UNKNOWN',
)


class NodeConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'node.conf'
        self.path.write_text('')
        self.manager = NodeConfigManager(str(self.path))
        self.manager._reconfigure_slurm = Mock(return_value=True)

    def test_example_create_and_read_round_trip(self):
        self.assertTrue(self.manager.add_node(**NodeCreate(**PAYLOAD).model_dump()))
        self.assertEqual(self.path.read_text(), EXAMPLE + '\n')
        node = self.manager.get_node('g50r061')
        self.assertEqual(node, dict(PAYLOAD, raw_line=EXAMPLE))

    def test_partial_update_preserves_then_clears_optional_fields(self):
        self.path.write_text(EXAMPLE + ' Feature=avx512 # keep State=DOWN\n')
        self.assertTrue(self.manager.update_node('g50r061', cpus=64))
        self.assertEqual(self.manager.get_node('g50r061')['state'], 'UNKNOWN')
        self.assertTrue(self.manager.update_node(
            'g50r061', node_addr='172.90.50.7', parameters='', state='DOWN'
        ))
        node = self.manager.get_node('g50r061')
        self.assertEqual(node['node_addr'], '172.90.50.7')
        self.assertEqual(node['state'], 'DOWN')
        self.assertNotIn('parameters', node)
        self.assertIn('Feature=avx512 # keep State=DOWN', self.path.read_text())
        self.assertTrue(self.manager.update_node('g50r061', node_addr='', state=''))
        node = self.manager.get_node('g50r061')
        self.assertNotIn('node_addr', node)
        self.assertNotIn('state', node)

    def test_new_fields_are_inserted_before_comment(self):
        self.path.write_text('NodeName=g50r061 CPUs=128 # State=DOWN\n')
        self.assertTrue(self.manager.update_node(
            'g50r061', node_addr='172.90.50.6', parameters='numa_node_as_socket',
            state='UNKNOWN',
        ))
        self.assertEqual(self.manager.get_node('g50r061')['state'], 'UNKNOWN')
        self.assertIn('State=UNKNOWN # State=DOWN', self.path.read_text())

    def test_update_and_delete_restore_exact_file_and_mode_on_reload_failure(self):
        original = b'# untouched\r\nNodeName=g50r061 CPUs=128\r\n'
        for action in ("update", "delete"):
            with self.subTest(action=action):
                self.path.write_bytes(original)
                self.path.chmod(0o640)
                attempted = []

                def reject_reload():
                    attempted.append(self.path.read_bytes())
                    return False

                self.manager._reconfigure_slurm = reject_reload
                if action == "update":
                    result = self.manager.update_node("g50r061", cpus=64)
                else:
                    result = self.manager.delete_node("g50r061")
                self.assertFalse(result)
                self.assertNotEqual(attempted, [original])
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_add_failure_restores_existing_mode_or_removes_new_file(self):
        self.manager._reconfigure_slurm = Mock(return_value=False)
        self.path.write_bytes(b'# original\r\n')
        self.path.chmod(0o600)
        self.assertFalse(self.manager.add_node('new', 4))
        self.assertEqual(self.path.read_bytes(), b'# original\r\n')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

        self.path.unlink()
        self.assertFalse(self.manager.add_node('new', 4))
        self.assertFalse(self.path.exists())

    def test_models_reject_config_injection(self):
        for model in (NodeCreate, NodeUpdate):
            for field in ('node_addr', 'parameters', 'state'):
                for value in ('x\nNodeName=evil', 'x State=DOWN', 'x#comment', 'x\x00', 'x"'):
                    with self.subTest(model=model, field=field, value=value):
                        with self.assertRaises(ValidationError):
                            model(**{**PAYLOAD, field: value})

    def test_routes_forward_fields_to_manager(self):
        user = {'username': 'admin', 'is_admin': True}
        with patch.object(app.slurm_mgr, 'add_node_config', return_value=True) as add:
            asyncio.run(app.create_node(NodeCreate(**PAYLOAD), user))
            self.assertEqual(add.call_args.kwargs['node_addr'], PAYLOAD['node_addr'])
            self.assertEqual(add.call_args.kwargs['parameters'], PAYLOAD['parameters'])
            self.assertEqual(add.call_args.kwargs['state'], 'UNKNOWN')
        with patch.object(app.slurm_mgr, 'get_node_from_config', return_value=PAYLOAD), patch.object(
            app.slurm_mgr, 'update_node_config', return_value=True
        ) as update:
            asyncio.run(app.update_node_config(
                'g50r061', NodeUpdate(node_addr='', parameters='', state='DOWN'), user
            ))
            self.assertEqual(update.call_args.kwargs['node_addr'], '')
            self.assertEqual(update.call_args.kwargs['parameters'], '')
            self.assertEqual(update.call_args.kwargs['state'], 'DOWN')


if __name__ == '__main__':
    unittest.main()
