"""Application configuration and filesystem locations."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def _resource_dir(name: str) -> Path:
    packaged_path = PACKAGE_ROOT / name
    return packaged_path if packaged_path.is_dir() else PROJECT_ROOT / name


STATIC_DIR = _resource_dir("static")
TEMPLATES_DIR = _resource_dir("templates")
DEFAULT_SLURM_CONFIG_DIR = "/etc/slurm"
_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})

load_dotenv(PROJECT_ROOT / ".env")


def env_bool(name: str, default: bool) -> bool:
    """Read an explicit boolean environment variable or reject invalid input."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    allowed = ", ".join(sorted(_TRUE_VALUES | _FALSE_VALUES))
    raise ValueError(f"{name} must be one of: {allowed}")


def env_positive_int(name: str, default: int) -> int:
    """Read a positive integer, falling back when configuration is invalid."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def slurm_config_file(filename: str) -> str:
    """Resolve a managed Slurm config file from the configured directory."""
    config_dir = os.getenv("SLURM_CONFIG_DIR", DEFAULT_SLURM_CONFIG_DIR)
    return str(Path(config_dir).expanduser() / filename)


@dataclass(frozen=True)
class Settings:
    """Process-level settings used while constructing the ASGI app."""

    app_title: str = "智算中心管理门户"
    auth_enabled: bool = env_bool("AUTHORIZED", True)
    session_https_only: bool = env_bool("SESSION_HTTPS_ONLY", False)
    login_max_failed_attempts: int = env_positive_int("LOGIN_MAX_FAILED_ATTEMPTS", 5)
    login_lockout_minutes: int = env_positive_int("LOGIN_LOCKOUT_MINUTES", 30)
    file_upload_max_mb: int = env_positive_int("FILE_UPLOAD_MAX_MB", 1024)
    file_edit_max_kb: int = env_positive_int("FILE_EDIT_MAX_KB", 2048)
    terminal_enabled: bool = env_bool("TERMINAL_ENABLED", True)
    terminal_idle_minutes: int = env_positive_int("TERMINAL_IDLE_MINUTES", 30)
    terminal_max_sessions: int = env_positive_int("TERMINAL_MAX_SESSIONS", 2)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a fresh snapshot after page-managed environment changes."""
        return cls(
            auth_enabled=env_bool("AUTHORIZED", True),
            session_https_only=env_bool("SESSION_HTTPS_ONLY", False),
            login_max_failed_attempts=env_positive_int(
                "LOGIN_MAX_FAILED_ATTEMPTS", 5
            ),
            login_lockout_minutes=env_positive_int("LOGIN_LOCKOUT_MINUTES", 30),
            file_upload_max_mb=env_positive_int("FILE_UPLOAD_MAX_MB", 1024),
            file_edit_max_kb=env_positive_int("FILE_EDIT_MAX_KB", 2048),
            terminal_enabled=env_bool("TERMINAL_ENABLED", True),
            terminal_idle_minutes=env_positive_int("TERMINAL_IDLE_MINUTES", 30),
            terminal_max_sessions=env_positive_int("TERMINAL_MAX_SESSIONS", 2),
        )


settings = Settings.from_env()
