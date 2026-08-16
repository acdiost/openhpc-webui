import re
import os
import subprocess
import tempfile
from typing import List, Dict, Optional
from pathlib import Path

from ..config import slurm_config_file
from ..audit import structured_print as print


class PartitionConfigManager:
    """Slurm 分区配置文件管理器"""

    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or slurm_config_file("partition.conf")

    def read_partitions(self) -> List[Dict]:
        """读取配置文件中的所有分区"""
        try:
            with open(self.config_file, 'r') as f:
                content = f.read()

            partitions = []
            # 匹配每一行分区配置
            lines = content.strip().split('\n')

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                partition = self._parse_partition_line(line)
                if partition:
                    partitions.append(partition)

            return partitions
        except FileNotFoundError:
            print(f"配置文件不存在: {self.config_file}")
            return []
        except Exception as e:
            print(f"读取配置文件失败: {e}")
            return []

    def _parse_partition_line(self, line: str) -> Optional[Dict]:
        """解析一行分区配置"""
        try:
            # 提取配置项
            partition = {}

            # 提取 PartitionName
            name_match = re.search(r'PartitionName=(\S+)', line)
            if name_match:
                partition['name'] = name_match.group(1)
            else:
                return None

            # 提取 Nodes
            nodes_match = re.search(r'Nodes=(\S+)', line)
            if nodes_match:
                partition['nodes'] = nodes_match.group(1)

            # 提取 Default
            default_match = re.search(r'Default=(YES|NO)', line)
            partition['default'] = default_match.group(1) == 'YES' if default_match else False

            # 提取 State
            state_match = re.search(r'State=(\S+)', line)
            partition['state'] = state_match.group(1) if state_match else 'UP'

            # 提取 AllowGroups
            groups_match = re.search(r'AllowGroups=(\S+)', line)
            partition['allow_groups'] = groups_match.group(1) if groups_match else ''

            # 提取 MaxTime
            maxtime_match = re.search(r'MaxTime=(\S+)', line)
            partition['max_time'] = maxtime_match.group(1) if maxtime_match else ''

            # 保存原始配置行（用于其他未解析的参数）
            partition['raw_line'] = line

            return partition
        except Exception as e:
            print(f"解析分区配置失败: {line}, 错误: {e}")
            return None

    def get_partition(self, name: str) -> Optional[Dict]:
        """获取单个分区配置"""
        partitions = self.read_partitions()
        for partition in partitions:
            if partition['name'] == name:
                return partition
        return None

    def add_partition(self, name: str, nodes: str, **kwargs) -> bool:
        """添加新分区"""
        try:
            # 检查分区是否已存在
            existing = self.get_partition(name)
            if existing:
                print(f"分区 {name} 已存在")
                return False

            # 构建配置行
            config_line = self._build_config_line(name, nodes, **kwargs)

            # 追加到配置文件
            with open(self.config_file, 'a') as f:
                f.write(config_line + '\n')

            # 重新加载 Slurm 配置
            return self._reconfigure_slurm()
        except Exception as e:
            print(f"添加分区失败: {e}")
            return False

    def update_partition(self, name: str, **kwargs) -> bool:
        """更新分区配置"""
        try:
            lines = self._read_config_lines()
            target_index = self._find_line_index(lines, name)
            if target_index is None:
                print(f"分区 {name} 不存在")
                return False

            default = kwargs.get("default")
            updates = {
                "Nodes": kwargs.get("nodes"),
                "State": kwargs.get("state"),
                "Default": None if default is None else "YES" if default else "NO",
                "AllowGroups": kwargs.get("allow_groups"),
                "MaxTime": kwargs.get("max_time"),
            }
            lines[target_index] = self._update_config_line(
                lines[target_index], updates
            )
            return self._write_config_lines(lines)
        except Exception as e:
            print(f"更新分区失败: {e}")
            return False

    def delete_partition(self, name: str) -> bool:
        """删除分区"""
        try:
            lines = self._read_config_lines()
            target_index = self._find_line_index(lines, name)
            if target_index is None:
                print(f"分区 {name} 不存在")
                return False

            del lines[target_index]
            return self._write_config_lines(lines)
        except Exception as e:
            print(f"删除分区失败: {e}")
            return False

    def _build_config_line(self, name: str, nodes: str, **kwargs) -> str:
        """构建分区配置行"""
        parts = [f"PartitionName={name}"]
        parts.append(f"Nodes={nodes}")

        # 添加可选参数
        default = kwargs.get('default', False)
        parts.append(f"Default={'YES' if default else 'NO'}")

        state = kwargs.get('state', 'UP')
        parts.append(f"State={state}")

        if kwargs.get('allow_groups'):
            parts.append(f"AllowGroups={kwargs['allow_groups']}")

        if kwargs.get('max_time'):
            parts.append(f"MaxTime={kwargs['max_time']}")

        return ' '.join(parts)

    def _read_config_lines(self) -> List[str]:
        with open(self.config_file, "r", encoding="utf-8") as config:
            return config.readlines()

    def _find_line_index(self, lines: List[str], name: str) -> Optional[int]:
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            partition = self._parse_partition_line(stripped)
            if partition and partition["name"] == name:
                return index
        return None

    @staticmethod
    def _update_config_line(line: str, updates: Dict[str, object]) -> str:
        ending = "\n" if line.endswith("\n") else ""
        body = line[:-1] if ending else line
        for key, value in updates.items():
            if value is None:
                continue
            if any(char.isspace() for char in str(value)):
                raise ValueError(f"{key} 不能包含空白字符")
            pattern = rf"(?<!\S){re.escape(key)}=\S+"
            replacement = f"{key}={value}" if str(value) else ""
            if re.search(pattern, body):
                body = re.sub(pattern, replacement, body, count=1)
            elif replacement:
                body = f"{body.rstrip()} {replacement}"
            body = re.sub(r"[ \t]{2,}", " ", body).rstrip()
        return body + ending

    def _write_config_lines(self, lines: List[str]) -> bool:
        """原子写回配置文件，并保留未修改的原始行。"""
        temp_path = None
        try:
            config_path = Path(self.config_file)
            mode = config_path.stat().st_mode
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(config_path.parent),
                prefix=f".{config_path.name}.",
                delete=False,
            ) as temp_file:
                temp_path = temp_file.name
                temp_file.writelines(lines)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.chmod(temp_path, mode)
            os.replace(temp_path, config_path)

            print(f"分区配置文件已更新: {self.config_file}")

            # 尝试重新加载 Slurm 配置（失败不影响操作结果）
            reconfigure_success = self._reconfigure_slurm()
            if not reconfigure_success:
                print("警告: 配置文件已更新，但 Slurm 未能自动重新加载配置")
                print("提示: 可以手动运行 'scontrol reconfigure' 或重启 slurmctld 服务")

            # 配置文件更新成功就返回 True
            return True
        except Exception as e:
            print(f"写入配置文件失败: {e}")
            return False
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def _reconfigure_slurm(self) -> bool:
        """重新加载 Slurm 配置"""
        try:
            # 使用 scontrol reconfigure 重新加载配置
            result = subprocess.run(
                ['scontrol', 'reconfigure'],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                print(f"重新加载 Slurm 配置失败: {result.stderr}")
                return False

            print("Slurm 配置已重新加载")
            return True
        except Exception as e:
            print(f"重新加载 Slurm 配置失败: {e}")
            return False
