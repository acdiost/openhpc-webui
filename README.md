# OpenHPC Web 管理门户

智算中心轻量化管理门户 (Lightweight Management Portal) - 基于 FastAPI 的内网 HPC 集群管理系统。

## 🎯 项目简介

这是一个为 HPC 中心设计的轻量级 Web 管理门户,支持在离线内网环境下管理:
- **LDAP 身份系统** - 用户和组的增删改查
- **Slurm 集群资源** - 分区、节点状态监控
- **作业管理** - 实时作业队列查看与管理
- **Slurm 账户同步** - LDAP 用户与 Slurm 账户自动同步

**设计特点:**
- 🇨🇳 全中文界面
- 🎨 主题色: #dc3023 (中国红/科研红)
- 📦 离线部署 - 所有资源本地化,无需外网
- 🔒 LDAP 管理员认证
- ⚡ 极简专业的管理后台风格

## 📋 功能模块

### 1. 仪表盘 (`/`)
- 用户总数统计
- 活动作业数量
- 节点状态概览
- 作业统计图表

### 2. 用户管理 (`/users`)
- 查看所有 LDAP 用户列表
- 创建新用户 (指定 UID/GID/Home/Shell)
- 修改用户信息和密码
- 删除用户
- 自动同步到 Slurm 账户系统

### 3. 组管理 (`/groups`)
- 查看所有 LDAP 组
- 创建/删除组
- 管理组成员关系

### 4. 分区管理 (`/partitions`)
- 查看 Slurm 分区状态
- 节点状态监控 (Alloc/Idle/Down)
- 创建/更新/删除分区
- CPU 和内存使用情况

### 5. 节点监控 (`/nodes`)
- 计算节点列表
- 节点状态和资源占用
- 按分区过滤

### 6. 作业管理 (`/jobs`)
- 实时作业队列 (squeue)
- 作业详细信息查看
- 管理员取消作业 (scancel)
- 已完成作业历史记录
- 作业状态统计

## 🛠️ 技术栈

- **后端:** FastAPI (Python 3.9+)
- **前端:** 原生 HTML/JS + Tailwind CSS (本地化)
- **认证:** LDAP Admin 绑定
- **依赖:**
  - `ldap3` - LDAP 操作
  - `uvicorn` - ASGI 服务器
  - `python-dotenv` - 环境变量管理
  - Slurm CLI 工具 (sinfo, squeue, sacct, scontrol, sacctmgr)

## 🚀 快速开始

### 前置要求

- Python 3.9+
- OpenLDAP 服务
- Slurm 集群
- uv (Python 包管理器)

### 安装步骤

1. **克隆项目**
```bash
cd /opt/openhpc-web
git clone <repository-url> openhpc-web
cd openhpc-web
```

2. **安装依赖**
```bash
# 使用 uv 安装依赖
uv sync

# 或激活虚拟环境
source .venv/bin/activate
```

3. **配置环境变量**
```bash
cp .env.example .env
vim .env
```

编辑 `.env` 文件:
```ini
# LDAP 配置
LDAP_DEFAULT_BIND_DN=cn=admin,dc=acdiost,dc=com
LDAP_DEFAULT_AUTHTOK=your-ldap-password
LDAP_URI=ldap://localhost:389
LDAP_BASE_DN=dc=acdiost,dc=com

# Slurm 配置
SLURM_DEFAULT_ACCOUNT=cardc

# 应用配置
SECRET_KEY=<使用 openssl rand -hex 32 生成>
SESSION_HTTPS_ONLY=True
JOB_OUTPUT_ALLOWED_ROOTS=/data/jobs
DEBUG=False
```

4. **运行应用**

开发模式:
```bash
uvicorn main:app --reload --port 6827
```

生产模式:
```bash
uvicorn main:app --host 0.0.0.0 --port 6827
```

5. **访问应用**

打开浏览器访问: `http://your-server:6827`

直接使用 HTTP 进行本地调试时需设置 `SESSION_HTTPS_ONLY=False`；生产环境应通过 HTTPS 反向代理访问并保持该值为 `True`。

默认登录使用 LDAP 管理员账户。

## 📦 生产部署

### 使用 Supervisor 管理

详细部署说明请参考 [DEPLOYMENT.md](./DEPLOYMENT.md)

快速部署:

```bash
# 1. 安装 Supervisor
sudo yum install supervisor -y

# 2. 复制配置文件
sudo cp openhpc-web.ini /etc/supervisord.d/

# 3. 创建日志目录
sudo mkdir -p /var/log/openhpc-web

# 4. 启动服务
sudo systemctl enable supervisord
sudo systemctl start supervisord
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start openhpc-web

# 5. 查看状态
sudo supervisorctl status openhpc-web
```

