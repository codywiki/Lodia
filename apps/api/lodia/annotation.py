from __future__ import annotations

import re
from typing import Dict, Iterable, List

from .domain import AnnotationResult, RedactionResult


DOMAIN_KEYWORDS: Dict[str, Iterable[str]] = {
    "software_engineering": ("代码", "报错", "git", "api", "数据库", "部署", "python", "typescript", "bug", "stack trace"),
    "customer_service": ("客户", "售后", "投诉", "工单", "客服", "满意度"),
    "sales_marketing": ("线索", "转化", "文案", "广告", "私域", "投放", "增长"),
    "legal_finance_hr": ("合同", "发票", "报销", "招聘", "绩效", "财务", "法务"),
    "agent_trace": ("tool call", "function", "terminal", "browser", "stdout", "stderr", "执行记录"),
}

TASK_KEYWORDS: Dict[str, Iterable[str]] = {
    "debugging": ("报错", "bug", "异常", "修复", "debug", "stack trace"),
    "generation": ("生成", "撰写", "写一份", "起草", "输出"),
    "evaluation": ("评测", "打分", "比较", "benchmark", "验收"),
    "analysis": ("分析", "归因", "总结", "洞察", "复盘"),
    "workflow_execution": ("执行", "调用", "任务", "步骤", "trace"),
}


def annotate(redacted_text: str, redaction: RedactionResult) -> AnnotationResult:
    domain = _best_label(redacted_text, DOMAIN_KEYWORDS, "general_knowledge")
    task_type = _best_label(redacted_text, TASK_KEYWORDS, "qa_assistance")
    quality_score = score_quality(redacted_text, redaction)
    value_score = score_case_value(redacted_text, domain, task_type, quality_score)
    difficulty = "advanced" if quality_score >= 0.82 else "intermediate" if quality_score >= 0.62 else "basic"
    reuse_types = _reuse_types(task_type, domain, quality_score)
    confidence = min(0.95, 0.45 + quality_score * 0.45 + (0.1 if domain != "general_knowledge" else 0))
    return AnnotationResult(
        domain=domain,
        task_type=task_type,
        difficulty=difficulty,
        reuse_types=reuse_types,
        quality_score=round(quality_score, 4),
        confidence=round(confidence, 4),
        labels={
            "language": "zh-CN" if re.search(r"[\u4e00-\u9fa5]", redacted_text) else "en",
            "value_score": f"{value_score:.4f}",
            "value_tier": _value_tier(value_score),
        },
    )


def score_quality(redacted_text: str, redaction: RedactionResult) -> float:
    length = len(redacted_text.strip())
    context_score = min(length / 1800, 1.0)
    task_clarity = _contains_any(redacted_text, ("请", "帮", "如何", "需要", "目标", "要求", "问题", "任务"))
    result_feedback = _contains_any(redacted_text, ("结果", "验证", "通过", "失败", "反馈", "报错", "验收", "输出"))
    reusable = _contains_any(redacted_text, ("步骤", "流程", "规则", "标准", "场景", "案例", "评测", "数据集"))
    risk_penalty = redaction.privacy_risk_score * 0.15
    score = (
        context_score * 0.25
        + task_clarity * 0.2
        + result_feedback * 0.2
        + reusable * 0.2
        + 0.15
        - risk_penalty
    )
    return max(0.0, min(score, 1.0))


def score_case_value(redacted_text: str, domain: str, task_type: str, quality_score: float) -> float:
    has_context = _contains_any(redacted_text, ("背景", "上下文", "约束", "目标", "输入", "限制"))
    has_process = _contains_any(redacted_text, ("步骤", "过程", "调用", "工具", "trace", "执行"))
    has_outcome = _contains_any(redacted_text, ("结果", "验收", "通过", "失败", "反馈", "修正"))
    has_reuse = _contains_any(redacted_text, ("规则", "标准", "SOP", "可复用", "评测", "数据集"))
    domain_weight = 0.1 if domain in {"software_engineering", "customer_service", "agent_trace"} else 0.04
    task_weight = 0.08 if task_type in {"debugging", "evaluation", "workflow_execution"} else 0.03
    score = quality_score * 0.48 + has_context * 0.12 + has_process * 0.12 + has_outcome * 0.12 + has_reuse * 0.08 + domain_weight + task_weight
    return max(0.0, min(score, 1.0))


def _value_tier(score: float) -> str:
    if score >= 0.82:
        return "A"
    if score >= 0.66:
        return "B"
    if score >= 0.5:
        return "C"
    return "D"


def _best_label(text: str, keyword_map: Dict[str, Iterable[str]], fallback: str) -> str:
    lowered = text.lower()
    scores = {
        label: sum(1 for keyword in keywords if keyword.lower() in lowered)
        for label, keywords in keyword_map.items()
    }
    label, score = max(scores.items(), key=lambda item: item[1])
    return label if score > 0 else fallback


def _contains_any(text: str, keywords: Iterable[str]) -> float:
    lowered = text.lower()
    return 1.0 if any(keyword.lower() in lowered for keyword in keywords) else 0.0


def _reuse_types(task_type: str, domain: str, quality_score: float) -> List[str]:
    reuse = ["case_library"]
    if task_type in {"evaluation", "debugging", "workflow_execution"} and quality_score >= 0.55:
        reuse.append("eval_candidate")
    if domain in {"software_engineering", "customer_service", "sales_marketing"} and quality_score >= 0.72:
        reuse.append("training_candidate")
    return reuse
