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
  status: string;
  redacted_text: string;
  authorization_snapshot_id?: string | null;
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
    "请分析这个客服投诉案例，客户手机号 13800138000，邮箱 user@example.com，API key sk-abcdefghijklmnopqrstuvwxyz。要求输出处理步骤、验收结果和可复用规则。"
  );
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [caseItem, setCaseItem] = useState<CaseItem | null>(null);
  const [dataset, setDataset] = useState<DatasetResult | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [reviewQueue, setReviewQueue] = useState<ReviewQueueItem[]>([]);
  const [metricsSnapshot, setMetricsSnapshot] = useState<MetricsSnapshot | null>(null);
  const [dataContract, setDataContract] = useState<DataContract | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadedAsset, setUploadedAsset] = useState<AssetItem | null>(null);
  const [authorizations, setAuthorizations] = useState<AuthorizationSnapshot[]>([]);
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
              <button className="secondary-action" onClick={buildDataset} disabled={loading || !caseItem || caseItem.quality_gate.drl !== "DRL3"}>
                生成数据集
              </button>
              <button className="secondary-action" onClick={loadReviewQueue} disabled={loading}>
                审核队列
              </button>
              <button className="secondary-action" onClick={loadAuditLogs} disabled={loading}>
                审计日志
              </button>
              <button className="secondary-action" onClick={loadMetrics} disabled={loading}>
                指标
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
                    <small>{item.quality_gate.drl}</small>
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
              </div>
            ) : (
              <p className="empty-state">生产指标用于接入 SLS、Prometheus 和告警面板。</p>
            )}
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
