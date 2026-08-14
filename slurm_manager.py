import subprocess
import re
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from partition_config import PartitionConfigManager
from node_config import NodeConfigManager
import os
from collections import deque
from pathlib import Path


class SlurmManager:
    """Slurm 管理器 - 处理 Slurm 分区和作业操作"""

    def __init__(self):
        self.config_mgr = PartitionConfigManager()
        self.node_config_mgr = NodeConfigManager()

    def list_partitions(self) -> List[Dict]:
        """列出所有分区 - 从配置文件读取并结合运行时状态"""
        try:
            # 从配置文件读取分区
            config_partitions = self.config_mgr.read_partitions()

            # 获取运行时状态
            runtime_status = self._get_runtime_status()

            # 合并配置和运行时状态
            partitions = []
            for partition in config_partitions:
                name = partition['name']
                runtime = runtime_status.get(name, {})

                # 安全地获取运行时状态，提供默认值
                node_state = runtime.get('node_state', 'N/A')
                nodelist = runtime.get('nodelist', partition.get('nodes', ''))
                cpus = runtime.get('cpus', 'N/A')
                total_nodes = runtime.get('total_nodes', 0)
                total_cpus = runtime.get('total_cpus', 0)

                # 确保数值类型正确
                try:
                    total_nodes = int(total_nodes) if total_nodes else 0
                except (ValueError, TypeError):
                    total_nodes = 0

                try:
                    total_cpus = int(total_cpus) if total_cpus else 0
                except (ValueError, TypeError):
                    total_cpus = 0

                merged = {
                    'name': name,
                    'nodes': partition.get('nodes', ''),
                    'default': partition.get('default', False),
                    'state': partition.get('state', 'UP'),
                    'allow_groups': partition.get('allow_groups', ''),
                    'max_time': partition.get('max_time', ''),
                    # 运行时状态
                    'node_state': node_state,
                    'nodelist': nodelist,
                    'cpus': cpus,
                    'total_nodes': total_nodes,
                    'total_cpus': total_cpus,
                    # 节点状态统计
                    'idle_nodes': runtime.get('idle_nodes', 0),
                    'alloc_nodes': runtime.get('alloc_nodes', 0),
                    'offline_nodes': runtime.get('offline_nodes', 0)
                }
                partitions.append(merged)

            return partitions
        except Exception as e:
            print(f"查询分区失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _get_runtime_status(self) -> Dict[str, Dict]:
        """获取分区的运行时状态

        注意: sinfo 对同一分区可能输出多行（不同节点状态），需要聚合
        """
        # 使用字典的列表来收集同一分区的多行数据
        partition_lines = {}

        try:
            # 使用 sinfo 命令获取运行时状态
            result = subprocess.run(
                ['sinfo', '-o', '%P|%a|%l|%D|%T|%N|%C'],
                capture_output=True,
                text=True,
                check=True
            )

            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:  # Skip header
                if not line.strip():
                    continue

                parts = line.split('|')
                if len(parts) < 7:
                    print(f"警告: sinfo 输出格式异常，字段不足: {line}")
                    continue

                try:
                    partition_name = parts[0].rstrip('*')

                    # 初始化分区记录
                    if partition_name not in partition_lines:
                        partition_lines[partition_name] = {
                            'state': parts[1].strip() if parts[1] else 'N/A',
                            'timelimit': parts[2].strip() if parts[2] else 'N/A',
                            'total_nodes': 0,
                            'node_state_counts': {},  # 记录每种状态的节点数量
                            'nodelists': [],    # 收集所有节点列表
                            'alloc_cpus': 0,
                            'idle_cpus': 0,
                            'other_cpus': 0,
                            'total_cpus': 0
                        }

                    # 累加节点数量
                    nodes_count = 0
                    if parts[3].strip().isdigit():
                        nodes_count = int(parts[3])
                    partition_lines[partition_name]['total_nodes'] += nodes_count

                    # 记录每种状态的节点数量
                    node_state = parts[4].strip() if parts[4] else 'N/A'
                    if node_state not in partition_lines[partition_name]['node_state_counts']:
                        partition_lines[partition_name]['node_state_counts'][node_state] = 0
                    partition_lines[partition_name]['node_state_counts'][node_state] += nodes_count

                    # 收集节点列表
                    nodelist = parts[5].strip() if parts[5] else ''
                    if nodelist:
                        partition_lines[partition_name]['nodelists'].append(nodelist)

                    # 解析并累加 CPU 信息 (A/I/O/T 格式)
                    cpu_str = parts[6].strip() if parts[6] else ''
                    cpu_match = re.match(r'(\d+)/(\d+)/(\d+)/(\d+)', cpu_str)
                    if cpu_match:
                        partition_lines[partition_name]['alloc_cpus'] += int(cpu_match.group(1))
                        partition_lines[partition_name]['idle_cpus'] += int(cpu_match.group(2))
                        partition_lines[partition_name]['other_cpus'] += int(cpu_match.group(3))
                        partition_lines[partition_name]['total_cpus'] += int(cpu_match.group(4))

                except Exception as parse_error:
                    print(f"警告: 解析分区状态失败: {line}, 错误: {parse_error}")
                    continue

            # 生成最终的状态映射
            status_map = {}
            for partition_name, data in partition_lines.items():
                # 汇总节点列表
                nodelist_summary = ','.join(data['nodelists']) if data['nodelists'] else 'N/A'

                # 重构 CPU 格式字符串
                cpus_str = f"{data['alloc_cpus']}/{data['idle_cpus']}/{data['other_cpus']}/{data['total_cpus']}"

                # 计算各状态节点数量
                idle_nodes = data['node_state_counts'].get('idle', 0)
                mixed_nodes = data['node_state_counts'].get('mixed', 0)
                allocated_nodes = data['node_state_counts'].get('allocated', 0) + data['node_state_counts'].get('alloc', 0)
                down_nodes = data['node_state_counts'].get('down', 0)
                drained_nodes = data['node_state_counts'].get('drained', 0) + data['node_state_counts'].get('drain', 0) + data['node_state_counts'].get('draining', 0)
                invalid_nodes = data['node_state_counts'].get('inval', 0) + data['node_state_counts'].get('invalid', 0)

                # 已分配节点 = mixed + allocated
                alloc_nodes = mixed_nodes + allocated_nodes
                # 离线节点 = down + drained + invalid
                offline_nodes = down_nodes + drained_nodes + invalid_nodes

                # 生成节点状态字符串（用于显示徽章）
                node_state_list = []
                for state, count in data['node_state_counts'].items():
                    if count > 0:
                        node_state_list.append(state)
                node_state_summary = ','.join(node_state_list) if node_state_list else 'N/A'

                status_map[partition_name] = {
                    'state': data['state'],
                    'timelimit': data['timelimit'],
                    'total_nodes': data['total_nodes'],
                    'idle_nodes': idle_nodes,
                    'alloc_nodes': alloc_nodes,
                    'offline_nodes': offline_nodes,
                    'node_state': node_state_summary,
                    'node_state_counts': data['node_state_counts'],  # 详细的状态计数
                    'nodelist': nodelist_summary,
                    'cpus': cpus_str,
                    'total_cpus': data['total_cpus']
                }

        except Exception as e:
            print(f"获取运行时状态失败: {e}")
            import traceback
            traceback.print_exc()

        return status_map

    def get_partition_detail(self, partition_name: str) -> Dict:
        """获取分区详细信息"""
        try:
            result = subprocess.run(
                ['scontrol', 'show', 'partition', partition_name],
                capture_output=True,
                text=True,
                check=True
            )

            detail = {}
            output = result.stdout.strip()

            # Parse key=value pairs
            # Handle multi-line output
            patterns = [
                r'AllowGroups=(\S+)',
                r'MaxTime=(\S+)',
                r'TotalCPUs=(\d+)',
                r'Nodes=(\S+)',
                r'State=(\S+)',
                r'DefaultTime=(\S+)',
                r'MaxNodes=(\S+)'
            ]

            for pattern in patterns:
                match = re.search(pattern, output)
                if match:
                    key = pattern.split('=')[0].replace(r'\(', '').replace(r'\)', '')
                    detail[key] = match.group(1)

            return detail
        except Exception as e:
            print(f"获取分区详情失败: {e}")
            return {}

    def get_partition(self, partition_name: str) -> Optional[Dict]:
        """获取单个分区信息"""
        partitions = self.list_partitions()
        for partition in partitions:
            if partition['name'] == partition_name:
                return partition
        return None

    def create_partition(self, name: str, nodes: str, **kwargs) -> bool:
        """创建新分区 - 添加到配置文件并重新加载"""
        return self.config_mgr.add_partition(name, nodes, **kwargs)

    def update_partition(self, name: str, **kwargs) -> bool:
        """更新分区信息 - 更新配置文件并重新加载"""
        return self.config_mgr.update_partition(name, **kwargs)

    def delete_partition(self, name: str) -> bool:
        """删除分区 - 从配置文件删除并重新加载"""
        return self.config_mgr.delete_partition(name)

    def list_nodes(self) -> List[Dict]:
        """列出所有节点"""
        try:
            result = subprocess.run(
                ['sinfo', '-N', '-o', '%N|%T|%C|%m|%P'],
                capture_output=True,
                text=True,
                check=True
            )

            lines = result.stdout.strip().split('\n')
            if len(lines) < 2:
                return []

            nodes = []
            for line in lines[1:]:  # Skip header
                parts = line.split('|')
                if len(parts) >= 5:
                    node = {
                        'name': parts[0],
                        'state': parts[1],
                        'cpus': parts[2],
                        'memory': parts[3],
                        'partition': parts[4]
                    }
                    nodes.append(node)

            return nodes
        except Exception as e:
            print(f"查询节点失败: {e}")
            return []

    def list_jobs(self, state: Optional[str] = None, partition: Optional[str] = None) -> List[Dict]:
        """列出活动作业队列

        Args:
            state: 过滤作业状态 (RUNNING, PENDING, etc.)
            partition: 过滤分区名称
        """
        try:
            # 使用 squeue 获取作业队列
            cmd = ['squeue', '-o', '%i|%j|%u|%P|%T|%D|%M|%S|%N']
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            lines = result.stdout.strip().split('\n')
            if len(lines) < 2:
                return []

            jobs = []
            for line in lines[1:]:  # Skip header
                parts = line.split('|')
                if len(parts) >= 9:
                    job_state = parts[4]
                    job_partition = parts[3]

                    # 应用过滤器
                    if state and job_state != state:
                        continue
                    if partition and job_partition != partition:
                        continue

                    job = {
                        'job_id': parts[0],
                        'name': parts[1],
                        'user': parts[2],
                        'partition': job_partition,
                        'state': job_state,
                        'nodes': parts[5],
                        'time': parts[6],
                        'start_time': parts[7],
                        'nodelist': self._normalize_null(parts[8]) if len(parts) > 8 else None
                    }
                    jobs.append(job)

            return jobs
        except Exception as e:
            print(f"查询作业队列失败: {e}")
            return []

    def get_job_detail(self, job_id: str) -> Optional[Dict]:
        """获取作业详细信息

        Args:
            job_id: 作业ID
        """
        try:
            result = subprocess.run(
                ['scontrol', 'show', 'job', job_id],
                capture_output=True,
                text=True,
                check=True
            )

            output = result.stdout.strip()
            if not output or 'Invalid job id' in output:
                return None

            # 解析 key=value 格式的输出
            detail = {}
            patterns = {
                'JobId': r'JobId=(\d+)',
                'JobName': r'JobName=(\S+)',
                'UserId': r'UserId=(\S+)',
                'GroupId': r'GroupId=(\S+)',
                'Partition': r'Partition=(\S+)',
                'JobState': r'JobState=(\S+)',
                'RunTime': r'RunTime=(\S+)',
                'TimeLimit': r'TimeLimit=(\S+)',
                'SubmitTime': r'SubmitTime=([^\s]+\s+[^\s]+)',
                'StartTime': r'StartTime=([^\s]+\s+[^\s]+)',
                'EndTime': r'EndTime=([^\s]+\s+[^\s]+)',
                'NodeList': r'^\s*NodeList=(\S+)',
                'NumNodes': r'NumNodes=(\d+)',
                'NumCPUs': r'NumCPUs=(\d+)',
                'WorkDir': r'WorkDir=(\S+)',
                'StdOut': r'StdOut=(\S+)',
                'StdErr': r'StdErr=(\S+)',
                'Command': r'Command=(.+?)(?=\n\s+\w+=|\Z)'
            }

            for key, pattern in patterns.items():
                match = re.search(pattern, output, re.MULTILINE | re.DOTALL)
                if match:
                    detail[key] = match.group(1).strip()

            detail["NodeList"] = self._normalize_null(detail.get("NodeList"))
            return detail
        except Exception as e:
            print(f"获取作业详情失败: {e}")
            return None

    def cancel_job(self, job_id: str) -> bool:
        """取消作业

        Args:
            job_id: 作业ID
        """
        try:
            result = subprocess.run(
                ['scancel', job_id],
                capture_output=True,
                text=True,
                check=True
            )
            return True
        except Exception as e:
            print(f"取消作业失败: {e}")
            return False

    def list_completed_jobs(self, limit: int = 20) -> List[Dict]:
        """列出最近完成的作业

        Args:
            limit: 返回作业数量限制
        """
        try:
            # 使用 sacct 获取历史作业
            # -S: 开始时间 (今天)
            # -o: 输出格式
            # -X: 只显示作业记录,不显示作业步骤
            # -n: 不显示头部
            # --parsable2: 以制表符分隔的可解析格式输出
            # sacct 字段说明：
            # JobID | JobName | User | State | Elapsed | End | ExitCode
            cmd = """sacct -S today -o JobID,JobName,User,State,Elapsed,End,ExitCode -X -n --parsable2 | sort -t'|' -k6,6r"""
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)

            lines = result.stdout.strip().split('\n')
            jobs = []

            # 允许的状态前缀（必须用前缀匹配）
            valid_prefix = (
                'COMPLETED',
                'FAILED',
                'CANCELLED',
                'TIMEOUT',
                'OUT_OF_MEMORY',
                'PREEMPTED',   # 被抢占，很多系统视为“被取消”
                'NODE_FAIL'    # 节点故障导致结束
            )

            for line in lines:
                if not line.strip():
                    continue

                parts = line.split('|')
                if len(parts) < 7:
                    continue

                state = parts[3].strip()

                # 关键修复点：前缀匹配，而不是 state == "CANCELLED"
                if state.startswith(valid_prefix):
                    job = {
                        'job_id': parts[0],
                        'name': parts[1],
                        'user': parts[2],
                        'state': state,
                        'elapsed': parts[4],
                        'end_time': parts[5],
                        'exit_code': parts[6]
                    }
                    jobs.append(job)

                if len(jobs) >= limit:
                    break

            return jobs

        except Exception as e:
            print(f"查询历史作业失败: {e}")
            return []

    def get_job_stats(self) -> Dict:
        """获取作业统计信息"""
        try:
            all_jobs = self.list_jobs()
            completed_jobs = self.list_completed_jobs()

            running_count = sum(1 for j in all_jobs if j['state'] == 'RUNNING')
            pending_count = sum(1 for j in all_jobs if j['state'] == 'PENDING')
            completed_count = sum(1 for j in completed_jobs if j['state'] == 'COMPLETED')
            failed_count = sum(1 for j in completed_jobs if j['state'] in ['FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY'])

            return {
                'running': running_count,
                'pending': pending_count,
                'completed': completed_count,
                'failed': failed_count
            }
        except Exception as e:
            print(f"获取作业统计失败: {e}")
            return {
                'running': 0,
                'pending': 0,
                'completed': 0,
                'failed': 0
            }

    def get_user_job_report(
        self,
        username: str,
        range_key: str = "day",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict:
        """获取用户历史作业与机时统计

        Args:
            username: 用户名
            range_key: day/week/month
        """
        start_date, end_date = self._resolve_report_period(
            range_key, start_date, end_date
        )
        jobs = []
        totals = {
            "total_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "cancelled_jobs": 0,
            "cpu_hours": 0.0,
            "gpu_hours": 0.0,
            "elapsed_hours": 0.0,
        }

        json_jobs = self._get_user_job_report_json(username, start_date, end_date)
        if json_jobs is None:
            return {
                "range": range_key,
                "start_date": start_date,
                "end_date": end_date,
                "jobs": jobs,
                "totals": totals,
            }

        jobs = json_jobs["jobs"]
        totals = json_jobs["totals"]

        tres_hours = self._get_user_tres_hours(username, start_date, end_date)
        if tres_hours is not None:
            totals.update(tres_hours)

        totals["cpu_hours"] = round(totals["cpu_hours"], 2)
        totals["gpu_hours"] = round(totals["gpu_hours"], 2)
        totals["elapsed_hours"] = round(totals["elapsed_hours"], 2)

        return {
            "range": range_key,
            "start_date": start_date,
            "end_date": end_date,
            "jobs": jobs[:50],
            "totals": totals,
        }

    @staticmethod
    def _resolve_report_period(
        range_key: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> tuple:
        """返回供 Slurm 查询使用的左闭右开时间区间。"""
        now = (now or datetime.now()).replace(microsecond=0)
        normalized_start = SlurmManager._normalize_date(start_date or "")
        normalized_end = SlurmManager._normalize_date(end_date or "")

        if normalized_start:
            start = datetime.strptime(normalized_start, "%Y-%m-%d")
        elif range_key == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0)
        elif range_key == "week":
            start = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0
            )
        else:
            start = now.replace(hour=0, minute=0, second=0)

        if normalized_end:
            end = datetime.strptime(normalized_end, "%Y-%m-%d") + timedelta(days=1)
        else:
            end = now

        return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")

    def _get_user_tres_hours(
        self, username: str, start_date: str, end_date: str
    ) -> Optional[Dict[str, float]]:
        """使用 sreport 的区间截断结果统计已分配核时和卡时。"""
        totals = {}
        for tres, field in (("cpu", "cpu_hours"), ("gres/gpu", "gpu_hours")):
            cmd = [
                "sreport",
                "-T",
                tres,
                "-t",
                "Seconds",
                "cluster",
                "UserUtilizationByAccount",
                f"Users={username}",
                f"Start={start_date}",
                f"End={end_date}",
                "-n",
                "-P",
                "format=Login,Used",
            ]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except Exception as e:
                print(f"获取用户 {tres} 使用量失败: {e}")
                return None

            used_seconds = 0
            for line in result.stdout.splitlines():
                parts = line.strip().split("|")
                if len(parts) >= 2 and parts[0] == username:
                    used_seconds += self._safe_int(parts[1])
            totals[field] = round(used_seconds / 3600, 2)

        return totals

    def _get_user_job_report_json(
        self, username: str, start_date: str, end_date: Optional[str]
    ) -> Optional[Dict]:
        cmd = [
            "sacct",
            "-u",
            username,
            "-S",
            start_date,
            "-o",
            "JobID,JobName,State,ElapsedRaw,AllocCPUS,AllocTRES,Partition,Submit,Start,End",
            "-X",
            "-n",
            "--parsable2",
            "--json",
        ]
        if end_date:
            cmd[5:5] = ["-E", end_date]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception as e:
            print(f"获取用户作业报表 JSON 失败: {e}")
            return None

        try:
            payload = json.loads(result.stdout)
        except Exception as e:
            print(f"解析 sacct JSON 失败: {e}")
            return None

        jobs = []
        totals = {
            "total_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "cancelled_jobs": 0,
            "cpu_hours": 0.0,
            "gpu_hours": 0.0,
            "elapsed_hours": 0.0,
        }

        for job in payload.get("jobs", []):
            job_id = job.get("job_id") or job.get("JobID")
            if not job_id:
                continue
            if isinstance(job_id, str) and "." in job_id:
                continue

            job_id_str = str(job_id)
            name = job.get("name") or job.get("JobName", "")

            state_value = ""
            state_obj = job.get("state") or {}
            if isinstance(state_obj, dict):
                current = state_obj.get("current") or []
                if isinstance(current, list) and current:
                    state_value = current[0]
                elif isinstance(current, str):
                    state_value = current
            if not state_value:
                state_value = job.get("State", "")

            time_obj = job.get("time") or {}
            elapsed_raw = self._safe_int(time_obj.get("elapsed", 0))
            elapsed_raw = self._clip_elapsed_to_period(
                elapsed_raw, time_obj, start_date, end_date
            )

            alloc_cpus = self._safe_int(
                (job.get("required") or {}).get("CPUs", 0)
            )
            if alloc_cpus == 0:
                tres = job.get("tres") or {}
                for source in ("allocated", "requested"):
                    for req in tres.get(source, []):
                        if req.get("type") == "cpu":
                            alloc_cpus = self._safe_int(req.get("count", 0))
                            break
                    if alloc_cpus:
                        break

            alloc_gpus = 0
            tres = job.get("tres") or {}
            for allocated in tres.get("allocated", []):
                if (
                    allocated.get("type") == "gres"
                    and allocated.get("name") == "gpu"
                ):
                    alloc_gpus = self._safe_int(allocated.get("count", 0))
                    break
            if alloc_gpus == 0:
                for requested in tres.get("requested", []):
                    if (
                        requested.get("type") == "gres"
                        and requested.get("name") == "gpu"
                    ):
                        alloc_gpus = self._safe_int(requested.get("count", 0))
                        break

            cpu_hours = (
                round((elapsed_raw * alloc_cpus) / 3600, 2)
                if elapsed_raw and alloc_cpus > 0
                else 0.0
            )
            gpu_hours = (
                round((elapsed_raw * alloc_gpus) / 3600, 2)
                if elapsed_raw and alloc_gpus > 0
                else 0.0
            )
            elapsed_hours = round(elapsed_raw / 3600, 2) if elapsed_raw else 0.0

            submit_time = self._format_epoch(time_obj.get("submission"))
            start_time = self._format_epoch(time_obj.get("start"))
            end_time = self._format_epoch(time_obj.get("end"))

            jobs.append(
                {
                    "job_id": job_id_str,
                    "name": name,
                    "state": state_value,
                    "elapsed_hours": elapsed_hours,
                    "cpu_hours": cpu_hours,
                    "gpu_hours": gpu_hours,
                    "alloc_cpus": alloc_cpus,
                    "alloc_gpus": alloc_gpus,
                    "partition": job.get("partition") or job.get("Partition", ""),
                    "submit_time": submit_time,
                    "start_time": start_time,
                    "end_time": end_time,
                }
            )

            totals["total_jobs"] += 1
            totals["cpu_hours"] += cpu_hours
            totals["gpu_hours"] += gpu_hours
            totals["elapsed_hours"] += elapsed_hours

            if state_value.startswith("COMPLETED"):
                totals["completed_jobs"] += 1
            elif state_value.startswith("CANCELLED"):
                totals["cancelled_jobs"] += 1
            elif state_value.startswith(
                ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED")
            ):
                totals["failed_jobs"] += 1

        totals["cpu_hours"] = round(totals["cpu_hours"], 2)
        totals["gpu_hours"] = round(totals["gpu_hours"], 2)
        totals["elapsed_hours"] = round(totals["elapsed_hours"], 2)

        return {"jobs": jobs, "totals": totals}

    @staticmethod
    def _clip_elapsed_to_period(
        elapsed_seconds: int,
        time_obj: Dict,
        start_date: str,
        end_date: Optional[str],
    ) -> int:
        """按作业与报表区间的重叠比例截断 Slurm ElapsedRaw。"""
        if elapsed_seconds <= 0 or not end_date:
            return elapsed_seconds

        try:
            period_start = datetime.fromisoformat(start_date).timestamp()
            period_end = datetime.fromisoformat(end_date).timestamp()
            job_start = int(time_obj.get("start", 0))
            job_end = int(time_obj.get("end", 0))
            if job_start <= 0:
                return elapsed_seconds
            if job_end <= job_start or job_end >= 4294967294:
                job_end = job_start + elapsed_seconds

            wall_seconds = job_end - job_start
            overlap_seconds = max(
                0,
                min(job_end, period_end) - max(job_start, period_start),
            )
            if overlap_seconds >= wall_seconds:
                return elapsed_seconds
            return int(round(elapsed_seconds * overlap_seconds / wall_seconds))
        except (TypeError, ValueError, OverflowError):
            return elapsed_seconds

    @staticmethod
    def _format_epoch(value: Optional[int]) -> str:
        try:
            if value is None:
                return "Unknown"
            if isinstance(value, dict):
                value = value.get("number")
            value = int(value)
            if value <= 0 or value >= 4294967294:
                return "Unknown"
            return datetime.fromtimestamp(value).isoformat(timespec="seconds")
        except Exception:
            return "Unknown"

    @staticmethod
    def _safe_int(value: str, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_null(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if stripped in {"(null)", "null", "NULL", "N/A", "None"}:
            return None
        return stripped

    @staticmethod
    def _normalize_date(value: str) -> Optional[str]:
        try:
            value = value.strip()
            if not value:
                return None
            datetime.strptime(value, "%Y-%m-%d")
            return value
        except Exception:
            return None

    def list_accounts(self) -> List[Dict]:
        """列出所有 Slurm 账户（sacctmgr show account --json）。"""
        try:
            result = subprocess.run(
                ["sacctmgr", "show", "account", "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout or "{}")
            accounts = data.get("accounts", []) or []
            items: List[Dict] = []
            for account in accounts:
                name = account.get("name") or ""
                items.append(
                    {
                        "name": name,
                        "description": account.get("description") or "",
                        "organization": account.get("organization") or "",
                        "coordinators": account.get("coordinators") or [],
                        "flags": account.get("flags") or [],
                    }
                )
            return sorted(items, key=lambda x: x.get("name", ""))
        except Exception as e:
            print(f"获取 Slurm 账户失败: {e}")
            return []

    def create_account(
        self, name: str, description: Optional[str] = None, organization: Optional[str] = None
    ) -> bool:
        """创建 Slurm 账户。"""
        try:
            args = ["sacctmgr", "-i", "add", "account", f"name={name}"]
            if description:
                args.append(f"Description={description}")
            if organization:
                args.append(f"Organization={organization}")

            result = subprocess.run(args, capture_output=True, text=True, check=True)
            print(f"Slurm 创建账户 {name} 成功: {result.stdout}")
            return True
        except Exception as e:
            print(f"Slurm 创建账户失败: {e}")
            return False

    def update_account(
        self, name: str, description: Optional[str] = None, organization: Optional[str] = None
    ) -> bool:
        """更新 Slurm 账户。"""
        try:
            changes = []
            if description is not None:
                changes.append(f"Description={description}")
            if organization is not None:
                changes.append(f"Organization={organization}")

            if not changes:
                return True

            args = ["sacctmgr", "-i", "modify", "account", f"name={name}", "set"]
            args.extend(changes)
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            print(f"Slurm 更新账户 {name} 成功: {result.stdout}")
            return True
        except Exception as e:
            print(f"Slurm 更新账户失败: {e}")
            return False

    def delete_account(self, name: str) -> bool:
        """删除 Slurm 账户。"""
        try:
            result = subprocess.run(
                ["sacctmgr", "-i", "delete", "account", f"name={name}"],
                capture_output=True,
                text=True,
                check=True,
            )
            print(f"Slurm 删除账户 {name} 成功: {result.stdout}")
            return True
        except Exception as e:
            print(f"Slurm 删除账户失败: {e}")
            return False

    def list_associations(self, account: Optional[str] = None) -> List[Dict]:
        """列出 Slurm 账户关联（association）。"""
        try:
            if not account:
                account = os.getenv("SLURM_DEFAULT_ACCOUNT", "acdiost")
            result = subprocess.run(
                [
                    "sacctmgr",
                    "show",
                    "assoc",
                    f"account={account}",
                    "format=Cluster,Account,User%20,Partition,Qos",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout or "{}")
            assocs = data.get("associations", []) or []
            items: List[Dict] = []
            for assoc in assocs:
                default_qos = ""
                default_info = assoc.get("default") or {}
                if isinstance(default_info, dict):
                    default_qos = default_info.get("qos") or ""
                grp_tres_mins = {}
                max_info = assoc.get("max") or {}
                tres_info = max_info.get("tres") if isinstance(max_info, dict) else {}
                group_info = tres_info.get("group") if isinstance(tres_info, dict) else {}
                minutes = group_info.get("minutes") if isinstance(group_info, dict) else []
                if isinstance(minutes, list):
                    for item in minutes:
                        if not isinstance(item, dict):
                            continue
                        t_type = item.get("type") or ""
                        t_name = item.get("name") or ""
                        count = item.get("count")
                        if not t_type:
                            continue
                        key = f"{t_type}/{t_name}" if t_name else t_type
                        try:
                            grp_tres_mins[key] = int(count)
                        except Exception:
                            continue
                items.append(
                    {
                        "id": assoc.get("id"),
                        "cluster": assoc.get("cluster") or "",
                        "account": assoc.get("account") or "",
                        "user": assoc.get("user") or "",
                        "partition": assoc.get("partition") or "",
                        "qos": assoc.get("qos") or [],
                        "default_qos": default_qos,
                        "is_default": assoc.get("is_default"),
                        "shares_raw": assoc.get("shares_raw"),
                        "parent_account": assoc.get("parent_account") or "",
                        "grp_tres_mins": grp_tres_mins,
                        "cpu_minutes": grp_tres_mins.get("cpu", 0),
                        "gpu_minutes": grp_tres_mins.get("gres/gpu", 0),
                    }
                )
            return items
        except Exception as e:
            print(f"获取 Slurm 关联失败: {e}")
            return []

    def create_association(
        self,
        username: str,
        account: str,
        partition: Optional[str] = None,
        qos: Optional[str] = None,
        default_qos: Optional[str] = None,
    ) -> bool:
        """创建 Slurm 用户关联。"""
        try:
            args = ["sacctmgr", "-i", "add", "user", username, f"account={account}"]
            if partition:
                args.append(f"partition={partition}")
            if qos:
                args.append(f"qos={qos}")
            if default_qos:
                args.append(f"defaultqos={default_qos}")
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            print(f"Slurm 创建关联 {username}/{account} 成功: {result.stdout}")
            return True
        except Exception as e:
            print(f"Slurm 创建关联失败: {e}")
            return False

    def update_association(
        self,
        username: str,
        account: str,
        partition: Optional[str] = None,
        qos: Optional[str] = None,
        default_qos: Optional[str] = None,
    ) -> bool:
        """更新 Slurm 用户关联。"""
        try:
            changes = []
            if partition is not None:
                changes.append(f"Partition={partition}")
            if qos is not None:
                changes.append(f"Qos={qos}")
            if default_qos is not None:
                changes.append(f"DefaultQOS={default_qos}")
            if not changes:
                return True

            args = [
                "sacctmgr",
                "-i",
                "modify",
                "user",
                f"name={username}",
                f"account={account}",
                "set",
            ]
            args.extend(changes)
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            print(f"Slurm 更新关联 {username}/{account} 成功: {result.stdout}")
            return True
        except Exception as e:
            print(f"Slurm 更新关联失败: {e}")
            return False

    def delete_association(
        self, username: str, account: str, partition: Optional[str] = None
    ) -> bool:
        """删除 Slurm 用户关联。"""
        try:
            args = ["sacctmgr", "-i", "delete", "user", f"name={username}", f"account={account}"]
            if partition:
                args.append(f"partition={partition}")
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            print(f"Slurm 删除关联 {username}/{account} 成功: {result.stdout}")
            return True
        except Exception as e:
            print(f"Slurm 删除关联失败: {e}")
            return False

    def set_association_tres_minutes(
        self, username: str, account: str, cpu_minutes: Optional[int] = None, gpu_minutes: Optional[int] = None
    ) -> bool:
        """设置关联的 GrpTRESMins（核时/卡时）。"""
        try:
            parts = []
            if cpu_minutes is not None:
                parts.append(f"cpu={int(cpu_minutes)}")
            if gpu_minutes is not None:
                parts.append(f"gres/gpu={int(gpu_minutes)}")
            if not parts:
                return True

            value = ",".join(parts)
            args = [
                "sacctmgr",
                "-i",
                "modify",
                "user",
                username,
                "where",
                f"account={account}",
                "set",
                f"GrpTRESMins={value}",
            ]
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            print(f"Slurm 设置 GrpTRESMins {username}/{account} 成功: {result.stdout}")
            return True
        except Exception as e:
            print(f"Slurm 设置 GrpTRESMins 失败: {e}")
            return False

    def add_user_account(self, username: str, account: Optional[str] = None) -> bool:
        """添加用户到 Slurm 账户系统

        Args:
            username: 用户名
            account: Slurm 账户名,默认从环境变量 SLURM_DEFAULT_ACCOUNT 读取,或使用 "cardc"
        """
        try:
            # 获取默认账户
            if not account:
                account = os.getenv('SLURM_DEFAULT_ACCOUNT', 'cardc')

            # 使用 sacctmgr 添加用户账户
            # -i: 立即执行,不需要确认
            # account=账户名: 指定用户所属账户
            result = subprocess.run(
                ['sacctmgr', '-i', 'add', 'user', username, f'account={account}'],
                capture_output=True,
                text=True,
                check=True
            )
            print(f"Slurm 添加用户 {username} 到账户 {account} 成功: {result.stdout}")
            return True
        except Exception as e:
            print(f"Slurm 添加用户失败: {e}")
            return False

    def remove_user_account(self, username: str) -> bool:
        """从 Slurm 账户系统删除用户

        Args:
            username: 用户名
        """
        try:
            # 使用 sacctmgr 删除用户账户
            # -i: 立即执行,不需要确认
            result = subprocess.run(
                ['sacctmgr', '-i', 'delete', 'user', username],
                capture_output=True,
                text=True,
                check=True
            )
            print(f"Slurm 删除用户 {username} 成功: {result.stdout}")
            return True
        except Exception as e:
            print(f"Slurm 删除用户失败: {e}")
            return False

    # ==================== 节点管理方法 ====================

    def get_node_detail(self, node_name: str) -> Optional[Dict]:
        """获取节点详细信息

        Args:
            node_name: 节点名称
        """
        try:
            result = subprocess.run(
                ['scontrol', 'show', 'node', node_name],
                capture_output=True,
                text=True,
                check=True
            )

            output = result.stdout.strip()
            if not output or 'Invalid node name' in output:
                return None

            # 解析 key=value 格式的输出
            detail = {}
            patterns = {
                'NodeName': r'NodeName=(\S+)',
                'State': r'State=(\S+)',
                'CPUs': r'CPUTot=(\d+)',
                'AllocCPUs': r'CPUAlloc=(\d+)',
                'RealMemory': r'RealMemory=(\d+)',
                'AllocMem': r'AllocMem=(\d+)',
                'Partitions': r'Partitions=(\S+)',
                'OS': r'OS=(\S+)',
                'Arch': r'Arch=(\S+)',
                'Gres': r'Gres=(\S+)',
                'Reason': r'Reason=(.+?)(?=\n\s+\w+=|\Z)'
            }

            for key, pattern in patterns.items():
                match = re.search(pattern, output, re.MULTILINE | re.DOTALL)
                if match:
                    detail[key] = match.group(1).strip()

            return detail
        except Exception as e:
            print(f"获取节点详情失败: {e}")
            return None

    def update_node_state(self, node_name: str, state: str, reason: Optional[str] = None) -> bool:
        """更新节点状态

        Args:
            node_name: 节点名称
            state: 目标状态 (DRAIN, RESUME, DOWN, etc.)
            reason: 状态变更原因
        """
        try:
            cmd = ['scontrol', 'update', f'NodeName={node_name}', f'State={state}']
            if reason:
                cmd.append(f'Reason="{reason}"')

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            print(f"更新节点 {node_name} 状态为 {state} 成功")
            return True
        except Exception as e:
            print(f"更新节点状态失败: {e}")
            return False

    def drain_node(self, node_name: str, reason: str = "管理员手动下线") -> bool:
        """下线节点（设置为 DRAIN 状态）

        Args:
            node_name: 节点名称
            reason: 下线原因
        """
        return self.update_node_state(node_name, 'DRAIN', reason)

    def resume_node(self, node_name: str) -> bool:
        """恢复节点上线（设置为 RESUME 状态）

        Args:
            node_name: 节点名称
        """
        return self.update_node_state(node_name, 'RESUME')

    def add_node_config(self, node_name: str, cpus: int, **kwargs) -> bool:
        """添加节点到配置文件

        Args:
            node_name: 节点名称
            cpus: CPU 数量
            **kwargs: 其他节点参数 (boards, sockets_per_board, cores_per_socket, etc.)
        """
        return self.node_config_mgr.add_node(node_name, cpus, **kwargs)

    def update_node_config(self, node_name: str, **kwargs) -> bool:
        """更新节点配置

        Args:
            node_name: 节点名称
            **kwargs: 要更新的节点参数
        """
        return self.node_config_mgr.update_node(node_name, **kwargs)

    def delete_node_config(self, node_name: str) -> bool:
        """从配置文件删除节点

        Args:
            node_name: 节点名称

        Raises:
            ValueError: 如果节点正在被分区使用
        """
        # 检查节点是否被分区使用
        partitions_using_node = self._check_node_in_partitions(node_name)
        if partitions_using_node:
            partition_names = ', '.join(partitions_using_node)
            error_msg = f"无法删除节点 {node_name}: 该节点正在被以下分区使用: {partition_names}。请先从这些分区中移除该节点。"
            print(error_msg)
            raise ValueError(error_msg)

        return self.node_config_mgr.delete_node(node_name)

    def _check_node_in_partitions(self, node_name: str) -> List[str]:
        """检查节点是否被任何分区使用

        Args:
            node_name: 节点名称

        Returns:
            使用该节点的分区名称列表
        """
        partitions = self.config_mgr.read_partitions()
        using_partitions = []

        for partition in partitions:
            nodes_str = partition.get('nodes', '')
            if not nodes_str:
                continue

            # 检查节点是否在节点列表中
            # 支持格式: node12, node[12-21], node12,node13
            if self._is_node_in_range(node_name, nodes_str):
                using_partitions.append(partition['name'])

        return using_partitions

    def _is_node_in_range(self, node_name: str, nodes_str: str) -> bool:
        """检查节点名称是否在节点范围字符串中

        Args:
            node_name: 节点名称，如 "node12"
            nodes_str: 节点范围字符串，如 "node[12-21,23]" 或 "node12,node13"

        Returns:
            True 如果节点在范围内
        """
        # 简单的逗号分隔列表
        if ',' in nodes_str and '[' not in nodes_str:
            return node_name in [n.strip() for n in nodes_str.split(',')]

        # 范围表达式，如 node[12-21,23]
        if '[' in nodes_str and ']' in nodes_str:
            # 提取前缀和范围部分
            import re
            match = re.match(r'([a-zA-Z]+)\[([\d,-]+)\]', nodes_str)
            if match:
                prefix = match.group(1)
                ranges = match.group(2)

                # 检查节点名称是否匹配前缀
                if not node_name.startswith(prefix):
                    return False

                # 提取节点编号
                node_num_str = node_name[len(prefix):]
                if not node_num_str.isdigit():
                    return False
                node_num = int(node_num_str)

                # 检查编号是否在范围内
                for range_part in ranges.split(','):
                    if '-' in range_part:
                        # 范围：12-21
                        start, end = map(int, range_part.split('-'))
                        if start <= node_num <= end:
                            return True
                    else:
                        # 单个数字：23
                        if int(range_part) == node_num:
                            return True
                return False

        # 单个节点名称
        return node_name == nodes_str.strip()

    def get_node_from_config(self, node_name: str) -> Optional[Dict]:
        """从配置文件获取节点信息

        Args:
            node_name: 节点名称
        """
        return self.node_config_mgr.get_node(node_name)

    def list_nodes_from_config(self) -> List[Dict]:
        """从配置文件列出所有节点"""
        return self.node_config_mgr.read_nodes()

    def read_job_output(
        self,
        job_id: str,
        file_type: str,
        max_lines: int = 1000,
        allowed_roots: Optional[List[str]] = None,
        job_detail: Optional[Dict] = None,
    ) -> Dict:
        """读取作业输出文件内容

        Args:
            job_id: 作业ID
            file_type: 文件类型 ('stdout' 或 'stderr')
            max_lines: 最多读取的行数,默认1000行

        Returns:
            包含文件内容和元数据的字典
        """
        try:
            # 获取作业详情以获得文件路径
            job_detail = job_detail or self.get_job_detail(job_id)
            if not job_detail:
                return {
                    'success': False,
                    'error': '作业不存在',
                    'content': None
                }

            # 获取文件路径
            file_path_key = 'StdOut' if file_type == 'stdout' else 'StdErr'
            file_path = job_detail.get(file_path_key)

            if not file_path or file_path == 'N/A':
                return {
                    'success': False,
                    'error': f'{file_type} 文件路径未配置',
                    'content': None
                }

            resolved_path = Path(file_path).expanduser().resolve(strict=False)
            resolved_roots = []
            for root in allowed_roots or []:
                resolved_root = Path(root).expanduser().resolve(strict=False)
                if resolved_root != Path(resolved_root.anchor):
                    resolved_roots.append(resolved_root)

            if not any(
                resolved_path == root or root in resolved_path.parents
                for root in resolved_roots
            ):
                return {
                    'success': False,
                    'forbidden': True,
                    'error': '作业输出文件不在允许读取的目录内',
                    'content': None,
                }

            # 检查文件是否存在
            if not resolved_path.is_file():
                return {
                    'success': False,
                    'error': f'文件不存在: {resolved_path}',
                    'content': None
                }

            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(resolved_path, flags)
            opened_stat = os.fstat(descriptor)
            try:
                current_path = resolved_path.resolve(strict=True)
                current_stat = current_path.stat()
                still_allowed = any(
                    current_path == root or root in current_path.parents
                    for root in resolved_roots
                )
                same_file = (
                    opened_stat.st_dev == current_stat.st_dev
                    and opened_stat.st_ino == current_stat.st_ino
                )
                if not still_allowed or not same_file:
                    os.close(descriptor)
                    return {
                        'success': False,
                        'forbidden': True,
                        'error': '作业输出文件路径在读取期间发生变化',
                        'content': None,
                    }
            except Exception:
                os.close(descriptor)
                raise

            file_size = opened_stat.st_size

            # 读取文件内容（最多读取最后 max_lines 行）
            lines = deque(maxlen=max_lines)
            line_count = 0
            with os.fdopen(
                descriptor, 'r', encoding='utf-8', errors='replace'
            ) as output:
                for line in output:
                    lines.append(line)
                    line_count += 1
            content = ''.join(lines)
            actual_lines = len(lines)

            return {
                'success': True,
                'content': content,
                'file_path': str(resolved_path),
                'file_size': file_size,
                'lines_read': actual_lines,
                'max_lines': max_lines,
                'truncated': line_count > max_lines,
            }

        except Exception as e:
            print(f"读取作业输出文件失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': f'读取文件失败: {str(e)}',
                'content': None
            }
