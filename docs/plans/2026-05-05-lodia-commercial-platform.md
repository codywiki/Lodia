# Lodia 顶级商业化平台实施计划

版本：v1.0  
日期：2026-05-05  
目标：把 Lodia 从 PRD 和官网推进为可持续迭代的商业化数据资产平台代码库。

## 1. 建设原则

Lodia 的完整产品不是一个“上传文件后自动打标签”的工具，而是一条可信的数据资产生产线。所有工程实现必须同时满足五个条件：

- 隐私优先：原始数据只进入隔离区，脱敏后才进入标注、审核和导出。
- 自动优先：自动解析、脱敏、去重、聚类、标注和质量门禁覆盖绝大多数流程。
- 人审兜底：人工只介入抽检、风险复核、训练集升级和 gold eval 二审。
- 出厂可证：每个可商用数据集必须有 Data Contract、Manifest、Quality Report 和 UsageEvent。
- 分账公平：平台只保留总收益扣除总成本后的 20%，其余 80% 按可审计贡献权重分给贡献者。

## 2. 完整产品模块

| 模块 | 产品能力 | 技术服务 |
| --- | --- | --- |
| Lodia Inbox | 邮件、上传、插件、API、MCP 多入口收集 | Ingestion Gateway、Upload API、Webhook |
| Raw Quarantine | 原始数据隔离、加密、TTL、访问审计 | Quarantine Store、KMS、TTL Worker |
| Lodia Pipeline | 解析、抽取、脱敏、去重、标注、DRL | Parser、Redaction、Annotation、Quality Gate |
| Lodia Studio | 审核、抽检、专家精标、争议处理 | Review Service、Expert Review、Sampling |
| Lodia Gold | 商用数据集、训练集、评测集、行业包 | Dataset Builder、Contract Checker、Export |
| Lodia Eval | 模型、Agent、企业流程评测 | Eval Harness、Rubric、Holdout Registry |
| Lodia Ledger | UsageEvent、PayoutEvent、对账、收益 | Append-only Ledger、Reconciliation、Payout |
| Lodia Trust | 合规、安全、风控、数据主体权利 | Policy Engine、Audit、DSR、Risk Control |
| Enterprise | 企业租户、SSO、RBAC、私有词库、私有化 | Tenant、RBAC、SSO、Private Deployment |

## 3. 分阶段实施

### Phase 0：工程主干

目标：建立可以长期扩展的 monorepo 和核心算法服务。

交付：

- `apps/api`：FastAPI 后端骨架。
- `apps/web`：React 控制台骨架。
- `packages/schemas`：Case、Dataset、Ledger schema。
- `docker-compose.yml`：本地开发环境。
- 核心处理模块：redaction、dedup、annotation、quality gate、payout。
- 单元测试覆盖隐私脱敏和收益分配。

验收：

- `python -m unittest discover apps/api/tests` 通过。
- 后端可通过 `/api/health` 进行健康检查。
- 前端能展示贡献者、审核、数据集和账本概览。

### Phase 1：可信闭环

目标：跑通从提交到收益的可演示闭环。

交付：

- 用户开发态登录。
- 文本/文件提交。
- Raw Quarantine 本地对象存储。
- 自动脱敏与残留扫描。
- Case Normalizer。
- canonical hash、simhash 去重。
- 自动标注和质量分。
- DRL0-DRL2 自动分级。
- 审核台升级 DRL3。
- Dataset Builder 生成 JSONL/CSV/Markdown。
- UsageEvent 和 PayoutEvent。

验收：

- 一个样例 Case 可从提交进入 DRL3。
- 一个 DRL3 Case 可被打包成数据集。
- 数据集导出生成 Manifest 和 Quality Report。
- 贡献者账本生成 pending payout。

### Phase 2：商业试点

目标：支持首批企业客户和高质量种子贡献者。

交付：

