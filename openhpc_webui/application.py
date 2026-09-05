import asyncio
import json
import logging
import os
import pwd
import re
import secrets
import shlex
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import NoReturn, Optional
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .audit import AuditMiddleware, log_event, sanitize
from .config import STATIC_DIR, TEMPLATES_DIR, settings
from .schemas import (
    AccountCreate,
    AccountUpdate,
    AccountTRESMinutesUpdate,
    QosCreate,
    QosUpdate,
    AdminUserRequest,
    AssocCreate,
    AssocTRESMinutesUpdate,
    AssocUpdate,
    GroupCreate,
    GroupMemberUpdate,
    GroupUpdate,
    FileDirectoryCreate,
    FileContentUpdate,
    FileRenameRequest,
    LoginRequest,
    NodeCreate,
    NodeStateUpdate,
    NodeUpdate,
    PartitionCreate,
    PartitionUpdate,
    PasswordChangeRequest,
    TerminalAnnouncementSettingsUpdate,
    TerminalAISettingsUpdate,
    UserCreate,
    UserCreditRequest,
    UserQuotaUpdate,
    UserUpdate,
)
from .services import admin_manager as admin_mgr
from .services.auth_manager import AuthenticationServiceError, AuthManager
from .services.file_manager import FileAccessDenied, FileManager, FileManagerError
from .services.ldap_manager import LDAPManager
from .services.login_limiter import LoginAttemptLimiter
from .services.nfs_quota_manager import NFSQuotaManager
from .services.slurm_manager import SlurmManager
from .services.terminal_manager import TerminalError, TerminalManager, TerminalSession
from .services.terminal_announcement import (
    TerminalAnnouncementError,
    public_config as public_terminal_announcement_config,
    save_config as save_terminal_announcement_config,
)
from .services.terminal_ai import (
    TerminalAIClient,
    TerminalAIError,
    TerminalAIReply,
    clean_terminal_output,
    command_requires_confirmation,
    get_config as get_terminal_ai_config,
    is_probable_command,
    public_config as public_terminal_ai_config,
    save_config as save_terminal_ai_config,
    validate_model_name,
)

AUTH_ENABLED = settings.auth_enabled

_INSECURE_SECRET_KEYS = {
    "",
    "change-me-in-production",
    "your-secret-key-change-in-production",
}

_DISABLED_LOGIN_SHELLS = {
    "/bin/false",
    "/sbin/nologin",
    "/usr/sbin/nologin",
}

_CREDIT_COMMENT_INPUT_MAX_LENGTH = 478


def _timestamp_credit_comment(comment: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"[{timestamp}] {comment.strip()}"


def _is_disabled_login_shell(shell: Optional[str]) -> bool:
    return (shell or "").strip() in _DISABLED_LOGIN_SHELLS


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

router = APIRouter()

# Paths are anchored to the package, so the server can start from any directory.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["app_version"] = __version__
templates.env.globals["terminal_enabled"] = settings.terminal_enabled and AUTH_ENABLED
templates.env.globals["terminal_script_version"] = str(
    (STATIC_DIR / "terminal.js").stat().st_mtime_ns
)

# Initialize managers
ldap_mgr = LDAPManager()
slurm_mgr = SlurmManager()
auth_mgr = AuthManager()
quota_mgr = NFSQuotaManager()
file_mgr = FileManager(
    max_upload_bytes=settings.file_upload_max_mb * 1024 * 1024,
    max_edit_bytes=settings.file_edit_max_kb * 1024,
)
login_limiter = LoginAttemptLimiter(
    max_failures=settings.login_max_failed_attempts,
    lockout_seconds=settings.login_lockout_minutes * 60,
)
terminal_mgr = TerminalManager(max_sessions_per_user=settings.terminal_max_sessions)
terminal_ai_client = TerminalAIClient()
logger = logging.getLogger(__name__)


def _pick_fields(value: Optional[dict], fields: tuple[str, ...]) -> Optional[dict]:
    if not value:
        return None
    return {field: value.get(field) for field in fields if field in value}


async def _audit_snapshot(request: Request, body: dict):
    """Read the current persisted state for the resource changed by a request."""
    path = request.url.path

    if not AUTH_ENABLED:
        request.state.audit_actor = _DEBUG_USER["username"]

    if path.startswith("/api/auth/"):
        if path.endswith("change-password"):
            return {"password": "[REDACTED]"}
        return {"authenticated": bool(request.session.get("user"))}

    if AUTH_ENABLED and not request.session.get("user"):
        return {"access": "unauthenticated"}

    match = re.fullmatch(r"/api/admin(?:/([^/]+))?", path)
    if match:
        username = match.group(1) or body.get("username")
        return {"username": username, "is_admin": admin_mgr.is_admin(username or "")}

    match = re.fullmatch(
        r"/api/ldap/users(?:/([^/]+)(?:/(?:disable|enable|quota|ssh-key))?)?",
        path,
    )
    if match:
        username = match.group(1) or body.get("username")
        record = _pick_fields(
            ldap_mgr.get_user(username) if username else None,
            ("username", "uid", "gid", "home", "shell", "cn", "sn"),
        )
        if record is not None:
            record["is_admin"] = admin_mgr.is_admin(username)
            if path.endswith("/quota"):
                record["storage_quota"] = quota_mgr.get_user_quota(username)
        return record

    if path.endswith("/add-member") or path.endswith("/remove-member"):
        group_name = body.get("group_name")
        return _pick_fields(
            ldap_mgr.get_group(group_name) if group_name else None,
            ("name", "gid", "description", "members"),
        )

    match = re.fullmatch(r"/api/ldap/groups(?:/([^/]+))?", path)
    if match:
        group_name = match.group(1) or body.get("name")
        return _pick_fields(
            ldap_mgr.get_group(group_name) if group_name else None,
            ("name", "gid", "description", "members"),
        )

    match = re.fullmatch(r"/api/slurm/accounts(?:/([^/]+)(?:/tres-minutes)?)?", path)
    if match:
        account_name = match.group(1) or body.get("name")
        account = next(
            (item for item in slurm_mgr.list_accounts() if item.get("name") == account_name),
            None,
        )
        snapshot = _pick_fields(account, ("name", "description", "organization"))
        if snapshot is not None and path.endswith("/tres-minutes"):
            snapshot["tres_minutes"] = slurm_mgr.get_account_tres_minutes(account_name)
        return snapshot

    match = re.fullmatch(r"/api/slurm/qos(?:/([^/]+))?", path)
    if match:
        qos_name = match.group(1) or body.get("name")
        return next((item for item in slurm_mgr.list_qos() if item.get("name") == qos_name), None)

    match = re.fullmatch(r"/api/slurm/associations(?:/([^/]+)/([^/]+)(?:/tres-minutes)?)?", path)
    if match:
        account_name = match.group(1) or body.get("account")
        username = match.group(2) or body.get("username")
        return next(
            (
                item
                for item in slurm_mgr.list_associations(account=account_name)
                if item.get("user") == username
                and (not body.get("partition") or item.get("partition") == body.get("partition"))
            ),
            None,
        )

    match = re.fullmatch(r"/api/slurm/partitions(?:/([^/]+))?", path)
    if match:
        name = match.group(1) or body.get("name")
        return slurm_mgr.config_mgr.get_partition(name) if name else None

    match = re.fullmatch(r"/api/slurm/nodes(?:/([^/]+)(?:/(?:config|drain|resume|state))?)?", path)
    if match:
        name = match.group(1) or body.get("name")
        if not name:
            return None
        if path.endswith("/config") or request.method == "DELETE" or (request.method == "POST" and path == "/api/slurm/nodes"):
            return slurm_mgr.get_node_from_config(name)
        return _pick_fields(
            slurm_mgr.get_node_detail(name),
            ("name", "state", "reason", "cpus", "memory", "gres"),
        )

    match = re.fullmatch(r"/api/slurm/jobs/([^/]+)", path)
    if match:
        return _pick_fields(slurm_mgr.get_job_detail(match.group(1)), ("JobId", "JobState", "UserId", "Partition"))

    match = re.fullmatch(r"/api/slurm/users/([^/]+)/credit", path)
    if match:
        username = match.group(1)
        limits = slurm_mgr.get_users_tres_limits().get(username)
        return sanitize(limits)

    return None


# ── 异常处理 ────────────────────────────────────────────────────────────────


async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """未登录 → 登录页；非管理员访问管理页 → 作业页；其余返回 JSON。"""
    if exc.status_code == 401:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=401, content={"detail": exc.detail}, headers=exc.headers
            )
        return RedirectResponse(url="/login", status_code=302)
    if exc.status_code == 403:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=403, content={"detail": exc.detail}, headers=exc.headers
            )
        return RedirectResponse(url="/jobs", status_code=302)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


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

    username = user.get("username", "")
    current_shell = ldap_mgr.get_user_login_shell(username)
    if _is_disabled_login_shell(current_shell):
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="账户已禁用"
        )

    # 每次请求动态刷新 is_admin（管理员变更立即生效，无需重新登录）
    user = dict(user)
    user["is_admin"] = admin_mgr.is_admin(username)
    return user


