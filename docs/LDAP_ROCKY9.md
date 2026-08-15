# Rocky Linux 9 LDAP 安装与初始化

本文说明如何在 Rocky Linux 9 上部署 OpenLDAP，并初始化为本项目要求的目录结构：

```text
dc=acdiost,dc=com
├── ou=People
│   └── uid=admin
└── ou=Groups
    └── cn=admins
```

本项目固定从 `ou=People,dc=acdiost,dc=com` 查询用户，从
`ou=Groups,dc=acdiost,dc=com` 查询组。请不要把组织单元改成其他名称。

本文步骤已在以下环境验证：

- Rocky Linux 9.7（aarch64）
- `openldap-servers-2.6.8-2.el9.aarch64`
- `openldap-clients-2.6.8-4.el9.0.1.aarch64`

## 1. 安装软件包

Rocky Linux 9 的不同镜像或内网软件源所提供的软件包可能不同。先检查
`openldap-servers` 是否可用：

```bash
sudo dnf install -y dnf-plugins-core
sudo dnf config-manager --set-enabled crb
sudo dnf install -y epel-release
sudo dnf makecache
dnf repoquery openldap-servers
```

能查到软件包后安装服务端和客户端：

```bash
sudo dnf install -y openldap-servers openldap-clients
```

> 不要在 Rocky Linux 9 上强行安装 EL8 的 RPM。如果内网源没有
> `openldap-servers`，应先将与 Rocky Linux 9 匹配的 OpenLDAP RPM 及其依赖同步到
> 内网仓库，再继续本文步骤。

启动服务：

```bash
sudo systemctl enable --now slapd
sudo systemctl status slapd --no-pager
```

确认本机 389 端口正在监听：

```bash
sudo ss -lntp | grep ':389'
```

## 2. 设置目录后缀和管理账号

先生成目录管理密码的 SSHA 哈希。命令会提示输入两次密码：

```bash
slappasswd
```

保存输出的完整字符串，例如 `{SSHA}...`。不要把明文密码或哈希提交到 Git。

查询当前 MDB 数据库的配置 DN：

```bash
sudo ldapsearch -Y EXTERNAL -H ldapi:/// \
  -b cn=config '(olcDatabase=*mdb)' dn olcSuffix
```

通常返回 `olcDatabase={2}mdb,cn=config`。以实际查询结果为准，将下面 LDIF 第一行
的 DN 和 `<SSHA password hash>` 替换后保存为 `/root/set-domain.ldif`：

```ldif
dn: olcDatabase={2}mdb,cn=config
changetype: modify
replace: olcSuffix
olcSuffix: dc=acdiost,dc=com
-
replace: olcRootDN
olcRootDN: cn=admin,dc=acdiost,dc=com
-
replace: olcRootPW
olcRootPW: <SSHA password hash>
-
replace: olcAccess
olcAccess: {0}to attrs=userPassword by self write by anonymous auth by * none
olcAccess: {1}to * by self read by users read by * none
```

应用配置：

```bash
sudo ldapmodify -Y EXTERNAL -H ldapi:/// -f /root/set-domain.ldif
sudo rm -f /root/set-domain.ldif
```

这里的 `cn=admin,dc=acdiost,dc=com` 是目录管理绑定账号，不需要在目录树中另建
同名条目。WebUI 使用它执行用户和组的增删改查。

上述 ACL 允许匿名连接校验密码，但禁止读取密码属性；认证用户可读取目录并修改
自己的密码。`olcRootDN` 不受该 ACL 限制，仍可管理整个业务目录。

## 3. 确认基础 schema

用户和组功能依赖 `cosine`、`inetorgperson` 和 `nis` schema。检查是否已加载：

```bash
sudo ldapsearch -Y EXTERNAL -H ldapi:/// \
  -b cn=schema,cn=config '(cn=*)' cn
```

RPM 默认通常已经加载。若结果中缺少下列 schema，再执行：

