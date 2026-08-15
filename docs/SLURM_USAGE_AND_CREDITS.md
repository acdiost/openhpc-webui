# Slurm 核时统计与额度拨付

本文档说明 `openhpc_webui` 如何统计 CPU 核时、GPU 卡时，以及如何通过
Slurm Association 的 `GrpTRESMins` 完成额度拨付。终端登录提示脚本见
[`scripts/slurm_usage_login.sh`](../scripts/slurm_usage_login.sh)。

## 概念与单位

- CPU 核时：分配的 CPU 核数乘以运行时间，例如 16 核运行 2 小时为 32 核时。
- GPU 卡时：分配的 GPU 数量乘以运行时间，例如 2 张卡运行 3 小时为 6 卡时。
- `GrpTRESMins`：Slurm Association 上的 TRES 分钟上限。`cpu=600` 表示
  600 CPU 分钟，即 10 核时；`gres/gpu=60` 表示 1 卡时。
- `N`：Slurm 输出中的不限额状态，例如 `cpu=N(120)` 表示 CPU 不限额，
  括号内的 120 是控制器记录的已用分钟数。

核时用量与额度不是同一个概念。报表用量来自指定时间区间内的实际资源消耗；
额度及有效已用量来自 Slurm 控制器当前维护的 Association 状态。

## 用量统计

### 区间汇总

后端分别查询 CPU 和 GPU 的已分配 TRES 秒数：

```bash
sreport -T cpu -t Seconds \
  cluster UserUtilizationByAccount \
  Users=<用户名> Start=<开始时间> End=<结束时间> \
  -n -P format=Login,Used

sreport -T gres/gpu -t Seconds \
  cluster UserUtilizationByAccount \
  Users=<用户名> Start=<开始时间> End=<结束时间> \
  -n -P format=Login,Used
```

同一用户可能在多个账户下产生记录，程序会累计用户名匹配行的 `Used` 字段：

```text
CPU 核时 = CPU Used 秒数之和 / 3600
GPU 卡时 = GPU Used 秒数之和 / 3600
```

程序支持今日、本周、本月和自定义日期。时间区间采用左闭右开形式；自定义结束
日期会加一天，因此选择 `2026-08-01` 至 `2026-08-15` 时包含 8 月 15 日全天。
实现见 `SlurmManager._resolve_report_period()` 和
`SlurmManager._get_user_tres_hours()`。

### 作业明细

作业明细通过以下命令获取：

```bash
sacct -u <用户名> -S <开始时间> -E <结束时间> \
  -o JobID,JobName,State,ElapsedRaw,AllocCPUS,AllocTRES,Partition,Submit,Start,End \
  -X -n --parsable2 --json
```

单个作业按如下公式计算：

```text
核时 = 统计区间内运行秒数 × AllocCPUS / 3600
卡时 = 统计区间内运行秒数 × AllocGPU / 3600
```

跨越统计区间边界的作业会按作业与查询区间的重叠比例截断运行时间。报表总核时和
总卡时优先采用 `sreport` 的区间汇总结果；`sacct` 负责作业明细、状态数量和运行
时长。API 最多返回前 50 条作业明细。

管理员可使用以下接口：

```http
GET /api/slurm/users/<username>/report?range=month
GET /api/slurm/users/<username>/report?range=custom&start=2026-08-01&end=2026-08-15
GET /api/slurm/users/<username>/report.csv?range=month
```

普通用户仪表盘通过 `GET /api/slurm/my/dashboard` 展示本人本月用量。

## 额度读取

读取指定 Association 的绝对额度：

```bash
sacctmgr show assoc where \
  user=<用户名> account=<账户> partition=<分区> \
  format=User,Account,Partition,GrpTRESMins -n -P
```

全局 Association 不传 `partition`。示例输出：

```text
dawn|research|GPU|cpu=600,gres/gpu=60
```

读取控制器正在执行的额度和累计已用量：

```bash
scontrol show assoc_mgr flags=assoc users=<用户名>
```

示例字段：

```text
GrpTRESMins=cpu=600(200),gres/gpu=60(10)
```

它表示 CPU 上限/已用分别为 600/200 分钟，GPU 上限/已用分别为 60/10 分钟。
用户列表优先展示无分区的全局 Association；不存在全局关联时使用查询结果中的
第一个分区关联。

## 额度拨付

用户管理页和集群用户页都调用增减额度接口：

```http
POST /api/slurm/users/<username>/credit
Content-Type: application/json
```

增加额度示例：

