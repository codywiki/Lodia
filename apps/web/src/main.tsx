import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BadgeCheck,
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
  const [loading, setLoading] = useState(false);

  async function runPreview() {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/pipeline/preview"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
        headers: { "Content-Type": "application/json" },
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
        headers: { "Content-Type": "application/json" },
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
        headers: { "Content-Type": "application/json" },
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
          <span className="status-pill">CN Independent</span>
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
              <button className="secondary-action" onClick={buildDataset} disabled={loading || !caseItem || caseItem.quality_gate.drl !== "DRL3"}>
                生成数据集
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
              </div>
            ) : (
              <p className="empty-state">审核到 DRL3 后即可生成数据集，并产生 UsageEvent 与 PayoutEvent。</p>
            )}
          </div>
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
      </section>
    </main>
  );
}

function formatMoney(cents: number) {
  return `¥${(cents / 100).toFixed(2)}`;
}

createRoot(document.getElementById("root")!).render(<App />);
