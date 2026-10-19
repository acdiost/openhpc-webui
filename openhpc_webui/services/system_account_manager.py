"""Local Linux account and group management for single-node deployments."""

from __future__ import annotations

import grp
import os
import pwd
import re
import secrets
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from ..audit import structured_print as print
from .integration_timeout import bounded_timeout_seconds


_ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SSH_PUBLIC_KEY = re.compile(
    r"^(?:ssh-(?:rsa|ed25519)|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/=]+(?: .*)?$"
)


def _safe_chpasswd_value(password: Optional[str]) -> bool:
    return password is None or not any(char in password for char in "\x00\r\n")


class SystemAccountManager:
    """Expose local NSS accounts through the directory-manager interface."""

    def __init__(self) -> None:
        self.command_timeout = bounded_timeout_seconds(
            "SYSTEM_ACCOUNT_COMMAND_TIMEOUT_SECONDS", 30
        )
        try:
            self.minimum_uid = max(0, int(os.getenv("SYSTEM_USER_MIN_UID", "1000")))
        except ValueError:
            self.minimum_uid = 1000

    def _run(self, args: List[str], *, input_text: Optional[str] = None) -> bool:
        try:
            result = subprocess.run(
                args,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=self.command_timeout,
                check=False,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"系统账户命令执行失败: {args[0]}: {exc}")
            return False
        if result.returncode != 0:
            print(f"系统账户命令返回失败: {args[0]}: {result.stderr.strip()}")
            return False
        return True

    @staticmethod
    def _groups_for(record) -> List[str]:
        groups = {
            group.gr_name
            for group in grp.getgrall()
            if group.gr_gid == record.pw_gid or record.pw_name in group.gr_mem
        }
        return sorted(groups)

    @classmethod
    def _user_dict(cls, record) -> Dict:
        gecos = (record.pw_gecos or "").split(",")
        display_name = gecos[0].strip() if gecos else ""
        phone = gecos[2].strip() if len(gecos) > 2 else ""
        email = gecos[4].strip() if len(gecos) > 4 else ""
        return {
            "dn": f"system:{record.pw_name}",
            "username": record.pw_name,
            "uid": str(record.pw_uid),
            "gid": str(record.pw_gid),
            "home": record.pw_dir,
            "shell": record.pw_shell,
            "cn": display_name or record.pw_name,
            "sn": display_name or record.pw_name,
            "phone": phone,
            "email": email,
            "groups": cls._groups_for(record),
        }

    def check_connection(self) -> Dict[str, str]:
        return {"status": "connected", "server": "local system", "backend": "system"}

    def list_users(self, search_filter: str = "") -> List[Dict]:
        del search_filter
        admin_users = {
            value.strip()
            for value in os.getenv("ADMIN_USERS", "").split(",")
            if value.strip()
        }
        return [
            self._user_dict(record)
            for record in pwd.getpwall()
            if record.pw_uid >= self.minimum_uid or record.pw_name in admin_users
        ]

    def get_user(self, username: str) -> Optional[Dict]:
        try:
            return self._user_dict(pwd.getpwnam(username))
        except KeyError:
            return None

    def get_user_auth_state(self, username: str) -> Optional[tuple[str, str]]:
        try:
            record = pwd.getpwnam(username)
        except KeyError:
            return None
        identity = f"system:{record.pw_uid}:{record.pw_gid}:{record.pw_dir}"
        return record.pw_shell, identity

    def create_user(
        self,
        username: str,
        uid: int,
        gid: int,
        home: str,
        shell: str = "/bin/bash",
        password: str = None,
        sn: str = None,
        phone: str = None,
        email: str = None,
    ) -> bool:
        if not _ACCOUNT_NAME.fullmatch(username) or not _safe_chpasswd_value(password):
            return False
        args = [
            "useradd", "--uid", str(uid), "--gid", str(gid),
            "--home-dir", home, "--shell", shell, "--create-home",
        ]
        if any(value and value.strip() for value in (sn, phone, email)):
            gecos = f"{(sn or username).strip()},,{(phone or '').strip()},,{(email or '').strip()}"
            args.extend(["--comment", gecos])
        args.append(username)
        if not self._run(args):
            return False
        if password is not None and not self._run(
            ["chpasswd"], input_text=f"{username}:{password}\n"
        ):
            self._run(["userdel", "--remove", username])
            return False
        return True

    def delete_user(self, username: str) -> bool:
        return bool(_ACCOUNT_NAME.fullmatch(username)) and self._run(
            ["userdel", "--remove", username]
        )

    def update_user(
        self,
        username: str,
        gid: int = None,
        home: str = None,
        shell: str = None,
        password: str = None,
        sn: str = None,
        phone: str = None,
        email: str = None,
    ) -> bool:
        if not _ACCOUNT_NAME.fullmatch(username) or not _safe_chpasswd_value(password):
            return False
        args = ["usermod"]
        if gid is not None:
            args.extend(["--gid", str(gid)])
        if home is not None:
            args.extend(["--home", home, "--move-home"])
        if shell is not None:
            args.extend(["--shell", shell])
        if any(value is not None for value in (sn, phone, email)):
            try:
                existing = (pwd.getpwnam(username).pw_gecos or "").split(",")
            except KeyError:
                return False
            existing.extend([""] * (5 - len(existing)))
            if sn is not None:
                existing[0] = sn.strip() or username
            if phone is not None:
                existing[2] = phone.strip()
            if email is not None:
                existing[4] = email.strip()
            args.extend(["--comment", ",".join(existing[:5])])
        if len(args) > 1:
            args.append(username)
            if not self._run(args):
                return False
        if password is not None:
            return self._run(["chpasswd"], input_text=f"{username}:{password}\n")
        return True

    def list_groups(self, search_filter: str = "") -> List[Dict]:
        del search_filter
        users = pwd.getpwall()
        groups = []
        for group in grp.getgrall():
            if group.gr_gid < self.minimum_uid:
                continue
            members = set(group.gr_mem)
            members.update(user.pw_name for user in users if user.pw_gid == group.gr_gid)
            groups.append(
                {
                    "dn": f"system:{group.gr_name}",
                    "name": group.gr_name,
                    "gid": str(group.gr_gid),
                    "members": sorted(members),
                    "description": "",
                }
            )
        return groups

    def get_group(self, group_name: str) -> Optional[Dict]:
        try:
            group = grp.getgrnam(group_name)
        except KeyError:
            return None
        members = set(group.gr_mem)
        members.update(
            user.pw_name for user in pwd.getpwall() if user.pw_gid == group.gr_gid
        )
        return {
            "dn": f"system:{group.gr_name}",
            "name": group.gr_name,
            "gid": str(group.gr_gid),
            "members": sorted(members),
            "description": "",
        }

    def create_group(self, group_name: str, gid: int, description: str = "") -> bool:
        del description
        return bool(_ACCOUNT_NAME.fullmatch(group_name)) and self._run(
            ["groupadd", "--gid", str(gid), group_name]
        )

    def update_group(
        self, group_name: str, gid: int = None, description: str = None
    ) -> bool:
        del description
        if not _ACCOUNT_NAME.fullmatch(group_name) or gid is None:
            return bool(_ACCOUNT_NAME.fullmatch(group_name))
        return self._run(["groupmod", "--gid", str(gid), group_name])

    def delete_group(self, group_name: str) -> bool:
        return bool(_ACCOUNT_NAME.fullmatch(group_name)) and self._run(
            ["groupdel", group_name]
        )

    def add_user_to_group(self, username: str, group_name: str) -> bool:
        if not _ACCOUNT_NAME.fullmatch(username) or not _ACCOUNT_NAME.fullmatch(group_name):
            return False
        return self._run(["gpasswd", "--add", username, group_name])

    def remove_user_from_group(self, username: str, group_name: str) -> bool:
        if not _ACCOUNT_NAME.fullmatch(username) or not _ACCOUNT_NAME.fullmatch(group_name):
            return False
        return self._run(["gpasswd", "--delete", username, group_name])

    def set_ssh_public_key(self, username: str, public_key: str) -> bool:
        if "\n" in public_key or not _SSH_PUBLIC_KEY.fullmatch(public_key.strip()):
            return False
        home_fd = -1
        ssh_fd = -1
        key_fd = -1
        temporary_name = ""
        try:
            record = pwd.getpwnam(username)
            home = Path(record.pw_dir)
            if not home.is_absolute():
                return False
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            home_fd = os.open(str(home), directory_flags)
            try:
                os.mkdir(".ssh", mode=0o700, dir_fd=home_fd)
            except FileExistsError:
                pass
            ssh_fd = os.open(".ssh", directory_flags, dir_fd=home_fd)
            os.fchmod(ssh_fd, 0o700)
            os.fchown(ssh_fd, record.pw_uid, record.pw_gid)

            temporary_name = f".authorized_keys-{secrets.token_hex(12)}"
            key_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=ssh_fd,
            )
            os.fchown(key_fd, record.pw_uid, record.pw_gid)
            key_handle = os.fdopen(key_fd, "w", encoding="utf-8")
            key_fd = -1
            with key_handle as handle:
                handle.write(public_key.strip() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.rename(
                temporary_name,
                "authorized_keys",
                src_dir_fd=ssh_fd,
                dst_dir_fd=ssh_fd,
            )
            temporary_name = ""
            return True
        except (KeyError, OSError) as exc:
            print(f"写入系统用户 SSH 公钥失败: {exc}")
            return False
        finally:
            if temporary_name and ssh_fd >= 0:
                try:
                    os.unlink(temporary_name, dir_fd=ssh_fd)
                except OSError:
                    pass
            if key_fd >= 0:
                os.close(key_fd)
            if ssh_fd >= 0:
                os.close(ssh_fd)
            if home_fd >= 0:
                os.close(home_fd)
