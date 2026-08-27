# 通过 PyPI 安装和运行 openhpc_webui

本文档适用于从 PyPI 安装 `openhpc-webui 0.2.0`，而不是从 Git 仓库部署。
PyPI 安装包已包含页面模板和静态资源，但不包含仓库根目录下的 `env.example`、
Supervisor 示例及运维脚本。

PyPI 项目地址：<https://pypi.org/project/openhpc-webui/>

## 1. 适用场景

PyPI 安装适合以下场景：

- 希望安装固定版本，不需要修改源码或前端资源；
- 由 systemd、Supervisor 或其他进程管理器注入环境变量；
- 部署主机能够访问 LDAP、Slurm 控制端和所需文件系统。

门户不会安装或配置 OpenLDAP、Slurm、Nginx、磁盘配额等系统组件。运行主机仍需
提供 `sinfo`、`squeue`、`sacct`、`scontrol`、`sacctmgr`、`scancel` 等命令；启用
磁盘配额时还需安装 `quota` 和 `setquota`。

## 2. 创建独立 Python 环境

建议使用虚拟环境，避免与系统 Python 包混装：

```bash
sudo install -d -m 0755 /opt/openhpc-webui
sudo python3 -m venv /opt/openhpc-webui/venv
sudo /opt/openhpc-webui/venv/bin/python -m pip install --upgrade pip
sudo /opt/openhpc-webui/venv/bin/python -m pip install "openhpc-webui==0.2.0"
```

验证安装结果：

```bash
/opt/openhpc-webui/venv/bin/python -c \
  'import openhpc_webui; print(openhpc_webui.__version__)'
/opt/openhpc-webui/venv/bin/python -m pip show openhpc-webui
```

版本输出应为 `0.2.0`。

## 3. 配置环境变量

创建 `/opt/openhpc-webui/.env`。示例中的占位值必须替换为实际配置；密码包含空格、
`#` 等特殊字符时应使用引号：

```dotenv
AUTHORIZED=True
SECRET_KEY=replace-with-output-of-openssl-rand-hex-32
SESSION_HTTPS_ONLY=True
LOG_LEVEL=INFO

LDAP_URI=ldap://ldap.internal.example
LDAP_PORT=389
LDAP_USE_SSL=False
LDAP_BASE_DN=dc=example,dc=com
LDAP_DEFAULT_BIND_DN=cn=admin,dc=example,dc=com
LDAP_DEFAULT_AUTHTOK_TYPE=password
LDAP_DEFAULT_AUTHTOK=replace-with-ldap-admin-password

ADMIN_USERS=admin
SLURM_DEFAULT_ACCOUNT=dawn
SLURM_CONFIG_DIR=/etc/slurm
NFS_QUOTA_FS=
JOB_OUTPUT_ALLOWED_ROOTS=

LOGIN_MAX_FAILED_ATTEMPTS=5
LOGIN_LOCKOUT_MINUTES=30
```

保护配置文件：

```bash
sudo chown root:root /opt/openhpc-webui/.env
sudo chmod 600 /opt/openhpc-webui/.env
```

生产环境必须设置独立的 `SECRET_KEY`，保持 `AUTHORIZED=True`。通过 HTTPS 反向代理
访问时设置 `SESSION_HTTPS_ONLY=True`；仅在受控网络直接使用 HTTP 调试时才设为
`False`。

## 4. 快速验证

在当前 Shell 加载配置并启动：

```bash
set -a
. /opt/openhpc-webui/.env
set +a
/opt/openhpc-webui/venv/bin/openhpc_webui
```

该快捷命令固定监听 `0.0.0.0:6827`，只适合受控环境中的首次验证。验证完成后按
`Ctrl+C` 停止。生产环境不要直接将 6827 端口暴露到公网。

## 5. 使用 systemd 运行

生产环境建议让 Uvicorn 仅监听回环地址，再由 Nginx 提供 HTTPS。创建
`/etc/systemd/system/openhpc-webui.service`：

```ini
[Unit]
Description=OpenHPC Web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/openhpc-webui
EnvironmentFile=/opt/openhpc-webui/.env
ExecStart=/opt/openhpc-webui/venv/bin/uvicorn openhpc_webui.application:app --host 127.0.0.1 --port 6827 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

加载并启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now openhpc-webui
sudo systemctl status openhpc-webui
sudo journalctl -u openhpc-webui -f
```

当前版本会修改 Slurm 配置、执行管理命令并可能调用 `setquota`，示例因此使用
`root`。若改用专用账户，必须单独授予 LDAP、Slurm、配额、配置目录和环境文件所需
权限，不能只修改 systemd 的 `User`。

`WorkingDirectory` 与 `EnvironmentFile` 中的 `.env` 路径保持一致，是为了让权限管理
页面对 `ADMIN_USERS` 的在线修改可以持久化，并在服务重启后继续生效。

Nginx 反向代理及 HTTPS 配置参见[生产部署指南](./DEPLOYMENT.md)。

## 6. 升级与回滚

升级前先查看 PyPI 上的目标版本，并备份环境配置：

```bash
sudo cp -a /opt/openhpc-webui/.env /opt/openhpc-webui/.env.bak
sudo /opt/openhpc-webui/venv/bin/python -m pip install --upgrade \
  "openhpc-webui==0.2.0"
sudo systemctl restart openhpc-webui
```

升级到后续版本时，将命令中的 `0.2.0` 替换为目标版本号。

验证版本和服务：

```bash
/opt/openhpc-webui/venv/bin/python -c \
  'import openhpc_webui; print(openhpc_webui.__version__)'
sudo systemctl status openhpc-webui
```

需要回滚时，重新安装上一个明确版本并重启服务。不要在生产环境使用未固定版本的
自动升级。

## 7. 离线安装

在可联网、操作系统和 CPU 架构相同或兼容的主机上下载包及依赖：

```bash
python3 -m pip download --dest wheelhouse "openhpc-webui==0.2.0"
```

将整个 `wheelhouse` 目录复制到内网主机后安装：

```bash
/opt/openhpc-webui/venv/bin/python -m pip install \
  --no-index --find-links ./wheelhouse "openhpc-webui==0.2.0"
```

这只包含 Python 包。Slurm、LDAP 客户端、Nginx、配额工具和系统服务配置仍需通过
操作系统的软件源或离线介质准备。

## 8. 常见问题

### 启动时报 `SECRET_KEY` 错误

确认服务读取了 `/opt/openhpc-webui/.env`，并且 `SECRET_KEY` 至少包含 32 个字符：

```bash
openssl rand -hex 32
sudo systemctl show openhpc-webui --property=EnvironmentFiles
```

### 页面能打开，但 LDAP 或 Slurm 操作失败

在服务运行主机上分别验证 LDAP 地址、Bind DN 和 Slurm 命令。Web 管理员身份不会
绕过 Linux 文件权限、Slurm 权限或 LDAP ACL。

### 静态资源或模板找不到

确认实际导入的是虚拟环境内的正式安装包：

```bash
/opt/openhpc-webui/venv/bin/python -c \
  'import openhpc_webui; print(openhpc_webui.__file__)'
```

不要同时从旧源码目录启动同名模块。必要时在虚拟环境中重新安装指定版本。

### 卸载

先停止服务，再卸载 Python 包：

```bash
sudo systemctl disable --now openhpc-webui
sudo /opt/openhpc-webui/venv/bin/python -m pip uninstall openhpc-webui
```

卸载 Python 包不会删除 `/opt/openhpc-webui/.env`、systemd unit、Nginx 配置、LDAP
数据或 Slurm 配置。
