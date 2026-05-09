import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BadgeCheck,
  Activity,
  ClipboardCheck,
  Database,
  FileCheck2,
  Layers3,
  LockKeyhole,
  Save,
  Scale,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import "./styles.css";

type PreviewResponse = {
  status: string;
  case: {
    case_id: string;
    redaction: {
      redacted_text: string;
      privacy_risk_score: number;
      passed: boolean;
    };
    annotation: {
      domain: string;
      task_type: string;
      difficulty: string;
      quality_score: number;
      confidence: number;
      reuse_types: string[];
      labels: Record<string, string>;
    };
    quality_gate: {
      drl: string;
      gate_results: Record<string, string>;
      allowed_uses: string[];
      blocked_uses: string[];
      required_actions: string[];
      commercial_ready: boolean;
    };
  };
};

type CaseItem = {
  case_id: string;
  owner_id: string;
  status: string;
  redacted_text: string;
  authorization_snapshot_id?: string | null;
  review_claimed_by?: string | null;
  review_claimed_at?: string | null;
  created_at?: string;
  updated_at?: string;
  annotation: {
    quality_score: number;
    domain: string;
    task_type: string;
    labels?: Record<string, string>;
  };
  quality_gate: {
    drl: string;
    gate_results?: Record<string, string>;
    allowed_uses?: string[];
    blocked_uses?: string[];
    commercial_ready: boolean;
    required_actions: string[];
  };
};

type DatasetResult = {
  id: string;
  name: string;
  status: string;
  case_ids: string[];
  payout?: {
    contributor_pool_cents: number;
    platform_share_cents: number;
    allocations: Array<{ contributor_id: string; case_id: string; amount_cents: number }>;
  };
};

type AuditLog = {
  id: string;
  actor_id: string;
  event_type: string;
  entity_type: string;
  entity_id: string;
  created_at: string;
};

type ReviewQueueItem = CaseItem;

type SubmissionCreateResponse = {
  submission_id: string;
  status?: string;
  case?: CaseItem;
};

type SubmissionStatus = {
  submission_id: string;
  owner_id: string;
  status: string;
  submission: {
    id: string;
    owner_id: string;
    source_type: string;
    status: string;
    allowed_uses: string[];
    raw_expires_at?: string | null;
    raw_deleted_at?: string | null;
    created_at: string;
  };
  case?: CaseItem | null;
  jobs: Array<{
    id: string;
    queue_name: string;
    job_type: string;
    status: string;
    attempts: number;
    error: string;
    created_at: string;
    updated_at: string;
  }>;
};

type LongHorizonTaskFields = Record<string, string[]>;

type LongHorizonWorkbench = {
  case: CaseItem;
  schema: string;
  long_horizon_task: {
    task: LongHorizonTaskFields;
    evidence: {
      missing: string[];
      refined_fields?: string[];
      source_chars: number;
    };
    quality: {
      score: number;
      tier: string;
      gate: string;
      refined: boolean;
      refined_by: string;
      refined_at: string;
    };
  };
  fields: LongHorizonTaskFields;
  field_quality: {
    score: number;
    tier: string;
    passed: boolean;
    filled_fields: string[];
    missing: string[];
    required_missing: string[];
    source_evidence_too_thin: boolean;
  };
  missing: string[];
  required_actions: string[];
  review_claimed_by?: string | null;
};

type MetricsSnapshot = {
  cases: Record<string, number>;
  assets: Record<string, number>;
  jobs: Record<string, number>;
  users: Record<string, number>;
  authorizations: Record<string, number>;
  datasets: number;
  pending_payout_cents: number;
  audit_events: number;
};

type ObservabilitySnapshot = {
  ok: boolean;
  case_drl: Record<string, number>;
  payouts: Record<string, number>;
  payout_batches: Record<string, number>;
  reviews: Record<string, number>;
  model_invocations: Record<string, number>;
  queue_depth: Record<string, number>;
};

type PayoutBatch = {
  id: string;
  status: string;
  payout_count: number;
  total_amount_cents: number;
  settled_at: string | null;
};

type DataContract = {
  id: string;
  dataset_id: string;
  status: string;
  contract: {
    version: string;
    purpose: string;
    min_drl: string;
    case_count: number;
  };
};

type AssetItem = {
  id: string;
  owner_id: string;
  submission_id: string | null;
  authorization_snapshot_id: string | null;
  filename: string;
  media_type: string;
  asset_type: string;
  byte_size: number;
  status: string;
};

type AuthorizationSnapshot = {
  id: string;
  owner_id: string;
  status: string;
  allowed_uses: string[];
  policy_version: string;
  terms_version: string;
};

type ContributorDashboard = {
  contributor_id: string;
  cases: {
    total: number;
    by_status: Record<string, number>;
    by_drl: Record<string, number>;
    recent: CaseItem[];
  };
  assets: {
    total: number;
    by_status: Record<string, number>;
  };
  ledger: {
    pending_cents: number;
    batched_cents: number;
    settled_cents: number;
    total_cents: number;
    payout_count: number;
  };
  source_trust?: SourceTrustProfile;
};

type EnterpriseCustomer = {
  id: string;
  tenant_id: string;
  name: string;
  status: string;
  contact_email_domain: string;
};

type EnterpriseContract = {
  id: string;
  customer_id: string;
  status: string;
  version: string;
  expires_at: string;
};

type EnterpriseOrder = {
  id: string;
  customer_id: string;
  dataset_id: string;
  contract_id: string;
  status: string;
  gross_revenue_cents: number;
  direct_cost_cents: number;
  max_reads: number;
  usage_event_id: string;
  delivery_grant_id: string;
};

type DeliveryGrant = {
  id: string;
  order_id?: string;
  dataset_id: string;
  customer_id: string;
  status: string;
  token_suffix: string;
  delivery_token?: string;
  read_count: number;
  max_reads: number;
  expires_at: string;
};

type PayoutProfile = {
  contributor_id: string;
  status: string;
  country_region: string;
  account_type: string;
  account_ref_suffix: string;
  kyc_status: string;
  tax_status: string;
  risk_status: string;
};

type TenantQuota = {
  tenant_id: string;
  monthly_order_limit: number;
  monthly_delivery_read_limit: number;
};

type Dispute = {
  id: string;
  entity_type: string;
  entity_id: string;
  status: string;
  held_payout_count: number;
};

type SourceTrustProfile = {
  contributor_id: string;
  score: number;
  case_count: number;
  accepted_count: number;
  rejected_count: number;
  duplicate_count: number;
};

type ReviewSample = {
  id: string;
  case_id: string;
  sample_type: string;
  status: string;
  blind: boolean;
  decision: string;
  score: number;
};

type EvalRun = {
  id: string;
  dataset_id: string;
  status: string;
  metrics: { case_count: number; holdout_overlap_count: number; duplicate_count: number };
  findings: Array<{ code: string; severity: string }>;
};

type ReconciliationReport = {
  id: string;
  status: string;
  summary: { anomaly_count: number };
  anomalies: Array<{ code: string }>;
};

type DsrRequest = {
  id: string;
  owner_id: string;
  request_type: string;
  status: string;
  deleted_cases: number;
  deleted_assets: number;
};

type Invoice = {
  id: string;
  order_id: string;
  invoice_no_suffix: string;
  status: string;
  amount_cents: number;
  tax_cents: number;
};

type SsoProviderConfig = {
  id: string;
  tenant_id: string;
  provider_type: string;
  status: string;
  domain: string;
};

type Inbox = {
  id: string;
  owner_id: string;
  address: string;
  status: string;
};

type InboundMessage = {
  id: string;
  inbox_id: string;
  owner_id: string;
  status: string;
  subject: string;
  submission_id: string;
};

type WebhookIngestion = {
  id: string;
  source: string;
  owner_id: string;
  status: string;
};

type ContentSafetyResult = {
  id: string;
  entity_type: string;
  entity_id: string;
  status: string;
  risk_level: string;
  action: string;
  categories: string[];
};

type ComplianceTask = {
  id: string;
  task_type: string;
  status: string;
  title: string;
};

type LaunchReadiness = {
  ready: boolean;
  target_profile?: string;
  blockers: Array<{ code: string; count?: number; items?: Array<string | Record<string, unknown>> }>;
  warnings?: Array<{ code: string; count?: number; items?: Array<string | Record<string, unknown>> }>;
  next_actions?: string[];
  signals?: {
    schema_migrations_ok?: boolean;
    schema_migrations_applied?: number;
    schema_migrations_expected?: number;
  };
};

type MigrationStatus = {
  ok: boolean;
  latest_expected: string;
  latest_applied: string;
  missing_versions: string[];
};

type MigrationPlan = {
  target_version: string;
  current_version: string;
  pending_versions: string[];
  rollback_versions: string[];
  ok: boolean;
};

type TemporaryUploadCredentials = {
  supported: boolean;
  backend: string;
  reason?: string;
  key_prefix: string;
  expires_in_seconds: number;
};

type ProviderConfig = {
  id: string;
  provider_type: string;
  provider_name: string;
  status: string;
  mode: string;
};

type InternalTestBootstrapResult = {
  status: string;
  warning: string;
  internal_test_readiness: LaunchReadiness;
  production_readiness: LaunchReadiness;
  seeded_provider_configs: ProviderConfig[];
  completed_compliance_tasks: ComplianceTask[];
};

type PayoutTransfer = {
  id: string;
  batch_id: string;
  provider_name: string;
  status: string;
  amount_cents: number;
};

type BuyerUsageReport = {
  id: string;
  grant_id: string;
  status: string;
  reported_case_count: number;
};

type OperationalAlerts = {
  ok: boolean;
  alert_count: number;
  critical_count: number;
};

type MaintenanceResult = {
  status: string;
  raw: { purged_count: number };
  upload_sessions: { expired_count: number };
  remaining_critical_count: number;
};

type CommercialProof = {
  dataset_id: string;
  proof_hash: string;
  case_count: number;
  commercial_checks: {
    all_authorizations_active: boolean;
    artifact_hashes_present: boolean;
  };
};

type ContributorOnboarding = {
  contributor_id: string;
  ready: boolean;
  next_actions: string[];
  signals: {
    case_count: number;
    commercial_ready_count: number;
    active_inbox_count: number;
    active_authorization_count: number;
    payout_profile_status: string;
  };
};

type EnterprisePortal = {
  grant: DeliveryGrant;
  dataset: {
    id: string;
    name: string;
    status: string;
    case_count: number;
    quality_score: number;
  };
  order: {
    id: string;
    status: string;
    max_reads: number;
  } | null;
  usage_reports: BuyerUsageReport[];
  available_artifacts: string[];
};

