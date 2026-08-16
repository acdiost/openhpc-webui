# 用户磁盘配额

本项目通过 `NFS_QUOTA_FS` 指定配额文件系统，并使用 `quota`/`setquota` 读取和修改用户配额。XFS 和 ext4 都受支持，但必须先在实际挂载点启用用户配额，否则页面中的配额操作会明确返回“未配置或未启用”，不会假报成功。

`NFS_QUOTA_FS` 必须填写 `findmnt -T <用户家目录>` 返回的实际挂载点。`/home`
只是根文件系统中的普通目录时，应填写 `/`，不能填写 `/home`。

| 文件系统 | 挂载选项 | 初始化方式 | WebUI 读写命令 |
|---|---|---|---|
| XFS | `uquota` 或 `usrquota` | 配额元数据由 XFS 管理，不运行 `quotacheck` | `quota` / `setquota` |
| ext4 | `usrquota` | 维护窗口执行 `quotacheck`，再执行 `quotaon` | `quota` / `setquota` |

## Rocky Linux 9 根 XFS 实操

以下步骤已在 Rocky Linux 9.7、XFS 根文件系统上使用 LDAP 用户 `dawn`
（UID/GID `10001`）验证。

先确认 `/home` 所属的实际文件系统：

```bash
findmnt -T /home -o TARGET,SOURCE,FSTYPE,OPTIONS
```

若 `TARGET` 为 `/` 且选项包含 `noquota`，备份并编辑 `/etc/fstab`，为根 XFS
增加 `uquota`：

```text
/dev/mapper/rlm-root / xfs defaults,uquota 0 0
```

根文件系统在读取 `/etc/fstab` 前已经挂载，还需要为所有内核增加启动参数：

```bash
sudo grubby --update-kernel=ALL --args='rootflags=uquota'
sudo findmnt --verify --verbose
sudo reboot
```

重启后验证 accounting 和 enforcement 均为 `ON`：

```bash
findmnt -T /home -o TARGET,SOURCE,FSTYPE,OPTIONS
sudo xfs_quota -x -c 'state' /
```

为 `dawn` 设置 1 GiB 软、硬限额并查询：

```bash
sudo setquota -u dawn 1048576 1048576 0 0 /
quota -w -v -u --filesystem=/ dawn
sudo xfs_quota -x -c 'report -h -u' /
```

对应的 WebUI 配置为：

```dotenv
NFS_QUOTA_FS=/
```

## Rocky Linux 9 ext4 启用步骤

先安装工具并确认 `/home` 所属的设备、文件系统和实际挂载点：

```bash
sudo dnf install -y quota
findmnt -T /home -o TARGET,SOURCE,FSTYPE,OPTIONS
```

以下命令仅适用于 `FSTYPE` 为 `ext4`。若结果中的 `TARGET` 为 `/home`，说明
`/home` 是独立分区；若 `TARGET` 为 `/`，应按照后面的“根 ext4”说明操作。

### 独立的 `/home` ext4 分区

先备份 `/etc/fstab`，再为 `/home` 的 ext4 挂载项加入 `usrquota`：

```bash
sudo cp -p /etc/fstab /etc/fstab.backup.$(date +%Y%m%d%H%M%S)
```

```text
UUID=<home-uuid> /home ext4 defaults,usrquota 0 2
```

在没有用户会话、NFS 服务或应用进程占用 `/home` 的维护窗口重新挂载，然后创建
并启用用户配额数据：

```bash
sudo systemctl daemon-reload
sudo umount /home
sudo mount /home
sudo quotacheck -cum /home
sudo quotaon -v /home
```

不要强制卸载正在使用的 `/home`。无法安全卸载时，应安排维护窗口重启，并在没有
用户写入的情况下执行 `quotacheck`。

设置 `dawn` 的 1 GiB 软、硬限额并验证：

```bash
sudo setquota -u dawn 1048576 1048576 0 0 /home
quota -w -v -u --filesystem=/home dawn
sudo repquota -u /home
```

