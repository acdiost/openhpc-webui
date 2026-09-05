import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


class TerminalFrontendTests(unittest.TestCase):
    def test_sidebar_offers_current_and_new_tab_terminal_entries(self):
        sidebar = (PROJECT_ROOT / "templates/components/sidebar.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('class="terminal-nav-row"', sidebar)
        self.assertIn('class="terminal-nav-new-tab"', sidebar)
        self.assertIn('target="_blank"', sidebar)
        self.assertIn('rel="noopener"', sidebar)

    def test_terminal_supports_minimize_and_guarded_navigation(self):
        template = (PROJECT_ROOT / "templates/terminal.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "static/terminal.js").read_text(
            encoding="utf-8"
        )

        for element_id in (
            "minimizeTerminalButton",
            "terminalLeaveDialog",
            "terminalStayButton",
            "terminalOpenTargetButton",
            "terminalLeaveButton",
        ):
            self.assertIn(f'id="{element_id}"', template)

        self.assertIn('terminalWindow.classList.toggle("is-minimized"', script)
        self.assertIn('openTargetButton.setAttribute("href", url)', script)
        self.assertIn('target="_blank" rel="noopener"', template)
        self.assertIn('window.addEventListener("beforeunload"', script)
        self.assertIn("openLeaveDialog(link.href)", script)

    def test_terminal_ai_requires_control_enter_confirmation(self):
        template = (PROJECT_ROOT / "templates/terminal.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "static/terminal.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("Ctrl+Enter", template)
        self.assertIn('send({type: "submit", line: currentLine})', script)
        self.assertIn('send({type: "execute_ai"})', script)
        self.assertIn('event.ctrlKey', script)
        self.assertNotIn('send({type: "input", data: message.command', script)

    def test_terminal_ai_offers_inline_hint_and_new_conversation(self):
        template = (PROJECT_ROOT / "templates/terminal.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "static/terminal.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="newAIChatButton"', template)
        self.assertIn("输入命令，或输入问题与 AI 对话", script)
        self.assertIn('send({type: "new_ai_chat"})', script)
        self.assertIn('message.type === "ai_chat_reset"', script)
        self.assertIn("terminal_script_version", template)

    def test_terminal_ai_auto_approval_requires_risk_acknowledgement(self):
        template = (PROJECT_ROOT / "templates/terminal.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "static/terminal.js").read_text(
            encoding="utf-8"
        )

        for element_id in (
            "terminalAutoApprove",
            "terminalAutoApproveDialog",
            "terminalAutoApproveCancel",
            "terminalAutoApproveConfirm",
            "terminalStepLimit",
            "terminalAIModelBar",
            "terminalAIModelInput",
            "terminalAIModelOptions",
            "terminalAIModelApply",
        ):
            self.assertIn(f'id="{element_id}"', template)
        self.assertIn("可能覆盖文件、提交作业、占用 GPU 配额", template)
        self.assertIn('send({type: "set_auto_approve", enabled: true})', script)
        self.assertIn("message.requires_confirmation", script)
        self.assertIn("默认上限 10 步", template)
        self.assertIn('max="50"', template)
        self.assertIn('send({type: "set_ai_max_steps", max_steps: normalized})', script)
        model_position = template.index('id="terminalAIModelBar"')
        step_position = template.index('id="terminalStepLimitControl"')
        actions_start = template.index('class="terminal-bar-actions"')
        actions_end = template.index('<div id="terminal"', actions_start)
        self.assertLess(actions_start, model_position)
        self.assertLess(model_position, step_position)
        self.assertLess(step_position, actions_end)
        self.assertIn('send({type: "set_ai_model", model})', script)
        self.assertIn('message.type === "ai_model_changed"', script)
        self.assertIn("选择或输入模型 ID", template)

    def test_terminal_ai_tracks_bracketed_paste_as_user_text(self):
        script = (PROJECT_ROOT / "static/terminal.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('const pasteStart = "\\x1b[200~"', script)
        self.assertIn('const pasteEnd = "\\x1b[201~"', script)
        self.assertIn("trackTerminalInput(data)", script)
        self.assertIn("handleBracketedPaste(data)", script)
        self.assertIn("handleSingleLineInput(pastedText)", script)
        self.assertIn("terminal.paste(text)", script)

    def test_terminal_ai_classifies_text_and_enter_from_one_input_event(self):
        script = (PROJECT_ROOT / "static/terminal.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("function handleSingleLineInput(data)", script)
        self.assertIn('const submitted = Boolean(match[2])', script)
        self.assertIn("if (submitted) submitTrackedLine()", script)
        self.assertIn("terminal.onData(handleTerminalData)", script)
        self.assertIn("lineTrackingReliable = true", script)
        self.assertIn('if (/(?:\\r\\n?|\\n)$/.test(data))', script)
        self.assertIn("当前目标尚未完成，可补充信息后继续", script)


if __name__ == "__main__":
    unittest.main()
