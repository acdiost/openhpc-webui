import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from openhpc_webui.audit import sanitize
from openhpc_webui.services import terminal_ai
from openhpc_webui.services.terminal_ai import (
    TerminalAIClient,
    TerminalAIError,
    clean_terminal_output,
    get_config,
    is_probable_command,
    save_config,
    TerminalAIReply,
)
import openhpc_webui.application as main


class _FakeWebSocket:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.json = []
        self.binary = []

    async def receive(self):
        return self.messages.pop(0)

    async def send_json(self, payload):
        self.json.append(payload)

    async def send_bytes(self, payload):
        self.binary.append(payload)


class _FakeSession:
    def __init__(self, reads=None):
        self.writes = []
        self.reads = list(reads or [])

    def write(self, payload):
        self.writes.append(payload)

    def read(self):
        return self.reads.pop(0)

    def resize(self, cols, rows):
        return None


class TerminalAICommandTests(unittest.TestCase):
    def test_detects_shell_commands_and_natural_language(self):
        self.assertTrue(is_probable_command("cd /tmp"))
        self.assertTrue(is_probable_command("module load cuda"))
        self.assertTrue(is_probable_command("squeue -u $USER"))
        self.assertTrue(is_probable_command("FOO=bar ./run.sh"))
        self.assertTrue(is_probable_command("!my-cluster-alias"))
        self.assertFalse(is_probable_command("帮我查看当前 GPU 使用情况"))
        self.assertFalse(is_probable_command("how can I inspect a slurm job"))

    def test_unknown_command_can_be_forced_with_bang(self):
        with patch.object(terminal_ai.shutil, "which", return_value=None):
            self.assertFalse(is_probable_command("cluster-status --all"))
            self.assertTrue(is_probable_command("!cluster-status --all"))

    def test_cleans_ansi_and_bounds_terminal_output(self):
        cleaned = clean_terminal_output(b"\x1b[31mfailed\x1b[0m\r\nnext\x00")
        self.assertEqual(cleaned, "failed\nnext")


class TerminalAIConfigTests(unittest.TestCase):
    def test_persists_quoted_settings_without_returning_key(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            terminal_ai, "PROJECT_ROOT", Path(temp_dir)
        ), patch.dict(os.environ, {}, clear=False):
            result = save_config(
                enabled=True,
                provider="vllm",
                base_url="http://127.0.0.1:8000/v1/",
                model="Qwen/Qwen3",
                api_key="private-value",
                clear_api_key=False,
                timeout_seconds=45,
            )
            content = (Path(temp_dir) / ".env").read_text(encoding="utf-8")

        self.assertTrue(result["api_key_configured"])
        self.assertNotIn("api_key", result)
        self.assertIn('TERMINAL_AI_PROVIDER="vllm"', content)
        self.assertIn('TERMINAL_AI_API_KEY="private-value"', content)

    def test_rejects_invalid_endpoint_and_key_control_characters(self):
        common = dict(
            enabled=True,
            provider="sglang",
            model="model",
            clear_api_key=False,
            timeout_seconds=60,
        )
        with self.assertRaisesRegex(TerminalAIError, "Base URL"):
            save_config(base_url="file:///tmp/model", api_key=None, **common)
        with self.assertRaisesRegex(TerminalAIError, "控制字符"):
            save_config(base_url="http://localhost:30000/v1", api_key="a\nb", **common)

    def test_deepseek_uses_default_openai_compatible_url(self):
        environment = {
            "TERMINAL_AI_ENABLED": "True",
            "TERMINAL_AI_PROVIDER": "deepseek",
            "TERMINAL_AI_BASE_URL": "",
            "TERMINAL_AI_MODEL": "deepseek-chat",
        }
        with patch.dict(os.environ, environment, clear=False):
            config = get_config()
        self.assertTrue(config.available)
        self.assertEqual(config.base_url, "https://api.deepseek.com/v1")

    def test_audit_sanitizer_redacts_api_key(self):
        self.assertEqual(sanitize({"api_key": "secret"})["api_key"], "[REDACTED]")


class TerminalAIReplyTests(unittest.TestCase):
    def test_ai_command_is_parsed_but_not_executed(self):
        client = TerminalAIClient()
        client._complete = AsyncMock(
            return_value='{"answer":"可以查看队列", "command":"squeue -u $USER"}'
        )
        history = []

        reply = asyncio.run(client.ask("查看我的作业", history))

        self.assertEqual(reply.command, "squeue -u $USER")
        self.assertEqual(reply.answer, "可以查看队列")
        self.assertEqual(len(history), 2)

    def test_multiline_ai_command_is_preserved_for_confirmed_scripts(self):
        reply = TerminalAIClient._parse_reply(
            '{"answer":"需要两步", "command":"echo one\\necho two"}'
        )
        self.assertEqual(reply.command, "echo one\necho two")

    def test_file_action_becomes_a_confirmed_shell_write(self):
        reply = TerminalAIClient._parse_reply(json.dumps({
            "answer": "将创建 GPU 测试脚本",
            "command": None,
            "file": {
                "path": "gpu_test.sh",
                "content": "#!/bin/bash\n#SBATCH --gres=gpu:1\nsleep 60",
                "executable": True,
            },
        }))

        self.assertIn("cat > gpu_test.sh <<'__OPENHPC_AI_FILE_EOF__'", reply.command)
        self.assertIn("#SBATCH --gres=gpu:1", reply.command)
        self.assertTrue(reply.command.endswith("chmod +x gpu_test.sh"))
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                ["/bin/sh", "-c", reply.command], cwd=temp_dir, check=False
            )
            created = Path(temp_dir) / "gpu_test.sh"
            self.assertEqual(completed.returncode, 0)
            self.assertIn("sleep 60", created.read_text(encoding="utf-8"))
            self.assertTrue(created.stat().st_mode & 0o100)

    def test_recovers_file_action_from_markdown_model_reply(self):
        reply = TerminalAIClient._parse_reply(
            "请将以下内容保存为 `gpu_test.sh`：\n"
            "```bash\n#!/bin/bash\n#SBATCH --gres=gpu:1\nsleep 60\n```\n"
            "然后运行 sbatch。"
        )

        self.assertEqual(reply.answer, "已生成文件 gpu_test.sh；确认后将写入当前工作目录。")
        self.assertIn("cat > gpu_test.sh", reply.command)
        self.assertIn("sleep 60", reply.command)
        self.assertTrue(reply.command.endswith("chmod +x gpu_test.sh"))

    def test_file_action_rejects_paths_outside_current_directory(self):
        for path in ("/etc/profile", "../outside.sh", "~/hidden.sh"):
            with self.subTest(path=path):
                command = TerminalAIClient._file_write_command({
                    "path": path,
                    "content": "echo unsafe",
                    "executable": True,
                })
                self.assertIsNone(command)


