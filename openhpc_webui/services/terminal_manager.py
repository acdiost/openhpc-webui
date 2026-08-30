"""Pseudo-terminal sessions for the browser terminal."""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import pwd
import signal
import struct
import termios
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


_DISABLED_SHELLS = {"/bin/false", "/sbin/nologin", "/usr/sbin/nologin"}
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"


class TerminalError(RuntimeError):
    """A safe, user-facing terminal session failure."""


@dataclass(frozen=True)
class TerminalIdentity:
    username: str
    uid: int
    gid: int
    home: str
    shell: str


class TerminalSession:
    """One login shell attached to a PTY master."""

    def __init__(self, identity: TerminalIdentity, pid: int, master_fd: int):
        self.identity = identity
        self.pid = pid
        self.master_fd = master_fd
        self._closed = False
        self._close_lock = threading.Lock()

    def read(self, size: int = 65536) -> bytes:
        try:
            return os.read(self.master_fd, size)
        except OSError as exc:
            # Linux reports EIO when the PTY slave has closed.
            if exc.errno in {errno.EIO, errno.EBADF}:
                return b""
            raise

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        view = memoryview(data)
        while view:
            written = os.write(self.master_fd, view)
            view = view[written:]

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        dimensions = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, dimensions)

    def close(self) -> None:
        """Close the PTY, terminate its process group, and reap the child."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                os.close(self.master_fd)
            except OSError:
                pass

            try:
                os.killpg(self.pid, signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass

            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                try:
                    waited, _ = os.waitpid(self.pid, os.WNOHANG)
                except ChildProcessError:
                    return
                if waited == self.pid:
                    return
                time.sleep(0.02)

            try:
                os.killpg(self.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass


class TerminalManager:
    """Resolve system identities and enforce per-user session limits."""

    def __init__(self, max_sessions_per_user: int = 2):
        self.max_sessions_per_user = max_sessions_per_user
        self._active: Dict[str, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def resolve_identity(username: str) -> TerminalIdentity:
        if not username:
            raise TerminalError("用户信息缺失")
        try:
            record = pwd.getpwnam(username)
        except KeyError as exc:
            raise TerminalError("当前 LDAP 用户尚未同步为系统账户") from exc

        shell = record.pw_shell or "/bin/bash"
        if shell in _DISABLED_SHELLS:
            raise TerminalError("当前账户已禁用终端登录")
        if not Path(shell).is_absolute() or not os.access(shell, os.X_OK):
            raise TerminalError("当前账户未配置可执行的登录 Shell")
        if not record.pw_dir or not Path(record.pw_dir).is_dir():
            raise TerminalError("当前账户的 Home 目录不存在")

        process_uid = os.geteuid()
        if process_uid not in {0, record.pw_uid}:
            raise TerminalError("服务进程无权切换到当前系统账户")
        return TerminalIdentity(
            username=username,
            uid=record.pw_uid,
            gid=record.pw_gid,
            home=record.pw_dir,
            shell=shell,
        )

    def open(self, username: str, cols: int = 120, rows: int = 32) -> TerminalSession:
        identity = self.resolve_identity(username)
        with self._lock:
            active = self._active.get(username, 0)
            if active >= self.max_sessions_per_user:
                raise TerminalError("已达到当前用户的终端会话数量上限")
            self._active[username] = active + 1

        session: Optional[TerminalSession] = None
        try:
            session = self._spawn(identity)
            session.resize(cols, rows)
            return session
        except BaseException:
            if session is not None:
                session.close()
            self._release(username)
            raise

    @staticmethod
    def _spawn(identity: TerminalIdentity) -> TerminalSession:
        pid, master_fd = pty.fork()
        if pid:
            return TerminalSession(identity, pid, master_fd)

        try:
            if os.geteuid() == 0:
                os.initgroups(identity.username, identity.gid)
                os.setgid(identity.gid)
                os.setuid(identity.uid)
            os.chdir(identity.home)
            environment = {
                "COLORTERM": "truecolor",
                "HOME": identity.home,
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "LOGNAME": identity.username,
                "PATH": _DEFAULT_PATH,
                "SHELL": identity.shell,
                "TERM": "xterm-256color",
                "USER": identity.username,
            }
            executable_name = Path(identity.shell).name
            os.execve(identity.shell, [f"-{executable_name}"], environment)
        except BaseException:
            try:
                os.write(2, b"Unable to start the login shell.\r\n")
            finally:
                os._exit(126)

    def close(self, session: Optional[TerminalSession]) -> None:
        if session is None:
            return
        try:
            session.close()
        finally:
            self._release(session.identity.username)

    def _release(self, username: str) -> None:
        with self._lock:
            active = self._active.get(username, 0)
            if active <= 1:
                self._active.pop(username, None)
            else:
                self._active[username] = active - 1
