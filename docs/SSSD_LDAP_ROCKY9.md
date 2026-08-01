# Rocky Linux 9 使用 SSSD 接入 LDAP

本文说明如何让 Rocky Linux 9 通过 SSSD 查询 LDAP 用户和组，并在首次登录时自动
创建家目录。LDAP 目录结构沿用本项目的约定：

```text
dc=acdiost,dc=com
├── ou=People
├── ou=Groups
└── ou=Services
```

本文步骤已在以下环境实测：

- Rocky Linux 9.7（aarch64）
- SSSD `2.9.8-4.el9_8.1`
- OpenLDAP `2.6.8`
- LDAP 与 SSSD 位于同一台测试服务器
- 测试用户 `dawn`，UID/GID 为 `10001`

LDAP 服务端的安装和基础目录初始化参见
[Rocky Linux 9 LDAP 安装与初始化](./LDAP_ROCKY9.md)。

## 1. 配置原则

SSSD 需要先搜索用户 DN，再使用用户密码完成认证。本文创建独立的
`cn=sssd,ou=Services,dc=acdiost,dc=com` 查询账号，不把 LDAP 目录管理员密码写入
`/etc/sssd/sssd.conf`。

LDAP 数据库需要允许已认证账号读取目录。本项目 LDAP 文档配置的 ACL 已满足要求：

```ldif
olcAccess: {0}to attrs=userPassword by self write by anonymous auth by * none
olcAccess: {1}to * by self read by users read by * none
```

第一条规则禁止读取其他用户的密码，第二条规则允许 SSSD 查询账号读取用户和组。

## 2. 检查 UID 和 GID

创建用户前，同时检查本地系统和 LDAP，避免 UID/GID 冲突：

```bash
getent passwd dawn
getent group dawn

ldapsearch -x -LLL -H ldap://127.0.0.1:389 \
  -D 'cn=admin,dc=acdiost,dc=com' -W \
  -b 'dc=acdiost,dc=com' \
  '(|(uid=dawn)(cn=dawn))' dn uid uidNumber gidNumber
```

本文实测时 `10000` 已分配给 `admin`，因此为 `dawn` 使用 `10001`。

## 3. 创建 SSSD 查询账号

生成随机的 SSSD 查询密码及 SSHA 哈希：

```bash
SSSD_BIND_PASSWORD=$(openssl rand -hex 24)
SSSD_BIND_HASH=$(slappasswd -s "$SSSD_BIND_PASSWORD")
```

妥善保存 `SSSD_BIND_PASSWORD`，稍后需要写入 `sssd.conf`。不要把它提交到 Git。

创建 `/root/sssd-bind.ldif`，将 `<SSSD bind SSHA hash>` 替换为
`SSSD_BIND_HASH` 的值：

```ldif
dn: ou=Services,dc=acdiost,dc=com
objectClass: top
objectClass: organizationalUnit
ou: Services

dn: cn=sssd,ou=Services,dc=acdiost,dc=com
objectClass: top
objectClass: organizationalRole
objectClass: simpleSecurityObject
cn: sssd
description: Read-only bind identity for SSSD
userPassword: <SSSD bind SSHA hash>
```

使用目录管理账号导入：

```bash
ldapadd -x -H ldap://127.0.0.1:389 \
  -D 'cn=admin,dc=acdiost,dc=com' -W \
  -f /root/sssd-bind.ldif
rm -f /root/sssd-bind.ldif
```

验证查询账号能够绑定和读取目录：

```bash
ldapsearch -x -LLL -H ldap://127.0.0.1:389 \
  -D 'cn=sssd,ou=Services,dc=acdiost,dc=com' -W \
  -b 'ou=People,dc=acdiost,dc=com' \
  '(objectClass=posixAccount)' uid uidNumber gidNumber
```

## 4. 创建 dawn 用户和组

使用 `slappasswd` 为 `dawn` 生成初始密码哈希：

```bash
slappasswd
```

创建 `/root/dawn.ldif`，将 `<dawn SSHA password hash>` 替换为命令输出：

```ldif
dn: cn=dawn,ou=Groups,dc=acdiost,dc=com
objectClass: top
objectClass: posixGroup
cn: dawn
gidNumber: 10001
memberUid: dawn
description: Primary group for dawn

dn: uid=dawn,ou=People,dc=acdiost,dc=com
objectClass: top
objectClass: inetOrgPerson
objectClass: posixAccount
uid: dawn
cn: Dawn
sn: Dawn
uidNumber: 10001
gidNumber: 10001
homeDirectory: /home/dawn
loginShell: /bin/bash
userPassword: <dawn SSHA password hash>
```

导入并删除临时文件：

```bash
ldapadd -x -H ldap://127.0.0.1:389 \
  -D 'cn=admin,dc=acdiost,dc=com' -W \
  -f /root/dawn.ldif
rm -f /root/dawn.ldif
```

## 5. 安装 SSSD

```bash
sudo dnf install -y \
  sssd sssd-ldap sssd-tools oddjob-mkhomedir
```

各软件包用途：

