"""超限之后到底要卖什么。

门③仓位管理原来在超限时只说「只能调整持仓」。这里钉住的是那句话缺掉的
部分：卖哪只、卖多少股，以及三条限额同时超时不要把同一笔仓位算着卖三遍。
"""

import pytest

from account.rebalance import MIN_TRIM_DOLLARS, _waterfill, plan_rebalance
from scoring.position_exposure import compute_exposures


def _exposures(holdings, *, equity=100_000.0, cash=10_000.0, chains=None, adv=None):
    chains = chains or {}
    rows = [{"symbol": s, "market_value": v} for s, v in holdings.items()]
    return compute_exposures(
        rows, equity, cash, lambda s: chains.get(s), [], avg_dollar_volume=adv
    )


# ── 削平：从最集中的地方削起 ────────────────────────────────────────────

def test_waterfill_takes_it_all_from_the_largest_when_that_is_enough():
    cuts = _waterfill({"A": 100, "B": 50, "C": 20}, 30)
    assert cuts == {"A": 30}


def test_waterfill_levels_the_top_two_once_the_largest_reaches_the_second():
    # 要削 70：A 单独削到 50 只够 50，再往下 A 和 B 一起降。
    cuts = _waterfill({"A": 100, "B": 50, "C": 20}, 70)
    assert cuts == pytest.approx({"A": 60.0, "B": 10.0})


def test_waterfill_never_cuts_more_than_exists():
    cuts = _waterfill({"A": 100, "B": 50}, 500)
    assert sum(cuts.values()) == 150


def test_waterfill_of_nothing_is_nothing():
    assert _waterfill({}, 100) == {}
    assert _waterfill({"A": 100}, 0) == {}


# ── 单票 ───────────────────────────────────────────────────────────────

def test_a_single_name_over_its_cap_is_trimmed_to_the_cap():
    exposures = _exposures({"NVDA": 22_000, "PLTR": 5_000})
    plan = plan_rebalance(exposures, {"single_stock_max": 15.0})

    assert len(plan.trims) == 1
    trim = plan.trims[0]
    assert trim.symbol == "NVDA"
    assert trim.dollars == pytest.approx(7_000)
    assert trim.to_pct == pytest.approx(15.0)


def test_share_count_rounds_up_so_the_sale_actually_lands_inside_the_line():
    # $7,000 / $180 = 38.9 股。取 38 股只卖掉 $6,840，仍然超限。
    exposures = _exposures({"NVDA": 22_000})
    plan = plan_rebalance(exposures, {"single_stock_max": 15.0}, prices={"NVDA": 180.0})
    assert plan.trims[0].shares == 39


def test_no_price_means_no_invented_share_count():
    exposures = _exposures({"NVDA": 22_000})
    plan = plan_rebalance(exposures, {"single_stock_max": 15.0})
    assert plan.trims[0].shares is None


def test_everything_inside_the_lines_produces_no_orders():
    exposures = _exposures({"NVDA": 10_000, "PLTR": 5_000})
    plan = plan_rebalance(
        exposures, {"single_stock_max": 15.0, "chain_max": 40.0, "cash_floor_min": 5.0}
    )
    assert plan.is_empty


def test_an_unset_limit_is_not_treated_as_zero():
    # 限额没设 = 还没回答的问题，不是"上限 0%，全卖掉"。
    exposures = _exposures({"NVDA": 40_000})
    assert plan_rebalance(exposures, {"single_stock_max": None}).is_empty


# ── 顺序结算：不要把同一笔仓位卖三遍 ─────────────────────────────────────

def test_the_chain_cut_accounts_for_what_the_single_name_cut_already_removed():
    # NVDA 22% + AMD 18% + MU 8% = AI芯片 48%（上限 40%）。单票上限 15% 先把
    # NVDA 削到 15、AMD 削到 15，链上就只剩 38% —— 已经在线内，产业链这一条
    # 不该再要求多卖一分钱。
    exposures = _exposures(
        {"NVDA": 22_000, "AMD": 18_000, "MU": 8_000},
        chains={"NVDA": "AI芯片", "AMD": "AI芯片", "MU": "AI芯片"},
    )
    plan = plan_rebalance(exposures, {"single_stock_max": 15.0, "chain_max": 40.0})

    by_symbol = {t.symbol: t.dollars for t in plan.trims}
    assert by_symbol == pytest.approx({"NVDA": 7_000, "AMD": 3_000})
    assert all("产业链集中度（AI芯片）" not in t.reasons for t in plan.trims)


def test_a_chain_still_over_after_the_single_name_cuts_is_trimmed_further():
    # 四只各 12%（单票 15% 都没超），链上 48%，上限 40% —— 必须再削 8 个点。
    holdings = {"NVDA": 12_000, "AMD": 12_000, "MU": 12_000, "TSM": 12_000}
    exposures = _exposures(holdings, chains={s: "AI芯片" for s in holdings})
    plan = plan_rebalance(exposures, {"single_stock_max": 15.0, "chain_max": 40.0})

    assert sum(t.dollars for t in plan.trims) == pytest.approx(8_000)
    assert all("产业链集中度（AI芯片）" in t.reasons for t in plan.trims)


