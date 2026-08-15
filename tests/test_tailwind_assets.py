import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
TAILWIND_CSS = PROJECT_ROOT / "static/all-tailwind-classes-full-min.css"


class TailwindAssetTests(unittest.TestCase):
    def test_compiled_stylesheet_stays_purged(self):
        self.assertLess(TAILWIND_CSS.stat().st_size, 100_000)

    def test_runtime_utility_classes_are_compiled(self):
        stylesheet = TAILWIND_CSS.read_text(encoding="utf-8")

        for selector in (
            ".fixed",
            ".top-4",
            ".right-4",
            ".bg-green-600",
            ".bg-red-600",
            ".bg-yellow-600",
            ".hover\\:bg-gray-300",
        ):
            self.assertIn(selector, stylesheet)


if __name__ == "__main__":
    unittest.main()