- `sssd`：NSS/PAM 身份缓存和认证服务
- `sssd-ldap`：LDAP 身份与认证后端
- `sssd-tools`：`sssctl`、缓存检查和诊断工具
- `oddjob-mkhomedir`：用户首次登录时创建家目录

## 6. 配置 SSSD

创建 `/etc/sssd/sssd.conf`，将 `<SSSD bind plaintext password>` 替换为第 3 步保存
的随机密码：

```ini
[sssd]
config_file_version = 2
services = nss, pam
domains = LDAP

[nss]
homedir_substring = /home

[pam]

[domain/LDAP]
id_provider = ldap
auth_provider = ldap
chpass_provider = ldap
access_provider = permit

ldap_uri = ldap://127.0.0.1:389
ldap_search_base = dc=acdiost,dc=com
ldap_user_search_base = ou=People,dc=acdiost,dc=com
ldap_group_search_base = ou=Groups,dc=acdiost,dc=com
ldap_schema = rfc2307
ldap_group_member = memberUid

ldap_default_bind_dn = cn=sssd,ou=Services,dc=acdiost,dc=com
ldap_default_authtok_type = password
ldap_default_authtok = <SSSD bind plaintext password>

ldap_id_use_start_tls = false
cache_credentials = true
enumerate = false
use_fully_qualified_names = false
fallback_homedir = /home/%u
default_shell = /bin/bash
ldap_network_timeout = 3
ldap_opt_timeout = 3
```

设置严格权限并检查配置：

```bash
sudo chown root:root /etc/sssd/sssd.conf
sudo chmod 600 /etc/sssd/sssd.conf
sudo restorecon -v /etc/sssd/sssd.conf
sudo sssctl config-check
```

实测 `sssctl config-check` 返回：

```text
Issues identified by validators: 0
Messages generated during configuration merging: 0
```

## 7. 配置 NSS、PAM 和自动家目录

Rocky Linux 9 使用 authselect 管理 NSS/PAM，不要手工修改 authselect 生成的 PAM
文件：

```bash
sudo authselect select sssd with-mkhomedir --force
sudo systemctl enable --now oddjobd
sudo systemctl enable --now sssd
sudo sss_cache -E
sudo systemctl restart sssd
```

检查状态：

```bash
authselect current
systemctl is-enabled sssd oddjobd
systemctl is-active sssd oddjobd slapd
```

实测结果为 `sssd with-mkhomedir`，三个服务均为 `active`。

## 8. 验证 dawn 查询

通过 NSS 查询 LDAP 用户：

```bash
getent passwd dawn
id dawn
getent group dawn
sssctl user-show dawn
```

本次服务器实测输出：

```text
dawn:*:10001:10001:Dawn:/home/dawn:/bin/bash
uid=10001(dawn) gid=10001(dawn) groups=10001(dawn)
dawn:*:10001:dawn
```

这说明查询路径已经完整生效：

```text
getent/id -> NSS -> SSSD -> LDAP -> SSSD cache -> NSS
```

首次使用 `su - dawn` 或 SSH 登录后，`oddjob-mkhomedir` 会创建 `/home/dawn`。
执行登录测试前，应确认 `/home` 是本机目录还是已经挂载的共享存储，避免在错误的
文件系统上创建家目录。

## 9. 计算节点跨主机配置

本文实测环境中 SSSD 和 slapd 位于同一台服务器，因此使用
`ldap://127.0.0.1:389`，密码不会离开本机。计算节点连接独立 LDAP 服务器时，不能
照搬明文配置，应部署 CA 和服务器证书，并至少使用 StartTLS：

```ini
ldap_uri = ldap://ldap.acdiost.com:389
ldap_id_use_start_tls = true
ldap_tls_cacert = /etc/openldap/certs/acdiost-ca.crt
ldap_tls_reqcert = demand
```

也可以使用 `ldaps://ldap.acdiost.com:636`。证书中的主机名必须与 `ldap_uri` 一致，
且 LDAP 防火墙只允许管理节点和计算节点网段访问。

## 10. 故障排查

### `id dawn` 提示用户不存在

```bash
sudo sssctl config-check
sudo sssctl domain-status LDAP
sudo sss_cache -E
sudo systemctl restart sssd
sudo journalctl -u sssd -n 100 --no-pager
```

确认 LDAP 源条目存在：

```bash
ldapsearch -x -LLL -H ldap://127.0.0.1:389 \
  -D 'cn=sssd,ou=Services,dc=acdiost,dc=com' -W \
  -b 'uid=dawn,ou=People,dc=acdiost,dc=com' \
  '(objectClass=posixAccount)' uid uidNumber gidNumber homeDirectory loginShell
```

### SSSD 拒绝启动

SSSD 会拒绝读取权限过宽的配置文件：

```bash
sudo chown root:root /etc/sssd/sssd.conf
sudo chmod 600 /etc/sssd/sssd.conf
sudo restorecon -v /etc/sssd/sssd.conf
sudo sssctl config-check
```

### 修改 LDAP 用户后仍显示旧信息

```bash
sudo sss_cache -u dawn
getent passwd dawn
```

### 登录成功但没有家目录

```bash
authselect current
systemctl status oddjobd --no-pager
journalctl -u oddjobd -n 100 --no-pager
```

