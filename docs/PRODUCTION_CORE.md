# Lodia 生产化核心改造

本阶段把 MVP 的本地闭环升级为可生产部署的基础骨架，同时保留 SQLite 本地开发模式。

## 已落地模块

- 数据库：通过 `Database` 抽象支持 SQLite 与 Postgres。
- 对象存储：通过 `ObjectStorage` 抽象支持本地对象目录与 S3-compatible 存储。
- 数据库连接：Postgres 使用连接池，SQLite 开发模式使用短连接 session，避免请求级连接泄漏。
- 版本迁移：`schema_migrations` 记录 P0/P1/P2 迁移版本，启动时补齐新增表、列和索引。
- 队列 Worker：`jobs` 表提供持久化任务队列，生产可接 Redis 做分发，DB 仍保留兜底扫描。
- 幂等处理：`submission_id` 建唯一索引，Worker 重试不会重复生成同一条 Case。
- Auth/RBAC：支持环境 Bootstrap Token、数据库用户、登录 Token、Token 撤销和 `admin`、`reviewer`、`contributor` 三类角色。
- 审核后台：支持审核队列、DRL3 人审、DRL4 专家验证、DRL5 双人 gold review、驳回、审计日志和控制台查看。
- 数据出厂：Dataset Builder 生成 `Data Contract`、Manifest、Quality Report 和 JSONL。
- 多模态资产：支持资产上传、类型识别、危险文件拒收、文本/trace 证据抽取、PDF 文本层提取、图片/PDF/音视频待提取状态和专用 Worker 扩展点。
- 授权快照：每次提交记录用途范围、协议版本和授权状态，撤回后同步阻断 Case、Asset 和未来数据集出厂。
- Model Gateway：记录本地规则标注和多模态提取调用，为后续国内模型、OCR、ASR 和成本核算预留统一审计表。
- Ledger：支持 UsageEvent、PayoutEvent、贡献者账本、payout settle、结算批次、批次 manifest 和幂等结算保护。
- 直传会话：支持 `/api/assets/upload-sessions` 创建对象存储上传会话，上传完成后回调入库，避免大文件穿过 API。
- 租户底座：支持 `tenants`、用户 `tenant_id` 和 token auth context，为企业租户、SSO 和配额隔离预留主键。
- 操作审批：支持审批请求、审批/拒绝、审批审计，供高风险导出和结算接入。
- 生产护栏：API request id、请求体上限、单节点限流、请求签名开关、分页查询、`/api/ready` 就绪检查、`/api/admin/observability` 指标快照和 Prometheus 文本指标。
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
LODIA_MAX_ASSET_BYTES=1048576
LODIA_UPLOAD_SESSION_TTL_SECONDS=900
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

## 多模态与授权门禁

`/api/assets` 接收多模态资产，当前生产底座先完成类型识别、文件风险扫描、raw quarantine、文本/trace 证据抽取、PDF 文本层提取和红线文件拒收。图片、扫描 PDF、音频、视频默认进入 `extraction_pending`，可通过 `/api/assets/{asset_id}/extract` 投递到 `extraction` 队列，后续接入 OCR、ASR、视频关键帧和文档解析 Worker 时不改变资产表和任务队列接口。

大文件走 `/api/assets/upload-sessions` 创建直传会话。S3-compatible/OSS 后端返回 presigned PUT URL；本地对象存储会返回 `direct_upload_supported=false`，只用于内部测试。客户端上传完成后调用 `/api/assets/upload-sessions/{session_id}/complete`，服务端重新读取对象、校验大小、执行风险扫描、绑定授权并进入原有资产处理链路。

`authorization_snapshots` 是数据出厂的一等门禁。每个文本提交和资产上传都会绑定授权快照，Data Contract 会记录 `authorization_snapshot_ids`。授权撤回后，对应 Case 和 Asset 标记为 `withdrawn`，未来数据集生成会跳过这些 Case。

## 租户与供应商审计

`tenants` 和用户 `tenant_id` 已进入认证上下文。当前版本先完成租户元数据和用户归属，后续企业 SSO、租户级配额、按租户数据集出厂和租户级审计可直接复用该主键。

每次本地规则标注或多模态提取都会写入 `model_invocations` 和 `vendor_processing_records`。接入国内 OCR、ASR、文档解析或 LLM 供应商时，必须继续记录 provider、service_type、region、purpose、data_category、input_hash 和状态，便于中国区合规审计和成本核算。

## DRL4/DRL5 与结算

自动化 worker 最高只生成 DRL2 候选。DRL3 必须人审通过；DRL4 必须在 DRL3 基础上完成专家验证且具备 training 授权；DRL5 必须具备 gold_eval 授权、DRL4 基础和两名不同 reviewer 的 gold review。`gold_eval` 数据集强制 `min_drl=DRL5`，Data Contract 会阻断仍存在 `gold_second_review` 等待办项的 Case。

结算链路支持单个 payout settle，也支持先生成 `payout_batches`。批次会写入 manifest，对应 `payout_events` 从 `pending` 进入 `batched`，最终 settle 后统一进入 `settled` 并记录 `settled_at`、外部流水和审计事件。已经 settled 的批次不能重复结算。

## 1w 日活生产口径

本仓库当前提供应用内防线，正式高配生产环境还应在网关和基础设施层叠加：

- API 网关或 Nginx/OpenResty 做公网限流、WAF、TLS、请求大小限制和上传链路隔离。
- 只有在服务只接收可信反向代理流量时，才开启 `LODIA_TRUST_PROXY_HEADERS=true` 读取 `X-Forwarded-For`。
- Postgres 使用托管 RDS 或独立高配实例，按实际并发调高 `LODIA_DB_POOL_MAX_SIZE`，并避免超过数据库 `max_connections`。
- 对象存储使用 OSS/S3-compatible bucket，原始数据和脱敏数据分 bucket 或分前缀隔离，生产建议启用 KMS。
- Worker 按 ingestion、extraction、redaction、annotation、review-export 分队列横向扩容。
- 多模态大文件上传走对象存储直传和完成回调，API 只作为控制面和验收入口。
- 列表接口必须分页，批量导出必须有 `LODIA_DATASET_MAX_CASES` 或后台离线任务上限。

## 下一步

- 将当前内置 versioned migration 升级为 Alembic CLI 工作流。
- 将审计日志、`/api/admin/metrics`、`/api/admin/observability` 和 `/api/admin/metrics/prometheus` 接入 SLS/Prometheus/Grafana。
- 接入真实 OSS RAM 角色或 STS 临时凭据，补删除证明对象。
- 接入真实 PaddleOCR/ASR/文档解析供应商，并将供应商调用落入 `model_invocations`。
- 将 Reviewer Console 拆成独立路由和更完整的人审工作台。
- 在当前租户底座上增加企业 SSO、订单合同、租户配额、真实提现通道和争议仲裁。