const metrics = [
  { label: "LLM 任务聚焦", value: "100%", icon: LockKeyhole },
  { label: "长程证据门禁", value: "9 项", icon: Sparkles },
  { label: "DRL3+ 精选", value: "18.4%", icon: BadgeCheck },
  { label: "贡献者分成", value: "80%", icon: Scale },
];

const modules = [
  ["Inbox", "LLM 对话与 Agent 任务采集", "active"],
  ["Pipeline", "脱敏、去重、长程任务门禁", "active"],
  ["Studio", "抽检、复核、专家精标", "planned"],
  ["Gold", "长程任务训练集与 gold eval", "planned"],
  ["Ledger", "UsageEvent、PayoutEvent、对账", "active"],
  ["Trust", "中国区合规、安全审计、风控", "active"],
];

const apiUrl = (path: string) => path;

function LandingPage() {
  const [note, setNote] = useState("也可以邮件联系：contact@lodia.cn");

  function submitInterest(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name") || "你");
    setNote(`${name}，已记录意向。正式接入前请通过 contact@lodia.cn 完成确认。`);
    event.currentTarget.reset();
  }

  return (
    <div className="landing-page" id="top">
      <header className="landing-header">
        <nav className="landing-nav" aria-label="主导航">
          <a className="landing-brand" href="/" aria-label="Lodia 首页">
            <span className="landing-brand-mark" aria-hidden="true" />
            <span>Lodia</span>
          </a>
          <div className="landing-nav-links">
            <a href="#earn">个人收益</a>
            <a href="#privacy">隐私保护</a>
            <a href="#enterprise">企业训练数据</a>
          </div>
          <a className="landing-nav-cta" href="/app">进入内测</a>
        </nav>
      </header>

      <main>
        <section className="landing-section landing-hero" aria-labelledby="hero-title">
          <div className="landing-hero-copy">
            <h1 id="hero-title">Lodia</h1>
            <p className="landing-hero-line">把 LLM 长程任务变成可复用、可变现的数据资产。</p>
            <p className="landing-hero-sub">
              转发或上传 ChatGPT、Kimi、Claude、Codex、Cursor 等对话和 Agent 执行记录。Lodia 只聚焦目标清楚、过程完整、结果可验收的长程任务 Case。
            </p>
            <div className="landing-hero-actions">
              <a className="landing-button landing-button-primary" href="/app">进入内测控制台</a>
              <a className="landing-button landing-button-quiet" href="#enterprise">企业合作</a>
            </div>
          </div>

          <figure className="landing-hero-visual" aria-label="Lodia 产品界面预览">
            <img src="/assets/lodia-dashboard.svg" alt="Lodia 控制台展示 LLM 长程任务 Case 的收益、脱敏、审核和数据集出厂状态" />
          </figure>
        </section>

        <section className="landing-consumer-strip" aria-label="个人收益摘要">
          <div>
            <strong>一键收集</strong>
            <span>LLM 对话、Agent trace、任务复盘</span>
          </div>
          <div>
            <strong>自动处理</strong>
            <span>脱敏、去重、长程任务评分</span>
          </div>
          <div>
            <strong>持续收益</strong>
            <span>采纳、评测、训练授权</span>
          </div>
        </section>

        <section className="landing-section landing-steps" id="earn" aria-labelledby="earn-title">
          <div className="landing-section-heading">
            <h2 id="earn-title">一段完整的 AI 任务过程，才是资产。</h2>
            <p>
              当前 Lodia 只收 LLM 长程任务数据：有目标、有约束、有过程、有中间结果、有迭代、有验收。普通闲聊、单轮问答和泛内容暂不进入数据产品。
            </p>
          </div>

          <div className="landing-step-grid">
            <article className="landing-step">
              <span className="landing-step-number">1</span>
              <h3>转发或上传</h3>
              <p>把 ChatGPT、Kimi、Claude、Codex、Cursor 等长程任务记录转发到你的 Lodia Inbox。</p>
            </article>
            <article className="landing-step">
              <span className="landing-step-number">2</span>
              <h3>自动处理</h3>
              <p>系统先隔离原始数据，再完成脱敏、残留扫描、去重、结构化和长程任务证据评分。</p>
            </article>
            <article className="landing-step">
              <span className="landing-step-number">3</span>
              <h3>采纳后收益</h3>
              <p>Case 被纳入长程任务训练集、评测集或企业验收样本后，进入收益账本。</p>
            </article>
          </div>
        </section>

        <section className="landing-section landing-ledger-section" aria-labelledby="ledger-title">
          <div className="landing-ledger-copy">
            <h2 id="ledger-title">收益透明。用途清楚。</h2>
            <p>
              每条长程任务 Case 都绑定 CaseID、授权范围、数据集版本和 UsageEvent。你能看到它是否被收录、被用于评测或训练纳入。
            </p>
            <a className="landing-inline-link" href="#contact">申请成为早期贡献者</a>
          </div>
          <div className="landing-ledger-card" aria-label="收益账本示例">
            <div className="landing-ledger-top">
              <span>本月任务资产</span>
              <strong>¥486.80</strong>
            </div>
            <div className="landing-ledger-row">
              <span>长程任务收录</span>
              <strong>18 条</strong>
            </div>
            <div className="landing-ledger-row">
              <span>评测使用</span>
              <strong>63 次</strong>
            </div>
            <div className="landing-ledger-row">
              <span>训练授权</span>
              <strong>12 次</strong>
            </div>
            <div className="landing-ledger-row landing-muted">
              <span>收益以实际采纳、授权和使用记录为准</span>
            </div>
          </div>
        </section>

        <section className="landing-section landing-privacy" id="privacy" aria-labelledby="privacy-title">
          <div className="landing-privacy-visual" aria-hidden="true">
            <div className="landing-shield">
              <span />
            </div>
          </div>
          <div className="landing-privacy-copy">
            <h2 id="privacy-title">先保护，再变现。</h2>
            <p>
              Lodia 中国区默认独立运营，生产数据、日志、模型调用和备份留存在中国大陆。原始数据进入隔离区，脱敏后才进入标注和审核链路。
            </p>
            <ul className="landing-check-list">
              <li>原始数据 Raw Quarantine 隔离处理</li>
              <li>敏感个人信息和重要数据候选默认不进市场</li>
              <li>授权、撤回、导出和收益记录可审计</li>
            </ul>
          </div>
        </section>

        <section className="landing-section landing-enterprise" id="enterprise" aria-labelledby="enterprise-title">
          <div className="landing-section-heading">
            <h2 id="enterprise-title">企业获得更真实的长程任务训练数据。</h2>
            <p>
              Lodia 帮企业把真实 LLM 任务过程沉淀为私有案例库、评测集和高质量训练数据候选池，暂不扩展到泛图片、音频、视频或闲聊数据。
            </p>
          </div>
          <div className="landing-enterprise-layout">
            <div className="landing-enterprise-panel">
              <h3>长程任务数据出厂</h3>
              <p>每个可交付数据集都经过 DRL 分级、Quality Gate、Data Contract 和 Quality Report。</p>
              <div className="landing-pipeline">
                <span>脱敏</span>
                <span>去重</span>
                <span>审核</span>
                <span>出厂</span>
              </div>
            </div>
            <div className="landing-enterprise-panel landing-dark">
              <h3>企业私有优先</h3>
              <p>企业数据默认不进入公开市场，可配置专属租户、私有敏感词库、审核队列和数据保留周期。</p>
              <a className="landing-button landing-button-light" href="#contact">联系企业合作</a>
            </div>
          </div>
        </section>

        <section className="landing-section landing-contact" id="contact" aria-labelledby="contact-title">
          <div>
            <h2 id="contact-title">准备让长程任务 Case 开始产生价值？</h2>
            <p>早期只接收 LLM 长程任务贡献、企业评测/训练数据合作和私有化部署咨询。</p>
          </div>
          <form className="landing-contact-form" onSubmit={submitInterest}>
            <label>
              <span>称呼</span>
              <input name="name" autoComplete="name" placeholder="你的名字" required />
            </label>
            <label>
              <span>联系方式</span>
              <input name="contact" autoComplete="email" placeholder="邮箱或微信" required />
            </label>
            <label>
              <span>合作类型</span>
              <select name="type">
                <option>个人贡献者</option>
                <option>企业长程任务数据合作</option>
                <option>私有化部署</option>
              </select>
            </label>
            <button className="landing-button landing-button-primary" type="submit">提交意向</button>
            <p className="landing-form-note">{note}</p>
          </form>
        </section>
      </main>

      <footer className="landing-footer">
        <span>Lodia</span>
        <span>LLM Long-Horizon Task Data Platform</span>
        <a href="/app">进入内测控制台</a>
      </footer>
    </div>
  );
}

const LONG_HORIZON_FIELD_CONFIG = [
  { key: "objective", label: "任务目标", hint: "用户真正想完成的工作结果" },
  { key: "context", label: "上下文", hint: "业务背景、输入材料、历史状态" },
  { key: "constraints", label: "约束条件", hint: "权限、依赖、时间、不可触碰边界" },
  { key: "steps", label: "执行步骤", hint: "LLM/Agent 的多步行动或推理路径" },
  { key: "tool_results", label: "工具结果", hint: "命令、浏览器、文件、接口或日志返回" },
  { key: "failures", label: "失败路径", hint: "报错、阻塞、未通过验证的分支" },
  { key: "corrections", label: "修正迭代", hint: "重试、调整、二次审核、补救动作" },
  { key: "acceptance", label: "验收标准", hint: "怎么判断任务完成且可交付" },
  { key: "reusable_rules", label: "可复用规则", hint: "SOP、评测信号、训练可学习模式" },
];

function workbenchFieldsToDraft(fields: LongHorizonTaskFields = {}) {
  return Object.fromEntries(
    LONG_HORIZON_FIELD_CONFIG.map((field) => [field.key, (fields[field.key] || []).join("\n")])
  );
}

