"""LDAP and local-system authentication backends."""
import ctypes
import ctypes.util
import os
import pwd
from typing import Optional, Dict
from ldap3 import Server, Connection, ALL, SIMPLE
from ldap3.core.exceptions import LDAPInvalidCredentialsResult
from ..audit import structured_print as print
from ..config import identity_backend
from .integration_timeout import bounded_timeout_seconds



class AuthenticationServiceError(RuntimeError):
    """LDAP authentication could not complete due to an infrastructure error."""


class AuthManager:
    """Authenticate users against LDAP or the host PAM stack."""

    def __init__(self):
        self.backend = identity_backend()
        self.ldap_uri = os.getenv('LDAP_URI', 'ldap://localhost:389')
        self.base_dn = os.getenv('LDAP_BASE_DN', 'dc=acdiost,dc=com')
        self.use_ssl = os.getenv('LDAP_USE_SSL', 'False').lower() == 'true'
        self.connect_timeout = bounded_timeout_seconds(
            "LDAP_CONNECT_TIMEOUT_SECONDS", 5
        )
        self.receive_timeout = bounded_timeout_seconds(
            "LDAP_RECEIVE_TIMEOUT_SECONDS", 10
        )

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, str]]:
        """
        验证用户凭证

        Args:
            username: 用户名
            password: 密码

        Returns:
            如果认证成功，返回用户信息字典；否则返回None
        """
        if not username or not password:
            return None

        if self.backend == "system":
            return self._authenticate_system_user(username, password)

        try:
            # 构建用户DN
            user_dn = f"uid={username},ou=People,{self.base_dn}"

            # 创建LDAP服务器对象
            server = Server(
                self.ldap_uri,
                get_info=ALL,
                use_ssl=self.use_ssl,
                connect_timeout=self.connect_timeout,
            )

            # 尝试使用用户凭证绑定
            conn = Connection(
                server,
                user=user_dn,
                password=password,
                authentication=SIMPLE,
                auto_bind=True,
                raise_exceptions=True,
                receive_timeout=self.receive_timeout,
            )

            # 如果绑定成功，获取用户信息
            if conn.bound:
                # 搜索用户详细信息
                conn.search(
                    search_base=user_dn,
                    search_filter='(objectClass=*)',
                    attributes=[
                        'uid', 'cn', 'sn', 'mail', 'uidNumber', 'gidNumber', 'loginShell'
                    ]
                )

                if conn.entries:
                    entry = conn.entries[0]
                    user_info = {
                        'username': str(entry.uid) if hasattr(entry, 'uid') else username,
                        'cn': str(entry.sn)
                        if hasattr(entry, 'sn') and entry.sn
                        else str(entry.cn) if hasattr(entry, 'cn') else username,
                        'mail': str(entry.mail) if hasattr(entry, 'mail') else '',
                        'uid_number': str(entry.uidNumber) if hasattr(entry, 'uidNumber') else '',
                        'gid_number': str(entry.gidNumber) if hasattr(entry, 'gidNumber') else '',
                        'shell': str(entry.loginShell.value)
                        if hasattr(entry, 'loginShell') and entry.loginShell
                        else ''
                    }
                    conn.unbind()
                    return user_info

                conn.unbind()
                return {'username': username, 'cn': username}

            return None

        except LDAPInvalidCredentialsResult:
            return None
        except Exception as e:
            print(f"认证失败: {str(e)}")
            raise AuthenticationServiceError("LDAP authentication unavailable") from e

    def _authenticate_system_user(
        self, username: str, password: str
    ) -> Optional[Dict[str, str]]:
        """Authenticate a local account through PAM without exposing the password."""
        try:
            record = pwd.getpwnam(username)
        except KeyError:
            return None
        try:
            authenticated = _authenticate_with_pam(username, password)
        except Exception as exc:
            print(f"系统认证失败: {exc}")
            raise AuthenticationServiceError("system authentication unavailable") from exc
        if not authenticated:
            return None
        display_name = (record.pw_gecos or "").split(",", 1)[0].strip()
        return {
            "username": record.pw_name,
            "cn": display_name or record.pw_name,
            "mail": "",
            "uid_number": str(record.pw_uid),
            "gid_number": str(record.pw_gid),
            "shell": record.pw_shell,
        }

    def verify_user_exists(self, username: str) -> bool:
        """
        验证用户是否存在于LDAP中

        Args:
            username: 用户名

        Returns:
            用户存在返回True，否则返回False
        """
        if self.backend == "system":
            try:
                pwd.getpwnam(username)
                return True
            except KeyError:
                return False
        try:
            # 使用管理员凭证连接
            admin_dn = os.getenv('LDAP_DEFAULT_BIND_DN')
            admin_password = os.getenv('LDAP_DEFAULT_AUTHTOK')

            server = Server(
                self.ldap_uri,
                get_info=ALL,
                use_ssl=self.use_ssl,
                connect_timeout=self.connect_timeout,
            )
            conn = Connection(
                server,
                user=admin_dn,
                password=admin_password,
                auto_bind=True,
                receive_timeout=self.receive_timeout,
            )

            # 搜索用户
            user_dn = f"uid={username},ou=People,{self.base_dn}"
            conn.search(
                search_base=user_dn,
                search_filter='(objectClass=posixAccount)',
                attributes=['uid']
            )

            exists = len(conn.entries) > 0
            conn.unbind()
            return exists

        except Exception:
            return False


