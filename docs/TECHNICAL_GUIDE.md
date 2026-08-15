# openhpc_webui 技术指南

本文档适用于 `openhpc_webui 0.2.0`，面向负责安装、配置、维护和开发的集群管理员与开发者。终端用户请阅读[用户使用手册](./USER_MANUAL.md)。

## 技术架构

- 后端：FastAPI、Starlette Session、Jinja2，支持 Python 3.9 及以上版本
- 前端：原生 HTML、JavaScript 和本地 Tailwind CSS 静态资源
- 身份与目录：OpenLDAP，通过 `ldap3` 完成认证和目录操作
- 调度系统：通过 `sinfo`、`squeue`、`sacct`、`scontrol`、`sacctmgr` 和 `scancel` 等 Slurm CLI 交互
- 进程服务：Uvicorn；仓库提供 Supervisor 配置示例
- 配置：`.env` 环境变量，由 `python-dotenv` 加载

所有浏览器端资源均保存在 `static/`，运行时不需要访问公共 CDN。

## 环境要求

- Python 3.9+
- `uv` Python 包管理器
- 可访问的 OpenLDAP 服务
- 已安装客户端命令且可访问控制端的 Slurm 集群
- 运行门户的系统用户具有所需 Slurm 命令和配置文件权限

在 Rocky Linux 9 上从零配置身份服务时，可参考：

- [LDAP 安装与初始化](./LDAP_ROCKY9.md)
- [使用 SSSD 接入 LDAP](./SSSD_LDAP_ROCKY9.md)

## 安装

```bash
git clone https://github.com/acdiost/openhpc-webui.git /opt/openhpc_webui
cd /opt/openhpc_webui
uv sync
cp env.example .env
```

编辑 `.env`，至少配置 LDAP 管理凭据、LDAP 地址和 Session 密钥。启用认证时，`SECRET_KEY` 必须是至少 32 个字符的独立随机值：

```bash
openssl rand -hex 32
```

## 配置

以 `env.example` 为配置模板，不要把包含真实凭据的 `.env` 提交到版本库。

### LDAP 与认证

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `LDAP_DEFAULT_BIND_DN` | LDAP 管理员 Bind DN | - |
| `LDAP_DEFAULT_AUTHTOK` | LDAP 管理员密码 | - |
| `LDAP_URI` | LDAP 服务地址 | `ldap://localhost` |
| `LDAP_BASE_DN` | LDAP Base DN | `dc=acdiost,dc=com` |
| `LDAP_USE_SSL` | 是否使用 LDAPS | `False` |
| `AUTHORIZED` | 是否启用登录认证；生产环境必须为 `True` | `True` |
| `ADMIN_USERS` | 管理员用户名，多个值以英文逗号分隔 | - |

登录认证使用用户 DN `uid=<username>,ou=People,<LDAP_BASE_DN>`。目录结构不同时，需要同步调整认证与 LDAP 管理模块。

### Session 与网络

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `SECRET_KEY` | Session 签名密钥；启用认证时至少 32 个字符 | 必须配置 |
| `SESSION_HTTPS_ONLY` | 是否只通过 HTTPS 发送 Session Cookie | `False` |

直接通过 HTTP 访问时保持 `SESSION_HTTPS_ONLY=False`。经 HTTPS 反向代理访问时必须设为 `True`，并确保代理正确传递协议和客户端信息。

### Slurm、作业输出与存储

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `SLURM_DEFAULT_ACCOUNT` | 新建 LDAP 用户时使用的默认 Slurm 账户 | `dawn` |
| `JOB_OUTPUT_ALLOWED_ROOTS` | 允许读取作业输出的额外根目录；Linux 下以冒号分隔 | - |
| `NFS_QUOTA_FS` | 启用用户存储配额时使用的文件系统路径 | - |

用户在 LDAP 中配置的 Home 目录会自动加入其作业输出允许范围。不要将敏感系统目录加入 `JOB_OUTPUT_ALLOWED_ROOTS`。

## 运行

项目安装后可直接使用命令行入口：

```bash
uv run openhpc_webui
```

开发模式：

```bash
uvicorn openhpc_webui.application:app --reload --port 6827
```

本机生产预检：

```bash
uvicorn openhpc_webui.application:app --host 127.0.0.1 --port 6827 \
  --proxy-headers --forwarded-allow-ips=127.0.0.1
```

本机可访问 `http://127.0.0.1:6827`。生产环境应通过 Nginx 提供 HTTPS，完整安装、进程托管、升级和回滚步骤请参阅[生产部署](./DEPLOYMENT.md)。

## 权限模型

