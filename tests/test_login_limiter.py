import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from openhpc_webui import application as main
from openhpc_webui.services.auth_manager import AuthenticationServiceError
from openhpc_webui.services.login_limiter import LoginAttemptLimiter


class LoginAttemptLimiterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "login-limiter.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _limiter(self, **kwargs):
        return LoginAttemptLimiter(db_path=self.db_path, **kwargs)

    def test_fifth_consecutive_failure_locks_for_thirty_minutes(self):
        now = [100.0]
        limiter = self._limiter(time_fn=lambda: now[0])

        for _ in range(4):
            self.assertEqual(limiter.record_failure("alice"), 0)
        self.assertEqual(limiter.record_failure("alice"), 1800)
        self.assertEqual(limiter.retry_after("alice"), 1800)

        now[0] += 1800
        self.assertEqual(limiter.retry_after("alice"), 0)

    def test_success_resets_consecutive_failures(self):
        limiter = self._limiter()
        for _ in range(4):
            limiter.record_failure("alice")

        limiter.record_success("alice")

        self.assertEqual(limiter.record_failure("alice"), 0)

    def test_stale_failure_sequence_expires(self):
        now = [100.0]
        limiter = self._limiter(time_fn=lambda: now[0])
        for _ in range(4):
            limiter.record_failure("alice")

        now[0] += 1800

        self.assertEqual(limiter.record_failure("alice"), 0)

    def test_username_variants_share_the_same_limit(self):
        limiter = self._limiter()
        for variant in ("Alice", "ALICE", " alice ", "aLiCe"):
            self.assertEqual(limiter.record_failure(variant, "192.0.2.1"), 0)

        self.assertEqual(limiter.record_failure("alice", "192.0.2.1"), 1800)
        self.assertEqual(limiter.retry_after("ALICE", "192.0.2.1"), 1800)

    def test_distributed_failures_delay_but_do_not_lock_the_account(self):
        limiter = self._limiter(max_failures=5)
        for index in range(5):
            self.assertEqual(
                limiter.record_failure("alice", f"192.0.2.{index}"),
                0,
            )

        self.assertEqual(limiter.retry_after("alice", "198.51.100.10"), 0)
        self.assertEqual(limiter.failure_delay("alice"), 1)

    def test_source_limit_applies_across_different_usernames(self):
        limiter = self._limiter(source_max_failures=3)

        self.assertEqual(limiter.record_failure("alice", "192.0.2.1"), 0)
        self.assertEqual(limiter.record_failure("bob", "192.0.2.1"), 0)
        self.assertEqual(limiter.record_failure("carol", "192.0.2.1"), 1800)

        self.assertEqual(limiter.retry_after("dave", "192.0.2.1"), 1800)
        self.assertEqual(limiter.retry_after("dave", "198.51.100.1"), 0)

    def test_success_does_not_reset_the_source_failure_budget(self):
        limiter = self._limiter(source_max_failures=3)
        limiter.record_failure("alice", "192.0.2.1")
        limiter.record_failure("bob", "192.0.2.1")

        limiter.record_success("bob", "192.0.2.1")

        self.assertEqual(limiter.record_failure("carol", "192.0.2.1"), 1800)

    def test_ipv6_addresses_are_grouped_by_network(self):
        limiter = self._limiter(source_max_failures=2)

        self.assertEqual(limiter.record_failure("alice", "2001:db8::1"), 0)
        self.assertEqual(
            limiter.record_failure("bob", "2001:db8::ffff"),
            1800,
        )

    def test_separate_instances_share_failure_state(self):
        first = self._limiter(max_failures=2)
        second = self._limiter(max_failures=2)

        self.assertEqual(first.record_failure("alice", "192.0.2.1"), 0)
        self.assertEqual(second.record_failure("ALICE", "192.0.2.1"), 1800)
        self.assertEqual(first.retry_after("alice", "192.0.2.1"), 1800)


class LoginLockoutApiTests(unittest.TestCase):
    def setUp(self):
        main.login_limiter.clear()

    def tearDown(self):
        main.login_limiter.clear()

    def test_fifth_failure_locks_and_locked_request_skips_ldap(self):
        client = TestClient(main.app)
        with patch.object(main.auth_mgr, "authenticate_user", return_value=None) as authenticate:
            responses = [
                client.post(
                    "/api/auth/login",
                    json={"username": "lock-test", "password": "wrong"},
                )
                for _ in range(5)
            ]
            locked_response = client.post(
                "/api/auth/login",
                json={"username": "lock-test", "password": "correct"},
            )

        self.assertEqual([response.status_code for response in responses[:4]], [401] * 4)
        self.assertEqual(responses[4].status_code, 429)
        self.assertEqual(responses[4].headers["Retry-After"], "1800")
        self.assertEqual(locked_response.status_code, 429)
        self.assertEqual(authenticate.call_count, 5)

    def test_success_clears_prior_failures(self):
        client = TestClient(main.app)
        success = {"username": "reset-test", "cn": "Reset Test", "shell": "/bin/bash"}
        with patch.object(
            main.auth_mgr,
            "authenticate_user",
            side_effect=[None, None, None, None, success, None],
        ), patch.object(
            main.ldap_mgr, "get_user_auth_state",
            return_value=("/bin/bash", "uuid-reset")
        ), patch.object(main.admin_mgr, "is_admin", return_value=False):
            for _ in range(4):
                self.assertEqual(
                    client.post(
                        "/api/auth/login",
                        json={"username": "reset-test", "password": "wrong"},
                    ).status_code,
                    401,
                )
            self.assertEqual(
                client.post(
                    "/api/auth/login",
                    json={"username": "reset-test", "password": "correct"},
                ).status_code,
                200,
            )
            response = client.post(
                "/api/auth/login",
                json={"username": "reset-test", "password": "wrong-again"},
            )

        self.assertEqual(response.status_code, 401)

    def test_authentication_service_failure_does_not_lock_account(self):
        client = TestClient(main.app)
        with patch.object(
            main.auth_mgr,
            "authenticate_user",
            side_effect=AuthenticationServiceError("LDAP unavailable"),
        ) as authenticate:
            responses = [
                client.post(
                    "/api/auth/login",
                    json={"username": "service-test", "password": "secret"},
                )
                for _ in range(6)
            ]

        self.assertEqual([response.status_code for response in responses], [503] * 6)
        self.assertEqual(authenticate.call_count, 6)
        self.assertEqual(main.login_limiter.retry_after("service-test"), 0)

    def test_login_page_handles_retry_after_countdown(self):
        template = (main.TEMPLATES_DIR / "login.html").read_text(encoding="utf-8")

        self.assertIn('r.headers.get("Retry-After")', template)
        self.assertIn("startLockCountdown", template)
