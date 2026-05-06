import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BadgeCheck,
  Activity,
  Database,
  FileCheck2,
  Layers3,
  LockKeyhole,
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
    };
    quality_gate: {
      drl: string;
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
  annotation: {
    quality_score: number;
    domain: string;
    task_type: string;
  };
  quality_gate: {
    drl: string;
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

const metrics = [
  { label: "Raw 隔离", value: "100%", icon: LockKeyhole },
  { label: "自动处理", value: "92%", icon: Sparkles },
  { label: "DRL3+ 候选", value: "18.4%", icon: BadgeCheck },
  { label: "贡献者池", value: "80%", icon: Scale },
];

const modules = [
  ["Inbox", "多入口采集与授权快照", "active"],
  ["Pipeline", "脱敏、去重、标注、质量门禁", "active"],
  ["Studio", "抽检、复核、专家精标", "planned"],
  ["Gold", "商用数据集与 gold eval", "planned"],
  ["Ledger", "UsageEvent、PayoutEvent、对账", "active"],
  ["Trust", "中国区合规、安全审计、风控", "active"],
];

const apiUrl = (path: string) => path;

function App() {
  const [text, setText] = useState(
    "请分析这个客服投诉案例，客户手机号 13800138000，邮箱 user@example.com，包含一个测试密钥占位符。要求输出处理步骤、验收结果和可复用规则。"
  );
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [caseItem, setCaseItem] = useState<CaseItem | null>(null);
  const [dataset, setDataset] = useState<DatasetResult | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [reviewQueue, setReviewQueue] = useState<ReviewQueueItem[]>([]);
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
      const response = await fetch(apiUrl("/api/submissions/text"), {
        method: "POST",
        headers: requestHeaders(apiToken),
        body: JSON.stringify({
          owner_id: "demo_contributor",
          text,
          allowed_uses: ["private_library", "candidate_pool", "commercial_dataset", "training"],
        }),
      });
      const payload = await response.json();
      setCaseItem(payload.case);
      setDataset(null);
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
      setCaseItem(await response.json());
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
        await loadReviewQueue();
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
        <div className="brand">Lodia</div>
        <nav>
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
          <div className="top-actions">
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
            <button className="secondary-action compact-action" onClick={login} disabled={loading || !loginPassword}>
              登录
            </button>
            <span className="status-pill">CN Independent</span>
          </div>
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
              <h2>多模态资产</h2>
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
              <p className="empty-state">文本、日志和 trace 会自动抽取证据；图片、PDF、音视频先进入专用提取队列。</p>
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
                <button className="secondary-action" onClick={() => loadDatasetArtifact("data")} disabled={loading}>
                  导出预览
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
                    <small>{item.quality_gate.drl} · {item.review_claimed_by || "unclaimed"}</small>
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
              </div>
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

createRoot(document.getElementById("root")!).render(<App />);