- `AUTHORIZED=False` 仅用于本地调试，会跳过登录并以调试管理员身份访问系统。
- `AUTHORIZED=True` 时，所有受保护页面和 API 都要求有效 Session。
- `ADMIN_USERS` 中的用户拥有管理权限；修改管理员列表后，权限会在后续请求中即时刷新。
- 普通用户只能访问个人账户和作业相关功能，并只能操作自己的作业。

门户运行用户还必须具备底层系统权限。Web 层的管理员身份不会绕过操作系统、Slurm 或文件系统权限。

## Slurm 同步机制

通过门户创建 LDAP 用户时，系统会为该用户建立 Slurm 关联；删除用户时会移除对应关联。默认账户由 `SLURM_DEFAULT_ACCOUNT` 控制。

进行生产部署前，应使用门户运行账户验证以下命令：

```bash
sinfo
squeue
sacct
sacctmgr show account
```

涉及分区、节点或账户修改的操作还需要相应管理权限。

## 项目结构

```text
openhpc_webui/
├── openhpc_webui/           # Python 应用包
│   ├── __main__.py          # python -m openhpc_webui 入口
│   ├── application.py       # FastAPI 工厂、页面与 API 路由
│   ├── cli.py               # openhpc_webui 命令行入口
│   ├── config.py            # 环境配置与项目路径
│   ├── schemas.py           # API 请求模型
│   └── services/            # LDAP、Slurm 与系统集成
├── templates/               # Jinja2 页面与共享组件
├── static/                  # 离线 CSS 和页面脚本
├── tests/                   # 自动化测试
├── docs/                    # 用户、部署和技术文档
├── env.example              # 环境变量模板
├── openhpc_webui.ini        # Supervisor 配置示例
└── pyproject.toml           # Python 项目与依赖配置
```

## 页面与 API

主要页面包括：

- `/`：管理员仪表盘
- `/users`、`/groups`：LDAP 用户和组
- `/accounts`、`/cluster-users`：Slurm 账户与用户关联
- `/partitions`、`/nodes`：分区和节点
- `/jobs`：作业管理
- `/account`：个人账户
- `/admin`：门户管理员权限

API 按职责划分：

- `/api/auth/*`：登录、登出、当前用户和密码修改
- `/api/ldap/*`：LDAP 状态、用户和组
- `/api/slurm/*`：账户、关联、分区、节点、作业和用量
- `/api/admin/*`：门户管理员列表维护

路由处理应保持轻量，LDAP、Slurm、认证与配置文件操作应继续放在对应 manager 模块中。

## 开发与验证

安装或更新依赖：

```bash
uv sync
uv add <package-name>
```

快速语法检查：

```bash
python -m compileall -q openhpc_webui
```

运行现有测试：

```bash
python -m unittest discover -s tests
```

涉及页面或系统集成的变更，还应手动验证登录、用户和组管理、分区、节点、账户与作业页面。模板或静态资源变更应在 Pull Request 中附界面截图。

## 安全基线

- 生产环境保持 `AUTHORIZED=True`，并使用独立强随机 `SECRET_KEY`。
- 通过 HTTPS 暴露服务时设置 `SESSION_HTTPS_ONLY=True`。
- 使用强 LDAP 管理密码并定期轮换，不在日志、文档或提交中记录凭据。
- 仅授予门户运行用户完成业务所需的 Slurm 和文件权限。
- 将服务限制在受控内网，并通过防火墙和反向代理控制访问。
- 定期审计管理员列表、Slurm 管理操作和应用日志。
- 严格限制可读取作业输出的目录范围。

## 故障排查

### 登录成功后返回登录页

检查访问协议与 `SESSION_HTTPS_ONLY` 是否匹配。HTTP 访问必须为 `False`；HTTPS 访问应为 `True`。修改环境变量后需要重启应用。

### LDAP 连接或认证失败

确认 LDAP 地址、Base DN、Bind DN、密码和用户目录结构。可在服务器上直接验证：

```bash
ldapsearch -x \
  -H ldap://localhost:389 \
  -D "cn=admin,dc=acdiost,dc=com" \
  -W \
  -b "dc=acdiost,dc=com"
```

使用 `-W` 交互输入密码，避免凭据进入 Shell 历史记录。

### Slurm 页面无数据或操作失败

用运行门户的系统用户执行 `sinfo`、`squeue`、`sacct` 和 `sacctmgr show account`，检查命令是否存在、控制端是否可达以及权限是否充足。

### Supervisor 无法启动应用

检查虚拟环境路径、工作目录、`.env` 和日志：

```bash
sudo supervisorctl status openhpc_webui
sudo tail -f /var/log/openhpc_webui/error.log
sudo tail -f /var/log/supervisord.log
```

更多 Supervisor 配置检查见[生产部署](./DEPLOYMENT.md)。
