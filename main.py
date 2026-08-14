import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import admin_manager as admin_mgr
from auth_manager import AuthManager
from ldap_manager import LDAPManager
from nfs_quota_manager import NFSQuotaManager
from slurm_manager import SlurmManager

load_dotenv()

AUTH_ENABLED = os.getenv("AUTHORIZED", "True").strip().lower() not in (
    "false",
    "0",
    "no",
)

_INSECURE_SECRET_KEYS = {
    "",
    "change-me-in-production",
    "your-secret-key-change-in-production",
}


def _get_session_secret() -> str:
    secret_key = os.getenv("SECRET_KEY", "").strip()
    if AUTH_ENABLED and (
        secret_key in _INSECURE_SECRET_KEYS or len(secret_key) < 32
    ):
        raise RuntimeError(
            "SECRET_KEY must be set to a unique value of at least 32 characters"
        )
    return secret_key or "authentication-disabled-development-only"

# 调试模式下使用的虚拟用户信息（始终是管理员，方便调试所有功能）
_DEBUG_USER: dict = {
    "username": "debug",
    "cn": "调试用户 (认证已关闭)",
    "mail": "",
    "uid_number": "0",
    "gid_number": "0",
    "is_admin": True,
}

app = FastAPI(title="智算中心管理门户")

# Add session middleware
SECRET_KEY = _get_session_secret()
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "False").strip().lower() in (
    "true",
    "1",
    "yes",
)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="templates")

# Initialize managers
ldap_mgr = LDAPManager()
slurm_mgr = SlurmManager()
auth_mgr = AuthManager()
quota_mgr = NFSQuotaManager()


# ── 异常处理 ────────────────────────────────────────────────────────────────


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """未登录 → 登录页；非管理员访问管理页 → 作业页；其余返回 JSON。"""
    if exc.status_code == 401:
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"detail": exc.detail})
        return RedirectResponse(url="/login", status_code=302)
    if exc.status_code == 403:
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=403, content={"detail": exc.detail})
        return RedirectResponse(url="/jobs", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# ── 认证依赖 ─────────────────────────────────────────────────────────────────


async def get_current_user(request: Request) -> dict:
    """
    检查用户是否已登录，并动态注入最新的 is_admin 状态。

    - AUTHORIZED=False 时：直接返回调试用户（始终是管理员）。
    - 每次请求都从 os.environ 实时读取管理员列表，
      使权限变更无需重新登录即可生效。
    """
    if not AUTH_ENABLED:
        user = dict(request.session.get("user") or _DEBUG_USER)
        user["is_admin"] = True
        return user

    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")

    # 每次请求动态刷新 is_admin（管理员变更立即生效，无需重新登录）
    user = dict(user)
    user["is_admin"] = admin_mgr.is_admin(user.get("username", ""))
    return user


async def get_current_user_optional(request: Request) -> Optional[dict]:
    """可选认证；AUTHORIZED=False 时返回调试用户。"""
    if not AUTH_ENABLED:
        user = dict(request.session.get("user") or _DEBUG_USER)
        user["is_admin"] = True
        return user
    user = request.session.get("user")
    if user:
        user = dict(user)
        user["is_admin"] = admin_mgr.is_admin(user.get("username", ""))
    return user


def _require_admin(user: dict) -> None:
    """若 user 不是管理员则抛出 403，供页面路由和 API 统一调用。"""
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限"
        )


def _job_owner(job: dict) -> str:
    return job.get("UserId", "").split("(", 1)[0].strip()


def _job_output_allowed_roots(owner: str) -> list[str]:
    roots = [
        item.strip()
        for item in os.getenv("JOB_OUTPUT_ALLOWED_ROOTS", "").split(os.pathsep)
        if item.strip()
    ]
    owner_data = ldap_mgr.get_user(owner) if owner else None
    if owner_data and owner_data.get("home"):
        roots.append(owner_data["home"])
    return roots


def _generate_ssh_key_pair(username: str) -> tuple[str, str, str]:
    key_type = os.getenv("SSH_KEY_TYPE", "rsa").strip().lower()
    key_bits = int(os.getenv("SSH_KEY_BITS", "4096"))

    with tempfile.TemporaryDirectory() as temp_dir:
        key_path = Path(temp_dir) / "id_key"
        args = ["ssh-keygen", "-t", key_type, "-N", "", "-f", str(key_path), "-C", username]
        if key_type == "rsa":
            args.extend(["-b", str(key_bits)])

        subprocess.run(args, check=True, capture_output=True, text=True)
        private_key = key_path.read_text(encoding="utf-8")
        public_key = key_path.with_suffix(".pub").read_text(encoding="utf-8").strip()

    return private_key, public_key, key_type


# ── Pydantic 模型 ─────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    uid: int
    gid: int
    home: str
    shell: str = "/bin/bash"
    password: Optional[str] = None
    is_admin: bool = False  # 创建时直接授予管理员权限
    storage_quota_gb: Optional[float] = None  # NFS 配额（GB），None/0 = 不限制


class UserUpdate(BaseModel):
    gid: Optional[int] = None
    home: Optional[str] = None
    shell: Optional[str] = None
    password: Optional[str] = None
    cn: Optional[str] = None
    is_admin: Optional[bool] = None  # None = 不修改权限
    storage_quota_gb: Optional[float] = None  # NFS 配额（GB），None/0 = 不限制


