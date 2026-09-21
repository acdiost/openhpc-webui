# Slurm TRES 核时与卡时计费配置

本文记录 2026-08-14 在 `cluster` 集群完成的 TRES 分钟计费配置、验证结果和运维约束。

## 配置目标

- CPU 按分配的 CPU 数量乘以运行分钟数扣除核时。
- GPU 按显式申请的 GPU 数量乘以运行分钟数扣除卡时。
- 用量永久累计，不使用公平份额，不随时间衰减，也不周期重置。
- 普通作业按提交顺序调度，不使用 backfill 插队。
- 分区不限制默认或最大作业时长。
- 余额不足以覆盖作业申请时，作业保持 `PENDING`，不在运行中透支。

## 生效配置

控制器实际读取的主配置是 `/usr/local/etc/slurm.conf`。服务器还存在独立的
`/etc/slurm/slurm.conf`，但当前 `scontrol show config` 显示生效路径为前者，修改前必须再次确认 `SLURM_CONF`。

主配置关键项：

```ini
# 严格按提交顺序调度，禁止后提交作业通过 backfill 插队
SchedulerType=sched/builtin

# 启用 safe：余额不足以覆盖作业请求时限时保持等待，不在运行中途透支
AccountingStorageEnforce=associations,limits,qos,safe

# 仅使用等待时间确定优先级；禁用公平份额、作业大小、分区和 QOS 权重
PriorityType=priority/multifactor

# 计费用量永久累计，不按时间衰减，也不按周期重置
PriorityDecayHalfLife=0
PriorityUsageResetPeriod=NONE

# 较早提交的作业获得更高 Age 优先级
PriorityWeightAge=100000
PriorityWeightFairshare=0
PriorityWeightJobSize=0
PriorityWeightPartition=0
PriorityWeightQOS=0
PriorityMaxAge=7-0
```

`safe` 已隐含启用 `limits` 和 `associations`；只有确认当前集群要求每个作业显式指定或
通过 Association 默认获得一个有效 QOS 时，才应额外保留 `qos`。如果集群不需要强制
QOS，可将该项简化为 `AccountingStorageEnforce=safe`。保留 `qos` 时，变更前必须确认
所有可提交作业的 Association 都拥有可用 QOS，并且默认 QOS（如已设置）包含在允许列表中：

```bash
sacctmgr show assoc where cluster=cluster \
  format=Cluster,Account,User,Partition,DefaultQOS,QOS -n -P
sacctmgr show qos format=Name -n -P
```

`/usr/local/etc/partition.conf`：

```ini
# 分区不限制作业时长；启用 safe 时，受 TRES 分钟限额的作业应显式声明 --time
PartitionName=GPU Nodes=node[31,32,33,34] Default=YES State=UP MaxTime=INFINITE
```

当前生效值为 `DefaultTime=NONE`、`MaxTime=UNLIMITED`。

## TRES 限额

用户关联使用 `GrpTRESMins`。Association 由集群、账户、用户和可选分区共同确定，
写入前必须限定并回读同一条 Association，避免同时修改其他集群或分区的额度。
分区专属 Association 使用：

```bash
sacctmgr -i modify user name=USER where cluster=CLUSTER account=ACCOUNT partition=PARTITION \
  set GrpTRESMins=cpu=CPU_MINUTES,gres/gpu=GPU_MINUTES
```

无分区的全局 Association 必须显式匹配空分区；在 Shell 中保留双引号，写作
`partition=\"\"`：

```bash
sacctmgr -i modify user name=USER where cluster=CLUSTER account=ACCOUNT partition=\"\" \
  set GrpTRESMins=cpu=CPU_MINUTES,gres/gpu=GPU_MINUTES
```

执行修改前先用同样的筛选条件查询，确认只返回目标记录：

```bash
sacctmgr show assoc where cluster=CLUSTER user=USER account=ACCOUNT partition=PARTITION \
  format=Cluster,Account,User,Partition,GrpTRESMins -n -P
```