WebUI 配置必须填写实际挂载点：

```dotenv
NFS_QUOTA_FS=/home
```

### `/home` 位于根 ext4 分区

若 `findmnt -T /home` 返回的 `TARGET` 是 `/`，应为根 ext4 挂载项加入
`usrquota`：

```text
UUID=<root-uuid> / ext4 defaults,usrquota 0 1
```

根文件系统无法像独立 `/home` 一样在线卸载。应安排维护窗口重启，并在业务写入
停止的情况下执行：

```bash
sudo quotacheck -cum /
sudo quotaon -v /
sudo setquota -u dawn 1048576 1048576 0 0 /
quota -w -v -u --filesystem=/ dawn
```

对应的 WebUI 配置为：

```dotenv
NFS_QUOTA_FS=/
```

此时 ext4 的配额数据属于根文件系统。不要在普通目录 `/home` 中单独创建
`aquota.user`，也不要将 `NFS_QUOTA_FS` 错写成 `/home`。

### ext4 验证和关闭

检查挂载参数、配额状态和用户报告：

```bash
QUOTA_FS=/home  # 根 ext4 时改为 /
findmnt -T /home -o TARGET,SOURCE,FSTYPE,OPTIONS
sudo quotaon -p "$QUOTA_FS"
sudo repquota -u "$QUOTA_FS"
```

清除 `dawn` 的限额时，将软、硬限制都设为 `0`：

```bash
QUOTA_FS=/home  # 根 ext4 时改为 /
sudo setquota -u dawn 0 0 0 0 "$QUOTA_FS"
```

需要完全关闭 ext4 用户配额时，先在维护窗口运行 `quotaoff`，再从 `/etc/fstab`
移除 `usrquota` 并重新挂载或重启。不要在配额启用期间直接删除配额数据文件。

## CentOS/RHEL 7 XFS 启用步骤

以下命令需要 `root` 权限。先确认目标设备和挂载点：

```bash
findmnt -T /home -o TARGET,SOURCE,FSTYPE,OPTIONS
```

编辑 `/etc/fstab`，在 `/home` 的 XFS 挂载参数中加入 `uquota`（`usrquota` 也可）：

```text
UUID=<home-uuid> /home xfs defaults,nofail,uquota 0 2
```

建议先备份：

```bash
cp -p /etc/fstab /etc/fstab.backup.$(date +%Y%m%d%H%M%S)
```

如果 `/home` 没有被使用，可以在维护窗口卸载后重新挂载；生产环境通常应安排重启：

```bash
umount /home
mount /home
# 或维护窗口重启后由 systemd 按 /etc/fstab 挂载
```

不要在有用户会话、NFS 服务或应用进程使用 `/home` 时强制卸载。`mount -o remount,uquota /home` 在部分旧版 XFS/内核组合上不能动态开启 quota；此时必须完整卸载后再挂载或重启。

## 验证

```bash
findmnt -T /home -o TARGET,SOURCE,FSTYPE,OPTIONS
xfs_quota -x -c 'state' /home
quota -u dawn
xfs_quota -x -c 'report -h -u' /home
```

验证成功时，挂载选项不应再包含 `noquota`，并应能看到 `user quota state: ON` 或等效的 XFS quota 状态。

可用测试用户设置限额并读取：

```bash
xfs_quota -x -c 'limit -u bsoft=100g bhard=100g dawn' /home
quota -u dawn
```

清除测试限额（恢复不限制）：

```bash
xfs_quota -x -c 'limit -u bsoft=0 bhard=0 dawn' /home
```

## Web UI 配置

在应用环境中设置：

```dotenv
NFS_QUOTA_FS=/home
```

用户列表的“更多操作”中选择“修改磁盘配额”即可单独修改配额。接口会在 quota 未启用、用户不存在或 `setquota` 失败时返回错误。普通“编辑用户”操作不会读写配额，因此 quota 未启用不会阻断 LDAP 用户资料更新；创建用户时若显式填写配额，仍需先启用 quota。
