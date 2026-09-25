"""Descriptor-confined filesystem operations and isolated user-credential workers."""

import errno
import io
import json
import os
import pwd
import secrets
import shutil
import socket
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO


class FileManagerError(Exception):
    """A safe, user-facing filesystem failure."""


class FileAccessDenied(FileManagerError):
    """A path or POSIX permission prevents access."""


_NOFOLLOW = os.O_NOFOLLOW
_DIRECTORY = os.O_DIRECTORY
_TRAVERSE = getattr(os, "O_PATH", os.O_RDONLY)
_CODE_ROOT = str(Path(__file__).resolve().parents[2])


class FileManager:
    """Operate within a pinned virtual root, never following an unchecked pathname."""

    def __init__(self, max_upload_bytes=1024 * 1024 * 1024, max_edit_bytes=2 * 1024 * 1024):
        self.max_upload_bytes = max_upload_bytes
        self.max_edit_bytes = max_edit_bytes

    def scope_root(self, user: dict, ldap_manager) -> Path:
        if user.get("is_admin"):
            return Path("/")
        username = str(user.get("username") or "").strip()
        record = ldap_manager.get_user(username) if username else None
        home = str((record or {}).get("home") or "").strip()
        if not home or not Path(home).is_absolute():
            raise FileManagerError("用户未配置有效的 Home 目录")
        try:
            root = Path(home).resolve(strict=True)
            if not root.is_dir():
                raise FileManagerError("用户 Home 路径不是目录")
        except (OSError, RuntimeError) as exc:
            raise FileManagerError("用户 Home 目录不存在或不可访问") from exc
        return root

    def for_user(self, user: dict, ldap_manager):
        """Admins retain the portal identity; everyone else uses a fresh OS process."""
        if user.get("is_admin"):
            return self
        username = str(user.get("username") or "").strip()
        record = ldap_manager.get_user(username) if username else None
        try:
            uid, gid = int(record["uid"]), int(record["gid"])
        except (TypeError, ValueError, KeyError) as exc:
            raise FileAccessDenied("用户缺少有效的系统 UID/GID") from exc
        if uid < 0 or gid < 0 or (os.geteuid() == 0 and uid == 0):
            raise FileAccessDenied("用户缺少有效的系统 UID/GID")
        return _UserFiles(self, username, uid, gid)

    @staticmethod
    def _parts(value: str, base=()):
        parts = list(base)
        for part in value.split("/"):
            if not part or part == ".":
                continue
            if part == "..":
                if not parts:
                    raise FileAccessDenied("无权访问 Home 目录之外的路径")
                parts.pop()
            elif "\x00" in part:
                raise FileAccessDenied("路径包含非法字符")
            else:
                parts.append(part)
        return parts

    @contextmanager
    def _root_fd(self, root: Path):
        """Open even the root's ancestors without following a raced symlink."""
        fd = os.open("/", _TRAVERSE | _DIRECTORY)
        try:
            for part in Path(root).parts[1:]:
                next_fd = os.open(part, _TRAVERSE | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
        except PermissionError as exc:
            os.close(fd)
            raise FileAccessDenied("没有访问该目录的系统权限") from exc
        except OSError as exc:
            os.close(fd)
            raise FileManagerError("文件管理根目录不存在或不可访问") from exc
        try:
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _item(self, value: str, root: Path, *, directory=False, traverse=False, access=os.O_RDONLY):
        """Follow in-scope symlinks by readlinkat, opening every component O_NOFOLLOW.

        Relative links are interpreted against the pinned parent; absolute links
        must lexically remain under the canonical virtual root. No check/open gap
        or ambient process-cwd resolution occurs for any component.
        """
        pending = self._parts((value or "/").strip())
        resolved = []
        depth = 0
        with self._root_fd(root) as root_fd:
            fd = os.dup(root_fd)
            try:
                while pending:
                    part = pending.pop(0)
                    flags = (_TRAVERSE if pending or traverse else access) | _NOFOLLOW
                    if not pending and not traverse:
                        flags |= getattr(os, "O_NONBLOCK", 0)
                    if pending or directory:
                        flags |= _DIRECTORY
                    try:
                        next_fd = os.open(part, flags, dir_fd=fd)
                    except OSError as exc:
                        if exc.errno not in (errno.ELOOP, errno.ENOTDIR) or not stat.S_ISLNK(
                            os.stat(part, dir_fd=fd, follow_symlinks=False).st_mode
                        ):
                            raise
                        depth += 1
                        if depth > 40:
                            raise FileManagerError("路径包含过多符号链接") from exc
                        link = os.readlink(part, dir_fd=fd)
                        if link.startswith("/"):
                            target = Path(link)
                            if root != Path("/"):
                                try:
                                    target = target.relative_to(root)
                                except ValueError as error:
                                    raise FileAccessDenied("无权访问 Home 目录之外的路径") from error
                            resolved = []
                            link = str(target)
                        pending = self._parts(link, resolved) + pending
                        resolved = []
                        os.close(fd)
                        fd = os.dup(root_fd)
                        continue
                    os.close(fd)
                    fd = next_fd
                    resolved.append(part)
                if not pending and directory and not resolved and not traverse:
                    opened = os.open(".", os.O_RDONLY | _DIRECTORY, dir_fd=fd)
                    os.close(fd)
                    fd = opened
                yield fd, tuple(resolved)
            except PermissionError as exc:
                raise FileAccessDenied("没有访问该路径的系统权限") from exc
            except FileManagerError:
                raise
            except OSError as exc:
                raise FileManagerError("文件或目录不存在或无法访问") from exc
            finally:
                os.close(fd)

    @contextmanager
    def _parent(self, value: str, root: Path):
        parts = self._parts((value or "/").strip())
        if not parts:
            raise FileManagerError("不能操作根目录")
        with self._item("/" + "/".join(parts[:-1]), root, directory=True, traverse=True) as (fd, canonical):
            yield fd, canonical, parts[-1]

    def resolve(self, virtual_path: str, root: Path, *, strict: bool = True) -> Path:
        # Retained for callers that need a display path; NEVER use its result to open.
        with self._item(virtual_path, root) as (_, parts):
            return root.joinpath(*parts)

    @staticmethod
    def virtual_path(path: Path, root: Path) -> str:
        if root == Path("/"):
            return str(path)
        relative = path.relative_to(root)
        return "/" if str(relative) == "." else f"/{relative.as_posix()}"

    def list_directory(self, virtual_path: str, root: Path, *, show_hidden=False, cursor=0, limit=100):
        if cursor < 0:
            raise FileManagerError("分页游标无效")
        if limit < 1 or limit > 200:
            raise FileManagerError("每页数量必须在 1 到 200 之间")
        with self._item(virtual_path, root, directory=True) as (fd, parts):
            entries = []
            visible = 0
            has_more = False
            try:
                with os.scandir(fd) as children:
                    for child in children:
                        if not show_hidden and child.name.startswith("."):
                            continue
                        if visible < cursor:
                            visible += 1
                            continue
                        if len(entries) >= limit:
                            has_more = True
                            break
                        visible += 1
                        try:
                            info = child.stat(follow_symlinks=False)
                            is_link = stat.S_ISLNK(info.st_mode)
                            is_dir = stat.S_ISDIR(info.st_mode)
                            if is_link:
                                try:
                                    with self._item("/" + "/".join((*parts, child.name)), root) as (target_fd, _):
                                        is_dir = stat.S_ISDIR(os.fstat(target_fd).st_mode)
                                except FileManagerError:
                                    pass
                            entries.append({
                                "name": child.name,
                                "path": self.virtual_path(root.joinpath(*parts, child.name), root),
                                "is_directory": is_dir,
                                "is_symlink": is_link,
                                "size": None if is_dir else info.st_size,
                                "modified_at": datetime.fromtimestamp(info.st_mtime, tz=timezone.utc).isoformat(),
                                "mode": stat.filemode(info.st_mode),
                            })
                        except OSError:
                            continue
            except PermissionError as exc:
                raise FileAccessDenied("没有读取该目录的系统权限") from exc
            except OSError as exc:
                raise FileManagerError("目录读取失败") from exc
            return {
                "path": self.virtual_path(root.joinpath(*parts), root),
                "parent": self.virtual_path(root.joinpath(*parts[:-1]), root) if parts else None,
                "entries": entries,
                "cursor": cursor,
                "next_cursor": visible if has_more else None,
                "has_more": has_more,
                "limit": limit,
                "show_hidden": show_hidden,
            }

    def read_text(self, virtual_path: str, root: Path) -> dict:
        with self._item(virtual_path, root) as (fd, parts):
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise FileManagerError("仅支持编辑普通文本文件")
            if info.st_size > self.max_edit_bytes:
                raise FileManagerError("文件过大，请下载后使用专用工具编辑")
            with os.fdopen(os.dup(fd), "rb") as source:
                content = source.read(self.max_edit_bytes + 1)
        if len(content) > self.max_edit_bytes:
            raise FileManagerError("文件过大，请下载后使用专用工具编辑")
        if b"\x00" in content:
            raise FileManagerError("二进制文件不能在线编辑，请下载后处理")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileManagerError("文件不是 UTF-8 文本，不能在线编辑") from exc
        return {"path": self.virtual_path(root.joinpath(*parts), root), "name": parts[-1] if parts else root.name,
                "content": text, "size": len(content), "max_bytes": self.max_edit_bytes}

    def write_text(self, virtual_path: str, content: str, root: Path) -> None:
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_edit_bytes:
            raise FileManagerError("编辑内容超过大小限制")
        # A symlink to an in-scope file is permitted; the resolved filename is
        # pinned to its parent directory before replacement.
        with self._item(virtual_path, root, access=os.O_WRONLY) as (fd, parts):
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise FileManagerError("仅支持编辑普通文本文件")
            with self._parent("/" + "/".join(parts), root) as (parent_fd, _, name):
                # O_WRONLY checks the *file's* permissions, not just its parent.
                try:
                    writable = os.open(name, os.O_WRONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=parent_fd)
                    try:
                        current = os.fstat(writable)
                        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                            raise FileManagerError("文件已发生变化，请重试")
                    finally:
                        os.close(writable)
                    temporary = self._temporary(parent_fd, ".edit-")
                    try:
                        with os.fdopen(os.dup(temporary[0]), "wb") as output:
                            output.write(encoded)
                            output.flush()
                            os.fsync(output.fileno())
                        os.fchmod(temporary[0], stat.S_IMODE(info.st_mode) & 0o777)
                        if os.geteuid() == 0:
                            os.fchown(temporary[0], info.st_uid, info.st_gid)
                        os.replace(temporary[1], name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    finally:
                        os.close(temporary[0])
                        self._unlink_temp(parent_fd, temporary[1])
                except PermissionError as exc:
                    raise FileAccessDenied("没有修改该文件的系统权限") from exc

    @staticmethod
    def _validate_name(name: str) -> str:
        value = (name or "").strip()
        if not value or value in {".", ".."} or "/" in value or "\x00" in value:
            raise FileManagerError("名称无效")
        return value

    @staticmethod
    def _temporary(parent_fd, prefix):
        for _ in range(10):
            name = prefix + secrets.token_hex(12)
            try:
                return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=parent_fd), name
            except FileExistsError:
                continue
        raise FileManagerError("无法创建临时文件")

    @staticmethod
    def _unlink_temp(fd, name):
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass

    def create_directory(self, virtual_parent: str, name: str, root: Path) -> str:
        name = self._validate_name(name)
        with self._item(virtual_parent, root, directory=True, traverse=True) as (fd, parts):
            try:
                os.mkdir(name, 0o750, dir_fd=fd)
            except FileExistsError as exc:
                raise FileManagerError("同名文件或目录已存在") from exc
            except PermissionError as exc:
                raise FileAccessDenied("没有在该目录中新建内容的系统权限") from exc
            return self.virtual_path(root.joinpath(*parts, name), root)

    def upload(self, virtual_parent: str, filename: str, source: BinaryIO, root: Path) -> str:
        name = self._validate_name(filename)
        with self._item(virtual_parent, root, directory=True, traverse=True) as (fd, parts):
            try:
                temporary_fd, temporary_name = self._temporary(fd, ".upload-")
                try:
                    total = 0
                    with os.fdopen(os.dup(temporary_fd), "wb") as output:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > self.max_upload_bytes:
                                raise FileManagerError("上传文件超过大小限制")
                            output.write(chunk)
                    os.fchmod(temporary_fd, 0o640)
                    os.link(temporary_name, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                finally:
                    os.close(temporary_fd)
                    self._unlink_temp(fd, temporary_name)
            except FileExistsError as exc:
                raise FileManagerError("同名文件或目录已存在") from exc
            except PermissionError as exc:
                raise FileAccessDenied("没有在该目录中上传文件的系统权限") from exc
            return self.virtual_path(root.joinpath(*parts, name), root)

    def rename(self, virtual_path: str, new_name: str, root: Path) -> str:
        if not self._parts((virtual_path or "/").strip()):
            raise FileManagerError("不能重命名根目录")
        name = self._validate_name(new_name)
        with self._parent(virtual_path, root) as (fd, parts, old):
            try:
                os.stat(old, dir_fd=fd, follow_symlinks=False)
                try:
                    os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise FileManagerError("同名文件或目录已存在")
                os.rename(old, name, src_dir_fd=fd, dst_dir_fd=fd)
            except PermissionError as exc:
                raise FileAccessDenied("没有重命名该项目的系统权限") from exc
            return self.virtual_path(root.joinpath(*parts, name), root)

    @classmethod
    def _remove_tree(cls, parent_fd, name):
        fd = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=parent_fd)
        try:
            with os.scandir(fd) as entries:
                for child in entries:
                    if child.is_dir(follow_symlinks=False):
                        cls._remove_tree(fd, child.name)
                    else:
                        os.unlink(child.name, dir_fd=fd)
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=parent_fd)

    def delete(self, virtual_path: str, root: Path) -> None:
        if not self._parts((virtual_path or "/").strip()):
            raise FileManagerError("不能删除根目录")
        with self._parent(virtual_path, root) as (fd, _, name):
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    self._remove_tree(fd, name)
                else:
                    os.unlink(name, dir_fd=fd)
            except PermissionError as exc:
                raise FileAccessDenied("没有删除该项目的系统权限") from exc

    @contextmanager
    def open_download(self, virtual_path: str, root: Path):
        with self._item(virtual_path, root) as (fd, parts):
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise FileManagerError("目标路径不是文件")
            with os.fdopen(os.dup(fd), "rb") as source:
                yield source, (parts[-1] if parts else root.name)


