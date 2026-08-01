项目开发需求说明：智算中心轻量化管理门户 (LMP)

1. 项目概览
构建一个基于 FastAPI 的轻量化 Web 管理门户，用于在离线内网环境下管理 LDAP 身份体系、Slurm 集群资源及作业状态。

设计风格：管理后台风，极简、专业。

主题色：#dc3023 (中国红/科研红)。

语言：全中文界面。

部署环境：内网离线环境（所有静态资源需本地化）。

2. 技术栈要求
后端：FastAPI (Python 3.9+)

前端：原生 HTML/JS + Tailwind CSS (需通过 CDN 本地化或引入内网私有化文件)

认证：LDAP Admin 绑定认证

依赖库：

python-ldap: 处理 LDAP 增删改查。

pyslurm 或 直接调用 subprocess 解析 Slurm 命令行工具 (sinfo, squeue, sacct)。

uvicorn: ASGI 服务器。

3. 核心功能模块
3.1 LDAP 用户与组管理
用户管理：列表展示、创建用户（指定 UID/GID、家目录、Shell）、修改密码、删除用户。

组管理：同步管理 LDAP Group，支持将用户移入/移出特定组。

状态检查：验证 LDAP 服务连接状态。

3.2 Slurm 资源管理
分区 (Partition) 概览：展示分区名称、节点状态（Alloc/Idle/Down）、限额配置。

节点监控：查看计算节点的状态、CPU/显存占用率简报。

队列控制：支持对特定分区进行 Up/Down 操作（需管理员权限）。

3.3 作业管理 (Job Viewer)
作业列表：实时展示 squeue 信息（JobID、用户、分区、状态、运行时间、节点数）。

作业详情：点击查看作业提交脚本或标准输出路径。

作业干预：支持管理员取消（scancel）指定作业。

4. UI/UX 视觉规范
布局：左侧固定侧边栏导航，顶部面包屑，右侧主体内容区。

配色方案：

主色调：#dc3023 (用于导航选中态、按钮、Logo)。

背景色：#f8f9fa (淡灰)。

文字：核心文字 #1a1a1a。

组件风格：

卡片式容器，带微小投影。

表格要求：紧凑型，支持分列排序。

状态标签：Slurm 运行中（绿色）、排队（黄色）、完成/失败（灰色/红色）。

5. 离线化适配指令 (重要)
静态资源：严禁从外部 CDN 加载 Tailwind、FontAwesome 或 Google Fonts。

打包逻辑：在 static/ 目录下存放 tailwind.min.css。

字体：使用系统默认无衬线字体（PingFang SC, Microsoft YaHei）。

6. 开发任务清单 
第一阶段：环境搭建 "使用 uv 初始化 FastAPI 项目结构(已完成)，配置静态资源文件夹。编写一个简单的 Tailwind HTML 模板，要求左侧侧边栏导航，主题色为 #dc3023，并在内网环境下能正常显示 UI。"

第二阶段：LDAP 模块 "编写 LDAP 逻辑类，实现基于 python-ldap 的用户列表获取和用户创建功能。注意处理 LDAP 的错误捕获。"

第三阶段：Slurm 交互 "封装 Slurm 命令行解析工具。通过 Python 解析 sinfo --json 或命令字符串，提取分区状态和作业队列，并映射到前端表格。"

第四阶段：交互优化 "为删除和取消作业操作添加二次确认弹窗（Modal），使用 Tailwind 编写美化后的通知提醒（Toast）。"

7. 预想页面结构
/dashboard: 概览（用户总数、活动作业数、空闲节点数）。

/users: LDAP 用户管理表格。

/partitions: Slurm 分区与节点状态。

/jobs: 作业队列实时监控。
