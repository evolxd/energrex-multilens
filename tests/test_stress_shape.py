"""损失曲线的形状，以及两条限额打架时怎么换算到同一把尺子上。

2026-09-20 的实测：BD 273.5%（限额 350%，还剩 76.5 个点余量）的同时，
压力 -10% 是 17.5%（限额 15%，超了 2.5 个点）。两条线治理同一个风险却
给出相反的结论——BD 那条线根本没在起作用。

同一份数据还显示：头 10% 亏 $9,294，第二个 10% 只多亏 $7,054。后一段比
前一段便宜，说明保护腿挂得太远。这个特征在只有 -10/-20 两个点的时候是
隐形的。
"""

import pytest

from account.risk import (
    STRESS_SHOCKS,
    compute_portfolio_stress_test,
    implied_bd_ceiling,
    protection_gap,
    stress_curve_shape,
)


# ── 阶梯本身 ────────────────────────────────────────────────────────────

def _stock(symbol, qty, mv):
    return {"symbol": symbol, "quantity": qty, "market_value": mv}


def _run(stocks, options=(), **kw):
    return compute_portfolio_stress_test(
        list(stocks), list(options),
        underlying_prices=kw.pop("prices", {}),
        iv_map=kw.pop("iv_map", {}),
        beta_map=kw.pop("beta_map", {}),
        **kw,
    )


def test_the_default_ladder_has_the_two_governed_levels_plus_the_shape_points():
    # -10 和 -20 是限额盯的档位，-5/-15 是为了看出曲线形状。
    assert -0.10 in STRESS_SHOCKS and -0.20 in STRESS_SHOCKS
    result = _run([_stock("AAA", 100, 10_000.0)])
    assert set(result["stress_ladder"]) == {-5, -10, -15, -20}


def test_the_named_keys_still_come_from_the_ladder():
    result = _run([_stock("AAA", 100, 10_000.0)])
    assert result["stress_10"] == result["stress_ladder"][-10]
    assert result["stress_20"] == result["stress_ladder"][-20]


def test_a_stock_only_book_loses_exactly_beta_times_the_shock():
    # 股票没有凸性，-5% 的损失必须正好是 -10% 的一半。
    result = _run([_stock("AAA", 100, 10_000.0)], beta_map={"AAA": 2.0})
    ladder = result["stress_ladder"]
    assert ladder[-10] == pytest.approx(-2_000.0)      # 10,000 × 2.0 × -10%
    assert ladder[-5] == pytest.approx(ladder[-10] / 2)
    assert ladder[-20] == pytest.approx(ladder[-10] * 2)


def test_a_custom_ladder_is_honoured():
    result = _run([_stock("AAA", 100, 10_000.0)], shocks=(-0.30,))
    assert set(result["stress_ladder"]) == {-30}
    # 阶梯里没有 -10 时，具名键退回 0 而不是抛错——但调用方要知道这件事，
    # 所以默认档位里永远包含 -10/-20。
    assert result["stress_10"] == 0.0


def test_integer_percent_keys_survive_the_float_shock_values():
    # -0.1 在浮点里不是精确值；用它当字典键，跨模块取值会取不到。
    ladder = _run([_stock("AAA", 100, 1_000.0)])["stress_ladder"]
    assert all(isinstance(k, int) for k in ladder)


# ── 曲线形状 ────────────────────────────────────────────────────────────

def test_bands_are_ordered_shallow_to_deep_and_start_from_zero():
    bands = stress_curve_shape({-5: -100.0, -10: -200.0, -15: -300.0, -20: -400.0})
    assert [b["to_pct"] for b in bands] == [-5, -10, -15, -20]
    assert bands[0]["from_pct"] == 0


def test_marginal_is_the_extra_loss_of_that_band_not_the_running_total():
    bands = stress_curve_shape({-10: -9_294.0, -20: -16_348.0})
    assert bands[0]["marginal"] == pytest.approx(-9_294.0)
    assert bands[1]["marginal"] == pytest.approx(-7_054.0)


def test_a_linear_book_has_no_band_cheaper_than_the_one_before():
    bands = stress_curve_shape({-5: -100.0, -10: -200.0, -15: -300.0, -20: -400.0})
    assert not any(b["cheaper_than_previous"] for b in bands)


