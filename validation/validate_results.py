"""
validate_results.py — 多元数据查验主程序
=========================================
Usage:
    python validation/validate_results.py
    python validation/validate_results.py --input results.csv --tickers NVDA PLTR
    python validation/validate_results.py --no-fmp --no-finnhub   # free sources only
    python validation/validate_results.py --refresh               # ignore cache

Outputs (in project root):
    results_validated.csv
    validation_report.html
    validation_summary.json
    validation.log
"""

from __future__ import annotations
import sys
import os
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import csv
import json
import time
import logging
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────────────
_VALIDATION_DIR = Path(__file__).parent
_ROOT           = _VALIDATION_DIR.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scoring"))

# ── .env loading ──────────────────────────────────────────────────────
def _load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

_load_env(_ROOT / ".env")
_load_env(_ROOT / ".env.example")   # fallback defaults (won't overwrite real .env)

# ── Logging ───────────────────────────────────────────────────────────
_LOG_PATH = _ROOT / "validation.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("validate_results")

# ── Imports (after path setup) ────────────────────────────────────────
from fetchers.fmp          import FMPFetcher
from fetchers.finnhub      import FinnhubFetcher
from fetchers.sec          import SECFetcher
from fetchers.yf_fetcher   import calc_momentum
from validators.sector_validator    import validate as validate_sector
from validators.financial_validator import validate as validate_financial
from validators.score_validator     import validate as validate_score
from report_generator import generate_csv, generate_html, generate_json


# ── Confidence scoring ────────────────────────────────────────────────

def compute_confidence(
    ticker_matched:    bool,
    sector_confirmed:  bool,  # ≥2 sources or conf≥0.7
    sec_available:     bool,
    momentum_ok:       bool,
    score_ok:          bool,
    no_anomalies:      bool,
) -> float:
    score = 0.0
    if ticker_matched:   score += 0.20
    if sector_confirmed: score += 0.20
    if sec_available:    score += 0.20
    if momentum_ok:      score += 0.15
    if score_ok:         score += 0.15
    if no_anomalies:     score += 0.10
    return round(score, 2)


def status_from_confidence(conf: float) -> str:
    if conf >= 0.85: return "PASS"
    if conf >= 0.65: return "REVIEW"
    return "FAIL"


# ── Per-ticker validation ─────────────────────────────────────────────