def _stop_worker(proc):
    """Reap a worker even if its socket is abandoned during a disconnect."""
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)


class _FramedSource:
    """Require an explicit end marker before committing a streamed edit/upload."""

    def __init__(self, wire):
        self.wire = wire

    def read(self, size):
        header = self.wire.read(4)
        if len(header) != 4:
            raise FileManagerError("上传数据传输中断")
        length = int.from_bytes(header, "big")
        if length > min(size, 1024 * 1024):
            raise FileManagerError("上传数据帧无效")
        if not length:
            return b""
        data = self.wire.read(length)
        if len(data) != length:
            raise FileManagerError("上传数据传输中断")
        return data


class _UserFiles:
    """Request-scoped adapter that runs each operation in a dropped-credential child."""

    def __init__(self, manager, username, uid, gid):
        self.manager, self.username, self.uid, self.gid = manager, username, uid, gid

    def _request(self, action, root, args, source=None):
        parent, child = socket.socketpair()
        command = [sys.executable, "-I", "-c",
                   "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                   "from openhpc_webui.services.file_manager import _worker; _worker()",
                   _CODE_ROOT, str(child.fileno()), self.username, str(self.uid), str(self.gid),
                   str(root), action, str(self.manager.max_upload_bytes),
                   str(self.manager.max_edit_bytes), json.dumps(args)]
        try:
            proc = subprocess.Popen(command, pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    cwd="/", env={"PATH": os.defpath, "LANG": "C.UTF-8"})
        except OSError as exc:
            parent.close()
            child.close()
            raise FileManagerError("无法启动用户文件服务") from exc
        child.close()
        reader = parent.makefile("rb")
        streaming = False
        try:
            if source is not None:
                total = 0
                limit = self.manager.max_upload_bytes if action == "upload" else self.manager.max_edit_bytes
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise FileManagerError("上传文件超过大小限制" if action == "upload" else "编辑内容超过大小限制")
                    parent.sendall(len(chunk).to_bytes(4, "big"))
                    parent.sendall(chunk)
                parent.sendall(b"\x00\x00\x00\x00")
            parent.shutdown(socket.SHUT_WR)
            header_limit = 1024 * 1024
            if action == "read":
                header_limit = max(header_limit, 6 * self.manager.max_edit_bytes + 65536)
            header = reader.readline(header_limit + 1)
            if not header.endswith(b"\n"):
                raise FileManagerError("用户文件服务异常结束")
            response = json.loads(header)
            if "error" in response:
                error = FileAccessDenied if response.get("denied") else FileManagerError
                raise error(response["error"])
            if action == "download":
                streaming = True
                return reader, parent, proc, response["name"]
            return response["result"]
        except (OSError, ValueError) as exc:
            raise FileManagerError("用户文件服务异常结束") from exc
        finally:
            if not streaming:
                reader.close()
                parent.close()
                _stop_worker(proc)

    def list_directory(self, path, root, *, show_hidden=False, cursor=0, limit=100):
        return self._request("list", root, [path, show_hidden, cursor, limit])

    def read_text(self, path, root):
        return self._request("read", root, [path])

    def write_text(self, path, content, root):
        self._request("write", root, [path], source=io.BytesIO(content.encode("utf-8")))

    def create_directory(self, path, name, root):
        return self._request("mkdir", root, [path, name])

    def upload(self, path, filename, source, root):
        return self._request("upload", root, [path, filename], source=source)

    def rename(self, path, name, root):
        return self._request("rename", root, [path, name])

    def delete(self, path, root):
        self._request("delete", root, [path])

    @contextmanager
    def open_download(self, path, root):
        reader, conn, proc, name = self._request("download", root, [path])
        try:
            yield reader, name
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            reader.close()
            conn.close()
            _stop_worker(proc)


