# openhpc_webui 生产部署

本文档适用于 `openhpc_webui 0.3.0`，使用 Supervisor 托管单个 Uvicorn 进程，并通过 Nginx 提供 HTTPS。示例安装目录为 `/opt/openhpc_webui`，应用仅监听 `127.0.0.1:6827`。

## 1. 部署前提

部署主机应满足：

- Python 3.9 或更高版本，并已安装 `uv`、Git、OpenSSH 客户端和 Supervisor。
- 能访问 LDAP 服务以及 Slurm 控制端。
- 已安装 `sinfo`、`squeue`、`sacct`、`scontrol`、`sacctmgr`、`scancel`；启用存储配额时还需 `quota`、`setquota`。
- Slurm 配置包含项目管理的 `/etc/slurm/partition.conf` 和 `/etc/slurm/node.conf`；可通过 `SLURM_CONFIG_DIR` 覆盖目录。
- 防火墙只向受控网络开放 Nginx 的 80/443 端口，不开放 6827。

先确认系统命令可用：

```bash
python3 --version
uv --version
command -v ssh-keygen sinfo squeue sacct scontrol sacctmgr scancel
```

当前实现会直接替换 Slurm 配置片段、执行 `scontrol reconfigure`，启用 NFS 配额时还会执行 `setquota`。仓库提供的 Supervisor 配置因此使用 `root`。只有在关闭配额写入，并向专用账户完整授予 Slurm 管理权限、配置目录写权限和 `.env` 写权限后，才能安全地修改 `user=`。配置文件采用同目录临时文件加原子替换，不能只授予文件写权限，也不要使用会在替换时失效的符号链接。

## 2. 安装项目

进入 root Shell，使用锁文件安装生产依赖：

```bash
sudo -i
git clone https://github.com/acdiost/openhpc-webui.git /opt/openhpc_webui
cd /opt/openhpc_webui
uv sync --locked --no-dev
cp env.example .env
chmod 600 .env
exit
```

离线环境应提前把仓库、uv 可执行文件和 Python 包缓存带入内网；不要在生产主机上临时改动 `uv.lock`。

## 3. 配置环境变量

编辑 `/opt/openhpc_webui/.env`，至少确认以下配置：

```dotenv
AUTHORIZED=True
SECRET_KEY=<至少 32 字符的独立随机值>
SESSION_HTTPS_ONLY=True

LDAP_URI=ldap://ldap.internal.example
LDAP_PORT=389
LDAP_USE_SSL=False
LDAP_BASE_DN=dc=example,dc=com
LDAP_DEFAULT_BIND_DN=cn=admin,dc=example,dc=com
LDAP_DEFAULT_AUTHTOK=<LDAP 管理密码>

ADMIN_USERS=admin
SLURM_DEFAULT_ACCOUNT=dawn
SLURM_CONFIG_DIR=/etc/slurm
NFS_QUOTA_FS=
JOB_OUTPUT_ALLOWED_ROOTS=
FILE_UPLOAD_MAX_MB=1024
FILE_EDIT_MAX_KB=2048
```

生成 Session 密钥：

```bash
openssl rand -hex 32
```

使用 HTTPS 反向代理时必须设置 `SESSION_HTTPS_ONLY=True`。只有在受控网络中直接使用 HTTP 访问 Uvicorn 时才设置为 `False`。生产环境必须保持 `AUTHORIZED=True`。

`.env` 需要由运行用户读取和写入，因为权限管理页面会持久化 `ADMIN_USERS`。不要在日志、工单或 Shell 历史中输出整个 `.env`。

文件管理中，普通用户的页面根目录固定映射到 LDAP `homeDirectory`；管理员首次进入自己的 NSS/SSSD Home，但访问范围仍映射到系统 `/`。要让管理员真正以 root 权限访问所有路径，Supervisor 必须按仓库默认配置使用 `user=root`；如果改为专用运行账户，管理员页面会提示权限受限。上传、新建目录会在 root 运行模式下恢复为普通用户的 LDAP UID/GID。在线编辑仅接受大小不超过 `FILE_EDIT_MAX_KB` 的 UTF-8 普通文本。由于管理员可修改和删除系统路径，只应向受信任的运维人员授予管理员权限，并确保审计日志被集中留存。

## 4. 校验 Slurm 集成

门户默认维护以下两个普通文件：

```text
/etc/slurm/partition.conf
/etc/slurm/node.conf
```

设置 `SLURM_CONFIG_DIR` 后，门户会改为维护该目录下的同名文件。它们必须被当前生效的 `slurm.conf` 包含。部署前先备份 Slurm 配置，并确认两个文件所在目录可写：

