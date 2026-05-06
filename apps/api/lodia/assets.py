from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Dict, List, Optional

from .redaction import redact_text
from .serde import to_jsonable


TEXTUAL_MEDIA_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/x-ndjson",
    "application/yaml",
    "application/x-yaml",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/x-log",
    "text/xml",
}

IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
DOCUMENT_MEDIA_TYPES = {"application/pdf"}
AUDIO_MEDIA_TYPES = {"audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/webm"}
VIDEO_MEDIA_TYPES = {"video/mp4", "video/quicktime", "video/webm"}

BLOCKED_EXTENSIONS = {
    ".app",
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".dmg",
    ".exe",
    ".jar",
    ".js",
    ".msi",
    ".ps1",
    ".scr",
    ".sh",
}

MAX_TEXT_EXTRACT_BYTES = 512_000
PRINTABLE_RATIO_MIN = 0.85


@dataclass(frozen=True)
class AssetInspection:
    filename: str
    media_type: str
    asset_type: str
    byte_size: int
    sha256: str
    status: str
    metadata: Dict[str, Any]
    risk: Dict[str, Any]
    extracted_text: str
    redaction: Optional[Dict[str, Any]]


def inspect_asset(filename: str, media_type: Optional[str], content: bytes) -> AssetInspection:
    clean_name = sanitize_filename(filename)
    normalized_media_type = normalize_media_type(clean_name, media_type)
    asset_type = classify_asset(clean_name, normalized_media_type)
    digest = hashlib.sha256(content).hexdigest()
    risk_findings = _risk_findings(clean_name, normalized_media_type, content)
    extracted_text = _extract_text(asset_type, content)
    redaction = to_jsonable(redact_text(extracted_text)) if extracted_text else None
    status = _status(asset_type, risk_findings, extracted_text, redaction)
    metadata = {
        "filename": clean_name,
        "media_type": normalized_media_type,
        "asset_type": asset_type,
        "extractable_text": bool(extracted_text),
        "extracted_text_chars": len(extracted_text),
    }
    return AssetInspection(
        filename=clean_name,
        media_type=normalized_media_type,
        asset_type=asset_type,
        byte_size=len(content),
        sha256=digest,
        status=status,
        metadata=metadata,
        risk={
            "blocked": any(item["severity"] == "critical" for item in risk_findings),
            "findings": risk_findings,
        },
        extracted_text=extracted_text,
        redaction=redaction,
    )


def sanitize_filename(filename: str) -> str:
    candidate = PurePath(filename or "asset.bin").name.strip()
    candidate = re.sub(r"[^A-Za-z0-9._ -]+", "_", candidate)
    return candidate[:160] or "asset.bin"


def normalize_media_type(filename: str, media_type: Optional[str]) -> str:
    clean_media_type = (media_type or "").split(";", 1)[0].strip().lower()
    if clean_media_type and clean_media_type != "application/octet-stream":
        return clean_media_type
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or clean_media_type or "application/octet-stream"


def classify_asset(filename: str, media_type: str) -> str:
    suffix = PurePath(filename).suffix.lower()
    if media_type in TEXTUAL_MEDIA_TYPES or suffix in {".log", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".yaml", ".yml"}:
        return "text"
    if media_type in IMAGE_MEDIA_TYPES:
        return "image"
    if media_type in DOCUMENT_MEDIA_TYPES:
        return "pdf"
    if media_type in AUDIO_MEDIA_TYPES:
        return "audio"
    if media_type in VIDEO_MEDIA_TYPES:
        return "video"
    if suffix in {".trace", ".har"}:
        return "trace"
    return "binary"


def _risk_findings(filename: str, media_type: str, content: bytes) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    suffix = PurePath(filename).suffix.lower()
    if suffix in BLOCKED_EXTENSIONS:
        findings.append(
            {
                "type": "blocked_extension",
                "severity": "critical",
                "message": f"{suffix} files are not accepted as dataset evidence",
            }
        )
    if _has_executable_magic(content):
        findings.append(
            {
                "type": "executable_magic",
                "severity": "critical",
                "message": "binary executable content is not accepted",
            }
        )
    if media_type == "application/octet-stream" and suffix not in {".trace", ".har"}:
        findings.append(
            {
                "type": "unknown_media_type",
                "severity": "medium",
                "message": "unknown file type requires manual review",
            }
        )
    return findings


def _extract_text(asset_type: str, content: bytes) -> str:
    if asset_type not in {"text", "trace"}:
        return ""
    sample = content[:MAX_TEXT_EXTRACT_BYTES]
    if not _looks_textual(sample):
        return ""
    try:
        return sample.decode("utf-8")
    except UnicodeDecodeError:
        return sample.decode("utf-8", errors="replace")


def _looks_textual(content: bytes) -> bool:
    if not content:
        return False
    printable = 0
    for byte in content:
        if byte in {9, 10, 13} or 32 <= byte <= 126 or byte >= 128:
            printable += 1
    return printable / len(content) >= PRINTABLE_RATIO_MIN


def _has_executable_magic(content: bytes) -> bool:
    return content.startswith((b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"))


def _status(asset_type: str, risk_findings: List[Dict[str, Any]], extracted_text: str, redaction: Optional[Dict[str, Any]]) -> str:
    if any(item["severity"] == "critical" for item in risk_findings):
        return "rejected"
    if redaction and not redaction.get("passed", False):
        return "privacy_review"
    if extracted_text:
        return "evidence_ready"
    if asset_type in {"image", "pdf", "audio", "video"}:
        return "extraction_pending"
    return "manual_review"
