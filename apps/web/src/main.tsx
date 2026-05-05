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

function App() {
  const [text, setText] = useState(
    "请分析这个客服投诉案例，客户手机号 13800138000，邮箱 user@example.com，API key sk-abcdefghijklmnopqrstuvwxyz。要求输出处理步骤、验收结果和可复用规则。"
  );
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);

  async function runPreview() {
    setLoading(true);
    try {
      const response = await fetch("http://localhost:8000/api/pipeline/preview", {
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
            <button className="primary-action" onClick={runPreview} disabled={loading}>
              {loading ? "处理中" : "运行自动脱敏与质量门禁"}
            </button>
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

createRoot(document.getElementById("root")!).render(<App />);
