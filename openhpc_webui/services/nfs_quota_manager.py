import os
import re
import subprocess
from typing import Dict, Optional
from ..audit import structured_print as print


class NFSQuotaManager:
    """读取和设置指定文件系统上的用户存储配额。"""

    def __init__(self) -> None:
        # 启用 quota 的文件系统挂载点，如 /、/home 或 /data。
        configured_fs = os.getenv("NFS_QUOTA_FS", "").strip()
        self.quota_fs = os.path.realpath(configured_fs) if configured_fs else ""
        self._quota_ready: Optional[bool] = None

    def is_enabled(self) -> bool:
        """Return whether the configured filesystem has readable user quotas."""
        if not self.quota_fs:
            return False
        if self._quota_ready is not None:
            return self._quota_ready
        if not self._quota_filesystem_identifiers():
            self._quota_ready = False
            return False

        try:
            result = subprocess.run(
                self._quota_command("root"),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            print(f"检查用户配额状态失败: {exc}")
            self._quota_ready = False
            return False

        self._quota_ready = (
            result.returncode == 0 and self._parse_quota_output(result.stdout) is not None
        )
        if not self._quota_ready:
            detail = (result.stderr or result.stdout).strip()
            print(f"文件系统 {self.quota_fs} 未启用用户配额: {detail}")
        return self._quota_ready

    def get_user_quota(self, username: str) -> Optional[Dict[str, float]]:
        """读取用户配额与使用量（GB）。返回 None 表示不可用或未配置。"""
        if not self.is_enabled():
            return None

        try:
            result = subprocess.run(
                self._quota_command(username),
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except FileNotFoundError:
            print("quota 命令不存在，无法获取 NFS 配额")
            return None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            print(f"获取用户配额失败: {detail.strip()}")
            return None

        quota_row = self._parse_quota_output(result.stdout)
        if not quota_row:
            return None

        blocks_used_kb = quota_row.get("blocks_used_kb", 0)
        soft_kb = quota_row.get("blocks_soft_kb", 0)
        hard_kb = quota_row.get("blocks_hard_kb", 0)
        limit_kb = hard_kb if hard_kb > 0 else soft_kb

        return {
            "used_gb": self._kb_to_gb(blocks_used_kb),
            "limit_gb": self._kb_to_gb(limit_kb) if limit_kb > 0 else 0.0,
        }

    def set_user_quota(self, username: str, quota_gb: Optional[float]) -> bool:
        """设置用户配额（GB）。None 或 <=0 表示不限制。"""
        if not self.is_enabled():
            print("NFS_QUOTA_FS 未配置，跳过设置配额")
            return False

        soft_kb = 0
        hard_kb = 0
        if quota_gb is not None and quota_gb > 0:
            quota_kb = int(quota_gb * 1024 * 1024)
            soft_kb = quota_kb
            hard_kb = quota_kb

        try:
            subprocess.run(
                [
                    "setquota",
                    "-u",
                    username,
                    str(soft_kb),
                    str(hard_kb),
                    "0",
                    "0",
                    self.quota_fs,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            return True
        except FileNotFoundError:
            print("setquota 命令不存在，无法设置 NFS 配额")
            return False
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            print(f"设置用户配额失败: {detail.strip()}")
            return False

    def _quota_command(self, username: str) -> list:
        # CentOS 7 ships quota-tools 4.01, which supports -w but not the
        # --filesystem option (added in quota-tools 4.06).  Query all mounted
        # quota filesystems and select quota_fs while parsing the output.
        return [
            "quota",
            "-w",
            "-v",
            "-u",
            username,
        ]

    def _parse_quota_output(self, output: str) -> Optional[Dict[str, int]]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return None

        data_rows = []
        pending_filesystem = None
        for line in lines:
            lower = line.lower()
            if lower.startswith("disk quotas for"):
                continue
            if lower.startswith("filesystem"):
                continue
            parts = line.split()
            if len(parts) == 1 and not self._is_quota_number(parts[0]):
                pending_filesystem = parts[0]
                continue
            if pending_filesystem and parts and self._is_quota_number(parts[0]):
                parts.insert(0, pending_filesystem)
                pending_filesystem = None
            if len(parts) >= 4:
                data_rows.append(parts)

        if not data_rows:
            return None

        identifiers = self._quota_filesystem_identifiers()
        selected = next(
            (parts for parts in data_rows if self._filesystem_matches(parts[0], identifiers)),
            None,
        )

        if not selected or len(selected) < 4:
            return None

        def to_int(value: str) -> int:
            try:
                return int(value.rstrip("*+"))
            except ValueError:
                return 0

        # columns: filesystem blocks quota limit grace files quota limit grace
        blocks_used_kb = to_int(selected[1])
        blocks_soft_kb = to_int(selected[2])
        blocks_hard_kb = to_int(selected[3])

        return {
            "blocks_used_kb": blocks_used_kb,
            "blocks_soft_kb": blocks_soft_kb,
            "blocks_hard_kb": blocks_hard_kb,
        }

    @staticmethod
    def _is_quota_number(value: str) -> bool:
        return bool(re.fullmatch(r"\d+[+*]?", value))

    @staticmethod
    def _filesystem_matches(filesystem: str, identifiers: set) -> bool:
        if filesystem in identifiers:
            return True
        return filesystem.startswith("/") and os.path.realpath(filesystem) in identifiers

    def _quota_filesystem_identifiers(self) -> set:
        """Resolve the configured path to its actual mount and backing source."""
        if not self.quota_fs:
            return set()
        selected = None
        try:
            with open("/proc/self/mountinfo", "r", encoding="utf-8") as mounts:
                for line in mounts:
                    before, separator, after = line.partition(" - ")
                    if not separator:
                        continue
                    fields, source_fields = before.split(), after.split()
                    if len(fields) < 5 or len(source_fields) < 2:
                        continue
                    mountpoint = re.sub(
                        r"\\([0-7]{3})",
                        lambda match: chr(int(match.group(1), 8)),
                        fields[4],
                    )
                    mountpoint = os.path.realpath(mountpoint)
                    if self.quota_fs != mountpoint and not self.quota_fs.startswith(
                        mountpoint.rstrip("/") + "/"
                    ):
                        continue
                    if selected is None or len(mountpoint) >= len(selected[0]):
                        source = re.sub(
                            r"\\([0-7]{3})",
                            lambda match: chr(int(match.group(1), 8)),
                            source_fields[1],
                        )
                        selected = (mountpoint, source)
        except OSError as exc:
            print(f"读取文件系统挂载映射失败: {exc}")
            return set()
        if selected is None:
            return set()
        mountpoint, source = selected
        identifiers = {mountpoint, source}
        if source.startswith("/"):
            identifiers.add(os.path.realpath(source))
        return identifiers

    @staticmethod
    def _kb_to_gb(value_kb: int) -> float:
        return round(value_kb / 1024 / 1024, 2)
