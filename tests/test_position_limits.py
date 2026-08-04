"""Tests for portfolio limit governance.

Written mostly as attempts to defeat the gates, because a governance rule
that has only been tested on the happy path has not been tested.
"""

import datetime as dt

import pytest

from scoring.position_limits import (
    COOLING_OFF_DAYS,
    CORRECTION_WINDOW_HOURS,
    LIMIT_BY_KEY,
    LOOSEN_COOLDOWN_DAYS,
    MAX_LOOSEN_STEP_PCT,
    MIN_REASON_CHARS,
    build_record,
    effective_limit,
    evaluate_change,
    pending_change,
)

T0 = dt.datetime(2026, 8, 1, 10, 0, 0)
GOOD_REASON = "产业链集中度评估后决定放宽，已与年度风险预算核对。"


def rec(key, old, new, direction, at, effective_at=None):
    return {
        "payload": {
            "key": key,
            "old_value": old,
            "new_value": new,
            "direction": direction,
            "timestamp": at.isoformat(timespec="seconds"),
            "effective_at": (effective_at or at).isoformat(timespec="seconds"),
        }
    }


def settled(key="single_stock_max", value=15.0, at=None):
    """A limit set long ago, well past its correction window."""
    at = at or (T0 - dt.timedelta(days=365))
    return [rec(key, None, value, "initial", at)]


# ── Direction semantics ────────────────────────────────────────────────

def test_max_limit_loosens_upward():
    spec = LIMIT_BY_KEY["single_stock_max"]
    assert spec.is_loosening(15.0, 20.0)
    assert not spec.is_loosening(15.0, 10.0)


def test_cash_floor_loosens_downward():
    """The cash floor is a minimum. Lowering it spends the safety buffer, so
    that is the direction the friction belongs on."""
    spec = LIMIT_BY_KEY["cash_floor_min"]
    assert spec.is_loosening(10.0, 5.0)
    assert not spec.is_loosening(10.0, 20.0)


def test_cash_floor_reduction_is_gated_not_waved_through():
    v = evaluate_change(
        "cash_floor_min", 3.0, reason="", now=T0,
        history=settled("cash_floor_min", 10.0), exposure=12.0,
    )
    assert not v.allowed
    assert v.direction == "loosen"


def test_raising_the_cash_floor_is_immediate():
    v = evaluate_change(
        "cash_floor_min", 20.0, reason="", now=T0,
        history=settled("cash_floor_min", 10.0), exposure=12.0,
    )
    assert v.allowed and v.direction == "tighten"
    assert v.effective_at == T0


# ── Tightening ─────────────────────────────────────────────────────────

def test_tightening_needs_no_reason_and_lands_at_once():
    v = evaluate_change(
        "single_stock_max", 10.0, reason="", now=T0,
        history=settled(), exposure=8.0,
    )
    assert v.allowed and v.direction == "tighten"
    assert v.effective_at == T0
    assert v.blockers == []


def test_tightening_is_allowed_even_while_in_breach():
    """Being over the line is a reason to pull it in, never a reason to stop."""
    v = evaluate_change(
        "single_stock_max", 12.0, reason="", now=T0,
        history=settled(value=15.0), exposure=22.0,
    )
    assert v.allowed and v.direction == "tighten"


# ── The breach lock ────────────────────────────────────────────────────

def test_cannot_raise_a_limit_while_breaching_it():
    """The move the whole module exists to stop: at 22% against a 15% cap,
    raise the cap and become 'compliant' without selling anything."""
    v = evaluate_change(
        "single_stock_max", 20.0, reason=GOOD_REASON, now=T0,
        history=settled(value=15.0), exposure=22.0,
    )
    assert not v.allowed
    assert any("超限" in b for b in v.blockers)


def test_loosening_is_refused_when_exposure_is_unknown():
    """If the breach lock cannot be evaluated it cannot be assumed to pass."""
    v = evaluate_change(
        "single_stock_max", 18.0, reason=GOOD_REASON, now=T0,
        history=settled(value=15.0), exposure=None,
    )
    assert not v.allowed
    assert any("敞口" in b for b in v.blockers)


def test_loosening_allowed_when_comfortably_inside_the_line():
    v = evaluate_change(
        "single_stock_max", 18.0, reason=GOOD_REASON, now=T0,
        history=settled(value=15.0), exposure=11.0,
    )
    assert v.allowed and v.blockers == []


# ── Reason, step size, frequency ───────────────────────────────────────

def test_short_reason_is_rejected():
    v = evaluate_change(
        "single_stock_max", 18.0, reason="想加仓", now=T0,
        history=settled(value=15.0), exposure=11.0,
    )
    assert not v.allowed
    assert any(str(MIN_REASON_CHARS) in b for b in v.blockers)


def test_oversized_step_is_rejected():
    v = evaluate_change(
        "single_stock_max", 15.0 + MAX_LOOSEN_STEP_PCT + 0.1,
        reason=GOOD_REASON, now=T0,
        history=settled(value=15.0), exposure=11.0,
    )
    assert not v.allowed
    assert any("单次放宽" in b for b in v.blockers)


def test_second_loosening_inside_the_cooldown_is_rejected():
    """Salami slicing: 15 -> 18 -> 21 -> 24, every step 'small'."""
    history = settled(value=15.0) + [
        rec("single_stock_max", 15.0, 18.0, "loosen", T0 - dt.timedelta(days=3)),
    ]
    v = evaluate_change(
        "single_stock_max", 21.0, reason=GOOD_REASON, now=T0,
        history=history, exposure=11.0,
    )
    assert not v.allowed
    assert any("不足" in b for b in v.blockers)


