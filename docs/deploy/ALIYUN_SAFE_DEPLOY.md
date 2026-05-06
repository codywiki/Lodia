# Lodia 阿里云轻量应用服务器安全旁路部署

这套部署方式适用于阿里云轻量应用服务器（SWAS）或普通 ECS，默认不修改服务器现有 Nginx、PM2、systemd、数据库或已有 Docker Compose 项目。

## 默认边界

- 独立远端目录：`~/lodia`
- 独立 Compose 项目名：`lodia_prod`
- 默认只监听服务器本机：`127.0.0.1:18080`
- Postgres 数据目录：`~/lodia/storage/prod/postgres`
- Redis 数据目录：`~/lodia/storage/prod/redis`
- 对象存储本地目录：`~/lodia/storage/prod/objects`
- 不使用宿主机 `80/443/8000/5173/5432/6379`
- 自动生成生产 `Admin/Reviewer/Contributor` Token、密码 pepper 和请求签名 secret，并写入远端 `~/lodia/.env.production`
- 默认单个资产上传上限 `1MB`，内部测试可通过 `LODIA_MAX_ASSET_BYTES` 和 `LODIA_MAX_REQUEST_BODY_BYTES` 同步调大

如果服务器上已有项目，先保持默认 `127.0.0.1:18080` 本机监听，通过 SSH 隧道验证；确认无冲突后再接入现有反向代理或开放公网高位端口。

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

生产环境 API 默认启用 Auth/RBAC。打开控制台后，把 `~/lodia/.env.production` 中的 `LODIA_ADMIN_TOKEN` 填到页面右上角 `API Token` 输入框，再执行审核、生成数据集或查看审计日志。

## 公网访问

确认阿里云安全组允许对应高位端口后，才使用公网绑定：

```bash
SSH_TARGET=user@your-aliyun-host LODIA_BIND_HOST=0.0.0.0 LODIA_WEB_PORT=18080 scripts/deploy-aliyun-safe.sh
```

生产域名和 HTTPS 建议后续再接入现有反向代理，接入前先确认现有站点配置和证书自动续期方式。

## OSS / S3 对象存储

默认部署使用独立的本地对象存储目录，避免影响服务器现有服务。接入阿里云 OSS 或其他 S3-compatible 服务时，在 `.env.production` 中设置：

```bash
LODIA_OBJECT_STORAGE_BACKEND=s3
LODIA_S3_BUCKET=your-bucket
LODIA_S3_ENDPOINT_URL=https://oss-cn-zhangjiakou.aliyuncs.com
LODIA_S3_REGION=cn-zhangjiakou
LODIA_S3_PREFIX=lodia
LODIA_S3_SSE_ALGORITHM=aws:kms
LODIA_S3_KMS_KEY_ID=your-kms-key-id
```

AccessKey 不要写进仓库。优先使用服务器环境变量、RAM 角色或最小权限凭据。

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
