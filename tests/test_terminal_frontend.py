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


if __name__ == "__main__":
    unittest.main()