def test_loosening_allowed_once_the_cooldown_expires():
    history = settled(value=15.0) + [
        rec("single_stock_max", 15.0, 18.0, "loosen",
            T0 - dt.timedelta(days=LOOSEN_COOLDOWN_DAYS + 1)),
    ]
    v = evaluate_change(
        "single_stock_max", 21.0, reason=GOOD_REASON, now=T0,
        history=history, exposure=11.0,
    )
    assert v.allowed


def test_repeated_tightening_is_never_rate_limited():
    history = settled(value=15.0) + [
        rec("single_stock_max", 15.0, 12.0, "tighten", T0 - dt.timedelta(hours=1)),
    ]
    v = evaluate_change(
        "single_stock_max", 9.0, reason="", now=T0, history=history, exposure=8.0,
    )
    assert v.allowed


# ── Cooling-off ────────────────────────────────────────────────────────

def test_approved_loosening_does_not_take_effect_immediately():
    v = evaluate_change(
        "single_stock_max", 18.0, reason=GOOD_REASON, now=T0,
        history=settled(value=15.0), exposure=11.0,
    )
    assert v.effective_at == T0 + dt.timedelta(days=COOLING_OFF_DAYS)


def test_old_limit_still_governs_during_cooling_off():
    """Recording the change must not be the same as living under it."""
    history = settled(value=15.0) + [
        rec("single_stock_max", 15.0, 20.0, "loosen", T0,
            effective_at=T0 + dt.timedelta(days=COOLING_OFF_DAYS)),
    ]
    during = T0 + dt.timedelta(days=COOLING_OFF_DAYS - 1)
    after = T0 + dt.timedelta(days=COOLING_OFF_DAYS, seconds=1)
    assert effective_limit(history, "single_stock_max", during) == 15.0
    assert effective_limit(history, "single_stock_max", after) == 20.0


def test_pending_change_is_visible_while_it_waits():
    history = settled(value=15.0) + [
        rec("single_stock_max", 15.0, 20.0, "loosen", T0,
            effective_at=T0 + dt.timedelta(days=COOLING_OFF_DAYS)),
    ]
    during = T0 + dt.timedelta(days=1)
    assert pending_change(history, "single_stock_max", during)["new_value"] == 20.0
    after = T0 + dt.timedelta(days=COOLING_OFF_DAYS + 1)
    assert pending_change(history, "single_stock_max", after) is None


# ── Setup and the correction window ────────────────────────────────────

def test_first_setup_is_immediate():
    v = evaluate_change(
        "single_stock_max", 15.0, reason="", now=T0, history=[], exposure=None,
    )
    assert v.allowed and v.direction == "initial"
    assert v.effective_at == T0


def test_typo_in_a_brand_new_limit_can_be_fixed_even_while_breaching():
    """A limit set minutes ago has governed nothing yet. Set 5% by mistake
    against a real 12% holding and the breach lock would otherwise wedge it
    permanently shut."""
    history = [rec("single_stock_max", None, 5.0, "initial", T0)]
    v = evaluate_change(
        "single_stock_max", 15.0, reason="", now=T0 + dt.timedelta(hours=1),
        history=history, exposure=12.0,
    )
    assert v.allowed
    assert v.effective_at == T0 + dt.timedelta(hours=1)


def test_correction_window_closes_and_never_reopens():
    history = [rec("single_stock_max", None, 15.0, "initial", T0)]
    later = T0 + dt.timedelta(hours=CORRECTION_WINDOW_HOURS + 1)
    v = evaluate_change(
        "single_stock_max", 20.0, reason=GOOD_REASON, now=later,
        history=history, exposure=22.0,
    )
    assert not v.allowed
    assert any("超限" in b for b in v.blockers)


def test_later_changes_do_not_reopen_the_correction_window():
    """The window is anchored to the first record, not the most recent one,
    so a tightening cannot be used to unlock a loosening."""
    history = [
        rec("single_stock_max", None, 15.0, "initial", T0 - dt.timedelta(days=200)),
        rec("single_stock_max", 15.0, 12.0, "tighten", T0 - dt.timedelta(minutes=5)),
    ]
    v = evaluate_change(
        "single_stock_max", 17.0, reason=GOOD_REASON, now=T0,
        history=history, exposure=16.0,
    )
    assert not v.allowed


# ── Misc ───────────────────────────────────────────────────────────────

def test_unchanged_value_is_not_a_change():
    v = evaluate_change(
        "single_stock_max", 15.0, reason="", now=T0,
        history=settled(value=15.0), exposure=8.0,
    )
    assert not v.allowed and v.direction == "unchanged"


def test_unknown_key_is_rejected():
    v = evaluate_change("made_up", 1.0, reason=GOOD_REASON, now=T0, history=[], exposure=1.0)
    assert not v.allowed


@pytest.mark.parametrize("key", [spec.key for spec in LIMIT_BY_KEY.values()])
def test_every_limit_has_a_label_and_help(key):
    spec = LIMIT_BY_KEY[key]
    assert spec.label and spec.help_text


def test_record_captures_exposure_at_the_time_of_change():
    """So a later review can ask what the portfolio looked like when the rule
    was bent, not just what the rule became."""
    v = evaluate_change(
        "single_stock_max", 18.0, reason=GOOD_REASON, now=T0,
        history=settled(value=15.0), exposure=11.5,
    )
    payload = build_record(
        "single_stock_max", 18.0, v,
        reason=GOOD_REASON, now=T0, exposure=11.5, old_value=15.0,
    )
    assert payload["exposure_at_change"] == 11.5
    assert payload["old_value"] == 15.0
    assert payload["direction"] == "loosen"
    assert payload["effective_at"].startswith("2026-08-08")
