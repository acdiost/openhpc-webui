"""
LDAP认证管理模块
提供用户身份验证功能
"""
import os
from typing import Optional, Dict
from ldap3 import Server, Connection, ALL, SIMPLE
from ldap3.core.exceptions import LDAPInvalidCredentialsResult
from dotenv import load_dotenv
from ..audit import structured_print as print

load_dotenv()


class AuthenticationServiceError(RuntimeError):
    """LDAP authentication could not complete due to an infrastructure error."""


class AuthManager:
    """LDAP认证管理器"""

    def __init__(self):
        self.ldap_uri = os.getenv('LDAP_URI', 'ldap://localhost:389')
        self.base_dn = os.getenv('LDAP_BASE_DN', 'dc=acdiost,dc=com')
        self.use_ssl = os.getenv('LDAP_USE_SSL', 'False').lower() == 'true'

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

        try:
            # 构建用户DN
            user_dn = f"uid={username},ou=People,{self.base_dn}"

            # 创建LDAP服务器对象
            server = Server(self.ldap_uri, get_info=ALL, use_ssl=self.use_ssl)

            # 尝试使用用户凭证绑定
            conn = Connection(
                server,
                user=user_dn,
                password=password,
                authentication=SIMPLE,
                auto_bind=True,
                raise_exceptions=True,
            )

            # 如果绑定成功，获取用户信息
            if conn.bound:
                # 搜索用户详细信息
                conn.search(
                    search_base=user_dn,
                    search_filter='(objectClass=*)',
                    attributes=[
                        'uid', 'cn', 'mail', 'uidNumber', 'gidNumber', 'loginShell'
                    ]
                )

                if conn.entries:
                    entry = conn.entries[0]
                    user_info = {
                        'username': str(entry.uid) if hasattr(entry, 'uid') else username,
                        'cn': str(entry.cn) if hasattr(entry, 'cn') else username,
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

    def verify_user_exists(self, username: str) -> bool:
        """
        验证用户是否存在于LDAP中

        Args:
            username: 用户名

        Returns:
            用户存在返回True，否则返回False
        """
        try:
            # 使用管理员凭证连接
            admin_dn = os.getenv('LDAP_DEFAULT_BIND_DN')
            admin_password = os.getenv('LDAP_DEFAULT_AUTHTOK')

            server = Server(self.ldap_uri, get_info=ALL, use_ssl=self.use_ssl)
            conn = Connection(
                server,
                user=admin_dn,
                password=admin_password,
                auto_bind=True
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
