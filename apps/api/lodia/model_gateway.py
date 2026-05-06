from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict


MODEL_GATEWAY_VERSION = "local-gateway-2026-05-06"


@dataclass(frozen=True)
class ExtractionResult:
    provider: str
    task_type: str
    status: str
    text: str
    metadata: Dict[str, Any]
    error: str = ""
    cost_micros: int = 0
    latency_ms: int = 0


def annotation_invocation(case: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "local_rules",
        "task_type": "structured_annotation",
        "entity_type": "case",
        "entity_id": case["case_id"],
        "status": "succeeded",
        "input_hash": _sha256(case["redacted_text"]),
        "output": {
            "model_gateway_version": MODEL_GATEWAY_VERSION,
            "annotation": case["annotation"],
            "quality_gate": case["quality_gate"],
        },
        "error": "",
        "cost_micros": 0,
        "latency_ms": 0,
    }


def extract_asset_text(asset_type: str, media_type: str, content: bytes) -> ExtractionResult:
    if asset_type == "pdf":
        text = _extract_printable_text(content)
        if text:
            return ExtractionResult(
                provider="local_pdf_text_layer",
                task_type="asset_text_extraction",
                status="succeeded",
                text=text,
                metadata={
                    "model_gateway_version": MODEL_GATEWAY_VERSION,
                    "method": "printable_text_layer",
                    "media_type": media_type,
                    "chars": len(text),
                },
            )
        return ExtractionResult(
            provider="unconfigured",
            task_type="asset_text_extraction",
            status="deferred",
            text="",
            metadata={
                "model_gateway_version": MODEL_GATEWAY_VERSION,
                "reason": "pdf_text_layer_unavailable",
                "required_worker": "ocr_or_pdf_parser",
            },
            error="extractor_not_configured",
        )

    if asset_type in {"image", "audio", "video"}:
        return ExtractionResult(
            provider="unconfigured",
            task_type="asset_text_extraction",
            status="deferred",
            text="",
            metadata={
                "model_gateway_version": MODEL_GATEWAY_VERSION,
                "reason": f"{asset_type}_extractor_required",
                "required_worker": {"image": "ocr", "audio": "asr", "video": "frame_ocr_asr"}[asset_type],
            },
            error="extractor_not_configured",
        )

    return ExtractionResult(
        provider="local_rules",
        task_type="asset_text_extraction",
        status="unsupported",
        text="",
        metadata={"model_gateway_version": MODEL_GATEWAY_VERSION, "reason": "asset_type_not_extractable"},
        error="asset_type_not_extractable",
    )


def extraction_invocation(asset_id: str, result: ExtractionResult) -> Dict[str, Any]:
    return {
        "provider": result.provider,
        "task_type": result.task_type,
        "entity_type": "asset",
        "entity_id": asset_id,
        "status": result.status,
        "input_hash": "",
        "output": result.metadata,
        "error": result.error,
        "cost_micros": result.cost_micros,
        "latency_ms": result.latency_ms,
    }


def _extract_printable_text(content: bytes) -> str:
    sample = content[:2_000_000]
    chunks = re.findall(rb"[\x09\x0a\x0d\x20-\x7e\x80-\xff]{8,}", sample)
    decoded = "\n".join(chunk.decode("utf-8", errors="ignore").strip() for chunk in chunks)
    decoded = re.sub(r"\s+", " ", decoded).strip()
    if len(decoded) < 24:
        return ""
    return decoded[:512_000]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
