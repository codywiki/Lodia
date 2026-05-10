# Lodia Go 生产核心

本阶段将 Lodia 的生产主干切换为 `Go + MySQL + Redis + OSS`。当前产品焦点继续收束为 `llm_long_horizon_task`：只建设 LLM 长程任务高质量数据，图片、PDF、音频、视频等入口暂时作为任务证据附件，不作为独立数据产品。

## 核心栈

- API：Go 标准库 `net/http`，单二进制部署，默认监听 `:8080`。
- Worker：Go Worker，Redis 负责 job id 分发，MySQL `jobs` 表作为状态源和重试账本。
- 数据库：MySQL 8.x，保存提交、Case、审核、资产、数据集、artifact 索引和审计日志。
- 缓存/队列：Redis 7，当前用于异步 ingestion 队列，后续可拆分 annotation、export、maintenance 等队列。
- 对象存储：独立 OSS；开发环境可用本地对象目录，生产推荐阿里云 OSS 私有 bucket。
- 前端：React/Vite 控制台，生产由 Nginx 静态服务并代理 `/api/` 到 Go API。

## 工程主干

当前可发布主干是 [`apps/api-go`](../apps/api-go)。新后端能力统一进入 Go API，CI 以 Go API、Web 构建、Compose 配置和 production smoke 为准：

```bash
make ci
docker compose up -d --build mysql redis api worker
make production-smoke
docker compose down
```

## 已落地主链路

- `/api/pipeline/preview`：自动脱敏、长程任务预标注、质量门禁预览。
- `/api/submissions/text`：文本 Case 入库，raw 写入对象存储隔离区，MySQL 记录提交状态，Redis 投递异步处理任务。
- Go Worker：读取 raw 对象，执行自动脱敏、canonical hash 去重、长程任务结构化、DRL 分级和 Case 入库。
- Raw 最小化：默认 `LODIA_PURGE_RAW_AFTER_PROCESSING=true`，处理成功后删除 raw 对象，只保留脱敏正文、结构化标签、质量门禁和审计摘要。
- `/api/submissions/{id}`：提交状态轮询，返回处理状态、Worker 状态和生成 Case。
- `/api/review/queue`、`claim/release/approve/reject`：审核队列、认领、释放、通过和驳回。
- `/api/review/{case_id}/long-horizon`：Reviewer 字段级精标工作台，覆盖目标、上下文、约束、步骤、工具结果、失败、修正、验收和可复用规则。
- `/api/assets`：文件进入对象存储，文本类附件可抽取为长程任务 Case；非文本多模态资产保留为待接 OCR/ASR/文档解析的证据资产。
- `/api/datasets`：从商用就绪 Case 生成 JSONL、Manifest、Quality Report 和 Data Contract，并写入对象存储。
- `/api/admin/datasets/{id}/evaluate`：执行数据集质量评估，检查 artifact 完整性、Case 去重、train/eval holdout 重叠、DRL、商用门禁、脱敏、内容安全和长程任务必填字段，输出 metrics、findings 和 readiness score。
- `/api/admin/metrics`、`/api/admin/observability`、`/api/audit/logs`：基础可观测和审计查看。
- Domestic Model Gateway：`/api/pipeline/preview` 和提交处理统一经过脱敏前置的模型网关；开发可用本地规则，生产可切换国内 HTTP 模型服务，并将供应商调用写入审计表。
- `/api/admin/model-gateway/health`、`/api/admin/vendor-processing-records`：查看模型网关健康、区域、模式、调用计数和最近供应商处理记录；生产准入会阻断未配置好的模型网关。

## MySQL 设计

核心表：

