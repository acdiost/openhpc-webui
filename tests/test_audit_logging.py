import io
import json
import logging
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from openhpc_webui import application as main
from openhpc_webui.audit import JsonFormatter, sanitize


class AuditLoggingTests(unittest.TestCase):
    def test_sanitize_redacts_nested_secrets_and_inline_credentials(self):
        value = sanitize(
            {
                "username": "alice",
                "password": "plain-text",
                "nested": {"access_token": "abc"},
                "error": "bind failed password=hunter2",
                "json_error": 'request contained {"password": "quoted-secret"}',
                "header": "Authorization: Bearer bearer-secret",
                "uri": "ldap://admin:ldap-secret@example.test",
            }
        )

        self.assertEqual(value["username"], "alice")
        self.assertEqual(value["password"], "[REDACTED]")
        self.assertEqual(value["nested"]["access_token"], "[REDACTED]")
        self.assertNotIn("hunter2", value["error"])
        self.assertNotIn("quoted-secret", str(value))
        self.assertNotIn("bearer-secret", str(value))
        self.assertNotIn("ldap-secret", str(value))

    def test_formatter_emits_valid_json_without_secret(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("audit-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        logger.info(
            "operation password=do-not-log",
            extra={"fields": {"event_type": "audit", "new_password": "secret"}},
        )
        event = json.loads(stream.getvalue())

        self.assertEqual(event["event_type"], "audit")
        self.assertEqual(event["new_password"], "[REDACTED]")
        self.assertNotIn("do-not-log", stream.getvalue())
        self.assertIn("timestamp", event)
        self.assertIn("request_id", event)

    def test_login_audit_has_actor_source_before_after_and_no_password(self):
        audit_logger = logging.getLogger("openhpc_webui.audit")
        with patch.object(
            main.auth_mgr,
            "authenticate_user",
            return_value={"username": "alice", "cn": "Alice", "shell": "/bin/bash"},
        ), patch.object(main.admin_mgr, "is_admin", return_value=False), self.assertLogs(
            audit_logger, level="INFO"
        ) as captured:
            response = TestClient(main.app).post(
                "/api/auth/login",
                json={"username": "alice", "password": "top-secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response.headers)
        event = captured.records[-1].fields
        self.assertEqual(event["event_type"], "audit")
        self.assertEqual(event["actor"], "alice")
        self.assertEqual(event["action"], "login")
        self.assertEqual(event["result"], "success")
        self.assertIn("before", event)
        self.assertIn("after", event)
        self.assertNotIn("top-secret", str(event))

    def test_failed_login_redacts_password(self):
        audit_logger = logging.getLogger("openhpc_webui.audit")
        with patch.object(main.auth_mgr, "authenticate_user", return_value=None), self.assertLogs(
            audit_logger, level="WARNING"
        ) as captured:
            response = TestClient(main.app).post(
                "/api/auth/login",
                json={"username": "alice", "password": "wrong-secret"},
            )

        self.assertEqual(response.status_code, 401)
        event = captured.records[-1].fields
        self.assertEqual(event["result"], "failure")
        self.assertEqual(event["after"], {"authenticated": False})
        self.assertNotIn("wrong-secret", str(event))

    def test_admin_change_records_persisted_before_and_after_state(self):
        audit_logger = logging.getLogger("openhpc_webui.audit")
        with patch.object(main, "AUTH_ENABLED", False), patch.object(
            main.admin_mgr, "is_admin", side_effect=[False, True]
        ), patch.object(main.admin_mgr, "add_admin", return_value=True), patch.object(
            main.admin_mgr, "get_admin_list", return_value=["debug", "alice"]
        ), self.assertLogs(audit_logger, level="INFO") as captured:
            response = TestClient(main.app).post(
                "/api/admin", json={"username": "alice"}
            )

        event = captured.records[-1].fields
        self.assertEqual(response.status_code, 200)
        self.assertEqual(event["actor"], "debug")
        self.assertEqual(event["before"], {"username": "alice", "is_admin": False})
        self.assertEqual(event["after"], {"username": "alice", "is_admin": True})
