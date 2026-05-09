# Lodia 垂直聚焦迭代 PRD：LLM 长程任务高质量数据

版本：v0.5  
日期：2026-05-07  
状态：当前产品聚焦版  
产品焦点：`llm_long_horizon_task`

## 1. 聚焦结论

Lodia 当前阶段只建设一个数据产品：**LLM 长程任务高质量数据**。

暂不建设：

- 泛图片、音频、视频数据
- 普通聊天、闲聊、情绪陪伴数据
- 单轮问答和短 prompt 数据
- 通用行业文本、知识库切片和新闻语料
- 未包含执行过程、验收结果或人类反馈的 AI 回答集合

附件、截图、PDF、音视频只作为某条 LLM 长程任务 Case 的证据补充，不作为独立数据资产销售。

## 2. 数据定义

一条合格的 LLM 长程任务 Case，必须能回答：

- 任务目标是什么
- 上下文和约束是什么
- 用户如何与 LLM/Agent 互动
- 是否发生工具调用、代码执行、浏览器操作、文件处理或多步推理
- 中间结果、报错、失败路径或关键观察是什么
- 是否经过追问、修正、重试或二次审核
- 最终验收标准是什么，结果是否通过
- 这条 Case 能沉淀出什么可复用规则、SOP、评测样本或训练信号

## 3. 目标用户

贡献者侧：

- 高频使用 ChatGPT、Kimi、Claude、Gemini、DeepSeek 等模型完成复杂工作的人
- Codex、Cursor、Claude Code、AutoGLM、Manus、各类 Agent 工具的重度用户
- 开发者、产品经理、运营、咨询顾问、客服质检、销售解决方案、企业 AI 推进负责人

企业侧：

- 需要训练 Agent 长程任务能力的模型团队
- 需要评测工具调用、任务执行、纠错和验收能力的 AI 产品团队
- 希望沉淀内部 AI 使用经验库、私有评测集和 SOP 的企业

## 4. 核心价值

对贡献者：

- 不再只贡献“聊天碎片”，而是贡献真实任务经验
- 只有高质量、可复用、可验证的长程任务 Case 才可能被采纳
- 被纳入训练集、评测集、企业交付或 gold eval 后持续进入收益账本

对企业：

- 获得真实世界的长程任务样本，而不是模板化指令
- 每条样本带有脱敏、授权、DRL、审核、质量报告和使用边界
- 可用于训练任务规划、工具调用、错误恢复、验收判断和多轮协作能力

## 5. 质量门禁

系统新增 `llm_long_horizon_gate`，自动检查九类证据：

- `llm_or_agent_context`
- `task_objective`
- `constraints`
- `execution_trace`
- `intermediate_evidence`
- `iteration`
- `acceptance`
- `reusable_rule`
- `multi_turn_or_trace_shape`

自动评分输出：

- `long_horizon_score`
- `long_horizon_tier`
- `long_horizon_signals`
- `long_horizon_missing`
- `long_horizon_refined_json`
- `long_horizon_refined_by`
- `long_horizon_refined_at`

门禁策略：

- 低于阈值的 Case 可进入个人库或候选池，但不能进入商用训练/评测数据集
- 自动标注只能到候选层，DRL3 仍需要人工审核
- Reviewer 可在字段级精标工作台补齐目标、上下文、约束、步骤、工具结果、失败修正、验收和可复用规则；保存前再次脱敏，精标字段优先进入出厂 JSONL
- 精标不能凭空制造长程数据：源文本过短或核心字段缺失时，`llm_long_horizon_gate` 仍保持失败
- DRL4/DRL5 需要专家验证、gold review、holdout 和数据集 overlap 检查

## 6. 产品迭代范围

本轮已调整：

- 官网定位改为 LLM 长程任务数据
- 控制台默认样例改为 Codex Agent 长程任务复盘
- 后端新增长程任务证据评分与质量门禁
- 数据集 JSONL 新增 `long_horizon_task` 结构化字段，包含 objective、context、constraints、steps、tool_results、failures、corrections、acceptance、reusable_rules
- Data Contract 和 Quality Report 增加长程任务 schema、平均长程任务分和缺失证据统计
- 生产准入的必需 provider 收束为 LLM、对象存储、支付、发票
- OCR/ASR 从 P0 必需项降级为可选任务证据提取
- 附件入口在产品表达上改为任务证据附件
- Reviewer 字段级精标工作台：后端支持读取/保存长程任务精标字段，前端支持九字段编辑、字段覆盖率、缺失字段和源文本证据查看

本轮已补齐：

- 企业样例包按任务类型拆分：代码修复、Agent 工具调用、企业流程执行、模型评测复盘
- 长程任务采集插件的一键导出 trace 和证据附件归档，进入统一脱敏、标注、质量门禁和可复用数据集链路

P1：

- 长程任务 replay/eval runner
- Buyer usage report 细分训练、评测、人工标注复用场景
- 贡献者收益权重加入长程任务完整度、验收强度和稀缺任务类型
