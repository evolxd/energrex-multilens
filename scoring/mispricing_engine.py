"""ENERGREX mispricing discovery and special-situations gate engine.

This upstream engine never calculates a second investment score.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from scoring.mispricing_evidence import (
    EvidenceRecord,
    evidence_quality,
    parse_evidence_book,
)
from scoring.mispricing_monitor import Operator, validate_warning_tier
from scoring.mispricing_special_situations import (
    LiquidationResult,
    as_liquidation_dict,
    calculate_liquidation_value,
)


# V2.5: requires value_capture and survival_gate on every case. The
# adapters that build the downstream valuation request always read
# case.get("value_capture")/case.get("survival_gate"), but the schema never
# declared either field, so every case fed them an empty dict silently --
# the SURVIVAL and VALUE_CAPTURE gates were asserted PASS/FAIL by the case
# author with no structured numbers backing the claim. See
# mispricing_adapters.build_valuation_request().
#
# V2.6: requires conviction_protocol on every case (MISPRICING_ENGINE_STANDARD
# section "principle adherence and falsification protocol"). This does not
# score -- it is the pre-committed, written-at-thesis-creation record of what
# price weakness is tolerable, what fact changes force an exit, and what
# capital/liquidity/position limits govern this specific thesis, so that a
# real drawdown gets mechanically routed against a standing decision instead
# of re-litigated from scratch under pressure. capital_structure_stop,
# liquidity_stop, and every fact_change_thresholds entry must reference a
# rule_id that already exists in the case's own monitor_rules, so the
# commitment is enforced by the same executable rule it is written against
# rather than being a second, unmonitored promise.
ENGINE_VERSION = "2.6"


class OpportunityPath(str, Enum):
    GREAT_BUSINESS_STUMBLE = "GREAT_BUSINESS_STUMBLE"
    BUYBACK_TIME_MACHINE = "BUYBACK_TIME_MACHINE"
    HIDDEN_SCARCE_ASSET = "HIDDEN_SCARCE_ASSET"
    CAPITAL_ALLOCATION_RERATING = "CAPITAL_ALLOCATION_RERATING"
    IMPLIED_EXPECTATIONS_GAP = "IMPLIED_EXPECTATIONS_GAP"
    LIQUIDATION_AND_ASSET_ARBITRAGE = "LIQUIDATION_AND_ASSET_ARBITRAGE"


class StyleLineage(str, Enum):
    GRAHAM_LIQUIDATION = "GRAHAM_LIQUIDATION"
    SPECIAL_SITUATION_ARBITRAGE = "SPECIAL_SITUATION_ARBITRAGE"
    QUALITY_AT_REASONABLE_PRICE = "QUALITY_AT_REASONABLE_PRICE"
    GREAT_BUSINESS_TEMPORARY_TROUBLE = "GREAT_BUSINESS_TEMPORARY_TROUBLE"
    CONTROL_CAPITAL_ALLOCATION = "CONTROL_CAPITAL_ALLOCATION"
    BUYBACK_COMPOUNDING = "BUYBACK_COMPOUNDING"


class GateName(str, Enum):
    BUSINESS_TRUTH = "BUSINESS_TRUTH"
    SURVIVAL = "SURVIVAL"
    MISPRICING = "MISPRICING"
    VALUE_CAPTURE = "VALUE_CAPTURE"
    PRICE_ODDS = "PRICE_ODDS"
    LIQUIDATION = "LIQUIDATION"


class GateStatus(str, Enum):
    PASS = "PASS"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    FAIL = "FAIL"


class ResearchDecision(str, Enum):
    DEEP_RESEARCH_P0 = "DEEP_RESEARCH_P0"
    DEEP_RESEARCH_P1 = "DEEP_RESEARCH_P1"
    WATCH_P2 = "WATCH_P2"
    WAIT_FOR_PRICE = "WAIT_FOR_PRICE"
    REJECT_P3 = "REJECT_P3"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


HARD_REJECTION_GATES = {
    GateName.BUSINESS_TRUTH,
    GateName.SURVIVAL,
    GateName.MISPRICING,
    GateName.VALUE_CAPTURE,
    GateName.LIQUIDATION,
}
# Only these five gates are self-declared in a case's `gates` array and
# checked against linked evidence via _parse_gates. LIQUIDATION is computed
# by the engine from scoring/mispricing_special_situations.py and folded
# into the final gates tuple inside evaluate_mispricing_case — it is never
# hand-declared, so it is intentionally excluded here.
REQUIRED_GATES = (
    GateName.BUSINESS_TRUTH,
    GateName.SURVIVAL,
    GateName.MISPRICING,
    GateName.VALUE_CAPTURE,
    GateName.PRICE_ODDS,
)
ALLOWED_TRIGGER_ACTIONS = {"REVIEW", "PAUSE_ADD", "DOWNGRADE", "REDUCE", "EXIT"}


@dataclass(frozen=True)
class GateResult:
    name: GateName
    status: GateStatus
    reason: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Thesis:
    market_believes: str
    variant_view: str
    because: str
    falsifiable_by: str


@dataclass(frozen=True)
class MonitorRule:
    rule_id: str
    gate: GateName
    metric: str
    operator: Operator
    threshold: float
    threshold_high: float | None
    consecutive_periods: int
    action: str
    warning_threshold: float | None = None
    action_on_warning: str | None = None


@dataclass(frozen=True)
class MispricingDecision:
    engine_version: str
    schema_version: str
    ticker: str
    primary_path: OpportunityPath
    secondary_paths: tuple[OpportunityPath, ...]
    style_lineage: StyleLineage
    decision: ResearchDecision
    confidence: Confidence
    evidence_validity_rate: float
    strong_source_rate: float
    thesis: Thesis
    gates: tuple[GateResult, ...]
    evidence: tuple[EvidenceRecord, ...]
    monitor_rules: tuple[MonitorRule, ...]
    quick_reject: GateResult
    payer_economics: Mapping[str, Any]
    conviction_protocol: Mapping[str, Any]
    liquidation_result: LiquidationResult | None
    blocking_reasons: tuple[str, ...]

    @property
    def eligible_for_energrex_research(self) -> bool:
        return self.decision in {
            ResearchDecision.DEEP_RESEARCH_P0,
            ResearchDecision.DEEP_RESEARCH_P1,
        }


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _path(value: Any, field: str) -> OpportunityPath:
    if isinstance(value, OpportunityPath):
        return value
    try:
        return OpportunityPath(str(value))
    except ValueError as exc:
        raise ValueError(f"{field} has unsupported opportunity path: {value}") from exc


def _style_lineage(value: Any) -> StyleLineage:
    if isinstance(value, StyleLineage):
        return value
    try:
        return StyleLineage(str(value))
    except ValueError as exc:
        raise ValueError(f"Unsupported style_lineage: {value}") from exc


def _evaluate_quick_reject(
    raw: Any,
    *,
    primary_path: OpportunityPath,
) -> GateResult:
    if not isinstance(raw, Mapping):
        return GateResult(
            GateName.BUSINESS_TRUTH,
            GateStatus.NEEDS_EVIDENCE,
            "quick_reject object is missing",
            (),
        )
    positive_fields = [
        "business_explainable_150_words",
        "payer_identified",
        "price_setter_identified",
        "core_cash_flow_verifiable",
        "liquidity_runway_36m",
        "common_equity_capture_verifiable",
        "thesis_beyond_famous_holder_or_short_interest",
        "source_quality_sufficient",
    ]
    negative_fields = ["forced_refinancing_risk", "forced_dilution_risk"]
    if primary_path in {
        OpportunityPath.HIDDEN_SCARCE_ASSET,
        OpportunityPath.LIQUIDATION_AND_ASSET_ARBITRAGE,
    }:
        positive_fields.append("asset_title_and_transferability_verifiable")
    if primary_path == OpportunityPath.LIQUIDATION_AND_ASSET_ARBITRAGE:
        positive_fields.append("liquidation_hidden_liabilities_estimable")

    failures = [field for field in positive_fields if raw.get(field) is False]
    failures.extend(field for field in negative_fields if raw.get(field) is True)
    unknown = [field for field in positive_fields + negative_fields if raw.get(field) is None]
    evidence_ids = tuple(
        str(value).strip()
        for value in raw.get("evidence_ids", ())
        if str(value).strip()
    )
    if failures:
        return GateResult(
            GateName.BUSINESS_TRUTH,
            GateStatus.FAIL,
            "Quick reject failed: " + ", ".join(failures),
            evidence_ids,
        )
    if unknown:
        return GateResult(
            GateName.BUSINESS_TRUTH,
            GateStatus.NEEDS_EVIDENCE,
            "Quick reject unknown: " + ", ".join(unknown),
            evidence_ids,
        )
    return GateResult(
        GateName.BUSINESS_TRUTH,
        GateStatus.PASS,
        "Quick reject passed",
        evidence_ids,
    )


def _parse_payer_economics(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    result = dict(raw)
    payers = result.get("economic_payers")
    if payers is not None and (
        not isinstance(payers, Sequence) or isinstance(payers, (str, bytes))
    ):
        raise ValueError("payer_economics.economic_payers must be a list")
    return result


def _payer_economics_gaps(payer: Mapping[str, Any]) -> tuple[str, ...]:
    required = (
        "end_user",
        "economic_payers",
        "price_setter",
        "leverage_support_assessment",
    )
    return tuple(field for field in required if not payer.get(field))


def _parse_gates(
    raw_gates: Iterable[Mapping[str, Any]],
    evidence_book: Mapping[str, EvidenceRecord],
    *,
    today: dt.date | None,
) -> tuple[GateResult, ...]:
    parsed: list[GateResult] = []
    seen: set[GateName] = set()
    for item in raw_gates:
        try:
            name = GateName(str(item.get("name")))
            requested_status = GateStatus(str(item.get("status")))
        except ValueError as exc:
            raise ValueError(f"Invalid gate name or status: {item}") from exc
        if name in seen:
            raise ValueError(f"Gate {name.value} appears more than once")
        seen.add(name)
        evidence_ids = tuple(
            _text(value, f"{name.value}.evidence_ids")
            for value in item.get("evidence_ids", ())
        )
        reason = _text(item.get("reason"), f"{name.value}.reason")
        if requested_status == GateStatus.PASS and not evidence_ids:
            requested_status = GateStatus.NEEDS_EVIDENCE
            reason += " | no evidence was linked"
        quality = evidence_quality(evidence_ids, evidence_book, today=today)
        if requested_status == GateStatus.PASS and quality["validity_rate"] < 1.0:
            requested_status = GateStatus.NEEDS_EVIDENCE
            reason += " | linked evidence is missing, stale, or unverified"
        parsed.append(GateResult(name, requested_status, reason, evidence_ids))

    missing = set(REQUIRED_GATES) - seen
    if missing:
        names = ", ".join(sorted(gate.value for gate in missing))
        raise ValueError(f"Missing required gates: {names}")
    return tuple(sorted(parsed, key=lambda gate: REQUIRED_GATES.index(gate.name)))


def _parse_monitor_rules(
    raw_rules: Iterable[Mapping[str, Any]],
) -> tuple[MonitorRule, ...]:
    parsed: list[MonitorRule] = []
    seen: set[str] = set()
    for item in raw_rules:
        rule_id = _text(item.get("rule_id"), "monitor_rules.rule_id")
        if rule_id in seen:
            raise ValueError(f"Duplicate monitor rule_id: {rule_id}")
        seen.add(rule_id)
        try:
            gate = GateName(str(item.get("gate")))
            operator = Operator(str(item.get("operator")))
            threshold = float(item.get("threshold"))
            threshold_high = (
                float(item.get("threshold_high"))
                if item.get("threshold_high") is not None
                else None
            )
            consecutive_periods = int(item.get("consecutive_periods") or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid monitor rule: {item}") from exc
        if operator == Operator.BETWEEN and threshold_high is None:
            raise ValueError(f"{rule_id}: BETWEEN requires threshold_high")
        if consecutive_periods < 1:
            raise ValueError(f"{rule_id}: consecutive_periods must be >= 1")
        action = _text(item.get("action"), f"{rule_id}.action").upper()
        if action not in ALLOWED_TRIGGER_ACTIONS:
            raise ValueError(f"Unsupported monitor action: {action}")
        warning_threshold = (
            float(item.get("warning_threshold"))
            if item.get("warning_threshold") is not None
            else None
        )
        action_on_warning = (
            str(item.get("action_on_warning") or "").strip().upper() or None
        )
        validate_warning_tier(rule_id, operator, threshold, warning_threshold, action_on_warning)
        if action_on_warning is not None and action_on_warning not in ALLOWED_TRIGGER_ACTIONS:
            raise ValueError(f"Unsupported monitor action_on_warning: {action_on_warning}")
        parsed.append(
            MonitorRule(
                rule_id,
                gate,
                _text(item.get("metric"), f"{rule_id}.metric"),
                operator,
                threshold,
                threshold_high,
                consecutive_periods,
                action,
                warning_threshold,
                action_on_warning,
            )
        )
    if not parsed:
        raise ValueError("At least one monitor rule is required")
    if all(rule.gate == GateName.PRICE_ODDS for rule in parsed):
        raise ValueError("At least one monitor rule must test a non-price thesis assumption")
    return tuple(parsed)


def _parse_conviction_protocol(
    raw: Any,
    monitor_rules: Sequence[MonitorRule],
) -> Mapping[str, Any]:
    """Cross-reference validation the schema cannot express on its own: the
    protocol's capital/liquidity stops and fact-change thresholds must each
    name a rule_id that actually exists in this case's own monitor_rules, so
    the written commitment is enforced by the same executable rule it claims
    to be governed by rather than an unmonitored second promise."""
    if not isinstance(raw, Mapping):
        raise ValueError("conviction_protocol object is missing")
    gate_by_rule_id = {rule.rule_id: rule.gate for rule in monitor_rules}

    for field in ("capital_structure_stop", "liquidity_stop"):
        rule_id = _text(raw.get(field), f"conviction_protocol.{field}")
        if rule_id not in gate_by_rule_id:
            raise ValueError(
                f"conviction_protocol.{field} references unknown monitor rule_id: {rule_id}"
            )
        if gate_by_rule_id[rule_id] != GateName.SURVIVAL:
            raise ValueError(
                f"conviction_protocol.{field} must reference a SURVIVAL-gated monitor "
                f"rule, not {gate_by_rule_id[rule_id].value}"
            )

    thresholds = raw.get("fact_change_thresholds")
    if not isinstance(thresholds, Sequence) or isinstance(thresholds, (str, bytes)):
        raise ValueError("conviction_protocol.fact_change_thresholds must be a list")
    for item in thresholds:
        if not isinstance(item, Mapping):
            raise ValueError("conviction_protocol.fact_change_thresholds entries must be objects")
        rule_id = _text(
            item.get("monitor_rule_id"), "conviction_protocol.fact_change_thresholds.monitor_rule_id"
        )
        if rule_id not in gate_by_rule_id:
            raise ValueError(
                "conviction_protocol.fact_change_thresholds references unknown "
                f"monitor rule_id: {rule_id}"
            )

    drawdown = raw.get("allowed_price_drawdown_range")
    if isinstance(drawdown, Mapping):
        min_pct, max_pct = drawdown.get("min_pct"), drawdown.get("max_pct")
        if isinstance(min_pct, (int, float)) and isinstance(max_pct, (int, float)):
            if min_pct > max_pct:
                raise ValueError(
                    "conviction_protocol.allowed_price_drawdown_range.min_pct "
                    "must be <= max_pct"
                )

    return dict(raw)


def _confidence(validity_rate: float, strong_source_rate: float) -> Confidence:
    if validity_rate >= 0.95 and strong_source_rate >= 0.80:
        return Confidence.HIGH
    if validity_rate >= 0.85 and strong_source_rate >= 0.60:
        return Confidence.MEDIUM
    return Confidence.LOW


def evaluate_mispricing_case(
    case: Mapping[str, Any],
    *,
    today: dt.date | None = None,
) -> MispricingDecision:
    schema_version = _text(case.get("schema_version"), "schema_version")
    if schema_version != ENGINE_VERSION:
        raise ValueError(
            f"Unsupported schema_version {schema_version}; expected {ENGINE_VERSION}"
        )
    ticker = _text(case.get("ticker"), "ticker").upper()
    primary_path = _path(case.get("primary_path"), "primary_path")
    style_lineage = _style_lineage(case.get("style_lineage"))
    secondary_paths = tuple(
        _path(value, "secondary_paths") for value in case.get("secondary_paths", ())
    )
    if primary_path in secondary_paths:
        raise ValueError("primary_path must not be repeated in secondary_paths")
    if len(set(secondary_paths)) != len(secondary_paths):
        raise ValueError("secondary_paths must be unique")
    if len(secondary_paths) > 2:
        raise ValueError("secondary_paths must contain at most two paths")

    payer_economics = _parse_payer_economics(case.get("payer_economics"))
    quick_reject = _evaluate_quick_reject(
        case.get("quick_reject"),
        primary_path=primary_path,
    )
    payer_gaps = _payer_economics_gaps(payer_economics)
    if quick_reject.status == GateStatus.PASS and payer_gaps:
        quick_reject = GateResult(
            GateName.BUSINESS_TRUTH,
            GateStatus.NEEDS_EVIDENCE,
            "payer_economics incomplete: " + ", ".join(payer_gaps),
            quick_reject.evidence_ids,
        )

    raw_thesis = case.get("thesis")
    if not isinstance(raw_thesis, Mapping):
        raise ValueError("thesis must be an object")
    thesis = Thesis(
        _text(raw_thesis.get("market_believes"), "thesis.market_believes"),
        _text(raw_thesis.get("variant_view"), "thesis.variant_view"),
        _text(raw_thesis.get("because"), "thesis.because"),
        _text(raw_thesis.get("falsifiable_by"), "thesis.falsifiable_by"),
    )

    raw_evidence = case.get("evidence")
    if not isinstance(raw_evidence, Sequence) or isinstance(raw_evidence, (str, bytes)):
        raise ValueError("evidence must be a list")
    evidence_book = parse_evidence_book(raw_evidence)

    liquidation_result = None
    if primary_path == OpportunityPath.LIQUIDATION_AND_ASSET_ARBITRAGE:
        raw_liquidation = case.get("liquidation_value")
        if not isinstance(raw_liquidation, Mapping):
            raise ValueError("liquidation_value is required for liquidation path")
        liquidation_result = calculate_liquidation_value(
            raw_liquidation,
            evidence_book=evidence_book,
            today=today,
        )

    raw_gates = case.get("gates")
    if not isinstance(raw_gates, Sequence) or isinstance(raw_gates, (str, bytes)):
        raise ValueError("gates must be a list")
    gates = _parse_gates(raw_gates, evidence_book, today=today)

    if liquidation_result is not None:
        # LIQUIDATION is computed, not hand-declared (see REQUIRED_GATES),
        # so it is folded into the gates tuple here rather than parsed from
        # the case's own `gates` array.
        gates = gates + (
            GateResult(
                GateName.LIQUIDATION,
                GateStatus(liquidation_result.gate_status),
                "; ".join(liquidation_result.gate_reasons)
                or "Liquidation gate passed",
                liquidation_result.evidence_ids,
            ),
        )

    raw_rules = case.get("monitor_rules")
    if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, (str, bytes)):
        raise ValueError("monitor_rules must be a list")
    monitor_rules = _parse_monitor_rules(raw_rules)
    conviction_protocol = _parse_conviction_protocol(
        case.get("conviction_protocol"), monitor_rules
    )

    referenced_ids = [value for gate in gates for value in gate.evidence_ids]
    quality = evidence_quality(referenced_ids, evidence_book, today=today)
    confidence = _confidence(quality["validity_rate"], quality["strong_source_rate"])

    hard_failures = [
        gate for gate in gates
        if gate.name in HARD_REJECTION_GATES and gate.status == GateStatus.FAIL
    ]
    needs_evidence = [gate for gate in gates if gate.status == GateStatus.NEEDS_EVIDENCE]
    price_gate = next(gate for gate in gates if gate.name == GateName.PRICE_ODDS)

    quick_reject_blocker = quick_reject.status != GateStatus.PASS

    if quick_reject.status == GateStatus.FAIL:
        decision = ResearchDecision.REJECT_P3
        blockers = (quick_reject.reason,)
    elif hard_failures:
        decision = ResearchDecision.REJECT_P3
        blockers = tuple(f"{gate.name.value}: {gate.reason}" for gate in hard_failures)
    elif quick_reject_blocker:
        decision = ResearchDecision.WATCH_P2
        blockers = (quick_reject.reason,)
    elif needs_evidence:
        decision = ResearchDecision.WATCH_P2
        blockers = tuple(f"{gate.name.value}: {gate.reason}" for gate in needs_evidence)
    elif price_gate.status == GateStatus.FAIL:
        decision = ResearchDecision.WAIT_FOR_PRICE
        blockers = (f"{price_gate.name.value}: {price_gate.reason}",)
    elif confidence == Confidence.HIGH:
        decision = ResearchDecision.DEEP_RESEARCH_P0
        blockers = ()
    elif confidence == Confidence.MEDIUM:
        decision = ResearchDecision.DEEP_RESEARCH_P1
        blockers = ()
    else:
        decision = ResearchDecision.WATCH_P2
        blockers = ("CONFIDENCE: evidence quality is LOW",)

    return MispricingDecision(
        ENGINE_VERSION,
        schema_version,
        ticker,
        primary_path,
        secondary_paths,
        style_lineage,
        decision,
        confidence,
        quality["validity_rate"],
        quality["strong_source_rate"],
        thesis,
        gates,
        tuple(evidence_book.values()),
        monitor_rules,
        quick_reject,
        payer_economics,
        conviction_protocol,
        liquidation_result,
        blockers,
    )


def as_dict(decision: MispricingDecision) -> dict[str, Any]:
    return {
        "engine_version": decision.engine_version,
        "schema_version": decision.schema_version,
        "ticker": decision.ticker,
        "primary_path": decision.primary_path.value,
        "secondary_paths": [path.value for path in decision.secondary_paths],
        "style_lineage": decision.style_lineage.value,
        "decision": decision.decision.value,
        "eligible_for_energrex_research": decision.eligible_for_energrex_research,
        "confidence": decision.confidence.value,
        "evidence_validity_rate": round(decision.evidence_validity_rate, 4),
        "strong_source_rate": round(decision.strong_source_rate, 4),
        "thesis": decision.thesis.__dict__,
        "gates": [
            {
                "name": gate.name.value,
                "status": gate.status.value,
                "reason": gate.reason,
                "evidence_ids": list(gate.evidence_ids),
            }
            for gate in decision.gates
        ],
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "claim": item.claim,
                "source": item.source,
                "source_tier": item.source_tier.value,
                "status": item.status.value,
                "as_of": item.as_of.isoformat(),
                "verified_at": item.verified_at.isoformat(),
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            }
            for item in decision.evidence
        ],
        "monitor_rules": [
            {
                "rule_id": rule.rule_id,
                "gate": rule.gate.value,
                "metric": rule.metric,
                "operator": rule.operator.value,
                "threshold": rule.threshold,
                "threshold_high": rule.threshold_high,
                "consecutive_periods": rule.consecutive_periods,
                "action": rule.action,
                "warning_threshold": rule.warning_threshold,
                "action_on_warning": rule.action_on_warning,
            }
            for rule in decision.monitor_rules
        ],
        "quick_reject": {
            "status": decision.quick_reject.status.value,
            "reason": decision.quick_reject.reason,
            "evidence_ids": list(decision.quick_reject.evidence_ids),
        },
        "payer_economics": dict(decision.payer_economics),
        "conviction_protocol": dict(decision.conviction_protocol),
        "liquidation_value": (
            as_liquidation_dict(decision.liquidation_result)
            if decision.liquidation_result is not None
            else None
        ),
        "blocking_reasons": list(decision.blocking_reasons),
    }
