import tempfile
import unittest
from pathlib import Path

from scripts.bump_version import (
    MANAGED_VERSION_FILES,
    VersionUpdateError,
    build_updates,
    check_versions,
    write_updates,
)


class BumpVersionTests(unittest.TestCase):
    def create_project(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for relative_path in MANAGED_VERSION_FILES:
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative_path == "openhpc_webui/__about__.py":
                content = '__version__ = "0.2.1"\n'
            else:
                content = "适用版本 0.2.1\n"
            path.write_text(content, encoding="utf-8")
        (root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Unreleased\n\n### Added\n\n- 新功能\n\n## 0.2.1 - 20260830\n",
            encoding="utf-8",
        )
        return root

    def test_build_and_write_updates_synchronizes_managed_files(self):
        root = self.create_project()

        updates = build_updates(root, "0.2.2", "20260901")
        write_updates(updates)

        self.assertEqual(check_versions(root), "0.2.2")
        for relative_path in MANAGED_VERSION_FILES:
            content = (root / relative_path).read_text(encoding="utf-8")
            self.assertIn("0.2.2", content)
            self.assertNotIn("0.2.1", content)
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## Unreleased\n\n## 0.2.2 - 20260901", changelog)
        self.assertIn("## 0.2.1 - 20260830", changelog)

    def test_check_versions_rejects_inconsistent_file(self):
        root = self.create_project()
        (root / "README.md").write_text("适用版本 0.2.0\n", encoding="utf-8")

        with self.assertRaises(VersionUpdateError):
            check_versions(root)

    def test_build_updates_rejects_downgrade_by_default(self):
        root = self.create_project()

        with self.assertRaises(VersionUpdateError):
            build_updates(root, "0.2.0", "20260901")

    def test_build_updates_does_not_write_during_planning(self):
        root = self.create_project()

        build_updates(root, "0.2.2", "20260901")

        self.assertEqual(check_versions(root), "0.2.1")
        self.assertNotIn(
            "0.2.2",
            (root / "CHANGELOG.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
