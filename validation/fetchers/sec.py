"""
SEC EDGAR fetcher (free, no API key required)
=============================================
Endpoints used:
  https://www.sec.gov/files/company_tickers.json      → ticker → CIK mapping
  https://data.sec.gov/submissions/CIK{cik:010d}.json → company name, SIC, filings list
  https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json → XBRL financial facts

Rate limit: 10 req/s. We sleep between calls to avoid 429s.
All data cached to cache/raw/sec_*.json.
"""

from __future__ import annotations
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent / "cache" / "raw"
_HEADERS = {"User-Agent": "ai-valuation-validator research@example.com"}
_SLEEP = 0.12   # 100ms+ between calls → well under 10 req/s


def _cache_path(name: str) -> Path:
    return _CACHE_DIR / f"sec_{name}.json"


def _is_fresh(path: Path, ttl_hours: int = 24) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=ttl_hours)


def _fetch(url: str, cache_name: str, ttl_hours: int = 24) -> dict | None:
    cache = _cache_path(cache_name)
    if _is_fresh(cache, ttl_hours):
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        time.sleep(_SLEEP)
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception as e:
        logger.warning("SEC EDGAR %s: %s", url, e)
        return None


def _get_cik_map() -> dict[str, int]:
    """Return {TICKER: cik_int} from SEC's canonical company_tickers.json."""
    data = _fetch(
        "https://www.sec.gov/files/company_tickers.json",
        "company_tickers", ttl_hours=168   # refresh weekly
    )
    if not data:
        return {}
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


_CIK_MAP: dict[str, int] = {}   # module-level lazy cache

# Tickers absent from SEC's company_tickers.json but with known CIKs.
# Chinese ADRs (TCEHY, BIDU, BABA etc.) are foreign private issuers and
# intentionally excluded — no entry needed for them.
_MANUAL_CIK_OVERRIDES: dict[str, int] = {
    "PSTG": 1474432,   # Pure Storage Inc.
}


def _cik(ticker: str) -> int | None:
    global _CIK_MAP
    t = ticker.upper()
    if t in _MANUAL_CIK_OVERRIDES:
        return _MANUAL_CIK_OVERRIDES[t]
    if not _CIK_MAP:
        _CIK_MAP = _get_cik_map()
    return _CIK_MAP.get(t)


def _latest_annual_value(facts_node: dict) -> float | None:
    """
    Given a us-gaap concept node like:
      {"units": {"USD": [{end, val, form, ...}, ...]}}
    Return the most recent 10-K value.
    """
    if not facts_node:
        return None
    for unit_data in facts_node.get("units", {}).values():
        annual = [e for e in unit_data if e.get("form") == "10-K"]
        if not annual:
            annual = [e for e in unit_data if e.get("form") in ("10-K", "20-F")]
        if annual:
            annual.sort(key=lambda x: x.get("end", ""), reverse=True)
            return annual[0].get("val")
    return None


class SECFetcher:
    def __init__(self, ttl_hours: int = 24):
        self.ttl = ttl_hours

    def get_cik(self, ticker: str) -> int | None:
        return _cik(ticker)

    def get_company_info(self, ticker: str) -> dict:
        cik = _cik(ticker)
        if cik is None:
            return {}
        data = _fetch(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
            f"submissions_{ticker}", self.ttl
        )
        if not data:
            return {}
        sic_desc = data.get("sicDescription", "")
        return {
            "company_name": data.get("name", ""),
            "sic":          data.get("sic", ""),
            "sic_desc":     sic_desc,
            "state":        data.get("stateOfIncorporation", ""),
            "fiscal_year":  data.get("fiscalYearEnd", ""),
            "cik":          cik,
            "edgar_url":    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=10-K",
        }

    def get_financials(self, ticker: str) -> dict:
        """Extract key financial metrics from XBRL company facts."""
        cik = _cik(ticker)
        if cik is None:
            return {}
        data = _fetch(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
            f"facts_{ticker}", self.ttl
        )
        if not data:
            return {}

        gaap = data.get("facts", {}).get("us-gaap", {})

        def _get(*keys: str) -> float | None:
            for k in keys:
                v = _latest_annual_value(gaap.get(k))
                if v is not None:
                    return v
            return None

        revenue     = _get("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                           "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")
        gross       = _get("GrossProfit")
        operating   = _get("OperatingIncomeLoss")
        net         = _get("NetIncomeLoss")
        rd          = _get("ResearchAndDevelopmentExpense")
        assets      = _get("Assets")
        liabilities = _get("Liabilities")

        result: dict = {}
        if revenue and revenue > 0:
            result["revenue_sec"] = revenue
            if gross is not None:
                result["gross_margin_sec"] = round(gross / revenue, 4)
            if operating is not None:
                result["operating_margin_sec"] = round(operating / revenue, 4)
            if net is not None:
                result["net_margin_sec"] = round(net / revenue, 4)
            if rd is not None:
                result["rd_intensity_sec"] = round(rd / revenue, 4)
        if assets and liabilities:
            equity = assets - liabilities
            if equity and equity != 0:
                result["de_ratio_sec"] = round(liabilities / equity, 3)

        return result

    def get_business_description(self, ticker: str) -> str:
        """
        Try to extract the business description from the company's most recent 10-K.
        Uses the filings index to find the 10-K, then fetches the filing summary.
        This is best-effort; returns empty string on failure.
        """
        cik = _cik(ticker)
        if cik is None:
            return ""
        data = _fetch(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
            f"submissions_{ticker}", self.ttl
        )
        if not data:
            return ""

        filings = data.get("filings", {}).get("recent", {})
        forms   = filings.get("form", [])
        dates   = filings.get("filingDate", [])
        accNums = filings.get("accessionNumber", [])

        # Find most recent 10-K
        for form, date, acc in zip(forms, dates, accNums):
            if form == "10-K":
                acc_clean = acc.replace("-", "")
                # Return clickable filing index URL directly (no fetch needed)
                return (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                        f"{acc_clean}/{acc}-index.htm")
        return ""