class GroupCreate(BaseModel):
    name: str
    gid: int
    description: Optional[str] = ""


class GroupUpdate(BaseModel):
    gid: Optional[int] = None
    description: Optional[str] = None


class GroupMemberUpdate(BaseModel):
    username: str
    group_name: str


class AccountCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    organization: Optional[str] = ""


class AccountUpdate(BaseModel):
    description: Optional[str] = None
    organization: Optional[str] = None


class AssocCreate(BaseModel):
    username: str
    account: str
    partition: Optional[str] = None
    qos: Optional[str] = None  # 逗号分隔
    default_qos: Optional[str] = None


class AssocUpdate(BaseModel):
    partition: Optional[str] = None
    qos: Optional[str] = None
    default_qos: Optional[str] = None


class AssocTRESMinutesUpdate(BaseModel):
    cpu_minutes: Optional[int] = None
    gpu_minutes: Optional[int] = None
    partition: Optional[str] = None


class PartitionCreate(BaseModel):
    name: str
    nodes: str
    default: Optional[bool] = False
    state: Optional[str] = "UP"
    max_time: Optional[str] = None
    allow_groups: Optional[str] = None


class PartitionUpdate(BaseModel):
    state: Optional[str] = None
    max_time: Optional[str] = None
    allow_groups: Optional[str] = None
    nodes: Optional[str] = None
    default: Optional[bool] = None


class NodeCreate(BaseModel):
    name: str
    cpus: int
    boards: Optional[int] = 1
    sockets_per_board: Optional[int] = 1
    cores_per_socket: Optional[int] = 1
    threads_per_core: Optional[int] = 1
    real_memory: Optional[int] = None
    gres: Optional[str] = None


class NodeUpdate(BaseModel):
    cpus: Optional[int] = None
    boards: Optional[int] = None
    sockets_per_board: Optional[int] = None
    cores_per_socket: Optional[int] = None
    threads_per_core: Optional[int] = None
    real_memory: Optional[int] = None
    gres: Optional[str] = None


class NodeStateUpdate(BaseModel):
    state: str  # DRAIN / RESUME / DOWN
    reason: Optional[str] = None


class AdminUserRequest(BaseModel):
    username: str


class UserCreditRequest(BaseModel):
    account: Optional[str] = None
    partition: Optional[str] = None
    cpu_hours: Optional[float] = None
    gpu_hours: Optional[float] = None
    hours: Optional[float] = None  # 兼容旧版仅 CPU 核时请求
    reason: Optional[str] = None
    note: Optional[str] = None
    effective_date: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


# ═══════════════════════════════════════════════════════════════════════════════
# 认证路由
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页面；AUTHORIZED=False 时直接跳转首页。"""
    if not AUTH_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    if request.session.get("user"):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/api/auth/login")
async def login(request: Request, login_data: LoginRequest):
    """处理登录请求，成功后将用户信息写入 session。"""
    user_info = auth_mgr.authenticate_user(login_data.username, login_data.password)
    if not user_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    # is_admin 不写入 session，每次请求动态计算（保证权限变更即时生效）
    request.session["user"] = user_info
    # 返回时附上当前 is_admin 状态供前端使用
    user_info = dict(user_info)
    user_info["is_admin"] = admin_mgr.is_admin(user_info.get("username", ""))
    return {"message": "登录成功", "user": user_info}


@app.post("/api/auth/logout")
async def logout(request: Request):
    """清除 session。"""
    request.session.clear()
    return {"message": "已登出"}


@app.post("/api/auth/change-password")
async def change_password(
    payload: PasswordChangeRequest, user: dict = Depends(get_current_user)
):
    """当前用户修改自己的密码。"""
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="认证已关闭，无法修改密码")
    username = (user or {}).get("username", "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户信息缺失")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    verified = auth_mgr.authenticate_user(username, payload.current_password)
    if not verified:
        raise HTTPException(status_code=400, detail="当前密码不正确")

    success = ldap_mgr.update_user(username=username, password=payload.new_password)
    if not success:
        raise HTTPException(status_code=500, detail="密码更新失败")
    return {"message": "密码已更新"}


@app.get("/api/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    """获取当前登录用户信息（含实时 is_admin 状态）。"""
    return user


# ═══════════════════════════════════════════════════════════════════════════════
# 页面路由（受保护）
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(get_current_user)):
    """仪表盘：仅管理员可访问，普通用户跳转至 /jobs。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, user: dict = Depends(get_current_user)):
    """用户管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("users.html", {"request": request, "user": user})


@app.get("/groups", response_class=HTMLResponse)
async def groups_page(request: Request, user: dict = Depends(get_current_user)):
    """组管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("groups.html", {"request": request, "user": user})


@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request, user: dict = Depends(get_current_user)):
    """账户管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("accounts.html", {"request": request, "user": user})


@app.get("/cluster-users", response_class=HTMLResponse)
async def cluster_users_page(request: Request, user: dict = Depends(get_current_user)):
    """集群用户管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse(
        "cluster_users.html", {"request": request, "user": user}
    )


