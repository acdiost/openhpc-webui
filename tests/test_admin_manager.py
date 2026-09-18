import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from openhpc_webui.services import admin_manager


class AdminManagerPersistenceTests(unittest.TestCase):
    def test_failed_persistence_does_not_change_runtime_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("ADMIN_USERS=debug\nOTHER=value\n", encoding="utf-8")

            with patch.dict(
                os.environ, {"ADMIN_USERS": "debug"}, clear=False
            ), patch.object(
                admin_manager, "_find_env_file", return_value=str(env_path)
            ), patch(
                "openhpc_webui.services.admin_manager.os.replace",
                side_effect=OSError("read only"),
            ):
                self.assertFalse(admin_manager.add_admin("alice"))
                self.assertEqual(os.environ["ADMIN_USERS"], "debug")
                self.assertEqual(
                    env_path.read_text(encoding="utf-8"),
                    "ADMIN_USERS=debug\nOTHER=value\n",
                )

    def test_runtime_permissions_change_only_after_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("ADMIN_USERS=debug\nOTHER=value\n", encoding="utf-8")
            observed_runtime_values = []
            real_replace = os.replace

            def observe_replace(source, destination):
                observed_runtime_values.append(os.environ["ADMIN_USERS"])
                real_replace(source, destination)

            with patch.dict(
                os.environ, {"ADMIN_USERS": "debug"}, clear=False
            ), patch.object(
                admin_manager, "_find_env_file", return_value=str(env_path)
            ), patch(
                "openhpc_webui.services.admin_manager.os.replace",
                side_effect=observe_replace,
            ):
                self.assertTrue(admin_manager.add_admin("alice"))
                self.assertEqual(os.environ["ADMIN_USERS"], "debug,alice")

            self.assertEqual(observed_runtime_values, ["debug"])
            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "ADMIN_USERS=debug,alice\nOTHER=value\n",
            )

    def test_concurrent_admin_changes_are_serialized(self):
        active_writes = 0
        maximum_active_writes = 0
        counter_lock = threading.Lock()
        start = threading.Barrier(3)

        def slow_write(_admins):
            nonlocal active_writes, maximum_active_writes
            with counter_lock:
                active_writes += 1
                maximum_active_writes = max(maximum_active_writes, active_writes)
            time.sleep(0.03)
            with counter_lock:
                active_writes -= 1
            return True

        def grant(username):
            start.wait()
            admin_manager.add_admin(username)

        with patch.dict(
            os.environ, {"ADMIN_USERS": "debug"}, clear=False
        ), patch.object(admin_manager, "_write_admin_list", side_effect=slow_write):
            first = threading.Thread(target=grant, args=("alice",))
            second = threading.Thread(target=grant, args=("bob",))
            first.start()
            second.start()
            start.wait()
            first.join(timeout=1)
            second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(maximum_active_writes, 1)


if __name__ == "__main__":
    unittest.main()