### 常用管理命令

```bash
# 查看日志
sudo tail -f /var/log/openhpc-web/access.log
sudo tail -f /var/log/openhpc-web/error.log

# 重启应用
sudo supervisorctl restart openhpc-web

# 停止应用
sudo supervisorctl stop openhpc-web
```

## 📁 项目结构

```
openhpc-web/
├── main.py                  # FastAPI 应用入口
├── ldap_manager.py         # LDAP 管理模块
├── slurm_manager.py        # Slurm 管理模块
├── auth_manager.py         # 认证管理模块
├── partition_config.py     # 分区配置管理
├── templates/              # Jinja2 模板
│   ├── index.html         # 仪表盘
│   ├── login.html         # 登录页
│   ├── users.html         # 用户管理
│   ├── groups.html        # 组管理
│   ├── partitions.html    # 分区管理
│   ├── nodes.html         # 节点监控
│   └── jobs.html          # 作业管理
├── static/                 # 静态资源 (离线)
│   ├── all-tailwind-classes-full-min.css
│   └── main.js
├── .env                    # 环境变量配置
├── openhpc-web.ini        # Supervisor 配置
├── CLAUDE.md              # AI 开发指南
├── DEPLOYMENT.md          # 部署文档
├── requirement.md         # 需求说明
└── README.md              # 本文件
```

## 🔧 开发指南

### 添加新依赖

```bash
uv add package-name
```

### 代码风格

- 遵循 PEP 8 规范
- 使用类型注解
- 中文注释和文档字符串

### API 路由结构

```
/                           # 仪表盘页面
/login                      # 登录页面
/users, /groups, ...        # 功能页面

/api/auth/*                 # 认证 API
/api/ldap/users             # 用户管理 API
/api/ldap/groups            # 组管理 API
/api/slurm/partitions       # 分区管理 API
/api/slurm/nodes            # 节点监控 API
/api/slurm/jobs             # 作业管理 API
```

### Slurm 账户同步机制

当通过 Web 界面创建或删除 LDAP 用户时,系统会自动:

1. **创建用户**: 执行 `sacctmgr -i add user <username> account=<account>`
2. **删除用户**: 执行 `sacctmgr -i delete user <username>`

默认账户由环境变量 `SLURM_DEFAULT_ACCOUNT` 指定 (默认: `cardc`)。

## 🔐 安全注意事项

- **启用认证时必须设置至少 32 个随机字符的 `SECRET_KEY`**
- LDAP 管理员密码使用强密码
- 建议在防火墙后运行,仅内网访问
- 定期审计用户操作日志
- 考虑启用 HTTPS (通过 Nginx 反向代理)

## 📝 环境变量说明

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `LDAP_DEFAULT_BIND_DN` | LDAP 管理员 DN | - |
| `LDAP_DEFAULT_AUTHTOK` | LDAP 管理员密码 | - |
| `LDAP_URI` | LDAP 服务器地址 | `ldap://localhost:389` |
| `LDAP_BASE_DN` | LDAP Base DN | `dc=acdiost,dc=com` |
| `LDAP_PORT` | LDAP 端口 | `389` |
| `LDAP_USE_SSL` | 是否使用 SSL | `False` |
| `SLURM_DEFAULT_ACCOUNT` | Slurm 默认账户 | `cardc` |
| `SECRET_KEY` | Session 密钥，至少 32 个随机字符 | **必须设置** |
| `SESSION_HTTPS_ONLY` | 仅通过 HTTPS 发送 Session Cookie | `True` |
| `JOB_OUTPUT_ALLOWED_ROOTS` | 作业输出额外允许目录，Linux 下用冒号分隔 | - |
| `DEBUG` | 调试模式 | `False` |

## 🐛 故障排查

### LDAP 连接失败

```bash
# 测试 LDAP 连接
ldapsearch -x -H ldap://localhost:389 -D "cn=admin,dc=acdiost,dc=com" -w password -b "dc=acdiost,dc=com"
```

### Slurm 命令权限问题

确保运行用户有执行 Slurm 命令的权限:
```bash
sinfo
squeue
sacctmgr show account
```

### Supervisor 进程未启动

```bash
# 查看 Supervisor 日志
sudo tail -f /var/log/supervisord.log

# 查看应用日志
sudo tail -f /var/log/openhpc-web/error.log
```

## 📄 许可证

本项目仅供内部使用。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。

## 📧 联系方式

如有问题,请联系系统管理员。

---

**注意:** 本系统设计用于内网离线环境,所有静态资源已本地化,无需外网连接。
