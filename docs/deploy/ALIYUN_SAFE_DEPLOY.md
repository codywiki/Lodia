# Lodia 阿里云安全旁路部署

这套部署方式默认不修改服务器现有 Nginx、PM2、systemd、数据库或已有 Docker Compose 项目。

## 默认边界

- 独立远端目录：`~/lodia`
- 独立 Compose 项目名：`lodia_prod`
- 默认只监听服务器本机：`127.0.0.1:18080`
- 数据目录：`~/lodia/storage/prod`
- 不使用宿主机 `80/443/8000/5173/5432/6379`

## 部署

```bash
SSH_TARGET=user@your-aliyun-host scripts/deploy-aliyun-safe.sh
```

如果 `18080` 已被占用：

```bash
SSH_TARGET=user@your-aliyun-host LODIA_WEB_PORT=18081 scripts/deploy-aliyun-safe.sh
```

## 本地访问

默认部署不会公网暴露服务。使用 SSH 隧道访问：

```bash
ssh -L 18080:127.0.0.1:18080 user@your-aliyun-host
```

然后打开：

```text
http://127.0.0.1:18080
```

## 公网访问

确认阿里云安全组允许对应高位端口后，才使用公网绑定：

```bash
SSH_TARGET=user@your-aliyun-host LODIA_BIND_HOST=0.0.0.0 LODIA_WEB_PORT=18080 scripts/deploy-aliyun-safe.sh
```

生产域名和 HTTPS 建议后续再接入现有反向代理，接入前先确认现有站点配置和证书自动续期方式。

## 回滚

```bash
ssh user@your-aliyun-host
cd ~/lodia
docker compose -p lodia_prod -f docker-compose.prod.yml down
```

需要清除数据时再执行：

```bash
rm -rf ~/lodia/storage/prod
```