class _PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _PamResponse(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_char_p), ("resp_retcode", ctypes.c_int)]


_PamConversationFunction = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(ctypes.POINTER(_PamMessage)),
    ctypes.POINTER(ctypes.POINTER(_PamResponse)),
    ctypes.c_void_p,
)


class _PamConversation(ctypes.Structure):
    _fields_ = [("conv", _PamConversationFunction), ("appdata_ptr", ctypes.c_void_p)]


def _authenticate_with_pam(username: str, password: str) -> bool:
    """Run the host's ``login`` PAM policy for a username/password pair."""
    library_name = ctypes.util.find_library("pam")
    if not library_name:
        raise RuntimeError("PAM library not found")
    pam = ctypes.CDLL(library_name)
    libc = ctypes.CDLL(ctypes.util.find_library("c") or None)
    libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
    libc.calloc.restype = ctypes.c_void_p
    libc.strdup.argtypes = [ctypes.c_char_p]
    libc.strdup.restype = ctypes.c_void_p

    username_bytes = username.encode("utf-8")
    password_bytes = password.encode("utf-8")

    @_PamConversationFunction
    def conversation(message_count, messages, responses, app_data):
        del app_data
        allocated = libc.calloc(message_count, ctypes.sizeof(_PamResponse))
        if not allocated:
            return 5  # PAM_BUF_ERR
        reply = ctypes.cast(allocated, ctypes.POINTER(_PamResponse))
        for index in range(message_count):
            style = messages[index].contents.msg_style
            if style == 1:  # PAM_PROMPT_ECHO_OFF
                value = password_bytes
            elif style == 2:  # PAM_PROMPT_ECHO_ON
                value = username_bytes
            else:
                value = b""
            duplicated = libc.strdup(value)
            if not duplicated:
                return 5
            reply[index].resp = ctypes.cast(duplicated, ctypes.c_char_p)
            reply[index].resp_retcode = 0
        responses[0] = reply
        return 0

    pam.pam_start.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_PamConversation),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    pam.pam_start.restype = ctypes.c_int
    pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
    pam.pam_authenticate.restype = ctypes.c_int
    pam.pam_acct_mgmt.argtypes = [ctypes.c_void_p, ctypes.c_int]
    pam.pam_acct_mgmt.restype = ctypes.c_int
    pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
    pam.pam_end.restype = ctypes.c_int

    handle = ctypes.c_void_p()
    config = _PamConversation(conversation, None)
    result = pam.pam_start(b"login", username_bytes, ctypes.byref(config), ctypes.byref(handle))
    if result == 0:
        result = pam.pam_authenticate(handle, 0)
    if result == 0:
        result = pam.pam_acct_mgmt(handle, 0)
    if handle:
        pam.pam_end(handle, result)
    if result in {4, 5, 19}:  # system, allocation, or conversation errors
        raise RuntimeError(f"PAM infrastructure error: {result}")
    return result == 0