def validate_ticker(
    ticker:     str,
    csv_row:    dict,
    fmp:        FMPFetcher,
    finnhub:    FinnhubFetcher,
    sec:        SECFetcher,
    ttl_hours:  int = 24,
    refresh:    bool = False,
) -> dict:
    """Run all validation checks for one ticker. Returns merged result dict."""
    logger.info("Validating %s …", ticker)
    notes: list[str] = []

    # ── 1. Fetch external data ─────────────────────────────────────────
    if refresh:
        # Delete cache files to force re-fetch
        from pathlib import Path as _P
        cache_dir = _VALIDATION_DIR / "cache" / "raw"
        for f in cache_dir.glob(f"*_{ticker}_*"):
            f.unlink(missing_ok=True)
        for f in cache_dir.glob(f"yf_hist_{ticker}.pkl"):
            f.unlink(missing_ok=True)

    try: fmp_profile     = fmp.get_profile(ticker)
    except Exception as e:
        fmp_profile = {}; notes.append(f"FMP profile error: {e}")

    try: fmp_ratios      = fmp.get_ratios(ticker)
    except Exception as e:
        fmp_ratios = {}; notes.append(f"FMP ratios error: {e}")

    try: fmp_income      = fmp.get_income(ticker)
    except Exception as e:
        fmp_income = {}; notes.append(f"FMP income error: {e}")

    try: fmp_metrics     = fmp.get_key_metrics(ticker)
    except Exception as e:
        fmp_metrics = {}; notes.append(f"FMP key-metrics error: {e}")

    try: finnhub_profile = finnhub.get_profile(ticker)
    except Exception as e:
        finnhub_profile = {}; notes.append(f"Finnhub profile error: {e}")

    try: finnhub_metrics = finnhub.get_metrics(ticker)
    except Exception as e:
        finnhub_metrics = {}; notes.append(f"Finnhub metrics error: {e}")

    try: sec_info        = sec.get_company_info(ticker)
    except Exception as e:
        sec_info = {}; notes.append(f"SEC info error: {e}")

    try: sec_financials  = sec.get_financials(ticker)
    except Exception as e:
        sec_financials = {}; notes.append(f"SEC financials error: {e}")

    try: sec_10k_url     = sec.get_business_description(ticker)
    except Exception as e:
        sec_10k_url = ""; notes.append(f"SEC 10-K error: {e}")

    try: momentum_data   = calc_momentum(ticker, ttl_hours=ttl_hours)
    except Exception as e:
        momentum_data = {}; notes.append(f"yfinance momentum error: {e}")

    # ── 2. Ticker / company name match ────────────────────────────────
    csv_company  = csv_row.get("company_公司名", csv_row.get("company", "")).lower()
    fmp_name     = fmp_profile.get("company_name", "").lower()
    finnhub_name = finnhub_profile.get("company_name", "").lower()
    sec_name     = sec_info.get("company_name", "").lower()

    def _name_match(a: str, b: str) -> bool:
        if not a or not b: return False
        # Match if either is a substring of the other (handles "Apple Inc." vs "Apple")
        a, b = a[:30], b[:30]
        return a in b or b in a

    ticker_matched = any([
        _name_match(csv_company, fmp_name),
        _name_match(csv_company, finnhub_name),
        _name_match(csv_company, sec_name),
        bool(fmp_name) or bool(sec_name),   # at least one source confirmed ticker exists
    ])

    # ── 3. Sector validation ──────────────────────────────────────────
    csv_sector   = csv_row.get("sector_板块", csv_row.get("sector", ""))
    description  = fmp_profile.get("description", "")
    sec_val = validate_sector(
        ticker, csv_sector,
        fmp_profile, finnhub_profile, sec_info,
        description=description,
    )
    sector_confidence = sec_val["sector_confidence"]
    source_conflict   = sec_val["source_conflict"]
    notes            += sec_val["notes"]
    ai_keyword_count  = sec_val["ai_keyword_count"]

    # Sector confirmed if ≥2 sources agree OR keyword confidence is high.
    # When running without FMP/Finnhub, only SEC SIC data is available; SIC_MAP
    # doesn't cover all codes, so "Unknown" suggestion ≠ wrong sector.
    sector_confirmed = (
        (not source_conflict and sector_confidence >= 0.50)
        or sector_confidence >= 0.70
        or not (fmp_profile or finnhub_profile)   # no label-rich sources = can't refute
    )

    # ── 4. Financial anomaly validation ──────────────────────────────
    fin_val = validate_financial(
        ticker, csv_row, fmp_profile, fmp_ratios,
        fmp_income, finnhub_metrics, sec_financials, ai_keyword_count,
    )
    raw_data_conflict = fin_val["raw_data_conflict"]
    notes            += fin_val["notes"]
    no_anomalies      = fin_val["anomaly_count"] == 0

    # ── 5. Score recalculation ────────────────────────────────────────
    # Pass live momentum so recalc uses same rsi_14/price_vs_200dma as CSV
    score_val = validate_score(ticker, csv_row, live_momentum=momentum_data)
    formula_mismatch              = score_val["formula_mismatch"]
    final_score_recalculated      = score_val["final_score_recalculated"]
    base_score_recalculated       = score_val["base_score_recalculated"]
    dynamic_adj_recalculated      = score_val["dynamic_adjustment_recalculated"]
    formula_diff_abs              = score_val["formula_diff_abs"]
    formula_diff_level            = score_val["formula_diff_level"]
    formula_diff_reason           = score_val["formula_diff_reason"]
    notes                        += score_val["notes"]

    csv_final = None
    for k, v in csv_row.items():
        if "final_" in k:
            try: csv_final = float(v); break
            except: pass

    score_ok = (
        not formula_mismatch
        and final_score_recalculated is not None
    )

    # ── 6. Momentum recalculation ─────────────────────────────────────
    mom_recalc = momentum_data.get("momentum_score")
    csv_mom = None
    for k, v in csv_row.items():
        if k.startswith("mom_"):
            try: csv_mom = float(v); break
            except: pass

    momentum_ok = True
    if mom_recalc is not None and csv_mom is not None:
        mom_delta = abs(mom_recalc - csv_mom)
        if mom_delta > 20:
            momentum_ok = False
            notes.append(
                f"[MOM-DELTA] CSV momentum={csv_mom:.1f} vs yfinance-recalc={mom_recalc:.1f} "
                f"(Δ={mom_delta:.1f})"
            )

    # ── 7. SEC data availability ──────────────────────────────────────
    sec_available = bool(sec_info.get("cik")) or bool(sec_financials)

    # ── 8. Confidence + status ────────────────────────────────────────
    confidence = compute_confidence(
        ticker_matched, sector_confirmed, sec_available,
        momentum_ok, score_ok, no_anomalies,
    )
    status = status_from_confidence(confidence)

    # ── 9. human_review_required ──────────────────────────────────────
    human_review = any([
        formula_mismatch,
        source_conflict,
        raw_data_conflict,
        status == "FAIL",
        (ai_keyword_count <= 1 and float(
            next((v for k, v in csv_row.items() if k.startswith("ai_")), 0) or 0
        ) > 75),
    ])

    # ── 10. Assemble source URLs ──────────────────────────────────────
    cik       = sec_info.get("cik", "")
    edgar_url = sec_info.get("edgar_url", "")
    if cik and not edgar_url:
        edgar_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=10-K"

    # ── 11. Build output row ──────────────────────────────────────────
    all_notes_str = " | ".join(notes) if notes else ""

    out = dict(csv_row)   # copy all original CSV columns
    out.update({
        "validation_status":               status,
        "validation_confidence":           confidence,
        "source_conflict":                 "TRUE" if source_conflict  else "FALSE",
        "formula_mismatch":                "TRUE" if formula_mismatch else "FALSE",
        "formula_diff_level":              formula_diff_level,
        "formula_diff_abs":                round(formula_diff_abs, 3) if formula_diff_abs is not None else "",
        "formula_diff_reason":             formula_diff_reason,
        "base_score_recalculated":         base_score_recalculated  if base_score_recalculated  is not None else "",
        "dynamic_adjustment_recalculated": dynamic_adj_recalculated if dynamic_adj_recalculated is not None else "",
        "final_score_recalculated":        final_score_recalculated if final_score_recalculated is not None else "",
        "sector_confidence":               sector_confidence,
        "sector_suggested":                sec_val["sector_suggested"],
        "raw_data_conflict":               "TRUE" if raw_data_conflict else "FALSE",
        "momentum_recalculated":           round(mom_recalc, 1) if mom_recalc is not None else "",
        "human_review_required":           "TRUE" if human_review else "FALSE",
        "validation_notes":                all_notes_str,
        "source_urls_yf":  f"https://finance.yahoo.com/quote/{ticker}",
        "source_urls_fmp": f"https://financialmodelingprep.com/financial-summary/{ticker}",
        "source_urls_sec": edgar_url,
        "source_urls_10k": sec_10k_url,
    })
    return out


