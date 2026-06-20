"""
SEC EDGAR AI-exposure extractor.
Two approaches:
  1. XBRL company facts API  — structured segment revenue (NVDA, MRVL)
  2. Full-text regex          — MD&A / narrative disclosures (all tickers)
Output: edgar_cache.json  {ticker: {field: {value, confidence, method, fetched_at}}}
"""

import html as _html_module
import json
import re
import time
import logging
import pathlib
import requests
from collections import defaultdict
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_PATH = pathlib.Path(__file__).parent / "edgar_cache.json"
_BASE       = "https://data.sec.gov"
_HEADERS    = {"User-Agent": "ai-valuation-tool evolxd@gmail.com"}
_SLEEP      = 0.4   # seconds between EDGAR calls (rate limit: be polite)

# EDGAR CIK identifiers — stable permanent IDs
TICKER_CIK: dict[str, str] = {
    "NVDA": "0001045810",
    "AVGO": "0001730168",
    "MRVL": "0001835632",   # Marvell Technology, Inc. (Delaware, post-2021 redomicile)
    "PLTR": "0001321655",
    "SNOW": "0001640147",
    "NOW":  "0001373715",
    "PANW": "0001327567",
    "CRWD": "0001535527",
    "FTNT": "0001262039",
    "ONTO": "0000704532",   # Onto Innovation Inc. (formerly Rudolph Technologies)
}


# ─────────────────────────────────────────────────────────────────────
# EDGAR API helpers
# ─────────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 45) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        logger.warning("EDGAR GET failed %s: %s", url, e)
        return None


def fetch_xbrl_facts(cik: str) -> Optional[dict]:
    """Download full XBRL company facts JSON."""
    r = _get(f"{_BASE}/api/xbrl/companyfacts/CIK{cik}.json", timeout=90)
    return r.json() if r else None


def fetch_latest_filing_text(cik: str, form_type: str = "10-Q") -> Optional[str]:
    """
    Download primary document of the most recent 10-Q (or 10-K).
    Returns up to 600 KB of HTML/text, or None.
    """
    r = _get(f"{_BASE}/submissions/CIK{cik}.json")
    if not r:
        return None

    recent = r.json().get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    accns   = recent.get("accessionNumber", [])
    primary = recent.get("primaryDocument", [])

    cik_int = int(cik)
    for i, form in enumerate(forms):
        if form != form_type:
            continue
        accn_flat = accns[i].replace("-", "")
        doc_url   = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik_int}/{accn_flat}/{primary[i]}"
        )
        time.sleep(_SLEEP)
        dr = _get(doc_url, timeout=90)
        if dr and len(dr.text) > 5_000:
            return dr.text[:3_000_000]   # 3 MB — enough to include MD&A in large iXBRL files

    return None


# ─────────────────────────────────────────────────────────────────────
# XBRL segment extraction
# ─────────────────────────────────────────────────────────────────────

