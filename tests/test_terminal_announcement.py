import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openhpc_webui.services import terminal_announcement
from openhpc_webui.services.terminal_announcement import (
    TerminalAnnouncementError,
    get_config,
    public_config,
    save_config,
)


class TerminalAnnouncementTests(unittest.TestCase):
    def test_persists_announcement_and_applies_it_immediately(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            terminal_announcement, "PROJECT_ROOT", Path(temp_dir)
        ), patch.dict(os.environ, {}, clear=True):
            result = save_config(
                enabled=True,
                message="今晚维护\n请提前保存作业。",
                text_color="#112233",
                background_color="#fef3c7",
                bold=True,
            )
            content = (Path(temp_dir) / ".env").read_text(encoding="utf-8")

            self.assertTrue(result["visible"])
            self.assertEqual(result["message"], "今晚维护\n请提前保存作业。")
            self.assertEqual(get_config().text_color, "#112233")
            self.assertIn('TERMINAL_ANNOUNCEMENT_ENABLED="True"', content)
            self.assertIn("TERMINAL_ANNOUNCEMENT_MESSAGE=", content)
            self.assertEqual((Path(temp_dir) / ".env").stat().st_mode & 0o777, 0o600)

    def test_rejects_empty_enabled_notice_and_invalid_colors(self):
        common = {
            "enabled": True,
            "message": "维护公告",
            "text_color": "#112233",
            "background_color": "#fef3c7",
            "bold": False,
        }
        with self.assertRaises(TerminalAnnouncementError):
            save_config(**{**common, "message": ""})
        with self.assertRaises(TerminalAnnouncementError):
            save_config(**{**common, "text_color": "red;display:none"})

    def test_invalid_environment_colors_fall_back_to_safe_defaults(self):
        with patch.dict(os.environ, {
            "TERMINAL_ANNOUNCEMENT_ENABLED": "True",
            "TERMINAL_ANNOUNCEMENT_MESSAGE": "notice",
            "TERMINAL_ANNOUNCEMENT_TEXT_COLOR": "red;position:fixed",
            "TERMINAL_ANNOUNCEMENT_BACKGROUND_COLOR": "url(example)",
            "TERMINAL_ANNOUNCEMENT_BOLD": "True",
        }, clear=True):
            result = public_config()

        self.assertEqual(result["text_color"], "#92400e")
        self.assertEqual(result["background_color"], "#fef3c7")


if __name__ == "__main__":
    unittest.main()
