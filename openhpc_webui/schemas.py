"""Request models used by the HTTP API."""

from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    sn: Optional[str] = Field(None, max_length=128)
    uid: int
    gid: int
    home: str
    shell: str = "/bin/bash"
    password: Optional[str] = None
    is_admin: bool = False
    storage_quota_gb: Optional[float] = None


class UserUpdate(BaseModel):
    gid: Optional[int] = None
    home: Optional[str] = None
    shell: Optional[str] = None
    password: Optional[str] = None
    sn: Optional[str] = Field(None, max_length=128)
    is_admin: Optional[bool] = None


class UserQuotaUpdate(BaseModel):
    """独立修改用户存储配额；0 或 null 表示不限制。"""

    storage_quota_gb: Optional[float] = 0


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


class AccountTRESMinutesUpdate(BaseModel):
    """设置账户级 CPU/GPU TRES 分钟上限。"""

    cpu_minutes: Optional[int] = Field(None, ge=0)
    gpu_minutes: Optional[int] = Field(None, ge=0)
    comment: Optional[str] = Field(None, max_length=478)


class QosCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    priority: Optional[int] = Field(0, ge=0)
    max_wall: Optional[str] = None
    max_jobs_pu: Optional[int] = Field(None, ge=0)
    max_submit_jobs_pu: Optional[int] = Field(None, ge=0)
    max_tres_pu: Optional[str] = None
    max_jobs_pa: Optional[int] = Field(None, ge=0)
    max_submit_jobs_pa: Optional[int] = Field(None, ge=0)
    max_tres_pa: Optional[str] = None


class QosUpdate(BaseModel):
    description: Optional[str] = None
    priority: Optional[int] = Field(None, ge=0)
    max_wall: Optional[str] = None
    max_jobs_pu: Optional[int] = Field(None, ge=0)
    max_submit_jobs_pu: Optional[int] = Field(None, ge=0)
    max_tres_pu: Optional[str] = None
    max_jobs_pa: Optional[int] = Field(None, ge=0)
    max_submit_jobs_pa: Optional[int] = Field(None, ge=0)
    max_tres_pa: Optional[str] = None


class AssocCreate(BaseModel):
    username: str
    account: str
    partition: Optional[str] = None
    qos: Optional[str] = None
    default_qos: Optional[str] = None


class AssocUpdate(BaseModel):
    partition: str = Field(..., max_length=64)
    qos: Optional[str] = Field(None, max_length=1024)
    default_qos: Optional[str] = Field(None, max_length=64)


class AssocTRESMinutesUpdate(BaseModel):
    cpu_minutes: Optional[int] = None
    gpu_minutes: Optional[int] = None
    partition: Optional[str] = None
    comment: Optional[str] = None


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
    state: str
    reason: Optional[str] = None


class AdminUserRequest(BaseModel):
    username: str


class UserCreditRequest(BaseModel):
    account: Optional[str] = None
    partition: Optional[str] = None
    cpu_hours: Optional[float] = None
    gpu_hours: Optional[float] = None
    hours: Optional[float] = None
    reason: Optional[str] = None
    comment: Optional[str] = None
    # Deprecated compatibility alias. New clients should send comment.
    note: Optional[str] = None
    effective_date: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class FileDirectoryCreate(BaseModel):
    path: str = Field("/", max_length=4096)
    name: str = Field(..., min_length=1, max_length=255)


class FileRenameRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)
    new_name: str = Field(..., min_length=1, max_length=255)


class FileContentUpdate(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)
    content: str
