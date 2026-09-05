"""Structured application logging and HTTP audit records."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


AUDIT_LOGGER_NAME = "openhpc_webui.audit"
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "bind_password",
    "cookie",
    "current_password",
    "ldap_default_authtok",
    "new_password",
    "password",
    "private_key",
    "secret",
    "secret_key",
    "session",
    "set-cookie",
    "ssh_private_key",
    "token",
}
_SENSITIVE_KEY_PARTS = ("password", "passwd", "secret", "token", "private_key", "api_key")
_INLINE_SECRET = re.compile(
    r"(?i)(password|passwd|secret|token|authorization|cookie)[\"']?\s*[:=]\s*"
    r"(?:[\"'][^\"']*[\"']|[^\s,;}\]]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_URI_CREDENTIALS = re.compile(r"(?i)(\b(?:ldap|ldaps|https?)://)[^/@\s:]+:[^/@\s]+@")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_MAX_STRING_LENGTH = 2048
_MAX_COLLECTION_ITEMS = 100
_MAX_DEPTH = 8
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

SnapshotResolver = Callable[[Request, Dict[str, Any]], Awaitable[Any]]


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or any(
        part in normalized for part in _SENSITIVE_KEY_PARTS
    )


def sanitize(value: Any, *, key: Optional[str] = None, _depth: int = 0) -> Any:
    """Return a bounded, JSON-safe value with secrets removed."""
    if key is not None and _is_sensitive_key(key):
        return "[REDACTED]"
    if _depth >= _MAX_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        result = {
            str(item_key): sanitize(item, key=str(item_key), _depth=_depth + 1)
            for item_key, item in list(value.items())[:_MAX_COLLECTION_ITEMS]
        }
        if len(value) > _MAX_COLLECTION_ITEMS:
            result["_truncated_items"] = len(value) - _MAX_COLLECTION_ITEMS
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [sanitize(item, _depth=_depth + 1) for item in items[:_MAX_COLLECTION_ITEMS]]
        if len(items) > _MAX_COLLECTION_ITEMS:
            result.append(f"[TRUNCATED {len(items) - _MAX_COLLECTION_ITEMS} ITEMS]")
        return result
    text = str(value)
    text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    text = _URI_CREDENTIALS.sub(r"\1[REDACTED]@", text)
    text = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    if len(text) > _MAX_STRING_LENGTH:
        return text[:_MAX_STRING_LENGTH] + "...[TRUNCATED]"
    return text


class JsonFormatter(logging.Formatter):
    """Emit one valid JSON object per line for collectors and operators."""

    def format(self, record: logging.LogRecord) -> str:
        event: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitize(record.getMessage()),
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            event.update(sanitize(fields))
        if record.exc_info:
            event["exception"] = sanitize(self.formatException(record.exc_info))
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging() -> None:
    """Configure project and Uvicorn output as one-line JSON."""
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    project_logger = logging.getLogger("openhpc_webui")
    project_logger.setLevel(level)
    project_logger.propagate = False

    if not any(getattr(handler, "_openhpc_json", False) for handler in project_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._openhpc_json = True  # type: ignore[attr-defined]
        project_logger.addHandler(handler)

    # Uvicorn configures its handlers before importing the ASGI application.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(logger_name).handlers:
            handler.setFormatter(JsonFormatter())


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"fields": fields})


def structured_print(*values: Any, sep: str = " ", end: str = "\n", **_: Any) -> None:
    """Compatibility sink for legacy service diagnostics, emitted as JSON."""
    rendered = sep.join(str(value) for value in values)
    lowered = rendered.lower()
    if any(marker in lowered for marker in ("失败", "error", "exception")):
        level = logging.ERROR
    elif any(marker in lowered for marker in ("警告", "warning", "不存在", "未找到")):
        level = logging.WARNING
    elif any(marker in lowered for marker in ("found ", "added ")):
        level = logging.DEBUG
    else:
        level = logging.INFO
    log_event(logging.getLogger("openhpc_webui.service"), level, rendered.rstrip(end), category="service")


def log_current_exception(message: str) -> None:
    logging.getLogger("openhpc_webui.service").exception(message)


def _action_for(method: str) -> str:
    return {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}.get(method, "access")


def _specific_action(path: str, method: str) -> str:
    if path == "/api/admin":
        return "grant_admin" if method == "POST" else "update_admin"
    if path.startswith("/api/admin/") and method == "DELETE":
        return "revoke_admin"
    if re.fullmatch(r"/api/slurm/jobs/[^/]+", path) and method == "DELETE":
        return "cancel_job"
    suffix_actions = {
        "/login": "login",
        "/logout": "logout",
        "/change-password": "change_password",
        "/disable": "disable",
        "/enable": "enable",
        "/ssh-key": "reset_ssh_key",
        "/add-member": "add_member",
        "/remove-member": "remove_member",
        "/credit": "allocate_credit",
        "/drain": "drain",
        "/resume": "resume",
        "/state": "change_state",
        "/tres-minutes": "set_tres_minutes",
        "/quota": "set_quota",
    }
    for suffix, action in suffix_actions.items():
        if path.endswith(suffix):
            return action
    return _action_for(method)


def _resource_for(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if parts[:2] == ["api", "auth"]:
        return "authentication"
    return "/".join(parts[1:3]) if len(parts) >= 3 else "/".join(parts)


def _target_for(request: Request, body: Dict[str, Any]) -> Dict[str, Any]:
    route_params = dict(request.path_params)
    identifiers = {
        key: value
        for key, value in body.items()
        if key
        in {
            "name",
            "username",
            "group_name",
            "account",
            "partition",
            "state",
            "path",
            "new_name",
        }
    }
    if request.url.path.startswith("/api/files") and request.query_params.get("path"):
        identifiers["path"] = request.query_params["path"]
    return sanitize({"path": request.url.path, "route_params": route_params, "identifiers": identifiers})


async def _json_body(request: Request) -> Dict[str, Any]:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        return {}
    try:
        value = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class AuditMiddleware(BaseHTTPMiddleware):
    """Write a complete audit record for every state-changing API request."""

    def __init__(self, app: Any, snapshot_resolver: Optional[SnapshotResolver] = None):
        super().__init__(app)
        self.snapshot_resolver = snapshot_resolver
        self.logger = logging.getLogger(AUDIT_LOGGER_NAME)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        supplied_request_id = request.headers.get("x-request-id", "").strip()
        request_id = supplied_request_id if _REQUEST_ID_PATTERN.fullmatch(supplied_request_id) else str(uuid.uuid4())
        token = request_id_var.set(request_id)
        started = time.monotonic()
        response: Optional[Response] = None
        error: Optional[BaseException] = None
        body: Dict[str, Any] = {}
        before: Any = None
        actor_before = "anonymous"

        try:
            if request.method in _MUTATING_METHODS and request.url.path.startswith("/api/"):
                body = await _json_body(request)
                if "session" in request.scope:
                    actor_before = (request.session.get("user") or {}).get("username") or "anonymous"
                if self.snapshot_resolver:
                    try:
                        before = await self.snapshot_resolver(request, body)
                    except Exception:
                        before = {"snapshot": "unavailable"}
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        except BaseException as exc:
            error = exc
            raise
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            status_code = response.status_code if response else 500
            if request.method in _MUTATING_METHODS and request.url.path.startswith("/api/"):
                after: Any = None
                if self.snapshot_resolver:
                    try:
                        after = await self.snapshot_resolver(request, body)
                    except Exception:
                        after = {"snapshot": "unavailable"}
                session_user = (request.session.get("user") or {}) if "session" in request.scope else {}
                actor = (
                    actor_before
                    if actor_before != "anonymous"
                    else session_user.get("username")
                    or getattr(request.state, "audit_actor", None)
                    or body.get("username")
                    or "anonymous"
                )
                result = "success" if status_code < 400 else "failure"
                fields = {
                    "event_type": "audit",
                    "actor": actor,
                    "source_ip": request.client.host if request.client else "unknown",
                    "http_method": request.method,
                    "action": _specific_action(request.url.path, request.method),
                    "resource": _resource_for(request.url.path),
                    "target": _target_for(request, body),
                    "before": before,
                    "after": after if self.snapshot_resolver else sanitize(body),
                    "result": result,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                }
                result_detail = getattr(request.state, "audit_result_detail", None)
                if result_detail:
                    fields["result_detail"] = result_detail
                if error is not None:
                    fields["error_type"] = type(error).__name__
                log_event(
                    self.logger,
                    logging.INFO if status_code < 400 else logging.WARNING,
                    "audit operation completed",
                    **fields,
                )
            else:
                session_user = (request.session.get("user") or {}) if "session" in request.scope else {}
                log_event(
                    logging.getLogger("openhpc_webui.access"),
                    logging.ERROR if status_code >= 500 else logging.INFO,
                    "request completed",
                    event_type="access",
                    actor=session_user.get("username") or getattr(request.state, "audit_actor", None) or "anonymous",
                    source_ip=request.client.host if request.client else "unknown",
                    http_method=request.method,
                    path=request.url.path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
            request_id_var.reset(token)


configure_logging()