```bash
sudo test -f /etc/slurm/partition.conf
sudo test -f /etc/slurm/node.conf
sudo cp -a /etc/slurm/partition.conf /etc/slurm/partition.conf.bak
sudo cp -a /etc/slurm/node.conf /etc/slurm/node.conf.bak

sinfo
squeue
sacct -n -X --starttime today
sacctmgr show account
sudo scontrol reconfigure
```

如果只需要查看集群状态，不应向普通门户管理员开放分区、节点、账户和配额修改功能；当前版本尚未提供只读部署开关。

## 5. 安装 Supervisor 配置

创建日志目录：

```bash
sudo install -d -o root -g root -m 0750 /var/log/openhpc_webui
```

复制仓库中的 [Supervisor 配置](../openhpc_webui.ini)：

```bash
# Rocky Linux / RHEL
sudo cp /opt/openhpc_webui/openhpc_webui.ini \
  /etc/supervisord.d/openhpc_webui.ini

# Ubuntu / Debian（必须使用 .conf 后缀）
sudo cp /opt/openhpc_webui/openhpc_webui.ini \
  /etc/supervisor/conf.d/openhpc_webui.conf
```

启动 Supervisor 并加载应用：

```bash
# Rocky Linux / RHEL
sudo systemctl enable --now supervisord

# Ubuntu / Debian
sudo systemctl enable --now supervisor

sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status openhpc_webui
```

状态应为 `RUNNING`。如果显示 `BACKOFF` 或 `FATAL`，先检查 `/var/log/openhpc_webui/error.log`。

## 6. 配置 Nginx 与 HTTPS

安装 Nginx 并准备由可信 CA 或内部 CA 签发的证书。示例站点配置：

仓库同时提供可直接复制修改的 [Nginx HTTPS/WebSocket 示例](../deploy/nginx/openhpc_webui.conf.example)。

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ""      close;
}

