import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key-0123456789abcdef')

from fastapi.testclient import TestClient
import openhpc_webui.application as main
from openhpc_webui.services.slurm_manager import SlurmManager


PROJECT_ROOT = Path(__file__).parents[1]


class SlurmUserPartitionFrontendTests(unittest.TestCase):
    def test_partition_column_and_editor_live_on_slurm_user_page(self):
        slurm_page = (PROJECT_ROOT / 'templates/slurm_users.html').read_text(encoding='utf-8')
        ldap_page = (PROJECT_ROOT / 'templates/users.html').read_text(encoding='utf-8')

        self.assertIn('<th>允许的分区</th>', slurm_page)
        self.assertIn('id="slurmPartitionModal"', slurm_page)
        self.assertIn('id="slurmPartitionAccount"', slurm_page)
        self.assertIn('id="slurmPartitionChoice"', slurm_page)
        self.assertNotIn('允许的 Partition', ldap_page)



@patch.dict(os.environ, {'SLURM_CLUSTER_NAME': 'cluster'})
class SlurmUserManagerTests(unittest.TestCase):
    @patch('openhpc_webui.services.slurm_manager.subprocess.run')
    def test_groups_all_accounts_and_partitions_and_excludes_other_clusters(self, run):
        run.return_value = Mock(stdout=(
            'alice|research|None|cluster|research||\n'
            'alice|research|None|cluster|research|gpu|\n'
            'alice|research|None|cluster|other||\n'
            'bob|other|Operator|elsewhere|other||\n'
            'orphan||None||||\n'
        ))
        users = SlurmManager().list_slurm_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]['accounts'], ['other', 'research'])
        self.assertEqual(len(users[0]['associations']), 3)
        self.assertEqual(users[0]['default_account'], 'research')
        args = run.call_args.args[0]
        self.assertIn('WithAssoc', args)
        self.assertIn('cluster=cluster', args)
        self.assertFalse(any(arg.startswith('account=') for arg in args))

    @patch('openhpc_webui.services.slurm_manager.subprocess.run')
    def test_failures_are_not_empty_lists(self, run):
        for failure in [FileNotFoundError(), subprocess.TimeoutExpired('sacctmgr', 30), subprocess.CalledProcessError(1, 'sacctmgr')]:
            run.side_effect = failure
            with self.assertRaises(RuntimeError):
                SlurmManager().list_slurm_users()
        run.side_effect = None
        run.return_value = Mock(stdout='malformed')
        with self.assertRaises(RuntimeError):
            SlurmManager().list_slurm_users()

    @patch('openhpc_webui.services.slurm_manager.subprocess.run')
    def test_missing_cluster_and_invalid_names_never_run_commands(self, run):
        manager = SlurmManager()
        with patch.dict(os.environ, {'SLURM_CLUSTER_NAME': ''}):
            with self.assertRaises(RuntimeError):
                manager.list_slurm_users()
        for method, args in [(manager.create_slurm_user, ('alice,bob', 'research')), (manager.update_slurm_user, ('alice', 'a,b')), (manager.delete_slurm_user, ('alice cluster=other',))]:
            with self.assertRaises(ValueError):
                method(*args)
        run.assert_not_called()

    @patch.object(SlurmManager, 'list_slurm_users')
    @patch('openhpc_webui.services.slurm_manager.subprocess.run')
    def test_create_and_duplicate_protection(self, run, listing):
        manager = SlurmManager()
        listing.return_value = []
        run.return_value = Mock(stdout='Added')
        manager.create_slurm_user('alice', 'research')
        self.assertIn('cluster=cluster', run.call_args.args[0])
        self.assertIn('defaultaccount=research', run.call_args.args[0])
        listing.return_value = [{'username': 'alice'}]
        run.reset_mock()
        with self.assertRaises(FileExistsError):
            manager.create_slurm_user('alice', 'research')
        run.assert_not_called()

    @patch.object(SlurmManager, 'list_slurm_users')
    @patch('openhpc_webui.services.slurm_manager.subprocess.run')
    def test_update_requires_associated_account_and_delete_is_cluster_scoped(self, run, listing):
        manager = SlurmManager()
        listing.return_value = [{'username': 'alice', 'accounts': ['research']}]
        run.return_value = Mock(stdout='Updated')
        with self.assertRaises(ValueError):
            manager.update_slurm_user('alice', 'unrelated')
        run.assert_not_called()
        manager.update_slurm_user('alice', 'research')
        self.assertIn('cluster=cluster', run.call_args.args[0])
        self.assertIn('defaultaccount=research', run.call_args.args[0])
        manager.delete_slurm_user('alice')
        self.assertEqual(run.call_args.args[0], ['sacctmgr', '-i', 'delete', 'user', 'where', 'name=alice', 'cluster=cluster'])
        listing.return_value = []
        with self.assertRaises(LookupError):
            manager.delete_slurm_user('alice')


class SlurmUserApiTests(unittest.TestCase):
    def setUp(self):
        self.app = main.create_app()
        self.app.dependency_overrides[main.get_current_user] = lambda: {'username': 'admin', 'is_admin': True}
        self.client = TestClient(self.app)

    def test_page_and_crud(self):
        response = self.client.get('/slurm-users')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Slurm 用户管理', response.text)
        with patch.object(main.slurm_mgr, 'list_slurm_users', return_value=[{'username': 'alice'}]):
            self.assertEqual(self.client.get('/api/slurm/users').json()['count'], 1)
        for method, path, payload, operation, status in [
            ('post', '/api/slurm/users', {'username': 'alice', 'account': 'research'}, 'create_slurm_user', 201),
            ('put', '/api/slurm/users/alice', {'default_account': 'research'}, 'update_slurm_user', 200),
            ('delete', '/api/slurm/users/alice', None, 'delete_slurm_user', 200),
        ]:
            with patch.object(main.slurm_mgr, operation) as mock:
                response = self.client.request(method, path, json=payload)
                self.assertEqual(response.status_code, status, response.text)
                mock.assert_called_once()

    def test_non_admin_cannot_read_or_mutate(self):
        self.app.dependency_overrides[main.get_current_user] = lambda: {'username': 'alice', 'is_admin': False}
        for method, path, payload in [('get', '/api/slurm/users', None), ('post', '/api/slurm/users', {'username': 'alice', 'account': 'research'}), ('put', '/api/slurm/users/alice', {'default_account': 'research'}), ('delete', '/api/slurm/users/alice', None)]:
            self.assertEqual(self.client.request(method, path, json=payload).status_code, 403)
        self.assertEqual(self.client.get('/slurm-users', follow_redirects=False).status_code, 302)

    def test_validation_and_service_errors(self):
        self.assertEqual(self.client.post('/api/slurm/users', json={'username': '', 'account': 'research'}).status_code, 422)
        for error, status in [(ValueError('invalid'), 400), (LookupError('missing'), 404), (FileExistsError('duplicate'), 409), (RuntimeError('unavailable'), 503)]:
            with patch.object(main.slurm_mgr, 'create_slurm_user', side_effect=error):
                response = self.client.post('/api/slurm/users', json={'username': 'alice', 'account': 'research'})
                self.assertEqual(response.status_code, status)
        with patch.object(main.slurm_mgr, 'list_slurm_users', side_effect=RuntimeError('unavailable')):
            self.assertEqual(self.client.get('/api/slurm/users').status_code, 503)


if __name__ == '__main__':
    unittest.main()
