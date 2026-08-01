# Supervisor 部署说明

## 安装 Supervisor

```bash
# CentOS/RHEL
sudo yum install supervisor -y

# Ubuntu/Debian
sudo apt-get install supervisor -y
```

## 配置部署

### 1. 复制配置文件

```bash
# 将配置文件复制到 Supervisor 配置目录
sudo cp /opt/openhpc-web/openhpc-web.ini /etc/supervisord.d/

# 或者对于 Ubuntu/Debian
sudo cp /opt/openhpc-web/openhpc-web.ini /etc/supervisor/conf.d/
```

### 2. 重新加载配置

```bash
# 重新读取配置文件
sudo supervisorctl reread

# 更新进程组
sudo supervisorctl update
```

### 3. 启动应用

```bash
# 启动 openhpc-web 应用
sudo supervisorctl start openhpc-web
```

## 常用管理命令

```bash
# 查看所有进程状态
sudo supervisorctl status

# 启动应用
sudo supervisorctl start openhpc-web

# 停止应用
sudo supervisorctl stop openhpc-web

# 重启应用
sudo supervisorctl restart openhpc-web

# 查看日志
sudo tail -f /var/log/openhpc-web/access.log
sudo tail -f /var/log/openhpc-web/error.log
```

## 开机自启动

```bash
# CentOS/RHEL
sudo systemctl enable supervisord
sudo systemctl start supervisord

# Ubuntu/Debian
sudo systemctl enable supervisor
sudo systemctl start supervisor
```

## 配置说明

配置文件 `openhpc-web.ini` 的主要参数:

- `command`: 启动命令,使用虚拟环境中的 uvicorn
- `directory`: 工作目录
- `user`: 运行用户 (root 用于访问 Slurm 和 LDAP)
- `autostart`: 随 Supervisor 启动自动启动
- `autorestart`: 进程异常退出时自动重启
- `stdout_logfile`: 标准输出日志路径
- `stderr_logfile`: 错误日志路径
- `stdout_logfile_maxbytes`: 日志文件最大 50MB
- `stdout_logfile_backups`: 保留 10 个日志备份

## 故障排查

如果应用无法启动,检查:

1. 日志文件权限:
```bash
sudo ls -la /var/log/openhpc-web/
```

2. 虚拟环境路径是否正确:
```bash
ls -la /opt/openhpc-web/.venv/bin/uvicorn
```

3. 环境变量配置:
```bash
cat /opt/openhpc-web/.env
```

4. Supervisor 日志:
```bash
sudo tail -f /var/log/supervisord.log
```