server {
    listen 80;
    server_name hpc.example.edu;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name hpc.example.edu;

    ssl_certificate     /etc/pki/tls/certs/openhpc_webui.crt;
    ssl_certificate_key /etc/pki/tls/private/openhpc_webui.key;

    location / {
        proxy_pass http://127.0.0.1:6827;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

检查并重新加载 Nginx：

```bash
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

Supervisor 配置只信任来自 `127.0.0.1` 的代理头。Nginx 不在同一台主机时，应使用实际代理地址更新 `--forwarded-allow-ips`，并用防火墙限制 6827 只接受该代理连接。

Web 终端要求登录用户已经通过 SSSD/NSS 同步为本机 Linux 账户，具有唯一 UID、存在的 Home 目录和可执行的登录 Shell。服务以 root 运行时会在启动 Shell 前调用 `initgroups`、`setgid` 和 `setuid` 降权；应用环境变量不会传入终端。门户管理员也始终以自己的系统身份启动，并以 NSS 返回的个人 Home 作为初始工作目录，不沿用管理员文件管理的 `/` 或 `/root` 范围。可用 `TERMINAL_ENABLED=False` 完全关闭入口。生产环境必须使用 HTTPS/WSS，并保留上面的 WebSocket `Upgrade` 请求头。

终端页面支持悬浮收缩，但该模式仍属于当前浏览器页面，不会跨页面迁移 WebSocket。用户需要同时操作其他门户页面时，应使用侧边栏的新标签入口，或在离开确认框中选择“新标签打开目标”。浏览器刷新、关闭页签或确认离开时会关闭当前 Shell，因此不要把 Web 终端作为脱离会话的长期任务托管工具；长任务应使用 Slurm、`tmux` 或 `screen`。

### 终端 AI 模型

终端 AI 通过 OpenAI 兼容的 `POST /chat/completions` 接口访问 DeepSeek、vLLM、SGLang 或其他兼容服务。可直接编辑 `.env`，也可由管理员在“账户设置”页面在线保存：

```dotenv
TERMINAL_AI_ENABLED=True
TERMINAL_AI_PROVIDER=deepseek
TERMINAL_AI_BASE_URL=https://api.deepseek.com/v1
TERMINAL_AI_MODEL=deepseek-chat
TERMINAL_AI_API_KEY=replace-with-a-real-key
TERMINAL_AI_TIMEOUT_SECONDS=60
```

vLLM 常用 Base URL 为 `http://127.0.0.1:8000/v1`，SGLang 常用值为 `http://127.0.0.1:30000/v1`，但应以实际启动参数为准。若模型服务运行在容器中，`127.0.0.1` 指向 WebUI 容器自身；应改用同一 Docker 网络中的服务名或可路由地址，并用防火墙限制访问范围。

设置页写入配置后当前 WebUI 进程立即生效，新建终端连接会获得最新状态；直接编辑 `.env` 后仍需重启应用。`.env` 必须允许 WebUI 进程写入且权限应限制为 `0600`。API Key 只保存在服务端，接口仅返回“是否已配置”，审计日志会对 `api_key` 字段脱敏。

启用后，自然语言问题会发送给所配置的模型服务。用户确认 AI 建议命令后，最多约 128 KiB 的命令输出也会发送用于总结，因此只应接入符合本单位数据安全要求的模型服务。建议优先部署内网 vLLM/SGLang，并限制模型 API 仅由 WebUI 后端访问。

AI 对话上下文仅保存在当前 WebSocket 会话内，最多保留最近四轮；点击“新对话”、断开连接或关闭页面都会清除，不写入数据库。执行 AI 建议命令时，WebUI 会过滤 PTY 对内部包装行的回显；用户只能看到确认过的命令及真实输出，内部开始/完成标记也不会显示或发送给总结模型。

模型响应协议支持 `answer`、`command` 和 `file`。`file` 包含相对路径、完整内容和是否可执行，后端会把它转换为当前用户 Shell 中的待确认写入操作；模型即使没有严格返回 JSON，只要回复中包含“保存为文件名”和 fenced 代码块，WebUI 也会恢复成相同的待确认操作。服务端不会绕过 PTY 以 root 身份代替用户写文件。

## 7. 上线验证

```bash
sudo supervisorctl status openhpc_webui
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:6827/login
curl -fsS -o /dev/null -w '%{http_code}\n' https://hpc.example.edu/login
```

两个请求都应返回 `200`。随后使用 LDAP 普通用户和管理员用户分别验证：登录、权限隔离、用户与组查询、作业查询。生产变更前还应备份并验证分区、节点、账户和配额操作。

## 8. 日常运维

```bash
sudo supervisorctl status openhpc_webui
sudo supervisorctl restart openhpc_webui
sudo supervisorctl stop openhpc_webui
sudo supervisorctl start openhpc_webui

sudo tail -f /var/log/openhpc_webui/access.log
sudo tail -f /var/log/openhpc_webui/error.log
```

单进程是当前版本的预期配置。管理员列表会同时更新进程环境与 `.env`；直接增加多个 Uvicorn worker 会导致进程间权限状态短暂不一致。

## 9. 升级与回滚

升级前备份 `.env` 和 Slurm 配置：

```bash
sudo -i
cd /opt/openhpc_webui
install -d -m 0700 /var/backups/openhpc_webui
cp -a .env /var/backups/openhpc_webui/.env
git pull --ff-only
uv sync --locked --no-dev
.venv/bin/python -m unittest discover -s tests
supervisorctl restart openhpc_webui
supervisorctl status openhpc_webui
exit
```

如果升级失败，切回升级前确认过的 Git 提交，重新执行 `uv sync --locked --no-dev`，并从 `/var/backups/openhpc_webui/.env` 恢复配置。不要只回退 Python 文件而保留不匹配的锁文件。

## 10. 故障排查

### Supervisor 无法启动

```bash
sudo supervisorctl status openhpc_webui
sudo tail -n 100 /var/log/openhpc_webui/error.log
sudo test -x /opt/openhpc_webui/.venv/bin/uvicorn
sudo stat /opt/openhpc_webui/.env
```

确认 Supervisor 配置中的 `directory`、`command` 和实际安装目录完全一致。

### 登录或 Session 异常

- 确认 `AUTHORIZED=True`，`SECRET_KEY` 长度不少于 32 个字符。
- HTTPS 部署确认 `SESSION_HTTPS_ONLY=True`，且 Nginx 传递了 `X-Forwarded-Proto`。
- 修改 `.env` 后重启应用；通过权限管理页面修改 `ADMIN_USERS`，或通过账户设置页面修改终端 AI 配置，不需要重启。

### LDAP 连接失败

从应用主机使用 `.env` 中相同的 URI、Bind DN 和 Base DN 执行 `ldapsearch`。不要把密码直接写在命令行参数中，使用 `-W` 交互输入。

### Slurm 页面无数据或管理操作失败

以 Supervisor 配置中的运行用户执行 `sinfo`、`squeue`、`sacct`、`sacctmgr show account` 和 `scontrol reconfigure`。同时确认 `SLURM_CONFIG_DIR` 对应目录权限、SlurmDBD 状态和控制端连通性。

### 配额操作失败

确认 `quota`、`setquota` 已安装，`NFS_QUOTA_FS` 指向启用了用户配额的实际文件系统，并且运行用户有执行 `setquota` 的权限。不使用配额时将 `NFS_QUOTA_FS` 留空。
