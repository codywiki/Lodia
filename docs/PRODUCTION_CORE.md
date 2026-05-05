# Lodia 生产化核心改造

本阶段把 MVP 的本地闭环升级为可生产部署的基础骨架，同时保留 SQLite 本地开发模式。

## 已落地模块

- 数据库：通过 `Database` 抽象支持 SQLite 与 Postgres。
- 对象存储：通过 `ObjectStorage` 抽象支持本地对象目录与 S3-compatible 存储。
- 数据库连接：Postgres 使用连接池，SQLite 开发模式使用短连接 session，避免请求级连接泄漏。
- 版本迁移：`schema_migrations` 记录 P0 迁移版本，启动时补齐新增表、列和索引。
- 队列 Worker：`jobs` 表提供持久化任务队列，生产可接 Redis 做分发，DB 仍保留兜底扫描。
- 幂等处理：`submission_id` 建唯一索引，Worker 重试不会重复生成同一条 Case。
- Auth/RBAC：支持环境 Bootstrap Token、数据库用户、登录 Token、Token 撤销和 `admin`、`reviewer`、`contributor` 三类角色。
- 审核后台：支持审核队列、DRL3 审核通过、驳回、审计日志和控制台查看。
- 数据出厂：Dataset Builder 生成 `Data Contract`、Manifest、Quality Report 和 JSONL。
- Ledger：支持 UsageEvent、PayoutEvent、贡献者账本和 payout settle。
- 操作审批：支持审批请求、审批/拒绝、审批审计，供高风险导出和结算接入。
- 生产护栏：API request id、请求体上限、单节点限流、请求签名开关、分页查询、`/api/ready` 就绪检查。
- 查询性能：Case 增加 `drl`、`quality_score` 查询列和核心索引，数据集生成按 DRL/质量分筛选并限制批量大小。
- Raw Quarantine：原始对象记录过期时间，支持 TTL purge，S3-compatible 存储支持 SSE/KMS 参数。
- 部署：生产 Compose 启动 `postgres`、`redis`、`api`、`worker`、`web` 五类服务。

## 环境变量

```bash
LODIA_ENV=production
POSTGRES_DSN=postgresql://lodia:password@postgres:5432/lodia
REDIS_URL=redis://redis:6379/0
LODIA_QUEUE_BACKEND=redis
LODIA_OBJECT_STORAGE_BACKEND=local
LODIA_OBJECT_STORAGE_DIR=/objects
LODIA_ADMIN_TOKEN=...
LODIA_REVIEWER_TOKEN=...
LODIA_CONTRIBUTOR_TOKEN=...
LODIA_PASSWORD_PEPPER=...
LODIA_RAW_OBJECT_TTL_HOURS=24
LODIA_DB_POOL_MIN_SIZE=1
LODIA_DB_POOL_MAX_SIZE=10
LODIA_MAX_REQUEST_BODY_BYTES=1048576
LODIA_RATE_LIMIT_ENABLED=true
LODIA_RATE_LIMIT_REQUESTS=120
LODIA_RATE_LIMIT_WINDOW_SECONDS=60
LODIA_TRUST_PROXY_HEADERS=false
LODIA_REQUIRE_REQUEST_SIGNATURE=false
LODIA_REQUEST_SIGNATURE_SECRET=...
LODIA_MAX_PAGE_LIMIT=500
LODIA_DATASET_MAX_CASES=5000
```

启用异步处理：

```bash
LODIA_ASYNC_PROCESSING=true
```

启用 S3-compatible 对象存储：

```bash
LODIA_OBJECT_STORAGE_BACKEND=s3
LODIA_S3_BUCKET=...
LODIA_S3_ENDPOINT_URL=...
LODIA_S3_REGION=cn-zhangjiakou
LODIA_S3_PREFIX=lodia
LODIA_S3_SSE_ALGORITHM=aws:kms
LODIA_S3_KMS_KEY_ID=...
```

## 权限规则

- `contributor`：提交和预览 Case。
- `reviewer`：查看 Case、审核 Case、查看数据集。
- `admin`：生成数据集、查看账本、查看审计日志、查看队列。

未配置 token 时：

- development：开放 demo 模式，便于本地开发。
- production：返回 `auth_not_configured`，防止误开公网服务。

## 队列策略

SQLite 使用单进程安全的简单领取逻辑。Postgres 使用 `FOR UPDATE SKIP LOCKED` 原子领取任务，支持多 Worker 横向扩展。生产环境可开启 `LODIA_QUEUE_BACKEND=redis`，Redis 负责 job id 分发，Postgres `jobs` 表仍作为状态源和兜底恢复源。

任务处理以提交记录为幂等边界。即使 Worker 在处理完成和标记完成之间异常退出，重试时也会复用已经生成的 Case，而不是重复入库。

## 1w 日活生产口径

本仓库当前提供应用内防线，正式高配生产环境还应在网关和基础设施层叠加：

- API 网关或 Nginx/OpenResty 做公网限流、WAF、TLS、请求大小限制和上传链路隔离。
- 只有在服务只接收可信反向代理流量时，才开启 `LODIA_TRUST_PROXY_HEADERS=true` 读取 `X-Forwarded-For`。
- Postgres 使用托管 RDS 或独立高配实例，按实际并发调高 `LODIA_DB_POOL_MAX_SIZE`，并避免超过数据库 `max_connections`。
- 对象存储使用 OSS/S3-compatible bucket，原始数据和脱敏数据分 bucket 或分前缀隔离，生产建议启用 KMS。
- Worker 按 ingestion、redaction、annotation、review-export 分队列横向扩容。
- 列表接口必须分页，批量导出必须有 `LODIA_DATASET_MAX_CASES` 或后台离线任务上限。

## 下一步

- 将当前内置 versioned migration 升级为 Alembic CLI 工作流。
- 将审计日志和 `/api/admin/metrics` 接入 SLS/Prometheus/Grafana。
- 接入真实 OSS RAM 角色或 STS 临时凭据，补删除证明对象。
- 将 Reviewer Console 拆成独立路由和更完整的人审工作台。
- 增加企业租户、SSO、订单合同、提现结算和争议仲裁。