```json
{
  "account": "research",
  "partition": "GPU",
  "cpu_hours": 120,
  "gpu_hours": 24,
  "reason": "project",
  "note": "项目追加"
}
```

负数表示扣除：

```json
{
  "account": "research",
  "cpu_hours": -10,
  "reason": "correction"
}
```

拨付过程如下：

1. 将小时乘以 60，并使用四舍五入转换为整数分钟。
2. 未指定账户时，通过 `sacctmgr show user name=<用户名>
   format=User,DefaultAccount -n -P` 查找默认账户。
3. 读取当前 `GrpTRESMins` 上限和 `scontrol assoc_mgr` 中的有效已用量。
4. 正数拨付使用 `新上限 = max(当前上限, 已用量) + 拨付量`。
5. 负数扣除使用 `新上限 = max(已用量, 当前上限 + 扣除量)`，不会低于
   已经消耗的额度；不限额状态不能直接执行负数扣除。
6. 写入 Slurm 后重新读取 Association，只有回读值与期望值一致才返回成功。

最终执行的 Slurm 命令为：

```bash
sacctmgr -i modify user <用户名> \
  where account=<账户> partition=<分区> \
  set GrpTRESMins=cpu=<CPU分钟>,gres/gpu=<GPU分钟> \
  Comment="[2026-08-15 14:30:00] <拨付说明>"

sacctmgr -i modify user dawn \
where account=dawn \
set GrpTRESMins=cpu=3000,gres/gpu=700 \
Comment="[2026-08-15 14:30:00] 项目A 2026年度GPU额度"

sacctmgr show assoc \
  where user=dawn account=dawn \
  format=User,Account,Partition,GrpTRESMins,Comment
```

无分区关联时省略 `partition=<分区>`。拨付代码使用进程内线程锁保护
“读取、计算、写入、验证”，可避免同一 Web 进程内的并发覆盖，但不能替代多个
Web 进程之间的分布式锁。

接口只允许管理员调用；Slurm 名称只接受字母、数字、点、下划线和短横线，最长
64 个字符；单次调整不能超过 1,000,000 小时；用户填写的 Comment 最长 478 个
字符且不能换行。后端会使用当前系统时间生成
`[YYYY-MM-DD HH:MM:SS] 拨付说明`，完整 Comment 不超过 500 个字符，并写入
Slurm Association，在集群用户列表中回读展示；拨付原因、
Comment 和操作人也会输出到应用日志。Association 的 Comment 只保存最后一次
拨付说明，如需完整历史审计，仍应长期保留应用日志或接入独立审计数据库。

项目还保留绝对分钟设置接口：

```http
POST /api/slurm/associations/<account>/<username>/tres-minutes
```

它直接设置非负的 `cpu_minutes`、`gpu_minutes`，不是在旧额度上增减。当前页面使用
的是前述增减小时接口。

## 登录终端自动展示

脚本默认只在交互式终端中运行，显示当前用户、默认 Slurm 账户、本月 CPU/GPU
实际用量、控制器累计已用量、额度上限和剩余量。部署方式、测试命令、超时配置和
用户隐藏方法已写在脚本头部注释中。

推荐部署到所有登录节点：

```bash
sudo install -m 0755 scripts/slurm_usage_login.sh \
  /etc/profile.d/openhpc-slurm-usage.sh
```

部署前应确认登录节点可以执行：

```bash
command -v sreport sacctmgr scontrol
sacctmgr show user name="$USER" format=User,DefaultAccount -n -P
scontrol show assoc_mgr flags=assoc users="$USER"
```

直接测试脚本不会要求交互式 Shell：

```bash
bash scripts/slurm_usage_login.sh
```

脚本使用 `timeout` 时，每条 Slurm 查询默认最多等待 3 秒，可在系统 Profile 中
设置 `SLURM_USAGE_TIMEOUT=5` 调整。用户可创建 `~/.hush_slurm_usage` 隐藏提示。

## 统计口径注意事项

- 登录脚本明确区分“本月消耗”和“累计已用”，两者来自不同 Slurm 命令。
- Web 普通用户仪表盘的“剩余”目前使用总额度减本月 `sreport` 用量；用户管理
  列表的“剩余”使用 Association 上限减控制器累计已用量，因此两个页面可能显示
  不同结果。
- `GrpTRESMins` 是 Association 的组 TRES 分钟限制。实际限制效果还取决于
  SlurmDBD、AccountingStorageEnforce、QOS、父账户和其他 Association 配置。