```bash
sudo ldapadd -Y EXTERNAL -H ldapi:/// \
  -f /etc/openldap/schema/cosine.ldif
sudo ldapadd -Y EXTERNAL -H ldapi:/// \
  -f /etc/openldap/schema/inetorgperson.ldif
sudo ldapadd -Y EXTERNAL -H ldapi:/// \
  -f /etc/openldap/schema/nis.ldif
```

如果命令返回 `Duplicate entry`，表示该 schema 已存在，无需重复添加。

## 4. 添加 SSH 公钥 schema

WebUI 的 SSH 公钥功能会给用户增加 `ldapPublicKey` 对象类，并写入
`sshPublicKey` 属性。创建 `/root/openssh-lpk.ldif`：

```ldif
dn: cn=openssh-lpk,cn=schema,cn=config
objectClass: olcSchemaConfig
cn: openssh-lpk
olcAttributeTypes: ( 1.3.6.1.4.1.24552.500.1.1 NAME 'sshPublicKey' DESC 'OpenSSH public key' EQUALITY octetStringMatch SYNTAX 1.3.6.1.4.1.1466.115.121.1.40 )
olcObjectClasses: ( 1.3.6.1.4.1.24552.500.1.2 NAME 'ldapPublicKey' DESC 'OpenSSH public key holder' SUP top AUXILIARY MUST uid MAY sshPublicKey )
```

加载并删除临时文件：

```bash
sudo ldapadd -Y EXTERNAL -H ldapi:/// -f /root/openssh-lpk.ldif
sudo rm -f /root/openssh-lpk.ldif
```

不使用 WebUI 的 SSH 公钥管理功能时，可以跳过本节。

## 5. 初始化目录树

为用于网页登录的 `admin` 用户单独生成密码哈希：

```bash
slappasswd
```

目录管理绑定账号和网页登录用户是两个不同用途的身份，建议使用不同密码。

将下面内容保存为 `/root/base-tree.ldif`，并把 `<admin user SSHA hash>` 替换为刚生成
的哈希：

```ldif
dn: dc=acdiost,dc=com
objectClass: top
objectClass: dcObject
objectClass: organization
dc: acdiost
o: ACDIOST

dn: ou=People,dc=acdiost,dc=com
objectClass: top
objectClass: organizationalUnit
ou: People

dn: ou=Groups,dc=acdiost,dc=com
objectClass: top
objectClass: organizationalUnit
ou: Groups

dn: cn=admins,ou=Groups,dc=acdiost,dc=com
objectClass: top
objectClass: posixGroup
cn: admins
gidNumber: 10000
memberUid: admin
description: openhpc_webui administrators

dn: uid=admin,ou=People,dc=acdiost,dc=com
objectClass: top
objectClass: inetOrgPerson
objectClass: posixAccount
uid: admin
cn: admin
sn: admin
uidNumber: 10000
gidNumber: 10000
homeDirectory: /home/admin
loginShell: /bin/bash
userPassword: <admin user SSHA hash>
```

导入目录树。`-W` 会提示输入第 2 步设置的目录管理密码：

```bash
ldapadd -x -H ldap://127.0.0.1:389 \
  -D 'cn=admin,dc=acdiost,dc=com' -W \
  -f /root/base-tree.ldif
sudo rm -f /root/base-tree.ldif
```

UID 和 GID 必须在整个集群中唯一。示例使用 `10000`，生产环境应先与现有 Linux、
NFS 和 Slurm 账号规划核对。

## 6. 配置防火墙和 SELinux

LDAP 与 WebUI 在同一台服务器时，无需开放 389 端口。分开部署时，只允许 WebUI
服务器或管理网段访问，下面以 `10.10.0.0/24` 为例：

```bash
sudo firewall-cmd --permanent \
  --add-rich-rule='rule family="ipv4" source address="10.10.0.0/24" port protocol="tcp" port="389" accept'
sudo firewall-cmd --reload
```

