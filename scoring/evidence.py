"""Shared trust primitive for hand-entered data.

Both scoring/input_verification.py (AI 估值评分的人工覆盖值) and
scoring/mispricing_monitor.py (误价监控的 metric_observations) independently
arrived at the same shape for a hand-entered datum -- {value, status, ...}
with status == "verified" as the one string that means "trust this number".
This module holds just that narrow, shared piece.

It deliberately does NOT absorb input_verification.py's stricter
audit_override_entry gate (source + verified_at + non-future-date checks).
That gate solves a different problem -- UI badging discipline, with an
explicit historical carve-out for the 95 of 100 existing override entries
that predate the "record a source" convention (see input_verification.py's
module docstring). Folding that into this primitive would either weaken it
for input_verification's callers or silently start requiring a source from
metric_observations entries that were never designed to carry one.
"""
from __future__ import annotations

import math
from typing import Any, Optional

TRUSTED_STATUS = "verified"


def numeric_value(entry: Any) -> Optional[float]:
    """Extract entry["value"] as a finite float, independent of status.

    Split out from trusted_numeric_value because mispricing_monitor's
    split_observations needs to tell "not a number at all" (skip, don't
    count either way) apart from "a number that just isn't verified yet"
    (count as pending) -- a single status-gated check can't make that
    distinction since both cases return the same "not trusted" answer.
    """
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def trusted_numeric_value(entry: Any) -> Optional[float]:
    """Return entry["value"] as a float iff status == "verified" and the
    value is present and finite; None otherwise (including non-dict input).

    This is the one check both callers actually share. Everything past it --
    source traceability, verified_at validity, legacy carve-outs -- is
    specific to how each caller uses the number and stays where it was.
    """
    if not isinstance(entry, dict):
        return None
    if entry.get("status") != TRUSTED_STATUS:
        return None
    return numeric_value(entry)