async def get_current_user_optional(request: Request) -> Optional[dict]:
    """可选认证；AUTHORIZED=False 时返回调试用户。"""
    if not AUTH_ENABLED:
        user = dict(request.session.get("user") or _DEBUG_USER)
        user["is_admin"] = True
        return user
    user = request.session.get("user")
    if user:
        current_shell = ldap_mgr.get_user_login_shell(user.get("username", ""))
        if _is_disabled_login_shell(current_shell):
            request.session.clear()
            return None
        user = dict(user)
        user["is_admin"] = admin_mgr.is_admin(user.get("username", ""))
    return user


def _require_admin(user: dict) -> None:
    """若 user 不是管理员则抛出 403，供页面路由和 API 统一调用。"""
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限"
        )


def _file_scope(user: dict) -> Path:
    """Return the caller's filesystem root and normalize service failures."""
    try:
        return file_mgr.scope_root(user, ldap_mgr)
    except FileAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _file_home_path(user: dict) -> str:
    """Return the initial virtual path for file management.

    Regular users already see their Home as virtual ``/``. Administrators keep
    a system-root scope, but start in the Home reported for their own NSS
    identity instead of inheriting root's Home.
    """
    if not user.get("is_admin"):
        return "/"
    username = str(user.get("username") or "").strip()
    if not username:
        return "/"
    try:
        home = Path(pwd.getpwnam(username).pw_dir)
        resolved = home.resolve(strict=True)
    except (KeyError, OSError, RuntimeError):
        return "/"
    return str(resolved) if resolved.is_dir() and resolved.is_absolute() else "/"


def _raise_file_error(exc: FileManagerError) -> NoReturn:
    if isinstance(exc, FileAccessDenied):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _raise_login_failure(request: Request, username: str) -> NoReturn:
    retry_after = login_limiter.record_failure(username)
    request.state.audit_result_detail = (
        "account_locked" if retry_after else "invalid_credentials"
    )
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录失败次数过多，账户已临时锁定",
            headers={"Retry-After": str(retry_after)},
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
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


# ═══════════════════════════════════════════════════════════════════════════════
# 认证路由
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页面；AUTHORIZED=False 时直接跳转首页。"""
    if not AUTH_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    if request.session.get("user"):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/api/auth/login")
async def login(request: Request, login_data: LoginRequest):
    """处理登录请求，成功后将用户信息写入 session。"""
    retry_after = login_limiter.retry_after(login_data.username)
    if retry_after:
        request.state.audit_result_detail = "account_locked"
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录失败次数过多，账户已临时锁定",
            headers={"Retry-After": str(retry_after)},
        )

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", login_data.username):
        _raise_login_failure(request, login_data.username)

    try:
        user_info = auth_mgr.authenticate_user(login_data.username, login_data.password)
    except AuthenticationServiceError:
        request.state.audit_result_detail = "authentication_service_unavailable"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试",
        )
    if not user_info:
        _raise_login_failure(request, login_data.username)
    login_shell = user_info.get("shell")
    if login_shell is None:
        login_shell = ldap_mgr.get_user_login_shell(login_data.username)
    if _is_disabled_login_shell(login_shell):
        _raise_login_failure(request, login_data.username)
    login_limiter.record_success(login_data.username)
    request.state.audit_result_detail = "authenticated"
    # is_admin 不写入 session，每次请求动态计算（保证权限变更即时生效）
    request.session["user"] = user_info
    # 返回时附上当前 is_admin 状态供前端使用
    user_info = dict(user_info)
    user_info["is_admin"] = admin_mgr.is_admin(user_info.get("username", ""))
    return {"message": "登录成功", "user": user_info}


@router.post("/api/auth/logout")
async def logout(request: Request):
    """清除 session。"""
    request.session.clear()
    return {"message": "已登出"}


@router.post("/api/auth/change-password")
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

    try:
        verified = auth_mgr.authenticate_user(username, payload.current_password)
    except AuthenticationServiceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试",
        )
    if not verified:
        raise HTTPException(status_code=400, detail="当前密码不正确")

    success = ldap_mgr.update_user(username=username, password=payload.new_password)
    if not success:
        raise HTTPException(status_code=500, detail="密码更新失败")
    return {"message": "密码已更新"}


@router.get("/api/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    """获取当前登录用户信息（含实时 is_admin 状态）。"""
    return user


# ═══════════════════════════════════════════════════════════════════════════════
# 页面路由（受保护）
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(get_current_user)):
    """根据用户角色显示管理员或个人总览。"""
    if not user.get("is_admin"):
        return templates.TemplateResponse(
            "user_dashboard.html", {"request": request, "user": user}
        )
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, user: dict = Depends(get_current_user)):
    """用户管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("users.html", {"request": request, "user": user})


@router.get("/groups", response_class=HTMLResponse)
async def groups_page(request: Request, user: dict = Depends(get_current_user)):
    """组管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("groups.html", {"request": request, "user": user})


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request, user: dict = Depends(get_current_user)):
    """账户管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("accounts.html", {"request": request, "user": user})


@router.get("/qos", response_class=HTMLResponse)
async def qos_page(request: Request, user: dict = Depends(get_current_user)):
    """Slurm QoS 管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("qos.html", {"request": request, "user": user})


@router.get("/cluster-users", response_class=HTMLResponse)
async def cluster_users_page(request: Request, user: dict = Depends(get_current_user)):
    """集群用户管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse(
        "cluster_users.html", {"request": request, "user": user}
    )


@router.get("/partitions", response_class=HTMLResponse)
async def partitions_page(request: Request, user: dict = Depends(get_current_user)):
    """分区管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse(
        "partitions.html", {"request": request, "user": user}
    )


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request, user: dict = Depends(get_current_user)):
    """作业管理：所有登录用户均可访问。"""
    return templates.TemplateResponse("jobs.html", {"request": request, "user": user})


@router.get("/slurm-guide", response_class=HTMLResponse)
async def slurm_guide_page(request: Request, user: dict = Depends(get_current_user)):
    """Slurm 操作手册：所有登录用户均可访问。"""
    return templates.TemplateResponse(
        "slurm_guide.html", {"request": request, "user": user}
    )


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, user: dict = Depends(get_current_user)):
    """个人账户设置：当前用户可查看身份并修改自己的密码。"""
    return templates.TemplateResponse("account.html", {"request": request, "user": user})


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: dict = Depends(get_current_user)):
    """系统设置：仅管理员可维护全局门户配置。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("settings.html", {"request": request, "user": user})


@router.get("/api/terminal/ai/settings")
async def get_terminal_ai_settings(user: dict = Depends(get_current_user)):
    """Return public AI status, with endpoint details only for administrators."""
    return public_terminal_ai_config(include_endpoint=bool(user.get("is_admin")))


