import re
import os
import stat
import subprocess
import tempfile
from typing import List, Dict, Optional
from pathlib import Path

from ..config import slurm_config_file
from ..audit import structured_print as print
from .integration_timeout import bounded_timeout_seconds


class NodeConfigManager:
    """Slurm 节点配置文件管理器"""

    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or slurm_config_file("node.conf")

    def read_nodes(self) -> List[Dict]:
        """读取配置文件中的所有节点"""
        try:
            with open(self.config_file, 'r') as f:
                content = f.read()

            nodes = []
            # 匹配每一行节点配置
            lines = content.strip().split('\n')

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                node = self._parse_node_line(line)
                if node:
                    nodes.append(node)

            return nodes
        except FileNotFoundError:
            print(f"配置文件不存在: {self.config_file}")
            return []
        except Exception as e:
            print(f"读取配置文件失败: {e}")
            return []

    def _parse_node_line(self, line: str) -> Optional[Dict]:
        """解析一行节点配置"""
        try:
            node = {}
            raw_line = line
            line = line.split("#", 1)[0]

            # 提取 NodeName
            name_match = re.search(r'NodeName=(\S+)', line)
            if name_match:
                node['name'] = name_match.group(1)
            else:
                return None

            # 提取 CPUs
            cpus_match = re.search(r'CPUs=(\d+)', line)
            if cpus_match:
                node['cpus'] = int(cpus_match.group(1))

            # 提取 Boards
            boards_match = re.search(r'Boards=(\d+)', line)
            if boards_match:
                node['boards'] = int(boards_match.group(1))

            # 提取 SocketsPerBoard
            sockets_match = re.search(r'SocketsPerBoard=(\d+)', line)
            if sockets_match:
                node['sockets_per_board'] = int(sockets_match.group(1))

            # 提取 CoresPerSocket
            cores_match = re.search(r'CoresPerSocket=(\d+)', line)
            if cores_match:
                node['cores_per_socket'] = int(cores_match.group(1))

            # 提取 ThreadsPerCore
            threads_match = re.search(r'ThreadsPerCore=(\d+)', line)
            if threads_match:
                node['threads_per_core'] = int(threads_match.group(1))

            # 提取 RealMemory
            memory_match = re.search(r'RealMemory=(\d+)', line)
            if memory_match:
                node['real_memory'] = int(memory_match.group(1))

            # 提取 Gres (可选)
            gres_match = re.search(r'Gres=(\S+)', line)
            if gres_match:
                node['gres'] = gres_match.group(1)

            for key, field in (
                ("NodeAddr", "node_addr"),
                ("Parameters", "parameters"),
                ("State", "state"),
            ):
                match = re.search(rf"(?<!\S){key}=(\S+)", line)
                if match:
                    node[field] = match.group(1)

            # 保存原始配置行
            node['raw_line'] = raw_line

            return node
        except Exception as e:
            print(f"解析节点配置失败: {line}, 错误: {e}")
            return None

    def get_node(self, name: str) -> Optional[Dict]:
        """获取单个节点配置"""
        nodes = self.read_nodes()
        for node in nodes:
            if node['name'] == name:
                return node
        return None

    def add_node(self, name: str, cpus: int, **kwargs) -> bool:
        """添加新节点"""
        try:
            # 检查节点是否已存在
            existing = self.get_node(name)
            if existing:
                print(f"节点 {name} 已存在")
                return False

            config_path = Path(self.config_file)
            config_existed = config_path.exists()
            original_bytes = config_path.read_bytes() if config_existed else None
            original_mode = stat.S_IMODE(config_path.stat().st_mode) if config_existed else None
            original_lines = self._read_config_lines() if config_existed else []
            # 构建配置行并原子写入
            config_line = self._build_config_line(name, cpus, **kwargs)
            updated_lines = list(original_lines)
            if updated_lines and not updated_lines[-1].endswith("\n"):
                updated_lines[-1] += "\n"
            updated_lines.append(config_line + "\n")
            if not self._replace_config_lines(updated_lines):
                return False

            # 重新加载 Slurm 配置
            if self._reconfigure_slurm():
                return True

            if self._restore_config(original_bytes, original_mode):
                print(f"添加节点 {name} 后重载失败，已回滚配置文件")
            else:
                print(f"严重警告: 添加节点 {name} 后重载和配置回滚均失败")
            return False
        except Exception as e:
            print(f"添加节点失败: {e}")
            return False

    def update_node(self, name: str, **kwargs) -> bool:
        """更新节点配置"""
        try:
            lines = self._read_config_lines()
            target_index = self._find_line_index(lines, name)
            if target_index is None:
                print(f"节点 {name} 不存在")
                return False

            updates = {
                "CPUs": kwargs.get("cpus"),
                "Boards": kwargs.get("boards"),
                "SocketsPerBoard": kwargs.get("sockets_per_board"),
                "CoresPerSocket": kwargs.get("cores_per_socket"),
                "ThreadsPerCore": kwargs.get("threads_per_core"),
                "RealMemory": kwargs.get("real_memory"),
                "Gres": kwargs.get("gres"),
                "NodeAddr": kwargs.get("node_addr"),
                "Parameters": kwargs.get("parameters"),
                "State": kwargs.get("state"),
            }
            lines[target_index] = self._update_config_line(
                lines[target_index], updates
            )
            return self._write_config_lines(lines)
        except Exception as e:
            print(f"更新节点失败: {e}")
            return False

    def delete_node(self, name: str) -> bool:
        """删除节点"""
        try:
            lines = self._read_config_lines()
            target_index = self._find_line_index(lines, name)
            if target_index is None:
                print(f"节点 {name} 不存在")
                return False

            del lines[target_index]
            return self._write_config_lines(lines)
        except Exception as e:
            print(f"删除节点失败: {e}")
            return False

    def _build_config_line(self, name: str, cpus: int, **kwargs) -> str:
        """构建节点配置行"""
        parts = [f"NodeName={name}"]
        if kwargs.get("node_addr"):
            parts.append(f"NodeAddr={kwargs['node_addr']}")
        parts.append(f"CPUs={cpus}")

        # 添加可选参数
        if 'boards' in kwargs and kwargs['boards']:
            parts.append(f"Boards={kwargs['boards']}")

        if 'sockets_per_board' in kwargs and kwargs['sockets_per_board']:
            parts.append(f"SocketsPerBoard={kwargs['sockets_per_board']}")

        if 'cores_per_socket' in kwargs and kwargs['cores_per_socket']:
            parts.append(f"CoresPerSocket={kwargs['cores_per_socket']}")

        if 'threads_per_core' in kwargs and kwargs['threads_per_core']:
            parts.append(f"ThreadsPerCore={kwargs['threads_per_core']}")

        if 'real_memory' in kwargs and kwargs['real_memory']:
            parts.append(f"RealMemory={kwargs['real_memory']}")

        if 'gres' in kwargs and kwargs['gres']:
            parts.append(f"Gres={kwargs['gres']}")

        if kwargs.get("parameters"):
            parts.append(f"Parameters={kwargs['parameters']}")
        if kwargs.get("state"):
            parts.append(f"State={kwargs['state']}")

        return ' '.join(parts)

    def _read_config_lines(self) -> List[str]:
        with open(self.config_file, "r", encoding="utf-8") as config:
            return config.readlines()

    def _find_line_index(self, lines: List[str], name: str) -> Optional[int]:
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            node = self._parse_node_line(stripped)
            if node and node["name"] == name:
                return index
        return None

    @staticmethod
    def _update_config_line(line: str, updates: Dict[str, object]) -> str:
        ending = "\n" if line.endswith("\n") else ""
        body = line[:-1] if ending else line
        body, marker, comment = body.partition("#")
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
        if marker:
            body = body.rstrip() + " #" + comment
        return body + ending

    def _replace_config_lines(self, lines, mode=None) -> bool:
        """原子替换配置文件内容，保留原始字节与权限供回滚使用。"""
        temp_path = None
        try:
            config_path = Path(self.config_file)
            if mode is None:
                mode = stat.S_IMODE(config_path.stat().st_mode) if config_path.exists() else 0o644
            content = lines if isinstance(lines, bytes) else "".join(lines).encode("utf-8")
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=str(config_path.parent),
                prefix=f".{config_path.name}.",
                delete=False,
            ) as temp_file:
                temp_path = temp_file.name
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.chmod(temp_path, mode)
            os.replace(temp_path, config_path)
            return True
        except Exception as e:
            print(f"写入配置文件失败: {e}")
            return False
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def _restore_config(self, original_bytes: Optional[bytes], original_mode: Optional[int]) -> bool:
        if original_bytes is not None:
            return self._replace_config_lines(original_bytes, mode=original_mode)
        try:
            Path(self.config_file).unlink(missing_ok=True)
            return True
        except OSError as exc:
            print(f"删除新增节点配置文件失败: {exc}")
            return False

    def _write_config_lines(self, lines: List[str]) -> bool:
        """写回文件并重载；重载失败则恢复原文件。"""
        config_path = Path(self.config_file)
        try:
            original_bytes = config_path.read_bytes()
            original_mode = stat.S_IMODE(config_path.stat().st_mode)
        except OSError as exc:
            print(f"读取原节点配置文件失败: {exc}")
            return False
        if not self._replace_config_lines(lines):
            return False
        if self._reconfigure_slurm():
            print(f"节点配置文件已更新: {self.config_file}")
            return True
        if self._restore_config(original_bytes, original_mode):
            print("节点配置重载失败，已回滚配置文件")
        else:
            print("严重警告: 节点配置重载和配置回滚均失败")
        return False

    def _reconfigure_slurm(self) -> bool:
        """重新加载 Slurm 配置"""
        try:
            # 使用 scontrol reconfigure 重新加载配置
            result = subprocess.run(
                ['scontrol', 'reconfigure'],
                capture_output=True,
                text=True,
                timeout=bounded_timeout_seconds(
                    "SLURM_COMMAND_TIMEOUT_SECONDS", 15
                ),
            )

            if result.returncode != 0:
                print(f"重新加载 Slurm 配置失败: {result.stderr}")
                return False

            print("Slurm 配置已重新加载")
            return True
        except Exception as e:
            print(f"重新加载 Slurm 配置失败: {e}")
            return False
