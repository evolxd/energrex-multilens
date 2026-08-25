"""Integrity rules for manually entered ENERGREX stock data.

Two independent gates, kept separate on purpose:

`trusted_override_value()` is the production gate `refresh_scores.py` calls to
decide whether an override's value reaches the scoring `data` dict. It only
requires `status == "verified"` and a finite numeric value. `status:"pending"`
used to be applied to production scoring identically to a real verified value
-- a genuine `pending` placeholder silently overriding mock_data.py's curated
figures (HANDOFF.md §7.1). This function is what stops that.

`audit_override_entry()` is the stricter, UI-facing gate app.py uses to decide
whether an entry is safe to badge as newly "verified" going forward -- it also
requires a traceable source and a valid, non-future `verified_at` date. It is
deliberately NOT what gates production scoring: of the 100 currently-verified
override entries, 95 predate the convention of recording a source and would
fail this check. Wiring it into `trusted_override_value` would silently drop
those 95 overrides' influence on scoring the next time refresh_scores.py runs
-- a large, invisible change to nearly every ticker's score as a side effect
of tightening a UI badge. app.py already has a separate `legacy_verified`
status for exactly this case, so an old entry without a source reads as
"legacy" in the UI rather than as untrusted or broken.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from typing import Any, Optional


TRUSTED_STATUS = "verified"
_EMPTY_SOURCE_MARKERS = {"", "n/a", "na", "none", "unknown", "待补", "待補"}


@dataclass(frozen=True)
class EntryAudit:
    trusted: bool
    issues: tuple[str, ...]


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _valid_source(value: Any) -> bool:
    source = str(value or "").strip()
    return len(source) >= 3 and source.lower() not in _EMPTY_SOURCE_MARKERS


def _parse_iso_date(value: Any) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def trusted_override_value(field: str, entry: Any) -> Optional[float]:
    """Production gate: the value refresh_scores.py is allowed to score with.

    entry should be {"value": ..., "status": ..., "verified_at": ...,
    "source": ...}. Only status == "verified" with a present, finite value is
    accepted; anything else (including "pending") returns None so the caller
    falls back to mock_data.py or the existing CSV value.
    """
    if not isinstance(entry, dict):
        return None
    if entry.get("status") != TRUSTED_STATUS:
        return None
    value = entry.get("value")
    if value is None or not _finite_number(value):
        return None
    return float(value)


def audit_override_entry(
    field: str,
    entry: Any,
    *,
    today: _dt.date | None = None,
) -> EntryAudit:
    """UI gate: whether one entry is complete enough to badge as verified.

    Stricter than trusted_override_value on purpose -- it also requires a
    traceable source and a valid, non-future verified_at date. Do not use
    this to gate production scoring; see the module docstring.
    """
    if not isinstance(entry, dict):
        return EntryAudit(False, (f"{field}: legacy entry has no verification metadata",))

    issues: list[str] = []
    if str(entry.get("status") or "").strip().lower() != TRUSTED_STATUS:
        issues.append(f"{field}: status is not verified")
    if not _finite_number(entry.get("value")):
        issues.append(f"{field}: value is missing or not finite")
    if not _valid_source(entry.get("source")):
        issues.append(f"{field}: verified value requires a traceable source")

    verified_at = _parse_iso_date(entry.get("verified_at"))
    if verified_at is None:
        issues.append(f"{field}: verified_at must be an ISO date")
    elif verified_at > (today or _dt.date.today()):
        issues.append(f"{field}: verified_at cannot be in the future")

    return EntryAudit(not issues, tuple(issues))


def audit_override_book(overrides: dict) -> dict:
    """Summarize override integrity (the strict, UI-facing gate) without
    mutating the source document."""
    total = trusted = 0
    issues: list[dict[str, Any]] = []
    for ticker, fields in (overrides or {}).items():
        if not isinstance(fields, dict):
            continue
        for field, entry in fields.items():
            total += 1
            result = audit_override_entry(field, entry)
            if result.trusted:
                trusted += 1
            else:
                issues.append({
                    "ticker": ticker,
                    "field": field,
                    "issues": list(result.issues),
                })
    return {
        "total_entries": total,
        "trusted_entries": trusted,
        "untrusted_entries": total - trusted,
        "trusted_rate": round(trusted / total, 4) if total else 0.0,
        "issues": issues,
    }
