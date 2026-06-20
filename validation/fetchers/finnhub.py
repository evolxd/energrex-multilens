"""
Finnhub fetcher
===============
Endpoints used:
  /stock/profile2?symbol={ticker}   → company name, sector, country, exchange
  /stock/metric?symbol={ticker}&metric=all → 50+ financial metrics

Free tier: 60 req/min. Keys missing → returns empty dict (graceful skip).
"""

from __future__ import annotations
import os
import json
import logging
import requests
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"
_CACHE_DIR = Path(__file__).parent.parent / "cache" / "raw"


def _cache_path(ticker: str, endpoint: str) -> Path:
    return _CACHE_DIR / f"finnhub_{ticker}_{endpoint}.json"


def _is_fresh(path: Path, ttl_hours: int = 24) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=ttl_hours)


def _get(url: str, ticker: str, endpoint: str, ttl_hours: int = 24) -> dict | None:
    cache = _cache_path(ticker, endpoint)
    if _is_fresh(cache, ttl_hours):
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception as e:
        logger.warning("Finnhub %s %s: %s", endpoint, ticker, e)
        return None


class FinnhubFetcher:
    def __init__(self, api_key: str | None = None, ttl_hours: int = 24):
        self.key = api_key or os.getenv("FINNHUB_API_KEY", "")
        self.ttl = ttl_hours
        self.available = bool(self.key) and self.key != "your_finnhub_key_here"

    def _url(self, path: str, params: str = "") -> str:
        sep = "&" if "?" in path else "?"
        return f"{_BASE}/{path}{sep}token={self.key}{params}"

    def get_profile(self, ticker: str) -> dict:
        if not self.available:
            return {}
        raw = _get(self._url(f"stock/profile2?symbol={ticker}"), ticker, "profile", self.ttl)
        if not raw or not isinstance(raw, dict) or not raw.get("name"):
            return {}
        return {
            "company_name": raw.get("name", ""),
            "sector":       raw.get("finnhubIndustry", ""),
            "exchange":     raw.get("exchange", ""),
            "country":      raw.get("country", ""),
            "market_cap":   raw.get("marketCapitalization"),  # in millions
            "website":      raw.get("weburl", ""),
            "ipo_date":     raw.get("ipo", ""),
        }

    def get_metrics(self, ticker: str) -> dict:
        if not self.available:
            return {}
        raw = _get(
            self._url(f"stock/metric?symbol={ticker}&metric=all"),
            ticker, "metrics", self.ttl
        )
        if not raw or not isinstance(raw, dict):
            return {}
        m = raw.get("metric", {})
        return {
            "pe_ratio":         m.get("peTTM"),
            "ps_ratio":         m.get("psTTM"),
            "pb_ratio":         m.get("pbQuarterly"),
            "gross_margin":     m.get("grossMarginTTM"),      # already in %
            "operating_margin": m.get("operatingMarginTTM"),  # already in %
            "net_margin":       m.get("netMarginTTM"),        # already in %
            "revenue_growth_yoy": m.get("revenueGrowthTTMYoy"),  # already in %
            "eps_growth_yoy":   m.get("epsGrowthTTMYoy"),
            "roic":             m.get("roicTTM"),
            "de_ratio":         m.get("totalDebt/totalEquityAnnual"),
            "current_ratio":    m.get("currentRatioAnnual"),
            "52w_high":         m.get("52WeekHigh"),
            "52w_low":          m.get("52WeekLow"),
            "beta":             m.get("beta"),
        }
