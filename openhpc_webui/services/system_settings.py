"""Validated persistence for administrator-managed system settings."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit

from ..config import PROJECT_ROOT


class SystemSettingsError(ValueError):
    """A safe configuration error that can be returned to an administrator."""


_WRITE_LOCK = threading.Lock()
_TRUE_VALUES = {"true", "1", "yes", "on"}
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

_DEFAULTS: Dict[str, str] = {
    "SETUP_COMPLETED": "False",
    "LDAP_DEFAULT_BIND_DN": "",
    "LDAP_DEFAULT_AUTHTOK_TYPE": "password",
    "LDAP_DEFAULT_AUTHTOK": "",
    "LDAP_URI": "ldap://localhost",
    "LDAP_BASE_DN": "dc=acdiost,dc=com",
    "LDAP_PORT": "389",
    "LDAP_USE_SSL": "False",
    "LOG_LEVEL": "INFO",
    "LOGIN_MAX_FAILED_ATTEMPTS": "5",
    "LOGIN_LOCKOUT_MINUTES": "30",
    "SECRET_KEY": "",
    "SESSION_HTTPS_ONLY": "False",
    "JOB_OUTPUT_ALLOWED_ROOTS": "",
    "FILE_UPLOAD_MAX_MB": "1024",
    "FILE_EDIT_MAX_KB": "2048",
    "TERMINAL_ENABLED": "True",
    "TERMINAL_IDLE_MINUTES": "30",
    "TERMINAL_MAX_SESSIONS": "2",
    "SLURM_DEFAULT_ACCOUNT": "dawn",
    "SLURM_CLUSTER_NAME": "cluster",
    "SLURM_CONFIG_DIR": "/etc/slurm",
    "AUTHORIZED": "True",
    "NFS_QUOTA_FS": "",
}

_FIELD_KEYS = {
    "ldap_bind_dn": "LDAP_DEFAULT_BIND_DN",
    "ldap_bind_password": "LDAP_DEFAULT_AUTHTOK",
    "ldap_uri": "LDAP_URI",
    "ldap_base_dn": "LDAP_BASE_DN",
    "ldap_port": "LDAP_PORT",
    "ldap_use_ssl": "LDAP_USE_SSL",
    "log_level": "LOG_LEVEL",
    "login_max_failed_attempts": "LOGIN_MAX_FAILED_ATTEMPTS",
    "login_lockout_minutes": "LOGIN_LOCKOUT_MINUTES",
    "secret_key": "SECRET_KEY",
    "session_https_only": "SESSION_HTTPS_ONLY",
    "job_output_allowed_roots": "JOB_OUTPUT_ALLOWED_ROOTS",
    "file_upload_max_mb": "FILE_UPLOAD_MAX_MB",
    "file_edit_max_kb": "FILE_EDIT_MAX_KB",
    "terminal_enabled": "TERMINAL_ENABLED",
    "terminal_idle_minutes": "TERMINAL_IDLE_MINUTES",
    "terminal_max_sessions": "TERMINAL_MAX_SESSIONS",
    "slurm_default_account": "SLURM_DEFAULT_ACCOUNT",
    "slurm_cluster_name": "SLURM_CLUSTER_NAME",
    "slurm_config_dir": "SLURM_CONFIG_DIR",
    "authorized": "AUTHORIZED",
    "nfs_quota_fs": "NFS_QUOTA_FS",
}

_BOOL_FIELDS = {
    "ldap_use_ssl",
    "session_https_only",
    "terminal_enabled",
    "authorized",
}
_POSITIVE_INT_FIELDS = {
    "login_max_failed_attempts": (1, 100),
    "login_lockout_minutes": (1, 10080),
    "file_upload_max_mb": (1, 1024 * 1024),
    "file_edit_max_kb": (1, 1024 * 1024),
    "terminal_idle_minutes": (1, 10080),
    "terminal_max_sessions": (1, 100),
}
_RESTART_KEYS = {"AUTHORIZED", "SECRET_KEY", "SESSION_HTTPS_ONLY"}


def _value(key: str) -> str:
    return os.getenv(key, _DEFAULTS[key]).strip()


def _as_bool(key: str) -> bool:
    return _value(key).lower() in _TRUE_VALUES


def _as_int(key: str) -> int:
    try:
        return int(_value(key))
    except ValueError:
        return int(_DEFAULTS[key])


def public_config() -> Dict[str, Any]:
    """Return editable configuration without ever returning stored secrets."""
    return {
        "ldap": {
            "uri": _value("LDAP_URI"),
            "base_dn": _value("LDAP_BASE_DN"),
            "bind_dn": _value("LDAP_DEFAULT_BIND_DN"),
            "bind_password_configured": bool(_value("LDAP_DEFAULT_AUTHTOK")),
            "port": _as_int("LDAP_PORT"),
            "use_ssl": _as_bool("LDAP_USE_SSL"),
        },
        "slurm": {
            "cluster_name": _value("SLURM_CLUSTER_NAME"),
            "default_account": _value("SLURM_DEFAULT_ACCOUNT"),
            "config_dir": _value("SLURM_CONFIG_DIR"),
        },
        "security": {
            "authorized": _as_bool("AUTHORIZED"),
            "session_https_only": _as_bool("SESSION_HTTPS_ONLY"),
            "secret_key_configured": bool(_value("SECRET_KEY")),
            "login_max_failed_attempts": _as_int("LOGIN_MAX_FAILED_ATTEMPTS"),
            "login_lockout_minutes": _as_int("LOGIN_LOCKOUT_MINUTES"),
        },
        "files": {
            "upload_max_mb": _as_int("FILE_UPLOAD_MAX_MB"),
            "edit_max_kb": _as_int("FILE_EDIT_MAX_KB"),
            "job_output_allowed_roots": _value("JOB_OUTPUT_ALLOWED_ROOTS"),
            "nfs_quota_fs": _value("NFS_QUOTA_FS"),
        },
        "terminal": {
            "enabled": _as_bool("TERMINAL_ENABLED"),
            "idle_minutes": _as_int("TERMINAL_IDLE_MINUTES"),
            "max_sessions": _as_int("TERMINAL_MAX_SESSIONS"),
        },
        "logging": {"level": _value("LOG_LEVEL").upper()},
    }


def _clean_text(field: str, value: Any, *, max_length: int = 4096) -> str:
    rendered = str(value).strip()
    if len(rendered) > max_length or any(ord(char) < 32 for char in rendered):
        raise SystemSettingsError(f"{field} 格式无效")
    return rendered


def _validate_update(update: Dict[str, Any]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for field, raw_value in update.items():
        if field not in _FIELD_KEYS or raw_value is None:
            continue
        key = _FIELD_KEYS[field]
        if field in _BOOL_FIELDS:
            values[key] = "True" if bool(raw_value) else "False"
        elif field in _POSITIVE_INT_FIELDS:
            minimum, maximum = _POSITIVE_INT_FIELDS[field]
            try:
                number = int(raw_value)
            except (TypeError, ValueError) as exc:
                raise SystemSettingsError(f"{field} 必须是整数") from exc
            if not minimum <= number <= maximum:
                raise SystemSettingsError(f"{field} 超出允许范围")
            values[key] = str(number)
        else:
            values[key] = _clean_text(field, raw_value)

    uri = values.get("LDAP_URI")
    if uri:
        parsed = urlsplit(uri)
        if parsed.scheme not in {"ldap", "ldaps"} or not parsed.hostname:
            raise SystemSettingsError("LDAP 地址必须使用 ldap:// 或 ldaps://")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SystemSettingsError("LDAP 地址不能包含凭据、查询参数或片段")
    port = values.get("LDAP_PORT")
    if port is not None and not 1 <= int(port) <= 65535:
        raise SystemSettingsError("LDAP 端口必须在 1 到 65535 之间")
    for key in ("SLURM_CLUSTER_NAME", "SLURM_DEFAULT_ACCOUNT"):
        if key in values and not _NAME_PATTERN.fullmatch(values[key]):
            raise SystemSettingsError(f"{key} 格式无效")
    for key in ("SLURM_CONFIG_DIR", "NFS_QUOTA_FS"):
        if values.get(key) and not Path(values[key]).is_absolute():
            raise SystemSettingsError(f"{key} 必须是绝对路径")
    roots = values.get("JOB_OUTPUT_ALLOWED_ROOTS")
    if roots and any(not Path(item).is_absolute() for item in roots.split(os.pathsep) if item):
        raise SystemSettingsError("作业输出目录必须全部是绝对路径")
    secret = values.get("SECRET_KEY")
    if secret is not None and secret and len(secret) < 32:
        raise SystemSettingsError("Session 密钥至少需要 32 个字符")
    level = values.get("LOG_LEVEL")
    if level is not None:
        level = level.upper()
        if level not in _LOG_LEVELS:
            raise SystemSettingsError("日志级别无效")
        values["LOG_LEVEL"] = level
    return values


def _write_env(values: Dict[str, str]) -> None:
    env_path = PROJECT_ROOT / ".env"
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    remaining = dict(values)
    output = []
    written = set()
    for line in content.splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in values:
            if key not in written:
                output.append(f"{key}={json.dumps(values[key], ensure_ascii=False)}")
                written.add(key)
                remaining.pop(key, None)
            continue
        output.append(line)
    output.extend(
        f"{key}={json.dumps(value, ensure_ascii=False)}"
        for key, value in remaining.items()
    )
    rendered = "\n".join(output).rstrip("\n") + "\n"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(env_path.parent),
            prefix=".env.settings-", delete=False,
        ) as handle:
            temporary_name = handle.name
            os.chmod(temporary_name, 0o600)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, env_path)
        temporary_name = ""
    except OSError as exc:
        raise SystemSettingsError("保存失败，请检查 .env 文件权限") from exc
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def save_config(update: Dict[str, Any]) -> Dict[str, Any]:
    """Validate, persist and publish settings to the current process."""
    changed = _validate_update(update)
    with _WRITE_LOCK:
        values = {key: os.getenv(key, default) for key, default in _DEFAULTS.items()}
        values.update(changed)
        _write_env(values)
        os.environ.update(values)
    restart_required = sorted(key for key in changed if key in _RESTART_KEYS)
    return {
        "config": public_config(),
        "restart_required": restart_required,
    }


def setup_required() -> bool:
    """Return whether this process still needs first-run configuration."""
    if _as_bool("SETUP_COMPLETED"):
        return False
    # Existing deployments predate the setup flag. Treat a complete legacy
    # environment as installed so upgrades never hijack them into the wizard.
    return len(_value("SECRET_KEY")) < 32


def complete_setup(update: Dict[str, Any]) -> Dict[str, Any]:
    """Persist the initial configuration and permanently lock the installer."""
    required = {
        "ldap_uri",
        "ldap_base_dn",
        "ldap_bind_dn",
        "ldap_bind_password",
        "ldap_port",
        "slurm_cluster_name",
        "slurm_default_account",
        "slurm_config_dir",
        "admin_username",
    }
    missing = sorted(field for field in required if not str(update.get(field, "")).strip())
    if missing:
        raise SystemSettingsError("请完成所有必填安装配置")
    admin_username = _clean_text("admin_username", update["admin_username"], max_length=64)
    if not _NAME_PATTERN.fullmatch(admin_username):
        raise SystemSettingsError("管理员用户名格式无效")

    settings_update = {key: value for key, value in update.items() if key != "admin_username"}
    changed = _validate_update(settings_update)
    with _WRITE_LOCK:
        if not setup_required():
            raise SystemSettingsError("系统已经完成安装，不能再次运行安装向导")
        values = {key: os.getenv(key, default) for key, default in _DEFAULTS.items()}
        values.update(changed)
        values.update(
            {
                "ADMIN_USERS": admin_username,
                "AUTHORIZED": "True",
                "SECRET_KEY": secrets.token_urlsafe(48),
                "SETUP_COMPLETED": "True",
            }
        )
        _write_env(values)
        os.environ.update(values)
    return {"config": public_config(), "restart_required": True}
