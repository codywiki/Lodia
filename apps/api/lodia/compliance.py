from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List


CONTENT_SAFETY_VERSION = "cn-safety-rules-2026-05-06"


@dataclass(frozen=True)
class ComplianceFinding:
    category: str
    risk_level: str
    action: str
    evidence: str


@dataclass(frozen=True)
class ComplianceScreenResult:
    status: str
    risk_level: str
    action: str
    categories: List[str]
    findings: List[ComplianceFinding]
    policy_version: str = CONTENT_SAFETY_VERSION


IMPORTANT_DATA_PATTERNS = [
    ("important_data_candidate", "high", "review", re.compile(r"(?:10万|100000|百万|千万|上亿).{0,24}(?:用户|客户|订单|交易|病历|定位|轨迹|名单)")),
    ("bulk_personal_info", "high", "review", re.compile(r"(?:客户名单|通讯录|员工名单|用户画像|银行流水|交易明细|行踪轨迹)")),
    ("sensitive_industry", "high", "review", re.compile(r"(?:病历|诊断|处方|贷款|征信|证券账户|保险理赔|未成年人|儿童信息)")),
]

CONTENT_BLOCK_PATTERNS = [
    ("illegal_content", "critical", "block", re.compile(r"(?:制作|售卖|购买).{0,12}(?:毒品|枪支|爆炸物|假证|木马|恶意软件)")),
    ("credential_abuse", "critical", "block", re.compile(r"(?:绕过|破解|盗取|批量登录|撞库).{0,16}(?:密码|验证码|账号|token|cookie)")),
]


def screen_text_for_compliance(text: str) -> ComplianceScreenResult:
    findings: List[ComplianceFinding] = []
    sample = (text or "")[:200_000]
    for category, risk_level, action, pattern in [*CONTENT_BLOCK_PATTERNS, *IMPORTANT_DATA_PATTERNS]:
        for match in pattern.finditer(sample):
            evidence = match.group(0)
            findings.append(
                ComplianceFinding(
                    category=category,
                    risk_level=risk_level,
                    action=action,
                    evidence=evidence[:120],
                )
            )
            break

    if any(item.action == "block" for item in findings):
        action = "block"
        status = "failed"
        risk_level = "critical"
    elif findings:
        action = "review"
        status = "review_required"
        risk_level = "high"
    else:
        action = "allow"
        status = "passed"
        risk_level = "low"

    return ComplianceScreenResult(
        status=status,
        risk_level=risk_level,
        action=action,
        categories=sorted({item.category for item in findings}),
        findings=findings,
    )


def compliance_result_to_dict(result: ComplianceScreenResult) -> Dict[str, object]:
    return {
        "status": result.status,
        "risk_level": result.risk_level,
        "action": result.action,
        "categories": result.categories,
        "findings": [finding.__dict__ for finding in result.findings],
        "policy_version": result.policy_version,
    }