def test_the_real_2026_09_20_numbers_show_the_second_band_is_cheaper():
    # 头 10% 亏 9,294，第二个 10% 只多亏 7,054 —— 长 put 在深水区才起作用。
    bands = stress_curve_shape({-10: -9_294.0, -20: -16_348.0})
    assert bands[1]["cheaper_than_previous"]


def test_an_empty_ladder_produces_no_bands():
    assert stress_curve_shape({}) == []


# ── 保护缺口的那句话 ────────────────────────────────────────────────────

def test_a_linear_book_reports_no_gap():
    assert protection_gap({-5: -100.0, -10: -200.0, -20: -400.0}) is None


def test_the_gap_message_names_both_bands_and_their_dollars():
    msg = protection_gap({-10: -9_294.0, -20: -16_348.0})
    assert msg is not None
    assert "9,294" in msg and "7,054" in msg
    assert "0%→-10%" in msg and "-10%→-20%" in msg


def test_an_empty_ladder_has_nothing_to_say():
    assert protection_gap({}) is None


# ── 压力线反推 BD 上限 ──────────────────────────────────────────────────

def test_the_ceiling_reproduces_the_2026_09_20_hand_calculation():
    # 净值 53,180 · BD 145,446 (273.5%) · 压力-10% 亏 9,294 · 限额 15%
    ceiling = implied_bd_ceiling(
        current_bd=145_446.0, stress_loss=-9_294.0,
        equity=53_180.0, stress_limit=0.15,
    )
    assert ceiling / 53_180.0 == pytest.approx(2.35, abs=0.01)


def test_proportional_trimming_makes_the_ceiling_exact_not_approximate():
    # 所有持仓同乘 k，BD 和压力损失同乘 k —— 非线性只在"大盘跌多少"那一维，
    # 不在"仓位多大"这一维。所以按这个上限等比例减仓，压力恰好落在限额上。
    equity, limit = 100_000.0, 0.15
    bd, loss = 300_000.0, -25_000.0
    ceiling = implied_bd_ceiling(
        current_bd=bd, stress_loss=loss, equity=equity, stress_limit=limit)

    k = ceiling / bd
    assert abs(loss * k) / equity == pytest.approx(limit)


def test_a_book_already_inside_the_limit_gets_a_ceiling_above_where_it_sits():
    ceiling = implied_bd_ceiling(
        current_bd=100_000.0, stress_loss=-5_000.0,
        equity=100_000.0, stress_limit=0.15,
    )
    assert ceiling > 100_000.0


def test_the_sign_of_the_loss_does_not_change_the_answer():
    kw = dict(current_bd=145_446.0, equity=53_180.0, stress_limit=0.15)
    assert implied_bd_ceiling(stress_loss=-9_294.0, **kw) == pytest.approx(
        implied_bd_ceiling(stress_loss=9_294.0, **kw)
    )


def test_nothing_to_convert_returns_none_rather_than_zero():
    # 返回 0 会被读成"BD 上限是 0，全部清仓"。
    kw = dict(current_bd=145_446.0, stress_loss=-9_294.0,
              equity=53_180.0, stress_limit=0.15)
    assert implied_bd_ceiling(**{**kw, "equity": 0.0}) is None
    assert implied_bd_ceiling(**{**kw, "stress_loss": 0.0}) is None
    assert implied_bd_ceiling(**{**kw, "current_bd": 0.0}) is None


def test_the_ceiling_can_sit_far_below_a_bd_limit_that_looks_unbreached():
    """两条限额打架的那个场景，钉死成测试。

    BD 273.5% 对着 350% 的限额还剩 76.5 个点余量，看起来很安全；但压力线
    换算过来的上限只有 235%。BD 那条线在这个组合上根本不会先触发。
    """
    equity = 53_180.0
    bd_ratio, bd_limit_ratio = 2.735, 3.50
    ceiling = implied_bd_ceiling(
        current_bd=bd_ratio * equity, stress_loss=-9_294.0,
        equity=equity, stress_limit=0.15,
    )
    ceiling_ratio = ceiling / equity
    assert bd_ratio < bd_limit_ratio          # BD 限额没超
    assert ceiling_ratio < bd_ratio           # 但压力线要求的更严
    assert ceiling_ratio < bd_limit_ratio     # BD 限额是松的，不起约束作用