如果 `systemctl is-active firewalld` 返回 `inactive`，上述规则不会生效，且 slapd
监听 `0.0.0.0:389` 时可能被同一网络中的其他主机访问。启用防火墙前先确认 SSH
和服务器上其他业务所需端口，避免中断远程连接。

保持 SELinux 为 Enforcing，不要使用 `setenforce 0` 规避配置问题：

```bash
getenforce
sudo restorecon -Rv /etc/openldap /var/lib/ldap
```

跨主机传输密码时不能长期使用明文 LDAP。生产环境应配置服务器证书并使用
StartTLS 或 LDAPS（636），同时仅开放对应端口。

## 7. 验证 LDAP

验证目录管理账号能够查询目录：

```bash
ldapsearch -x -LLL -H ldap://127.0.0.1:389 \
  -D 'cn=admin,dc=acdiost,dc=com' -W \
  -b 'dc=acdiost,dc=com'
```

验证网页登录用户能够绑定：

```bash
ldapwhoami -x -H ldap://127.0.0.1:389 \
  -D 'uid=admin,ou=People,dc=acdiost,dc=com' -W
```

成功时应返回：

```text
dn:uid=admin,ou=People,dc=acdiost,dc=com
```

## 8. 对接 openhpc_webui

在项目目录复制并编辑环境变量文件：

```bash
cd /opt/openhpc_webui
cp env.example .env
chmod 600 .env
vi .env
```

LDAP 与 WebUI 在同一台主机时使用：

```ini
LDAP_DEFAULT_BIND_DN=cn=admin,dc=acdiost,dc=com
LDAP_DEFAULT_AUTHTOK_TYPE=password
LDAP_DEFAULT_AUTHTOK=<第 2 步设置的目录管理明文密码>
LDAP_URI=ldap://127.0.0.1:389
LDAP_BASE_DN=dc=acdiost,dc=com
LDAP_PORT=389
LDAP_USE_SSL=False
ADMIN_USERS=admin
```

LDAP 在其他服务器时，将 `LDAP_URI` 改为该服务器的内网地址。完成后重启 WebUI：

```bash
sudo supervisorctl restart openhpc_webui
sudo supervisorctl status openhpc_webui
```

使用第 5 步创建的 `admin` 用户和密码登录 WebUI。不要使用目录管理绑定 DN 登录；
当前登录逻辑只接受 `ou=People` 下的 `uid` 用户。

## 9. 常见问题

### `dnf` 找不到 `openldap-servers`

确认 CRB、EPEL 或组织内部 Rocky Linux 9 软件源已启用：

```bash
dnf repolist --enabled
dnf repoquery --whatprovides '*/slapd'
```

不要混装其他 EL 大版本的软件包。

### WebUI 显示 LDAP 连接失败

先在 WebUI 主机使用 `.env` 中完全相同的 URI 和绑定 DN 执行：

```bash
ldapwhoami -x -H ldap://127.0.0.1:389 \
  -D 'cn=admin,dc=acdiost,dc=com' -W
```

然后检查服务和日志：

```bash
sudo systemctl status slapd --no-pager
sudo journalctl -u slapd -n 100 --no-pager
```

### 用户可以绑定但不能登录 WebUI

确认用户 DN 位于 `ou=People`，并具有 `uid`、`inetOrgPerson` 和 `posixAccount`：

```bash
ldapsearch -x -LLL -H ldap://127.0.0.1:389 \
  -D 'cn=admin,dc=acdiost,dc=com' -W \
  -b 'uid=admin,ou=People,dc=acdiost,dc=com' \
  '(objectClass=*)' dn objectClass uid uidNumber gidNumber
```

### SSH 公钥写入失败

确认 `sshPublicKey` 属性已经加载：

```bash
sudo ldapsearch -Y EXTERNAL -H ldapi:/// \
  -b cn=schema,cn=config '(olcAttributeTypes=*sshPublicKey*)' dn
```
