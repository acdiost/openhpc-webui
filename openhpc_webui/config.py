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

load_dotenv(PROJECT_ROOT / ".env")


def env_bool(name: str, default: bool) -> bool:
    """Read a conventional boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"true", "1", "yes", "on"}


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


settings = Settings()
