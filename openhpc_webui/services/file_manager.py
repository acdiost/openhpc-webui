"""Scoped filesystem operations for the web file manager."""

import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Optional


class FileManagerError(Exception):
    """Base error raised for a safe, user-facing filesystem failure."""


class FileAccessDenied(FileManagerError):
    """The requested path is outside the caller's filesystem scope."""


class FileManager:
    """Perform filesystem operations beneath a per-request virtual root.

    Administrators use the real filesystem root. Regular users receive their
    LDAP home directory as a virtual ``/``. Paths are resolved for every
    operation so ``..`` and symlink traversal cannot escape that virtual root.
    """

    def __init__(
        self,
        max_upload_bytes: int = 1024 * 1024 * 1024,
        max_edit_bytes: int = 2 * 1024 * 1024,
    ):
        self.max_upload_bytes = max_upload_bytes
        self.max_edit_bytes = max_edit_bytes

    def scope_root(self, user: dict, ldap_manager) -> Path:
        if user.get("is_admin"):
            return Path("/")

        username = str(user.get("username") or "").strip()
        user_data = ldap_manager.get_user(username) if username else None
        home = str((user_data or {}).get("home") or "").strip()
        if not home or not Path(home).is_absolute():
            raise FileManagerError("用户未配置有效的 Home 目录")
        try:
            root = Path(home).resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise FileManagerError("用户 Home 目录不存在或不可访问") from exc
        if not root.is_dir():
            raise FileManagerError("用户 Home 路径不是目录")
        return root

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    def resolve(self, virtual_path: str, root: Path, *, strict: bool = True) -> Path:
        try:
            root = root.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise FileManagerError("文件管理根目录不存在或不可访问") from exc
        value = (virtual_path or "/").strip()
        if "\x00" in value:
            raise FileAccessDenied("路径包含非法字符")
        # Treat all client paths as virtual absolute paths. This also prevents
        # Path's absolute-operand behavior from discarding a regular user's root.
        candidate = root.joinpath(value.lstrip("/"))
        # Check the non-strict canonical form first, both to reject lexical
        # traversal before existence errors and to catch existing symlinks.
        try:
            unchecked = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise FileManagerError("路径无法访问") from exc
        if not self._within(unchecked, root):
            raise FileAccessDenied("无权访问 Home 目录之外的路径")
        try:
            resolved = candidate.resolve(strict=strict)
        except FileNotFoundError as exc:
            raise FileManagerError("文件或目录不存在") from exc
        except (OSError, RuntimeError) as exc:
            raise FileManagerError("路径无法访问") from exc
        if not self._within(resolved, root):
            raise FileAccessDenied("无权访问 Home 目录之外的路径")
        return resolved

    def resolve_entry(self, virtual_path: str, root: Path) -> Path:
        """Resolve a leaf without following it, for rename/delete safety."""
        value = (virtual_path or "/").strip()
        if "\x00" in value:
            raise FileAccessDenied("路径包含非法字符")
        lexical = Path(value.lstrip("/"))
        if not lexical.parts:
            return root
        parent_virtual = "/" + "/".join(lexical.parts[:-1])
        parent = self.resolve(parent_virtual, root)
        candidate = parent / lexical.name
        try:
            candidate.lstat()
        except FileNotFoundError as exc:
            raise FileManagerError("文件或目录不存在") from exc
        return candidate

    @staticmethod
    def virtual_path(path: Path, root: Path) -> str:
        if root == Path("/"):
            return str(path)
        relative = path.relative_to(root)
        return "/" if str(relative) == "." else f"/{relative.as_posix()}"

    def list_directory(
        self,
        virtual_path: str,
        root: Path,
        *,
        show_hidden: bool = False,
        cursor: int = 0,
        limit: int = 100,
    ) -> dict:
        directory = self.resolve(virtual_path, root)
        if not directory.is_dir():
            raise FileManagerError("目标路径不是目录")
        if cursor < 0:
            raise FileManagerError("分页游标无效")
        if limit < 1 or limit > 200:
            raise FileManagerError("每页数量必须在 1 到 200 之间")

        entries = []
        visible_seen = 0
        page_seen = 0
        has_more = False
        try:
            # scandir is consumed lazily. We intentionally keep filesystem
            # iteration order so a huge directory does not need to be fully
            # materialized and sorted before the first page can be returned.
            with os.scandir(directory) as children:
                for child in children:
                    if not show_hidden and child.name.startswith("."):
                        continue
                    if visible_seen < cursor:
                        visible_seen += 1
                        continue
                    if page_seen >= limit:
                        has_more = True
                        break
                    visible_seen += 1
                    page_seen += 1
                    try:
                        info = child.stat(follow_symlinks=False)
                        is_symlink = stat.S_ISLNK(info.st_mode)
                        is_directory = child.is_dir(follow_symlinks=True)
                        child_path = directory / child.name
                        entries.append(
                            {
                                "name": child.name,
                                "path": self.virtual_path(child_path, root),
                                "is_directory": is_directory,
                                "is_symlink": is_symlink,
                                "size": None if is_directory else info.st_size,
                                "modified_at": datetime.fromtimestamp(
                                    info.st_mtime, tz=timezone.utc
                                ).isoformat(),
                                "mode": stat.filemode(info.st_mode),
                            }
                        )
                    except (FileNotFoundError, PermissionError, OSError):
                        continue
        except PermissionError as exc:
            raise FileAccessDenied("没有读取该目录的系统权限") from exc
        except OSError as exc:
            raise FileManagerError("目录读取失败") from exc
        current = self.virtual_path(directory, root)
        parent = None
        if directory != root:
            parent = self.virtual_path(directory.parent, root)
        return {
            "path": current,
            "parent": parent,
            "entries": entries,
            "cursor": cursor,
            "next_cursor": visible_seen if has_more else None,
            "has_more": has_more,
            "limit": limit,
            "show_hidden": show_hidden,
        }

    def read_text(self, virtual_path: str, root: Path) -> dict:
        target = self.resolve(virtual_path, root)
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(target, flags)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise FileManagerError("仅支持编辑普通文本文件")
                if info.st_size > self.max_edit_bytes:
                    raise FileManagerError("文件过大，请下载后使用专用工具编辑")
                content = source.read(self.max_edit_bytes + 1)
        except PermissionError as exc:
            raise FileAccessDenied("没有读取该文件的系统权限") from exc
        except FileManagerError:
            raise
        except OSError as exc:
            raise FileManagerError("文件读取失败") from exc
        if len(content) > self.max_edit_bytes:
            raise FileManagerError("文件过大，请下载后使用专用工具编辑")
        if b"\x00" in content:
            raise FileManagerError("二进制文件不能在线编辑，请下载后处理")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileManagerError("文件不是 UTF-8 文本，不能在线编辑") from exc
        return {
            "path": self.virtual_path(target, root),
            "name": target.name,
            "content": text,
            "size": len(content),
            "max_bytes": self.max_edit_bytes,
        }

    def write_text(self, virtual_path: str, content: str, root: Path) -> None:
        target = self.resolve(virtual_path, root)
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_edit_bytes:
            raise FileManagerError("编辑内容超过大小限制")
        try:
            info = target.stat()
            if not stat.S_ISREG(info.st_mode):
                raise FileManagerError("仅支持编辑普通文本文件")
        except PermissionError as exc:
            raise FileAccessDenied("没有修改该文件的系统权限") from exc
        except FileManagerError:
            raise
        except OSError as exc:
            raise FileManagerError("文件状态读取失败") from exc

        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".edit-", delete=False
            ) as output:
                temporary_name = output.name
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            temporary = Path(temporary_name)
            shutil.copystat(target, temporary, follow_symlinks=False)
            if os.geteuid() == 0:
                os.chown(temporary, info.st_uid, info.st_gid)
            os.replace(temporary, target)
        except PermissionError as exc:
            raise FileAccessDenied("没有修改该文件的系统权限") from exc
        except OSError as exc:
            raise FileManagerError("文件保存失败") from exc
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _validate_name(name: str) -> str:
        value = (name or "").strip()
        if not value or value in {".", ".."} or "/" in value or "\x00" in value:
            raise FileManagerError("名称无效")
        return value

    @staticmethod
    def owner_for(user: dict, ldap_manager) -> tuple[Optional[int], Optional[int]]:
        if user.get("is_admin"):
            return None, None
        record = ldap_manager.get_user(str(user.get("username") or "")) or {}
        try:
            return int(record.get("uid")), int(record.get("gid"))
        except (TypeError, ValueError):
            return None, None

    @staticmethod
    def _apply_owner(path: Path, owner: tuple[Optional[int], Optional[int]]) -> None:
        uid, gid = owner
        if uid is None or gid is None or os.geteuid() != 0:
            return
        os.chown(path, uid, gid, follow_symlinks=False)

    def create_directory(
        self, virtual_parent: str, name: str, root: Path, owner=(None, None)
    ) -> str:
        parent = self.resolve(virtual_parent, root)
        if not parent.is_dir():
            raise FileManagerError("目标路径不是目录")
        destination = parent / self._validate_name(name)
        try:
            destination.mkdir(mode=0o750)
            self._apply_owner(destination, owner)
        except FileExistsError as exc:
            raise FileManagerError("同名文件或目录已存在") from exc
        except PermissionError as exc:
            raise FileAccessDenied("没有在该目录中新建内容的系统权限") from exc
        except OSError as exc:
            raise FileManagerError("目录创建失败") from exc
        return self.virtual_path(destination, root)

    def upload(
        self,
        virtual_parent: str,
        filename: str,
        source: BinaryIO,
        root: Path,
        owner=(None, None),
    ) -> str:
        parent = self.resolve(virtual_parent, root)
        if not parent.is_dir():
            raise FileManagerError("目标路径不是目录")
        destination = parent / self._validate_name(filename)
        if destination.exists() or destination.is_symlink():
            raise FileManagerError("同名文件或目录已存在")

        temporary_name = None
        total = 0
        try:
            with tempfile.NamedTemporaryFile(dir=parent, prefix=".upload-", delete=False) as output:
                temporary_name = output.name
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_upload_bytes:
                        raise FileManagerError("上传文件超过大小限制")
                    output.write(chunk)
            temporary = Path(temporary_name)
            os.chmod(temporary, 0o640)
            self._apply_owner(temporary, owner)
            # A same-filesystem hard link provides atomic no-overwrite
            # semantics; unlike Path.replace(), it cannot replace an entry
            # created between the existence check and this final step.
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError as exc:
                raise FileManagerError("同名文件或目录已存在") from exc
            temporary.unlink()
        except PermissionError as exc:
            raise FileAccessDenied("没有在该目录中上传文件的系统权限") from exc
        except FileManagerError:
            raise
        except OSError as exc:
            raise FileManagerError("文件上传失败") from exc
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
        return self.virtual_path(destination, root)

    def rename(self, virtual_path: str, new_name: str, root: Path) -> str:
        source = self.resolve_entry(virtual_path, root)
        if source == root:
            raise FileManagerError("不能重命名根目录")
        destination = source.parent / self._validate_name(new_name)
        if destination.exists() or destination.is_symlink():
            raise FileManagerError("同名文件或目录已存在")
        try:
            source.rename(destination)
        except PermissionError as exc:
            raise FileAccessDenied("没有重命名该项目的系统权限") from exc
        except OSError as exc:
            raise FileManagerError("重命名失败") from exc
        return self.virtual_path(destination, root)

    def delete(self, virtual_path: str, root: Path) -> None:
        target = self.resolve_entry(virtual_path, root)
        if target == root:
            raise FileManagerError("不能删除根目录")
        try:
            if target.is_symlink() or not target.is_dir():
                target.unlink()
            else:
                shutil.rmtree(target)
        except PermissionError as exc:
            raise FileAccessDenied("没有删除该项目的系统权限") from exc
        except OSError as exc:
            raise FileManagerError("删除失败") from exc