- `cpu=60` 表示 60 核分钟，即 1 核时。
- `gres/gpu=60` 表示 60 卡分钟，即 1 卡时。
- `0` 是零额度，不是无限额度。
- 清除限额使用 `cpu=-1` 或 `gres/gpu=-1`，不要在业务拨付接口中把 `None` 当成 0。

GPU 作业必须显式申请 GPU，否则不会产生卡时：

```bash
#SBATCH --gres=gpu:1
#SBATCH --time=02:00:00
```

## 无限时长与 safe

分区不设置最大时长，但 `AccountingStorageEnforce=safe` 必须知道作业的有限时长，才能确认
剩余 TRES 分钟足够覆盖完整运行时间。因此：

- 无 TRES 分钟限额的用户可以提交 `TimeLimit=UNLIMITED` 作业。
- 有核时或卡时限额的用户应显式声明 `--time`。
- 有限余额用户不声明 `--time` 时，作业会以 `AssocGrpCPUMinutesLimit` 等原因为 `PENDING`。
- 分区不会限制用户可申请的有限时长，但 Slurm 会按资源数量乘以申请时长预留额度。

## Web 拨付口径

历史报表继续使用 `sreport`，用于展示所选时间范围内累计分配核时和卡时。

拨付和扣除必须读取 `slurmctld` 当前执行的关联有效用量：

```bash
scontrol show assoc_mgr flags=assoc users=USER
```

输出中的 `GrpTRESMins=cpu=LIMIT(USED),gres/gpu=LIMIT(USED)`，括号内 `USED` 是拨付计算使用的
有效分钟数。不能用从 1970 年开始的 `sreport` 累计值替代该值，否则切换 `NoDecay` 的存量用户
会得到错误余额。

调整规则：

```text
正数拨付：新限额 = max(当前限额, Slurm 有效已用量) + 拨付分钟
负数扣除：新限额 = max(Slurm 有效已用量, 当前限额 - 扣除分钟)
```

无限额度不能直接执行负数扣除。写入使用参数数组、进程内锁，并在 `sacctmgr` 写入后回读验证。

## 生产验证

测试用户：`dawn`。测试前后限额均恢复为：

```text
cpu=8488,gres/gpu=0
```

测试结果：

| 作业 | 请求 | 结果 |
| --- | --- | --- |
| `34038` | `gres/gpu=1`，1 分钟 | `PENDING (AssocGrpGRESMinutes)` |
| `34039` | `cpu=1`，CPU 限额临时设为已用量 | `PENDING (AssocGrpCPUMinutesLimit)` |
| `34040` | `cpu=1`，未指定 `--time` | `PENDING (AssocGrpCPUMinutesLimit)` |

三个测试作业均已取消，没有运行或消耗资源。

## 检查命令

```bash
systemctl status slurmctld
scontrol show config
scontrol show partition GPU
scontrol show assoc_mgr flags=assoc users=dawn
sacctmgr show assoc where cluster=cluster user=dawn \
  format=Cluster,Account,User,Partition,DefaultQOS,QOS,GrpTRESMins -n -P
sacctmgr show qos format=Name -n -P
squeue -u dawn -o "%.18i %.2t %R"
```

重点确认：

```text
AccountingStorageEnforce = associations,limits,qos,safe
PriorityDecayHalfLife    = 00:00:00
PriorityUsageResetPeriod = NONE
SchedulerType            = sched/builtin
```

## 备份与回滚

初始备份：

```text
/usr/local/etc/slurm.conf.backup20260814_162256_before_billing
/usr/local/etc/partition.conf.backup20260814_162256_before_billing
```

故障现场与阶段备份：

```text
/usr/local/etc/slurm.conf.backup20260814_173200_failed_restart
/usr/local/etc/partition.conf.backup20260814_173200_overwritten
/usr/local/etc/partition.conf.backup20260814_before_unlimited
```

回滚前先备份当前文件，然后恢复目标版本并重启：

```bash
cp -a BACKUP_FILE /usr/local/etc/slurm.conf
chown slurm:slurm /usr/local/etc/slurm.conf
systemctl restart slurmctld
```

分区配置同理恢复到 `/usr/local/etc/partition.conf`。重启后必须检查服务、分区、节点、队列和日志。
