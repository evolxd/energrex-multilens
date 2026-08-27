"""Tests for portfolio exposure measurement."""

import pytest

from scoring.position_exposure import (
    UNCLASSIFIED,
    approaching,
    breaches,
    compute_exposures,
    days_to_exit,
)

CHAINS = {"NVDA": "AI芯片", "AMD": "AI芯片", "MSFT": "大型科技"}


def chain_of(symbol):
    return CHAINS.get(symbol)


def pos(symbol, value):
    return {"symbol": symbol, "market_value": value}


def sample(cash=10_000.0, equity=100_000.0):
    return compute_exposures(
        [pos("NVDA", 30_000), pos("AMD", 15_000), pos("MSFT", 20_000)],
        equity,
        cash,
        chain_of,
    )


def test_single_stock_and_chain_readings():
    e = sample()
    assert e.max_single_stock_pct == pytest.approx(30.0)
    assert e.max_single_stock_ticker == "NVDA"
    # NVDA 30% + AMD 15% share a chain, so the chain reading is 45%, not 30%.
    assert e.max_chain_pct == pytest.approx(45.0)
    assert e.max_chain_name == "AI芯片"
    assert e.cash_pct == pytest.approx(10.0)


def test_unclassified_holdings_are_surfaced_not_dropped():
    """Silently dropping them would understate chain concentration exactly
    when a portfolio is full of untracked names."""
    e = compute_exposures(
        [pos("NVDA", 20_000), pos("WEIRDCO", 40_000)], 100_000.0, 0.0, chain_of
    )
    assert e.unclassified_tickers == ["WEIRDCO"]
    assert e.by_chain_pct[UNCLASSIFIED] == pytest.approx(40.0)
    assert e.max_chain_name == UNCLASSIFIED


def test_short_positions_count_by_size_not_direction():
    e = compute_exposures([pos("NVDA", -30_000)], 100_000.0, 0.0, chain_of)
    assert e.max_single_stock_pct == pytest.approx(30.0)


# ── Options ────────────────────────────────────────────────────────────

def test_options_are_grouped_under_their_underlying():
    """An account can hold most of its equity in option spreads and only a few
    percent in stock. Reading the stock table alone then reports a tiny largest
    name while one underlying actually stands at a large share of the book."""
    options = [
        pos("NVDA270115C00500000", 20_000),
        pos("NVDA270617C00600000", 30_000),
        pos("NVDA270617C00800000", -20_000),
        pos("NVDA270115C00850000", -10_000),
    ]
    e = compute_exposures([pos("MSFT", 5_000)], 100_000.0, 3_000.0, chain_of, options)
    assert e.max_single_stock_ticker == "NVDA"
    assert e.max_single_stock_pct == pytest.approx(20.0)
    assert e.by_ticker_pct["MSFT"] == pytest.approx(5.0)


def test_spread_legs_net_before_they_are_made_absolute():
    """Long 40k against short 25k of one name is a 15k bet. Taking each leg's
    magnitude would report 65k and treat a hedge as worse than an outright."""
    options = [
        pos("NVDA270617C00600000", 40_000),
        pos("NVDA270617C00800000", -25_000),
    ]
    e = compute_exposures([], 100_000.0, 0.0, chain_of, options)
    assert e.by_ticker_pct["NVDA"] == pytest.approx(15.0)


def test_stock_and_options_on_one_name_combine():
    options = [pos("NVDA270115C00210000", 4_000)]
    e = compute_exposures([pos("NVDA", 6_000)], 100_000.0, 0.0, chain_of, options)
    assert e.by_ticker_pct["NVDA"] == pytest.approx(10.0)


def test_option_underlying_inherits_the_chain():
    options = [pos("NVDA270115C00210000", 20_000)]
    e = compute_exposures([], 100_000.0, 0.0, chain_of, options)
    assert e.by_chain_pct["AI芯片"] == pytest.approx(20.0)
    assert e.unclassified_tickers == []


def test_a_fully_hedged_pair_contributes_nothing():
    options = [
        pos("NVDA270617C00600000", 10_000),
        pos("NVDA270617C00800000", -10_000),
    ]
    e = compute_exposures([], 100_000.0, 0.0, chain_of, options)
    assert "NVDA" not in e.by_ticker_pct


def test_non_occ_symbols_pass_through_unparsed():
    e = compute_exposures([], 100_000.0, 0.0, chain_of, [pos("NVDA", 5_000)])
    assert e.by_ticker_pct["NVDA"] == pytest.approx(5.0)


def test_duplicate_rows_for_one_symbol_are_summed():
    e = compute_exposures(
        [pos("NVDA", 10_000), pos("NVDA", 15_000)], 100_000.0, 0.0, chain_of
    )
    assert e.by_ticker_pct["NVDA"] == pytest.approx(25.0)


@pytest.mark.parametrize("equity", [None, 0.0, -1.0])
def test_unusable_equity_returns_nothing_rather_than_nonsense(equity):
    assert compute_exposures([pos("NVDA", 1.0)], equity, 0.0, chain_of) is None


