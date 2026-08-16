import os
import re
import subprocess
from typing import Dict, Optional
from ..audit import structured_print as print


class NFSQuotaManager:
    """读取和设置指定文件系统上的用户存储配额。"""

    def __init__(self) -> None:
        # 启用 quota 的文件系统挂载点，如 /、/home 或 /data。
        self.quota_fs = os.getenv("NFS_QUOTA_FS", "").strip()
        self._quota_ready: Optional[bool] = None

    def is_enabled(self) -> bool:
        """Return whether the configured filesystem has readable user quotas."""
        if not self.quota_fs:
            return False
        if self._quota_ready is not None:
            return self._quota_ready

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

        self._quota_ready = result.returncode == 0
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
        return [
            "quota",
            "-w",
            "-v",
            "-u",
            f"--filesystem={self.quota_fs}",
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

        selected = None
        for parts in data_rows:
            filesystem = parts[0]
            if self._filesystem_matches(filesystem):
                selected = parts
                break

        # The command is already restricted with --filesystem, while quota
        # commonly prints the backing device rather than the mount point.
        if selected is None and len(data_rows) == 1:
            selected = data_rows[0]

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

    def _filesystem_matches(self, filesystem: str) -> bool:
        if not self.quota_fs:
            return True
        if filesystem == self.quota_fs:
            return True
        if filesystem.endswith(self.quota_fs):
            return True
        if self.quota_fs.endswith(filesystem):
            return True
        return False

    @staticmethod
    def _kb_to_gb(value_kb: int) -> float:
        return round(value_kb / 1024 / 1024, 2)
