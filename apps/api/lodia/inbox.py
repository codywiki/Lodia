from __future__ import annotations

import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from typing import Dict, Optional


MAX_INBOUND_TEXT_CHARS = 200_000


@dataclass(frozen=True)
class ParsedInboundMessage:
    subject: str
    body_text: str
    metadata: Dict[str, str]


def normalize_inbox_address(value: str, fallback_domain: str) -> str:
    clean = (value or "").strip().lower()
    if "@" not in clean:
        clean = f"{clean}@{fallback_domain}"
    local, domain = clean.rsplit("@", 1)
    local = re.sub(r"[^a-z0-9._+-]+", "-", local).strip(".-")
    domain = domain.strip(".")
    if not local or not domain or "." not in domain:
        raise ValueError("invalid_inbox_address")
    return f"{local[:80]}@{domain[:160]}"


def inbox_local_part(owner_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9._+-]+", "-", owner_id or "contributor").strip(".-").lower()
    return (clean or "contributor")[:48]


def parse_inbound_message(subject: str = "", body_text: str = "", raw_mime: Optional[bytes] = None) -> ParsedInboundMessage:
    if raw_mime:
        message = BytesParser(policy=policy.default).parsebytes(raw_mime)
        parsed_subject = str(message.get("subject") or subject or "")[:500]
        text_parts = []
        if message.is_multipart():
            for part in message.walk():
                content_type = part.get_content_type()
                disposition = (part.get_content_disposition() or "").lower()
                if disposition == "attachment":
                    continue
                if content_type == "text/plain":
                    try:
                        text_parts.append(str(part.get_content()))
                    except Exception:
                        continue
        elif message.get_content_type() == "text/plain":
            try:
                text_parts.append(str(message.get_content()))
            except Exception:
                text_parts.append(body_text or "")
        parsed_body = "\n\n".join(item.strip() for item in text_parts if item.strip()) or body_text or ""
        return ParsedInboundMessage(
            subject=parsed_subject,
            body_text=parsed_body[:MAX_INBOUND_TEXT_CHARS],
            metadata={
                "from": str(message.get("from") or ""),
                "to": str(message.get("to") or ""),
                "message_id": str(message.get("message-id") or ""),
            },
        )

    return ParsedInboundMessage(
        subject=(subject or "")[:500],
        body_text=(body_text or "")[:MAX_INBOUND_TEXT_CHARS],
        metadata={},
    )


def inbound_case_text(subject: str, body_text: str) -> str:
    subject = (subject or "").strip()
    body_text = (body_text or "").strip()
    if subject and body_text:
        return f"Subject: {subject}\n\n{body_text}"
    return body_text or subject