- `submissions`：提交状态、raw 对象 URI、raw hash、授权用途、raw 过期/删除时间和重复 Case 指针。
- `cases`：脱敏正文、canonical hash、DRL、commercial_ready、自动标注 JSON、质量门禁 JSON、长程任务精标 JSON 和审核认领状态。
- `jobs`：Redis job id 的持久状态源，记录 attempts、max_attempts、error、locked_by 和重试状态。
- `assets`：文件/多模态证据资产的对象 URI、媒体类型、大小、状态和关联 submission；长程任务 trace 导出的证据附件也进入该归档链路。
- `datasets`、`dataset_artifacts`：数据集元数据和对象存储 artifact 索引。
- `reviews`：人工审核、字段精标、专家验证和 gold review 的记录。
- `audit_logs`：关键事件 append-only 审计。
- `schema_migrations`：正式迁移 registry，记录版本、checksum、状态和 applied_at，用于上线准入和 drift 识别。
- `users`、`auth_tokens`：数据库用户、角色、密码哈希、PAT token 哈希、过期和撤销状态，支持环境 token 之外的最小 RBAC 控制面。
- `usage_events`、`payout_events`：数据集出厂/买方使用事件、按 Case/贡献者拆分的分账事件、权重、金额和 pending/batched/settled 状态。
- `payout_batches`、`payout_transfers`：结算批次、供应商打款提交、回执哈希、金额和状态机。
- `enterprise_customers`、`enterprise_contracts`、`enterprise_orders`：企业客户、合同、订单、收入确认和交付授权关联。
- `delivery_grants`、`buyer_usage_reports`：交付 token 哈希、读取次数、过期控制和买方使用回传。
- `trace_exports` API：将长程任务 objective/context/constraints/steps/tool_results/failures/corrections/acceptance/reusable_rules 和证据附件归档到统一 Case 流程。
- `dsr_requests`：数据主体权利请求、履约时间和删除证明计数。
- `provider_configs`、`compliance_tasks`：供应商配置和中国区上线合规证据。
- `disputes`、`review_samples`、`dataset_evaluations`、`reconciliation_reports`：争议冻结、抽检盲审、数据集质量评估和业务对账。
- `invoices`、`sso_providers`、`inboxes`、`inbound_messages`、`webhook_cases`：发票、企业身份、收件箱入口、入站消息和外部系统 Case 接入。
- `content_safety_scans`、`payout_profiles`、`authorization_withdrawals`：内容安全扫描、贡献者收款资料和授权撤回阻断。
- `vendor_processing_records`：国内模型网关和受托处理方调用记录，只保存 provider、区域、模型/prompt 版本、脱敏输入 hash、输出 hash、耗时、token、成本和错误码，不保存原文。
- `records`：保留为低优先级实验对象的兼容层，不承接 P0 生产控制面对象。

索引策略：

- `cases.canonical_hash` 唯一索引用于重复 Case 自动过滤。
- `cases(status, created_at)` 支撑审核队列。
- `cases(drl, commercial_ready, updated_at)` 支撑数据集生成。
- `jobs(queue_name, status, available_at)` 支撑 Worker 状态巡检。
- `audit_logs(entity_type, entity_id, created_at)` 支撑合规追溯。

## OSS 数据分层

- `raw/...`：原始提交隔离区，默认处理完成即删除；如果业务要求保留，也必须设置 TTL 和独立权限。
- `assets/...`：贡献者上传的证据文件，文本可转为 Case，非文本等待专用提取 Worker。
- `datasets/...`：脱敏后的可交付 artifact，包括 JSONL、manifest、quality_report、data_contract。

生产建议使用独立私有 bucket，按前缀设置最小权限 RAM policy。API 进程只允许读写 Lodia 前缀，不允许 bucket 级管理权限。

贡献者上传大文件时走 STS 临时凭证直传：客户端先调用 `POST /api/object-storage/temporary-upload-credentials`，API 使用 RAM 角色生成仅允许写入 `LODIA_OSS_PREFIX/<requested-prefix>/` 的短期凭证。本地对象存储不会发放云凭证，会返回 `server_upload_only`，继续走服务端上传链路。

中国大陆区域的新 OSS 用户如果受到默认公网 endpoint 数据 API 限制，应将 `LODIA_OSS_ENDPOINT` 配置为已绑定 HTTPS 证书的自定义域名，并在 OSS、CORS、前端上传 SDK 中保持同一域名口径。

## 环境变量

