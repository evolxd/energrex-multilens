"""对冲的宽度够不够——成本率和 DTE 全合格的保护，仍然可能在需要它的
跌幅上一分钱都不多赔。

钉住的是 2026-09-20 那份真实持仓：QQQ 690/630 和 SMH 525/500 两个价差，
成本率和 DTE 检查全过，但有效窗口分别只有 5.5 和 2.4 个大盘百分点，中间
还隔着一段谁都不管的真空。压力测试在 -10% 上亏 21.8%，原因就在这里。
"""

import pytest

from account.hedge_width import (
    HedgeWindow,
    assess_width,
    build_windows,
)

# 2026-09-20 实测
SPOT = {"QQQ": 837.52, "SMH": 572.30}
BETA = {"QQQ": 1.31, "SMH": 1.77}
EQUITY = 53_230.0


def _leg(sym, root, kind, strike, qty, expiry="2026-12-18"):
    return {"sym": sym, "root": root, "type": kind, "strike": strike,
            "qty": qty, "expiry": expiry}


REAL_LEGS = [
    _leg("QQQ261218P00690000", "QQQ", "P", 690.0, 1, "2026-12-18"),
    _leg("QQQ261218P00630000", "QQQ", "P", 630.0, -1, "2026-12-18"),
    _leg("SMH270115P00525000", "SMH", "P", 525.0, 1, "2027-01-15"),
    _leg("SMH270115P00500000", "SMH", "P", 500.0, -1, "2027-01-15"),
]


# ── 配对 ────────────────────────────────────────────────────────────────

def test_a_long_put_pairs_with_the_highest_short_below_it():
    ws = build_windows(REAL_LEGS, spot_map=SPOT, beta_map=BETA)
    by_root = {w.underlying: w for w in ws}
    assert by_root["QQQ"].short_strike == 630.0
    assert by_root["SMH"].short_strike == 500.0


def test_a_short_put_above_the_long_is_a_credit_spread_not_protection():
    # 行权价高于长腿的空头 put 是看涨结构，配进来会把窗口算反。
    legs = [_leg("X", "QQQ", "P", 630.0, 1), _leg("Y", "QQQ", "P", 690.0, -1)]
    ws = build_windows(legs, spot_map=SPOT, beta_map=BETA)
    assert ws[0].short_strike is None


def test_a_short_on_a_different_expiry_is_not_the_other_leg():
    legs = [_leg("X", "QQQ", "P", 690.0, 1, "2026-12-18"),
            _leg("Y", "QQQ", "P", 630.0, -1, "2027-03-19")]
    assert build_windows(legs, spot_map=SPOT, beta_map=BETA)[0].short_strike is None


def test_an_unmatched_long_put_is_uncapped_which_is_not_a_defect():
    legs = [_leg("X", "QQQ", "P", 690.0, 1)]
    w = build_windows(legs, spot_map=SPOT, beta_map=BETA)[0]
    assert not w.is_capped
    assert w.cap_pct is None
    assert w.useful_window_pct is None


def test_calls_and_non_hedge_underlyings_are_ignored():
    legs = [_leg("C", "QQQ", "C", 900.0, 1),
            _leg("N", "NVDA", "P", 200.0, 1)]
    assert build_windows(legs, spot_map=SPOT, beta_map=BETA) == []


def test_one_short_leg_is_not_reused_by_two_longs():
    legs = [_leg("L1", "QQQ", "P", 690.0, 1), _leg("L2", "QQQ", "P", 680.0, 1),
            _leg("S", "QQQ", "P", 630.0, -1)]
    ws = build_windows(legs, spot_map=SPOT, beta_map=BETA)
    assert sum(1 for w in ws if w.is_capped) == 1


# ── 窗口换算 ────────────────────────────────────────────────────────────

def test_the_qqq_window_matches_the_hand_calculation():
    w = next(w for w in build_windows(REAL_LEGS, spot_map=SPOT, beta_map=BETA)
             if w.underlying == "QQQ")
    # (690/837.52 - 1) / 1.31 = -13.4% ； (630/837.52 - 1) / 1.31 = -18.9%
    assert w.activation_pct == pytest.approx(-0.134, abs=0.002)
    assert w.cap_pct == pytest.approx(-0.189, abs=0.002)
    assert w.useful_window_pct == pytest.approx(0.055, abs=0.002)


def test_the_smh_window_is_the_narrow_one():
    w = next(w for w in build_windows(REAL_LEGS, spot_map=SPOT, beta_map=BETA)
             if w.underlying == "SMH")
    assert w.activation_pct == pytest.approx(-0.047, abs=0.002)
    assert w.cap_pct == pytest.approx(-0.071, abs=0.002)
    assert w.useful_window_pct == pytest.approx(0.024, abs=0.002)
    assert w.max_payoff == pytest.approx(2_500.0)


def test_a_higher_beta_underlying_reaches_its_strikes_on_a_smaller_market_move():
    low = HedgeWindow("X", 90.0, 80.0, 1, spot=100.0, beta=1.0)
    high = HedgeWindow("Y", 90.0, 80.0, 1, spot=100.0, beta=2.0)
    assert abs(high.activation_pct) < abs(low.activation_pct)