@app.get("/partitions", response_class=HTMLResponse)
async def partitions_page(request: Request, user: dict = Depends(get_current_user)):
    """分区管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse(
        "partitions.html", {"request": request, "user": user}
    )


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request, user: dict = Depends(get_current_user)):
    """作业管理：所有登录用户均可访问。"""
    return templates.TemplateResponse("jobs.html", {"request": request, "user": user})


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, user: dict = Depends(get_current_user)):
    """账户设置：当前用户可修改自己的密码。"""
    return templates.TemplateResponse("account.html", {"request": request, "user": user})


@app.get("/nodes", response_class=HTMLResponse)
async def nodes_page(request: Request, user: dict = Depends(get_current_user)):
    """节点管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("nodes.html", {"request": request, "user": user})


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: dict = Depends(get_current_user)):
    """权限管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("admin.html", {"request": request, "user": user})


# ═══════════════════════════════════════════════════════════════════════════════
# 管理员权限 API
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/admin/list")
async def api_get_admin_list(user: dict = Depends(get_current_user)):
    """获取当前管理员列表（仅管理员）。"""
    _require_admin(user)
    admins = admin_mgr.get_admin_list()
    return {"admins": admins, "count": len(admins)}


@app.post("/api/admin")
async def api_add_admin(data: AdminUserRequest, user: dict = Depends(get_current_user)):
    """将用户添加到管理员列表（仅管理员）。"""
    _require_admin(user)
    username = data.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    success = admin_mgr.add_admin(username)
    if not success:
        raise HTTPException(
            status_code=500, detail="添加管理员失败，请检查 .env 文件权限"
        )
    return {
        "message": f"用户 {username} 已授予管理员权限",
        "admins": admin_mgr.get_admin_list(),
    }


@app.delete("/api/admin/{username}")
async def api_remove_admin(username: str, user: dict = Depends(get_current_user)):
    """将用户从管理员列表移除（仅管理员）。"""
    _require_admin(user)
    # 不允许管理员撤销自己（防止意外锁死）
    if username == user.get("username"):
        raise HTTPException(status_code=400, detail="不能撤销自己的管理员权限")
    success = admin_mgr.remove_admin(username)
    if not success:
        raise HTTPException(
            status_code=500, detail="移除管理员失败，请检查 .env 文件权限"
        )
    return {
        "message": f"用户 {username} 的管理员权限已移除",
        "admins": admin_mgr.get_admin_list(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LDAP 用户/组 API（仅管理员）
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/ldap/status")
async def ldap_status(user: dict = Depends(get_current_user)):
    """检查 LDAP 连接状态。"""
    _require_admin(user)
    return ldap_mgr.check_connection()


@app.get("/api/ldap/users")
async def get_users(user: dict = Depends(get_current_user)):
    """获取所有 LDAP 用户列表，并附加 is_admin 字段。"""
    _require_admin(user)
    users = ldap_mgr.list_users()
    admin_list = admin_mgr.get_admin_list()
    tres_limits = slurm_mgr.get_users_tres_limits()
    for u in users:
        u["is_admin"] = u.get("username", "") in admin_list
        user_limits = tres_limits.get(u.get("username", ""), {})
        u["cpu_minutes"] = user_limits.get("cpu_minutes")
        u["gpu_minutes"] = user_limits.get("gpu_minutes")
        quota = quota_mgr.get_user_quota(u.get("username", "")) if quota_mgr else None
        if quota:
            u["storage_used_gb"] = quota.get("used_gb")
            u["storage_quota_gb"] = quota.get("limit_gb") or 0
        else:
            u["storage_used_gb"] = None
            u["storage_quota_gb"] = 0
    return {"users": users, "count": len(users)}


@app.get("/api/ldap/users/{username}")
async def get_user(username: str, user: dict = Depends(get_current_user)):
    """获取单个用户信息，并附加 is_admin 字段。"""
    _require_admin(user)
    user_data = ldap_mgr.get_user(username)
    if not user_data:
        raise HTTPException(status_code=404, detail="用户不存在")
    user_data["is_admin"] = admin_mgr.is_admin(username)
    quota = quota_mgr.get_user_quota(username) if quota_mgr else None
    if quota:
        user_data["storage_used_gb"] = quota.get("used_gb")
        user_data["storage_quota_gb"] = quota.get("limit_gb") or 0
    else:
        user_data["storage_used_gb"] = None
        user_data["storage_quota_gb"] = 0
    return user_data


@app.post("/api/ldap/users")
async def create_user(user_data: UserCreate, user: dict = Depends(get_current_user)):
    """创建新 LDAP 用户，可选同时授予管理员权限。"""
    _require_admin(user)

    success = ldap_mgr.create_user(
        username=user_data.username,
        uid=user_data.uid,
        gid=user_data.gid,
        home=user_data.home,
        shell=user_data.shell,
        password=user_data.password,
    )
    if not success:
        raise HTTPException(status_code=500, detail="创建用户失败")

    # 同步到 Slurm 账户系统
    slurm_success = slurm_mgr.add_user_account(user_data.username)
    if not slurm_success:
        print(f"警告: Slurm 账户添加失败，但 LDAP 用户 {user_data.username} 已创建")

    # 处理管理员权限
    if user_data.is_admin:
        admin_mgr.add_admin(user_data.username)

    # 处理 NFS 配额（默认不限制）
    if quota_mgr:
        quota_mgr.set_user_quota(user_data.username, user_data.storage_quota_gb)

    return {
        "message": f"用户 {user_data.username} 创建成功",
        "is_admin": user_data.is_admin,
    }


@app.delete("/api/ldap/users/{username}")
async def delete_user(username: str, user: dict = Depends(get_current_user)):
    """删除用户（同时从管理员列表移除）。"""
    _require_admin(user)

    success = ldap_mgr.delete_user(username)
    if not success:
        raise HTTPException(status_code=500, detail="删除用户失败")

    # 从 Slurm 账户系统移除
    slurm_mgr.remove_user_account(username)

    # 如果该用户是管理员，同时移除管理员权限
    admin_mgr.remove_admin(username)

    return {"message": f"用户 {username} 已删除"}


@app.put("/api/ldap/users/{username}")
async def update_user(
    username: str, user_data: UserUpdate, user: dict = Depends(get_current_user)
):
    """更新用户信息，可选同步修改管理员权限。"""
    _require_admin(user)

    existing_user = ldap_mgr.get_user(username)
    if not existing_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user_data.is_admin is False and username == user.get("username"):
        raise HTTPException(status_code=400, detail="不能撤销自己的管理员权限")

    success = ldap_mgr.update_user(
        username=username,
        gid=user_data.gid,
        home=user_data.home,
        shell=user_data.shell,
        password=user_data.password,
        cn=user_data.cn,
    )
    if not success:
        raise HTTPException(status_code=500, detail="更新用户失败")

    # 处理管理员权限变更
    if user_data.is_admin is not None:
        if user_data.is_admin:
            admin_mgr.add_admin(username)
        else:
            admin_mgr.remove_admin(username)

    # 处理 NFS 配额更新（None = 不更新；0/负数 = 不限制）
    if user_data.storage_quota_gb is not None and quota_mgr:
        quota_mgr.set_user_quota(username, user_data.storage_quota_gb)

    return {
        "message": f"用户 {username} 更新成功",
        "is_admin": admin_mgr.is_admin(username),
    }


@app.post("/api/ldap/users/{username}/ssh-key")
async def reset_user_ssh_key(username: str, user: dict = Depends(get_current_user)):
    """生成并重置用户 SSH 密钥（仅管理员）。"""
    _require_admin(user)
    if not ldap_mgr.get_user(username):
        raise HTTPException(status_code=404, detail="用户不存在")

    try:
        private_key, public_key, key_type = _generate_ssh_key_pair(username)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="ssh-keygen 未安装或不可用")
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="生成 SSH 密钥失败")

    success = ldap_mgr.set_ssh_public_key(username, public_key)
    if not success:
        raise HTTPException(status_code=500, detail="写入 LDAP 失败")

    filename = f"{username}_id_{key_type}"
    return Response(
        content=private_key,
        media_type="application/octet-stream; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/ldap/groups")
async def get_groups(user: dict = Depends(get_current_user)):
    """获取所有 LDAP 组列表。"""
    _require_admin(user)
    groups = ldap_mgr.list_groups()
    return {"groups": groups, "count": len(groups)}


@app.get("/api/ldap/groups/{group_name}")
async def get_group(group_name: str, user: dict = Depends(get_current_user)):
    """获取单个组信息。"""
    _require_admin(user)
    group = ldap_mgr.get_group(group_name)
    if not group:
        raise HTTPException(status_code=404, detail="组不存在")
    return group


@app.post("/api/ldap/groups")
async def create_group(group: GroupCreate, user: dict = Depends(get_current_user)):
    """创建新组。"""
    _require_admin(user)
    success = ldap_mgr.create_group(
        group_name=group.name, gid=group.gid, description=group.description
    )
    if not success:
        raise HTTPException(status_code=500, detail="创建组失败")
    return {"message": f"组 {group.name} 创建成功"}


@app.put("/api/ldap/groups/{group_name}")
async def update_group(
    group_name: str, group: GroupUpdate, user: dict = Depends(get_current_user)
):
    """更新组信息。"""
    _require_admin(user)
    existing_group = ldap_mgr.get_group(group_name)
    if not existing_group:
        raise HTTPException(status_code=404, detail="组不存在")

    success = ldap_mgr.update_group(
        group_name=group_name, gid=group.gid, description=group.description
    )
    if not success:
        raise HTTPException(status_code=500, detail="更新组失败")
    return {"message": f"组 {group_name} 更新成功"}


@app.delete("/api/ldap/groups/{group_name}")
async def delete_group(group_name: str, user: dict = Depends(get_current_user)):
    """删除组。"""
    _require_admin(user)
    success = ldap_mgr.delete_group(group_name)
    if not success:
        raise HTTPException(status_code=500, detail="删除组失败")
    return {"message": f"组 {group_name} 已删除"}


@app.post("/api/ldap/groups/add-member")
async def add_group_member(
    data: GroupMemberUpdate, user: dict = Depends(get_current_user)
):
    """将用户添加到组。"""
    _require_admin(user)
    success = ldap_mgr.add_user_to_group(data.username, data.group_name)
    if not success:
        raise HTTPException(status_code=500, detail="添加组成员失败")
    return {"message": f"用户 {data.username} 已加入组 {data.group_name}"}


@app.post("/api/ldap/groups/remove-member")
async def remove_group_member(
    data: GroupMemberUpdate, user: dict = Depends(get_current_user)
):
    """从组中移除用户。"""
    _require_admin(user)
    success = ldap_mgr.remove_user_from_group(data.username, data.group_name)
    if not success:
        raise HTTPException(status_code=500, detail="移除组成员失败")
    return {"message": f"用户 {data.username} 已从组 {data.group_name} 移除"}


# ═══════════════════════════════════════════════════════════════════════════════
# Slurm 账户 API（仅管理员）
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/slurm/accounts")
async def get_accounts(user: dict = Depends(get_current_user)):
    """获取 Slurm 账户列表。"""
    _require_admin(user)
    accounts = slurm_mgr.list_accounts()
    return {"accounts": accounts, "count": len(accounts)}


@app.post("/api/slurm/accounts")
async def create_account(
    payload: AccountCreate, user: dict = Depends(get_current_user)
):
    """创建 Slurm 账户。"""
    _require_admin(user)
    success = slurm_mgr.create_account(
        name=payload.name,
        description=payload.description,
        organization=payload.organization,
    )
    if not success:
        raise HTTPException(status_code=500, detail="创建账户失败")
    return {"message": f"账户 {payload.name} 创建成功"}


@app.put("/api/slurm/accounts/{account_name}")
async def update_account(
    account_name: str, payload: AccountUpdate, user: dict = Depends(get_current_user)
):
    """更新 Slurm 账户。"""
    _require_admin(user)
    success = slurm_mgr.update_account(
        name=account_name,
        description=payload.description,
        organization=payload.organization,
    )
    if not success:
        raise HTTPException(status_code=500, detail="更新账户失败")
    return {"message": f"账户 {account_name} 更新成功"}


@app.delete("/api/slurm/accounts/{account_name}")
async def delete_account(account_name: str, user: dict = Depends(get_current_user)):
    """删除 Slurm 账户。"""
    _require_admin(user)
    success = slurm_mgr.delete_account(account_name)
    if not success:
        raise HTTPException(status_code=500, detail="删除账户失败")
    return {"message": f"账户 {account_name} 已删除"}


# ═══════════════════════════════════════════════════════════════════════════════
# Slurm 关联（集群用户）API（仅管理员）
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/slurm/associations")
async def get_associations(
    account: Optional[str] = None, user: dict = Depends(get_current_user)
):
    """获取 Slurm 关联列表。"""
    _require_admin(user)
    associations = slurm_mgr.list_associations(account=account)
    return {"associations": associations, "count": len(associations)}


@app.post("/api/slurm/associations")
async def create_association(
    payload: AssocCreate, user: dict = Depends(get_current_user)
):
    """创建 Slurm 用户关联。"""
    _require_admin(user)
    if not payload.username or not payload.account:
        raise HTTPException(status_code=400, detail="用户名和账户不能为空")
    success = slurm_mgr.create_association(
        username=payload.username,
        account=payload.account,
        partition=payload.partition,
        qos=payload.qos,
        default_qos=payload.default_qos,
    )
    if not success:
        raise HTTPException(status_code=500, detail="创建关联失败")
    return {"message": f"关联 {payload.username}/{payload.account} 创建成功"}


@app.put("/api/slurm/associations/{account_name}/{username}")
async def update_association(
    account_name: str,
    username: str,
    payload: AssocUpdate,
    user: dict = Depends(get_current_user),
):
    """更新 Slurm 用户关联。"""
    _require_admin(user)
    success = slurm_mgr.update_association(
        username=username,
        account=account_name,
        partition=payload.partition,
        qos=payload.qos,
        default_qos=payload.default_qos,
    )
    if not success:
        raise HTTPException(status_code=500, detail="更新关联失败")
    return {"message": f"关联 {username}/{account_name} 更新成功"}


@app.delete("/api/slurm/associations/{account_name}/{username}")
async def delete_association(
    account_name: str,
    username: str,
    partition: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """删除 Slurm 用户关联。"""
    _require_admin(user)
    success = slurm_mgr.delete_association(
        username=username, account=account_name, partition=partition
    )
    if not success:
        raise HTTPException(status_code=500, detail="删除关联失败")
    return {"message": f"关联 {username}/{account_name} 已删除"}


@app.post("/api/slurm/associations/{account_name}/{username}/tres-minutes")
async def set_association_tres_minutes(
    account_name: str,
    username: str,
    payload: AssocTRESMinutesUpdate,
    user: dict = Depends(get_current_user),
):
    """设置关联核时/卡时拨付（GrpTRESMins）。"""
    _require_admin(user)
    if payload.cpu_minutes is None and payload.gpu_minutes is None:
        raise HTTPException(status_code=400, detail="至少需要设置一种分钟额度")
    if payload.cpu_minutes is not None and payload.cpu_minutes < 0:
        raise HTTPException(status_code=400, detail="cpu_minutes 不能小于 0")
    if payload.gpu_minutes is not None and payload.gpu_minutes < 0:
        raise HTTPException(status_code=400, detail="gpu_minutes 不能小于 0")
    if payload.partition is not None and not slurm_mgr._is_valid_slurm_name(
        payload.partition
    ):
        raise HTTPException(status_code=400, detail="分区名称无效")

    success = slurm_mgr.set_association_tres_minutes(
        username=username,
        account=account_name,
        cpu_minutes=payload.cpu_minutes,
        gpu_minutes=payload.gpu_minutes,
        partition=payload.partition,
    )
    if not success:
        raise HTTPException(status_code=500, detail="设置核时/卡时失败")
    return {
        "message": f"关联 {username}/{account_name} 核时/卡时设置成功",
        "cpu_minutes": payload.cpu_minutes,
        "gpu_minutes": payload.gpu_minutes,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Slurm 分区 API（仅管理员）
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/slurm/partitions")
async def get_partitions(user: dict = Depends(get_current_user)):
    """获取分区列表。"""
    # 分区信息对所有登录用户开放（jobs 页面统计需要）
    partitions = slurm_mgr.list_partitions()
    return {"partitions": partitions, "count": len(partitions)}


@app.get("/api/slurm/partitions/{partition_name}")
async def get_partition(partition_name: str, user: dict = Depends(get_current_user)):
    """获取单个分区信息。"""
    partition = slurm_mgr.get_partition(partition_name)
    if not partition:
        raise HTTPException(status_code=404, detail="分区不存在")
    return partition


@app.post("/api/slurm/partitions")
async def create_partition(
    partition: PartitionCreate, user: dict = Depends(get_current_user)
):
    """创建新分区（仅管理员）。"""
    _require_admin(user)
    success = slurm_mgr.create_partition(
        name=partition.name,
        nodes=partition.nodes,
        default=partition.default,
        state=partition.state,
        max_time=partition.max_time,
        allow_groups=partition.allow_groups,
    )
    if not success:
        raise HTTPException(status_code=500, detail="创建分区失败")
    return {"message": f"分区 {partition.name} 创建成功"}


@app.put("/api/slurm/partitions/{partition_name}")
async def update_partition(
    partition_name: str,
    partition: PartitionUpdate,
    user: dict = Depends(get_current_user),
):
    """更新分区信息（仅管理员）。"""
    _require_admin(user)
    existing_partition = slurm_mgr.get_partition(partition_name)
    if not existing_partition:
        raise HTTPException(status_code=404, detail="分区不存在")

    success = slurm_mgr.update_partition(
        name=partition_name,
        state=partition.state,
        max_time=partition.max_time,
        allow_groups=partition.allow_groups,
        nodes=partition.nodes,
        default=partition.default,
    )
    if not success:
        raise HTTPException(status_code=500, detail="更新分区失败")
    return {"message": f"分区 {partition_name} 更新成功"}


@app.delete("/api/slurm/partitions/{partition_name}")
async def delete_partition(partition_name: str, user: dict = Depends(get_current_user)):
    """删除分区（仅管理员）。"""
    _require_admin(user)
    success = slurm_mgr.delete_partition(partition_name)
    if not success:
        raise HTTPException(status_code=500, detail="删除分区失败")
    return {"message": f"分区 {partition_name} 已删除"}


@app.get("/api/slurm/nodes")
async def get_nodes(user: dict = Depends(get_current_user)):
    """获取节点列表（仅管理员）。"""
    _require_admin(user)
    nodes = slurm_mgr.list_nodes()
    return {"nodes": nodes, "count": len(nodes)}


# ═══════════════════════════════════════════════════════════════════════════════
# Slurm 作业 API（所有登录用户可读；取消作业有权限检查）
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/slurm/jobs/stats")
async def get_job_stats(user: dict = Depends(get_current_user)):
    """获取作业统计信息。"""
    stats = slurm_mgr.get_job_stats()
    return stats


@app.get("/api/slurm/jobs/completed/recent")
async def get_completed_jobs(limit: int = 20, user: dict = Depends(get_current_user)):
    """获取最近完成的作业。"""
    jobs = slurm_mgr.list_completed_jobs(limit=limit)
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/api/slurm/jobs")
async def get_jobs(
    state: Optional[str] = None,
    partition: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """获取活动作业队列（所有登录用户可查看所有作业）。"""
    jobs = slurm_mgr.list_jobs(state=state, partition=partition)
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/api/slurm/jobs/{job_id}")
async def get_job_detail(job_id: str, user: dict = Depends(get_current_user)):
    """获取作业详细信息。"""
    job = slurm_mgr.get_job_detail(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")
    return job


@app.delete("/api/slurm/jobs/{job_id}")
async def cancel_job(job_id: str, user: dict = Depends(get_current_user)):
    """
    取消作业。

    权限规则：
    - 管理员：可取消任意作业
    - 普通用户：只能取消属于自己的作业
    """
    if not user.get("is_admin"):
        # 获取作业详情以验证所有权
        job = slurm_mgr.get_job_detail(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="作业不存在")
        # scontrol 返回的 UserId 格式为 "username(uid)"，提取纯用户名
        job_owner = job.get("UserId", "").split("(")[0].strip()
        current_username = user.get("username", "")
        if job_owner != current_username:
            raise HTTPException(
                status_code=403,
                detail=f"无权取消他人的作业（作业所有者: {job_owner}）",
            )

    success = slurm_mgr.cancel_job(job_id)
    if not success:
        raise HTTPException(status_code=500, detail="取消作业失败")
    return {"message": f"作业 {job_id} 已取消"}


@app.get("/api/slurm/jobs/{job_id}/output")
async def get_job_output(
    job_id: str, file_type: str, user: dict = Depends(get_current_user)
):
    """
    获取作业输出文件内容。普通用户只能读取自己的作业。

    Args:
        job_id:    作业 ID
        file_type: 'stdout' 或 'stderr'
    """
    if file_type not in ["stdout", "stderr"]:
        raise HTTPException(
            status_code=400, detail="file_type 必须是 'stdout' 或 'stderr'"
        )

    job = slurm_mgr.get_job_detail(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")

    owner = _job_owner(job)
    if not owner:
        raise HTTPException(status_code=403, detail="无法确认作业所有者")
    if not user.get("is_admin") and owner != user.get("username", ""):
        raise HTTPException(status_code=403, detail="无权查看他人的作业输出")

    allowed_roots = _job_output_allowed_roots(owner)
    if not allowed_roots:
        raise HTTPException(status_code=403, detail="未配置可读取的作业输出目录")

    result = slurm_mgr.read_job_output(
        job_id,
        file_type,
        allowed_roots=allowed_roots,
        job_detail=job,
    )

    if not result["success"]:
        error_status = 403 if result.get("forbidden") else 404
        raise HTTPException(
            status_code=error_status, detail=result.get("error", "读取文件失败")
        )

    return result


@app.get("/api/slurm/users/{username}/report")
async def get_user_report(
    username: str,
    range: str = "day",
    start: Optional[str] = None,
    end: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """获取用户历史作业与机时统计（仅管理员）。"""
    _require_admin(user)
    report = slurm_mgr.get_user_job_report(username, range, start, end)
    return report


@app.get("/api/slurm/users/{username}/report.csv")
async def export_user_report_csv(
    username: str,
    range: str = "day",
    start: Optional[str] = None,
    end: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """导出用户报表 CSV（仅管理员）。"""
    _require_admin(user)
    report = slurm_mgr.get_user_job_report(username, range, start, end)
    rows = report.get("jobs", [])

    header = [
        "job_id",
        "name",
        "state",
        "partition",
        "alloc_cpus",
        "alloc_gpus",
        "cpu_hours",
        "gpu_hours",
        "elapsed_hours",
        "submit_time",
        "start_time",
        "end_time",
    ]
    lines = [",".join(header)]
    for row in rows:
        values = [
            row.get("job_id", ""),
            row.get("name", ""),
            row.get("state", ""),
            row.get("partition", ""),
            row.get("alloc_cpus", ""),
            row.get("alloc_gpus", ""),
            row.get("cpu_hours", ""),
            row.get("gpu_hours", ""),
            row.get("elapsed_hours", ""),
            row.get("submit_time", ""),
            row.get("start_time", ""),
            row.get("end_time", ""),
        ]
        escaped = []
        for value in values:
            text = str(value)
            if '"' in text:
                text = text.replace('"', '""')
            if "," in text or "\n" in text or "\r" in text:
                text = f'"{text}"'
            escaped.append(text)
        lines.append(",".join(escaped))

    csv_content = "\n".join(lines)
    filename = f"{username}_report.csv"
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/slurm/users/{username}/credit")
async def allocate_user_credits(
    username: str,
    payload: UserCreditRequest,
    user: dict = Depends(get_current_user),
):
    """为用户增加核时/卡时额度（仅管理员）。"""
    _require_admin(user)
    if not slurm_mgr._is_valid_slurm_name(username) or (
        payload.account is not None
        and not slurm_mgr._is_valid_slurm_name(payload.account)
    ) or (
        payload.partition is not None
        and not slurm_mgr._is_valid_slurm_name(payload.partition)
    ):
        raise HTTPException(status_code=400, detail="用户名或 Slurm 账户名无效")
    cpu_hours = payload.cpu_hours if payload.cpu_hours is not None else payload.hours
    gpu_hours = payload.gpu_hours
    amounts = [value for value in (cpu_hours, gpu_hours) if value is not None]
    if not amounts or all(value == 0 for value in amounts):
        raise HTTPException(status_code=400, detail="核时或卡时至少一项不能为 0")
    if any(abs(value) > 1_000_000 for value in amounts):
        raise HTTPException(status_code=400, detail="单次调整不能超过 1000000 小时")
    if payload.reason and payload.reason not in {
        "project",
        "compensation",
        "grant",
        "correction",
        "other",
    }:
        raise HTTPException(status_code=400, detail="拨付原因无效")
    if payload.note and len(payload.note) > 500:
        raise HTTPException(status_code=400, detail="备注不能超过 500 个字符")

    grant_kwargs = {
        "username": username,
        "account": payload.account,
        "cpu_hours": cpu_hours,
        "gpu_hours": gpu_hours,
    }
    if payload.partition is not None:
        grant_kwargs["partition"] = payload.partition
    grant = slurm_mgr.grant_user_tres_hours(
        **grant_kwargs
    )
    if grant is None:
        raise HTTPException(status_code=500, detail="核时/卡时拨付失败")

    print(
        "核时/卡时拨付",
        {
            "username": username,
            "account": grant["account"],
            "cpu_hours": cpu_hours,
            "gpu_hours": gpu_hours,
            "reason": payload.reason,
            "note": payload.note,
            "operator": user.get("username"),
        },
    )

    return {
        "message": (
            f"已为 {username} 调整额度，剩余核时 "
            f"{round(grant['remaining_cpu_minutes'] / 60, 2)} h，剩余卡时 "
            f"{round(grant['remaining_gpu_minutes'] / 60, 2)} h"
        ),
        "username": username,
        "account": grant["account"],
        "partition": grant.get("partition"),
        "cpu_granted_hours": round(grant["cpu_granted_minutes"] / 60, 2),
        "gpu_granted_hours": round(grant["gpu_granted_minutes"] / 60, 2),
        "remaining_cpu_hours": round(grant["remaining_cpu_minutes"] / 60, 2),
        "remaining_gpu_hours": round(grant["remaining_gpu_minutes"] / 60, 2),
        "reason": payload.reason,
        "note": payload.note,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 节点管理 API（仅管理员）
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/slurm/nodes/config")
async def get_nodes_config(user: dict = Depends(get_current_user)):
    """获取配置文件中的所有节点（仅管理员）。"""
    _require_admin(user)
    nodes = slurm_mgr.list_nodes_from_config()
    return {"nodes": nodes, "count": len(nodes)}


@app.get("/api/slurm/nodes/{node_name}")
async def get_node(node_name: str, user: dict = Depends(get_current_user)):
    """获取节点详细运行时信息（仅管理员）。"""
    _require_admin(user)
    node = slurm_mgr.get_node_detail(node_name)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node


@app.get("/api/slurm/nodes/{node_name}/config")
async def get_node_config(node_name: str, user: dict = Depends(get_current_user)):
    """获取节点配置信息（仅管理员）。"""
    _require_admin(user)
    node = slurm_mgr.get_node_from_config(node_name)
    if not node:
        raise HTTPException(status_code=404, detail="节点配置不存在")
    return node


@app.post("/api/slurm/nodes")
async def create_node(node: NodeCreate, user: dict = Depends(get_current_user)):
    """添加新节点到配置文件（仅管理员）。"""
    _require_admin(user)
    success = slurm_mgr.add_node_config(
        node_name=node.name,
        cpus=node.cpus,
        boards=node.boards,
        sockets_per_board=node.sockets_per_board,
        cores_per_socket=node.cores_per_socket,
        threads_per_core=node.threads_per_core,
        real_memory=node.real_memory,
        gres=node.gres,
    )
    if not success:
        raise HTTPException(status_code=500, detail="添加节点失败")
    return {"message": f"节点 {node.name} 添加成功"}


@app.put("/api/slurm/nodes/{node_name}/config")
async def update_node_config(
    node_name: str, node: NodeUpdate, user: dict = Depends(get_current_user)
):
    """更新节点配置（仅管理员）。"""
    _require_admin(user)
    existing_node = slurm_mgr.get_node_from_config(node_name)
    if not existing_node:
        raise HTTPException(status_code=404, detail="节点配置不存在")

    success = slurm_mgr.update_node_config(
        node_name=node_name,
        cpus=node.cpus,
        boards=node.boards,
        sockets_per_board=node.sockets_per_board,
        cores_per_socket=node.cores_per_socket,
        threads_per_core=node.threads_per_core,
        real_memory=node.real_memory,
        gres=node.gres,
    )
    if not success:
        raise HTTPException(status_code=500, detail="更新节点配置失败")
    return {"message": f"节点 {node_name} 配置更新成功"}


@app.delete("/api/slurm/nodes/{node_name}/config")
async def delete_node_config(node_name: str, user: dict = Depends(get_current_user)):
    """从配置文件删除节点（仅管理员）。"""
    _require_admin(user)
    try:
        success = slurm_mgr.delete_node_config(node_name)
        if not success:
            raise HTTPException(status_code=500, detail="删除节点配置失败")
        return {"message": f"节点 {node_name} 配置已删除"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/slurm/nodes/{node_name}/drain")
async def drain_node(
    node_name: str,
    reason: Optional[str] = "管理员手动下线",
    user: dict = Depends(get_current_user),
):
    """下线节点 DRAIN（仅管理员）。"""
    _require_admin(user)
    success = slurm_mgr.drain_node(node_name, reason)
    if not success:
        raise HTTPException(status_code=500, detail="下线节点失败")
    return {"message": f"节点 {node_name} 已下线"}


@app.post("/api/slurm/nodes/{node_name}/resume")
async def resume_node(node_name: str, user: dict = Depends(get_current_user)):
    """恢复节点上线 RESUME（仅管理员）。"""
    _require_admin(user)
    success = slurm_mgr.resume_node(node_name)
    if not success:
        raise HTTPException(status_code=500, detail="恢复节点上线失败")
    return {"message": f"节点 {node_name} 已上线"}


@app.put("/api/slurm/nodes/{node_name}/state")
async def update_node_state(
    node_name: str,
    state_data: NodeStateUpdate,
    user: dict = Depends(get_current_user),
):
    """更新节点状态（仅管理员）。"""
    _require_admin(user)
    success = slurm_mgr.update_node_state(
        node_name, state_data.state, state_data.reason
    )
    if not success:
        raise HTTPException(status_code=500, detail="更新节点状态失败")
    return {"message": f"节点 {node_name} 状态已更新为 {state_data.state}"}
