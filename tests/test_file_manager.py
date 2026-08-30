import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from openhpc_webui.services.file_manager import (
    FileAccessDenied,
    FileManager,
    FileManagerError,
)
from fastapi.testclient import TestClient
from openhpc_webui import application as main


class FileManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "home" / "alice"
        self.root.mkdir(parents=True)
        self.root = self.root.resolve()
        self.ldap = SimpleNamespace(
            get_user=lambda username: {
                "username": username,
                "home": str(self.root),
                "uid": os.getuid(),
                "gid": os.getgid(),
            }
        )
        self.manager = FileManager(max_upload_bytes=8, max_edit_bytes=16)
        self.user = {"username": "alice", "is_admin": False}

    def tearDown(self):
        self.temporary.cleanup()

    def test_regular_user_root_is_ldap_home(self):
        self.assertEqual(self.manager.scope_root(self.user, self.ldap), self.root)
        self.assertEqual(
            self.manager.scope_root({"username": "admin", "is_admin": True}, self.ldap),
            Path("/"),
        )

    def test_dot_dot_cannot_escape_home(self):
        with self.assertRaises(FileAccessDenied):
            self.manager.resolve("/../../etc", self.root)

    def test_symlink_cannot_be_used_to_read_outside_home(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (self.root / "escape").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(FileAccessDenied):
            self.manager.resolve("/escape/secret.txt", self.root)

    def test_deleting_symlink_does_not_delete_its_target(self):
        target = self.root / "target"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)

        self.manager.delete("/link", self.root)

        self.assertFalse(link.exists())
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_list_create_upload_rename_and_delete(self):
        created = self.manager.create_directory("/", "work", self.root)
        self.assertEqual(created, "/work")

        uploaded = self.manager.upload(
            "/work", "data.txt", io.BytesIO(b"payload"), self.root
        )
        self.assertEqual(uploaded, "/work/data.txt")
        listing = self.manager.list_directory("/work", self.root)
        self.assertEqual([entry["name"] for entry in listing["entries"]], ["data.txt"])

        renamed = self.manager.rename("/work/data.txt", "result.txt", self.root)
        self.assertEqual(renamed, "/work/result.txt")
        self.manager.delete("/work", self.root)
        self.assertFalse((self.root / "work").exists())

    def test_upload_size_limit_removes_temporary_file(self):
        with self.assertRaisesRegex(FileManagerError, "超过大小限制"):
            self.manager.upload("/", "large.bin", io.BytesIO(b"123456789"), self.root)

        self.assertFalse((self.root / "large.bin").exists())
        self.assertEqual(list(self.root.glob(".upload-*")), [])

    def test_hidden_entries_are_optional(self):
        (self.root / ".secret").write_text("hidden", encoding="utf-8")
        (self.root / "visible").write_text("shown", encoding="utf-8")

        hidden_off = self.manager.list_directory("/", self.root)
        hidden_on = self.manager.list_directory("/", self.root, show_hidden=True)

        self.assertEqual(
            {entry["name"] for entry in hidden_off["entries"]}, {"visible"}
        )
        self.assertEqual(
            {entry["name"] for entry in hidden_on["entries"]},
            {".secret", "visible"},
        )

    def test_large_directory_is_returned_in_bounded_pages(self):
        for index in range(7):
            (self.root / f"item-{index}").write_text(str(index), encoding="utf-8")

        first = self.manager.list_directory("/", self.root, limit=3)
        second = self.manager.list_directory(
            "/", self.root, cursor=first["next_cursor"], limit=3
        )
        third = self.manager.list_directory(
            "/", self.root, cursor=second["next_cursor"], limit=3
        )

        names = {
            entry["name"]
            for page in (first, second, third)
            for entry in page["entries"]
        }
        self.assertEqual(names, {f"item-{index}" for index in range(7)})
        self.assertTrue(first["has_more"])
        self.assertTrue(second["has_more"])
        self.assertFalse(third["has_more"])
        self.assertLessEqual(max(len(page["entries"]) for page in (first, second, third)), 3)

    def test_utf8_text_can_be_read_and_saved(self):
        target = self.root / "notes.txt"
        target.write_text("旧内容", encoding="utf-8")

        opened = self.manager.read_text("/notes.txt", self.root)
        self.assertEqual(opened["content"], "旧内容")
        self.manager.write_text("/notes.txt", "新内容\n", self.root)

        self.assertEqual(target.read_text(encoding="utf-8"), "新内容\n")

    def test_binary_and_oversized_files_cannot_be_edited(self):
        (self.root / "binary.bin").write_bytes(b"abc\x00def")
        (self.root / "large.txt").write_bytes(b"12345678901234567")

        with self.assertRaisesRegex(FileManagerError, "二进制文件"):
            self.manager.read_text("/binary.bin", self.root)
        with self.assertRaisesRegex(FileManagerError, "文件过大"):
            self.manager.read_text("/large.txt", self.root)

    def test_root_directory_cannot_be_renamed_or_deleted(self):
        with self.assertRaisesRegex(FileManagerError, "不能删除根目录"):
            self.manager.delete("/", self.root)
        with self.assertRaisesRegex(FileManagerError, "不能重命名根目录"):
            self.manager.rename("/", "other", self.root)

    def test_invalid_home_is_rejected(self):
        missing_ldap = SimpleNamespace(
            get_user=lambda username: {"username": username, "home": "/missing/path"}
        )
        with self.assertRaisesRegex(FileManagerError, "不存在或不可访问"):
            self.manager.scope_root(self.user, missing_ldap)


class FileApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name).resolve()
        (self.home / "hello.txt").write_text("hello", encoding="utf-8")
        self.app = main.create_app()
        self.app.dependency_overrides[main.get_current_user] = lambda: {
            "username": "alice",
            "is_admin": False,
        }

    def tearDown(self):
        self.temporary.cleanup()

    def user_record(self, username):
        return {
            "username": username,
            "home": str(self.home),
            "uid": os.getuid(),
            "gid": os.getgid(),
        }

    def test_regular_user_api_lists_home_and_rejects_traversal(self):
        with patch.object(main.ldap_mgr, "get_user", side_effect=self.user_record), TestClient(
            self.app
        ) as client:
            response = client.get("/api/files", params={"path": "/"})
            denied = client.get("/api/files", params={"path": "/../../etc"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scope"], "home")
        self.assertEqual(response.json()["home_path"], "/")
        self.assertEqual(response.json()["entries"][0]["name"], "hello.txt")
        self.assertEqual(denied.status_code, 403)

    def test_admin_starts_in_own_system_home_but_keeps_root_scope(self):
        self.app.dependency_overrides[main.get_current_user] = lambda: {
            "username": "alice",
            "is_admin": True,
        }
        system_record = SimpleNamespace(pw_dir=str(self.home))
        with patch.object(main.pwd, "getpwnam", return_value=system_record), TestClient(
            self.app
        ) as client:
            page = client.get("/files")
            response = client.get("/api/files", params={"path": str(self.home)})

        self.assertEqual(page.status_code, 200)
        self.assertIn(f'loadFiles("{self.home}", 0)', page.text)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scope"], "root")
        self.assertEqual(response.json()["home_path"], str(self.home))
        self.assertEqual(response.json()["path"], str(self.home))

    def test_upload_api_writes_beneath_home(self):
        with patch.object(main.ldap_mgr, "get_user", side_effect=self.user_record), TestClient(
            self.app
        ) as client:
            response = client.post(
                "/api/files/upload",
                params={"path": "/"},
                files={"upload": ("upload.txt", b"uploaded", "text/plain")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.home / "upload.txt").read_bytes(), b"uploaded")

    def test_content_api_reads_and_updates_text_file(self):
        with patch.object(main.ldap_mgr, "get_user", side_effect=self.user_record), TestClient(
            self.app
        ) as client:
            opened = client.get("/api/files/content", params={"path": "/hello.txt"})
            saved = client.put(
                "/api/files/content",
                json={"path": "/hello.txt", "content": "updated\n"},
            )

        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.json()["content"], "hello")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual((self.home / "hello.txt").read_text(encoding="utf-8"), "updated\n")

    def test_file_page_uses_custom_dialog_and_editor(self):
        template = main.TEMPLATES_DIR.joinpath("files.html").read_text(encoding="utf-8")
        self.assertIn('id="hiddenToggle"', template)
        self.assertIn('id="fileDialog"', template)
        self.assertIn('id="fileLineNumbers"', template)
        self.assertIn('id="unsavedDialog"', template)
        self.assertIn("editor.value !== editorOriginalContent", template)
        self.assertIn("function goHome()", template)
        self.assertIn("loadFiles({{ initial_path | tojson }}, 0)", template)
        self.assertIn("openEditor(entry)", template)
        self.assertIn('id="nextPage"', template)
        self.assertNotIn("prompt(", template)
        self.assertNotIn("confirm(", template)

    def test_global_secondary_buttons_use_light_styling(self):
        stylesheet = main.STATIC_DIR.joinpath("compat.css").read_text(encoding="utf-8")
        self.assertIn(".btn-secondary { background-color: #fff;", stylesheet)
        self.assertNotIn(".btn-secondary { background-color: #6b7280;", stylesheet)


if __name__ == "__main__":
    unittest.main()
