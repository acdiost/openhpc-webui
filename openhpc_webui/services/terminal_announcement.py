"""Configurable notice displayed above authenticated Web terminal sessions."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Dict

from ..config import PROJECT_ROOT, env_bool


_ENV_KEYS = {
    "enabled": "TERMINAL_ANNOUNCEMENT_ENABLED",
    "message": "TERMINAL_ANNOUNCEMENT_MESSAGE",
    "text_color": "TERMINAL_ANNOUNCEMENT_TEXT_COLOR",
    "background_color": "TERMINAL_ANNOUNCEMENT_BACKGROUND_COLOR",
    "bold": "TERMINAL_ANNOUNCEMENT_BOLD",
}
_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_TEXT_COLOR = "#92400e"
_DEFAULT_BACKGROUND_COLOR = "#fef3c7"


class TerminalAnnouncementError(RuntimeError):
    """Safe announcement configuration error suitable for API responses."""


@dataclass(frozen=True)
class TerminalAnnouncementConfig:
    enabled: bool
    message: str
    text_color: str
    background_color: str
    bold: bool

    @property
    def visible(self) -> bool:
        return self.enabled and bool(self.message)


def _safe_color(value: str, fallback: str) -> str:
    value = value.strip()
    return value.lower() if _COLOR_PATTERN.fullmatch(value) else fallback


def get_config() -> TerminalAnnouncementConfig:
    """Read current process values, including settings saved at runtime."""
    return TerminalAnnouncementConfig(
        enabled=env_bool(_ENV_KEYS["enabled"], False),
        message=os.getenv(_ENV_KEYS["message"], "").strip(),
        text_color=_safe_color(
            os.getenv(_ENV_KEYS["text_color"], _DEFAULT_TEXT_COLOR),
            _DEFAULT_TEXT_COLOR,
        ),
        background_color=_safe_color(
            os.getenv(_ENV_KEYS["background_color"], _DEFAULT_BACKGROUND_COLOR),
            _DEFAULT_BACKGROUND_COLOR,
        ),
        bold=env_bool(_ENV_KEYS["bold"], True),
    )


def public_config() -> Dict[str, Any]:
    config = get_config()
    return {
        "enabled": config.enabled,
        "visible": config.visible,
        "message": config.message,
        "text_color": config.text_color,
        "background_color": config.background_color,
        "bold": config.bold,
    }


def save_config(
    *, enabled: bool, message: str, text_color: str,
    background_color: str, bold: bool,
) -> Dict[str, Any]:
    """Validate and atomically persist terminal announcement settings."""
    message = message.strip()
    if len(message) > 2_000:
        raise TerminalAnnouncementError("终端公告不能超过 2000 个字符")
    if any(
        ord(character) < 32 and character not in {"\n", "\t"}
        for character in message
    ):
        raise TerminalAnnouncementError("终端公告不能包含控制字符")
    if enabled and not message:
        raise TerminalAnnouncementError("启用终端公告前必须填写公告内容")
    if not _COLOR_PATTERN.fullmatch(text_color.strip()):
        raise TerminalAnnouncementError("公告文字颜色必须是六位十六进制颜色")
    if not _COLOR_PATTERN.fullmatch(background_color.strip()):
        raise TerminalAnnouncementError("公告背景颜色必须是六位十六进制颜色")
    values = {
        _ENV_KEYS["enabled"]: "True" if enabled else "False",
        _ENV_KEYS["message"]: message,
        _ENV_KEYS["text_color"]: text_color.strip().lower(),
        _ENV_KEYS["background_color"]: background_color.strip().lower(),
        _ENV_KEYS["bold"]: "True" if bold else "False",
    }
    _write_env(values)
    os.environ.update(values)
    return public_config()


def _write_env(values: Dict[str, str]) -> None:
    env_path = PROJECT_ROOT / ".env"
    try:
        content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        remaining = dict(values)
        managed_keys = set(values)
        written = set()
        output = []
        for line in content.splitlines():
            match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            key = match.group(1) if match else None
            if key in managed_keys:
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
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(env_path.parent), delete=False
        ) as handle:
            handle.write(rendered)
            temp_name = handle.name
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, env_path)
    except OSError as exc:
        raise TerminalAnnouncementError(
            "保存失败，请检查 .env 文件权限"
        ) from exc