- 多租户、RBAC、企业空间。
- 企业私有敏感词库。
- 邮件入口和 API 入口。
- 审核抽样策略。
- Source Trust Score。
- Domestic Model Gateway。
- 低置信自动标注模型增强。
- 数据合同模板。
- 企业订单和交付记录。
- 账期对账和冻结机制。

验收：

- 支持 3-5 家企业客户真实数据试点。
- 普通商用数据集最低 DRL3。
- 可训练数据集最低 DRL3/DRL4。
- UsageEvent 与订单、导出、PayoutEvent 可对账。

### Phase 3：规模化商业化

目标：形成可持续供给、销售和分账体系。

交付：

- 浏览器插件。
- 飞书、钉钉、企业微信入口。
- OCR、ASR、PDF、图片、代码日志处理。
- Cluster Service 和语义聚类。
- 数据集市场。
- 数据集订阅和 API 调用。
- 贡献者等级体系。
- 争议仲裁流程。
- 私有化部署包。
- 安全与合规控制台。

验收：

- 支持百万级 Case 存储和批量导出。
- 支持每月自动生成多个行业数据包。
- 支持客户按 Data Contract 验收。
- 支持可审计收益分账。

### Phase 4：Gold Eval 与数据标准

目标：形成 Lodia 的高端数据壁垒。

交付：

- DRL4 专家验证流程。
- DRL5 gold eval 双人审核和争议仲裁。
- Rubric Builder。
- Holdout isolation。
- 训练/eval overlap 检查。
- 质量回归评测。
- 行业 benchmark。

验收：

- 每个 gold eval 样本都有答案依据、评分标准、双审记录和版本。
- 数据集质量可被回归评测持续监控。

## 4. 自动化标注策略

自动化标注不直接等于商用准入。系统采用四层自动化：

1. 规则层：确定性标签、隐私识别、密钥识别、结构抽取。
2. 统计层：重复度、新颖度、质量分、来源可信度。
3. 模型层：领域分类、任务类型、答案质量、可复用用途。
4. 审核路由层：低风险抽检，高风险复核，高价值专家审。

自动 worker 最高只能把 Case 提升到 DRL2。DRL3 及以上必须来自人审、专家审、可验证证据或抽检策略明确放行。

## 5. 收益分配规则

平台收益计算：

```text
net_margin = gross_revenue - direct_costs
platform_share = net_margin * 20%
contributor_pool = net_margin * 80%
```

贡献者权重：

```text
case_weight =
quality_score
* novelty_score
* source_trust_score
* license_weight
* usage_weight
* duplicate_penalty
```

分配原则：

- 成本先扣除，避免平台用亏损收入分账。
- 平台只拿净收益的 20%。
- 贡献者池按 Case 权重分配。
- exact duplicate 不重复奖励。
- cluster 内补充上下文、真实执行结果、人工反馈可获得增量权重。
- 高收益 Case 延迟结算，等待风控和争议期。
- UsageEvent、PayoutEvent append-only，不覆盖历史。

## 6. 安全与合规基线

- 原始数据不进入普通日志、搜索索引、向量库和审核 UI。
- 所有原始对象必须有 TTL 和删除证明。
- 所有读取原始数据的服务必须使用独立最小权限 role。
- 所有 API 默认鉴权，敏感 API 增加审计和限流。
- 数据集导出前必须通过 Data Contract Check。
- 中国区默认阻断数据出境。
- 不使用境外模型或境外 SaaS 处理中国区用户数据。
- 不宣传“完全匿名化”或“零隐私风险”。

## 7. 本轮代码主干交付

本轮代码不会假装已经完成全部商业系统，而是建立可持续扩展的工程主干：

- 后端核心服务模块。
- 前端控制台雏形。
- 公平分账公式落地。
- 隐私脱敏和残留扫描落地。
- 质量门禁和 DRL 规则落地。
- 本地测试入口。
- GitHub 可继续协作开发。
