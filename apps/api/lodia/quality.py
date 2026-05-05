from __future__ import annotations

from typing import Dict, List

from .domain import AnnotationResult, DataReadinessLevel, DedupResult, QualityGateResult, RedactionResult


COMMERCIAL_USES = {"commercial_dataset", "training", "gold_eval"}


def run_quality_gate(
    redaction: RedactionResult,
    annotation: AnnotationResult,
    dedup: DedupResult,
    allowed_uses: List[str],
    human_reviewed: bool = False,
    expert_reviewed: bool = False,
    double_reviewed: bool = False,
) -> QualityGateResult:
    gate_results: Dict[str, str] = {
        "schema_gate": "passed",
        "privacy_gate": "passed" if redaction.passed else "failed",
        "license_gate": "passed" if allowed_uses else "failed",
        "dedup_gate": "passed" if dedup.duplicate_status == "unique" else "limited",
        "annotation_gate": "passed" if annotation.confidence >= 0.65 else "limited",
        "utility_gate": "passed" if annotation.quality_score >= 0.45 else "failed",
    }

    required_actions: List[str] = []
    blocked_uses: List[str] = []
    effective_allowed = list(allowed_uses)

    if not redaction.passed:
        required_actions.append("privacy_review")
        effective_allowed = []
        blocked_uses = sorted(COMMERCIAL_USES | {"private_library", "candidate_pool"})
        drl = DataReadinessLevel.DRL0
    elif not allowed_uses:
        required_actions.append("license_confirmation")
        blocked_uses = sorted(COMMERCIAL_USES | {"candidate_pool"})
        drl = DataReadinessLevel.DRL1
    elif annotation.confidence < 0.65 or annotation.quality_score < 0.45:
        required_actions.append("annotation_review")
        blocked_uses = sorted(COMMERCIAL_USES)
        drl = DataReadinessLevel.DRL1
    else:
        blocked_uses = sorted(use for use in COMMERCIAL_USES if use not in allowed_uses)
        drl = DataReadinessLevel.DRL2
        if COMMERCIAL_USES.intersection(allowed_uses):
            required_actions.append("human_review")

    if human_reviewed and redaction.passed and COMMERCIAL_USES.intersection(allowed_uses):
        drl = DataReadinessLevel.DRL3
        required_actions = [action for action in required_actions if action != "human_review"]

    if expert_reviewed and drl == DataReadinessLevel.DRL3 and "training" in allowed_uses:
        drl = DataReadinessLevel.DRL4

    if double_reviewed and expert_reviewed and "gold_eval" in allowed_uses:
        drl = DataReadinessLevel.DRL5

    commercial_ready = drl in {
        DataReadinessLevel.DRL3,
        DataReadinessLevel.DRL4,
        DataReadinessLevel.DRL5,
    }

    return QualityGateResult(
        drl=drl,
        gate_results=gate_results,
        allowed_uses=effective_allowed,
        blocked_uses=blocked_uses,
        required_actions=required_actions,
        commercial_ready=commercial_ready,
    )
