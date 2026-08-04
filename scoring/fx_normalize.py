"""Put a quote and its financial statements into the same currency.

Yahoo reports two currencies per ticker and they are not always the same.
For a US-listed ADR the quote is in USD while the statements stay in the
company's reporting currency:

    TSM   currency=USD  financialCurrency=TWD
    ASML  currency=USD  financialCurrency=EUR

Nothing in the pipeline read `financialCurrency`, so any figure combining the
two silently mixed units. `fcf_yield = freeCashflow / marketCap` divided TWD
by USD and produced 35.06% for TSM, roughly thirty times the real ~1.1%, and
a maximum score on that input. ASML had the same defect at EUR scale, where a
15% error was small enough to look like ordinary staleness.

Two rules here are worth stating because they are not obvious:

Enterprise value is recomputed rather than converted. Yahoo's own
`enterpriseValue` for TSM reconciles with neither currency -- 1.428e13 is not
the TWD figure (that would be ~6.6e13) nor the USD one (~2.0e12) -- so when
the currencies differ it is discarded and EV is rebuilt from market cap and
converted net debt. Ratios derived from it are rebuilt too.

Without a rate, affected fields are dropped rather than passed through. A
missing input scores as missing; a mixed-currency input scores as excellent.
"""

from __future__ import annotations

from typing import Any, Mapping

# Denominated in the company's reporting currency.
STATEMENT_FIELDS = frozenset(
    {
        "totalRevenue",
        "freeCashflow",
        "operatingCashflow",
        "ebitda",
        "totalDebt",
        "totalCash",
        "grossProfits",
        "netIncomeToCommon",
    }
)

# Denominated in the quote currency, alongside the price. forwardEps belongs
# here: it pairs with the quoted price to give forwardPE, and TSM's forwardPE
# of 24.45 only makes sense with a USD EPS.
QUOTE_FIELDS = frozenset({"currentPrice", "regularMarketPrice", "marketCap", "forwardEps"})

# Discarded when currencies differ: Yahoo derives these from an enterprise
# value that does not reconcile, so they cannot be repaired by scaling.
DERIVED_FROM_BROKEN_EV = frozenset(
    {"enterpriseValue", "enterpriseToRevenue", "enterpriseToEbitda"}
)


def fx_symbol(from_currency: str, to_currency: str) -> str | None:
    """Yahoo's FX ticker for one currency into another, e.g. TWDUSD=X."""
    if not from_currency or not to_currency:
        return None
    a, b = from_currency.strip().upper(), to_currency.strip().upper()
    if a == b:
        return None
    return f"{a}{b}=X"


def needs_normalization(info: Mapping[str, Any]) -> bool:
    quote = (info.get("currency") or "").strip().upper()
    statement = (info.get("financialCurrency") or "").strip().upper()
    return bool(quote and statement and quote != statement)


def normalize_info(
    info: Mapping[str, Any],
    rate: float | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return statements restated into the quote currency, plus a report.

    `rate` converts one unit of the statement currency into the quote
    currency -- 0.0308 for TWD into USD. A rate of None means it could not be
    obtained, and the affected fields are removed.
    """
    out = dict(info)
    quote = (info.get("currency") or "").strip().upper()
    statement = (info.get("financialCurrency") or "").strip().upper()

    report: dict[str, Any] = {
        "applied": False,
        "quote_currency": quote,
        "statement_currency": statement,
        "rate": rate,
        "converted": [],
        "dropped": [],
        "recomputed": [],
        "reason": "",
    }

    if not needs_normalization(info):
        report["reason"] = "报价币与财报币一致，无需换算。"
        return out, report

    if not rate or rate <= 0:
        for field in list(STATEMENT_FIELDS | DERIVED_FROM_BROKEN_EV):
            if field in out:
                del out[field]
                report["dropped"].append(field)
        report["reason"] = (
            f"报价币 {quote} 与财报币 {statement} 不一致，且无法取得汇率；"
            "相关字段已剔除，按缺失计，不输出混算结果。"
        )
        return out, report

    for field in STATEMENT_FIELDS:
        value = out.get(field)
        if value is None:
            continue
        try:
            out[field] = float(value) * rate
        except (TypeError, ValueError):
            continue
        report["converted"].append(field)

    # Yahoo's enterprise value does not reconcile in either currency here, so
    # rebuild it from market cap (already in the quote currency) and the net
    # debt we just converted.
    for field in DERIVED_FROM_BROKEN_EV:
        if field in out:
            del out[field]
            report["dropped"].append(field)

    market_cap = out.get("marketCap")
    total_debt = out.get("totalDebt")
    total_cash = out.get("totalCash")
    if market_cap:
        net_debt = (total_debt or 0.0) - (total_cash or 0.0)
        enterprise_value = float(market_cap) + net_debt
        out["enterpriseValue"] = enterprise_value
        report["recomputed"].append("enterpriseValue")

        revenue = out.get("totalRevenue")
        if revenue:
            out["enterpriseToRevenue"] = enterprise_value / float(revenue)
            report["recomputed"].append("enterpriseToRevenue")
        ebitda = out.get("ebitda")
        if ebitda:
            out["enterpriseToEbitda"] = enterprise_value / float(ebitda)
            report["recomputed"].append("enterpriseToEbitda")

    report["applied"] = True
    report["reason"] = (
        f"财报币 {statement} 已按 {rate:.6g} 换算为报价币 {quote}；"
        "企业价值由市值与换算后净负债重算。"
    )
    report["converted"].sort()
    report["dropped"].sort()
    return out, report


def fetch_rate(from_currency: str, to_currency: str, ticker_factory) -> float | None:
    """Spot rate via Yahoo's FX pair, or None.

    Spot against TTM statements is an approximation: a trailing-twelve-month
    revenue figure accumulated across a year of moving rates is not exactly
    convertible at today's price. It is the wrong answer by a few percent, as
    against thirty times wrong, and the alternative -- refusing to score any
    foreign issuer -- is worse.
    """
    symbol = fx_symbol(from_currency, to_currency)
    if symbol is None:
        return None
    try:
        rate = float(ticker_factory(symbol).fast_info.last_price)
    except Exception:
        return None
    return rate if rate > 0 else None