def test_payoff_is_capped_at_the_width_no_matter_how_deep_the_move():
    w = HedgeWindow("SMH", 525.0, 500.0, 1, spot=572.30, beta=1.77)
    assert w.payoff_at(-0.10) == pytest.approx(2_500.0)
    assert w.payoff_at(-0.50) == pytest.approx(2_500.0)       # 一分不多


def test_an_uncapped_put_keeps_paying_as_the_move_deepens():
    w = HedgeWindow("SMH", 525.0, None, 1, spot=572.30, beta=1.77)
    assert w.payoff_at(-0.50) > w.payoff_at(-0.20) > 0


def test_a_move_that_never_reaches_the_long_strike_pays_nothing():
    w = HedgeWindow("QQQ", 690.0, 630.0, 1, spot=837.52, beta=1.31)
    assert w.payoff_at(-0.05) == 0.0


# ── 判定 ────────────────────────────────────────────────────────────────

def _codes(report):
    return {f.code for f in report.findings}


def test_the_real_book_trips_every_width_check():
    r = assess_width(REAL_LEGS, spot_map=SPOT, beta_map=BETA, equity=EQUITY)
    codes = _codes(r)
    assert "ACTIVATES_TOO_DEEP" in codes        # QQQ 要跌 13.4% 才起效
    assert "CAPS_BEFORE_DEEP_SCENARIO" in codes  # 两个都在 -20% 之前打满
    assert "WINDOW_TOO_NARROW" in codes          # SMH 只有 2.4 个点
    assert "UNCOVERED_BAND" in codes             # -7.1% 到 -13.4% 的真空


def test_the_uncovered_band_between_the_two_hedges_is_found():
    r = assess_width(REAL_LEGS, spot_map=SPOT, beta_map=BETA, equity=EQUITY)
    msg = next(f.message for f in r.findings if f.code == "UNCOVERED_BAND")
    assert "7.1%" in msg and "13.4%" in msg


def test_the_immaterial_payoff_is_information_not_a_warning():
    # $2,500 在 $53,230 上是 4.7%，高于 3% 的门槛——不触发。
    r = assess_width(REAL_LEGS, spot_map=SPOT, beta_map=BETA, equity=EQUITY)
    assert "PAYOFF_IMMATERIAL" not in _codes(r)
    # 同一个价差放在大得多的账户上就不成规模了。
    big = assess_width(REAL_LEGS, spot_map=SPOT, beta_map=BETA, equity=500_000.0)
    hits = [f for f in big.findings if f.code == "PAYOFF_IMMATERIAL"]
    assert hits and all(f.severity == "INFO" for f in hits)


def test_buying_back_the_short_legs_clears_the_capping_findings():
    # 这就是算出来最划算的那个动作：花 $2,437 解除两处封顶。
    uncapped = [l for l in REAL_LEGS if l["qty"] > 0]
    r = assess_width(uncapped, spot_map=SPOT, beta_map=BETA, equity=EQUITY)
    codes = _codes(r)
    assert "CAPS_BEFORE_DEEP_SCENARIO" not in codes
    assert "WINDOW_TOO_NARROW" not in codes


def test_a_wide_spread_covering_the_governed_scenarios_is_clean():
    legs = [_leg("L", "QQQ", "P", 820.0, 1), _leg("S", "QQQ", "P", 560.0, -1)]
    r = assess_width(legs, spot_map=SPOT, beta_map=BETA, equity=EQUITY)
    assert not [f for f in r.findings if f.severity == "WARN"]


def test_no_hedge_at_all_reads_as_no_protection_not_as_passing():
    r = assess_width([], spot_map=SPOT, beta_map=BETA, equity=EQUITY)
    assert not r.has_hedges
    assert r.findings == []
    assert r.notes and "不是「保护合格」" in r.notes[0]


def test_a_missing_beta_is_reported_rather_than_silently_assumed():
    legs = [_leg("L", "SMH", "P", 525.0, 1), _leg("S", "SMH", "P", 500.0, -1)]
    r = assess_width(legs, spot_map=SPOT, beta_map={}, equity=EQUITY)
    assert r.findings == []
    assert any("beta" in n for n in r.notes)


def test_the_scenarios_come_from_the_caller_not_a_hardcoded_threshold():
    # 账户自己的压力线是多少，判定就跟着变。
    legs = [_leg("L", "QQQ", "P", 690.0, 1), _leg("S", "QQQ", "P", 630.0, -1)]
    strict = assess_width(legs, spot_map=SPOT, beta_map=BETA, equity=EQUITY,
                          primary_scenario=-0.05)
    loose = assess_width(legs, spot_map=SPOT, beta_map=BETA, equity=EQUITY,
                         primary_scenario=-0.20)
    assert "ACTIVATES_TOO_DEEP" in _codes(strict)
    assert "ACTIVATES_TOO_DEEP" not in _codes(loose)