class TerminalAIProtocolTests(unittest.TestCase):
    def test_ai_reply_only_stages_command_until_execute_message(self):
        websocket = _FakeWebSocket([
            {"type": "websocket.receive", "text": '{"type":"submit","line":"查看作业"}'},
            {"type": "websocket.disconnect"},
        ])
        session = _FakeSession()
        state = main._TerminalAIState()
        reply = TerminalAIReply("可以查看", "squeue -u $USER")
        async def receive():
            await main._receive_terminal_input(
                websocket, session, [0.0], state, asyncio.Lock()
            )
        with patch.object(
            main, "get_terminal_ai_config", return_value=SimpleNamespace(available=True)
        ), patch.object(main.terminal_ai_client, "ask", AsyncMock(return_value=reply)):
            asyncio.run(receive())

        self.assertEqual(session.writes, [b"\x15\r"])
        self.assertEqual(state.pending_command, "squeue -u $USER")
        self.assertEqual(websocket.json[-1]["type"], "ai_reply")

    def test_execute_message_runs_staged_command_with_completion_marker(self):
        websocket = _FakeWebSocket([
            {"type": "websocket.receive", "text": '{"type":"execute_ai"}'},
            {"type": "websocket.disconnect"},
        ])
        session = _FakeSession()
        state = main._TerminalAIState(pending_command="pwd")
        async def receive():
            await main._receive_terminal_input(
                websocket, session, [0.0], state, asyncio.Lock()
            )
        with patch.object(main.secrets, "token_hex", return_value="abc"):
            asyncio.run(receive())

        written = b"".join(session.writes)
        self.assertTrue(written.startswith(b"\x15__oh_sa="))
        self.assertIn(b"; eval pwd;", written)
        self.assertIn(b"START_abc__", written)
        self.assertIn(b"DONE_abc__", written)
        self.assertIsNone(state.pending_command)
        self.assertEqual(state.active_command, "pwd")

    def test_new_chat_clears_history_and_pending_command(self):
        websocket = _FakeWebSocket([
            {"type": "websocket.receive", "text": '{"type":"new_ai_chat"}'},
            {"type": "websocket.disconnect"},
        ])
        session = _FakeSession()
        state = main._TerminalAIState(
            history=[{"role": "user", "content": "old"}],
            pending_command="old-command",
        )

        async def receive():
            await main._receive_terminal_input(
                websocket, session, [0.0], state, asyncio.Lock()
            )

        asyncio.run(receive())

        self.assertEqual(state.history, [])
        self.assertIsNone(state.pending_command)
        self.assertEqual(websocket.json[-1]["type"], "ai_chat_reset")

    def test_completion_marker_is_hidden_and_output_is_summarized(self):
        start_marker = b"__OPENHPC_AI_START_abc__"
        marker = b"__OPENHPC_AI_DONE_abc__"
        echoed_wrapper = b"internal wrapper text\r\n"
        session = _FakeSession([
            echoed_wrapper + start_marker + b"\r\nresult\r\n" + marker + b":0\r\n$ ",
            b"",
        ])
        websocket = _FakeWebSocket()
        state = main._TerminalAIState(
            history=[
                {"role": "user", "content": "当前目录是什么"},
                {"role": "assistant", "content": "建议执行 pwd"},
            ],
            active_command="pwd",
            start_marker=start_marker,
            marker=marker,
        )
        summarize = AsyncMock(return_value="命令成功，输出了当前目录。")
        async def send_output():
            await main._send_terminal_output(
                websocket, session, [0.0], state, asyncio.Lock()
            )
        with patch.object(main.terminal_ai_client, "summarize", summarize):
            asyncio.run(send_output())

        rendered = b"".join(websocket.binary)
        self.assertNotIn(echoed_wrapper, rendered)
        self.assertNotIn(start_marker, rendered)
        self.assertNotIn(marker, rendered)
        self.assertIn(b"result", rendered)
        self.assertEqual(websocket.json[0]["type"], "ai_summary")
        summarize.assert_awaited_once()
        self.assertEqual(summarize.await_args.args[1], "result")
        self.assertIn("执行结果（退出码 0）", state.history[-1]["content"])


if __name__ == "__main__":
    unittest.main()
