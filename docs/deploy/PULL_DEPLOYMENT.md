# Lodia 可拉取部署版本

本文档描述服务器如何直接拉取 Lodia 镜像并启动服务。生产部署不在服务器上现场编译代码，服务器只负责拉镜像、读取 `.env.production`、启动 Compose 项目和执行只读部署 smoke。

## 部署模型

生产版本包含两类镜像、三类服务：

- `lodia-api`：Go API 服务。
- `lodia-api`：同一镜像通过 `/app/lodia-worker` 入口运行 Worker。
- `lodia-web`：React 静态前端和 Nginx `/api/` 反向代理。

生产 Compose 文件：

- `docker-compose.prod.yml`：默认拉取镜像运行。
- `docker-compose.build.yml`：仅用于 CI 或内部环境从源码构建镜像。

默认 Compose project name 建议固定为 `lodia`，避免影响服务器原有项目：

```bash
docker compose -p lodia --env-file .env.production -f docker-compose.prod.yml up -d
```

## 首次部署

```bash
mkdir -p /opt/lodia
cd /opt/lodia
git clone https://github.com/codywiki/lodia.git .
cp .env.production.example .env.production
```

编辑 `.env.production`，至少填写：

- `LODIA_API_IMAGE`
- `LODIA_WORKER_IMAGE`
- `LODIA_WEB_IMAGE`
- `MYSQL_PASSWORD`
- `MYSQL_ROOT_PASSWORD`
- `MYSQL_DSN`
- `LODIA_ALLOWED_ORIGINS`
- `LODIA_OSS_ENDPOINT`
- `LODIA_OSS_BUCKET`
- `LODIA_OSS_ACCESS_KEY_ID`
- `LODIA_OSS_ACCESS_KEY_SECRET`
- `LODIA_OSS_STS_ROLE_ARN`
- `LODIA_ADMIN_TOKEN`
- `LODIA_REVIEWER_TOKEN`
- `LODIA_CONTRIBUTOR_TOKEN`
- `LODIA_PASSWORD_PEPPER`

启动：

```bash
bash scripts/deploy-pull.sh .env.production
```

脚本会执行：

```text
docker compose pull
docker compose up -d --remove-orphans
scripts/deploy_smoke.sh
```

`scripts/deploy_smoke.sh` 是只读检查：健康检查、就绪检查、请求 ID、迁移状态和可观测性接口。它不会创建 Case、数据集、订单或结算记录。

## 镜像来源

默认镜像为 GitHub Container Registry：

```env
LODIA_API_IMAGE=ghcr.io/codywiki/lodia-api:latest
LODIA_WORKER_IMAGE=ghcr.io/codywiki/lodia-api:latest
LODIA_WEB_IMAGE=ghcr.io/codywiki/lodia-web:latest
```

中国大陆服务器建议使用阿里云 ACR 镜像，避免服务器依赖 GitHub 网络质量：

```env
LODIA_API_IMAGE=registry.cn-zhangjiakou.aliyuncs.com/<namespace>/lodia-api:latest
LODIA_WORKER_IMAGE=registry.cn-zhangjiakou.aliyuncs.com/<namespace>/lodia-api:latest
LODIA_WEB_IMAGE=registry.cn-zhangjiakou.aliyuncs.com/<namespace>/lodia-web:latest
```

GitHub Actions 支持在配置以下 Secrets 后同步推送 ACR：

- `ALIYUN_REGISTRY`
- `ALIYUN_REGISTRY_NAMESPACE`
- `ALIYUN_REGISTRY_USERNAME`
- `ALIYUN_REGISTRY_PASSWORD`

## 更新部署

```bash
cd /opt/lodia
git pull --ff-only
bash scripts/deploy-pull.sh .env.production
```

如果要固定版本，不使用 `latest`，将 `.env.production` 中的镜像 tag 改为对应 SHA tag：

```env
LODIA_API_IMAGE=ghcr.io/codywiki/lodia-api:sha-<commit-sha>
LODIA_WORKER_IMAGE=ghcr.io/codywiki/lodia-api:sha-<commit-sha>
LODIA_WEB_IMAGE=ghcr.io/codywiki/lodia-web:sha-<commit-sha>
```

## 回滚

将 `.env.production` 中的镜像 tag 改回上一版，然后执行：

```bash
bash scripts/deploy-pull.sh .env.production
```

数据库迁移需要单独评估回滚策略；不要在生产库上直接删除数据或重置卷。

## 与服务器原有服务隔离

默认设置：

- Compose project name：`lodia`
- Web 绑定：`127.0.0.1:18080`
- MySQL/Redis 不对外暴露端口
- 对象存储使用独立 OSS bucket 或独立前缀
- 数据目录位于 `./storage/prod`

如果同一台服务器已有服务，只需要保证 `LODIA_WEB_PORT` 不冲突，并由服务器上的 Nginx/OpenResty/Caddy 将公网域名反代到 `127.0.0.1:18080`。

## 全量业务 Smoke

`scripts/go_smoke.sh` 会创建测试用户、Case、数据集、订单、交付授权和结算记录，只适合临时环境或空白预发环境。

如确认需要在预发环境执行：

```bash
LODIA_DEPLOY_FULL_SMOKE=true bash scripts/deploy-pull.sh .env.production
```

正式生产默认不要开启 `LODIA_DEPLOY_FULL_SMOKE`。