def test_one_position_can_carry_two_reasons():
    exposures = _exposures(
        {"NVDA": 30_000, "AMD": 20_000},
        chains={"NVDA": "AI芯片", "AMD": "AI芯片"},
    )
    plan = plan_rebalance(exposures, {"single_stock_max": 15.0, "chain_max": 25.0})
    nvda = next(t for t in plan.trims if t.symbol == "NVDA")
    assert "单票集中度" in nvda.reasons
    assert any("产业链集中度" in r for r in nvda.reasons)


def test_chains_are_trimmed_independently_of_each_other():
    exposures = _exposures(
        {"NVDA": 25_000, "AMD": 25_000, "PLTR": 5_000},
        chains={"NVDA": "AI芯片", "AMD": "AI芯片", "PLTR": "AI软件/SaaS"},
    )
    plan = plan_rebalance(exposures, {"chain_max": 40.0})
    assert {t.symbol for t in plan.trims} == {"NVDA", "AMD"}


# ── 现金下限 ───────────────────────────────────────────────────────────

def test_selling_for_the_other_limits_already_refills_cash():
    # 现金 10%（下限 20%），但单票超限本来就要卖掉 12% —— 卖完现金到 22%，
    # 现金这条不该再要求多卖。
    exposures = _exposures({"NVDA": 27_000, "PLTR": 5_000}, cash=10_000)
    plan = plan_rebalance(
        exposures, {"single_stock_max": 15.0, "cash_floor_min": 20.0}
    )
    assert sum(t.dollars for t in plan.trims) == pytest.approx(12_000)
    assert plan.cash_pct_after == pytest.approx(22.0)


def test_cash_short_of_the_floor_is_raised_from_the_largest_holdings():
    exposures = _exposures({"NVDA": 10_000, "PLTR": 8_000}, cash=10_000)
    plan = plan_rebalance(exposures, {"cash_floor_min": 15.0})

    assert sum(t.dollars for t in plan.trims) == pytest.approx(5_000)
    assert plan.trims[0].symbol == "NVDA"
    assert plan.cash_pct_after == pytest.approx(15.0)


def test_a_cash_floor_nothing_could_reach_is_reported_not_silently_half_done():
    exposures = _exposures({"NVDA": 5_000}, equity=100_000.0, cash=1_000)
    plan = plan_rebalance(exposures, {"cash_floor_min": 50.0})
    assert plan.unresolved
    assert "现金下限" in plan.unresolved[0]


# ── 流动性 ─────────────────────────────────────────────────────────────

def test_a_position_too_big_to_exit_in_time_is_cut_to_what_fits():
    # ADV $1M，单日最多做 20% = $200k，5 天上限 = $1M 的仓位。持有 $30k、
    # ADV $10k 的小票：5 天只出得掉 $10k。
    exposures = _exposures(
        {"TINY": 30_000}, adv={"TINY": 10_000}
    )
    plan = plan_rebalance(
        exposures, {"liquidity_days_max": 5.0}, avg_dollar_volume={"TINY": 10_000}
    )
    assert plan.trims[0].dollars == pytest.approx(20_000)
    assert "流动性天数" in plan.trims[0].reasons


def test_a_ticker_with_no_volume_data_is_left_alone_rather_than_guessed_at():
    exposures = _exposures({"TINY": 30_000})
    plan = plan_rebalance(exposures, {"liquidity_days_max": 5.0}, avg_dollar_volume={})
    assert plan.is_empty


# ── 期权腿 ─────────────────────────────────────────────────────────────

def test_the_part_that_cannot_come_from_stock_is_flagged_not_turned_into_shares():
    # NVDA 敞口 22k，其中只有 4k 是现货，其余在价差里。要减 7k：4k 卖股票，
    # 剩下 3k 必须动期权腿，而减哪一腿不是这里能回答的。
    exposures = _exposures({"NVDA": 22_000})
    plan = plan_rebalance(
        exposures,
        {"single_stock_max": 15.0},
        prices={"NVDA": 200.0},
        stock_value={"NVDA": 4_000.0},
    )
    trim = plan.trims[0]
    assert trim.shares == 20                       # 4,000 / 200
    assert trim.option_dollars == pytest.approx(3_000)
    assert trim.needs_manual_option_leg


def test_rounding_dust_does_not_become_an_order():
    exposures = _exposures({"NVDA": 15_000 + MIN_TRIM_DOLLARS / 2})
    assert plan_rebalance(exposures, {"single_stock_max": 15.0}).is_empty


def test_no_equity_means_no_plan_rather_than_a_division_by_zero():
    from scoring.position_exposure import Exposures

    broken = Exposures(total_equity=0.0, cash_pct=0.0, by_ticker_pct={"NVDA": 50.0})
    assert plan_rebalance(broken, {"single_stock_max": 15.0}).is_empty