@router.put("/api/terminal/ai/settings")
async def update_terminal_ai_settings(
    payload: TerminalAISettingsUpdate, user: dict = Depends(get_current_user)
):
    """Persist the global OpenAI-compatible terminal model configuration."""
    _require_admin(user)
    try:
        result = await run_in_threadpool(
            save_terminal_ai_config,
            enabled=payload.enabled,
            provider=payload.provider,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key,
            timeout_seconds=payload.timeout_seconds,
        )
    except TerminalAIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "终端 AI 配置已保存并立即生效", **result}


@router.get("/api/terminal/announcement/settings")
async def get_terminal_announcement_settings(
    user: dict = Depends(get_current_user),
):
    """Return terminal announcement settings to administrators."""
    _require_admin(user)
    return public_terminal_announcement_config()


@router.put("/api/terminal/announcement/settings")
async def update_terminal_announcement_settings(
    payload: TerminalAnnouncementSettingsUpdate,
    user: dict = Depends(get_current_user),
):
    """Persist the global terminal announcement shown to terminal users."""
    _require_admin(user)
    try:
        result = await run_in_threadpool(
            save_terminal_announcement_config,
            enabled=payload.enabled,
            message=payload.message,
            text_color=payload.text_color,
            background_color=payload.background_color,
            bold=payload.bold,
        )
    except TerminalAnnouncementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "终端公告已保存并立即生效", **result}


@router.get("/files", response_class=HTMLResponse)
async def files_page(request: Request, user: dict = Depends(get_current_user)):
    """文件管理：普通用户以 Home 为根，管理员以系统根目录为根。"""
    return templates.TemplateResponse(
        "files.html",
        {
            "request": request,
            "user": user,
            "initial_path": _file_home_path(user),
        },
    )


@router.get("/nodes", response_class=HTMLResponse)
async def nodes_page(request: Request, user: dict = Depends(get_current_user)):
    """节点管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("nodes.html", {"request": request, "user": user})


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: dict = Depends(get_current_user)):
    """权限管理：仅管理员可访问。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/jobs", status_code=302)
    return templates.TemplateResponse("admin.html", {"request": request, "user": user})


# ═══════════════════════════════════════════════════════════════════════════════
# Web 终端
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/terminal", response_class=HTMLResponse)
async def terminal_page(request: Request, user: dict = Depends(get_current_user)):
    """xterm.js terminal page for authenticated system users."""
    if not settings.terminal_enabled or not AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="终端功能未启用")
    return templates.TemplateResponse(
        "terminal.html",
        {
            "request": request,
            "user": user,
            "terminal_announcement": public_terminal_announcement_config(),
        },
    )


def _websocket_origin_is_valid(websocket: WebSocket) -> bool:
    """Prevent another website from opening a credentialed terminal socket."""
    origin = websocket.headers.get("origin", "").strip()
    host = websocket.headers.get("host", "").strip().lower()
    if not origin or not host:
        return False
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host


@dataclass
class _TerminalAIState:
    history: list[dict[str, str]] = field(default_factory=list)
    active_goal: Optional[str] = None
    pending_command: Optional[str] = None
    active_command: Optional[str] = None
    start_marker: Optional[bytes] = None
    marker: Optional[bytes] = None
    capture: bytearray = field(default_factory=bytearray)
    display_pending: bytearray = field(default_factory=bytearray)
    auto_approve: bool = False
    loop_active: bool = False
    step_count: int = 0
    max_steps: int = 10
    model: Optional[str] = None


_TERMINAL_AI_DEFAULT_MAX_STEPS = 10
_TERMINAL_AI_MAX_ALLOWED_STEPS = 50


async def _send_ws_json(
    websocket: WebSocket, send_lock: asyncio.Lock, payload: dict
) -> None:
    async with send_lock:
        await websocket.send_json(payload)


async def _send_ws_bytes(
    websocket: WebSocket, send_lock: asyncio.Lock, payload: bytes
) -> None:
    if not payload:
        return
    async with send_lock:
        await websocket.send_bytes(payload)


async def _execute_ai_command(
    websocket: WebSocket,
    session: TerminalSession,
    last_activity: list[float],
    ai_state: _TerminalAIState,
    send_lock: asyncio.Lock,
    *,
    automatic: bool = False,
) -> bool:
    command = ai_state.pending_command
    if not command:
        await _send_ws_json(
            websocket, send_lock,
            {"type": "ai_error", "message": "没有待确认的 AI 命令"},
        )
        return False
    if ai_state.active_command:
        await _send_ws_json(
            websocket, send_lock,
            {"type": "ai_error", "message": "上一条 AI 命令仍在执行"},
        )
        return False
    if ai_state.step_count >= ai_state.max_steps:
        ai_state.pending_command = None
        ai_state.loop_active = False
        ai_state.active_goal = None
        await _send_ws_json(
            websocket,
            send_lock,
            {
                "type": "ai_loop_stopped",
                "message": f"已达到当前设置的 {ai_state.max_steps} 步上限，循环已停止",
            },
        )
        return False

    token = secrets.token_hex(16)
    start_marker = f"__OPENHPC_AI_START_{token}__".encode("ascii")
    marker = f"__OPENHPC_AI_DONE_{token}__".encode("ascii")
    ai_state.pending_command = None
    ai_state.active_command = command
    ai_state.start_marker = start_marker
    ai_state.marker = marker
    ai_state.capture.clear()
    ai_state.display_pending.clear()
    ai_state.step_count += 1
    wrapper = (
        f"__oh_sa=__OPENHPC_AI_; __oh_sb=START_{token}__; "
        "printf '%s%s\\n' \"$__oh_sa\" \"$__oh_sb\"; "
        f"eval {shlex.quote(command)}; __oh_status=$?; __oh_a=__OPENHPC_AI_; "
        f"__oh_b=DONE_{token}__; printf '\\n%s%s:%s\\n' "
        '"$__oh_a" "$__oh_b" "$__oh_status"\r'
    )
    session.write(b"\x15" + wrapper.encode("utf-8"))
    last_activity[0] = time.monotonic()
    await _send_ws_json(
        websocket,
        send_lock,
        {
            "type": "ai_executing",
            "command": command,
            "step": ai_state.step_count,
            "max_steps": ai_state.max_steps,
            "auto_approved": automatic,
        },
    )
    return True


