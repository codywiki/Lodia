from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import List, Optional

from .annotation import annotate
from .dedup import fingerprint
from .domain import CaseRecord, CaseTurn
from .quality import run_quality_gate
from .redaction import redact_text


@dataclass(frozen=True)
class ProcessedCase:
    case: CaseRecord
    status: str


def process_text_case(
    raw_text: str,
    owner_id: str,
    allowed_uses: List[str],
    known_hashes: Optional[List[str]] = None,
    known_simhashes: Optional[List[int]] = None,
    human_reviewed: bool = False,
) -> ProcessedCase:
    redaction = redact_text(raw_text)
    dedup = fingerprint(raw_text, redaction.redacted_text, known_hashes or [], known_simhashes or [])
    annotation = annotate(redaction.redacted_text, redaction)
    quality_gate = run_quality_gate(
        redaction=redaction,
        annotation=annotation,
        dedup=dedup,
        allowed_uses=allowed_uses,
        human_reviewed=human_reviewed,
    )
    case = CaseRecord(
        case_id=f"case_{uuid.uuid4().hex[:16]}",
        owner_id=owner_id,
        turns=[CaseTurn(role="mixed", content=redaction.redacted_text, order_index=0)],
        redaction=redaction,
        annotation=annotation,
        dedup=dedup,
        quality_gate=quality_gate,
    )
    status = "review_pending" if quality_gate.required_actions else "candidate_ready"
    if quality_gate.commercial_ready:
        status = "commercial_ready"
    if not redaction.passed:
        status = "privacy_review"
    return ProcessedCase(case=case, status=status)
