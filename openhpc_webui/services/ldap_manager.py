from ldap3 import Server, Connection, ALL, BASE, MODIFY_ADD, MODIFY_DELETE, MODIFY_REPLACE
from ldap3.utils.dn import escape_rdn
import os
import hashlib
import base64
from typing import List, Dict, Optional
from dotenv import load_dotenv
from ..audit import log_current_exception, structured_print as print

# Load environment variables
load_dotenv()


class LDAPManager:
    """LDAP 管理器 - 处理 LDAP 用户和组操作"""

    def __init__(self):
        self.ldap_uri = os.getenv('LDAP_URI', 'ldap://localhost:389')
        self.bind_dn = os.getenv('LDAP_DEFAULT_BIND_DN')
        self.bind_password = os.getenv('LDAP_DEFAULT_AUTHTOK')
        self.base_dn = os.getenv('LDAP_BASE_DN', 'dc=acdiost,dc=com')
        self.port = int(os.getenv('LDAP_PORT', '389'))
        self.use_ssl = os.getenv('LDAP_USE_SSL', 'False').lower() == 'true'

        # Build the server with port and SSL settings
        if self.use_ssl:
            self.server = Server(self.ldap_uri.replace('ldap://', 'ldaps://').replace(':389', ':636'),
                               port=636, use_ssl=True, get_info=ALL)
        else:
            self.server = Server(self.ldap_uri, port=self.port, get_info=ALL)

    @staticmethod
    def hash_password(password: str) -> str:
        """使用SSHA加密密码"""
        salt = os.urandom(4)
        sha = hashlib.sha1(password.encode('utf-8'))
        sha.update(salt)
        digest = sha.digest()
        b64_encoded = base64.b64encode(digest + salt).decode('utf-8')
        return '{SSHA}' + b64_encoded

    def connect(self) -> Optional[Connection]:
        """连接到 LDAP 服务器"""
        try:
            conn = Connection(
                self.server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True
            )
            return conn
        except Exception as e:
            print(f"LDAP 连接失败: {e}")
            return None

    def check_connection(self) -> Dict[str, any]:
        """检查 LDAP 连接状态"""
        try:
            conn = self.connect()
            if conn:
                conn.unbind()
                return {
                    "status": "connected",
                    "server": self.ldap_uri,
                    "bind_dn": self.bind_dn
                }
            return {
                "status": "disconnected",
                "error": "无法连接到 LDAP 服务器"
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

    def list_users(self, search_filter: str = "(objectClass=posixAccount)") -> List[Dict]:
        """列出所有用户"""
        conn = self.connect()
        if not conn:
            return []

        try:
            users = []
            search_base = f"ou=People,{self.base_dn}"

            # Perform LDAP search
            success = conn.search(
                search_base,
                search_filter,
                attributes=[
                    'uid', 'uidNumber', 'gidNumber', 'homeDirectory',
                    'loginShell', 'cn', 'sn', 'mail', 'telephoneNumber',
                    'displayName'
                ]
            )

            if not success:
                print(f"LDAP search failed: {conn.result}")
                return []

            print(f"Found {len(conn.entries)} entries")

            # Save user entries
            user_entries = list(conn.entries)

            for entry in user_entries:
                # Extract attributes safely
                username = str(entry.uid.value) if hasattr(entry, 'uid') and entry.uid else ''
                uid = str(entry.uidNumber.value) if hasattr(entry, 'uidNumber') and entry.uidNumber else ''
                gid = str(entry.gidNumber.value) if hasattr(entry, 'gidNumber') and entry.gidNumber else ''
                home = str(entry.homeDirectory.value) if hasattr(entry, 'homeDirectory') and entry.homeDirectory else ''
                shell = str(entry.loginShell.value) if hasattr(entry, 'loginShell') and entry.loginShell else ''
                cn = str(entry.cn.value) if hasattr(entry, 'cn') and entry.cn else ''
                sn = str(entry.sn.value) if hasattr(entry, 'sn') and entry.sn else ''
                email = str(entry.mail.value) if hasattr(entry, 'mail') and entry.mail else ''
                phone = (
                    str(entry.telephoneNumber.value)
                    if hasattr(entry, 'telephoneNumber') and entry.telephoneNumber
                    else ''
                )

                # Find primary group name
                groups = []
                if gid:
                    group_search = conn.search(
                        f"ou=Groups,{self.base_dn}",
                        f"(gidNumber={gid})",
                        attributes=['cn']
                    )
                    if group_search and conn.entries:
                        for group_entry in conn.entries:
                            if hasattr(group_entry, 'cn') and group_entry.cn:
                                groups.append(str(group_entry.cn.value))

                user = {
                    'dn': entry.entry_dn,
                    'username': username,
                    'uid': uid,
                    'gid': gid,
                    'home': home,
                    'shell': shell,
                    'cn': cn,
                    'sn': sn,
                    'phone': phone,
                    'email': email,
                    'groups': groups
                }
                users.append(user)
                print(f"Added user: {username} with groups: {groups}")

            return users
        except Exception as e:
            print(f"查询用户失败: {e}")
            log_current_exception("查询用户失败的调用栈")
            return []
        finally:
            conn.unbind()

    def get_user(self, username: str) -> Optional[Dict]:
        """获取单个用户信息"""
        users = self.list_users(f"(uid={username})")
        return users[0] if users else None

    def get_user_login_shell(self, username: str) -> Optional[str]:
        """轻量读取用户 loginShell；查询失败或用户不存在时返回 None。"""
        conn = self.connect()
        if not conn:
            return None

        try:
            user_dn = f"uid={escape_rdn(username)},ou=People,{self.base_dn}"
            success = conn.search(
                search_base=user_dn,
                search_filter="(objectClass=posixAccount)",
                search_scope=BASE,
                attributes=["loginShell"],
            )
            if not success or not conn.entries:
                return None
            entry = conn.entries[0]
            if not hasattr(entry, "loginShell") or not entry.loginShell:
                return ""
            return str(entry.loginShell.value or "")
        except Exception as e:
            print(f"查询用户登录 Shell 失败: {e}")
            return None
        finally:
            conn.unbind()

    def create_user(
        self,
        username: str,
        uid: int,
        gid: int,
        home: str,
        shell: str = "/bin/bash",
        password: str = None,
        sn: str = None,
        phone: str = None,
        email: str = None,
    ) -> bool:
        """创建新用户"""
        conn = self.connect()
        if not conn:
            return False

        try:
            dn = f"uid={username},ou=People,{self.base_dn}"
            surname = sn.strip() if sn and sn.strip() else username
            attrs = {
                'objectClass': ['top', 'posixAccount', 'inetOrgPerson'],
                'uid': username,
                'cn': username,
                'sn': surname,
                'uidNumber': str(uid),
                'gidNumber': str(gid),
                'homeDirectory': home,
                'loginShell': shell
            }

            if password:
                attrs['userPassword'] = self.hash_password(password)
            if phone and phone.strip():
                attrs['telephoneNumber'] = phone.strip()
            if email and email.strip():
                attrs['mail'] = email.strip()

            conn.add(dn, attributes=attrs)
            return conn.result['result'] == 0
        except Exception as e:
            print(f"创建用户失败: {e}")
            return False
        finally:
            conn.unbind()

    def delete_user(self, username: str) -> bool:
        """删除用户"""
        conn = self.connect()
        if not conn:
            return False

        try:
            dn = f"uid={username},ou=People,{self.base_dn}"
            conn.delete(dn)
            return conn.result['result'] == 0
        except Exception as e:
            print(f"删除用户失败: {e}")
            return False
        finally:
            conn.unbind()

    def update_user(
        self,
        username: str,
        gid: int = None,
        home: str = None,
        shell: str = None,
        password: str = None,
        sn: str = None,
        phone: str = None,
        email: str = None,
    ) -> bool:
        """更新用户信息"""
        conn = self.connect()
        if not conn:
            return False

        try:
            dn = f"uid={username},ou=People,{self.base_dn}"
            changes = {}

            if gid is not None:
                changes['gidNumber'] = [(MODIFY_REPLACE, [str(gid)])]
            if home is not None:
                changes['homeDirectory'] = [(MODIFY_REPLACE, [home])]
            if shell is not None:
                changes['loginShell'] = [(MODIFY_REPLACE, [shell])]
            if sn is not None:
                surname = sn.strip() or username
                changes['sn'] = [(MODIFY_REPLACE, [surname])]
            if password is not None:
                changes['userPassword'] = [(MODIFY_REPLACE, [self.hash_password(password)])]
            if phone is not None:
                phone = phone.strip()
                changes['telephoneNumber'] = [
                    (MODIFY_REPLACE, [phone]) if phone else (MODIFY_REPLACE, [])
                ]
            if email is not None:
                email = email.strip()
                changes['mail'] = [
                    (MODIFY_REPLACE, [email]) if email else (MODIFY_REPLACE, [])
                ]

            if not changes:
                return True  # No changes to make

            conn.modify(dn, changes)
            return conn.result['result'] == 0
        except Exception as e:
            print(f"更新用户失败: {e}")
            return False
        finally:
            conn.unbind()

    def list_groups(self, search_filter: str = "(objectClass=posixGroup)") -> List[Dict]:
        """列出所有组"""
        conn = self.connect()
        if not conn:
            return []

        try:
            groups = []
            search_base = f"ou=Groups,{self.base_dn}"

            # Perform LDAP search
            success = conn.search(
                search_base,
                search_filter,
                attributes=['cn', 'gidNumber', 'memberUid', 'description']
            )

            if not success:
                print(f"LDAP group search failed: {conn.result}")
                return []

            print(f"Found {len(conn.entries)} group entries")

            # Save group entries before searching for users
            group_entries = list(conn.entries)

            for entry in group_entries:
                # Extract attributes safely
                name = str(entry.cn.value) if hasattr(entry, 'cn') and entry.cn else ''
                gid = str(entry.gidNumber.value) if hasattr(entry, 'gidNumber') and entry.gidNumber else ''
                members = [str(m) for m in entry.memberUid.values] if hasattr(entry, 'memberUid') and entry.memberUid else []
                description = str(entry.description.value) if hasattr(entry, 'description') and entry.description else ''

                # Find users with this gid as their primary group
                if gid:
                    user_search = conn.search(
                        f"ou=People,{self.base_dn}",
                        f"(gidNumber={gid})",
                        attributes=['uid']
                    )
                    if user_search:
                        for user_entry in conn.entries:
                            if hasattr(user_entry, 'uid') and user_entry.uid:
                                username = str(user_entry.uid.value)
                                if username not in members:
                                    members.append(username)

                group = {
                    'dn': entry.entry_dn,
                    'name': name,
                    'gid': gid,
                    'members': members,
                    'description': description
                }
                groups.append(group)
                print(f"Added group: {name} with {len(members)} members")

            return groups
        except Exception as e:
            print(f"查询组失败: {e}")
            log_current_exception("查询组失败的调用栈")
            return []
        finally:
            conn.unbind()

    def get_group(self, group_name: str) -> Optional[Dict]:
        """获取单个组信息"""
        groups = self.list_groups(f"(cn={group_name})")
        return groups[0] if groups else None

    def create_group(self, group_name: str, gid: int, description: str = "") -> bool:
        """创建新组"""
        conn = self.connect()
        if not conn:
            return False

        try:
            dn = f"cn={group_name},ou=Groups,{self.base_dn}"
            attrs = {
                'objectClass': ['top', 'posixGroup'],
                'cn': group_name,
                'gidNumber': str(gid)
            }
            if description:
                attrs['description'] = description

            conn.add(dn, attributes=attrs)
            return conn.result['result'] == 0
        except Exception as e:
            print(f"创建组失败: {e}")
            return False
        finally:
            conn.unbind()

    def update_group(self, group_name: str, gid: int = None, description: str = None) -> bool:
        """更新组信息"""
        conn = self.connect()
        if not conn:
            return False

        try:
            dn = f"cn={group_name},ou=Groups,{self.base_dn}"
            changes = {}

            if gid is not None:
                changes['gidNumber'] = [(MODIFY_REPLACE, [str(gid)])]
            if description is not None:
                changes['description'] = [(MODIFY_REPLACE, [description])]

            if not changes:
                return True  # No changes to make

            conn.modify(dn, changes)
            return conn.result['result'] == 0
        except Exception as e:
            print(f"更新组失败: {e}")
            return False
        finally:
            conn.unbind()

    def delete_group(self, group_name: str) -> bool:
        """删除组"""
        conn = self.connect()
        if not conn:
            return False

        try:
            dn = f"cn={group_name},ou=Groups,{self.base_dn}"
            conn.delete(dn)
            return conn.result['result'] == 0
        except Exception as e:
            print(f"删除组失败: {e}")
            return False
        finally:
            conn.unbind()

    def add_user_to_group(self, username: str, group_name: str) -> bool:
        """将用户添加到组"""
        conn = self.connect()
        if not conn:
            return False

        try:
            dn = f"cn={group_name},ou=Groups,{self.base_dn}"
            conn.modify(dn, {'memberUid': [(MODIFY_ADD, [username])]})
            return conn.result['result'] == 0
        except Exception as e:
            print(f"添加用户到组失败: {e}")
            return False
        finally:
            conn.unbind()

    def remove_user_from_group(self, username: str, group_name: str) -> bool:
        """从组中移除用户"""
        conn = self.connect()
        if not conn:
            return False

        try:
            dn = f"cn={group_name},ou=Groups,{self.base_dn}"
            conn.modify(dn, {'memberUid': [(MODIFY_DELETE, [username])]})
            return conn.result['result'] == 0
        except Exception as e:
            print(f"从组中移除用户失败: {e}")
            return False
        finally:
            conn.unbind()

    def set_ssh_public_key(self, username: str, public_key: str) -> bool:
        """写入/重置用户的 SSH 公钥（ldapPublicKey + sshPublicKey）。"""
        conn = self.connect()
        if not conn:
            return False

        try:
            dn = f"uid={username},ou=People,{self.base_dn}"
            search_ok = conn.search(
                dn,
                "(objectClass=*)",
                search_scope=BASE,
                attributes=["objectClass"],
            )
            if not search_ok or not conn.entries:
                print(f"未找到用户 {username} 的 LDAP 条目")
                return False

            entry = conn.entries[0]
            existing_classes = []
            if hasattr(entry, "objectClass") and entry.objectClass:
                existing_classes = [str(c) for c in entry.objectClass.values]
            existing_lower = {c.lower() for c in existing_classes}

            changes = {
                "sshPublicKey": [(MODIFY_REPLACE, [public_key])],
            }
            if "ldappublickey" not in existing_lower:
                changes["objectClass"] = [(MODIFY_ADD, ["ldapPublicKey"])]

            conn.modify(dn, changes)
            return conn.result["result"] == 0
        except Exception as e:
            print(f"写入 SSH 公钥失败: {e}")
            return False
        finally:
            conn.unbind()
