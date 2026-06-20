"""
APScheduler jobs:
  - market_refresh : every 4 hours — yfinance + Polygon + MarketData + score
  - edgar_refresh  : every 7 days  — extract AI-exposure fields from SEC EDGAR
Merge priority (lowest → highest):
  mock_data → yfinance → Polygon → MarketData.app → EDGAR → user overrides
"""
import sys
import os
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scoring"))

logger = logging.getLogger(__name__)

# Derive ticker list from mock_data so new tickers are picked up automatically.
def _get_tickers() -> list[str]:
    try:
        from mock_data import MOCK_STOCKS
        return list(MOCK_STOCKS.keys())
    except Exception:
        return ["NVDA", "AVGO", "MRVL", "PLTR", "SNOW", "NOW", "PANW", "CRWD", "FTNT", "ONTO"]

TICKERS: list[str] = _get_tickers()

# Fields that yfinance handles better than Polygon (keep yfinance values when present)
_YFINANCE_PREFERRED = {"ev_ebitda", "peg_ratio", "fcf_yield"}

# Fields Polygon provides that are more reliable than yfinance
_POLYGON_PREFERRED = {
    "gross_margin", "operating_margin", "net_income_margin", "fcf_margin",
    "revenue_growth_yoy", "debt_to_equity", "market_cap",
    "ps_ratio", "ev_sales", "pe_ratio",
}


def _load_env():
    """Load .env into os.environ if not already set."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_path = os.path.abspath(env_path)
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k not in os.environ:
                    os.environ[k] = v


async def _run_refresh():
    """4-hour job: multi-source fetch → merge → score → snapshots."""
    _load_env()
    try:
        from yfinance_fetcher import fetch_portfolio_live_parallel, merge_live_into_mock
        from polygon_fetcher import fetch_polygon_portfolio
        from marketdata_fetcher import fetch_quotes_portfolio
        from mock_data import MOCK_STOCKS
        from scoring_engine import score_portfolio
        from edgar_fetcher import get_flat_values as edgar_values, get_confidence_map as edgar_conf
        from db_client import get_all_overrides, write_live_cache, write_score_snapshots

        now = datetime.now()

        logger.info("Scheduler: fetching market data for %d tickers", len(TICKERS))

        yf_all   = fetch_portfolio_live_parallel(TICKERS)
        poly_all = fetch_polygon_portfolio(TICKERS)
        md_all   = fetch_quotes_portfolio(TICKERS)

        write_live_cache(yf_all, now)

        overrides = get_all_overrides()
        active: dict = {}

        for ticker, mock in MOCK_STOCKS.items():
            data = dict(mock)

            # Layer 1: yfinance — price, ev_ebitda, beta fallback
            yf = yf_all.get(ticker, {})
            if yf:
                data = merge_live_into_mock(data, yf)

            # Layer 2: Polygon — financial statement ratios + EPS proxy + post-filing return
            poly = poly_all.get(ticker, {})
            if len(poly) >= 5:
                data["_has_polygon"] = True
            for field, val in poly.items():
                if field in _POLYGON_PREFERRED and val is not None:
                    data[field] = val
                elif field not in _YFINANCE_PREFERRED and val is not None:
                    data.setdefault(field, val)

            # Layer 3: MarketData.app — real-time price (most fresh)
            md = md_all.get(ticker, {})
            if md.get("current_price"):
                data["current_price"] = md["current_price"]

            # Layer 4: EDGAR AI-exposure fields + confidence metadata
            edgar = edgar_values(ticker)
            if edgar:
                data.update(edgar)
                conf = edgar_conf(ticker)
                if conf:
                    data["_edgar_confidence"] = conf

            # Layer 5: user overrides — highest priority
            ovr = overrides.get(ticker, {})
            if ovr:
                data.update(ovr)
                data["_override_fields"] = list(ovr.keys())

            active[ticker] = data

        scores_df = score_portfolio(active)
        write_score_snapshots(scores_df)

        logger.info("Scheduler: complete, %d tickers scored", len(scores_df))
    except Exception:
        logger.exception("Scheduler: refresh failed")


async def _run_edgar_refresh():
    """Weekly job: fetch SEC EDGAR filings → extract AI-exposure fields → update cache."""
    _load_env()
    try:
        from edgar_fetcher import fetch_ai_exposure_all, save_cache

        logger.info("EDGAR scheduler: starting extraction for %d tickers", len(TICKERS))
        results = fetch_ai_exposure_all(TICKERS)
        save_cache(results)

        total_fields = sum(
            sum(1 for k in fields if not k.startswith("_"))
            for fields in results.values()
        )
        logger.info("EDGAR scheduler: complete — %d fields extracted across %d tickers",
                    total_fields, len(results))
    except Exception:
        logger.exception("EDGAR scheduler: failed")


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        _run_refresh,
        trigger="interval",
        hours=4,
        id="market_refresh",
        next_run_time=datetime.now(),
    )

    scheduler.add_job(
        _run_edgar_refresh,
        trigger="interval",
        days=7,
        id="edgar_refresh",
        next_run_time=datetime.now() + timedelta(seconds=60),
    )

    return scheduler
