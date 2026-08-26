"""Tests for connecting a mispricing thesis's monitoring rules to a ticker.

Three pieces that already worked independently -- mispricing_store's hash
chain, mispricing_monitor's rule evaluator, and its case-state router -- are
wired together here so a position screen can ask "is this thesis still
holding up" for one ticker.

The property that matters most: metric_observations are hand-entered the
same way scoring/user_overrides.json entries are, and an unconfirmed number
must never be able to trigger REDUCE/EXIT on a real position by itself. It
can only ever raise a "go check this" count.
"""

import tempfile
from pathlib import Path

import pytest

from scoring.mispricing_monitor import (
    ObservationSet,
    split_observations,
    thesis_state_for_ticker,
)
from scoring.mispricing_store import append_snapshot


def rule(rule_id="r1", metric="m", threshold=0, periods=2, action="EXIT"):
    return {
        "rule_id": rule_id,
        "gate": "BUSINESS_TRUTH",
        "metric": metric,
        "operator": "LT",
        "threshold": threshold,
        "consecutive_periods": periods,
        "action": action,
    }


def case(ticker="TESTCO", monitor_rules=None, metric_observations=None, case_id="c1"):
    return {
        "case_id": case_id,
        "ticker": ticker,
        "opportunity_routing": {"primary_path": "GREAT_BUSINESS_STUMBLE"},
        "monitor_rules": monitor_rules or [rule()],
        "decision": {"next_review_date": "2026-09-01"},
        "metric_observations": metric_observations or {},
    }


def obs(*entries):
    return [{"value": v, "status": s} for v, s in entries]


# ── split_observations ──────────────────────────────────────────────────

def test_verified_entries_populate_history():
    result = split_observations({"m": obs((0.02, "verified"), (-0.01, "verified"))})
    assert result.verified_history == {"m": [0.02, -0.01]}
    assert result.pending_count == 0


def test_pending_entries_are_counted_not_included():
    result = split_observations({"m": obs((0.02, "verified"), (-0.05, "pending"))})
    assert result.verified_history == {"m": [0.02]}
    assert result.pending_by_metric == {"m": 1}


def test_order_is_preserved_for_the_consecutive_periods_window():
    """evaluate_rule reads the *last* N verified entries; reordering here
    would silently change which periods a rule judges."""
    result = split_observations(
        {"m": obs((1.0, "verified"), (2.0, "pending"), (3.0, "verified"))}
    )
    assert result.verified_history["m"] == [1.0, 3.0]


@pytest.mark.parametrize("status", ["pending", "not_applicable", "estimated", "", "PENDING"])
def test_only_the_exact_verified_status_counts_as_trusted(status):
    result = split_observations({"m": obs((1.0, status))})
    assert result.verified_history == {}
    assert result.pending_by_metric == {"m": 1}


def test_non_numeric_and_missing_values_are_skipped_entirely():
    """Garbage input should not show up as either trusted or pending -- it is
    not an unverified number, it is not a number."""
    result = split_observations(
        {"m": [{"value": "n/a", "status": "verified"}, {"value": None, "status": "verified"}]}
    )
    assert result == ObservationSet({}, {})


def test_empty_and_malformed_observations_do_not_raise():
    assert split_observations({}) == ObservationSet({}, {})
    assert split_observations({"m": "not-a-list"}) == ObservationSet({}, {})
    assert split_observations({"m": [{"no_value_key": True}]}) == ObservationSet({}, {})


# ── thesis_state_for_ticker ──────────────────────────────────────────────

def test_ticker_with_no_case_returns_none_not_an_exception():
    with tempfile.TemporaryDirectory() as tmp:
        assert thesis_state_for_ticker(Path(tmp) / "cases.jsonl", "NOBODY") is None


def test_two_verified_bad_periods_trigger_the_declared_action():
    with tempfile.TemporaryDirectory() as tmp:
        chain = Path(tmp) / "cases.jsonl"
        append_snapshot(
            chain,
            case(metric_observations={"m": obs((-0.01, "verified"), (-0.02, "verified"))}),
        )
        state = thesis_state_for_ticker(chain, "TESTCO")
        assert state["case_state"] == "EXIT"
        assert state["pending_observation_count"] == 0
        assert len(state["triggered_rules"]) == 1


def test_an_unverified_period_withholds_the_action_but_is_counted():
    """The central property: a pending number cannot itself cause EXIT."""
    with tempfile.TemporaryDirectory() as tmp:
        chain = Path(tmp) / "cases.jsonl"
        append_snapshot(
            chain,
            case(metric_observations={"m": obs((-0.01, "verified"), (-0.02, "pending"))}),
        )
        state = thesis_state_for_ticker(chain, "TESTCO")
        assert state["case_state"] != "EXIT"
        assert state["pending_observation_count"] == 1
        assert state["pending_by_metric"] == {"m": 1}


def test_all_pending_reads_as_active_with_a_visible_count():
    with tempfile.TemporaryDirectory() as tmp:
        chain = Path(tmp) / "cases.jsonl"
        append_snapshot(
            chain,
            case(metric_observations={"m": obs((-0.01, "pending"), (-0.02, "pending"))}),
        )
        state = thesis_state_for_ticker(chain, "TESTCO")
        assert state["case_state"] == "ACTIVE"
        assert state["pending_observation_count"] == 2


def test_a_later_snapshot_for_the_same_ticker_supersedes_the_earlier_one():
    with tempfile.TemporaryDirectory() as tmp:
        chain = Path(tmp) / "cases.jsonl"
        append_snapshot(
            chain,
            case(case_id="c1", metric_observations={"m": obs((-0.01, "verified"), (-0.02, "verified"))}),
        )
        append_snapshot(chain, case(case_id="c2", metric_observations={}))
        state = thesis_state_for_ticker(chain, "TESTCO")
        assert state["case_id"] == "c2"
        assert state["case_state"] == "ACTIVE"


def test_other_tickers_in_the_same_chain_do_not_interfere():
    with tempfile.TemporaryDirectory() as tmp:
        chain = Path(tmp) / "cases.jsonl"
        append_snapshot(
            chain,
            case(ticker="OTHERCO", metric_observations={"m": obs((-0.01, "verified"), (-0.02, "verified"))}),
        )
        append_snapshot(chain, case(ticker="TESTCO", metric_observations={}))
        assert thesis_state_for_ticker(chain, "TESTCO")["case_state"] == "ACTIVE"
        assert thesis_state_for_ticker(chain, "OTHERCO")["case_state"] == "EXIT"


def test_portfolio_breach_flag_overrides_thesis_state():
    """A portfolio/option limit breach must win even over a clean thesis --
    this is the same override route_case_state already exposes."""
    with tempfile.TemporaryDirectory() as tmp:
        chain = Path(tmp) / "cases.jsonl"
        append_snapshot(chain, case(metric_observations={}))
        state = thesis_state_for_ticker(
            chain, "TESTCO", portfolio_or_option_limit_breached=True
        )
        assert state["case_state"] == "RISK_REDUCTION_REQUIRED"


def test_ticker_lookup_is_case_and_whitespace_insensitive():
    with tempfile.TemporaryDirectory() as tmp:
        chain = Path(tmp) / "cases.jsonl"
        append_snapshot(chain, case(ticker="TESTCO", metric_observations={}))
        assert thesis_state_for_ticker(chain, " testco ") is not None
