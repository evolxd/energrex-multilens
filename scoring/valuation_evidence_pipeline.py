"""Point-in-time evidence pipeline for ENERGREX formal valuation.

The pipeline is deliberately provider agnostic. Fetchers may collect values,
but only this module may promote observations into a LIVE valuation field.
Promotion requires two genuinely different provider families, timestamp
integrity, matching units, and agreement inside a field-specific tolerance.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from scoring.unified_valuation import (
    FIELD_CROSSCHECK_LIMITS,
    FIELD_CROSSCHECK_MODES,
    PROFILE_SPECS,
    ValuationProfile,
)


PIPELINE_VERSION = "VALUATION_EVIDENCE_1.0"
ALLOWED_SOURCE_TYPES = {"PRIMARY", "MARKET", "CONSENSUS", "MODEL"}
ALLOWED_CROSS_CHECK_MODES = {"INDEPENDENT_ORIGIN", "INDEPENDENT_EXTRACTION"}

# Relative differences. These are reconciliation limits, not estimates of
# economic uncertainty. A conflict must be investigated instead of averaged.
FIELD_TOLERANCES = FIELD_CROSSCHECK_LIMITS


class EvidenceStatus(str, Enum):
    VERIFIED = "VERIFIED"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    CONFLICTED = "CONFLICTED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class SourceObservation:
    field: str
    value: float
    unit: str
    source: str
    source_type: str
    source_family: str
    origin_family: str
    lineage_id: str
    source_locator: str
    observed_at: dt.datetime
    available_at: dt.datetime
    retrieved_at: dt.datetime
    extraction_method: str


def _timestamp(value: Any, field: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _profile(value: Any) -> ValuationProfile:
    try:
        return value if isinstance(value, ValuationProfile) else ValuationProfile(str(value))
    except ValueError as exc:
        raise ValueError(f"Unsupported valuation profile: {value}") from exc


def parse_observation(payload: Mapping[str, Any]) -> SourceObservation:
    """Parse one immutable source observation without supplying defaults."""
    source_type = _text(payload.get("source_type"), "source_type").upper()
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise ValueError(f"source_type must be one of {sorted(ALLOWED_SOURCE_TYPES)}")
    observed = _timestamp(payload.get("observed_at"), "observed_at")
    available = _timestamp(payload.get("available_at"), "available_at")
    retrieved = _timestamp(payload.get("retrieved_at"), "retrieved_at")
    if observed > available:
        raise ValueError("observed_at cannot be after available_at")
    if available > retrieved:
        raise ValueError("available_at cannot be after retrieved_at")
    return SourceObservation(
        field=_text(payload.get("field"), "field"),
        value=_finite(payload.get("value"), "value"),
        unit=_text(payload.get("unit"), "unit"),
        source=_text(payload.get("source"), "source"),
        source_type=source_type,
        source_family=_text(payload.get("source_family"), "source_family").upper(),
        origin_family=_text(payload.get("origin_family"), "origin_family").upper(),
        lineage_id=_text(payload.get("lineage_id"), "lineage_id"),
        source_locator=_text(payload.get("source_locator"), "source_locator"),
        observed_at=observed,
        available_at=available,
        retrieved_at=retrieved,
        extraction_method=_text(payload.get("extraction_method"), "extraction_method"),
    )


def _relative_difference(left: float, right: float) -> float:
    denominator = max(abs(left), abs(right), 1e-12)
    return abs(left - right) / denominator


def _observation_payload(item: SourceObservation) -> dict[str, Any]:
    return {
        "field": item.field,
        "value": item.value,
        "unit": item.unit,
        "source": item.source,
        "source_type": item.source_type,
        "source_family": item.source_family,
        "origin_family": item.origin_family,
        "lineage_id": item.lineage_id,
        "source_locator": item.source_locator,
        "observed_at": item.observed_at.isoformat(),
        "available_at": item.available_at.isoformat(),
        "retrieved_at": item.retrieved_at.isoformat(),
        "extraction_method": item.extraction_method,
    }


def _source_rank(field: str, item: SourceObservation) -> tuple[int, float]:
    if field == "current_price":
        order = {"MARKET": 0, "PRIMARY": 1, "CONSENSUS": 2, "MODEL": 3}
    elif field in {"forward_eps", "forward_revenue"}:
        order = {"CONSENSUS": 0, "PRIMARY": 1, "MARKET": 2, "MODEL": 3}
    else:
        order = {"PRIMARY": 0, "MARKET": 1, "CONSENSUS": 2, "MODEL": 3}
    return (
        order.get(item.source_type, 9),
        -item.available_at.timestamp(),
    )


def _resolve_reconciliation_params(
    field: str,
    as_of: dt.datetime | str,
    tolerance: float | None,
    cross_check_mode: str | None,
) -> tuple[dt.datetime, float, str]:
    """Resolve and validate (cutoff, tolerance, cross_check_mode) for one
    field. Raises ValueError with the same messages as the original inline
    block on any invalid combination."""
    cutoff = _timestamp(as_of, "as_of") if not isinstance(as_of, dt.datetime) else as_of
    if cutoff.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    cutoff = cutoff.astimezone(dt.timezone.utc)
    limit = FIELD_TOLERANCES.get(field) if tolerance is None else tolerance
    if limit is None:
        raise ValueError(f"No reconciliation tolerance configured for {field}")
    if not 0 <= limit <= 0.25:
        raise ValueError("tolerance must be between 0 and 0.25")
    mode = (
        cross_check_mode
        or FIELD_CROSSCHECK_MODES[field]
    ).upper()
    if mode not in ALLOWED_CROSS_CHECK_MODES:
        raise ValueError(
            f"cross_check_mode must be one of {sorted(ALLOWED_CROSS_CHECK_MODES)}"
        )
    return cutoff, limit, mode


def _admit_observations(
    observations: Iterable[SourceObservation | Mapping[str, Any]],
    *,
    field: str,
    expected_unit: str,
    cutoff: dt.datetime,
    max_age_days: int,
) -> tuple[list[SourceObservation], list[dict[str, Any]]]:
    """Parse and filter raw observations into (accepted, rejected). Logic
    copied verbatim from the original inline block, including catching a
    parse_observation ValueError as a rejection rather than propagating it."""
    accepted: list[SourceObservation] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(observations):
        try:
            item = raw if isinstance(raw, SourceObservation) else parse_observation(raw)
            reasons: list[str] = []
            if item.field != field:
                reasons.append("FIELD_MISMATCH")
            if item.unit != expected_unit:
                reasons.append("UNIT_MISMATCH")
            if item.available_at > cutoff or item.retrieved_at > cutoff:
                reasons.append("LOOKAHEAD")
            age = (cutoff - item.available_at).total_seconds() / 86400
            if age > max_age_days:
                reasons.append("STALE")
            if reasons:
                rejected.append(
                    {"index": index, "source": item.source, "reasons": reasons}
                )
            else:
                accepted.append(item)
        except ValueError as exc:
            rejected.append({"index": index, "source": None, "reasons": [str(exc)]})
    return accepted, rejected


def _find_corroborating_pairs(
    accepted: list[SourceObservation],
    *,
    mode: str,
    limit: float,
) -> list[tuple[float, SourceObservation, SourceObservation]]:
    """Find every pair of accepted observations that independently
    corroborate each other within tolerance. Logic copied verbatim from
    the original nested-loop block."""
    pairs: list[tuple[float, SourceObservation, SourceObservation]] = []
    for left_index, left in enumerate(accepted):
        for right in accepted[left_index + 1 :]:
            if left.source_family == right.source_family:
                continue
            if left.source_locator == right.source_locator:
                continue
            if (
                mode == "INDEPENDENT_ORIGIN"
                and left.origin_family == right.origin_family
            ):
                continue
            if (
                mode == "INDEPENDENT_EXTRACTION"
                and left.extraction_method == right.extraction_method
            ):
                continue
            difference = _relative_difference(left.value, right.value)
            if difference <= limit:
                pairs.append((difference, left, right))
    return pairs


def _evidence_gap_result(
    field: str,
    mode: str,
    accepted: list[SourceObservation],
    rejected: list[dict[str, Any]],
    families: list[str],
    limit: float,
) -> dict[str, Any]:
    """Build the NEEDS_EVIDENCE/CONFLICTED envelope for when no corroborating
    pair was found. Logic copied verbatim from the original inline block,
    including that a pair excluded solely for sharing a source_locator is
    indistinguishable here from a pair that was compared and disagreed --
    both surface as CONFLICTED once families/origins/extraction_methods
    each have >=2 distinct values (not fixed here, out of scope)."""
    origins = sorted({item.origin_family for item in accepted})
    extraction_methods = sorted({item.extraction_method for item in accepted})
    lacks_independence = (
        len(families) < 2
        or (mode == "INDEPENDENT_ORIGIN" and len(origins) < 2)
        or (
            mode == "INDEPENDENT_EXTRACTION"
            and len(extraction_methods) < 2
        )
    )
    status = (
        EvidenceStatus.NEEDS_EVIDENCE
        if lacks_independence
        else EvidenceStatus.CONFLICTED
    )
    return {
        "field": field,
        "status": status.value,
        "cross_check_mode": mode,
        "record": None,
        "accepted_count": len(accepted),
        "independent_source_families": families,
        "origin_families": origins,
        "rejected": rejected,
        "reason": (
            f"{mode} requirements are not satisfied"
            if status == EvidenceStatus.NEEDS_EVIDENCE
            else f"independent observations disagree beyond {limit:.2%}"
        ),
        "observations": [_observation_payload(item) for item in accepted],
    }


def _select_best_pair(
    field: str,
    pairs: list[tuple[float, SourceObservation, SourceObservation]],
) -> tuple[float, SourceObservation, SourceObservation]:
    """Pick the strongest corroborating pair: best (lowest) source rank
    first, then smallest relative difference, then a deterministic
    family-name tie-break on the pair's own left/right slots. Logic copied
    verbatim from the original inline `min(...)` call."""
    return min(
        pairs,
        key=lambda pair: (
            min(_source_rank(field, pair[1]), _source_rank(field, pair[2])),
            pair[0],
            pair[1].source_family,
            pair[2].source_family,
        ),
    )


def _build_verified_result(
    field: str,
    mode: str,
    difference: float,
    left: SourceObservation,
    right: SourceObservation,
    accepted: list[SourceObservation],
    rejected: list[dict[str, Any]],
    limit: float,
    families: list[str],
) -> dict[str, Any]:
    """Order the winning pair into primary/secondary by source rank and
    build the VERIFIED envelope. Logic copied verbatim from the original
    inline block."""
    primary, secondary = sorted(
        (left, right),
        key=lambda item: _source_rank(field, item),
    )
    record = {
        "value": primary.value,
        "unit": primary.unit,
        "source": primary.source,
        "source_type": primary.source_type,
        "source_family": primary.source_family,
        "origin_family": primary.origin_family,
        "lineage_id": primary.lineage_id,
        "source_locator": primary.source_locator,
        "observed_at": primary.observed_at.isoformat(),
        "available_at": primary.available_at.isoformat(),
        "retrieved_at": primary.retrieved_at.isoformat(),
        "extraction_method": primary.extraction_method,
        "verification": {
            "status": EvidenceStatus.VERIFIED.value,
            "secondary_source": secondary.source,
            "secondary_source_family": secondary.source_family,
            "secondary_origin_family": secondary.origin_family,
            "secondary_lineage_id": secondary.lineage_id,
            "secondary_source_locator": secondary.source_locator,
            "secondary_extraction_method": secondary.extraction_method,
            "secondary_available_at": secondary.available_at.isoformat(),
            "secondary_retrieved_at": secondary.retrieved_at.isoformat(),
            "secondary_value": secondary.value,
            "relative_difference": difference,
            "tolerance": limit,
            "cross_check_mode": mode,
        },
    }
    return {
        "field": field,
        "status": EvidenceStatus.VERIFIED.value,
        "cross_check_mode": mode,
        "record": record,
        "accepted_count": len(accepted),
        "independent_source_families": families,
        "rejected": rejected,
        "reason": None,
    }


def reconcile_field(
    field: str,
    observations: Iterable[SourceObservation | Mapping[str, Any]],
    *,
    expected_unit: str,
    as_of: dt.datetime | str,
    max_age_days: int,
    tolerance: float | None = None,
    cross_check_mode: str | None = None,
) -> dict[str, Any]:
    """Reconcile one field without averaging conflicts or inventing values."""
    cutoff, limit, mode = _resolve_reconciliation_params(
        field, as_of, tolerance, cross_check_mode
    )

    accepted, rejected = _admit_observations(
        observations,
        field=field,
        expected_unit=expected_unit,
        cutoff=cutoff,
        max_age_days=max_age_days,
    )

    if not accepted:
        return {
            "field": field,
            "status": EvidenceStatus.INVALID.value,
            "record": None,
            "accepted_count": 0,
            "independent_source_families": [],
            "rejected": rejected,
            "reason": "no valid point-in-time observations",
        }

    pairs = _find_corroborating_pairs(accepted, mode=mode, limit=limit)
    families = sorted({item.source_family for item in accepted})

    if not pairs:
        return _evidence_gap_result(field, mode, accepted, rejected, families, limit)

    difference, left, right = _select_best_pair(field, pairs)
    return _build_verified_result(
        field, mode, difference, left, right, accepted, rejected, limit, families
    )


def build_evidence_bundle(
    profile: ValuationProfile | str,
    observations: Iterable[SourceObservation | Mapping[str, Any]],
    *,
    as_of: dt.datetime | str,
) -> dict[str, Any]:
    """Build all profile fields and return a fail-closed readiness envelope."""
    selected_profile = _profile(profile)
    parsed: list[SourceObservation] = []
    parse_errors: list[str] = []
    for index, raw in enumerate(observations):
        try:
            parsed.append(raw if isinstance(raw, SourceObservation) else parse_observation(raw))
        except ValueError as exc:
            parse_errors.append(f"observation[{index}]: {exc}")

    fields: dict[str, Any] = {}
    results: dict[str, Any] = {}
    for field, spec in PROFILE_SPECS[selected_profile].items():
        result = reconcile_field(
            field,
            [item for item in parsed if item.field == field],
            expected_unit=spec.unit,
            as_of=as_of,
            max_age_days=spec.max_age_days,
        )
        results[field] = result
        if result["record"] is not None:
            fields[field] = result["record"]

    blocking = [
        f"{field}: {result['status']} — {result['reason']}"
        for field, result in results.items()
        if result["status"] != EvidenceStatus.VERIFIED.value
    ]
    blocking.extend(parse_errors)
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "profile": selected_profile.value,
        "as_of": _timestamp(as_of, "as_of").isoformat()
        if not isinstance(as_of, dt.datetime)
        else as_of.astimezone(dt.timezone.utc).isoformat(),
        "fields": fields,
        "field_results": results,
        "blocking_reasons": blocking,
    }
    snapshot = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        .encode("utf-8")
    ).hexdigest()
    return {
        **payload,
        "evidence_snapshot_id": snapshot,
        "ready_for_live_valuation": not blocking,
    }


def build_live_valuation_request(
    *,
    valuation_case_id: str,
    ticker: str,
    profile: ValuationProfile | str,
    as_of: dt.datetime | str,
    observations: Iterable[SourceObservation | Mapping[str, Any]],
    scenarios: Mapping[str, Any],
    realization_months: int,
    scenario_probabilities: Mapping[str, float] | None = None,
    dispersion_reconciliation: str = "",
) -> dict[str, Any]:
    """Return a request envelope; a blocked bundle never becomes LIVE input."""
    cutoff = _timestamp(as_of, "as_of") if not isinstance(as_of, dt.datetime) else as_of
    if cutoff.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    bundle = build_evidence_bundle(profile, observations, as_of=cutoff)
    if not bundle["ready_for_live_valuation"]:
        return {
            "status": "BLOCKED",
            "request": None,
            "evidence": bundle,
        }
    request = {
        "schema_version": "1.0",
        "evidence_mode": "LIVE",
        "valuation_case_id": _text(valuation_case_id, "valuation_case_id"),
        "ticker": _text(ticker, "ticker").upper(),
        "profile": _profile(profile).value,
        "as_of": cutoff.astimezone(dt.timezone.utc).isoformat(),
        "as_of_date": cutoff.date().isoformat(),
        "fields": bundle["fields"],
        "scenarios": dict(scenarios),
        "realization_months": int(realization_months),
        "evidence_snapshot_id": bundle["evidence_snapshot_id"],
    }
    if scenario_probabilities is not None:
        request["scenario_probabilities"] = dict(scenario_probabilities)
    if dispersion_reconciliation:
        request["dispersion_reconciliation"] = dispersion_reconciliation
    return {
        "status": "READY",
        "request": request,
        "evidence": bundle,
    }


def audit_legacy_valuation_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Explain why a legacy flat row is not formal valuation evidence."""
    issues: list[str] = []
    required_metadata = {
        "available_at",
        "retrieved_at",
        "source_family",
        "source_locator",
        "secondary_source_family",
    }
    missing = sorted(key for key in required_metadata if not row.get(key))
    if missing:
        issues.append("missing provenance metadata: " + ", ".join(missing))
    source_tags = {
        str(value).strip().lower()
        for key, value in row.items()
        if key.startswith("source_urls_") and str(value or "").strip()
    }
    if source_tags:
        issues.append(
            "source URLs are row-level references and do not prove field-level cross-validation"
        )
    return {
        "status": "REVIEW_REQUIRED" if issues else "NEEDS_FIELD_MAPPING",
        "eligible_for_live_valuation": False,
        "issues": issues or ["legacy row requires explicit field-level observation mapping"],
    }
