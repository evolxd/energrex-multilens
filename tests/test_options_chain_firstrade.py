"""Tests for scoring/options_chain.py's Firstrade path.

Only _build_firstrade_chain_df is unit-tested here -- it's the pure
row-merge -> DataFrame transform. fetch_chain_firstrade/
fetch_expirations_firstrade themselves need a live CDP-attached, logged-in
Chrome (verified manually against a real Firstrade session on 2026-09-06,
see the commit this file ships with); there's nothing meaningful to fake
about a Selenium attach without just testing mocks calling mocks.
"""
import datetime
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
from options_chain import _build_firstrade_chain_df

_TODAY = datetime.date(2026, 9, 6)


def _row(symbol, side, strike, bid, ask, last, volume=0, oi=0,
         iv=None, delta=None, gamma=None, theta=None, vega=None):
    return {
        "symbol": symbol, "side": side, "strike": strike, "bid": bid,
        "ask": ask, "last": last, "volume": volume, "oi": oi,
        "iv": iv, "delta": delta, "gamma": gamma, "theta": theta, "vega": vega,
    }


def test_empty_rows_returns_empty_dataframe():
    assert _build_firstrade_chain_df([], "2026-10-16", 180.0).empty


def test_columns_match_marketdata_shape():
    """Same consumers (bull_put_spread_module.py etc.) read this DataFrame
    regardless of which data source produced it -- column names must match
    _parse_chain()'s MarketData.app output exactly."""
    rows = [_row("NVDA261016C00180000", "call", 180.0, 5.0, 5.2, 5.1,
                 iv=0.4, delta=0.55, gamma=0.01, theta=-0.05, vega=0.2)]
    df = _build_firstrade_chain_df(rows, "2026-10-16", 182.0, today=_TODAY)
    expected = {"symbol", "side", "strike", "bid", "ask", "last", "volume", "oi",
                "iv", "delta", "gamma", "theta", "vega", "mid", "und_px", "itm",
                "dte", "exp", "iv_pct"}
    assert expected.issubset(set(df.columns))


def test_mid_is_bid_ask_average_when_both_present():
    rows = [_row("T260101C00100000", "call", 100.0, 4.0, 4.4, 4.2)]
    df = _build_firstrade_chain_df(rows, "2026-10-16", 100.0, today=_TODAY)
    assert df["mid"].iloc[0] == pytest.approx(4.2)


def test_mid_falls_back_to_last_when_bid_or_ask_missing_or_nonpositive():
    rows = [
        _row("T260101C00100000", "call", 100.0, 0.0, 4.4, 4.35),   # bid=0
        _row("T260101P00100000", "put", 100.0, None, 1.2, 1.10),    # bid missing
    ]
    df = _build_firstrade_chain_df(rows, "2026-10-16", 100.0, today=_TODAY)
    assert df["mid"].iloc[0] == pytest.approx(4.35)
    assert df["mid"].iloc[1] == pytest.approx(1.10)


def test_itm_call_below_spot_and_put_above_spot():
    rows = [
        _row("T260101C00100000", "call", 100.0, 1, 1.1, 1.05),   # spot 120 -> ITM call
        _row("T260101C00150000", "call", 150.0, 1, 1.1, 1.05),   # spot 120 -> OTM call
        _row("T260101P00150000", "put", 150.0, 1, 1.1, 1.05),    # spot 120 -> ITM put
        _row("T260101P00100000", "put", 100.0, 1, 1.1, 1.05),    # spot 120 -> OTM put
    ]
    df = _build_firstrade_chain_df(rows, "2026-10-16", 120.0, today=_TODAY)
    itm_by_strike_side = {(r.strike, r.side): r.itm for r in df.itertuples()}
    assert itm_by_strike_side[(100.0, "call")] is True
    assert itm_by_strike_side[(150.0, "call")] is False
    assert itm_by_strike_side[(150.0, "put")] is True
    assert itm_by_strike_side[(100.0, "put")] is False


def test_itm_is_none_when_spot_unavailable():
    rows = [_row("T260101C00100000", "call", 100.0, 1, 1.1, 1.05)]
    df = _build_firstrade_chain_df(rows, "2026-10-16", None, today=_TODAY)
    assert df["itm"].iloc[0] is None
    assert pd.isna(df["und_px"].iloc[0])


def test_dte_computed_from_expiration_and_today():
    rows = [_row("T261016C00100000", "call", 100.0, 1, 1.1, 1.05)]
    df = _build_firstrade_chain_df(rows, "2026-10-16", 100.0, today=_TODAY)
    assert df["dte"].iloc[0] == (datetime.date(2026, 10, 16) - _TODAY).days


def test_dte_floors_at_zero_for_past_expirations():
    rows = [_row("T260101C00100000", "call", 100.0, 1, 1.1, 1.05)]
    df = _build_firstrade_chain_df(rows, "2026-01-01", 100.0, today=_TODAY)
    assert df["dte"].iloc[0] == 0


def test_iv_pct_is_iv_times_100():
    rows = [_row("T260101C00100000", "call", 100.0, 1, 1.1, 1.05, iv=0.4523)]
    df = _build_firstrade_chain_df(rows, "2026-10-16", 100.0, today=_TODAY)
    assert df["iv_pct"].iloc[0] == pytest.approx(45.23)


def test_missing_greeks_stay_nan_not_fabricated():
    """A contract the greeks endpoint didn't return anything for (thin/no-OI
    strikes are sometimes dropped by the vendor) must show NaN, not 0 or an
    invented placeholder."""
    rows = [_row("T260101C00100000", "call", 100.0, 1, 1.1, 1.05,
                 iv=None, delta=None, gamma=None, theta=None, vega=None)]
    df = _build_firstrade_chain_df(rows, "2026-10-16", 100.0, today=_TODAY)
    assert pd.isna(df["iv"].iloc[0])
    assert pd.isna(df["delta"].iloc[0])
    assert pd.isna(df["iv_pct"].iloc[0])