def test_breach_detection_across_both_limit_kinds():
    e = sample(cash=3_000.0)
    found = breaches(
        e, {"single_stock_max": 15.0, "chain_max": 35.0, "cash_floor_min": 10.0}
    )
    keys = {item["key"] for item in found}
    assert keys == {"single_stock_max", "chain_max", "cash_floor_min"}
    # Worst overshoot first: chain is 10 over, single stock 15 over, cash 7 under.
    assert found[0]["key"] == "single_stock_max"
    assert found[0]["overshoot"] == pytest.approx(15.0)


def test_unset_limit_is_not_reported_as_compliant():
    e = sample()
    assert breaches(e, {"single_stock_max": None}) == []
    assert approaching(e, {"single_stock_max": None}) == []


def test_approaching_flags_the_near_miss_but_not_the_breach():
    e = compute_exposures([pos("NVDA", 14_000)], 100_000.0, 50_000.0, chain_of)
    near = approaching(e, {"single_stock_max": 15.0})
    assert [item["key"] for item in near] == ["single_stock_max"]
    # Once it is actually over, it belongs to breaches, not approaching.
    over = compute_exposures([pos("NVDA", 16_000)], 100_000.0, 50_000.0, chain_of)
    assert approaching(over, {"single_stock_max": 15.0}) == []
    assert [i["key"] for i in breaches(over, {"single_stock_max": 15.0})] == [
        "single_stock_max"
    ]


def test_cash_floor_is_approached_from_above():
    """A floor gets close when cash falls toward it, not when it rises."""
    low = compute_exposures([pos("NVDA", 89_000)], 100_000.0, 11_000.0, chain_of)
    assert [i["key"] for i in approaching(low, {"cash_floor_min": 10.0})] == [
        "cash_floor_min"
    ]
    high = compute_exposures([pos("NVDA", 50_000)], 100_000.0, 50_000.0, chain_of)
    assert approaching(high, {"cash_floor_min": 10.0}) == []


def test_breach_detail_names_the_offender():
    e = sample()
    found = breaches(e, {"single_stock_max": 15.0})
    assert found[0]["detail"] == "NVDA"


def test_empty_portfolio_is_not_a_breach_of_a_max_limit():
    e = compute_exposures([], 100_000.0, 100_000.0, chain_of)
    assert breaches(e, {"single_stock_max": 15.0, "chain_max": 35.0}) == []


# ── Liquidity / days-to-exit ────────────────────────────────────────────

def test_days_to_exit_basic_math():
    # $1M position, $2M average daily dollar volume, cap trading at 20% of
    # that per day -> $400k/day capacity -> 2.5 days to unwind.
    assert days_to_exit(1_000_000, 2_000_000) == pytest.approx(2.5)


def test_days_to_exit_ignores_position_sign():
    # A short leg's negative market value is still a real position to exit.
    assert days_to_exit(-1_000_000, 2_000_000) == pytest.approx(2.5)


def test_days_to_exit_none_without_volume_data():
    assert days_to_exit(1_000_000, None) is None
    assert days_to_exit(1_000_000, 0) is None
    assert days_to_exit(1_000_000, -5) is None


def test_days_to_exit_respects_custom_participation_rate():
    # Trading only 10% of ADV per day doubles the time to unwind.
    assert days_to_exit(1_000_000, 2_000_000, max_pct_of_adv=0.10) == pytest.approx(5.0)


def test_a_ticker_without_volume_data_is_left_out_not_defaulted_liquid():
    """Missing coverage must read as "unknown," never as "0 days, all clear" --
    for_limit()'s None (not 0.0) is what lets the UI tell the two apart."""
    e = compute_exposures(
        [pos("NVDA", 30_000)], 100_000.0, 10_000.0, chain_of,
        avg_dollar_volume={},  # supplied but empty: NVDA not covered
    )
    assert e.by_ticker_days_to_exit == {}
    assert e.for_limit("liquidity_days_max") is None


def test_liquidity_reading_flows_through_for_limit_and_breaches():
    e = compute_exposures(
        [pos("NVDA", 30_000), pos("AMD", 15_000)], 100_000.0, 10_000.0, chain_of,
        avg_dollar_volume={"NVDA": 200_000, "AMD": 5_000_000},
    )
    # NVDA: 30_000 / (200_000 * 0.20) = 0.75 days. AMD: 15_000 / (5_000_000*0.20) = 0.015 days.
    assert e.by_ticker_days_to_exit["NVDA"] == pytest.approx(0.75)
    assert e.max_days_to_exit == pytest.approx(0.75)
    assert e.max_days_to_exit_ticker == "NVDA"
    assert e.for_limit("liquidity_days_max") == pytest.approx(0.75)

    found = breaches(e, {"liquidity_days_max": 0.5})
    assert [i["key"] for i in found] == ["liquidity_days_max"]
    assert found[0]["detail"] == "NVDA"
