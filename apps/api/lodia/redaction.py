from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Pattern, Tuple
from urllib.parse import urlsplit, urlunsplit

from .domain import RedactionFinding, RedactionResult


Detector = Tuple[str, Pattern[str], str, float]


DETECTORS: List[Detector] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "medium", 0.99),
    ("phone", re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"), "high", 0.98),
    ("cn_id", re.compile(r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"), "high", 0.97),
    ("bank_card", re.compile(r"(?<!\d)(?:[1-9]\d{3}[-\s]?){3,5}\d{3,4}(?!\d)"), "high", 0.72),
    ("secret", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,})\b"), "critical", 0.99),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "critical", 0.98),
    ("password", re.compile(r"(?i)\b(password|passwd|pwd|密[码碼]|口令)\s*[:=]\s*[^\s,;，。]{6,}"), "critical", 0.92),
    ("internal_url", re.compile(r"https?://(?:localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)[^\s]*"), "medium", 0.9),
    ("address", re.compile(r"[\u4e00-\u9fa5]{2,}(?:省|市|区|县|镇|街道|路|号楼|单元|室)"), "medium", 0.72),
    ("org", re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}(?:有限公司|股份有限公司|集团|银行|医院|学校|大学)"), "medium", 0.78),
]

URL_WITH_QUERY = re.compile(r"https?://[^\s]+?\?[^\s]+")


def redact_text(text: str) -> RedactionResult:
    redacted = _strip_url_queries(text)
    counters: Dict[str, int] = defaultdict(int)
    findings: List[RedactionFinding] = []

    for finding_type, pattern, risk_level, confidence in DETECTORS:
        redacted, detector_findings = _replace_pattern(
            redacted,
            pattern,
            finding_type,
            risk_level,
            confidence,
            counters,
        )
        findings.extend(detector_findings)

    residual_findings = scan_residual(redacted)
    privacy_risk_score = _risk_score(findings, residual_findings)
    return RedactionResult(
        redacted_text=redacted,
        findings=findings,
        residual_findings=residual_findings,
        privacy_risk_score=privacy_risk_score,
        passed=not residual_findings and privacy_risk_score < 0.8,
    )


def scan_residual(text: str) -> List[RedactionFinding]:
    residual: List[RedactionFinding] = []
    for finding_type, pattern, risk_level, confidence in DETECTORS:
        for _ in pattern.finditer(text):
            residual.append(
                RedactionFinding(
                    finding_type=finding_type,
                    placeholder=f"[RESIDUAL_{finding_type.upper()}]",
                    risk_level=risk_level,
                    confidence=confidence,
                )
            )
    if URL_WITH_QUERY.search(text):
        residual.append(
            RedactionFinding(
                finding_type="url_query",
                placeholder="[RESIDUAL_URL_QUERY]",
                risk_level="medium",
                confidence=0.9,
            )
        )
    return residual


def _replace_pattern(
    text: str,
    pattern: Pattern[str],
    finding_type: str,
    risk_level: str,
    confidence: float,
    counters: Dict[str, int],
) -> Tuple[str, List[RedactionFinding]]:
    findings: List[RedactionFinding] = []

    def replacement(_: re.Match[str]) -> str:
        counters[finding_type] += 1
        placeholder = f"[{finding_type.upper()}_{counters[finding_type]}]"
        findings.append(
            RedactionFinding(
                finding_type=finding_type,
                placeholder=placeholder,
                risk_level=risk_level,
                confidence=confidence,
            )
        )
        return placeholder

    return pattern.sub(replacement, text), findings


def _strip_url_queries(text: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        url = match.group(0)
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    return URL_WITH_QUERY.sub(replacement, text)


def _risk_score(findings: Iterable[RedactionFinding], residual_findings: Iterable[RedactionFinding]) -> float:
    score = 0.0
    weights = {"low": 0.05, "medium": 0.12, "high": 0.24, "critical": 0.45}
    for finding in findings:
        score += weights.get(finding.risk_level, 0.1) * finding.confidence
    for finding in residual_findings:
        score += weights.get(finding.risk_level, 0.1) * finding.confidence * 2
    return min(round(score, 4), 1.0)