def _worker():
    sock_fd, username, uid, gid, root, action, upload_limit, edit_limit, raw_args = sys.argv[1:]
    sock = socket.socket(fileno=int(sock_fd))
    try:
        uid, gid = int(uid), int(gid)
        if os.geteuid() == 0:
            identity = pwd.getpwnam(username)
            if identity.pw_uid != uid or identity.pw_gid != gid or uid == 0:
                raise FileAccessDenied("用户系统身份与 LDAP 记录不一致")
            os.setgroups(os.getgrouplist(username, gid))
            os.setgid(gid)
            os.setuid(uid)
        else:
            if (os.getuid(), os.geteuid(), os.getegid()) != (uid, uid, gid):
                raise FileAccessDenied("文件服务无法切换至用户身份")
            local = pwd.getpwuid(uid)
            if local.pw_gid != gid or not set(os.getgroups()).issubset(
                os.getgrouplist(local.pw_name, gid)
            ):
                raise FileAccessDenied("用户系统身份与 LDAP 记录不一致")
        manager = FileManager(int(upload_limit), int(edit_limit))
        root = Path(root)
        args = json.loads(raw_args)
        if action == "list":
            result = manager.list_directory(args[0], root, show_hidden=args[1], cursor=args[2], limit=args[3])
        elif action == "read":
            result = manager.read_text(args[0], root)
        elif action == "write":
            with sock.makefile("rb") as wire:
                source = _FramedSource(wire)
                chunks = []
                total = 0
                while True:
                    chunk = source.read(min(1024 * 1024, manager.max_edit_bytes + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > manager.max_edit_bytes:
                        raise FileManagerError("编辑内容超过大小限制")
                    chunks.append(chunk)
            manager.write_text(args[0], b"".join(chunks).decode("utf-8"), root)
            result = None
        elif action == "mkdir":
            result = manager.create_directory(args[0], args[1], root)
        elif action == "upload":
            with sock.makefile("rb") as wire:
                result = manager.upload(args[0], args[1], _FramedSource(wire), root)
        elif action == "rename":
            result = manager.rename(args[0], args[1], root)
        elif action == "delete":
            result = manager.delete(args[0], root)
        elif action == "download":
            with manager.open_download(args[0], root) as (source, name):
                sock.sendall(json.dumps({"name": name}).encode() + b"\n")
                with source:
                    shutil.copyfileobj(source, sock.makefile("wb", buffering=0), 1024 * 1024)
            return
        else:
            raise FileManagerError("无效文件操作")
        sock.sendall(json.dumps({"result": result}).encode() + b"\n")
    except (FileManagerError, OSError, KeyError, UnicodeError) as exc:
        error = str(exc) if isinstance(exc, FileManagerError) else "文件操作失败"
        try:
            sock.sendall(json.dumps({"error": error, "denied": isinstance(exc, (FileAccessDenied, PermissionError))}).encode() + b"\n")
        except OSError:
            pass
    finally:
        sock.close()

