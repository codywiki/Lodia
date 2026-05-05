from __future__ import annotations

import hashlib
import re
from typing import Iterable, Set

from .domain import DedupResult


TOKEN_RE = re.compile(r"[\w\u4e00-\u9fa5]+", re.UNICODE)


def fingerprint(raw_text: str, redacted_text: str, known_hashes: Iterable[str] = ()) -> DedupResult:
    canonical = canonicalize(redacted_text)
    raw_hash = sha256_text(raw_text)
    canonical_hash = sha256_text(canonical)
    simhash_value = simhash(tokens(canonical))
    known: Set[str] = set(known_hashes)
    duplicate_status = "canonical_duplicate" if canonical_hash in known else "unique"
    novelty_score = 0.2 if duplicate_status != "unique" else 1.0
    return DedupResult(
        raw_hash=raw_hash,
        canonical_hash=canonical_hash,
        simhash=simhash_value,
        duplicate_status=duplicate_status,
        novelty_score=novelty_score,
    )


def canonicalize(text: str) -> str:
    normalized = text.lower()
    normalized = re.sub(r"\[[A-Z_]+_\d+\]", "[REDACTED]", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tokens(text: str) -> Iterable[str]:
    return TOKEN_RE.findall(text.lower())


def simhash(items: Iterable[str], bits: int = 64) -> int:
    vector = [0] * bits
    for item in items:
        digest = int(hashlib.sha256(item.encode("utf-8")).hexdigest(), 16)
        for index in range(bits):
            vector[index] += 1 if digest & (1 << index) else -1
    value = 0
    for index, weight in enumerate(vector):
        if weight > 0:
            value |= 1 << index
    return value


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()
