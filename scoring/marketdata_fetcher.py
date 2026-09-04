"""
MarketData.app fetcher — real-time quotes (bid/ask/last/change).
Used for current_price when Polygon delayed bars are stale.
Free plan: candles + quotes available; earnings = 402.
"""

import os
import time
import logging
import datetime as dt
from typing import Optional
import requests

logger = logging.getLogger(__name__)

_BASE  = "https://api.marketdata.app/v1"
_SLEEP = 0.2


def _key() -> str:
    return os.environ.get("MARKETDATA_API_KEY", "")


def _get(path: str, timeout: int = 10) -> Optional[dict]:
    key = _key()
    if not key:
        return None
    try:
        url = f"{_BASE}{path}"
        params = {"token": key}
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code == 402:
            logger.warning("MarketData.app 402 (plan limit): %s", path)
            return None
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        logger.warning("MarketData.app request failed %s: %s", path, e)
        return None


def fetch_quote(ticker: str) -> dict:
    """Real-time quote: last price, change%, volume."""
    data = _get(f"/stocks/quotes/{ticker}/")
    time.sleep(_SLEEP)
    if not data or data.get("s") != "ok":
        return {}

    out = {}
    # All fields are 1-element lists in the response
    last   = data.get("last",      [None])[0]
    change = data.get("changepct", [None])[0]
    vol    = data.get("volume",    [None])[0]

    if last   is not None: out["current_price"]      = last
    if change is not None: out["price_change_pct_1d"] = round(change, 4)
    if vol    is not None: out["volume_1d"]           = int(vol)

    return out


def fetch_price_evidence(ticker: str) -> dict:
    """Return MarketData.app price evidence only when source time is supplied."""
    data = _get(f"/stocks/quotes/{ticker}/")
    time.sleep(_SLEEP)
    retrieved = dt.datetime.now(dt.timezone.utc)
    if not data or data.get("s") != "ok":
        return {"_evidence_error": "MarketData.app quote unavailable"}
    last = data.get("last", [None])[0]
    raw_timestamp = (
        data.get("updated", [None])[0]
        if isinstance(data.get("updated"), list)
        else data.get("updated")
    )
    if last is None:
        return {"_evidence_error": "MarketData.app quote lacks last price"}
    if raw_timestamp is None:
        return {
            "_evidence_error": (
                "MarketData.app quote lacks source timestamp; retrieval time "
                "cannot substitute for observed_at"
            )
        }
    try:
        numeric_timestamp = float(raw_timestamp)
        if numeric_timestamp > 10_000_000_000:
            numeric_timestamp /= 1000
        observed = dt.datetime.fromtimestamp(
            numeric_timestamp,
            tz=dt.timezone.utc,
        )
        if observed > retrieved:
            return {"_evidence_error": "MarketData.app timestamp is in the future"}
        return {
            "field": "current_price",
            "value": float(last),
            "unit": "USD/share",
            "source": "MarketData.app real-time quote",
            "source_type": "MARKET",
            "source_family": "MARKETDATA_APP",
            "origin_family": "MARKETDATA_APP_MARKET_DATA",
            "lineage_id": f"MARKETDATA:{ticker.upper()}:{raw_timestamp}",
            "source_locator": (
                f"https://api.marketdata.app/v1/stocks/quotes/{ticker.upper()}/"
            ),
            "observed_at": observed.isoformat(),
            "available_at": observed.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "extraction_method": "MarketData.app last quote",
        }
    except (TypeError, ValueError, OSError) as exc:
        return {"_evidence_error": f"MarketData.app timestamp invalid: {exc}"}


def fetch_quotes_portfolio(tickers: list[str]) -> dict[str, dict]:
    """Fetch real-time quotes for all tickers."""
    results = {}
    for ticker in tickers:
        try:
            results[ticker] = fetch_quote(ticker)
        except Exception as e:
            logger.error("MarketData.app: failed for %s: %s", ticker, e)
            results[ticker] = {}
    return results
