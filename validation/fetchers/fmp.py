"""
FMP (Financial Modeling Prep) fetcher
======================================
Endpoints used:
  /profile/{ticker}          → company name, sector, description, market cap
  /ratios-ttm/{ticker}       → P/E, P/S, gross margin, FCF margin, ROE, etc.
  /income-statement/{ticker} → revenue, gross profit, operating income, net income
  /key-metrics-ttm/{ticker}  → EV/EBITDA, EV/Revenue, FCF yield

Free tier: 250 req/day. Keys missing → returns empty dict (graceful skip).
"""

from __future__ import annotations
import os
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/api/v3"
_CACHE_DIR = Path(__file__).parent.parent / "cache" / "raw"


def _cache_path(ticker: str, endpoint: str) -> Path:
    return _CACHE_DIR / f"fmp_{ticker}_{endpoint}.json"


def _is_fresh(path: Path, ttl_hours: int = 24) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=ttl_hours)


def _get(url: str, ticker: str, endpoint: str, ttl_hours: int = 24) -> list | dict | None:
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
        logger.warning("FMP %s %s: %s", endpoint, ticker, e)
        return None


class FMPFetcher:
    def __init__(self, api_key: str | None = None, ttl_hours: int = 24):
        self.key = api_key or os.getenv("FMP_API_KEY", "")
        self.ttl = ttl_hours
        self.available = bool(self.key) and self.key != "your_fmp_key_here"

    def _url(self, path: str) -> str:
        return f"{_BASE}/{path}?apikey={self.key}"

    def get_profile(self, ticker: str) -> dict:
        if not self.available:
            return {}
        raw = _get(self._url(f"profile/{ticker}"), ticker, "profile", self.ttl)
        if not raw or not isinstance(raw, list) or not raw:
            return {}
        p = raw[0]
        return {
            "company_name":  p.get("companyName", ""),
            "sector":        p.get("sector", ""),
            "industry":      p.get("industry", ""),
            "description":   p.get("description", ""),
            "exchange":      p.get("exchangeShortName", ""),
            "market_cap":    p.get("mktCap"),
            "current_price": p.get("price"),
            "website":       p.get("website", ""),
            "isin":          p.get("isin", ""),
            "currency":      p.get("currency", "USD"),
        }

    def get_ratios(self, ticker: str) -> dict:
        if not self.available:
            return {}
        raw = _get(self._url(f"ratios-ttm/{ticker}"), ticker, "ratios", self.ttl)
        if not raw or not isinstance(raw, list) or not raw:
            return {}
        r = raw[0]
        return {
            "gross_margin":     r.get("grossProfitMarginTTM"),
            "operating_margin": r.get("operatingProfitMarginTTM"),
            "net_margin":       r.get("netProfitMarginTTM"),
            "fcf_margin":       r.get("freeCashFlowPerShareTTM"),  # per share; use as signal only
            "roe":              r.get("returnOnEquityTTM"),
            "roic":             r.get("returnOnCapitalEmployedTTM"),
            "pe_ratio":         r.get("peRatioTTM"),
            "forward_pe":       r.get("priceEarningsRatioTTM"),
            "ps_ratio":         r.get("priceToSalesRatioTTM"),
            "peg_ratio":        r.get("priceEarningsToGrowthRatioTTM"),
            "de_ratio":         r.get("debtEquityRatioTTM"),
            "current_ratio":    r.get("currentRatioTTM"),
        }

    def get_income(self, ticker: str) -> dict:
        if not self.available:
            return {}
        raw = _get(self._url(f"income-statement/{ticker}?limit=2"), ticker, "income", self.ttl)
        if not raw or not isinstance(raw, list) or len(raw) < 1:
            return {}
        cur = raw[0]
        rev_cur  = cur.get("revenue", 0) or 0
        rev_prev = raw[1].get("revenue", 0) if len(raw) > 1 else 0
        rev_growth = ((rev_cur - rev_prev) / rev_prev) if rev_prev and rev_prev != 0 else None
        return {
            "revenue":           rev_cur,
            "gross_profit":      cur.get("grossProfit"),
            "operating_income":  cur.get("operatingIncome"),
            "net_income":        cur.get("netIncome"),
            "rd_expense":        cur.get("researchAndDevelopmentExpenses"),
            "gross_margin_calc": (cur.get("grossProfit", 0) / rev_cur) if rev_cur else None,
            "revenue_growth_yoy": rev_growth,
            "period":            cur.get("date", ""),
        }

    def get_key_metrics(self, ticker: str) -> dict:
        if not self.available:
            return {}
        raw = _get(self._url(f"key-metrics-ttm/{ticker}"), ticker, "keymetrics", self.ttl)
        if not raw or not isinstance(raw, list) or not raw:
            return {}
        m = raw[0]
        return {
            "ev_ebitda":   m.get("enterpriseValueOverEBITDATTM"),
            "ev_sales":    m.get("evToSalesTTM"),
            "fcf_yield":   m.get("freeCashFlowYieldTTM"),
            "market_cap":  m.get("marketCapTTM"),
        }
