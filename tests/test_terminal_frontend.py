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


if __name__ == "__main__":
    unittest.main()
