# Lodia 阿里云轻量应用服务器安全旁路部署

这套部署方式适用于阿里云轻量应用服务器（SWAS）或普通 ECS，默认不修改服务器现有 Nginx、PM2、systemd、数据库或已有 Docker Compose 项目。

## 默认边界

- 独立远端目录：`~/lodia`
- 独立 Compose 项目名：`lodia_prod`
- 默认只监听服务器本机：`127.0.0.1:18080`
- MySQL 数据目录：`~/lodia/storage/prod/mysql`
- Redis 数据目录：`~/lodia/storage/prod/redis`
- 对象存储本地目录：`~/lodia/storage/prod/objects`
- 不使用宿主机 `80/443/8080/5173/3306/6379`
- 自动生成 MySQL 密码、MySQL root 密码、`Admin/Reviewer/Contributor` Token 和密码 pepper，并写入远端 `~/lodia/.env.production`
- 默认 API 请求体上限 `1MB`，内部测试可通过 `LODIA_MAX_REQUEST_BODY_BYTES` 调大

如果服务器上已有项目，先保持默认 `127.0.0.1:18080` 本机监听，通过 SSH 隧道验证；确认无冲突后再接入现有反向代理或开放公网高位端口。

## 部署

Docker Compose 环境：

```bash
SSH_TARGET=user@your-aliyun-host scripts/deploy-aliyun-safe.sh
```

阿里云轻量服务器如果 `docker` 实际是 Podman 兼容层、没有 `docker compose`，使用 Podman 旁路脚本：

```bash
SSH_OPTS='-i ~/.ssh/your_key -o IdentitiesOnly=yes' \
SSH_TARGET=user@your-aliyun-host \
LODIA_BIND_HOST=0.0.0.0 \
LODIA_WEB_PORT=18080 \
scripts/deploy-aliyun-podman-safe.sh
```

Podman 脚本只管理 `lodia-mysql`、`lodia-redis`、`lodia-api`、`lodia-worker`、`lodia-web` 五个容器和 `~/lodia` 目录；不会停止或修改其他业务容器。脚本会优先复用远端 `~/lodia/.env.production` 里的 MySQL 密码、MySQL root 密码、Admin Token、Reviewer Token、Contributor Token 和密码 pepper，避免重复部署后凭据漂移。中国大陆服务器拉 Docker Hub 可能超时，脚本默认使用 `docker.m.daocloud.io/library/...` 作为基础镜像源，可通过 `GO_IMAGE`、`ALPINE_IMAGE`、`NODE_IMAGE`、`NGINX_IMAGE`、`MYSQL_IMAGE`、`REDIS_IMAGE` 覆盖。

轻量服务器部署默认写入 `LODIA_DEPLOYMENT_PROFILE=internal_test`，用于内部体验和功能验收。内测可在控制台执行“内测初始化”，或调用 `POST /api/admin/internal-test/bootstrap` 一次性建立 mock 供应商和合规任务占位；正式生产迁移时必须改为 `LODIA_DEPLOYMENT_PROFILE=production`，并使用 OSS/KMS、STS、真实供应商和完整合规证据通过 `/api/admin/launch-readiness`。

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

生产环境 API 默认启用 Auth/RBAC。打开控制台后，把 `~/lodia/.env.production` 中的 `LODIA_ADMIN_TOKEN` 填到页面右上角 `API Token` 输入框，再执行审核、生成数据集或查看审计日志。

## 公网访问

确认阿里云安全组允许对应高位端口后，才使用公网绑定：

```bash
SSH_TARGET=user@your-aliyun-host LODIA_BIND_HOST=0.0.0.0 LODIA_WEB_PORT=18080 scripts/deploy-aliyun-safe.sh
```

轻量应用服务器还需要在控制台防火墙中放行 TCP `18080`；如果服务器本机 `curl http://127.0.0.1:18080/api/ready` 正常，但公网 `http://server-ip:18080` 超时，通常就是云侧防火墙未放行。

生产域名和 HTTPS 建议后续再接入现有反向代理，接入前先确认现有站点配置和证书自动续期方式。

## OSS 对象存储

默认部署使用独立的本地对象存储目录，避免影响服务器现有服务。接入阿里云 OSS 时，在 `.env.production` 中设置：

```bash
LODIA_OBJECT_STORAGE_BACKEND=oss
LODIA_OSS_ENDPOINT=https://oss-cn-zhangjiakou.aliyuncs.com
LODIA_OSS_BUCKET=your-private-bucket
LODIA_OSS_PREFIX=lodia
LODIA_OBJECT_STORAGE_STS_ENABLED=true
LODIA_OSS_STS_ROLE_ARN=acs:ram::your-account-id:role/your-upload-role
LODIA_OSS_STS_ENDPOINT_URL=https://sts.cn-zhangjiakou.aliyuncs.com
LODIA_OSS_STS_DURATION_SECONDS=900
```

AccessKey 不要写进仓库。优先使用服务器环境变量、RAM 角色或最小权限凭据；如果需要 OSS 直传临时凭证，使用 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` 注入运行时环境。直传凭证只允许写入指定前缀，默认有效期 900 秒，本地对象存储模式不会发放云凭证。

中国大陆区域的新 OSS 用户如果无法直接使用默认公网 endpoint 上传数据，应为 bucket 绑定自定义域名和 HTTPS 证书，并把 `LODIA_OSS_ENDPOINT` 设置为该自定义域名。

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
