"""OpenAI-compatible AI assistance for interactive terminal sessions."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from ..config import PROJECT_ROOT


_PROVIDERS = {"deepseek", "vllm", "sglang", "openai-compatible"}
_DEFAULT_BASE_URLS = {"deepseek": "https://api.deepseek.com/v1"}
_ENV_KEYS = {
    "enabled": "TERMINAL_AI_ENABLED",
    "provider": "TERMINAL_AI_PROVIDER",
    "base_url": "TERMINAL_AI_BASE_URL",
    "model": "TERMINAL_AI_MODEL",
    "api_key": "TERMINAL_AI_API_KEY",
    "timeout_seconds": "TERMINAL_AI_TIMEOUT_SECONDS",
}
_SHELL_BUILTINS = {
    ".", "alias", "bg", "bind", "break", "builtin", "cd", "command",
    "compgen", "complete", "continue", "declare", "dirs", "disown",
    "echo", "enable", "eval", "exec", "exit", "export", "false", "fc",
    "fg", "getopts", "hash", "help", "history", "jobs", "kill", "let",
    "local", "logout", "mapfile", "popd", "printf", "pushd", "pwd",
    "read", "readonly", "return", "set", "shift", "shopt", "source",
    "suspend", "test", "times", "trap", "true", "type", "typeset",
    "ulimit", "umask", "unalias", "unset", "wait",
    # Common HPC commands include shell functions such as Environment Modules,
    # which cannot be discovered with shutil.which from the WebUI process.
    "module", "ml", "sacct", "sacctmgr", "sbatch", "scancel", "scontrol",
    "sinfo", "squeue", "srun",
}
_COMMAND_PREFIX = re.compile(
    r"^(?:[./~]|\$\(|`|\(|\{|!|sudo\s|env\s|[A-Za-z_][A-Za-z0-9_]*=)"
)
_SHELL_OPERATOR = re.compile(r"(?:^|\s)(?:&&|\|\||\||;|>|>>|<|2>)")
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class TerminalAIError(RuntimeError):
    """Safe error that may be shown to a terminal user."""


@dataclass(frozen=True)
class TerminalAIConfig:
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: int

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.base_url and self.model)


@dataclass(frozen=True)
class TerminalAIReply:
    answer: str
    command: Optional[str] = None


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def get_config() -> TerminalAIConfig:
    provider = os.getenv(_ENV_KEYS["provider"], "deepseek").strip().lower()
    base_url = os.getenv(_ENV_KEYS["base_url"], "").strip()
    if not base_url:
        base_url = _DEFAULT_BASE_URLS.get(provider, "")
    try:
        timeout = int(os.getenv(_ENV_KEYS["timeout_seconds"], "60"))
    except ValueError:
        timeout = 60
    return TerminalAIConfig(
        enabled=_bool_env(_ENV_KEYS["enabled"]),
        provider=provider,
        base_url=base_url.rstrip("/"),
        model=os.getenv(_ENV_KEYS["model"], "").strip(),
        api_key=os.getenv(_ENV_KEYS["api_key"], "").strip(),
        timeout_seconds=max(5, min(timeout, 300)),
    )


def public_config(*, include_endpoint: bool = False) -> Dict[str, Any]:
    config = get_config()
    result: Dict[str, Any] = {
        "enabled": config.enabled,
        "available": config.available,
        "provider": config.provider,
        "model": config.model,
        "api_key_configured": bool(config.api_key),
        "timeout_seconds": config.timeout_seconds,
    }
    if include_endpoint:
        result["base_url"] = config.base_url
    return result


def _validate_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if any(ord(character) < 32 for character in value):
        raise TerminalAIError("Base URL 不能包含控制字符")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TerminalAIError("Base URL 必须是有效的 http:// 或 https:// 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TerminalAIError("Base URL 不能包含凭据、查询参数或片段")
    return value


def save_config(
    *, enabled: bool, provider: str, base_url: str, model: str,
    api_key: Optional[str], clear_api_key: bool, timeout_seconds: int,
) -> Dict[str, Any]:
    provider = provider.strip().lower()
    if provider not in _PROVIDERS:
        raise TerminalAIError("不支持的模型服务类型")
    base_url = base_url.strip() or _DEFAULT_BASE_URLS.get(provider, "")
    if enabled and not model.strip():
        raise TerminalAIError("启用终端 AI 前必须填写模型名称")
    if enabled and not base_url:
        raise TerminalAIError("启用终端 AI 前必须填写 Base URL")
    if base_url:
        base_url = _validate_url(base_url)
    for label, value in (("模型名称", model), ("API Key", api_key or "")):
        if any(ord(character) < 32 for character in value):
            raise TerminalAIError(f"{label}不能包含控制字符")
    values = {
        _ENV_KEYS["enabled"]: "True" if enabled else "False",
        _ENV_KEYS["provider"]: provider,
        _ENV_KEYS["base_url"]: base_url,
        _ENV_KEYS["model"]: model.strip(),
        _ENV_KEYS["timeout_seconds"]: str(max(5, min(timeout_seconds, 300))),
    }
    if clear_api_key:
        values[_ENV_KEYS["api_key"]] = ""
    elif api_key is not None and api_key.strip():
        values[_ENV_KEYS["api_key"]] = api_key.strip()
    _write_env(values)
    return public_config(include_endpoint=True)


def _write_env(values: Dict[str, str]) -> None:
    env_path = PROJECT_ROOT / ".env"
    try:
        content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        lines = content.splitlines()
        remaining = dict(values)
        managed_keys = set(values)
        written = set()
        output: List[str] = []
        for line in lines:
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
        raise TerminalAIError("保存失败，请检查 .env 文件权限") from exc
    os.environ.update(values)


def is_probable_command(line: str, path: Optional[str] = None) -> bool:
    """Conservatively distinguish shell input from a natural-language question."""
    value = line.strip()
    if not value:
        return True
    if value.startswith("!") or _COMMAND_PREFIX.search(value) or _SHELL_OPERATOR.search(value):
        return True
    try:
        words = shlex.split(value, posix=True)
    except ValueError:
        return True  # Let the shell report quoting errors.
    if not words:
        return True
    command = words[0]
    if command in _SHELL_BUILTINS or command.startswith("-"):
        return True
    search_path = path or "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
    return shutil.which(command, path=search_path) is not None


def clean_terminal_output(value: bytes, limit: int = 65_536) -> str:
    text = value[-limit:].decode("utf-8", errors="replace")
    text = _ANSI_ESCAPE.sub("", text).replace("\r", "")
    return "".join(char for char in text if char == "\n" or char == "\t" or ord(char) >= 32).strip()


class TerminalAIClient:
    """Small async client for DeepSeek, vLLM and SGLang chat endpoints."""

    async def ask(self, question: str, history: List[Dict[str, str]]) -> TerminalAIReply:
        system = (
            "你是 Linux/HPC 终端助手。回答必须是一个 JSON 对象，不要使用 Markdown 代码块："
            '{"answer":"简洁中文答复","command":null}。如果建议用户执行一条 Shell 命令，'
            "将完整命令放入 command；命令不会自动执行，必须由用户确认。不要声称命令已经执行。"
        )
        messages = [{"role": "system", "content": system}]
        messages.extend(history[-8:])
        messages.append({"role": "user", "content": question})
        content = await self._complete(messages)
        reply = self._parse_reply(content)
        history.extend([
            {"role": "user", "content": question},
            {"role": "assistant", "content": reply.answer + (f"\n建议命令：{reply.command}" if reply.command else "")},
        ])
        del history[:-8]
        return reply

    async def summarize(self, command: str, output: str, exit_code: int) -> str:
        prompt = (
            f"分析下面终端命令的执行结果并用中文简洁总结。退出码：{exit_code}\n"
            f"命令：{command}\n输出（可能已截断）：\n{output or '(无输出)'}"
        )
        return await self._complete([
            {
                "role": "system",
                "content": (
                    "你是 Linux/HPC 运维助手。用中文最多三个短句总结成功或失败、"
                    "关键结果和必要的下一步；不要使用 Markdown 标题或列表，不要编造，"
                    "不要提及任何内部状态标记或包装命令。"
                ),
            },
            {"role": "user", "content": prompt},
        ])

    async def _complete(self, messages: List[Dict[str, str]]) -> str:
        config = get_config()
        if not config.available:
            raise TerminalAIError("终端 AI 尚未配置或未启用")
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                response = await client.post(
                    f"{config.base_url}/chat/completions",
                    headers=headers,
                    json={"model": config.model, "messages": messages, "temperature": 0.2},
                )
                response.raise_for_status()
                if len(response.content) > 2 * 1024 * 1024:
                    raise TerminalAIError("模型服务返回内容过大")
                payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except TerminalAIError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise TerminalAIError("模型服务请求失败，请联系管理员检查配置") from exc
        if not isinstance(content, str) or not content.strip():
            raise TerminalAIError("模型服务返回了空内容")
        return content.strip()

    @staticmethod
    def _parse_reply(content: str) -> TerminalAIReply:
        candidate = content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
        if fenced:
            candidate = fenced.group(1)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return TerminalAIReply(answer=content.strip())
        if not isinstance(payload, dict):
            return TerminalAIReply(answer=content.strip())
        answer = str(payload.get("answer") or "").strip()
        command_value = payload.get("command")
        command = str(command_value).strip() if command_value else None
        if command and (any(ord(character) < 32 for character in command) or len(command) > 4096):
            command = None
        return TerminalAIReply(answer=answer or "模型已返回建议。", command=command)
