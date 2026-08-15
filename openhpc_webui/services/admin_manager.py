"""
管理员权限管理模块
从 .env 文件读写管理员列表，保证值的唯一性，修改后当前进程立即生效。
"""

import os
import re
from typing import List


def get_admin_list() -> List[str]:
    """
    从环境变量读取管理员用户名列表。
    每次调用都读取 os.environ（运行时可通过 _write_admin_list 实时更新）。

    Returns:
        去重后的管理员用户名列表（保持原始顺序）
    """
    raw = os.getenv("ADMIN_USERS", "")
    return _dedupe([u.strip() for u in raw.split(",") if u.strip()])


def is_admin(username: str) -> bool:
    """
    检查某用户名是否在管理员列表中（大小写敏感）。

    Args:
        username: 要检查的用户名

    Returns:
        是管理员返回 True，否则返回 False
    """
    return username in get_admin_list()


def set_admin(username: str, make_admin: bool) -> bool:
    """
    设置或取消用户的管理员权限，并持久化到 .env 文件。

    Args:
        username:   目标用户名
        make_admin: True 授予管理员，False 撤销管理员

    Returns:
        操作成功返回 True，写文件失败返回 False
    """
    if not username or not username.strip():
        return False

    admins = get_admin_list()

    if make_admin:
        if username in admins:
            return True  # 已是管理员，幂等
        admins.append(username)
    else:
        if username not in admins:
            return True  # 本就不是管理员，幂等
        admins = [a for a in admins if a != username]

    return _write_admin_list(admins)


def add_admin(username: str) -> bool:
    """
    将用户添加到管理员列表。已存在时幂等。

    Args:
        username: 要授权的用户名

    Returns:
        操作成功返回 True
    """
    return set_admin(username, True)


def remove_admin(username: str) -> bool:
    """
    将用户从管理员列表移除。不存在时幂等。

    Args:
        username: 要撤权的用户名

    Returns:
        操作成功返回 True
    """
    return set_admin(username, False)


# ── 内部工具函数 ────────────────────────────────────────────────────────────


def _dedupe(items: List[str]) -> List[str]:
    """去重并保持首次出现顺序。"""
    seen: set = set()
    result: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _find_env_file() -> str:
    """
    查找 .env 文件路径。
    优先查找当前工作目录，其次查找本模块所在目录。

    Returns:
        .env 文件的绝对路径（不保证文件一定存在）
    """
    # 1. 当前工作目录
    cwd_env = os.path.join(os.getcwd(), ".env")
    if os.path.exists(cwd_env):
        return cwd_env

    # 2. 本模块所在目录
    module_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(module_dir, ".env")


def _write_admin_list(admins: List[str]) -> bool:
    """
    将管理员列表写回 .env 文件，并同步更新 os.environ 使本进程立即生效。

    步骤：
      1. 去重（保持顺序）
      2. 序列化为逗号分隔字符串
      3. 更新 os.environ["ADMIN_USERS"]（当前进程立即生效，无需重启）
      4. 读取 .env 文件，替换或追加 ADMIN_USERS 行
      5. 写回文件

    Args:
        admins: 已经过处理的管理员列表

    Returns:
        成功返回 True，IO 异常返回 False
    """
    unique_admins = _dedupe(admins)
    admin_str = ",".join(unique_admins)

    # ── 1. 立即更新运行时环境变量 ──────────────────────────────────────────
    os.environ["ADMIN_USERS"] = admin_str

    # ── 2. 持久化到 .env 文件 ─────────────────────────────────────────────
    env_path = _find_env_file()

    try:
        # 读取现有内容
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            content = ""

        new_line = f"ADMIN_USERS={admin_str}"

        # 替换已有的 ADMIN_USERS 行，或追加新行
        if re.search(r"^ADMIN_USERS\s*=", content, re.MULTILINE):
            content = re.sub(
                r"^ADMIN_USERS\s*=.*$",
                new_line,
                content,
                flags=re.MULTILINE,
            )
        else:
            # 确保文件末尾有换行符后再追加
            if content and not content.endswith("\n"):
                content += "\n"
            content += new_line + "\n"

        with open(env_path, "w", encoding="utf-8") as f:
            f.write(content)

        return True

    except OSError as exc:
        print(f"[admin_manager] 写入 .env 文件失败: {exc}")
        return False