```bash
LODIA_ENV=production
LODIA_DEPLOYMENT_PROFILE=china_independent
LODIA_DATA_FOCUS=llm_long_horizon_task
LODIA_HTTP_ADDR=:8080

MYSQL_DSN=lodia:password@tcp(mysql:3306)/lodia?parseTime=true&charset=utf8mb4&loc=UTC
REDIS_URL=redis://redis:6379/0
LODIA_WORKER_QUEUE=ingestion
LODIA_ASYNC_PROCESSING=true

LODIA_OBJECT_STORAGE_BACKEND=oss
LODIA_OSS_ENDPOINT=https://oss-cn-zhangjiakou.aliyuncs.com
LODIA_OSS_BUCKET=lodia-private-bucket
LODIA_OSS_PREFIX=lodia
LODIA_OSS_ACCESS_KEY_ID=...
LODIA_OSS_ACCESS_KEY_SECRET=...
LODIA_OBJECT_STORAGE_STS_ENABLED=true
LODIA_OSS_STS_ROLE_ARN=acs:ram::your-account-id:role/your-upload-role
LODIA_OSS_STS_ENDPOINT_URL=https://sts.aliyuncs.com
LODIA_OSS_STS_SESSION_NAME=lodia-upload
LODIA_OSS_STS_DURATION_SECONDS=900

LODIA_RAW_OBJECT_TTL_HOURS=24
LODIA_PURGE_RAW_AFTER_PROCESSING=true
LODIA_MAX_REQUEST_BODY_BYTES=1048576
LODIA_RATE_LIMIT_ENABLED=true
LODIA_RATE_LIMIT_REQUESTS=600
LODIA_RATE_LIMIT_WINDOW_SECONDS=60
LODIA_TRUST_PROXY_HEADERS=false
LODIA_ACCESS_LOG_ENABLED=true
LODIA_DATASET_MAX_CASES=5000

LODIA_ADMIN_TOKEN=...
LODIA_REVIEWER_TOKEN=...
LODIA_CONTRIBUTOR_TOKEN=...
LODIA_PASSWORD_PEPPER=...

LODIA_MODEL_GATEWAY_MODE=http
LODIA_MODEL_GATEWAY_PROVIDER_TYPE=llm
LODIA_MODEL_GATEWAY_PROVIDER_NAME=domestic_llm
LODIA_MODEL_GATEWAY_REGION=CN
LODIA_MODEL_GATEWAY_ENDPOINT=https://model-gateway.internal.example.com/annotate
LODIA_MODEL_GATEWAY_API_KEY=...
LODIA_MODEL_GATEWAY_MODEL=domestic-long-task-critic
LODIA_MODEL_GATEWAY_PROMPT_VERSION=long_horizon_task.v1
LODIA_MODEL_GATEWAY_TIMEOUT_SECONDS=15
LODIA_MODEL_GATEWAY_MAX_INPUT_CHARS=8000
```

## 1w 日活口径

当前 Go 主干适合 1w 人/日的正式生产架构演进：API 无状态横向扩容，Worker 按队列横向扩容，MySQL 使用 RDS/高配独立实例，Redis 使用持久化实例，OSS 承担文件和数据集 artifact。轻量服务器只建议做内测控制面，不承载正式生产流量。

基础建议：

- API：2-4 台 2C4G 起步，按 p95 延迟和 CPU 扩容。
- Worker：2 台 2C4G 起步，按队列积压扩容。
- MySQL：RDS MySQL 8，至少 4C8G，`innodb_buffer_pool_size` 约为内存 50%-70%。
- Redis：1C2G 起步，开启 AOF；正式生产使用托管高可用实例。
- OSS：私有 bucket，KMS/服务端加密、生命周期规则、访问日志和删除回执。

## 生产护栏

- 每个 HTTP 响应写入 `X-Request-ID`；客户端传入合法 `X-Request-ID` 时透传，否则服务端生成。
- API 输出 JSON 结构化访问日志，包含 request id、方法、路径、状态码、响应字节数、耗时、客户端 IP 和 user agent，不记录 query string、token 或请求体。
- 单节点限流按客户端 IP + 固定窗口执行，返回 `X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`，触发时返回 `429` 和 `Retry-After`。
- `LODIA_TRUST_PROXY_HEADERS=false` 为默认值；只有在可信反向代理后方部署时才应启用 `X-Forwarded-For` / `X-Real-IP`。
- 模型调用必须先完成自动脱敏，HTTP 模式只发送 redacted text 和结构化 workbench；供应商审计记录不落原文，生产准入要求模型网关健康。
- 商用证明要求数据集评估状态为 `completed` 且无 critical finding；评估被阻断时，Proof 会返回 `dataset_evaluation_failed`。
- 运营告警会暴露被阻断的数据集评估，避免 critical finding 被埋在后台记录里。

## 下一步

- 将当前 STS Query API 签名替换为官方 SDK 适配器，并补充真实 OSS 兼容性回归环境。
- 增加 OpenTelemetry trace、Prometheus 指标细分和多实例/Redis 分布式限流。
- 将 migration registry 拆成逐版本 SQL 文件和回滚演练，当前 Go 主干已经具备 registry、checksum、status/plan 和上线准入信号。
- 将多模态证据提取拆成 `extraction` 队列，接入 OCR/ASR/文档解析供应商。
- 将当前 P0 结算/发票/提现控制面接入真实支付、发票和银行/三方打款供应商。
