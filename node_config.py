import re
import os
import subprocess
import tempfile
from typing import List, Dict, Optional
from pathlib import Path


class NodeConfigManager:
    """Slurm 节点配置文件管理器"""

    def __init__(self, config_file: str = "/usr/local/etc/node.conf"):
        self.config_file = config_file

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

            # 保存原始配置行
            node['raw_line'] = line

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

            # 构建配置行
            config_line = self._build_config_line(name, cpus, **kwargs)

            # 追加到配置文件
            with open(self.config_file, 'a') as f:
                f.write(config_line + '\n')

            # 重新加载 Slurm 配置
            return self._reconfigure_slurm()
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

            print(f"节点配置文件已更新: {self.config_file}")

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