async def _present_ai_reply(
    websocket: WebSocket,
    session: TerminalSession,
    last_activity: list[float],
    ai_state: _TerminalAIState,
    send_lock: asyncio.Lock,
    reply: TerminalAIReply,
    *,
    message_type: str,
    exit_code: Optional[int] = None,
) -> None:
    command = reply.command
    forced_confirmation = bool(
        command
        and (reply.requires_confirmation or command_requires_confirmation(command))
    )
    stopped_by_limit = bool(command and ai_state.step_count >= ai_state.max_steps)
    if stopped_by_limit:
        command = None
        ai_state.loop_active = False
    else:
        ai_state.loop_active = bool(command) or not reply.done
    if not command and reply.done:
        ai_state.active_goal = None
    ai_state.pending_command = command
    payload = {
        "type": message_type,
        "answer" if message_type == "ai_reply" else "summary": reply.answer,
        "command": command,
        "done": True if stopped_by_limit else (reply.done and not bool(command)),
        "turns": len(ai_state.history) // 2,
        "step": ai_state.step_count,
        "max_steps": ai_state.max_steps,
        "auto_approve": ai_state.auto_approve,
        "requires_confirmation": forced_confirmation,
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    await _send_ws_json(websocket, send_lock, payload)
    if reply.command and command is None:
        await _send_ws_json(
            websocket,
            send_lock,
            {
                "type": "ai_loop_stopped",
                "message": f"已达到当前设置的 {ai_state.max_steps} 步上限，循环已停止",
            },
        )
    elif command and ai_state.auto_approve and not forced_confirmation:
        await _execute_ai_command(
            websocket, session, last_activity, ai_state, send_lock, automatic=True
        )


async def _send_terminal_output(
    websocket: WebSocket,
    session: TerminalSession,
    last_activity: list[float],
    ai_state: _TerminalAIState,
    send_lock: asyncio.Lock,
) -> None:
    while True:
        data = await asyncio.to_thread(session.read)
        if not data:
            return
        last_activity[0] = time.monotonic()
        start_marker = ai_state.start_marker
        if start_marker:
            ai_state.display_pending.extend(data)
            start_index = ai_state.display_pending.find(start_marker)
            if start_index < 0:
                # Discard the wrapper echo, retaining only a possible partial marker.
                keep = len(start_marker) + 4
                if len(ai_state.display_pending) > keep:
                    del ai_state.display_pending[:-keep]
                continue
            suffix = ai_state.display_pending[start_index + len(start_marker):]
            line_end = re.match(rb"\r?\n", suffix)
            if not line_end:
                continue
            data = bytes(suffix[line_end.end():])
            ai_state.start_marker = None
            ai_state.capture.clear()
            ai_state.display_pending.clear()
            if not data:
                continue

        marker = ai_state.marker
        if not marker:
            await _send_ws_bytes(websocket, send_lock, data)
            continue

        ai_state.capture.extend(data)
        if len(ai_state.capture) > 131_072:
            del ai_state.capture[:-131_072]
        ai_state.display_pending.extend(data)
        marker_index = ai_state.display_pending.find(marker)
        if marker_index < 0:
            keep = len(marker) + 32
            if len(ai_state.display_pending) > keep:
                ready_bytes = bytes(ai_state.display_pending[:-keep])
                del ai_state.display_pending[:-keep]
                await _send_ws_bytes(websocket, send_lock, ready_bytes)
            continue

        suffix = ai_state.display_pending[marker_index + len(marker):]
        match = re.match(rb":(-?\d+)\r?\n", suffix)
        if not match:
            # The status line may be split across PTY reads.
            continue
        before_marker = bytes(ai_state.display_pending[:marker_index])
        remainder = bytes(suffix[match.end():])
        await _send_ws_bytes(websocket, send_lock, before_marker)
        command = ai_state.active_command or ""
        capture_marker_index = ai_state.capture.find(marker)
        captured = (
            bytes(ai_state.capture[:capture_marker_index])
            if capture_marker_index >= 0
            else bytes(ai_state.capture)
        )
        output = clean_terminal_output(captured)
        exit_code = int(match.group(1))
        ai_state.marker = None
        ai_state.active_command = None
        ai_state.capture.clear()
        ai_state.display_pending.clear()
        try:
            reply = await terminal_ai_client.analyze_and_continue(
                ai_state.history,
                command,
                output,
                exit_code,
                ai_state.active_goal,
                model=ai_state.model,
            )
            await _present_ai_reply(
                websocket,
                session,
                last_activity,
                ai_state,
                send_lock,
                reply,
                message_type="ai_summary",
                exit_code=exit_code,
            )
        except TerminalAIError as exc:
            ai_state.loop_active = False
            await _send_ws_json(
                websocket, send_lock, {"type": "ai_error", "message": str(exc)}
            )
        await _send_ws_bytes(websocket, send_lock, remainder)


async def _receive_terminal_input(
    websocket: WebSocket,
    session: TerminalSession,
    last_activity: list[float],
    ai_state: _TerminalAIState,
    send_lock: asyncio.Lock,
) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        text = message.get("text")
        if text is None or len(text) > 70_000:
            await _send_ws_json(websocket, send_lock, {"type": "error", "message": "终端消息无效"})
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            await _send_ws_json(websocket, send_lock, {"type": "error", "message": "终端消息格式无效"})
            continue
        if not isinstance(payload, dict):
            continue

        message_type = payload.get("type")
        if message_type == "input":
            data = str(payload.get("data", "")).encode("utf-8")
            if len(data) > 65_536:
                await _send_ws_json(websocket, send_lock, {"type": "error", "message": "单次输入过长"})
                continue
            session.write(data)
            last_activity[0] = time.monotonic()
        elif message_type == "resize":
            try:
                cols = max(20, min(500, int(payload.get("cols", 120))))
                rows = max(5, min(200, int(payload.get("rows", 32))))
            except (TypeError, ValueError):
                continue
            session.resize(cols, rows)
            last_activity[0] = time.monotonic()
        elif message_type == "submit":
            line = str(payload.get("line", ""))
            if len(line.encode("utf-8")) > 4096:
                await _send_ws_json(websocket, send_lock, {"type": "ai_error", "message": "输入过长"})
                continue
            continuing_goal = bool(
                ai_state.loop_active
                and ai_state.active_goal
                and not ai_state.pending_command
                and not ai_state.active_command
            )
            if line.strip():
                ai_state.pending_command = None
            config = get_terminal_ai_config()
            if not config.available or is_probable_command(line):
                if line.strip():
                    ai_state.loop_active = False
                    ai_state.step_count = 0
                    ai_state.active_goal = None
                if line.lstrip().startswith("!"):
                    forced = line.lstrip()[1:].lstrip()
                    session.write(b"\x15" + forced.encode("utf-8") + b"\r")
                else:
                    session.write(b"\r")
                last_activity[0] = time.monotonic()
                continue
            session.write(b"\x15\r")
            if not continuing_goal:
                ai_state.loop_active = False
                ai_state.step_count = 0
                ai_state.active_goal = line.strip()
            await _send_ws_json(websocket, send_lock, {"type": "ai_thinking"})
            try:
                reply = await terminal_ai_client.ask(
                    line.strip(),
                    ai_state.history,
                    ai_state.active_goal,
                    model=ai_state.model,
                )
            except TerminalAIError as exc:
                await _send_ws_json(websocket, send_lock, {"type": "ai_error", "message": str(exc)})
                continue
            await _present_ai_reply(
                websocket,
                session,
                last_activity,
                ai_state,
                send_lock,
                reply,
                message_type="ai_reply",
            )
        elif message_type == "new_ai_chat":
            if ai_state.active_command:
                await _send_ws_json(
                    websocket,
                    send_lock,
                    {"type": "ai_error", "message": "AI 命令执行期间不能开始新对话"},
                )
                continue
            ai_state.history.clear()
            ai_state.active_goal = None
            ai_state.pending_command = None
            ai_state.loop_active = False
            ai_state.step_count = 0
            ai_state.auto_approve = False
            await _send_ws_json(
                websocket, send_lock,
                {"type": "ai_chat_reset", "auto_approve": False},
            )
        elif message_type == "set_auto_approve":
            ai_state.auto_approve = payload.get("enabled") is True
            await _send_ws_json(
                websocket,
                send_lock,
                {
                    "type": "ai_auto_approve",
                    "enabled": ai_state.auto_approve,
                },
            )
            if (
                ai_state.auto_approve
                and ai_state.pending_command
                and not command_requires_confirmation(ai_state.pending_command)
            ):
                await _execute_ai_command(
                    websocket,
                    session,
                    last_activity,
                    ai_state,
                    send_lock,
                    automatic=True,
                )
        elif message_type == "set_ai_max_steps":
            try:
                requested_steps = int(payload.get("max_steps"))
            except (TypeError, ValueError):
                requested_steps = _TERMINAL_AI_DEFAULT_MAX_STEPS
            ai_state.max_steps = max(
                1, min(_TERMINAL_AI_MAX_ALLOWED_STEPS, requested_steps)
            )
            await _send_ws_json(
                websocket,
                send_lock,
                {
                    "type": "ai_loop_settings",
                    "max_steps": ai_state.max_steps,
                    "max_allowed_steps": _TERMINAL_AI_MAX_ALLOWED_STEPS,
                },
            )
        elif message_type == "set_ai_model":
            if ai_state.active_command:
                await _send_ws_json(
                    websocket,
                    send_lock,
                    {"type": "ai_error", "message": "AI 命令执行期间不能切换模型"},
                )
                continue
            try:
                selected_model = validate_model_name(str(payload.get("model", "")))
            except TerminalAIError as exc:
                await _send_ws_json(
                    websocket, send_lock, {"type": "ai_error", "message": str(exc)}
                )
                continue
            changed = selected_model != ai_state.model
            ai_state.model = selected_model
            if changed:
                ai_state.history.clear()
                ai_state.active_goal = None
                ai_state.pending_command = None
                ai_state.loop_active = False
                ai_state.step_count = 0
                ai_state.auto_approve = False
            await _send_ws_json(
                websocket,
                send_lock,
                {
                    "type": "ai_model_changed",
                    "model": ai_state.model,
                    "conversation_reset": changed,
                    "auto_approve": ai_state.auto_approve,
                },
            )
        elif message_type == "execute_ai":
            await _execute_ai_command(
                websocket, session, last_activity, ai_state, send_lock
            )
        elif message_type == "ping":
            await _send_ws_json(websocket, send_lock, {"type": "pong"})


async def _watch_terminal_idle(
    websocket: WebSocket,
    last_activity: list[float],
    send_lock: asyncio.Lock,
) -> None:
    idle_seconds = settings.terminal_idle_minutes * 60
    while True:
        await asyncio.sleep(min(30, idle_seconds))
        if time.monotonic() - last_activity[0] >= idle_seconds:
            await _send_ws_json(websocket, send_lock,
                {"type": "error", "message": "终端因长时间无操作已断开"}
            )
            return


@router.websocket("/ws/terminal")
async def terminal_websocket(websocket: WebSocket):
    """Bridge xterm.js messages to a login shell running in a PTY."""
    if not settings.terminal_enabled or not AUTH_ENABLED:
        await websocket.close(code=4403, reason="终端功能未启用")
        return
    if not _websocket_origin_is_valid(websocket):
        await websocket.close(code=4403, reason="WebSocket 来源无效")
        return

    user = websocket.session.get("user") if "session" in websocket.scope else None
    username = str((user or {}).get("username", "")).strip()
    if not username:
        await websocket.close(code=4401, reason="未登录")
        return
    current_shell = await run_in_threadpool(ldap_mgr.get_user_login_shell, username)
    if _is_disabled_login_shell(current_shell):
        await websocket.close(code=4403, reason="账户已禁用")
        return

    await websocket.accept()
    session: Optional[TerminalSession] = None
    tasks: list[asyncio.Task] = []
    try:
        # Portal administrators do not receive a privileged working directory:
        # TerminalManager resolves the same user's NSS Home and starts there.
        session = terminal_mgr.open(username)
        log_event(
            logger,
            logging.INFO,
            "browser terminal opened",
            event_type="terminal_session",
            action="open",
            actor=username,
            pid=session.pid,
        )
        send_lock = asyncio.Lock()
        terminal_ai_config = get_terminal_ai_config()
        ai_state = _TerminalAIState(model=terminal_ai_config.model or None)
        await _send_ws_json(websocket, send_lock,
            {
                "type": "ready",
                "username": username,
                "idle_minutes": settings.terminal_idle_minutes,
                "ai": public_terminal_ai_config(),
                "ai_loop": {
                    "max_steps": ai_state.max_steps,
                    "max_allowed_steps": _TERMINAL_AI_MAX_ALLOWED_STEPS,
                },
            }
        )
        last_activity = [time.monotonic()]
        tasks = [
            asyncio.create_task(_send_terminal_output(websocket, session, last_activity, ai_state, send_lock)),
            asyncio.create_task(_receive_terminal_input(websocket, session, last_activity, ai_state, send_lock)),
            asyncio.create_task(_watch_terminal_idle(websocket, last_activity, send_lock)),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled():
                task.result()
    except TerminalError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        pass
    except Exception:
        logger.exception("browser terminal failed", extra={"fields": {"actor": username}})
        try:
            await websocket.send_json({"type": "error", "message": "终端会话异常结束"})
        except Exception:
            pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if session is not None:
            await asyncio.to_thread(terminal_mgr.close, session)
            log_event(
                logger,
                logging.INFO,
                "browser terminal closed",
                event_type="terminal_session",
                action="close",
                actor=username,
                pid=session.pid,
            )
        try:
            await websocket.close(code=1000)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# 文件管理 API
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/files")
async def list_files(
    path: str = "/",
    show_hidden: bool = False,
    cursor: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    root = _file_scope(user)
    try:
        result = await run_in_threadpool(
            file_mgr.list_directory,
            path,
            root,
            show_hidden=show_hidden,
            cursor=cursor,
            limit=limit,
        )
    except FileManagerError as exc:
        _raise_file_error(exc)
    result["scope"] = "root" if user.get("is_admin") else "home"
    result["root_access"] = bool(user.get("is_admin") and os.geteuid() == 0)
    result["upload_max_mb"] = settings.file_upload_max_mb
    result["edit_max_kb"] = settings.file_edit_max_kb
    result["home_path"] = _file_home_path(user)
    return result


@router.get("/api/files/content")
async def get_file_content(path: str, user: dict = Depends(get_current_user)):
    root = _file_scope(user)
    try:
        return await run_in_threadpool(file_mgr.read_text, path, root)
    except FileManagerError as exc:
        _raise_file_error(exc)


@router.put("/api/files/content")
async def update_file_content(
    payload: FileContentUpdate, user: dict = Depends(get_current_user)
):
    root = _file_scope(user)
    try:
        await run_in_threadpool(file_mgr.write_text, payload.path, payload.content, root)
    except FileManagerError as exc:
        _raise_file_error(exc)
    return {"message": "文件保存成功", "path": payload.path}


@router.get("/api/files/download")
async def download_file(path: str, user: dict = Depends(get_current_user)):
    root = _file_scope(user)
    try:
        target = file_mgr.resolve(path, root)
        if not target.is_file():
            raise FileManagerError("目标路径不是文件")
    except FileManagerError as exc:
        _raise_file_error(exc)
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/api/files/directory")
async def create_file_directory(
    payload: FileDirectoryCreate, user: dict = Depends(get_current_user)
):
    root = _file_scope(user)
    try:
        created = await run_in_threadpool(
            file_mgr.create_directory,
            payload.path,
            payload.name,
            root,
            file_mgr.owner_for(user, ldap_mgr),
        )
    except FileManagerError as exc:
        _raise_file_error(exc)
    return {"message": "目录创建成功", "path": created}


@router.post("/api/files/upload")
async def upload_file(
    path: str = "/",
    upload: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    root = _file_scope(user)
    try:
        created = await run_in_threadpool(
            file_mgr.upload,
            path,
            upload.filename or "",
            upload.file,
            root,
            file_mgr.owner_for(user, ldap_mgr),
        )
    except FileManagerError as exc:
        _raise_file_error(exc)
    finally:
        await upload.close()
    return {"message": "文件上传成功", "path": created}


@router.put("/api/files/rename")
async def rename_file(
    payload: FileRenameRequest, user: dict = Depends(get_current_user)
):
    root = _file_scope(user)
    try:
        renamed = await run_in_threadpool(
            file_mgr.rename, payload.path, payload.new_name, root
        )
    except FileManagerError as exc:
        _raise_file_error(exc)
    return {"message": "重命名成功", "path": renamed}


@router.delete("/api/files")
async def delete_file(path: str, user: dict = Depends(get_current_user)):
    root = _file_scope(user)
    try:
        await run_in_threadpool(file_mgr.delete, path, root)
    except FileManagerError as exc:
        _raise_file_error(exc)
    return {"message": "删除成功"}


# ═══════════════════════════════════════════════════════════════════════════════
# 管理员权限 API
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/admin/list")
async def api_get_admin_list(user: dict = Depends(get_current_user)):
    """获取当前管理员列表（仅管理员）。"""
    _require_admin(user)
    admins = admin_mgr.get_admin_list()
    return {"admins": admins, "count": len(admins)}


@router.post("/api/admin")
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


@router.delete("/api/admin/{username}")
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


@router.get("/api/ldap/status")
async def ldap_status(user: dict = Depends(get_current_user)):
    """检查 LDAP 连接状态。"""
    _require_admin(user)
    return ldap_mgr.check_connection()


@router.get("/api/ldap/users")
async def get_users(user: dict = Depends(get_current_user)):
    """获取所有 LDAP 用户列表，并附加 is_admin 字段。"""
    _require_admin(user)
    users = ldap_mgr.list_users()
    admin_list = admin_mgr.get_admin_list()
    tres_limits = slurm_mgr.get_users_tres_limits()
    for u in users:
        username = u.get("username", "")
        u["is_admin"] = username in admin_list
        user_limits = tres_limits.get(username)
        u["has_tres_association"] = user_limits is not None
        for field in (
            "cpu_minutes",
            "gpu_minutes",
            "cpu_used_minutes",
            "gpu_used_minutes",
            "cpu_remaining_minutes",
            "gpu_remaining_minutes",
        ):
            u[field] = user_limits.get(field) if user_limits else None
        quota = quota_mgr.get_user_quota(u.get("username", "")) if quota_mgr else None
        if quota:
            u["storage_used_gb"] = quota.get("used_gb")
            u["storage_quota_gb"] = quota.get("limit_gb") or 0
        else:
            u["storage_used_gb"] = None
            u["storage_quota_gb"] = 0
    return {"users": users, "count": len(users)}


@router.get("/api/ldap/users/{username}")
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


@router.post("/api/ldap/users")
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
        sn=user_data.sn,
        phone=user_data.phone,
        email=user_data.email,
    )
    if not success:
        raise HTTPException(status_code=500, detail="创建用户失败")

    # 同步到 Slurm 账户系统
    slurm_success = slurm_mgr.add_user_account(user_data.username)
    if not slurm_success:
        logger.warning("Slurm 账户添加失败，但 LDAP 用户 %s 已创建", user_data.username)

    # 处理管理员权限
    if user_data.is_admin:
        admin_mgr.add_admin(user_data.username)

    # 处理 NFS 配额（默认不限制）
    if user_data.storage_quota_gb and user_data.storage_quota_gb > 0:
        if not quota_mgr.is_enabled():
            raise HTTPException(status_code=503, detail="NFS quota 未配置或未启用")
        if not quota_mgr.set_user_quota(user_data.username, user_data.storage_quota_gb):
            raise HTTPException(status_code=500, detail="设置磁盘配额失败")

    return {
        "message": f"用户 {user_data.username} 创建成功",
        "is_admin": user_data.is_admin,
    }


@router.delete("/api/ldap/users/{username}")
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


@router.post("/api/ldap/users/{username}/disable")
async def disable_user(username: str, user: dict = Depends(get_current_user)):
    """通过将登录 Shell 改为 nologin 禁用用户。"""
    _require_admin(user)

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
        raise HTTPException(status_code=400, detail="用户名格式无效")

    if not ldap_mgr.get_user(username):
        raise HTTPException(status_code=404, detail="用户不存在")

    success = ldap_mgr.update_user(username=username, shell="/sbin/nologin")
    if not success:
        raise HTTPException(status_code=500, detail="禁用用户失败")

    return {"message": f"用户 {username} 已禁用"}


@router.post("/api/ldap/users/{username}/enable")
async def enable_user(username: str, user: dict = Depends(get_current_user)):
    """通过恢复默认登录 Shell 启用用户。"""
    _require_admin(user)

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
        raise HTTPException(status_code=400, detail="用户名格式无效")

    if not ldap_mgr.get_user(username):
        raise HTTPException(status_code=404, detail="用户不存在")

    success = ldap_mgr.update_user(username=username, shell="/bin/bash")
    if not success:
        raise HTTPException(status_code=500, detail="启用用户失败")

    return {"message": f"用户 {username} 已启用"}


@router.put("/api/ldap/users/{username}")
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
        sn=user_data.sn,
        phone=user_data.phone,
        email=user_data.email,
    )
    if not success:
        raise HTTPException(status_code=500, detail="更新用户失败")

    # 处理管理员权限变更
    if user_data.is_admin is not None:
        if user_data.is_admin:
            admin_mgr.add_admin(username)
        else:
            admin_mgr.remove_admin(username)

    return {
        "message": f"用户 {username} 更新成功",
        "is_admin": admin_mgr.is_admin(username),
    }


@router.put("/api/ldap/users/{username}/quota")
async def update_user_quota(
    username: str,
    quota_data: UserQuotaUpdate,
    user: dict = Depends(get_current_user),
):
    """仅修改用户存储配额，避免为配额操作触发 LDAP 用户更新。"""
    _require_admin(user)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
        raise HTTPException(status_code=400, detail="用户名格式无效")
    if not ldap_mgr.get_user(username):
        raise HTTPException(status_code=404, detail="用户不存在")
    if not quota_mgr.is_enabled():
        raise HTTPException(status_code=503, detail="NFS quota 未配置或未启用")
    if quota_data.storage_quota_gb is not None and quota_data.storage_quota_gb < 0:
        raise HTTPException(status_code=400, detail="配额必须为 0 或正数")
    if not quota_mgr.set_user_quota(username, quota_data.storage_quota_gb):
        raise HTTPException(status_code=500, detail="设置磁盘配额失败，请检查 quota 工具和挂载配置")
    quota = quota_mgr.get_user_quota(username) or {"used_gb": 0, "limit_gb": 0}
    return {"message": f"用户 {username} 的磁盘配额已更新", "quota": quota}


@router.post("/api/ldap/users/{username}/ssh-key")
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


@router.get("/api/ldap/groups")
async def get_groups(user: dict = Depends(get_current_user)):
    """获取所有 LDAP 组列表。"""
    _require_admin(user)
    groups = ldap_mgr.list_groups()
    return {"groups": groups, "count": len(groups)}


@router.get("/api/ldap/groups/{group_name}")
async def get_group(group_name: str, user: dict = Depends(get_current_user)):
    """获取单个组信息。"""
    _require_admin(user)
    group = ldap_mgr.get_group(group_name)
    if not group:
        raise HTTPException(status_code=404, detail="组不存在")
    return group


@router.post("/api/ldap/groups")
async def create_group(group: GroupCreate, user: dict = Depends(get_current_user)):
    """创建新组。"""
    _require_admin(user)
    success = ldap_mgr.create_group(
        group_name=group.name, gid=group.gid, description=group.description
    )
    if not success:
        raise HTTPException(status_code=500, detail="创建组失败")
    return {"message": f"组 {group.name} 创建成功"}


@router.put("/api/ldap/groups/{group_name}")
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


@router.delete("/api/ldap/groups/{group_name}")
async def delete_group(group_name: str, user: dict = Depends(get_current_user)):
    """删除组。"""
    _require_admin(user)
    success = ldap_mgr.delete_group(group_name)
    if not success:
        raise HTTPException(status_code=500, detail="删除组失败")
    return {"message": f"组 {group_name} 已删除"}


@router.post("/api/ldap/groups/add-member")
async def add_group_member(
    data: GroupMemberUpdate, user: dict = Depends(get_current_user)
):
    """将用户添加到组。"""
    _require_admin(user)
    success = ldap_mgr.add_user_to_group(data.username, data.group_name)
    if not success:
        raise HTTPException(status_code=500, detail="添加组成员失败")
    return {"message": f"用户 {data.username} 已加入组 {data.group_name}"}


@router.post("/api/ldap/groups/remove-member")
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


@router.get("/api/slurm/accounts")
async def get_accounts(user: dict = Depends(get_current_user)):
    """获取 Slurm 账户列表。"""
    _require_admin(user)
    accounts = slurm_mgr.list_accounts()
    usage_by_account = slurm_mgr.get_accounts_tres_usage_minutes()
    for account in accounts:
        account_name = account.get("name", "")
        limits = slurm_mgr.get_account_tres_minutes(account_name)
        if limits:
            cpu_minutes = limits.get("cpu")
            gpu_minutes = limits.get("gres/gpu")
            account["cpu_minutes"] = cpu_minutes
            account["gpu_minutes"] = gpu_minutes
            usage = usage_by_account.get(account_name)
            if usage is not None:
                cpu_used_minutes = usage.get("cpu_used_minutes", 0)
                gpu_used_minutes = usage.get("gpu_used_minutes", 0)
                account["cpu_used_minutes"] = cpu_used_minutes
                account["gpu_used_minutes"] = gpu_used_minutes
                account["cpu_remaining_minutes"] = (
                    None
                    if cpu_minutes is None
                    else max(cpu_minutes - cpu_used_minutes, 0)
                )
                account["gpu_remaining_minutes"] = (
                    None
                    if gpu_minutes is None
                    else max(gpu_minutes - gpu_used_minutes, 0)
                )
    return {"accounts": accounts, "count": len(accounts)}


@router.post("/api/slurm/accounts")
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


@router.put("/api/slurm/accounts/{account_name}")
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


@router.get("/api/slurm/accounts/{account_name}/tres-minutes")
async def get_account_tres_minutes(account_name: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    if not slurm_mgr._is_valid_slurm_name(account_name):
        raise HTTPException(status_code=400, detail="账户名格式无效")
    limits = slurm_mgr.get_account_tres_minutes(account_name)
    if limits is None:
        raise HTTPException(status_code=404, detail="账户核时/卡时额度不存在")
    return {"account": account_name, "cpu_minutes": limits.get("cpu"),
            "gpu_minutes": limits.get("gres/gpu")}


@router.put("/api/slurm/accounts/{account_name}/tres-minutes")
async def set_account_tres_minutes(
    account_name: str, payload: AccountTRESMinutesUpdate,
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    if not slurm_mgr._is_valid_slurm_name(account_name):
        raise HTTPException(status_code=400, detail="账户名格式无效")
    if payload.cpu_minutes is None and payload.gpu_minutes is None:
        raise HTTPException(status_code=400, detail="至少需要设置一种分钟额度")
    if payload.comment is not None and (not payload.comment.strip() or len(payload.comment) > 478 or "\n" in payload.comment or "\r" in payload.comment):
        raise HTTPException(status_code=400, detail="Comment 不能为空且不能换行")
    if not slurm_mgr.set_account_tres_minutes(
        account_name, payload.cpu_minutes, payload.gpu_minutes, payload.comment
    ):
        raise HTTPException(status_code=500, detail="设置账户核时/卡时失败")
    return {"message": f"账户 {account_name} 核时/卡时设置成功", "account": account_name,
            "cpu_minutes": payload.cpu_minutes, "gpu_minutes": payload.gpu_minutes}


@router.delete("/api/slurm/accounts/{account_name}")
async def delete_account(account_name: str, user: dict = Depends(get_current_user)):
    """删除 Slurm 账户。"""
    _require_admin(user)
    success = slurm_mgr.delete_account(account_name)
    if not success:
        raise HTTPException(status_code=500, detail="删除账户失败")
    return {"message": f"账户 {account_name} 已删除"}


@router.get("/api/slurm/qos")
async def get_qos(user: dict = Depends(get_current_user)):
    _require_admin(user)
    qos = slurm_mgr.list_qos()
    return {"qos": qos, "count": len(qos)}


@router.post("/api/slurm/qos")
async def create_qos(payload: QosCreate, user: dict = Depends(get_current_user)):
    _require_admin(user)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", payload.name):
        raise HTTPException(status_code=400, detail="QoS 名称格式无效")
    values = payload.dict()
    success = slurm_mgr.create_qos(values.pop("name"), **values)
    if not success:
        raise HTTPException(status_code=500, detail="创建 QoS 失败")
    return {"message": f"QoS {payload.name} 创建成功"}


@router.put("/api/slurm/qos/{qos_name}")
async def update_qos(qos_name: str, payload: QosUpdate, user: dict = Depends(get_current_user)):
    _require_admin(user)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", qos_name):
        raise HTTPException(status_code=400, detail="QoS 名称格式无效")
    success = slurm_mgr.update_qos(qos_name, **payload.dict(exclude_unset=True))
    if not success:
        raise HTTPException(status_code=500, detail="更新 QoS 失败")
    return {"message": f"QoS {qos_name} 更新成功"}


@router.delete("/api/slurm/qos/{qos_name}")
async def delete_qos(qos_name: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", qos_name):
        raise HTTPException(status_code=400, detail="QoS 名称格式无效")
    if not slurm_mgr.delete_qos(qos_name):
        raise HTTPException(status_code=500, detail="删除 QoS 失败")
    return {"message": f"QoS {qos_name} 已删除"}


# ═══════════════════════════════════════════════════════════════════════════════
# Slurm 关联（集群用户）API（仅管理员）
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/slurm/associations")
async def get_associations(
    account: Optional[str] = None, user: dict = Depends(get_current_user)
):
    """获取 Slurm 关联列表。"""
    _require_admin(user)
    associations = slurm_mgr.list_associations(account=account)
    return {"associations": associations, "count": len(associations)}


@router.post("/api/slurm/associations")
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


@router.put("/api/slurm/associations/{account_name}/{username}")
async def update_association(
    account_name: str,
    username: str,
    payload: AssocUpdate,
    user: dict = Depends(get_current_user),
):
    """更新 Slurm 用户关联。"""
    _require_admin(user)
    if not slurm_mgr._is_valid_slurm_name(username):
        raise HTTPException(status_code=400, detail="用户名格式无效")
    if not slurm_mgr._is_valid_slurm_name(account_name):
        raise HTTPException(status_code=400, detail="账户名格式无效")
    if payload.partition and not slurm_mgr._is_valid_slurm_name(
        payload.partition
    ):
        raise HTTPException(status_code=400, detail="分区名格式无效")
    qos_names = [] if not payload.qos else payload.qos.split(",")
    if any(not slurm_mgr._is_valid_slurm_name(name) for name in qos_names):
        raise HTTPException(status_code=400, detail="QoS 名称格式无效")
    if payload.default_qos and not slurm_mgr._is_valid_slurm_name(
        payload.default_qos
    ):
        raise HTTPException(status_code=400, detail="默认 QoS 名称格式无效")
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


@router.delete("/api/slurm/associations/{account_name}/{username}")
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


@router.post("/api/slurm/associations/{account_name}/{username}/tres-minutes")
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
    if payload.comment is not None and (
        not payload.comment.strip()
        or len(payload.comment) > _CREDIT_COMMENT_INPUT_MAX_LENGTH
        or "\n" in payload.comment
        or "\r" in payload.comment
    ):
        raise HTTPException(status_code=400, detail="Comment 必须为 1-478 个字符且不能换行")
    stamped_comment = (
        _timestamp_credit_comment(payload.comment) if payload.comment else None
    )

    success = slurm_mgr.set_association_tres_minutes(
        username=username,
        account=account_name,
        cpu_minutes=payload.cpu_minutes,
        gpu_minutes=payload.gpu_minutes,
        partition=payload.partition,
        comment=stamped_comment,
    )
    if not success:
        raise HTTPException(status_code=500, detail="设置核时/卡时失败")
    return {
        "message": f"关联 {username}/{account_name} 核时/卡时设置成功",
        "cpu_minutes": payload.cpu_minutes,
        "gpu_minutes": payload.gpu_minutes,
        "comment": stamped_comment,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Slurm 分区 API（仅管理员）
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/slurm/partitions")
async def get_partitions(user: dict = Depends(get_current_user)):
    """获取分区列表。"""
    # 分区信息对所有登录用户开放（jobs 页面统计需要）
    partitions = slurm_mgr.list_partitions()
    return {"partitions": partitions, "count": len(partitions)}


@router.get("/api/slurm/partitions/{partition_name}")
async def get_partition(partition_name: str, user: dict = Depends(get_current_user)):
    """获取单个分区信息。"""
    partition = slurm_mgr.get_partition(partition_name)
    if not partition:
        raise HTTPException(status_code=404, detail="分区不存在")
    return partition


@router.post("/api/slurm/partitions")
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


@router.put("/api/slurm/partitions/{partition_name}")
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


@router.delete("/api/slurm/partitions/{partition_name}")
async def delete_partition(partition_name: str, user: dict = Depends(get_current_user)):
    """删除分区（仅管理员）。"""
    _require_admin(user)
    success = slurm_mgr.delete_partition(partition_name)
    if not success:
        raise HTTPException(status_code=500, detail="删除分区失败")
    return {"message": f"分区 {partition_name} 已删除"}


@router.get("/api/slurm/nodes")
async def get_nodes(user: dict = Depends(get_current_user)):
    """获取节点列表（仅管理员）。"""
    _require_admin(user)
    nodes = slurm_mgr.list_nodes()
    return {"nodes": nodes, "count": len(nodes)}


# ═══════════════════════════════════════════════════════════════════════════════
# Slurm 作业 API（所有登录用户可读；取消作业有权限检查）
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/slurm/jobs/stats")
async def get_job_stats(user: dict = Depends(get_current_user)):
    """获取作业统计信息。"""
    stats = slurm_mgr.get_job_stats()
    return stats


@router.get("/api/slurm/my/dashboard")
async def get_my_dashboard(user: dict = Depends(get_current_user)):
    """获取当前用户总览数据，不返回其他用户的作业。"""
    username = user.get("username", "")
    active_jobs = [
        job
        for job in slurm_mgr.list_jobs()
        if job.get("user") == username
    ]
    report = slurm_mgr.get_user_job_report(username, range_key="month")
    limits = slurm_mgr.get_users_tres_limits().get(username, {})
    totals = report.get("totals", {})

    def resource_usage(prefix: str) -> dict:
        used_hours = round(float(totals.get(f"{prefix}_hours", 0) or 0), 2)
        limit_minutes = limits.get(f"{prefix}_minutes")
        available_hours = (
            None if limit_minutes is None else round(limit_minutes / 60, 2)
        )
        remaining_hours = (
            None
            if available_hours is None
            else round(max(available_hours - used_hours, 0), 2)
        )
        return {
            "used_hours": used_hours,
            "remaining_hours": remaining_hours,
            "available_hours": available_hours,
        }

    return {
        "username": username,
        "active_jobs": active_jobs[:20],
        "recent_jobs": report.get("jobs", [])[:20],
        "totals": totals,
        "resources": {
            "cpu": resource_usage("cpu"),
            "gpu": resource_usage("gpu"),
        },
        "partitions": slurm_mgr.list_partitions(),
    }


@router.get("/api/slurm/jobs/completed/recent")
async def get_completed_jobs(limit: int = 20, user: dict = Depends(get_current_user)):
    """获取最近完成的作业。"""
    jobs = slurm_mgr.list_completed_jobs(limit=limit)
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/api/slurm/jobs")
async def get_jobs(
    state: Optional[str] = None,
    partition: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """获取活动作业队列（所有登录用户可查看所有作业）。"""
    jobs = slurm_mgr.list_jobs(state=state, partition=partition)
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/api/slurm/jobs/{job_id}")
async def get_job_detail(job_id: str, user: dict = Depends(get_current_user)):
    """获取作业详细信息。"""
    job = slurm_mgr.get_job_detail(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")
    return job


@router.get("/api/slurm/jobs/{job_id}/monitor")
async def get_job_resource_usage(job_id: str, user: dict = Depends(get_current_user)):
    """获取活动作业的实时 CPU、内存、磁盘和 GPU 占用。"""
    usage = slurm_mgr.get_job_resource_usage(job_id)
    if not usage:
        raise HTTPException(status_code=404, detail="作业不存在")
    return usage


@router.delete("/api/slurm/jobs/{job_id}")
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


@router.get("/api/slurm/jobs/{job_id}/output")
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


@router.get("/api/slurm/users/{username}/report")
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


@router.get("/api/slurm/users/{username}/report.csv")
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


@router.post("/api/slurm/users/{username}/credit")
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
    comment = payload.comment if payload.comment is not None else payload.note
    if comment is not None and (
        not comment.strip()
        or len(comment) > _CREDIT_COMMENT_INPUT_MAX_LENGTH
        or "\n" in comment
        or "\r" in comment
    ):
        raise HTTPException(status_code=400, detail="Comment 必须为 1-478 个字符且不能换行")
    stamped_comment = _timestamp_credit_comment(comment) if comment is not None else None

    grant_kwargs = {
        "username": username,
        "account": payload.account,
        "cpu_hours": cpu_hours,
        "gpu_hours": gpu_hours,
    }
    if payload.partition is not None:
        grant_kwargs["partition"] = payload.partition
    if stamped_comment is not None:
        grant_kwargs["comment"] = stamped_comment
    grant = slurm_mgr.grant_user_tres_hours(
        **grant_kwargs
    )
    if grant is None:
        raise HTTPException(status_code=500, detail="核时/卡时拨付失败")

    logger.info(
        "核时/卡时拨付完成",
        extra={"fields": {
            "username": username,
            "account": grant["account"],
            "cpu_hours": cpu_hours,
            "gpu_hours": gpu_hours,
            "reason": payload.reason,
            "comment": stamped_comment,
            "operator": user.get("username"),
        }},
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
        "comment": stamped_comment,
        "note": stamped_comment,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 节点管理 API（仅管理员）
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/slurm/nodes/config")
async def get_nodes_config(user: dict = Depends(get_current_user)):
    """获取配置文件中的所有节点（仅管理员）。"""
    _require_admin(user)
    nodes = slurm_mgr.list_nodes_from_config()
    return {"nodes": nodes, "count": len(nodes)}


@router.get("/api/slurm/nodes/{node_name}")
async def get_node(node_name: str, user: dict = Depends(get_current_user)):
    """获取节点详细运行时信息（仅管理员）。"""
    _require_admin(user)
    node = slurm_mgr.get_node_detail(node_name)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node


@router.get("/api/slurm/nodes/{node_name}/config")
async def get_node_config(node_name: str, user: dict = Depends(get_current_user)):
    """获取节点配置信息（仅管理员）。"""
    _require_admin(user)
    node = slurm_mgr.get_node_from_config(node_name)
    if not node:
        raise HTTPException(status_code=404, detail="节点配置不存在")
    return node


@router.post("/api/slurm/nodes")
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


@router.put("/api/slurm/nodes/{node_name}/config")
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


@router.delete("/api/slurm/nodes/{node_name}/config")
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


@router.post("/api/slurm/nodes/{node_name}/drain")
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


@router.post("/api/slurm/nodes/{node_name}/resume")
async def resume_node(node_name: str, user: dict = Depends(get_current_user)):
    """恢复节点上线 RESUME（仅管理员）。"""
    _require_admin(user)
    success = slurm_mgr.resume_node(node_name)
    if not success:
        raise HTTPException(status_code=500, detail="恢复节点上线失败")
    return {"message": f"节点 {node_name} 已上线"}


@router.put("/api/slurm/nodes/{node_name}/state")
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


def create_app() -> FastAPI:
    """Construct and configure a FastAPI application instance."""
    application = FastAPI(title=settings.app_title)
    application.add_middleware(AuditMiddleware, snapshot_resolver=_audit_snapshot)
    application.add_middleware(
        SessionMiddleware,
        secret_key=_get_session_secret(),
        same_site="lax",
        https_only=settings.session_https_only,
    )
    application.mount(
        "/static", StaticFiles(directory=str(STATIC_DIR)), name="static"
    )
    application.add_exception_handler(
        HTTPException, custom_http_exception_handler
    )
    application.include_router(router)
    return application


app = create_app()
