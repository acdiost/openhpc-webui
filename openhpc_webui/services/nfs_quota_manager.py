import os
import subprocess
from typing import Dict, Optional


class NFSQuotaManager:
    """NFS quota 管理器 - 读取/设置用户存储配额"""

    def __init__(self) -> None:
        # NFS quota 所在的文件系统或挂载点，如 /home 或 /data
        self.quota_fs = os.getenv("NFS_QUOTA_FS", "").strip()

    def is_enabled(self) -> bool:
        return bool(self.quota_fs)

    def get_user_quota(self, username: str) -> Optional[Dict[str, float]]:
        """读取用户配额与使用量（GB）。返回 None 表示不可用或未配置。"""
        if not self.is_enabled():
            return None

        try:
            result = subprocess.run(
                ["quota", "-u", username],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            print("quota 命令不存在，无法获取 NFS 配额")
            return None
        except Exception as exc:
            print(f"获取用户配额失败: {exc}")
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
                check=True,
            )
            return True
        except FileNotFoundError:
            print("setquota 命令不存在，无法设置 NFS 配额")
            return False
        except Exception as exc:
            print(f"设置用户配额失败: {exc}")
            return False

    def _parse_quota_output(self, output: str) -> Optional[Dict[str, int]]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return None

        data_lines = []
        for line in lines:
            lower = line.lower()
            if lower.startswith("disk quotas for"):
                continue
            if lower.startswith("filesystem"):
                continue
            data_lines.append(line)

        if not data_lines:
            return None

        selected = None
        for line in data_lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            filesystem = parts[0]
            if self._filesystem_matches(filesystem):
                selected = parts
                break

        if selected is None and len(data_lines) == 1:
            selected = data_lines[0].split()

        if not selected or len(selected) < 4:
            return None

        def to_int(value: str) -> int:
            try:
                return int(value)
            except Exception:
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
