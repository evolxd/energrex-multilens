"""Evidence integrity for ENERGREX mispricing cases."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class EvidenceStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PENDING = "PENDING"
    CONFLICTED = "CONFLICTED"


class SourceTier(str, Enum):
    PRIMARY_FILING = "PRIMARY_FILING"
    REGULATOR = "REGULATOR"
    BROKER = "BROKER"
    MARKET_DATA = "MARKET_DATA"
    MODEL = "MODEL"
    SECONDARY = "SECONDARY"
    MANUAL = "MANUAL"


STRONG_SOURCE_TIERS = {
    SourceTier.PRIMARY_FILING,
    SourceTier.REGULATOR,
    SourceTier.BROKER,
    SourceTier.MARKET_DATA,
    SourceTier.MODEL,
}


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    claim: str
    source: str
    source_tier: SourceTier
    status: EvidenceStatus
    as_of: dt.date
    verified_at: dt.date
    expires_at: dt.date | None = None


@dataclass(frozen=True)
class EvidenceAudit:
    trusted: bool
    strong: bool
    issues: tuple[str, ...]


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _date(value: Any, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value or "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def parse_evidence_book(
    raw_records: Iterable[Mapping[str, Any]],
) -> dict[str, EvidenceRecord]:
    book: dict[str, EvidenceRecord] = {}
    for item in raw_records:
        evidence_id = _text(item.get("evidence_id"), "evidence.evidence_id")
        if evidence_id in book:
            raise ValueError(f"Duplicate evidence_id: {evidence_id}")
        try:
            source_tier = SourceTier(str(item.get("source_tier")))
            status = EvidenceStatus(str(item.get("status")))
        except ValueError as exc:
            raise ValueError(f"Invalid evidence status or source tier: {item}") from exc
        expires_raw = item.get("expires_at")
        record = EvidenceRecord(
            evidence_id=evidence_id,
            claim=_text(item.get("claim"), f"{evidence_id}.claim"),
            source=_text(item.get("source"), f"{evidence_id}.source"),
            source_tier=source_tier,
            status=status,
            as_of=_date(item.get("as_of"), f"{evidence_id}.as_of"),
            verified_at=_date(item.get("verified_at"), f"{evidence_id}.verified_at"),
            expires_at=(
                _date(expires_raw, f"{evidence_id}.expires_at")
                if expires_raw not in (None, "")
                else None
            ),
        )
        book[evidence_id] = record
    if not book:
        raise ValueError("At least one evidence record is required")
    return book


def audit_evidence(
    record: EvidenceRecord,
    *,
    today: dt.date | None = None,
) -> EvidenceAudit:
    current_date = today or dt.date.today()
    issues: list[str] = []
    if record.status != EvidenceStatus.VERIFIED:
        issues.append(f"status is {record.status.value}")
    if record.as_of > current_date:
        issues.append("as_of is in the future")
    if record.verified_at > current_date:
        issues.append("verified_at is in the future")
    if record.verified_at < record.as_of:
        issues.append("verified_at precedes as_of")
    if record.expires_at is not None:
        if record.expires_at < record.as_of:
            issues.append("expires_at precedes as_of")
        elif record.expires_at < current_date:
            issues.append("evidence is expired")
    return EvidenceAudit(
        trusted=not issues,
        strong=not issues and record.source_tier in STRONG_SOURCE_TIERS,
        issues=tuple(issues),
    )


def evidence_quality(
    evidence_ids: Iterable[str],
    book: Mapping[str, EvidenceRecord],
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    unique_ids = tuple(dict.fromkeys(str(value) for value in evidence_ids))
    trusted = strong = 0
    issues: list[str] = []
    for evidence_id in unique_ids:
        record = book.get(evidence_id)
        if record is None:
            issues.append(f"{evidence_id}: evidence record is missing")
            continue
        audit = audit_evidence(record, today=today)
        trusted += int(audit.trusted)
        strong += int(audit.strong)
        issues.extend(f"{evidence_id}: {issue}" for issue in audit.issues)
    total = len(unique_ids)
    return {
        "referenced_count": total,
        "trusted_count": trusted,
        "strong_count": strong,
        "validity_rate": trusted / total if total else 0.0,
        "strong_source_rate": strong / total if total else 0.0,
        "issues": tuple(issues),
    }
