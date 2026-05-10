# Lodia

> 把长程 AI 工作转化为可复用数据集和持续增值的数据资产。
> Turn long-horizon AI work into reusable datasets and lasting data assets.

[中文](#中文) | [English](#english)

## 中文

Lodia 是面向 LLM 和 Agent 时代的数据资产平台。

它把高质量 AI 对话、Codex/Cursor 任务、Agent trace、评测复盘、工具执行记录和人工验收反馈，转化为经过授权、结构化、隐私安全、可审计、可商用的训练与评测数据集。

Lodia 的核心判断很简单：真正有价值的 AI 数据，不只是一段漂亮答案，而是一条完整任务路径，包括目标、上下文、约束、执行过程、工具证据、失败、修正、验收标准和可复用判断。

### Lodia 做什么

Lodia 帮助贡献者保存日常 AI 工作里的长期价值，也帮助 AI 公司获得更高质量的长程任务数据。

对贡献者来说，Lodia 是把真实工作经验变成个人数据资产的方式。一条有价值的 Case 被采纳、打包、交付或复用后，可以持续产生收益。

对 AI 团队和企业来说，Lodia 是一条可信的数据生产线，交付的数据可以训练、可以评测、可以审计，也可以公平分账。

### 数据聚焦

Lodia 当前聚焦 LLM 长程任务 Case。

一条合格 Case 应尽量包含以下结构：

- 任务目标
- 上下文
- 约束条件
- 执行步骤
- 工具结果
- 失败路径
- 修正过程
- 验收标准
- 可复用规则

截图、日志、文件、附件和多模态资产会作为任务证据保存，不作为独立的泛媒体数据产品。

### 产品流程

```text
AI 对话 / Agent trace / Codex 任务 / 评测复盘
-> 原始数据隔离
-> 自动脱敏与风险扫描
-> 去重与新颖度判断
-> 长程任务结构化抽取
-> 自动标注与质量评分
-> Reviewer 字段级精标
-> 内容安全与授权门禁
-> 数据集 artifact 与商用证明
-> 企业交付或受控导出
-> 使用事件、分账事件和贡献者收益账本
```

Lodia 的核心不是邮箱、表单或普通标注工具，而是一条可信的数据资产生产线：数据从哪里来，谁拥有它，允许用在哪里，被怎样处理过，为什么有价值，交付给了谁，收益应该如何回到贡献者。

### 当前工程主线

- Go API 服务：`apps/api-go`
- React 控制台与产品官网：`apps/web`
- MySQL 作为主事务数据库
- Redis 作为 Worker 队列
- OSS 兼容对象存储，用于原始证据、附件和数据集 artifact
- HTTP smoke 覆盖：`scripts/go_smoke.sh`
- CI 覆盖：`.github/workflows/app.yml`

新的后端能力统一进入 Go 主线。产品需求、技术架构、数据质量和生产化文档保存在 `docs`。

### 核心能力

- 贡献入口：文本提交、收件箱接入、Webhook Case 和一键 trace 导出。
- Trace 导入：带证据附件的长程任务结构化导入。
- 隐私处理：原始数据隔离、确定性脱敏、残留风险扫描和 raw retention 控制。
- 去重机制：raw hash、canonical hash、重复提交状态和新颖度判断。
- 自动标注：长程任务抽取、质量评分、DRL 门禁、复用意图和置信度。
- Reviewer 工作台：对目标、上下文、约束、步骤、工具结果、失败、修正、验收和可复用规则做字段级精标。
- 数据集打包：生成 JSONL、manifest、quality report 和 data contract。
- 商用证明：artifact hash、Case 就绪状态、内容安全状态、评测状态和授权状态。
- 企业交付：客户、合同、订单、交付授权、门户访问、使用回传、发票、对账和争议处理。
- 贡献者账本：使用事件、分账事件、结算批次、打款记录和贡献者收益看板。
- 治理能力：RBAC、审计日志、迁移 registry、上线准入、运营告警、DSR 请求、内容安全扫描、Domestic Model Gateway 和供应商调用审计。

### 公平分账模型

Lodia 的收益模型围绕贡献者利益设计。

平台只保留直接成本之后净收益的 20%。剩余 80% 进入贡献者池，并通过 Case 级 payout event 分配。分配权重可以参考 Case 质量、任务完整度、证据强度、审核结果和商业使用情况。

账本模型基于事件：

- UsageEvent 记录商业使用。
- PayoutEvent 记录每一次贡献者分配。
- PayoutBatch 汇总结算事件。
- PayoutTransfer 记录外部打款。
- Contributor dashboard 展示 pending、batched、settled 和累计收益。

### 数据质量标准

Lodia 不会把每一段对话都当作有价值数据。

一条 Case 只有通过以下门禁后，才可能进入商用数据集：

- 授权用途范围
- 隐私与脱敏检查
- 重复与新颖度检查
- 长程任务证据评分
- 必要的人审或专家精标
- 内容安全扫描
- 数据集评测
- 商用证明生成
- 授权撤回阻断

这就是 Lodia 从“收集内容”走向可复用、可审计、可商用数据资产的方式。

### 仓库结构

```text
apps/
  api-go/      Go API、Worker store、pipeline、review、dataset、ledger 和 enterprise delivery
  web/         React 产品官网与控制台
docs/          产品、架构、数据质量、合规和生产化文档
scripts/       Smoke tests、生产验证脚本和部署工具
.github/       CI 与仓库自动化
```

### 文档

- 产品需求：`docs/LODIA_PRD.md`
- 技术架构：`docs/LODIA_TECH_ARCHITECTURE.md`
- 生产核心：`docs/PRODUCTION_CORE.md`
- LLM 长程任务数据 PRD：`docs/LLM_LONG_HORIZON_TASK_DATA_PRD.md`
- 可拉取部署：`docs/deploy/PULL_DEPLOYMENT.md`

## English

Lodia is a data asset platform for LLM and Agent-era work.

It turns high-quality AI conversations, Codex/Cursor tasks, Agent traces, evaluation reviews, tool execution records, and human acceptance feedback into authorized, structured, privacy-safe, commercially usable training and evaluation datasets.

The product starts from one focused belief: the most valuable AI data is not a polished answer. It is the full task path: goal, context, constraints, process, tool evidence, failure, correction, acceptance, and reusable judgment.

### What Lodia Does

Lodia helps contributors preserve the value hidden inside daily AI work, and helps AI companies obtain higher-quality long-horizon task data.

For contributors, Lodia is a way to build personal data assets from real work. A useful case can keep generating revenue when it is accepted, packaged, delivered, or reused.

For AI teams and enterprises, Lodia is a governed pipeline for data that can actually be trained on, evaluated against, audited, and paid for fairly.

### Data Focus

Lodia currently focuses on LLM long-horizon task cases.

A qualified case should include as much of the following structure as possible:

- Objective
- Context
- Constraints
- Steps
- Tool results
- Failures
- Corrections
- Acceptance criteria
- Reusable rules

Attachments, screenshots, logs, files, and multimodal assets are treated as supporting evidence for a task case. They are not separate generic media datasets.

### Product Pipeline

```text
AI conversation / Agent trace / Codex task / evaluation review
-> raw data quarantine
-> automatic redaction and risk scan
-> deduplication and novelty check
-> long-horizon task extraction
-> structured annotation and quality scoring
-> reviewer field-level refinement
-> content-safety and authorization gates
-> dataset artifacts and commercial proof
-> enterprise delivery or controlled export
-> usage events, payout events, and contributor revenue ledger
```

The core of Lodia is not a mailbox, a form, or a labeling UI. It is a trusted data production line: where the data came from, who owns it, what it may be used for, how it was processed, why it is valuable, where it was delivered, and who should share the revenue.

### Current Engineering Spine

- Go API service under `apps/api-go`
- React console and product site under `apps/web`
- MySQL as the primary transactional store
- Redis-backed worker queue
- OSS-compatible object storage for raw evidence, assets, and dataset artifacts
- HTTP smoke coverage through `scripts/go_smoke.sh`
- CI coverage through `.github/workflows/app.yml`

The Go mainline owns new backend development. Product documentation and architecture specifications are kept under `docs`.

### Core Capabilities

- Contribution intake: text submissions, inbox ingestion, webhook cases, and one-click trace export.
- Trace export: structured long-horizon task import with evidence attachments.
- Privacy handling: raw data isolation, deterministic redaction, residual risk checks, and raw retention controls.
- Deduplication: raw hash, canonical hash, duplicate submission state, and novelty-aware intake.
- Annotation: long-horizon task extraction, quality score, DRL gate, reuse intent, and confidence.
- Reviewer workbench: field-level refinement for objective, context, constraints, steps, tool results, failures, corrections, acceptance, and reusable rules.
- Dataset packaging: data JSONL, manifest, quality report, and data contract artifacts.
- Commercial proof: artifact hash checks, case readiness, content-safety state, evaluation state, and authorization state.
- Enterprise delivery: customers, contracts, orders, delivery grants, portal access, usage reports, invoices, reconciliation, and disputes.
- Contributor ledger: usage events, payout events, payout batches, payout transfer records, and contributor dashboard.
- Governance: RBAC, audit logs, migration registry, launch readiness checks, operational alerts, DSR requests, content-safety scans, the Domestic Model Gateway, and vendor processing audit records.

### Fair Revenue Model

Lodia is designed around contributor-aligned economics.

The platform keeps 20% of net revenue after direct costs. The remaining 80% goes into the contributor pool and is distributed by case-level payout events. Allocation can consider case quality, task completeness, evidence strength, reviewer outcome, and commercial usage.

The accounting model is event-based:

- UsageEvent records commercial usage.
- PayoutEvent records each contributor allocation.
- PayoutBatch groups payable events.
- PayoutTransfer records external settlement.
- Contributor dashboard shows pending, batched, settled, and total earnings.

### Data Quality Standard

Lodia does not treat every conversation as useful data.

A case becomes commercially useful only when it passes the required gates:

- Authorized use scope
- Privacy and redaction checks
- Duplicate and novelty checks
- Long-horizon task evidence score
- Human review or expert refinement where required
- Content-safety scan
- Dataset evaluation
- Commercial proof generation
- Authorization-withdrawal blocking

This is how Lodia moves from collected content to reusable, auditable, commercially usable data.

### Repository Structure

```text
apps/
  api-go/      Go API, worker-facing store, pipeline, review, dataset, ledger, and enterprise delivery logic
  web/         React product site and console
docs/          Product, architecture, data quality, compliance, and production-readiness documents
scripts/       Smoke tests, production verification helpers, and deployment utilities
.github/       CI and repository automation
```

### Documentation

- Product requirements: `docs/LODIA_PRD.md`
- Technical architecture: `docs/LODIA_TECH_ARCHITECTURE.md`
- Production core: `docs/PRODUCTION_CORE.md`
- LLM long-horizon task data PRD: `docs/LLM_LONG_HORIZON_TASK_DATA_PRD.md`
- Pull-based deployment: `docs/deploy/PULL_DEPLOYMENT.md`

## License

Lodia is released under the GNU Affero General Public License v3.0.

See [LICENSE](./LICENSE).
