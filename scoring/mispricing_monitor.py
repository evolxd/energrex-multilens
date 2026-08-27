"""Executable monitoring rules for ENERGREX investment theses."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from .mispricing_store import latest_case_for
    from .evidence import TRUSTED_STATUS, numeric_value
except ImportError:  # pragma: no cover - direct import path
    from mispricing_store import latest_case_for
    from evidence import TRUSTED_STATUS, numeric_value

# Same convention as scoring/input_verification.py -- literally the same
# TRUSTED_STATUS constant, from scoring/evidence.py -- only a "verified"
# value is trusted to drive a real conclusion. A thesis's metric_observations
# are hand-entered exactly like user_overrides.json entries, and an
# unconfirmed number must not be able to trigger EXIT/REDUCE on a real
# position -- it can only ever raise a "go check this" flag.


class Operator(str, Enum):
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    EQ = "EQ"
    NE = "NE"
    BETWEEN = "BETWEEN"


class CaseState(str, Enum):
    ACTIVE = "ACTIVE"
    HOLD = "HOLD"
    ADD_IF_PREAUTHORIZED = "ADD_IF_PREAUTHORIZED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FREEZE_ADDS = "FREEZE_ADDS"
    REVALUE = "REVALUE"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    REUNDERWRITE = "REUNDERWRITE"
    RISK_REDUCTION_REQUIRED = "RISK_REDUCTION_REQUIRED"


# CaseState's non-neutral outcomes, split into the two severities any UI that
# surfaces thesis_state_for_ticker's result wants to render differently.
# HOLD and ADD_IF_PREAUTHORIZED are deliberately absent from both: they mean
# the thesis is intact under price-only opposition, which is not something
# to alert on. Single source of truth -- every page that shows a thesis
# alert (仓位管理, 作战室) imports these instead of redefining them.
SEVERE_STATES = frozenset({"EXIT", "REDUCE", "RISK_REDUCTION_REQUIRED"})
WATCH_STATES = frozenset({"REVIEW_REQUIRED", "FREEZE_ADDS", "REVALUE", "REUNDERWRITE"})


@dataclass(frozen=True)
class TriggerResult:
    rule_id: str
    triggered: bool
    observed_values: tuple[float, ...]
    reason: str
    action: str
    severity: str = "FAIL"


def _finite(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def compare(
    value: float,
    operator: Operator,
    threshold: float,
    threshold_high: float | None = None,
) -> bool:
    if operator == Operator.LT:
        return value < threshold
    if operator == Operator.LTE:
        return value <= threshold
    if operator == Operator.GT:
        return value > threshold
    if operator == Operator.GTE:
        return value >= threshold
    if operator == Operator.EQ:
        return value == threshold
    if operator == Operator.NE:
        return value != threshold
    if threshold_high is None:
        raise ValueError("BETWEEN requires threshold_high")
    if threshold_high < threshold:
        raise ValueError("threshold_high must be >= threshold")
    return threshold <= value <= threshold_high


_TWO_TIER_OPERATORS = {Operator.LT, Operator.LTE, Operator.GT, Operator.GTE}


def validate_warning_tier(
    rule_id: str,
    operator: Operator,
    threshold: float,
    warning_threshold: float | None,
    action_on_warning: str | None,
) -> None:
    """A warning tier must be a strictly milder version of the same test.

    Both fields are paired (declare both or neither), restricted to the
    four directional operators (BETWEEN/EQ/NE have no well-defined "milder"
    side), and the warning threshold must sit on the side of the fail
    threshold that triggers first as the metric degrades.
    """
    if (warning_threshold is None) != (not action_on_warning):
        raise ValueError(
            f"{rule_id}: warning_threshold and action_on_warning must be declared together"
        )
    if warning_threshold is None:
        return
    if operator not in _TWO_TIER_OPERATORS:
        raise ValueError(
            f"{rule_id}: warning_threshold is only valid with LT, LTE, GT, or GTE"
        )
    if operator in (Operator.LT, Operator.LTE) and warning_threshold <= threshold:
        raise ValueError(
            f"{rule_id}: warning_threshold must be greater than threshold for {operator.value}"
        )
    if operator in (Operator.GT, Operator.GTE) and warning_threshold >= threshold:
        raise ValueError(
            f"{rule_id}: warning_threshold must be less than threshold for {operator.value}"
        )


def evaluate_rule(
    rule: Mapping[str, object],
    metric_history: Mapping[str, Sequence[float]],
) -> TriggerResult:
    rule_id = str(rule.get("rule_id") or "").strip()
    metric = str(rule.get("metric") or "").strip()
    action = str(rule.get("action") or "").strip().upper()
    if not rule_id or not metric or not action:
        raise ValueError("rule_id, metric, and action are required")
    try:
        operator = Operator(str(rule.get("operator")))
    except ValueError as exc:
        raise ValueError(f"{rule_id}: invalid operator") from exc
    threshold = _finite(rule.get("threshold"), f"{rule_id}.threshold")
    threshold_high = (
        _finite(rule.get("threshold_high"), f"{rule_id}.threshold_high")
        if rule.get("threshold_high") is not None
        else None
    )
    warning_threshold = (
        _finite(rule.get("warning_threshold"), f"{rule_id}.warning_threshold")
        if rule.get("warning_threshold") is not None
        else None
    )
    action_on_warning = (
        str(rule.get("action_on_warning") or "").strip().upper() or None
    )
    validate_warning_tier(rule_id, operator, threshold, warning_threshold, action_on_warning)
    consecutive_periods = int(rule.get("consecutive_periods") or 1)
    if consecutive_periods < 1:
        raise ValueError("consecutive_periods must be >= 1")
    values = tuple(
        _finite(value, f"{rule_id}.{metric}") for value in metric_history.get(metric, ())
    )
    if len(values) < consecutive_periods:
        return TriggerResult(
            rule_id,
            False,
            values,
            f"need {consecutive_periods} observations; found {len(values)}",
            action,
        )
    window = values[-consecutive_periods:]
    fail_triggered = all(
        compare(value, operator, threshold, threshold_high) for value in window
    )
    if fail_triggered:
        return TriggerResult(
            rule_id,
            True,
            window,
            f"{metric} met {operator.value} for {consecutive_periods} period(s)",
            action,
            "FAIL",
        )
    if warning_threshold is not None:
        warning_triggered = all(
            compare(value, operator, warning_threshold) for value in window
        )
        if warning_triggered:
            return TriggerResult(
                rule_id,
                True,
                window,
                f"{metric} met {operator.value} warning threshold for {consecutive_periods} period(s)",
                action_on_warning,
                "WARNING",
            )
    return TriggerResult(
        rule_id,
        False,
        window,
        f"{metric} did not meet {operator.value} for required periods",
        action,
    )


def evaluate_rules(
    rules: Iterable[Mapping[str, object]],
    metric_history: Mapping[str, Sequence[float]],
) -> tuple[TriggerResult, ...]:
    return tuple(evaluate_rule(rule, metric_history) for rule in rules)


@dataclass(frozen=True)
class ObservationSet:
    """A thesis's metric_observations, split by trust.

    verified_history feeds evaluate_rules exactly like a normal
    metric_history. pending entries never do -- a hand-entered number the
    user has not confirmed must not be able to trigger REDUCE/EXIT on a real
    position, only surface as "go check this" (see TRUSTED_STATUS docstring
    above). pending_by_metric exists so the indicator can name which metric
    needs attention rather than just a bare count.
    """

    verified_history: dict[str, list[float]] = field(default_factory=dict)
    pending_by_metric: dict[str, int] = field(default_factory=dict)

    @property
    def pending_count(self) -> int:
        return sum(self.pending_by_metric.values())


def split_observations(metric_observations: Mapping[str, Any]) -> ObservationSet:
    """Partition {metric: [{value, status, ...}, ...]} into trusted vs pending.

    Chronological order within each metric's list is preserved for the
    verified series, since evaluate_rule's consecutive_periods window reads
    the *last* N entries -- reordering here would silently change which
    periods a rule is judging.
    """
    verified: dict[str, list[float]] = {}
    pending: dict[str, int] = {}
    for metric, entries in (metric_observations or {}).items():
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            numeric = numeric_value(entry)
            if numeric is None:
                continue
            if str(entry.get("status") or "").strip().lower() == TRUSTED_STATUS:
                verified.setdefault(metric, []).append(numeric)
            else:
                pending[metric] = pending.get(metric, 0) + 1
    return ObservationSet(verified, pending)


def thesis_state_for_ticker(
    chain_path: str | Path,
    ticker: str,
    *,
    portfolio_or_option_limit_breached: bool = False,
) -> dict[str, Any] | None:
    """Current monitoring state for a ticker's latest thesis, or None if it
    has no case on file.

    Wires three pieces that already exist and work on their own --
    mispricing_store.latest_case_for, split_observations, evaluate_rules,
    route_case_state -- so a position screen can ask "is this thesis still
    holding up" without re-implementing any of the trigger logic.
    """
    case = latest_case_for(chain_path, ticker)
    if case is None:
        return None

    observations = split_observations(case.get("metric_observations") or {})
    rules = case.get("monitor_rules") or []
    trigger_results = evaluate_rules(rules, observations.verified_history)
    case_state = route_case_state(
        trigger_results,
        portfolio_or_option_limit_breached=portfolio_or_option_limit_breached,
    )

    return {
        "case_id": case.get("case_id"),
        "ticker": case.get("ticker"),
        "primary_path": case.get("opportunity_routing", {}).get("primary_path"),
        "case_state": case_state.value,
        "triggered_rules": [r for r in trigger_results if r.triggered],
        "pending_observation_count": observations.pending_count,
        "pending_by_metric": dict(observations.pending_by_metric),
        "next_validation_date": case.get("decision", {}).get("next_review_date"),
    }


def route_case_state(
    results: Iterable[TriggerResult],
    *,
    price_only_opposition: bool = False,
    add_pre_authorized: bool = False,
    realization_delayed: bool = False,
    portfolio_or_option_limit_breached: bool = False,
) -> CaseState:
    """Convert evidence changes into an executable V2.3 thesis state.

    Price weakness alone cannot create an exit or an add. Adds require an
    explicit pre-authorization recorded before the drawdown.
    """
    triggered = [result for result in results if result.triggered]
    actions = {result.action.upper() for result in triggered}
    if portfolio_or_option_limit_breached:
        return CaseState.RISK_REDUCTION_REQUIRED
    if "EXIT" in actions:
        return CaseState.EXIT
    if "REDUCE" in actions:
        return CaseState.REDUCE
    if "DOWNGRADE" in actions:
        return CaseState.REVALUE
    if "PAUSE_ADD" in actions:
        return CaseState.FREEZE_ADDS
    if "REVIEW" in actions:
        return CaseState.REVIEW_REQUIRED
    if realization_delayed:
        return CaseState.REUNDERWRITE
    if price_only_opposition:
        return (
            CaseState.ADD_IF_PREAUTHORIZED
            if add_pre_authorized
            else CaseState.HOLD
        )
    return CaseState.ACTIVE