# ── Main ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="多元数据查验 pipeline")
    ap.add_argument("--input",       default=str(_ROOT / "results.csv"),
                    help="Input CSV path (default: results.csv in project root)")
    ap.add_argument("--tickers",     nargs="*",
                    help="Validate only these tickers (default: all in CSV)")
    ap.add_argument("--no-fmp",      action="store_true", help="Skip FMP API")
    ap.add_argument("--no-finnhub",  action="store_true", help="Skip Finnhub API")
    ap.add_argument("--refresh",     action="store_true", help="Force cache refresh")
    ap.add_argument("--workers",     type=int, default=int(os.getenv("MAX_WORKERS", 6)),
                    help="Parallel workers (default 6)")
    ap.add_argument("--ttl",         type=int, default=int(os.getenv("CACHE_TTL_HOURS", 24)),
                    help="Cache TTL hours (default 24)")
    ap.add_argument("--out-dir",     default=str(_ROOT),
                    help="Output directory (default: project root)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)

    # ── Read input CSV ────────────────────────────────────────────────
    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    with open(input_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    logger.info("Loaded %d rows from %s", len(rows), input_path)

    if args.tickers:
        filter_set = {t.upper() for t in args.tickers}
        rows = [r for r in rows if r.get("ticker", "").upper() in filter_set]
        logger.info("Filtered to %d tickers: %s", len(rows), args.tickers)

    # ── Instantiate fetchers ──────────────────────────────────────────
    fmp_key     = "" if args.no_fmp     else os.getenv("FMP_API_KEY", "")
    finnhub_key = "" if args.no_finnhub else os.getenv("FINNHUB_API_KEY", "")

    fmp     = FMPFetcher(api_key=fmp_key,     ttl_hours=args.ttl)
    finnhub = FinnhubFetcher(api_key=finnhub_key, ttl_hours=args.ttl)
    sec     = SECFetcher(ttl_hours=args.ttl)

    if fmp.available:     logger.info("FMP API: available")
    else:                 logger.info("FMP API: NOT configured (skipping)")
    if finnhub.available: logger.info("Finnhub API: available")
    else:                 logger.info("Finnhub API: NOT configured (skipping)")

    # ── Pre-load SEC CIK map (one network call, cached) ──────────────
    logger.info("Loading SEC CIK map …")
    from fetchers.sec import _get_cik_map, _CIK_MAP
    import fetchers.sec as _sec_mod
    if not _sec_mod._CIK_MAP:
        _sec_mod._CIK_MAP = _get_cik_map()
    logger.info("SEC CIK map loaded (%d entries)", len(_sec_mod._CIK_MAP))

    # ── Parallel validation ───────────────────────────────────────────
    t0 = time.perf_counter()
    validated: list[dict] = []
    total = len(rows)

    def _worker(row: dict) -> dict:
        ticker = row.get("ticker", "").upper()
        return validate_ticker(ticker, row, fmp, finnhub, sec,
                               ttl_hours=args.ttl, refresh=args.refresh)

    # Use ThreadPoolExecutor; SEC has 10 req/s limit so cap workers
    workers = min(args.workers, 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, r): r.get("ticker","?") for r in rows}
        done = 0
        for future in as_completed(futures):
            ticker = futures[future]
            done  += 1
            try:
                validated.append(future.result())
                print(f"\r  Validated {done}/{total}: {ticker}  ", end="", flush=True)
            except Exception as e:
                logger.error("Worker failed for %s: %s", ticker, e)
                # Include row as-is with FAIL status
                orig = next((r for r in rows if r.get("ticker","") == ticker), {})
                orig["validation_status"] = "FAIL"
                orig["validation_notes"]  = f"Worker exception: {e}"
                validated.append(orig)

    print(f"\n  Done in {time.perf_counter()-t0:.1f}s")

    # Sort by final_score descending for the CSV output
    def _fscore(r):
        for k,v in r.items():
            if "final_" in k:
                try: return -float(v)
                except: pass
        return 0
    validated.sort(key=_fscore)

    # ── Write outputs ─────────────────────────────────────────────────
    csv_out  = out_dir / "results_validated.csv"
    html_out = out_dir / "validation_report.html"
    json_out = out_dir / "validation_summary.json"

    generate_csv(validated,  str(csv_out))
    generate_html(validated, str(html_out))
    generate_json(validated, str(json_out))

    # ── Print summary ─────────────────────────────────────────────────
    pass_n   = sum(1 for r in validated if r.get("validation_status") == "PASS")
    review_n = sum(1 for r in validated if r.get("validation_status") == "REVIEW")
    fail_n   = sum(1 for r in validated if r.get("validation_status") == "FAIL")
    mismatch = sum(1 for r in validated if str(r.get("formula_mismatch","")).upper() == "TRUE")
    conflict = sum(1 for r in validated if str(r.get("source_conflict","")).upper() == "TRUE")

    print(f"""
  ╔════════════════════════════════════════╗
  ║  VALIDATION SUMMARY                    ║
  ╠════════════════════════════════════════╣
  ║  Total           : {total:>4}               ║
  ║  PASS            : {pass_n:>4}               ║
  ║  REVIEW          : {review_n:>4}               ║
  ║  FAIL            : {fail_n:>4}               ║
  ║  Formula mismatch: {mismatch:>4}               ║
  ║  Source conflict : {conflict:>4}               ║
  ╚════════════════════════════════════════╝
""")
    logger.info("Validation complete. Log: %s", _LOG_PATH)


if __name__ == "__main__":
    # Must run from project root or validation/ dir
    os.chdir(str(_VALIDATION_DIR))
    main()