def _quarterly_segment_revenues(facts: dict) -> dict[str, dict[str, float]]:
    """
    Parse XBRL revenue facts into {end_date: {segment_member: amount_usd}}.
    Tries both common revenue XBRL tags; filters to ~quarterly durations.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    # Try both common revenue tags (companies use one or the other)
    entries = (
        us_gaap.get("RevenueFromContractWithCustomerExcludingAssessedTax", {})
               .get("units", {}).get("USD", [])
        or
        us_gaap.get("Revenues", {}).get("units", {}).get("USD", [])
    )
    by_end: dict[str, dict[str, float]] = defaultdict(dict)

    for e in entries:
        if e.get("form") not in ("10-Q", "10-K"):
            continue
        end   = e.get("end", "")
        start = e.get("start", "")
        # Keep only quarterly-duration entries
        if start and end:
            try:
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                if not (60 <= days <= 125):
                    continue
            except ValueError:
                continue

        seg = e.get("segment")
        if seg is None:
            name = "Total"
        elif isinstance(seg, dict):
            name = seg.get("member", "Unknown")
        else:
            continue

        # Keep the latest value per (end, segment)
        if name not in by_end[end]:
            by_end[end][name] = e.get("val", 0)

    return dict(by_end)


def _segment_pct(seg_revs: dict[str, dict[str, float]],
                 keyword: str) -> Optional[float]:
    """
    From quarterly segment revenue dict, find latest period entry
    whose segment name contains `keyword` and return seg/total ratio.
    """
    if not seg_revs:
        return None
    latest = max(seg_revs)
    revs   = seg_revs[latest]
    total  = revs.get("Total")
    if not total or total == 0:
        return None
    kw = keyword.lower().replace(" ", "")
    for name, val in revs.items():
        if kw in name.lower().replace(" ", "").replace("_", ""):
            return round(val / total, 4)
    return None


# ─────────────────────────────────────────────────────────────────────
# HTML stripper
# ─────────────────────────────────────────────────────────────────────

def _plain(html: str) -> str:
    """Strip iXBRL header, scripts, styles, all tags; decode HTML entities; normalise spaces."""
    # Skip the iXBRL hidden header block (can be 100-200 KB of XBRL metadata)
    ix_end = html.lower().find("</ix:header>")
    if ix_end >= 0:
        html = html[ix_end + len("</ix:header>"):]
    # Remove script and style blocks entirely (preserves no content)
    html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ",
                  html, flags=re.DOTALL | re.IGNORECASE)
    # Strip all remaining HTML / iXBRL / XML tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode HTML entities: &#160; → ' ', &amp; → '&', &lt; → '<' etc.
    html = _html_module.unescape(html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def _dollar_m(s: str) -> Optional[float]:
    """Parse '$XX,XXX' → float in millions. Returns None if unparseable."""
    try:
        return float(s.replace(",", "").replace("$", "").strip())
    except (ValueError, AttributeError):
        return None


# ─────────────────────────────────────────────────────────────────────
# Per-ticker extraction
# Each function returns {field_name: {value, confidence, method}}
# ─────────────────────────────────────────────────────────────────────

def _extract_nvda(facts: Optional[dict], text: Optional[str]) -> dict:
    """
    NVDA: Data Center / Total from 'Revenue by Market Platform' table in 10-Q.
    NVDA files as a single operating segment; Data Center is a market platform category.
    Table format (after HTML stripping + entity decoding):
      'Data Center $ 75,246   $ 39,112'
      'Total revenue $ 81,615   $ 44,062'
    """
    result = {}
    if not text:
        return result

    plain = _plain(text)

    # Table format: 'Data Center $ 75,246' (amounts in millions per table header)
    dc_m = re.search(r"[Dd]ata [Cc]enter\s+\$\s*([\d,]+)", plain)
    # Total revenue row
    total_m = re.search(r"[Tt]otal\s+revenue\s+\$\s*([\d,]+)", plain)

    if dc_m and total_m:
        dc    = _dollar_m(dc_m.group(1))
        total = _dollar_m(total_m.group(1))
        if dc and total and total > 0:
            pct = round(dc / total, 4)
            result["datacenter_exposure_pct"] = {
                "value": pct, "confidence": "H", "method": "text_table_ratio"
            }
            result["ai_revenue_exposure_pct"] = {
                "value": round(pct * 0.97, 4),   # ~3% of DC is non-AI (Edge Computing portion)
                "confidence": "M", "method": "text_dc_proxy"
            }
            # DC gross margin ≈ company gross margin for NVDA (both ~75-78%); profit proxy is reliable
            result["ai_profit_exposure_pct"] = {
                "value": round(pct * 0.97, 4),   # same ratio as revenue — margins converge at scale
                "confidence": "M", "method": "text_dc_profit_proxy"
            }

    return result


def _extract_avgo(facts: Optional[dict], text: Optional[str]) -> dict:
    """AVGO: AI revenue as % of semiconductor solutions segment."""
    result = {}
    if not text:
        return result

    plain = _plain(text)

    # AVGO management explicitly states AI revenue: "AI revenue was $X.X billion"
    # Total semiconductor revenue also stated
    ai_rev_m = re.search(
        r"AI\s+revenue[^$]*?\$([\d.]+)\s*billion",
        plain, re.IGNORECASE
    )
    # Total revenue or semiconductor revenue
    total_m = re.search(
        r"(?:net revenue|total revenue)[^$]*?\$([\d.]+)\s*billion",
        plain, re.IGNORECASE
    )
    if ai_rev_m and total_m:
        ai_rev = float(ai_rev_m.group(1))
        total  = float(total_m.group(1))
        if total > 0:
            pct = round(ai_rev / total, 4)
            result["ai_revenue_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_ai_rev_over_total"
            }

    # Also try percentage mentions: "represented approximately XX%"
    if "ai_revenue_exposure_pct" not in result:
        pct_m = re.search(
            r"AI revenue.*?represent(?:ed|ing)\s+(?:approximately\s+)?(\d{2,3})\s*%",
            plain, re.IGNORECASE | re.DOTALL
        )
        if pct_m:
            pct = int(pct_m.group(1)) / 100
            result["ai_revenue_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_pct_mention"
            }

    return result


def _extract_mrvl(facts: Optional[dict], text: Optional[str]) -> dict:
    """
    MRVL: Data Center end market revenue / total net revenue.
    MRVL reports by end market: Data Center, Carrier Infrastructure, Enterprise Networking, etc.
    Table format: 'Data Center $ XXX' and 'Net revenue $ XXX' (in millions).
    """
    result = {}

    # XBRL attempt first
    if facts:
        seg_revs = _quarterly_segment_revenues(facts)
        dc_pct   = _segment_pct(seg_revs, "datacenter")
        if dc_pct:
            result["datacenter_exposure_pct"] = {
                "value": dc_pct, "confidence": "H", "method": "xbrl_segment"
            }
            result["ai_revenue_exposure_pct"] = {
                "value": round(dc_pct * 0.90, 4), "confidence": "M", "method": "xbrl_dc_proxy"
            }
            return result

    if not text:
        return result

    plain = _plain(text)

    # Table pattern: 'Data Center $ XXX' / 'Net revenue $ XXX'
    dc_m    = re.search(r"[Dd]ata [Cc]enter\s+\$\s*([\d,]+)", plain)
    total_m = re.search(r"[Nn]et\s+revenue\s+\$\s*([\d,]+)", plain)

    if not total_m:
        total_m = re.search(r"[Tt]otal\s+(?:net\s+)?revenue\s+\$\s*([\d,]+)", plain)

    if dc_m and total_m:
        dc    = _dollar_m(dc_m.group(1))
        total = _dollar_m(total_m.group(1))
        if dc and total and total > 0 and dc < total:
            pct = round(dc / total, 4)
            result["datacenter_exposure_pct"] = {
                "value": pct, "confidence": "H", "method": "text_table_ratio"
            }
            result["ai_revenue_exposure_pct"] = {
                "value": round(pct * 0.90, 4), "confidence": "M", "method": "text_dc_proxy"
            }
            # MRVL DC segment margin ≈ company blended margin; profit proxy is reliable
            result["ai_profit_exposure_pct"] = {
                "value": round(pct * 0.90, 4), "confidence": "M", "method": "text_dc_profit_proxy"
            }

    return result


def _extract_pltr(text: Optional[str]) -> dict:
    """
    PLTR: Commercial revenue / Total revenue as AI-platform proxy.
    PLTR breaks revenue into gov/commercial segments; commercial is the AI-driven growth segment.
    Filing uses 'Commercial revenue XXXXXX' (no $ for segment contribution table, amounts in thousands).
    """
    if not text:
        return {}
    result = {}
    plain  = _plain(text)

    # Segment contribution table: 'Commercial revenue 774,173' (no $, in thousands)
    comm_m  = re.search(r"[Cc]ommercial\s+revenue\s+([\d,]+)", plain)
    # Total revenue row: 'Total revenue $ 1,632,583'
    total_m = re.search(r"[Tt]otal\s+revenue\s+\$\s*([\d,]+)", plain)

    if comm_m and total_m:
        comm  = _dollar_m(comm_m.group(1))
        total = _dollar_m(total_m.group(1))
        if comm and total and total > 0 and comm < total:
            pct = round(comm / total, 4)
            result["software_ai_platform_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_commercial_pct"
            }

    # AIP customer count — multiple patterns
    for pat in [
        r"(\d{2,4})\s+(?:organizations?|customers?|enterprises?)[^.]{0,80}AIP",
        r"AIP[^.]{0,80}(\d{2,4})\s+(?:organizations?|customers?|enterprises?)",
    ]:
        aip_m = re.search(pat, plain, re.IGNORECASE)
        if aip_m:
            result["_aip_customer_count"] = {
                "value": int(aip_m.group(1).replace(",", "")),
                "confidence": "M", "method": "text_count"
            }
            break

    return result


def _extract_snow(text: Optional[str]) -> dict:
    """SNOW: Cortex AI workloads / accounts from 10-Q narrative or tables."""
    if not text:
        return {}
    result = {}
    plain  = _plain(text)

    # Bounded match: percentage and "Cortex" must be within 200 chars of each other
    # to avoid false positives from table-of-contents page numbers
    pct_m = re.search(
        r"(\d{1,2})\s*%\s{0,50}.{0,150}(?:Cortex|AI\s+workload)",
        plain, re.IGNORECASE
    )
    if not pct_m:
        pct_m = re.search(
            r"(?:Cortex|AI\s+workload).{0,150}(\d{1,2})\s*%",
            plain, re.IGNORECASE
        )
    if pct_m:
        pct = int(pct_m.group(1)) / 100
        if 0.02 <= pct <= 0.75:      # cap at 75% — SNOW AI exposure is unlikely >75%
            result["software_ai_platform_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_cortex_pct"
            }

    # Cortex account count (informational)
    cortex_m = re.search(
        r"([\d,]{4,7})\s+(?:accounts?|customers?)[^.]{0,80}Cortex",
        plain, re.IGNORECASE
    )
    if not cortex_m:
        cortex_m = re.search(
            r"Cortex[^.]{0,80}([\d,]{4,7})\s+(?:accounts?|customers?)",
            plain, re.IGNORECASE
        )
    if cortex_m:
        result["_cortex_accounts"] = {
            "value": int(cortex_m.group(1).replace(",", "")),
            "confidence": "M", "method": "text_count"
        }

    return result


def _extract_now(text: Optional[str]) -> dict:
    """NOW: Now Assist / AI agents ARR contribution."""
    if not text:
        return {}
    result = {}
    plain  = _plain(text)

    # "Now Assist contributed $X to ARR" or "Now Assist customers XX%"
    assist_pct_m = re.search(
        r"Now\s+Assist[^.]*?(\d{1,2})\s*%",
        plain, re.IGNORECASE
    )
    if assist_pct_m:
        pct = int(assist_pct_m.group(1)) / 100
        if 0.01 <= pct <= 0.90:
            result["software_ai_platform_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_now_assist_pct"
            }

    # ACV mentions: "$X million Now Assist ACV"
    assist_rev_m = re.search(
        r"Now\s+Assist\s+ACV[^$]*?\$([\d.]+)\s*(?:million|billion)",
        plain, re.IGNORECASE
    )
    if assist_rev_m:
        result["_now_assist_acv_usd"] = {
            "value": float(assist_rev_m.group(1)),
            "confidence": "M", "method": "text_dollar"
        }

    return result


def _extract_panw(text: Optional[str]) -> dict:
    """PANW: XSIAM ARR / Next-Gen Security ARR ratio."""
    if not text:
        return {}
    result = {}
    plain  = _plain(text)

    # "Next-Gen Security ARR of $X.X billion"
    ngx_m = re.search(
        r"[Nn]ext-[Gg]en\s+[Ss]ecurity\s+ARR[^$]*?\$([\d.]+)\s*billion",
        plain
    )
    # "XSIAM ARR of $X.X billion"
    xsiam_m = re.search(
        r"XSIAM\s+ARR[^$]*?\$([\d.]+)\s*billion",
        plain
    )
    if ngx_m and xsiam_m:
        ngx_arr   = float(ngx_m.group(1))
        xsiam_arr = float(xsiam_m.group(1))
        if ngx_arr > 0:
            pct = round(xsiam_arr / ngx_arr, 4)
            result["cybersecurity_ai_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_xsiam_over_ngx"
            }

    return result


def _extract_crwd(text: Optional[str]) -> dict:
    """CRWD: Charlotte AI adoption / Falcon Flex AI module share."""
    if not text:
        return {}
    result = {}
    plain  = _plain(text)

    # "Charlotte AI... XX% of customers" or "XX% adoption rate"
    pct_m = re.search(
        r"Charlotte\s+AI[^.]*?(\d{1,3})\s*%",
        plain, re.IGNORECASE
    )
    if pct_m:
        pct = int(pct_m.group(1)) / 100
        if 0.01 <= pct <= 0.99:
            result["cybersecurity_ai_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_charlotte_pct"
            }

    # Charlotte AI queries/interactions (informational)
    queries_m = re.search(
        r"Charlotte\s+AI[^.]*?([\d.]+)\s+(?:billion|million)\s+(?:queries|interactions)",
        plain, re.IGNORECASE
    )
    if queries_m:
        result["_charlotte_ai_queries"] = {
            "value": float(queries_m.group(1)),
            "confidence": "M", "method": "text_count"
        }

    return result


def _extract_ftnt(text: Optional[str]) -> dict:
    """FTNT: FortiAI / AI-powered security product share."""
    if not text:
        return {}
    result = {}
    plain  = _plain(text)

    # "FortiAI... XX%" or "AI-powered... XX% of..."
    pct_m = re.search(
        r"(?:FortiAI|AI-powered\s+security)[^.]*?(\d{1,2})\s*%",
        plain, re.IGNORECASE
    )
    if pct_m:
        pct = int(pct_m.group(1)) / 100
        if 0.01 <= pct <= 0.90:
            result["cybersecurity_ai_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_fortiai_pct"
            }

    return result


def _extract_onto(text: Optional[str]) -> dict:
    """ONTO: Advanced packaging / AI-related inspection revenue %."""
    if not text:
        return {}
    result = {}
    plain  = _plain(text)

    # "advanced packaging... XX% of revenue" or "XX%... advanced packaging"
    pct_m = re.search(
        r"(?:advanced\s+packaging|HBM|high[- ]bandwidth\s+memory)[^.]*?(\d{1,2})\s*%",
        plain, re.IGNORECASE
    )
    if not pct_m:
        pct_m = re.search(
            r"(\d{1,2})\s*%[^.]*?advanced\s+packaging",
            plain, re.IGNORECASE
        )
    if pct_m:
        pct = int(pct_m.group(1)) / 100
        if 0.05 <= pct <= 0.95:
            result["advanced_packaging_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_adv_pkg_pct"
            }
            result["ai_revenue_exposure_pct"] = {
                "value": pct, "confidence": "M", "method": "text_adv_pkg_proxy"
            }

    return result


# ─────────────────────────────────────────────────────────────────────
# Main orchestration
# ─────────────────────────────────────────────────────────────────────

def fetch_ai_exposure_all(tickers: Optional[list] = None) -> dict:
    """
    Fetch AI-exposure fields for all (or specified) tickers via EDGAR.
    Returns {ticker: {field: {value, confidence, method, fetched_at}}}
    """
    if tickers is None:
        tickers = list(TICKER_CIK.keys())

    results: dict = {}
    now_str = datetime.now().isoformat()

    for ticker in tickers:
        cik = TICKER_CIK.get(ticker)
        if not cik:
            logger.warning("No CIK mapping for %s", ticker)
            continue

        logger.info("EDGAR: extracting %s (CIK %s)", ticker, cik)
        extracted: dict = {}

        try:
            # Chip companies benefit from XBRL; everyone gets text
            needs_xbrl = ticker in ("NVDA", "MRVL")
            facts = fetch_xbrl_facts(cik) if needs_xbrl else None
            time.sleep(_SLEEP)

            # Cybersecurity ARR metrics live in earnings press release (8-K), not 10-Q
            if ticker in ("PANW", "CRWD", "FTNT"):
                text = fetch_latest_filing_text(cik, "8-K")
            else:
                text = fetch_latest_filing_text(cik, "10-Q")
            time.sleep(_SLEEP)

            if ticker == "NVDA":
                extracted = _extract_nvda(facts, text)
            elif ticker == "AVGO":
                extracted = _extract_avgo(facts, text)
            elif ticker == "MRVL":
                extracted = _extract_mrvl(facts, text)
            elif ticker == "PLTR":
                extracted = _extract_pltr(text)
            elif ticker == "SNOW":
                extracted = _extract_snow(text)
            elif ticker == "NOW":
                extracted = _extract_now(text)
            elif ticker == "PANW":
                extracted = _extract_panw(text)
            elif ticker == "CRWD":
                extracted = _extract_crwd(text)
            elif ticker == "FTNT":
                extracted = _extract_ftnt(text)
            elif ticker == "ONTO":
                extracted = _extract_onto(text)

        except Exception:
            logger.exception("EDGAR extraction failed for %s", ticker)

        # Stamp all entries with fetch timestamp
        for entry in extracted.values():
            entry["fetched_at"] = now_str

        results[ticker] = extracted
        n_found = sum(1 for k in extracted if not k.startswith("_"))
        logger.info("  %s: %d fields extracted", ticker, n_found)

    return results


# ─────────────────────────────────────────────────────────────────────
# Cache I/O
# ─────────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    """Load edgar_cache.json. Returns empty dict if missing or corrupt."""
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(results: dict) -> None:
    """Merge new results into edgar_cache.json."""
    existing = load_cache()
    for ticker, fields in results.items():
        if ticker not in existing:
            existing[ticker] = {}
        existing[ticker].update(fields)
    _CACHE_PATH.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    logger.info("EDGAR cache saved (%d tickers)", len(existing))


def get_flat_values(ticker: str) -> dict[str, float]:
    """
    Return {field: value} for a ticker (non-underscore fields only).
    Used for merging into scoring data dict.
    """
    cache = load_cache()
    return {
        field: entry["value"]
        for field, entry in cache.get(ticker, {}).items()
        if not field.startswith("_") and "value" in entry
    }


def get_confidence_map(ticker: str) -> dict[str, str]:
    """Return {field: confidence_grade} for a ticker."""
    cache = load_cache()
    return {
        field: entry.get("confidence", "L")
        for field, entry in cache.get(ticker, {}).items()
        if not field.startswith("_")
    }


# ─────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    tickers = sys.argv[1:] if len(sys.argv) > 1 else None
    results = fetch_ai_exposure_all(tickers)
    save_cache(results)

    print("\n=== EDGAR Extraction Summary ===")
    for t, fields in results.items():
        public = {k: v for k, v in fields.items() if not k.startswith("_")}
        if public:
            print(f"\n{t}:")
            for f, v in public.items():
                print(f"  {f}: {v['value']} [{v['confidence']}] via {v['method']}")
        else:
            print(f"\n{t}: (no fields extracted)")
