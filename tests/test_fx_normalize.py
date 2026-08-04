"""Tests for quote/statement currency normalization.

Numbers are the real Yahoo readings for TSM and ASML on 2026-08-02.
"""

import pytest

from scoring.fx_normalize import (
    fetch_rate,
    fx_symbol,
    needs_normalization,
    normalize_info,
)

TWD_USD = 0.0308166
EUR_USD = 1.1504833

# TSM: quote in USD, statements in TWD.
TSM = {
    "currency": "USD",
    "financialCurrency": "TWD",
    "currentPrice": 406.62,
    "marketCap": 2_108_923_969_536.0,
    "forwardEps": 16.63,
    "totalRevenue": 4_440_492_343_296.0,
    "freeCashflow": 739_057_991_680.0,
    "ebitda": 3_175_000_000_000.0,
    "totalDebt": 0.0,
    "totalCash": 2_536_000_000_000.0,
    "enterpriseValue": 14_279_204_929_536.0,
    "enterpriseToRevenue": 3.216,
    "sharesOutstanding": 5_186_474_013,
    "beta": 1.3,
}

DOMESTIC = {
    "currency": "USD",
    "financialCurrency": "USD",
    "marketCap": 1_000.0,
    "totalRevenue": 500.0,
    "freeCashflow": 50.0,
    "enterpriseValue": 1_100.0,
}


def test_mismatch_is_detected_only_when_currencies_differ():
    assert needs_normalization(TSM)
    assert not needs_normalization(DOMESTIC)
    assert not needs_normalization({"currency": "USD"})
    assert not needs_normalization({})


def test_fx_symbol_shape():
    assert fx_symbol("TWD", "USD") == "TWDUSD=X"
    assert fx_symbol("eur", "usd") == "EURUSD=X"
    assert fx_symbol("USD", "USD") is None
    assert fx_symbol("", "USD") is None


def test_domestic_ticker_is_untouched():
    out, report = normalize_info(DOMESTIC, 1.0)
    assert out == DOMESTIC
    assert not report["applied"]
    assert report["converted"] == []


# ── The defect this module exists for ──────────────────────────────────

def test_fcf_yield_becomes_plausible_after_conversion():
    """Mixed currency gave 35.06%. TSMC does not yield 35% of its market cap
    in free cash flow."""
    before = TSM["freeCashflow"] / TSM["marketCap"]
    assert before == pytest.approx(0.3504, abs=0.001)

    out, _ = normalize_info(TSM, TWD_USD)
    after = out["freeCashflow"] / out["marketCap"]
    assert after == pytest.approx(0.0108, abs=0.001)


def test_quote_side_fields_are_not_scaled():
    """Price, market cap and forward EPS are already in the quote currency.
    Converting them too would produce a different, equally wrong answer."""
    out, _ = normalize_info(TSM, TWD_USD)
    assert out["currentPrice"] == TSM["currentPrice"]
    assert out["marketCap"] == TSM["marketCap"]
    assert out["forwardEps"] == TSM["forwardEps"]


def test_share_count_and_ratios_are_not_scaled():
    out, _ = normalize_info(TSM, TWD_USD)
    assert out["sharesOutstanding"] == TSM["sharesOutstanding"]
    assert out["beta"] == TSM["beta"]


def test_statement_fields_are_scaled():
    out, report = normalize_info(TSM, TWD_USD)
    assert out["totalRevenue"] == pytest.approx(TSM["totalRevenue"] * TWD_USD)
    assert out["ebitda"] == pytest.approx(TSM["ebitda"] * TWD_USD)
    # ~$137B of revenue, not ~NT$4.4tn.
    assert 1.3e11 < out["totalRevenue"] < 1.5e11
    assert "totalRevenue" in report["converted"]
    assert "freeCashflow" in report["converted"]


# ── Enterprise value ───────────────────────────────────────────────────

def test_yahoo_enterprise_value_is_discarded_not_scaled():
    """1.428e13 reconciles with neither currency: the TWD figure would be
    ~6.6e13 and the USD one ~2.0e12. Scaling a broken number keeps it broken."""
    out, report = normalize_info(TSM, TWD_USD)
    assert out["enterpriseValue"] != TSM["enterpriseValue"] * TWD_USD
    assert "enterpriseValue" in report["recomputed"]


def test_enterprise_value_is_rebuilt_from_market_cap_and_net_cash():
    out, _ = normalize_info(TSM, TWD_USD)
    expected = TSM["marketCap"] - TSM["totalCash"] * TWD_USD
    assert out["enterpriseValue"] == pytest.approx(expected)
    # ~$2.03T for a ~$2.11T market cap holding ~$78B net cash.
    assert 1.9e12 < out["enterpriseValue"] < 2.1e12


def test_ev_ratios_land_near_the_hand_verified_overrides():
    """user_overrides.json carries manually checked EV/Sales 13.36 and
    EV/EBITDA 18.34 for TSM. Yahoo's own enterpriseToRevenue said 3.216."""
    out, _ = normalize_info(TSM, TWD_USD)
    assert 12.0 < out["enterpriseToRevenue"] < 17.0
    assert 17.0 < out["enterpriseToEbitda"] < 24.0


def test_asml_scale_error_is_the_same_defect():
    """EUR/USD is only ~1.15, so ASML's error looked like ordinary staleness
    rather than a unit mistake. 17.55 * 1.1505 = 20.19, against a hand-checked
    override of 20.39."""
    assert 17.55 * EUR_USD == pytest.approx(20.19, abs=0.05)


# ── No rate available ──────────────────────────────────────────────────

@pytest.mark.parametrize("rate", [None, 0.0, -1.0])
def test_without_a_rate_affected_fields_are_dropped(rate):
    """A missing input scores as missing. A mixed-currency input scores as
    excellent, which is the failure mode worth avoiding."""
    out, report = normalize_info(TSM, rate)
    for field in ("totalRevenue", "freeCashflow", "ebitda", "enterpriseValue"):
        assert field not in out
    assert not report["applied"]
    assert "无法取得汇率" in report["reason"]


def test_dropping_leaves_the_quote_side_intact():
    out, _ = normalize_info(TSM, None)
    assert out["currentPrice"] == TSM["currentPrice"]
    assert out["marketCap"] == TSM["marketCap"]


def test_report_names_the_currencies():
    _, report = normalize_info(TSM, TWD_USD)
    assert report["quote_currency"] == "USD"
    assert report["statement_currency"] == "TWD"
    assert "TWD" in report["reason"] and "USD" in report["reason"]


# ── Rate fetching ──────────────────────────────────────────────────────

def test_fetch_rate_uses_the_pair_symbol():
    seen = []

    class FakeTicker:
        def __init__(self, symbol):
            seen.append(symbol)
            self.fast_info = type("FI", (), {"last_price": TWD_USD})()

    assert fetch_rate("TWD", "USD", FakeTicker) == pytest.approx(TWD_USD)
    assert seen == ["TWDUSD=X"]


def test_fetch_rate_returns_none_on_failure_rather_than_raising():
    def boom(symbol):
        raise RuntimeError("network down")

    assert fetch_rate("TWD", "USD", boom) is None


def test_fetch_rate_rejects_a_nonsense_rate():
    class Zero:
        def __init__(self, symbol):
            self.fast_info = type("FI", (), {"last_price": 0.0})()

    assert fetch_rate("TWD", "USD", Zero) is None


def test_same_currency_needs_no_fetch():
    def boom(symbol):  # pragma: no cover - must not be called
        raise AssertionError("should not fetch for a matching pair")

    assert fetch_rate("USD", "USD", boom) is None
