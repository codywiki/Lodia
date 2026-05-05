from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class DataReadinessLevel(str, Enum):
    DRL0 = "DRL0"
    DRL1 = "DRL1"
    DRL2 = "DRL2"
    DRL3 = "DRL3"
    DRL4 = "DRL4"
    DRL5 = "DRL5"


@dataclass(frozen=True)
class RedactionFinding:
    finding_type: str
    placeholder: str
    risk_level: str
    confidence: float


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    findings: List[RedactionFinding]
    residual_findings: List[RedactionFinding]
    privacy_risk_score: float
    passed: bool


@dataclass(frozen=True)
class AnnotationResult:
    domain: str
    task_type: str
    difficulty: str
    reuse_types: List[str]
    quality_score: float
    confidence: float
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DedupResult:
    raw_hash: str
    canonical_hash: str
    simhash: int
    duplicate_status: str
    novelty_score: float


@dataclass(frozen=True)
class QualityGateResult:
    drl: DataReadinessLevel
    gate_results: Dict[str, str]
    allowed_uses: List[str]
    blocked_uses: List[str]
    required_actions: List[str]
    commercial_ready: bool


@dataclass(frozen=True)
class CaseTurn:
    role: str
    content: str
    order_index: int


@dataclass(frozen=True)
class CaseRecord:
    case_id: str
    owner_id: str
    turns: List[CaseTurn]
    redaction: RedactionResult
    annotation: AnnotationResult
    dedup: DedupResult
    quality_gate: QualityGateResult


@dataclass(frozen=True)
class RevenueEvent:
    event_id: str
    gross_revenue_cents: int
    direct_cost_cents: int
    billable: bool = True


@dataclass(frozen=True)
class ContributionWeight:
    case_id: str
    contributor_id: str
    quality_score: float
    novelty_score: float
    source_trust_score: float
    license_weight: float
    usage_count: int
    duplicate_penalty: float
    reviewed_level: DataReadinessLevel


@dataclass(frozen=True)
class PayoutAllocation:
    event_id: str
    contributor_id: str
    case_id: str
    amount_cents: int
    weight: float
    status: str = "pending"


@dataclass(frozen=True)
class PayoutPlan:
    event_id: str
    gross_revenue_cents: int
    direct_cost_cents: int
    net_margin_cents: int
    platform_share_cents: int
    contributor_pool_cents: int
    allocations: List[PayoutAllocation]
    warning: Optional[str] = None
