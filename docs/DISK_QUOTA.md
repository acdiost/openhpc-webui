# `/home` 磁盘配额

本项目通过 `NFS_QUOTA_FS` 指定配额文件系统，并使用 `quota`/`setquota` 读取和修改用户配额。XFS 文件系统必须先在挂载参数中启用用户配额，否则页面中的配额操作会明确返回“未配置或未启用”，不会假报成功。

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

用户列表的“更多操作”中选择“修改磁盘配额”即可单独修改配额。接口会在 quota 未启用、用户不存在或 `setquota` 失败时返回错误；创建/编辑用户时的配额设置也不会再忽略失败。
