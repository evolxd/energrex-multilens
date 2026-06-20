"""
Polygon.io financial fundamentals fetcher.
Replaces yfinance AND Alpha Vantage as primary data source.
Fields covered:
  gross_margin, operating_margin, net_income_margin
  revenue_growth_yoy, debt_to_equity
  current_price, market_cap, ps_ratio, ev_sales, fcf_margin (operating CF proxy)
  actual_eps_vs_consensus  — EPS YoY growth proxy (replaces AV EARNINGS)
  analyst_revision_30d     — post-filing 20-day price return (replaces AV EARNINGS)
  earnings_reaction_score  — news sentiment from /v2/reference/news
"""

import os
import time
import logging
from typing import Optional, Callable
import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.polygon.io"

# Rate limit: set POLYGON_CALLS_PER_MIN=100 for paid Starter plan; default 5 (free tier).
# Sleep is inserted after every API call to stay within the plan's rate limit.
_CALLS_PER_MIN: int = max(1, int(os.environ.get("POLYGON_CALLS_PER_MIN", "5")))
_SLEEP: float       = 60.0 / _CALLS_PER_MIN

_MAX_RETRIES   = 3
_BACKOFF_BASE  = 15  # seconds for first 429 backoff; doubles each retry


def _key() -> str:
    k = os.environ.get("POLYGON_API_KEY", "")
    if not k:
        raise RuntimeError("POLYGON_API_KEY not set")
    return k


def _get(url: str, params: dict | None = None, timeout: int = 20,
         _retry: int = 0) -> Optional[dict]:
    try:
        p = dict(params or {})
        p["apiKey"] = _key()
        r = requests.get(url, params=p, timeout=timeout)

        if r.status_code == 429:
            # Rate limited — exponential backoff then retry
            wait = _BACKOFF_BASE * (2 ** _retry)
            logger.warning("Polygon 429 rate limit, backing off %ds (retry %d/%d)",
                           wait, _retry + 1, _MAX_RETRIES)
            time.sleep(wait)
            if _retry < _MAX_RETRIES:
                return _get(url, params, timeout, _retry + 1)
            logger.error("Polygon 429 max retries exceeded: %s", url)
            return None

        if r.status_code == 402:
            logger.warning("Polygon 402 (plan limit): %s", url)
            return None

        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        logger.warning("Polygon request failed %s: %s", url, e)
        return None