function draftToWorkbenchFields(draft: Record<string, string>) {
  return Object.fromEntries(
    LONG_HORIZON_FIELD_CONFIG.map((field) => [
      field.key,
      (draft[field.key] || "")
        .split(/\n+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ])
  );
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json();
  if (!response.ok) {
    const detail = typeof payload?.detail === "string" ? payload.detail : `request_failed_${response.status}`;
    throw new Error(detail);
  }
  return payload as T;
}

function ConsoleApp() {
  const [text, setText] = useState(
    "请复盘一个 Codex Agent 长程任务，客户手机号 13800138000，邮箱 user@example.com。背景：部署内测版本失败；目标：不影响原服务完成修复；约束：只能重启 lodia-* 容器；过程：查看日志、定位 502 和数据库认证失败、修复配置、重跑部署；验收：/api/ready 通过、控制台可打开；输出可复用规则。"
  );
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [caseItem, setCaseItem] = useState<CaseItem | null>(null);
  const [dataset, setDataset] = useState<DatasetResult | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [reviewQueue, setReviewQueue] = useState<ReviewQueueItem[]>([]);
  const [submissionStatus, setSubmissionStatus] = useState<SubmissionStatus | null>(null);
  const [consoleNotice, setConsoleNotice] = useState("CN Independent");
  const [longHorizonWorkbench, setLongHorizonWorkbench] = useState<LongHorizonWorkbench | null>(null);
  const [longHorizonDraft, setLongHorizonDraft] = useState<Record<string, string>>(workbenchFieldsToDraft());
  const [longHorizonNotes, setLongHorizonNotes] = useState("字段级精标与证据补全");
  const [metricsSnapshot, setMetricsSnapshot] = useState<MetricsSnapshot | null>(null);
  const [observabilitySnapshot, setObservabilitySnapshot] = useState<ObservabilitySnapshot | null>(null);
  const [dataContract, setDataContract] = useState<DataContract | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadedAsset, setUploadedAsset] = useState<AssetItem | null>(null);
  const [authorizations, setAuthorizations] = useState<AuthorizationSnapshot[]>([]);
  const [payoutBatch, setPayoutBatch] = useState<PayoutBatch | null>(null);
  const [contributorDashboard, setContributorDashboard] = useState<ContributorDashboard | null>(null);
  const [datasets, setDatasets] = useState<DatasetResult[]>([]);
  const [datasetArtifact, setDatasetArtifact] = useState("");
  const [enterpriseCustomers, setEnterpriseCustomers] = useState<EnterpriseCustomer[]>([]);
  const [enterpriseContract, setEnterpriseContract] = useState<EnterpriseContract | null>(null);
  const [enterpriseOrder, setEnterpriseOrder] = useState<EnterpriseOrder | null>(null);
  const [deliveryGrant, setDeliveryGrant] = useState<DeliveryGrant | null>(null);
  const [tenantQuota, setTenantQuota] = useState<TenantQuota | null>(null);
  const [dispute, setDispute] = useState<Dispute | null>(null);
  const [payoutProfile, setPayoutProfile] = useState<PayoutProfile | null>(null);
  const [sourceTrust, setSourceTrust] = useState<SourceTrustProfile | null>(null);
  const [reviewSamples, setReviewSamples] = useState<ReviewSample[]>([]);
  const [evalRun, setEvalRun] = useState<EvalRun | null>(null);
  const [reconciliation, setReconciliation] = useState<ReconciliationReport | null>(null);
  const [dsrRequest, setDsrRequest] = useState<DsrRequest | null>(null);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [ssoProvider, setSsoProvider] = useState<SsoProviderConfig | null>(null);
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [inboundMessage, setInboundMessage] = useState<InboundMessage | null>(null);
  const [webhookIngestion, setWebhookIngestion] = useState<WebhookIngestion | null>(null);
  const [contentSafety, setContentSafety] = useState<ContentSafetyResult | null>(null);
  const [complianceTask, setComplianceTask] = useState<ComplianceTask | null>(null);
  const [launchReadiness, setLaunchReadiness] = useState<LaunchReadiness | null>(null);
  const [internalTestBootstrap, setInternalTestBootstrap] = useState<InternalTestBootstrapResult | null>(null);
  const [migrationStatus, setMigrationStatus] = useState<MigrationStatus | null>(null);
  const [migrationPlan, setMigrationPlan] = useState<MigrationPlan | null>(null);
  const [providerConfig, setProviderConfig] = useState<ProviderConfig | null>(null);
  const [temporaryCredentials, setTemporaryCredentials] = useState<TemporaryUploadCredentials | null>(null);
  const [payoutTransfer, setPayoutTransfer] = useState<PayoutTransfer | null>(null);
  const [buyerUsageReport, setBuyerUsageReport] = useState<BuyerUsageReport | null>(null);
  const [operationalAlerts, setOperationalAlerts] = useState<OperationalAlerts | null>(null);
  const [maintenanceResult, setMaintenanceResult] = useState<MaintenanceResult | null>(null);
  const [commercialProof, setCommercialProof] = useState<CommercialProof | null>(null);
  const [contributorOnboarding, setContributorOnboarding] = useState<ContributorOnboarding | null>(null);
  const [enterprisePortal, setEnterprisePortal] = useState<EnterprisePortal | null>(null);
  const [apiToken, setApiToken] = useState("");
  const [loginEmail, setLoginEmail] = useState("contributor@lodia.local");
  const [loginPassword, setLoginPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function runPreview() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/pipeline/preview"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          owner_id: "demo_contributor",
          text,
          allowed_uses: ["private_library", "candidate_pool", "commercial_dataset", "training"],
        }),
      });
      setPreview(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function submitCase() {
    setLoading(true);
    try {
      setConsoleNotice("正在提交 Case");
      const response = await fetch(apiUrl("/api/submissions/text"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          owner_id: "demo_contributor",
          text,
          allowed_uses: ["private_library", "candidate_pool", "commercial_dataset", "training"],
        }),
      });
      const payload = await readJson<SubmissionCreateResponse>(response);
      setLongHorizonWorkbench(null);
      setLongHorizonDraft(workbenchFieldsToDraft());
      setDataset(null);
      if (payload.case) {
        setCaseItem(payload.case);
        setSubmissionStatus({
          submission_id: payload.submission_id,
          owner_id: payload.case.owner_id,
          status: "processed",
          submission: {
            id: payload.submission_id,
            owner_id: payload.case.owner_id,
            source_type: "text",
            status: payload.case.status,
            allowed_uses: payload.case.quality_gate.allowed_uses || [],
            created_at: payload.case.created_at || "",
          },
          case: payload.case,
          jobs: [],
        });
        setConsoleNotice("Case 已同步处理完成");
        return;
      }
      setCaseItem(null);
      setSubmissionStatus({
        submission_id: payload.submission_id,
        owner_id: "",
        status: payload.status || "queued",
        submission: {
          id: payload.submission_id,
          owner_id: "",
          source_type: "text",
          status: payload.status || "queued",
          allowed_uses: [],
          created_at: "",
        },
        case: null,
        jobs: [],
      });
      setConsoleNotice("Case 已入队，等待 Worker 处理");
      const latest = await pollSubmissionStatus(payload.submission_id);
      if (latest?.case) {
        setConsoleNotice("异步处理完成，可以打开字段精标");
      } else {
        setConsoleNotice("仍在队列中，可稍后刷新状态");
      }
    } catch (error) {
      setConsoleNotice(error instanceof Error ? error.message : "提交失败");
    } finally {
      setLoading(false);
    }
  }

  async function loadSubmissionStatus(submissionId: string) {
    const response = await fetch(apiUrl(`/api/submissions/${submissionId}`), {
      headers: requestHeaders(apiToken, false),
    });
    const payload = await readJson<SubmissionStatus>(response);
    setSubmissionStatus(payload);
    if (payload.case) {
      setCaseItem(payload.case);
      setLongHorizonWorkbench(null);
      setLongHorizonDraft(workbenchFieldsToDraft());
    }
    return payload;
  }

  async function pollSubmissionStatus(submissionId: string) {
    let latest: SubmissionStatus | null = null;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      latest = await loadSubmissionStatus(submissionId);
      if (latest.case || latest.status === "failed") {
        return latest;
      }
      await sleep(1000);
    }
    return latest;
  }

  async function refreshPendingSubmission() {
    if (!submissionStatus) return;
    setLoading(true);
    try {
      setConsoleNotice("正在刷新提交状态");
      const latest = await loadSubmissionStatus(submissionStatus.submission_id);
      setConsoleNotice(latest.case ? "Case 已处理完成" : `当前状态：${latest.status}`);
    } catch (error) {
      setConsoleNotice(error instanceof Error ? error.message : "刷新失败");
    } finally {
      setLoading(false);
    }
  }

  async function approveCase() {
    if (!caseItem) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/review/${caseItem.case_id}/approve`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ reviewer_id: "reviewer_demo", notes: "Phase 1 demo approval" }),
      });
      const payload = await response.json();
      setCaseItem(payload);
      setLongHorizonWorkbench(null);
    } finally {
      setLoading(false);
    }
  }

  async function expertVerifyCase() {
    if (!caseItem) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/review/${caseItem.case_id}/expert-verify`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          reviewer_id: "expert_demo",
          notes: "Expert verification",
          score: 1,
          rubric: { evidence: "checked", usefulness: "high" },
          evidence: { source: "console" },
        }),
      });
      setCaseItem(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function goldReviewCase() {
    if (!caseItem) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/review/${caseItem.case_id}/gold-review`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          reviewer_id: `gold_${Date.now()}`,
          notes: "Gold review",
          score: 1,
          rubric: { answer_key: "verified", holdout: "isolated" },
          evidence: { source: "console" },
        }),
      });
      setCaseItem(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function buildDataset() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/datasets"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          name: "Demo Commercial Dataset",
          purpose: "commercial_dataset",
          min_drl: "DRL3",
          gross_revenue_cents: 100000,
          direct_cost_cents: 20000,
        }),
      });
      setDataset(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadAuditLogs() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/audit/logs?limit=8"), {
        headers: requestHeaders(apiToken, false),
      });
      const payload = await response.json();
      setAuditLogs(payload.items || []);
    } finally {
      setLoading(false);
    }
  }

  async function login() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/auth/login"), {
        method: "POST",
        headers: requestHeaders("", true),
        body: JSON.stringify({ email: loginEmail, password: loginPassword }),
      });
      const payload = await response.json();
      if (payload.token) {
        setApiToken(payload.token);
      }
    } finally {
      setLoading(false);
    }
  }

  async function loadReviewQueue() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/review/queue?limit=6"), {
        headers: requestHeaders(apiToken, false),
      });
      const payload = await response.json();
      setReviewQueue(payload.items || []);
    } finally {
      setLoading(false);
    }
  }

  async function loadLongHorizonWorkbench(targetCase: CaseItem | null = caseItem) {
    if (!targetCase) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/review/${targetCase.case_id}/long-horizon`), {
        headers: requestHeaders(apiToken, false),
      });
      const payload = await response.json();
      setLongHorizonWorkbench(payload);
      setLongHorizonDraft(workbenchFieldsToDraft(payload.fields || payload.long_horizon_task?.task || {}));
      if (payload.case) {
        setCaseItem(payload.case);
      }
    } finally {
      setLoading(false);
    }
  }

  async function saveLongHorizonWorkbench() {
    if (!caseItem) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/review/${caseItem.case_id}/long-horizon`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          reviewer_id: "reviewer_demo",
          notes: longHorizonNotes,
          fields: draftToWorkbenchFields(longHorizonDraft),
        }),
      });
      const payload = await response.json();
      setLongHorizonWorkbench(payload);
      setLongHorizonDraft(workbenchFieldsToDraft(payload.fields || {}));
      if (payload.case) {
        setCaseItem(payload.case);
      }
      await loadReviewQueue();
    } finally {
      setLoading(false);
    }
  }

  async function claimNextReview() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/review/claim"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({}),
      });
      const payload = await response.json();
      if (payload.case_id) {
        setCaseItem(payload);
        setLongHorizonWorkbench(null);
        setLongHorizonDraft(workbenchFieldsToDraft());
        await loadReviewQueue();
        await loadLongHorizonWorkbench(payload);
      }
    } finally {
      setLoading(false);
    }
  }

  async function releaseCurrentReview() {
    if (!caseItem) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/review/${caseItem.case_id}/release`), {
        method: "POST",
        headers: requestHeaders(apiToken),
      });
      setCaseItem(await response.json());
      await loadReviewQueue();
    } finally {
      setLoading(false);
    }
  }

  async function rejectCurrentCase() {
    if (!caseItem) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/review/${caseItem.case_id}/reject`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ reason: "Reviewer rejected from console" }),
      });
      setCaseItem(await response.json());
      await loadReviewQueue();
    } finally {
      setLoading(false);
    }
  }

  async function loadMetrics() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/metrics"), {
        headers: requestHeaders(apiToken, false),
      });
      setMetricsSnapshot(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadObservability() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/observability"), {
        headers: requestHeaders(apiToken, false),
      });
      setObservabilitySnapshot(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadDataContract() {
    if (!dataset) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/datasets/${dataset.id}/contract`), {
        headers: requestHeaders(apiToken, false),
      });
      setDataContract(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadDatasets() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/datasets?limit=6"), {
        headers: requestHeaders(apiToken, false),
      });
      const payload = await response.json();
      setDatasets(payload.items || []);
    } finally {
      setLoading(false);
    }
  }

  async function loadDatasetArtifact(artifact: "manifest" | "quality_report" | "data_contract" | "data") {
    if (!dataset) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/datasets/${dataset.id}/artifacts/${artifact}`), {
        headers: requestHeaders(apiToken, false),
      });
      setDatasetArtifact(await response.text());
    } finally {
      setLoading(false);
    }
  }

  async function uploadAsset() {
    if (!selectedFile) return;
    setLoading(true);
    try {
      const form = new FormData();
      form.append("file", selectedFile);
      form.append("owner_id", "demo_contributor");
      form.append("allowed_uses", JSON.stringify(["private_library", "candidate_pool", "commercial_dataset", "training"]));
      const response = await fetch(apiUrl("/api/assets"), {
        method: "POST",
        headers: requestHeaders(apiToken, false),
        body: form,
      });
      const payload = await response.json();
      setUploadedAsset(payload.asset || null);
      if (payload.asset?.submission_id) {
        await loadReviewQueue();
      }
    } finally {
      setLoading(false);
    }
  }

  async function requestAssetExtraction() {
    if (!uploadedAsset) return;
    setLoading(true);
    try {
      await fetch(apiUrl(`/api/assets/${uploadedAsset.id}/extract`), {
        method: "POST",
        headers: requestHeaders(apiToken),
      });
      setUploadedAsset({ ...uploadedAsset, status: "extraction_queued" });
    } finally {
      setLoading(false);
    }
  }

  async function createPayoutBatch() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/ledger/payout-batches"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ min_amount_cents: 1, max_events: 1000 }),
      });
      setPayoutBatch(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function settlePayoutBatch() {
    if (!payoutBatch) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/ledger/payout-batches/${payoutBatch.id}/settle`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ external_reference: "console-settlement", notes: "Settled from console" }),
      });
      setPayoutBatch(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadAuthorizations() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/authorizations?limit=6"), {
        headers: requestHeaders(apiToken, false),
      });
      const payload = await response.json();
      setAuthorizations(payload.items || []);
    } finally {
      setLoading(false);
    }
  }

  async function loadContributorDashboard() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/contributor/dashboard"), {
        headers: requestHeaders(apiToken, false),
      });
      setContributorDashboard(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadContributorOnboarding() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/contributor/onboarding"), {
        headers: requestHeaders(apiToken, false),
      });
      setContributorOnboarding(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function createEnterpriseCustomer() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/enterprise/customers"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ name: "Demo Buyer", contact_email: "buyer@example.com" }),
      });
      const customer = await response.json();
      if (customer.id) {
        setEnterpriseCustomers([customer, ...enterpriseCustomers.filter((item) => item.id !== customer.id)]);
      }
    } finally {
      setLoading(false);
    }
  }

  async function loadEnterpriseCustomers() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/enterprise/customers?limit=6"), {
        headers: requestHeaders(apiToken, false),
      });
      const payload = await response.json();
      setEnterpriseCustomers(payload.items || []);
    } finally {
      setLoading(false);
    }
  }

  async function ensureEnterpriseCustomer() {
    let customer = enterpriseCustomers[0];
    if (customer) return customer;
    const created = await fetch(apiUrl("/api/admin/enterprise/customers"), {
      method: "POST",
      headers: requestHeaders(apiToken),
      body: JSON.stringify({ name: "Demo Buyer", contact_email: "buyer@example.com" }),
    });
    customer = await created.json();
    setEnterpriseCustomers([customer]);
    return customer;
  }

  async function createEnterpriseContract() {
    setLoading(true);
    try {
      const customer = await ensureEnterpriseCustomer();
      const response = await fetch(apiUrl("/api/admin/enterprise/contracts"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          customer_id: customer.id,
          terms_version: "demo-contract",
          terms: { allowed_use: "training", resale: false },
        }),
      });
      setEnterpriseContract(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function createEnterpriseOrder() {
    if (!dataset) return;
    setLoading(true);
    try {
      const customer = await ensureEnterpriseCustomer();
      let contract = enterpriseContract;
      if (!contract) {
        const created = await fetch(apiUrl("/api/admin/enterprise/contracts"), {
          method: "POST",
          headers: requestHeaders(apiToken),
          body: JSON.stringify({ customer_id: customer.id, terms_version: "demo-contract" }),
        });
        contract = await created.json();
        setEnterpriseContract(contract);
      }
      if (!contract?.id) return;
      const response = await fetch(apiUrl("/api/admin/enterprise/orders"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          customer_id: customer.id,
          dataset_id: dataset.id,
          contract_id: contract.id,
          gross_revenue_cents: 100000,
          direct_cost_cents: 20000,
          max_reads: 20,
        }),
      });
      setEnterpriseOrder(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function recognizeEnterpriseOrder() {
    if (!enterpriseOrder) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/admin/enterprise/orders/${enterpriseOrder.id}/recognize-usage`), {
        method: "POST",
        headers: requestHeaders(apiToken, false),
      });
      setEnterpriseOrder(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function upsertDemoTenantQuota() {
    setLoading(true);
    try {
      const tenantId = enterpriseCustomers[0]?.tenant_id || "default";
      const response = await fetch(apiUrl(`/api/admin/tenant-quotas/${tenantId}`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ monthly_order_limit: 50, monthly_delivery_read_limit: 500 }),
      });
      setTenantQuota(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function openEnterpriseDispute() {
    if (!enterpriseOrder) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/disputes"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          entity_type: "enterprise_order",
          entity_id: enterpriseOrder.id,
          reason: "Demo quality challenge",
          hold_payouts: true,
        }),
      });
      setDispute(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function refreshCurrentSourceTrust() {
    const contributorId = caseItem?.owner_id || contributorDashboard?.contributor_id || "demo_contributor";
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/admin/source-trust/${contributorId}/refresh`), {
        method: "POST",
        headers: requestHeaders(apiToken, false),
      });
      setSourceTrust(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function scheduleReviewSamples() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/review-samples/schedule"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ sample_type: "random_audit", limit: 5, min_drl: "DRL3", reason: "Console audit" }),
      });
      const payload = await response.json();
      setReviewSamples(payload.items || []);
    } finally {
      setLoading(false);
    }
  }

  async function completeFirstReviewSample() {
    const sample = reviewSamples[0];
    if (!sample) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/review-samples/${sample.id}/complete`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ decision: "passed", score: 0.95, notes: "Console sample accepted" }),
      });
      const completed = await response.json();
      setReviewSamples([completed, ...reviewSamples.slice(1)]);
    } finally {
      setLoading(false);
    }
  }

  async function runCurrentDatasetEvaluation() {
    if (!dataset) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/admin/datasets/${dataset.id}/evaluate`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ eval_type: "quality_regression" }),
      });
      setEvalRun(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function runCurrentReconciliation() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/reconciliation"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ scope_type: enterpriseOrder ? "enterprise_order" : "all", scope_id: enterpriseOrder?.id || "" }),
      });
      setReconciliation(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function createAndFulfillDsr() {
    const ownerId = caseItem?.owner_id || "demo_contributor";
    setLoading(true);
    try {
      const created = await fetch(apiUrl("/api/admin/dsr"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ owner_id: ownerId, request_type: "restrict", reason: "Console DSR drill" }),
      });
      const request = await created.json();
      const fulfilled = await fetch(apiUrl(`/api/admin/dsr/${request.id}/fulfill`), {
        method: "POST",
        headers: requestHeaders(apiToken, false),
      });
      setDsrRequest(await fulfilled.json());
    } finally {
      setLoading(false);
    }
  }

  async function issueAndPayInvoice() {
    if (!enterpriseOrder) return;
    setLoading(true);
    try {
      const created = await fetch(apiUrl("/api/admin/invoices"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          order_id: enterpriseOrder.id,
          invoice_no: `INV-${Date.now()}`,
          amount_cents: enterpriseOrder.gross_revenue_cents,
          tax_cents: Math.round(enterpriseOrder.gross_revenue_cents * 0.06),
        }),
      });
      const createdInvoice = await created.json();
      const paid = await fetch(apiUrl(`/api/admin/invoices/${createdInvoice.id}/paid`), {
        method: "POST",
        headers: requestHeaders(apiToken, false),
      });
      setInvoice(await paid.json());
    } finally {
      setLoading(false);
    }
  }

  async function upsertDemoSsoProvider() {
    setLoading(true);
    try {
      const tenantId = enterpriseCustomers[0]?.tenant_id || "default";
      const response = await fetch(apiUrl("/api/admin/sso-providers"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          tenant_id: tenantId,
          provider_type: "oidc",
          issuer: "https://sso.example.com",
          domain: "example.com",
          metadata: { client_id_ref: "hash-only" },
          status: "testing",
        }),
      });
      setSsoProvider(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function createInboxAndReceiveCase() {
    setLoading(true);
    try {
      const created = await fetch(apiUrl("/api/admin/inboxes"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          owner_id: caseItem?.owner_id || "demo_contributor",
          allowed_uses: ["private_library", "candidate_pool", "commercial_dataset", "training"],
        }),
      });
      const createdInbox = await created.json();
      setInbox(createdInbox);
      const received = await fetch(apiUrl("/api/admin/inbound/messages"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          recipient: createdInbox.address,
          message_id: `<console-${Date.now()}@lodia.local>`,
          sender: "contributor@example.com",
          subject: "Console inbound case",
          body_text: text,
          enqueue: false,
        }),
      });
      setInboundMessage(await received.json());
    } finally {
      setLoading(false);
    }
  }

  async function ingestWebhookCase() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/webhook-cases"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          source: "console",
          external_id: `console-${Date.now()}`,
          owner_id: "demo_contributor",
          text,
          allowed_uses: ["private_library", "candidate_pool", "commercial_dataset", "training"],
          enqueue: false,
        }),
      });
      setWebhookIngestion(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function runCurrentContentSafety() {
    if (!caseItem) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/admin/content-safety/case/${caseItem.case_id}/run`), {
        method: "POST",
        headers: requestHeaders(apiToken, false),
      });
      setContentSafety(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadLaunchReadiness() {
    setLoading(true);
    try {
      const migrations = await fetch(apiUrl("/api/admin/migrations/status"), { headers: requestHeaders(apiToken, false) });
      setMigrationStatus(await migrations.json());
      const readiness = await fetch(apiUrl("/api/admin/launch-readiness"), { headers: requestHeaders(apiToken, false) });
      setLaunchReadiness(await readiness.json());
    } finally {
      setLoading(false);
    }
  }

  async function bootstrapInternalTest() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/internal-test/bootstrap"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ provider_mode: "mock", evidence_ref: "internal-test://console-bootstrap" }),
      });
      const result: InternalTestBootstrapResult = await response.json();
      setInternalTestBootstrap(result);
      setLaunchReadiness(result.production_readiness);
      if (result.seeded_provider_configs.length) {
        setProviderConfig(result.seeded_provider_configs[result.seeded_provider_configs.length - 1]);
      }
      if (result.completed_compliance_tasks.length) {
        setComplianceTask(result.completed_compliance_tasks[result.completed_compliance_tasks.length - 1]);
      }
      const migrations = await fetch(apiUrl("/api/admin/migrations/status"), { headers: requestHeaders(apiToken, false) });
      setMigrationStatus(await migrations.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadMigrationPlan() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/migrations/plan"), { headers: requestHeaders(apiToken, false) });
      setMigrationPlan(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function requestTemporaryUploadCredentials() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/object-storage/temporary-upload-credentials"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ key_prefix: "raw/direct/console", expires_in_seconds: 900 }),
      });
      setTemporaryCredentials(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadOperationalAlerts() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/operational-alerts"), { headers: requestHeaders(apiToken, false) });
      setOperationalAlerts(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function runMaintenance() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/maintenance/run?limit=100"), {
        method: "POST",
        headers: requestHeaders(apiToken, false),
      });
      setMaintenanceResult(await response.json());
      const alerts = await fetch(apiUrl("/api/admin/operational-alerts"), { headers: requestHeaders(apiToken, false) });
      setOperationalAlerts(await alerts.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadCommercialProof() {
    if (!dataset) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/admin/datasets/${dataset.id}/commercial-proof`), { headers: requestHeaders(apiToken, false) });
      setCommercialProof(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function submitAndConfirmPayoutTransfer() {
    if (!payoutBatch) return;
    setLoading(true);
    try {
      const submitted = await fetch(apiUrl("/api/admin/payout-transfers"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ batch_id: payoutBatch.id, provider_name: "mock_payout" }),
      });
      const transfer = await submitted.json();
      const confirmed = await fetch(apiUrl(`/api/admin/payout-transfers/${transfer.id}/confirm`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ status: "succeeded", external_reference: `PAY-${Date.now()}`, response: { receipt: "ok" } }),
      });
      setPayoutTransfer(await confirmed.json());
    } finally {
      setLoading(false);
    }
  }

  async function recordBuyerUsageReport() {
    if (!deliveryGrant || !dataset) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/admin/buyer-usage-reports"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          grant_id: deliveryGrant.id,
          external_event_id: `usage-${Date.now()}`,
          reported_case_count: dataset.case_ids.length,
          payload: { source: "console" },
        }),
      });
      setBuyerUsageReport(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function loadEnterprisePortal() {
    if (!deliveryGrant?.delivery_token) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/enterprise/portal/${deliveryGrant.id}`), {
        headers: {
          ...requestHeaders(apiToken, false),
          "X-Lodia-Delivery-Token": deliveryGrant.delivery_token,
        },
      });
      setEnterprisePortal(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function recordEnterprisePortalUsage() {
    if (!deliveryGrant?.delivery_token || !dataset) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/enterprise/portal/${deliveryGrant.id}/usage-reports`), {
        method: "POST",
        headers: {
          ...requestHeaders(apiToken),
          "X-Lodia-Delivery-Token": deliveryGrant.delivery_token,
        },
        body: JSON.stringify({
          external_event_id: `portal-${Date.now()}`,
          reported_case_count: dataset.case_ids.length,
          payload: { source: "enterprise_portal" },
        }),
      });
      setBuyerUsageReport(await response.json());
      await loadEnterprisePortal();
    } finally {
      setLoading(false);
    }
  }

  async function createDeliveryGrant() {
    if (!dataset) return;
    setLoading(true);
    try {
      const customer = await ensureEnterpriseCustomer();
      const response = await fetch(apiUrl(`/api/admin/datasets/${dataset.id}/delivery-grants`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ customer_id: customer.id, order_id: enterpriseOrder?.id, max_reads: 20 }),
      });
      setDeliveryGrant(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function submitPayoutProfile(verified = false) {
    setLoading(true);
    try {
      const contributorId = caseItem?.owner_id || contributorDashboard?.contributor_id || "demo_contributor";
      const endpoint = verified ? `/api/admin/payout-profiles/${contributorId}` : "/api/contributor/payout-profile";
      const response = await fetch(apiUrl(endpoint), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          country_region: "CN",
          account_type: "bank",
          account_reference: "6222000000000000",
          ...(verified ? { kyc_status: "verified", tax_status: "verified", risk_status: "clear" } : {}),
        }),
      });
      setPayoutProfile(await response.json());
    } finally {
      setLoading(false);
    }
  }

  async function withdrawCurrentAuthorization() {
    const authorizationId = caseItem?.authorization_snapshot_id || uploadedAsset?.authorization_snapshot_id;
    if (!authorizationId) return;
    setLoading(true);
    try {
      await fetch(apiUrl(`/api/authorizations/${authorizationId}/withdraw`), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({ reason: "Withdrawn from console" }),
      });
      await loadAuthorizations();
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand console-brand" href="/" aria-label="返回 Lodia 官网">Lodia</a>
        <nav>
          <a className="console-nav-link" href="/">官网首页</a>
          <button className="nav-active"><Database size={18} /> 数据工厂</button>
          <button><ShieldCheck size={18} /> 信任门禁</button>
          <button><FileCheck2 size={18} /> 数据集</button>
          <button><Scale size={18} /> 分账</button>
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Commercial Console</p>
            <h1>AI Case-to-Dataset 生产线</h1>
          </div>
          <form
            className="top-actions"
            onSubmit={(event) => {
              event.preventDefault();
              if (loginPassword && !loading) {
                void login();
              }
            }}
          >
            <input
              className="token-input"
              type="password"
              value={apiToken}
              onChange={(event) => setApiToken(event.target.value)}
              placeholder="API Token"
            />
            <input
              className="token-input"
              value={loginEmail}
              onChange={(event) => setLoginEmail(event.target.value)}
              placeholder="Email"
            />
            <input
              className="token-input"
              type="password"
              value={loginPassword}
              onChange={(event) => setLoginPassword(event.target.value)}
              placeholder="Password"
            />
            <button className="secondary-action compact-action" type="submit" disabled={loading || !loginPassword}>
              登录
            </button>
            <span className="status-pill">{consoleNotice}</span>
          </form>
        </header>

        <section className="metric-grid">
          {metrics.map((metric) => {
            const Icon = metric.icon;
            return (
              <article className="metric-card" key={metric.label}>
                <Icon size={20} />
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
              </article>
            );
          })}
        </section>

        <section className="split">
          <div className="panel">
            <div className="panel-heading">
              <Layers3 size={18} />
              <h2>处理链路预览</h2>
            </div>
            <textarea value={text} onChange={(event) => setText(event.target.value)} />
            <div className="action-row">
              <button className="primary-action" onClick={runPreview} disabled={loading}>
                {loading ? "处理中" : "预览门禁"}
              </button>
              <button className="secondary-action" onClick={submitCase} disabled={loading}>
                提交入库
              </button>
              <button className="secondary-action" onClick={approveCase} disabled={loading || !caseItem}>
                审核通过
              </button>
              <button className="secondary-action" onClick={rejectCurrentCase} disabled={loading || !caseItem}>
                驳回
              </button>
              <button className="secondary-action" onClick={expertVerifyCase} disabled={loading || !caseItem || caseItem.quality_gate.drl !== "DRL3"}>
                专家验证
              </button>
              <button className="secondary-action" onClick={goldReviewCase} disabled={loading || !caseItem || caseItem.quality_gate.drl !== "DRL4"}>
                Gold 复核
              </button>
              <button className="secondary-action" onClick={buildDataset} disabled={loading || !caseItem || !caseItem.quality_gate.commercial_ready}>
                生成数据集
              </button>
              <button className="secondary-action" onClick={loadReviewQueue} disabled={loading}>
                审核队列
              </button>
              <button className="secondary-action" onClick={claimNextReview} disabled={loading}>
                认领复核
              </button>
              <button className="secondary-action" onClick={releaseCurrentReview} disabled={loading || !caseItem?.review_claimed_by}>
                释放复核
              </button>
              <button className="secondary-action icon-action" onClick={() => loadLongHorizonWorkbench()} disabled={loading || !caseItem}>
                <ClipboardCheck size={15} />
                字段精标
              </button>
              <button className="secondary-action" onClick={loadAuditLogs} disabled={loading}>
                审计日志
              </button>
              <button className="secondary-action" onClick={loadMetrics} disabled={loading}>
                指标
              </button>
              <button className="secondary-action" onClick={loadObservability} disabled={loading}>
                观测
              </button>
              <button className="secondary-action" onClick={loadContributorDashboard} disabled={loading}>
                贡献者
              </button>
              <button className="secondary-action" onClick={loadContributorOnboarding} disabled={loading}>
                入驻检查
              </button>
            </div>
          </div>

          <div className="panel result-panel">
            <div className="panel-heading">
              <ShieldCheck size={18} />
              <h2>出厂门禁</h2>
            </div>
            {preview ? (
              <div className="result-stack">
                <div className="result-row">
                  <span>状态</span>
                  <strong>{preview.status}</strong>
                </div>
                <div className="result-row">
                  <span>DRL</span>
                  <strong>{preview.case.quality_gate.drl}</strong>
                </div>
                <div className="result-row">
                  <span>质量分</span>
                  <strong>{preview.case.annotation.quality_score.toFixed(2)}</strong>
                </div>
                <div className="result-row">
                  <span>长程任务</span>
                  <strong>
                    {preview.case.annotation.labels.long_horizon_tier || "D"} · {Number(preview.case.annotation.labels.long_horizon_score || 0).toFixed(2)}
                  </strong>
                </div>
                {preview.case.quality_gate.required_actions.length ? (
                  <div className="result-row">
                    <span>待补证据</span>
                    <strong>{preview.case.quality_gate.required_actions[0]}</strong>
                  </div>
                ) : null}
                <div className="redacted-output">{preview.case.redaction.redacted_text}</div>
              </div>
            ) : (
              <p className="empty-state">运行一次预览，查看自动脱敏、标签、质量分和 DRL。</p>
            )}
          </div>
        </section>

        <section className="split lower-split">
          <div className="panel">
            <div className="panel-heading">
              <Layers3 size={18} />
              <h2>任务证据附件</h2>
            </div>
            <div className="file-row">
              <input type="file" onChange={(event) => setSelectedFile(event.target.files?.[0] || null)} />
              <button className="secondary-action" onClick={uploadAsset} disabled={loading || !selectedFile}>
                上传资产
              </button>
              <button className="secondary-action" onClick={requestAssetExtraction} disabled={loading || !uploadedAsset || uploadedAsset.status !== "extraction_pending"}>
                提取
              </button>
            </div>
            {uploadedAsset ? (
              <div className="result-stack">
                <div className="result-row">
                  <span>AssetID</span>
                  <strong>{uploadedAsset.id}</strong>
                </div>
                <div className="result-row">
                  <span>类型</span>
                  <strong>{uploadedAsset.asset_type}</strong>
                </div>
                <div className="result-row">
                  <span>状态</span>
                  <strong>{uploadedAsset.status}</strong>
                </div>
              </div>
            ) : (
              <p className="empty-state">当前只服务 LLM 长程任务 Case；附件只作为任务证据补充，不单独建设图片、音频或视频数据产品。</p>
            )}
          </div>

          <div className="panel">
            <div className="panel-heading">
              <LockKeyhole size={18} />
              <h2>授权快照</h2>
            </div>
            <div className="action-row">
              <button className="secondary-action" onClick={loadAuthorizations} disabled={loading}>
                授权列表
              </button>
              <button
                className="secondary-action"
                onClick={withdrawCurrentAuthorization}
                disabled={loading || !(caseItem?.authorization_snapshot_id || uploadedAsset?.authorization_snapshot_id)}
              >
                撤回当前授权
              </button>
            </div>
            {authorizations.length ? (
              <div className="audit-list">
                {authorizations.map((item) => (
                  <div className="audit-row" key={item.id}>
                    <span>{item.status}</span>
                    <strong>{item.id}</strong>
                    <small>{item.allowed_uses.join(", ")}</small>
                  </div>
                ))}
              </div>
            ) : (
              <p className="empty-state">每次提交都会记录用途、协议版本和撤回状态，出厂前必须校验授权仍然有效。</p>
            )}
          </div>
        </section>

        <section className="split lower-split">
          <div className="panel">
            <div className="panel-heading">
              <BadgeCheck size={18} />
              <h2>Case 资产</h2>
            </div>
            {caseItem ? (
              <div className="result-stack">
                <div className="result-row">
                  <span>CaseID</span>
                  <strong>{caseItem.case_id}</strong>
                </div>
                <div className="result-row">
                  <span>状态</span>
                  <strong>{caseItem.status}</strong>
                </div>
                <div className="result-row">
                  <span>认领人</span>
                  <strong>{caseItem.review_claimed_by || "-"}</strong>
                </div>
                <div className="result-row">
                  <span>DRL</span>
                  <strong>{caseItem.quality_gate.drl}</strong>
                </div>
                <div className="result-row">
                  <span>质量分</span>
                  <strong>{caseItem.annotation.quality_score.toFixed(2)}</strong>
                </div>
                <div className="result-row">
                  <span>长程任务</span>
                  <strong>
                    {caseItem.annotation.labels?.long_horizon_tier || "-"} · {Number(caseItem.annotation.labels?.long_horizon_score || 0).toFixed(2)}
                  </strong>
                </div>
                <div className="result-row">
                  <span>门禁</span>
                  <strong>{caseItem.quality_gate.gate_results?.llm_long_horizon_gate || "-"}</strong>
                </div>
                {caseItem.quality_gate.required_actions.length ? (
                  <div className="result-row">
                    <span>审核重点</span>
                    <strong>{caseItem.quality_gate.required_actions.join(", ")}</strong>
                  </div>
                ) : null}
                <div className="action-row compact-row">
                  <button className="secondary-action icon-action" onClick={() => loadLongHorizonWorkbench()} disabled={loading}>
                    <ClipboardCheck size={15} />
                    打开精标
                  </button>
                  <button className="primary-action icon-action" onClick={saveLongHorizonWorkbench} disabled={loading || !longHorizonWorkbench}>
                    <Save size={15} />
                    保存精标
                  </button>
                </div>
              </div>
            ) : submissionStatus ? (
              <div className="result-stack">
                <div className="result-row">
                  <span>SubmissionID</span>
                  <strong>{submissionStatus.submission_id}</strong>
                </div>
                <div className="result-row">
                  <span>状态</span>
                  <strong>{submissionStatus.status}</strong>
                </div>
                <div className="result-row">
                  <span>Worker</span>
                  <strong>{submissionStatus.jobs[0]?.status || "waiting"}</strong>
                </div>
                <div className="result-row">
                  <span>用途</span>
                  <strong>{submissionStatus.submission.allowed_uses.join(", ") || "-"}</strong>
                </div>
                <div className="action-row compact-row">
                  <button className="secondary-action" onClick={refreshPendingSubmission} disabled={loading}>
                    刷新状态
                  </button>
                </div>
              </div>
            ) : (
              <p className="empty-state">提交入库后，这里会显示脱敏后的 Case 资产状态。</p>
            )}
          </div>

          <div className="panel">
            <div className="panel-heading">
              <Scale size={18} />
              <h2>数据集与分账</h2>
            </div>
            {dataset ? (
              <div className="result-stack">
                <div className="result-row">
                  <span>数据集</span>
                  <strong>{dataset.name}</strong>
                </div>
                <div className="result-row">
                  <span>Case 数</span>
                  <strong>{dataset.case_ids.length}</strong>
                </div>
                <div className="result-row">
                  <span>贡献者池</span>
                  <strong>{formatMoney(dataset.payout?.contributor_pool_cents || 0)}</strong>
                </div>
                <div className="result-row">
                  <span>平台留存</span>
                  <strong>{formatMoney(dataset.payout?.platform_share_cents || 0)}</strong>
                </div>
                <button className="secondary-action" onClick={loadDataContract} disabled={loading}>
                  Data Contract
                </button>
                <button className="secondary-action" onClick={loadDatasets} disabled={loading}>
                  数据集列表
                </button>
                <button className="secondary-action" onClick={() => loadDatasetArtifact("manifest")} disabled={loading}>
                  Manifest
                </button>
                <button className="secondary-action" onClick={() => loadDatasetArtifact("quality_report")} disabled={loading}>
                  质量报告
                </button>
                <button className="secondary-action" onClick={() => loadDatasetArtifact("data")} disabled={loading}>
                  长程任务 JSONL
                </button>
                <button className="secondary-action" onClick={createPayoutBatch} disabled={loading}>
                  生成结算批次
                </button>
                <button className="secondary-action" onClick={settlePayoutBatch} disabled={loading || !payoutBatch || payoutBatch.status !== "ready"}>
                  结算批次
                </button>
                {payoutBatch ? (
                  <div className="result-row">
                    <span>批次</span>
                    <strong>{payoutBatch.status} · {formatMoney(payoutBatch.total_amount_cents)}</strong>
                  </div>
                ) : null}
                {datasetArtifact ? (
                  <pre className="artifact-preview">{datasetArtifact.slice(0, 900)}</pre>
                ) : null}
              </div>
            ) : (
              <p className="empty-state">审核到 DRL3 后即可生成数据集，并产生 UsageEvent 与 PayoutEvent。</p>
            )}
          </div>
        </section>

        <section className="panel refinement-panel">
          <div className="refinement-heading">
            <div className="panel-heading">
              <ClipboardCheck size={18} />
              <h2>Reviewer 字段级精标工作台</h2>
            </div>
            <div className="refinement-toolbar">
              <button className="secondary-action icon-action" onClick={() => loadLongHorizonWorkbench()} disabled={loading || !caseItem}>
                <ClipboardCheck size={15} />
                载入 Case
              </button>
              <button className="primary-action icon-action" onClick={saveLongHorizonWorkbench} disabled={loading || !caseItem || !longHorizonWorkbench}>
                <Save size={15} />
                保存字段
              </button>
            </div>
          </div>
          {longHorizonWorkbench ? (
            <div className="refinement-layout">
              <div className="refinement-source">
                <div className="result-stack">
                  <div className="result-row">
                    <span>Schema</span>
                    <strong>{longHorizonWorkbench.schema}</strong>
                  </div>
                  <div className="result-row">
                    <span>字段覆盖</span>
                    <strong>
                      {longHorizonWorkbench.field_quality.filled_fields.length}/9 · {longHorizonWorkbench.field_quality.tier}
                    </strong>
                  </div>
                  <div className="result-row">
                    <span>精标门禁</span>
                    <strong>{longHorizonWorkbench.field_quality.passed ? "passed" : "needs_more_evidence"}</strong>
                  </div>
                  <div className="result-row">
                    <span>来源长度</span>
                    <strong>{longHorizonWorkbench.long_horizon_task.evidence.source_chars} chars</strong>
                  </div>
                </div>
                <div className="refinement-missing">
                  {(longHorizonWorkbench.field_quality.required_missing.length
                    ? longHorizonWorkbench.field_quality.required_missing
                    : longHorizonWorkbench.field_quality.missing
                  ).slice(0, 8).map((item) => (
                    <span className="mini-pill" key={item}>{item}</span>
                  ))}
                  {longHorizonWorkbench.field_quality.source_evidence_too_thin ? (
                    <span className="mini-pill warning-pill">source_evidence_too_thin</span>
                  ) : null}
                </div>
                <div className="redacted-output refinement-redacted">{longHorizonWorkbench.case.redacted_text}</div>
                <label className="notes-field">
                  <span>审核备注</span>
                  <input value={longHorizonNotes} onChange={(event) => setLongHorizonNotes(event.target.value)} />
                </label>
              </div>
              <div className="refinement-grid">
                {LONG_HORIZON_FIELD_CONFIG.map((field) => (
                  <label className="field-editor" key={field.key}>
                    <span>{field.label}</span>
                    <textarea
                      value={longHorizonDraft[field.key] || ""}
                      onChange={(event) => setLongHorizonDraft({ ...longHorizonDraft, [field.key]: event.target.value })}
                      placeholder={field.hint}
                    />
                  </label>
                ))}
              </div>
            </div>
          ) : (
            <p className="empty-state">认领或选择一个 Case 后载入精标工作台，将自动预填机器抽取字段；Reviewer 逐字段修正后保存，出厂 JSONL 会优先使用精标字段。</p>
          )}
        </section>

        <section className="split lower-split">
          <div className="panel">
            <div className="panel-heading">
              <FileCheck2 size={18} />
              <h2>审核队列</h2>
            </div>
            {reviewQueue.length ? (
              <div className="audit-list">
                {reviewQueue.map((item) => (
                  <div className="audit-row" key={item.case_id}>
                    <span>{item.status}</span>
                    <strong>{item.case_id}</strong>
                    <small>
                      {item.quality_gate.drl} · {item.annotation.labels?.long_horizon_tier || "-"} · {item.quality_gate.gate_results?.llm_long_horizon_gate || "-"}
                    </small>
                  </div>
                ))}
              </div>
            ) : (
              <p className="empty-state">待复核 Case 会进入抽检、隐私复核或 DRL3 升级队列。</p>
            )}
          </div>

          <div className="panel">
            <div className="panel-heading">
              <Activity size={18} />
              <h2>生产指标</h2>
            </div>
            {metricsSnapshot ? (
              <div className="result-stack">
                <div className="result-row">
                  <span>数据集</span>
                  <strong>{metricsSnapshot.datasets}</strong>
                </div>
                <div className="result-row">
                  <span>审计事件</span>
                  <strong>{metricsSnapshot.audit_events}</strong>
                </div>
                <div className="result-row">
                  <span>资产状态</span>
                  <strong>{Object.keys(metricsSnapshot.assets || {}).length}</strong>
                </div>
                <div className="result-row">
                  <span>待分账</span>
                  <strong>{formatMoney(metricsSnapshot.pending_payout_cents)}</strong>
                </div>
                {observabilitySnapshot ? (
                  <>
                    <div className="result-row">
                      <span>DRL5</span>
                      <strong>{observabilitySnapshot.case_drl.DRL5 || 0}</strong>
                    </div>
                    <div className="result-row">
                      <span>模型调用</span>
                      <strong>{Object.values(observabilitySnapshot.model_invocations).reduce((sum, value) => sum + value, 0)}</strong>
                    </div>
                  </>
                ) : null}
              </div>
            ) : (
              <p className="empty-state">生产指标用于接入 SLS、Prometheus 和告警面板。</p>
            )}
          </div>
        </section>

        <section className="split lower-split">
          <div className="panel">
            <div className="panel-heading">
              <Scale size={18} />
              <h2>贡献者中心</h2>
            </div>
            {contributorDashboard ? (
              <div className="result-stack">
                <div className="result-row">
                  <span>Case 总数</span>
                  <strong>{contributorDashboard.cases.total}</strong>
                </div>
                <div className="result-row">
                  <span>待收益</span>
                  <strong>{formatMoney(contributorDashboard.ledger.pending_cents)}</strong>
                </div>
                <div className="result-row">
                  <span>已结算</span>
                  <strong>{formatMoney(contributorDashboard.ledger.settled_cents)}</strong>
                </div>
                <div className="result-row">
                  <span>DRL3+</span>
                  <strong>{(contributorDashboard.cases.by_drl.DRL3 || 0) + (contributorDashboard.cases.by_drl.DRL4 || 0) + (contributorDashboard.cases.by_drl.DRL5 || 0)}</strong>
                </div>
                <div className="action-row">
                  <button className="secondary-action" onClick={() => submitPayoutProfile(false)} disabled={loading}>
                    提交收款资料
                  </button>
                  <button className="secondary-action" onClick={() => submitPayoutProfile(true)} disabled={loading}>
                    验证资料
                  </button>
                </div>
                {payoutProfile ? (
                  <div className="result-row">
                    <span>收款资料</span>
                    <strong>{payoutProfile.status} · {payoutProfile.account_type} {payoutProfile.account_ref_suffix}</strong>
                  </div>
                ) : null}
                {contributorOnboarding ? (
                  <div className="result-row">
                    <span>入驻检查</span>
                    <strong>{contributorOnboarding.ready ? "ready" : `${contributorOnboarding.next_actions.length} actions`}</strong>
                  </div>
                ) : null}
              </div>
            ) : contributorOnboarding ? (
              <div className="result-stack">
                <div className="result-row">
                  <span>入驻检查</span>
                  <strong>{contributorOnboarding.ready ? "ready" : `${contributorOnboarding.next_actions.length} actions`}</strong>
                </div>
                <div className="result-row">
                  <span>Case</span>
                  <strong>{contributorOnboarding.signals.case_count} · {contributorOnboarding.signals.payout_profile_status}</strong>
                </div>
              </div>
            ) : (
              <p className="empty-state">贡献者可以查看 Case、授权状态和持续收益账本。</p>
            )}
          </div>

          <div className="panel">
            <div className="panel-heading">
              <Database size={18} />
              <h2>企业交付</h2>
            </div>
            <div className="action-row">
              <button className="secondary-action" onClick={loadEnterpriseCustomers} disabled={loading}>
                客户列表
              </button>
              <button className="secondary-action" onClick={createEnterpriseCustomer} disabled={loading}>
                新建客户
              </button>
              <button className="secondary-action" onClick={createEnterpriseContract} disabled={loading}>
                创建合同
              </button>
              <button className="secondary-action" onClick={createEnterpriseOrder} disabled={loading || !dataset}>
                创建订单
              </button>
              <button className="secondary-action" onClick={createDeliveryGrant} disabled={loading || !dataset}>
                创建交付授权
              </button>
              <button className="secondary-action" onClick={recognizeEnterpriseOrder} disabled={loading || !enterpriseOrder}>
                确认收入
              </button>
              <button className="secondary-action" onClick={upsertDemoTenantQuota} disabled={loading}>
                配额
              </button>
              <button className="secondary-action" onClick={openEnterpriseDispute} disabled={loading || !enterpriseOrder}>
                争议
              </button>
              <button className="secondary-action" onClick={loadEnterprisePortal} disabled={loading || !deliveryGrant?.delivery_token}>
                买方门户
              </button>
              <button className="secondary-action" onClick={recordEnterprisePortalUsage} disabled={loading || !deliveryGrant?.delivery_token || !dataset}>
                门户回传
              </button>
            </div>
            {datasets.length ? (
              <div className="audit-list">
                {datasets.map((item) => (
                  <div className="audit-row" key={item.id}>
                    <span>{item.status}</span>
                    <strong>{item.name}</strong>
                    <small>{item.case_ids.length} cases</small>
                  </div>
                ))}
              </div>
            ) : (
              <p className="empty-state">企业侧只拿到脱敏数据、Manifest、Quality Report 和 Data Contract。</p>
            )}
            {enterpriseCustomers.length ? (
              <div className="audit-list">
                {enterpriseCustomers.map((item) => (
                  <div className="audit-row" key={item.id}>
                    <span>{item.status}</span>
                    <strong>{item.name}</strong>
                    <small>{item.contact_email_domain}</small>
                  </div>
                ))}
              </div>
            ) : null}
            {enterpriseContract || enterpriseOrder || deliveryGrant || tenantQuota || dispute ? (
              <div className="result-stack">
                {enterpriseContract ? (
                  <div className="result-row">
                    <span>企业合同</span>
                    <strong>{enterpriseContract.status} · {enterpriseContract.version}</strong>
                  </div>
                ) : null}
                {enterpriseOrder ? (
                  <div className="result-row">
                    <span>企业订单</span>
                    <strong>{enterpriseOrder.status} · {formatMoney(enterpriseOrder.gross_revenue_cents - enterpriseOrder.direct_cost_cents)}</strong>
                  </div>
                ) : null}
                {deliveryGrant ? (
                  <>
                    <div className="result-row">
                      <span>交付授权</span>
                      <strong>{deliveryGrant.status} · {deliveryGrant.token_suffix} · {deliveryGrant.read_count}/{deliveryGrant.max_reads}</strong>
                    </div>
                    {deliveryGrant.delivery_token ? (
                      <div className="result-row">
                        <span>一次性 Token</span>
                        <code className="secret-token">{deliveryGrant.delivery_token}</code>
                      </div>
                    ) : null}
                  </>
                ) : null}
                {tenantQuota ? (
                  <div className="result-row">
                    <span>租户配额</span>
                    <strong>{tenantQuota.tenant_id} · {tenantQuota.monthly_order_limit}/{tenantQuota.monthly_delivery_read_limit}</strong>
                  </div>
                ) : null}
                {dispute ? (
                  <div className="result-row">
                    <span>争议</span>
                    <strong>{dispute.status} · hold {dispute.held_payout_count}</strong>
                  </div>
                ) : null}
                {enterprisePortal ? (
                  <div className="result-row">
                    <span>买方门户</span>
                    <strong>{enterprisePortal.dataset.case_count} cases · {enterprisePortal.usage_reports.length} reports</strong>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>

        <section className="split lower-split">
          <div className="panel">
            <div className="panel-heading">
              <Activity size={18} />
              <h2>P0 入口</h2>
            </div>
            <div className="action-row">
              <button className="secondary-action" onClick={createInboxAndReceiveCase} disabled={loading}>
                收件箱入库
              </button>
              <button className="secondary-action" onClick={ingestWebhookCase} disabled={loading}>
                Webhook 入库
              </button>
              <button className="secondary-action" onClick={runCurrentContentSafety} disabled={loading || !caseItem}>
                内容安全
              </button>
            </div>
            <div className="result-stack">
              {inbox ? (
                <div className="result-row">
                  <span>Inbox</span>
                  <strong>{inbox.status} · {inbox.address}</strong>
                </div>
              ) : null}
              {inboundMessage ? (
                <div className="result-row">
                  <span>Inbound</span>
                  <strong>{inboundMessage.status} · {inboundMessage.submission_id || inboundMessage.subject}</strong>
                </div>
              ) : null}
              {webhookIngestion ? (
                <div className="result-row">
                  <span>Webhook</span>
                  <strong>{webhookIngestion.status} · {webhookIngestion.source}</strong>
                </div>
              ) : null}
              {contentSafety ? (
                <div className="result-row">
                  <span>Safety</span>
                  <strong>{contentSafety.status} · {contentSafety.action}</strong>
                </div>
              ) : null}
            </div>
            {!inbox && !inboundMessage && !webhookIngestion && !contentSafety ? (
              <p className="empty-state">邮件、Webhook 和内容安全结果会绑定 Case、审计和后续合规复核。</p>
            ) : null}
          </div>

          <div className="panel">
            <div className="panel-heading">
              <ShieldCheck size={18} />
              <h2>P0 准入</h2>
            </div>
            <div className="action-row">
              <button className="secondary-action" onClick={loadLaunchReadiness} disabled={loading}>
                准入检查
              </button>
              <button className="secondary-action" onClick={bootstrapInternalTest} disabled={loading}>
                内测初始化
              </button>
              <button className="secondary-action" onClick={loadMigrationPlan} disabled={loading}>
                迁移计划
              </button>
              <button className="secondary-action" onClick={requestTemporaryUploadCredentials} disabled={loading}>
                临时凭证
              </button>
              <button className="secondary-action" onClick={submitAndConfirmPayoutTransfer} disabled={loading || !payoutBatch}>
                转账回单
              </button>
              <button className="secondary-action" onClick={recordBuyerUsageReport} disabled={loading || !deliveryGrant || !dataset}>
                使用回传
              </button>
              <button className="secondary-action" onClick={loadOperationalAlerts} disabled={loading}>
                运营告警
              </button>
              <button className="secondary-action" onClick={runMaintenance} disabled={loading}>
                维护任务
              </button>
              <button className="secondary-action" onClick={loadCommercialProof} disabled={loading || !dataset}>
                商用证明
              </button>
            </div>
            <div className="result-stack">
              {launchReadiness ? (
                <div className="result-row">
                  <span>Production</span>
                  <strong>
                    {launchReadiness.ready ? "ready" : `${launchReadiness.blockers.length} blockers`}
                    {launchReadiness.next_actions?.length ? ` · ${launchReadiness.next_actions[0]}` : ""}
                  </strong>
                </div>
              ) : null}
              {internalTestBootstrap ? (
                <div className="result-row">
                  <span>Internal</span>
                  <strong>
                    {internalTestBootstrap.internal_test_readiness.ready ? "ready" : `${internalTestBootstrap.internal_test_readiness.blockers.length} blockers`}
                    {internalTestBootstrap.seeded_provider_configs.length ? ` · +${internalTestBootstrap.seeded_provider_configs.length} providers` : ""}
                  </strong>
                </div>
              ) : null}
              {migrationStatus ? (
                <div className="result-row">
                  <span>Schema</span>
                  <strong>{migrationStatus.ok ? "current" : `${migrationStatus.missing_versions.length} missing`}</strong>
                </div>
              ) : null}
              {migrationPlan ? (
                <div className="result-row">
                  <span>Plan</span>
                  <strong>{migrationPlan.pending_versions.length} pending · {migrationPlan.rollback_versions.length} rollback</strong>
                </div>
              ) : null}
              {temporaryCredentials ? (
                <div className="result-row">
                  <span>STS</span>
                  <strong>{temporaryCredentials.supported ? "issued" : temporaryCredentials.reason || "unsupported"}</strong>
                </div>
              ) : null}
              {providerConfig ? (
                <div className="result-row">
                  <span>Provider</span>
                  <strong>{providerConfig.status} · {providerConfig.provider_type}</strong>
                </div>
              ) : null}
              {complianceTask ? (
                <div className="result-row">
                  <span>Task</span>
                  <strong>{complianceTask.status} · {complianceTask.task_type}</strong>
                </div>
              ) : null}
              {payoutTransfer ? (
                <div className="result-row">
                  <span>Transfer</span>
                  <strong>{payoutTransfer.status} · {formatMoney(payoutTransfer.amount_cents)}</strong>
                </div>
              ) : null}
              {buyerUsageReport ? (
                <div className="result-row">
                  <span>Usage</span>
                  <strong>{buyerUsageReport.status} · {buyerUsageReport.reported_case_count} cases</strong>
                </div>
              ) : null}
              {operationalAlerts ? (
                <div className="result-row">
                  <span>Alerts</span>
                  <strong>{operationalAlerts.critical_count} critical · {operationalAlerts.alert_count} total</strong>
                </div>
              ) : null}
              {maintenanceResult ? (
                <div className="result-row">
                  <span>Maintenance</span>
                  <strong>{maintenanceResult.status} · raw {maintenanceResult.raw.purged_count} · upload {maintenanceResult.upload_sessions.expired_count}</strong>
                </div>
              ) : null}
              {commercialProof ? (
                <div className="result-row">
                  <span>Proof</span>
                  <strong>{commercialProof.case_count} cases · {commercialProof.proof_hash.slice(0, 10)}</strong>
                </div>
              ) : null}
            </div>
            {!launchReadiness && !internalTestBootstrap && !migrationStatus && !migrationPlan && !temporaryCredentials && !providerConfig && !payoutTransfer && !buyerUsageReport && !operationalAlerts && !maintenanceResult && !commercialProof ? (
              <p className="empty-state">迁移状态、供应商、备案任务、支付回单、运营告警、维护任务和商用证明共同构成上线前准入证据。</p>
            ) : null}
          </div>
        </section>

        <section className="split lower-split">
          <div className="panel">
            <div className="panel-heading">
              <ShieldCheck size={18} />
              <h2>质量运营</h2>
            </div>
            <div className="action-row">
              <button className="secondary-action" onClick={refreshCurrentSourceTrust} disabled={loading}>
                来源可信度
              </button>
              <button className="secondary-action" onClick={scheduleReviewSamples} disabled={loading}>
                抽检排队
              </button>
              <button className="secondary-action" onClick={completeFirstReviewSample} disabled={loading || !reviewSamples.length}>
                完成抽检
              </button>
              <button className="secondary-action" onClick={runCurrentDatasetEvaluation} disabled={loading || !dataset}>
                数据评测
              </button>
              <button className="secondary-action" onClick={runCurrentReconciliation} disabled={loading}>
                对账
              </button>
            </div>
            <div className="result-stack">
              {sourceTrust ? (
                <div className="result-row">
                  <span>可信度</span>
                  <strong>{sourceTrust.score.toFixed(2)} · {sourceTrust.accepted_count}/{sourceTrust.case_count}</strong>
                </div>
              ) : null}
              {reviewSamples.length ? (
                <div className="result-row">
                  <span>抽检</span>
                  <strong>{reviewSamples[0].status} · {reviewSamples[0].sample_type}</strong>
                </div>
              ) : null}
              {evalRun ? (
                <div className="result-row">
                  <span>评测</span>
                  <strong>{evalRun.status} · {evalRun.findings.length} findings</strong>
                </div>
              ) : null}
              {reconciliation ? (
                <div className="result-row">
                  <span>对账</span>
                  <strong>{reconciliation.status} · {reconciliation.summary.anomaly_count} anomalies</strong>
                </div>
              ) : null}
            </div>
            {!sourceTrust && !reviewSamples.length && !evalRun && !reconciliation ? (
              <p className="empty-state">抽检、盲审、来源可信度、数据评测和订单分账对账共同决定数据是否能稳定商用。</p>
            ) : null}
          </div>

          <div className="panel">
            <div className="panel-heading">
              <LockKeyhole size={18} />
              <h2>合规运营</h2>
            </div>
            <div className="action-row">
              <button className="secondary-action" onClick={createAndFulfillDsr} disabled={loading}>
                DSR 演练
              </button>
              <button className="secondary-action" onClick={issueAndPayInvoice} disabled={loading || !enterpriseOrder}>
                发票回款
              </button>
              <button className="secondary-action" onClick={upsertDemoSsoProvider} disabled={loading}>
                SSO 配置
              </button>
            </div>
            <div className="result-stack">
              {dsrRequest ? (
                <div className="result-row">
                  <span>DSR</span>
                  <strong>{dsrRequest.status} · cases {dsrRequest.deleted_cases}</strong>
                </div>
              ) : null}
              {invoice ? (
                <div className="result-row">
                  <span>发票</span>
                  <strong>{invoice.status} · {formatMoney(invoice.amount_cents + invoice.tax_cents)}</strong>
                </div>
              ) : null}
              {ssoProvider ? (
                <div className="result-row">
                  <span>SSO</span>
                  <strong>{ssoProvider.status} · {ssoProvider.provider_type} · {ssoProvider.domain}</strong>
                </div>
              ) : null}
            </div>
            {!dsrRequest && !invoice && !ssoProvider ? (
              <p className="empty-state">中国区独立运营需要把数据主体权利、发票税务、企业身份接入都沉到审计链路里。</p>
            ) : null}
          </div>
        </section>

        <section className="panel audit-panel">
          <div className="panel-heading">
            <FileCheck2 size={18} />
            <h2>Data Contract</h2>
          </div>
          {dataContract ? (
            <div className="result-stack">
              <div className="result-row">
                <span>版本</span>
                <strong>{dataContract.contract.version}</strong>
              </div>
              <div className="result-row">
                <span>用途</span>
                <strong>{dataContract.contract.purpose}</strong>
              </div>
              <div className="result-row">
                <span>Case 数</span>
                <strong>{dataContract.contract.case_count}</strong>
              </div>
            </div>
          ) : (
            <p className="empty-state">数据集通过出厂门禁后，会生成可审计的 Data Contract。</p>
          )}
        </section>

        <section className="module-grid">
          {modules.map(([name, description, state]) => (
            <article className="module-card" key={name}>
              <span className={state === "active" ? "dot active" : "dot"} />
              <h3>{name}</h3>
              <p>{description}</p>
            </article>
          ))}
        </section>

        <section className="panel audit-panel">
          <div className="panel-heading">
            <ShieldCheck size={18} />
            <h2>审计后台</h2>
          </div>
          {auditLogs.length ? (
            <div className="audit-list">
              {auditLogs.map((item) => (
                <div className="audit-row" key={item.id}>
                  <span>{item.event_type}</span>
                  <strong>{item.entity_type}:{item.entity_id}</strong>
                  <small>{item.actor_id}</small>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-state">生产环境使用 Admin Token 查看审计事件和关键状态变更。</p>
          )}
        </section>
      </section>
    </main>
  );
}

function requestHeaders(token: string, json = true) {
  const headers: Record<string, string> = {};
  if (json) {
    headers["Content-Type"] = "application/json";
  }
  if (token.trim()) {
    headers.Authorization = `Bearer ${token.trim()}`;
  }
  return headers;
}

function formatMoney(cents: number) {
  return `¥${(cents / 100).toFixed(2)}`;
}

function App() {
  const pathname = window.location.pathname;
  if (pathname.startsWith("/app")) {
    document.title = "Lodia Console";
    return <ConsoleApp />;
  }
  document.title = "Lodia - AI 数据资产平台";
  return <LandingPage />;
}

createRoot(document.getElementById("root")!).render(<App />);
