# Lodia

> Turn long-horizon AI work into reusable datasets and lasting data assets.

Lodia is a data asset platform for LLM and Agent-era work.

It turns high-quality AI conversations, Codex/Cursor tasks, Agent traces, evaluation reviews, tool execution records, and human acceptance feedback into authorized, structured, privacy-safe, commercially usable training and evaluation datasets.

The product starts from one focused belief: the most valuable AI data is not a polished answer. It is the full task path: goal, context, constraints, process, tool evidence, failure, correction, acceptance, and reusable judgment.

## What Lodia Does

Lodia helps contributors preserve the value hidden inside daily AI work, and helps AI companies obtain higher-quality long-horizon task data.

For contributors, Lodia is a way to build personal data assets from real work. A useful case can keep generating revenue when it is accepted, packaged, delivered, or reused.

For AI teams and enterprises, Lodia is a governed pipeline for data that can actually be trained on, evaluated against, audited, and paid for fairly.

## Data Focus

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

## Product Pipeline

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

## Current Engineering Spine

The active product mainline is:

- Go API service under `apps/api-go`
- React console and product site under `apps/web`
- MySQL as the primary transactional store
- Redis-backed worker queue
- OSS-compatible object storage for raw evidence, assets, and dataset artifacts
- HTTP smoke coverage through `scripts/go_smoke.sh`
- CI coverage through `.github/workflows/app.yml`

The Go mainline owns new backend development. Product documentation and architecture specifications are kept under `docs`.

## Core Capabilities

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
- Governance: RBAC, audit logs, migration registry, launch readiness checks, operational alerts, DSR requests, content-safety scans, and payout profiles.

## Fair Revenue Model

Lodia is designed around contributor-aligned economics.

The platform keeps 20% of net revenue after direct costs. The remaining 80% goes into the contributor pool and is distributed by case-level payout events. Allocation can consider case quality, task completeness, evidence strength, reviewer outcome, and commercial usage.

The accounting model is event-based:

- UsageEvent records commercial usage.
- PayoutEvent records each contributor allocation.
- PayoutBatch groups payable events.
- PayoutTransfer records external settlement.
- Contributor dashboard shows pending, batched, settled, and total earnings.

## Data Quality Standard

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

This is how Lodia moves from “collected content” to reusable, auditable, commercially usable data.

## Repository Structure

```text
apps/
  api-go/      Go API, worker-facing store, pipeline, review, dataset, ledger, and enterprise delivery logic
  web/         React product site and console
docs/          Product, architecture, data quality, compliance, and production-readiness documents
scripts/       Smoke tests, production verification helpers, and deployment utilities
.github/       CI and repository automation
```

## Documentation

- Product requirements: `docs/LODIA_PRD.md`
- Technical architecture: `docs/LODIA_TECH_ARCHITECTURE.md`
- Production core: `docs/PRODUCTION_CORE.md`
- LLM long-horizon task data PRD: `docs/LLM_LONG_HORIZON_TASK_DATA_PRD.md`

## License

Lodia is released under the GNU Affero General Public License v3.0.

See [LICENSE](./LICENSE).