def _safe(d: Optional[dict], *keys, default=None):
    """Nested dict safe-get."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


# ─────────────────────────────────────────────────────────────────────
# Financials
# ─────────────────────────────────────────────────────────────────────

def _fetch_financials(ticker: str, limit: int = 5) -> list[dict]:
    """Fetch last N quarterly filings from Polygon vX financials."""
    data = _get(f"{_BASE}/vX/reference/financials",
                {"ticker": ticker, "limit": limit, "timeframe": "quarterly"})
    time.sleep(_SLEEP)
    return _safe(data, "results") or []


def _v(fin_section: dict, field: str) -> Optional[float]:
    """Extract .value from a financials section field."""
    return _safe(fin_section, field, "value")


def _compute_quarterly_margins(q: dict) -> dict:
    """Derive margin ratios from a single quarterly filing."""
    out = {}
    is_ = _safe(q, "financials", "income_statement") or {}
    cf  = _safe(q, "financials", "cash_flow_statement") or {}

    rev   = _v(is_, "revenues")
    gp    = _v(is_, "gross_profit")
    op    = _v(is_, "operating_income_loss")
    ni    = _v(is_, "net_income_loss")
    op_cf = _v(cf,  "net_cash_flow_from_operating_activities")

    if rev and rev > 0:
        if gp  is not None: out["gross_margin"]        = round(gp  / rev, 4)
        if op  is not None: out["operating_margin"]    = round(op  / rev, 4)
        if ni  is not None: out["net_income_margin"]   = round(ni  / rev, 4)
        if op_cf is not None:
            # No capex breakdown on free plan; operating CF / revenue is the best proxy
            out["fcf_margin"] = round(op_cf / rev, 4)

    return out


def _compute_revenue_growth(quarters: list[dict]) -> Optional[float]:
    """
    YoY revenue growth: compare most recent quarter to same quarter prior year.
    Matches by fiscal_year / fiscal_period to avoid seasonal mismatch.
    """
    if len(quarters) < 2:
        return None
    latest = quarters[0]
    latest_fy  = _safe(latest, "fiscal_year")
    latest_fp  = _safe(latest, "fiscal_period")
    latest_rev = _v(_safe(latest, "financials", "income_statement") or {}, "revenues")
    if not (latest_fy and latest_fp and latest_rev):
        return None

    # Find same period prior year
    for q in quarters[1:]:
        if _safe(q, "fiscal_period") == latest_fp:
            prior_rev = _v(_safe(q, "financials", "income_statement") or {}, "revenues")
            if prior_rev and prior_rev > 0:
                return round((latest_rev - prior_rev) / prior_rev, 4)
    return None


def _compute_balance_sheet(q: dict) -> dict:
    """D/E and EV components from balance sheet."""
    out = {}
    bs = _safe(q, "financials", "balance_sheet") or {}

    equity     = _v(bs, "equity")
    long_debt  = _v(bs, "long_term_debt") or 0.0
    cur_assets = _v(bs, "current_assets")
    cur_liabs  = _v(bs, "current_liabilities")
    liabs      = _v(bs, "liabilities")

    if equity and equity > 0:
        out["debt_to_equity"] = round(long_debt / equity, 4)

    # Net cash proxy: current_assets - current_liabilities (working capital)
    if cur_assets is not None and cur_liabs is not None:
        out["_net_working_capital"] = cur_assets - cur_liabs

    out["_long_term_debt"]  = long_debt
    out["_total_equity"]    = equity
    out["_total_liabilities"] = liabs

    return out


# ─────────────────────────────────────────────────────────────────────
# Price / market cap
# ─────────────────────────────────────────────────────────────────────

def _fetch_price(ticker: str) -> dict:
    """
    Fetch latest close price from daily agg bars (last 5 trading days).
    Falls back to reference ticker market_cap / shares for price estimate.
    """
    import datetime
    end   = datetime.date.today().isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    data  = _get(f"{_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
                 {"adjusted": "true", "sort": "desc", "limit": 3})
    time.sleep(_SLEEP)
    results = _safe(data, "results") or []
    if results:
        return {"current_price": results[0].get("c")}
    return {}


def _fetch_reference(ticker: str) -> dict:
    """Market cap and shares from reference endpoint."""
    data = _get(f"{_BASE}/v3/reference/tickers/{ticker}")
    time.sleep(_SLEEP)
    res = _safe(data, "results") or {}
    out = {}
    mc = res.get("market_cap")
    if mc:
        out["market_cap"] = mc
    return out


# ─────────────────────────────────────────────────────────────────────
# Main per-ticker fetch
# ─────────────────────────────────────────────────────────────────────

def fetch_polygon_fundamentals(ticker: str) -> dict:
    """
    Fetch and derive all available fundamental fields for a ticker.
    Returns a flat dict of field→value, compatible with mock_data schema.
    """
    out = {}

    # --- Financial statements (5 quarters for YoY growth and EPS proxy) ---
    quarters = _fetch_financials(ticker, limit=5)
    filing_date: Optional[str] = None
    if not quarters:
        logger.warning("Polygon: no financials for %s", ticker)
    else:
        margins = _compute_quarterly_margins(quarters[0])
        out.update(margins)

        rev_growth = _compute_revenue_growth(quarters)
        if rev_growth is not None:
            out["revenue_growth_yoy"] = rev_growth

        bs_data = _compute_balance_sheet(quarters[0])
        out.update(bs_data)

        # EPS YoY proxy → actual_eps_vs_consensus
        eps_proxy = _compute_eps_yoy(quarters)
        if eps_proxy is not None:
            out["actual_eps_vs_consensus"] = eps_proxy

        # Save filing_date for post-filing return calc below
        filing_date = _safe(quarters[0], "filing_date")

    # --- Price ---
    price_data = _fetch_price(ticker)
    out.update(price_data)

    # --- Market cap ---
    ref_data = _fetch_reference(ticker)
    out.update(ref_data)

    # --- Derived valuation ratios ---
    price = out.get("current_price")
    mc    = out.get("market_cap")

    # TTM revenue from 4 quarters
    ttm_rev = None
    if len(quarters) >= 4:
        revs = []
        for q in quarters[:4]:
            r = _v(_safe(q, "financials", "income_statement") or {}, "revenues")
            if r is not None:
                revs.append(r)
        if len(revs) == 4:
            ttm_rev = sum(revs)

    if mc and ttm_rev and ttm_rev > 0:
        out["ps_ratio"] = round(mc / ttm_rev, 2)

        lt_debt = out.get("_long_term_debt", 0) or 0
        nwc     = out.get("_net_working_capital", 0) or 0
        # EV = market_cap + long_term_debt - net_working_capital (cash proxy)
        ev = mc + lt_debt - max(nwc, 0)
        out["ev_sales"] = round(ev / ttm_rev, 2)

    if mc and price and price > 0:
        # TTM EPS → trailing PE
        ttm_ni = None
        if len(quarters) >= 4:
            nis = []
            for q in quarters[:4]:
                n = _v(_safe(q, "financials", "income_statement") or {}, "net_income_loss")
                if n is not None:
                    nis.append(n)
            if len(nis) == 4:
                ttm_ni = sum(nis)
        shares = mc / price
        if ttm_ni and shares and shares > 0:
            ttm_eps = ttm_ni / shares
            if ttm_eps > 0:
                out["pe_ratio"] = round(price / ttm_eps, 2)

    # Drop internal helper keys
    for k in ["_long_term_debt", "_total_equity", "_total_liabilities", "_net_working_capital"]:
        out.pop(k, None)

    # --- Post-filing price return → analyst_revision_30d proxy ---
    if filing_date:
        post_ret = _fetch_post_filing_return(ticker, filing_date)
        if post_ret is not None:
            out["analyst_revision_30d"] = post_ret

    # --- News sentiment → earnings_reaction_score proxy ---
    sentiment = _fetch_news_sentiment(ticker)
    if sentiment is not None:
        out["earnings_reaction_score"] = sentiment

    logger.info("Polygon [%s]: %d fields fetched", ticker, len(out))
    return out


# ─────────────────────────────────────────────────────────────────────
# AV replacement: EPS proxy, post-filing return, news sentiment
# ─────────────────────────────────────────────────────────────────────

def _compute_eps_yoy(quarters: list[dict]) -> Optional[float]:
    """
    EPS YoY growth proxy for actual_eps_vs_consensus.
    Compares diluted_earnings_per_share same fiscal period year-over-year.
    Maps to [-0.10, 0.15] range matching the original AV surprise percentage scale.
    100% YoY EPS growth → ~0.10 (strong beat signal).
    """
    if len(quarters) < 2:
        return None
    latest    = quarters[0]
    latest_fp = _safe(latest, "fiscal_period")
    latest_is = _safe(latest, "financials", "income_statement") or {}
    latest_eps = _v(latest_is, "diluted_earnings_per_share")
    if not (latest_fp and latest_eps):
        return None
    for q in quarters[1:]:
        if _safe(q, "fiscal_period") == latest_fp:
            prior_is  = _safe(q, "financials", "income_statement") or {}
            prior_eps = _v(prior_is, "diluted_earnings_per_share")
            if prior_eps and prior_eps > 0:
                growth = (latest_eps - prior_eps) / prior_eps
                # Compress: divide by 10 so 100% growth → 0.10 signal
                proxy = growth / 10.0
                return round(float(max(-0.10, min(0.15, proxy))), 4)
    return None


def _fetch_post_filing_return(ticker: str, filing_date: str) -> Optional[float]:
    """
    Post-filing 20-trading-day price return as analyst_revision_30d proxy.
    Positive return after earnings filing → analysts revised estimates up.
    Returns value clamped to [-0.50, 0.50].
    """
    import datetime
    try:
        start = datetime.date.fromisoformat(filing_date)
        end   = start + datetime.timedelta(days=35)  # ~25 trading days window
        today = datetime.date.today()
        if end > today:
            end = today
        if (end - start).days < 5:
            return None
        data = _get(
            f"{_BASE}/v2/aggs/ticker/{ticker}/range/1/day"
            f"/{start.isoformat()}/{end.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": 25},
        )
        time.sleep(_SLEEP)
        results = _safe(data, "results") or []
        if len(results) < 5:
            return None
        first_close = results[0].get("c")
        last_close  = results[-1].get("c")
        if not (first_close and last_close and first_close > 0):
            return None
        ret = (last_close - first_close) / first_close
        return round(float(max(-0.50, min(0.50, ret))), 4)
    except Exception as e:
        logger.warning("Polygon post-filing return failed for %s: %s", ticker, e)
        return None


def _fetch_news_sentiment(ticker: str) -> Optional[float]:
    """
    Recent news sentiment from Polygon /v2/reference/news insights field.
    Positive majority → 0.05; negative → -0.05; neutral → 0.0.
    Used as a light earnings_reaction_score proxy.
    """
    data = _get(f"{_BASE}/v2/reference/news",
                {"ticker": ticker, "limit": 5, "sort": "published_utc"})
    time.sleep(_SLEEP)
    results = _safe(data, "results") or []
    sentiments = []
    for article in results:
        for insight in (article.get("insights") or []):
            if insight.get("ticker") == ticker:
                s = insight.get("sentiment")
                if s == "positive":
                    sentiments.append(1)
                elif s == "negative":
                    sentiments.append(-1)
                elif s == "neutral":
                    sentiments.append(0)
    if not sentiments:
        return None
    avg = sum(sentiments) / len(sentiments)
    return round(float(avg * 0.10), 4)


def fetch_polygon_portfolio(
    tickers: list[str],
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, dict]:
    """
    Fetch fundamentals for all tickers (~8 API calls each).
    progress_cb(done, total, ticker) called after each ticker completes.
    Rate: controlled by POLYGON_CALLS_PER_MIN env var (default 5 = free tier).
    """
    results = {}
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        try:
            results[ticker] = fetch_polygon_fundamentals(ticker)
        except Exception as e:
            logger.error("Polygon: failed for %s: %s", ticker, e)
            results[ticker] = {}
        if progress_cb:
            progress_cb(i + 1, total, ticker)
    return results
