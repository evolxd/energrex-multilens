"""
Quant Multi-Factor Audit Runner
================================
Usage:
    python quant_audit.py NVDA
    python quant_audit.py NVDA --no-live       # skip yfinance, use mock only
    python quant_audit.py NVDA MSFT TSM        # batch mode
    python quant_audit.py --list               # show all supported tickers

PowerShell:
    python quant_audit.py NVDA
    python quant_audit.py NVDA MSFT --no-live
"""

from __future__ import annotations

import sys
import os
import argparse
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# Force UTF-8 output on Windows terminals (cp1252 can't render box chars)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── path setup ────────────────────────────────────────────────────────
_ROOT   = os.path.dirname(os.path.abspath(__file__))
_SCORE  = os.path.join(_ROOT, "scoring")
sys.path.insert(0, _SCORE)
sys.path.insert(0, _ROOT)

import numpy as np
import pandas as pd

from quant_engine import (
    score_ticker,
    AuditDimension,
    AuditEntry,
    ScoreResult,
    DIM_WEIGHTS,
    CIRCUIT_BETA_THRESH,
    CIRCUIT_DRAWDOWN_THRESH,
    CIRCUIT_DE_THRESH,
    CIRCUIT_MULTIPLIER,
)
from quant_data import QUANT_META, QUANT_STANDALONE, QUANT_AI_EXPOSURE

try:
    from mock_data import MOCK_STOCKS
except ImportError:
    MOCK_STOCKS = {}

logging.basicConfig(level=logging.WARNING)


# ─────────────────────────────────────────────────────────────────────
# Technical indicators (yfinance-based)
# ─────────────────────────────────────────────────────────────────────

def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
    delta  = prices.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    avg_g  = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_l  = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs     = avg_g / avg_l.replace(0, 1e-10)
    rsi    = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi.iloc[-1])


def _calc_max_drawdown(prices: pd.Series) -> float:
    roll_max  = prices.cummax()
    drawdown  = (prices - roll_max) / roll_max
    return float(drawdown.min())   # returns negative value


def _calc_vol_30d(prices: pd.Series) -> float:
    ret = prices.pct_change().dropna()
    return float(ret.tail(30).std() * (252 ** 0.5))


# ─────────────────────────────────────────────────────────────────────
# yfinance data fetch
# ─────────────────────────────────────────────────────────────────────

def fetch_yfinance_data(ticker: str) -> dict:
    """
    Fetch all auto fields from yfinance.
    Returns empty dict on failure; caller falls back to mock.
    Dirty-field detection: stores list of removed fields in _bad_fields.
    """
    try:
        import yfinance as yf
    except ImportError:
        print(f"  [WARN] yfinance not installed — skipping live fetch for {ticker}")
        return {}

    bad_fields: list[str] = []
    out: dict = {}

    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period="1y", auto_adjust=True)

        # ── Valuation ──────────────────────────────────────────────
        _map_field(out, info, "peg_ratio",   ["trailingPegRatio", "pegRatio"], bad_fields,
                   guard=lambda v: 0 < v < 100)
        _map_field(out, info, "ev_ebitda",   ["enterpriseToEbitda"], bad_fields,
                   guard=lambda v: 0 < v < 3000)
        _map_field(out, info, "ev_sales",    ["enterpriseToRevenue"], bad_fields,
                   guard=lambda v: 0 < v < 500)
        _map_field(out, info, "forward_pe",  ["forwardPE"], bad_fields,
                   guard=lambda v: 0 < v < 1000)
        _map_field(out, info, "market_cap",  ["marketCap"], bad_fields,
                   guard=lambda v: v > 1e8)
        _map_field(out, info, "current_price",["currentPrice","regularMarketPrice"], bad_fields,
                   guard=lambda v: v > 0)

        # FCF Yield: compute from freeCashflow / marketCap
        fcf = info.get("freeCashflow")
        mc  = out.get("market_cap") or info.get("marketCap")
        if fcf and mc and mc > 0:
            fcfy = fcf / mc
            if 0 < fcfy < 0.5:
                out["fcf_yield"] = round(fcfy, 6)
            else:
                bad_fields.append("fcf_yield")

        # ── Growth ──────────────────────────────────────────────────
        _map_field(out, info, "revenue_growth_yoy", ["revenueGrowth"], bad_fields,
                   guard=lambda v: -0.5 < v < 5.0)
        _map_field(out, info, "eps_growth_yoy",     ["earningsGrowth"], bad_fields,
                   guard=lambda v: -2.0 < v < 20.0)

        # ── Quality ─────────────────────────────────────────────────
        _map_field(out, info, "gross_margin", ["grossMargins"], bad_fields,
                   guard=lambda v: 0 < v < 1.01)
        _map_field(out, info, "operating_margin", ["operatingMargins"], bad_fields,
                   guard=lambda v: -1.0 < v < 1.0)

        # FCF margin: freeCashflow / totalRevenue
        rev = info.get("totalRevenue")
        if fcf and rev and rev > 0:
            fm = fcf / rev
            if -1.0 < fm < 1.0:
                out["fcf_margin"] = round(fm, 6)
            else:
                bad_fields.append("fcf_margin")

        # ROIC proxy: returnOnEquity (best available from yfinance)
        _map_field(out, info, "roic", ["returnOnEquity"], bad_fields,
                   guard=lambda v: -2.0 < v < 5.0)

        _map_field(out, info, "beta", ["beta"], bad_fields,
                   guard=lambda v: 0 < v < 10)

        # ── Technical from price history ────────────────────────────
        if not hist.empty and "Close" in hist.columns:
            closes = hist["Close"].dropna()
            if len(closes) >= 30:
                cur = float(closes.iloc[-1])

                # 200DMA
                if len(closes) >= 200:
                    ma200 = float(closes.tail(200).mean())
                else:
                    ma200 = float(closes.mean())
                dev = (cur - ma200) / ma200
                if -1.0 < dev < 5.0:
                    out["price_vs_200dma"] = round(dev, 6)
                else:
                    bad_fields.append("price_vs_200dma")

                # RSI-14
                if len(closes) >= 15:
                    rsi = _calc_rsi(closes, 14)
                    if 0 < rsi < 100:
                        out["rsi_14"] = round(rsi, 2)
                    else:
                        bad_fields.append("rsi_14")

                # 30d annualised volatility
                out["volatility_30d"] = round(_calc_vol_30d(closes), 6)

                # 1y max drawdown
                out["max_drawdown_1y"] = round(_calc_max_drawdown(closes), 6)

        out["_bad_fields"] = bad_fields
        out["_yf_fetched_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    except Exception as exc:
        print(f"  [WARN] yfinance fetch failed for {ticker}: {exc}")
        return {"_bad_fields": [], "_yf_error": str(exc)}

    return out


def fetch_yfinance_batch(tickers: list[str], max_workers: int = 10) -> dict[str, dict]:
    """Parallel yfinance fetch for all tickers at once. ~5-8x faster than serial."""
    cache: dict[str, dict] = {}
    n = len(tickers)
    done = 0
    with ThreadPoolExecutor(max_workers=min(max_workers, n)) as pool:
        future_map = {pool.submit(fetch_yfinance_data, t): t for t in tickers}
        for future in as_completed(future_map):
            ticker = future_map[future]
            done += 1
            try:
                cache[ticker] = future.result()
            except Exception as e:
                cache[ticker] = {"_bad_fields": [], "_yf_error": str(e)}
            print(f"\r  yfinance: {done}/{n} fetched…", end="", flush=True)
    print()   # newline after progress
    return cache


def _map_field(
    out: dict,
    info: dict,
    target: str,
    keys: list[str],
    bad: list[str],
    guard=None,
):
    for k in keys:
        v = info.get(k)
        if v is not None:
            try:
                v = float(v)
                if guard is None or guard(v):
                    out[target] = round(v, 6)
                else:
                    bad.append(f"{target}(raw={v:.4g})")
            except (TypeError, ValueError):
                bad.append(f"{target}(non-numeric)")
            return


# ─────────────────────────────────────────────────────────────────────
# Data merging + cleaning
# ─────────────────────────────────────────────────────────────────────

def merge_data(ticker: str, use_live: bool = True,
               live_cache: dict[str, dict] | None = None) -> dict:
    """
    Priority stack (highest wins):
      1. yfinance live (from cache if available, else single fetch)
      2. MOCK_STOCKS
      3. QUANT_STANDALONE fallback
      4. QUANT_META delta fields (sector_tag, capex_rev)
      5. QUANT_AI_EXPOSURE — fills None values only (AI exposure补全)
    """
    # Base: MOCK_STOCKS or standalone
    if ticker in MOCK_STOCKS:
        base = dict(MOCK_STOCKS[ticker])
    elif ticker in QUANT_STANDALONE:
        base = dict(QUANT_STANDALONE[ticker])
    else:
        base = {}

    # Layer QUANT_META delta fields (sector_tag, capex_rev etc.)
    if ticker in QUANT_META:
        for k, v in QUANT_META[ticker].items():
            base.setdefault(k, v)

    # Layer QUANT_AI_EXPOSURE — overrides None values (补全 top-40 AI exposure字段)
    if ticker in QUANT_AI_EXPOSURE:
        for k, v in QUANT_AI_EXPOSURE[ticker].items():
            if base.get(k) is None:
                base[k] = v

    # Live yfinance overwrites
    if use_live:
        live = (live_cache.get(ticker) if live_cache is not None
                else fetch_yfinance_data(ticker))
        if live is None:
            live = fetch_yfinance_data(ticker)
        bad  = live.pop("_bad_fields", [])
        base["_bad_fields"] = bad
        live_applied = []
        for k, v in live.items():
            if not k.startswith("_"):
                base[k] = v
                live_applied.append(k)
        base["_live_fields"] = live_applied
        if "_yf_fetched_at" in live:
            base["_yf_fetched_at"] = live["_yf_fetched_at"]
    else:
        base.setdefault("_bad_fields", [])

    # Default sector
    if "sector_tag" not in base:
        base["sector_tag"] = "Hardware"

    return base


def clean_data(data: dict) -> dict:
    """
    Remove fields with clearly invalid values (NaN, Inf, extreme outliers).
    Records removed fields in _bad_fields.
    """
    bad = list(data.get("_bad_fields", []))
    cleaned = {}
    NUMERIC_FIELDS = {
        "peg_ratio", "ev_ebitda", "ev_sales", "forward_pe", "fcf_yield",
        "revenue_growth_yoy", "eps_growth_yoy", "fcf_growth_yoy",
        "next_year_revenue_growth_est",
        "gross_margin", "fcf_margin", "operating_margin", "roic",
        "debt_to_equity", "capex_rev", "net_revenue_retention",
        "ai_revenue_exposure_pct", "ai_profit_exposure_pct",
        "ai_growth_contribution_pct", "datacenter_exposure_pct",
        "advanced_packaging_exposure_pct", "ai_order_backlog_exposure",
        "software_ai_platform_exposure_pct",
        "actual_revenue_vs_consensus", "actual_eps_vs_consensus",
        "guidance_vs_consensus", "earnings_reaction_score",
        "market_expectation_score",
        "beta", "volatility_30d", "max_drawdown_1y",
        "valuation_risk", "liquidity_risk",
        "price_vs_200dma", "rsi_14",
    }
    for k, v in data.items():
        if k in NUMERIC_FIELDS:
            if v is None:
                cleaned[k] = v   # engine handles None gracefully
            else:
                try:
                    f = float(v)
                    import math
                    if math.isnan(f) or math.isinf(f):
                        bad.append(f"{k}(nan/inf)")
                    else:
                        cleaned[k] = f
                except (TypeError, ValueError):
                    bad.append(f"{k}(non-numeric)")
        else:
            cleaned[k] = v

    cleaned["_bad_fields"] = bad
    return cleaned


# ─────────────────────────────────────────────────────────────────────
# Audit Log Printer
# ─────────────────────────────────────────────────────────────────────

_W = 84   # total line width

def _box(text: str):
    pad  = _W - 4
    line = f"║  {text:<{pad}}║"
    print("╔" + "═" * (_W - 2) + "╗")
    for t in (text if isinstance(text, list) else [text]):
        print(f"║  {t:<{pad}}║")
    print("╚" + "═" * (_W - 2) + "╝")


def _hr(char="─"):
    print(char * _W)


def _dim_header(label: str, weight: float):
    right = f"w={weight*100:.0f}%"
    gap   = _W - len(label) - len(right) - 2
    print(f"\n  {label}{' ' * gap}{right}")
    _hr("─")


COL_FIELD  = 22
COL_RAW    = 14
COL_FORM   = 38
COL_SCORE  =  7

def _field_line(e: AuditEntry):
    flag = " [MISS]" if e.missing else ""
    raw  = _fmt_v(e.raw_value)
    form = e.formula[:COL_FORM - 1]
    sc   = f"{e.score:>6.1f}"
    print(f"  {e.field_name:<{COL_FIELD}}{raw:<{COL_RAW}}"
          f"{form:<{COL_FORM}}{sc}{flag}")
    if e.note and not e.missing:
        print(f"  {'':>{COL_FIELD + COL_RAW}}↳ {e.note}")


def _fmt_v(v) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:,.1f}"
        if abs(v) < 0.01:
            return f"{v:.6f}"
        return f"{v:.4f}"
    return str(v)


def print_audit_report(result: ScoreResult, data: dict):
    ts  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    yft = data.get("_yf_fetched_at", "mock-only")
    vin = data.get("_data_vintage", "manual")

    print()
    # Header box
    hdr_lines = [
        f"QUANT FACTOR AUDIT REPORT                              {ts}",
        f"Ticker: {result.ticker:<8}  Company: {result.company_name:<30}  Sector: {result.sector}",
    ]
    pad = _W - 4
    print("╔" + "═" * (_W - 2) + "╗")
    for l in hdr_lines:
        print(f"║  {l:<{pad}}║")
    print("╚" + "═" * (_W - 2) + "╝")

    # Meta info
    bad_n = len(result.bad_fields)
    print(f"  yfinance live: {yft:<25}  mock vintage: {vin}")
    print(f"  剔除壞字段 {bad_n} 個" +
          (f": {', '.join(result.bad_fields[:6])}" if bad_n else " ✓") +
          (f"... (+{bad_n-6} more)" if bad_n > 6 else ""))
    circ_txt = (f"⚡ TRIGGERED  →  FINAL×{CIRCUIT_MULTIPLIER}  [{result.circuit_reason}]"
                if result.circuit_triggered else "✓ not triggered")
    print(f"  Circuit Breaker: {circ_txt}")
    _hr("═")

    # Per-dimension blocks
    col_header = (f"  {'FIELD':<{COL_FIELD}}{'RAW VALUE':<{COL_RAW}}"
                  f"{'FORMULA  →  RESULT':<{COL_FORM}}{'SCORE':>{COL_SCORE}}")

    for dim in result.audit_dims:
        if dim.key == "risk_penalty":
            _dim_header(f"⑦ RISK PENALTY  (max -{int(20)} pts)", 0.0)
            print(col_header)
            _hr("─")
            for e in dim.entries:
                _field_line(e)
            _hr("─")
            print(f"  {'':>{COL_FIELD + COL_RAW}}"
                  f"{'RawRisk (0–1) × 20 pts  →':<{COL_FORM}}"
                  f"  -{dim.dim_score:>5.1f}")
            continue

        _dim_header(dim.label, dim.final_weight)
        print(col_header)
        _hr("─")
        for e in dim.entries:
            _field_line(e)
        _hr("─")

        # Weighted formula breakdown
        valid = [e for e in dim.entries if not e.missing]
        total_w = sum(e.weight for e in valid) or 1.0
        terms   = [f"{e.score:.1f}×{e.weight/total_w:.2f}" for e in valid]
        eq      = " + ".join(terms)
        print(f"  Weighted: {eq}")
        pad_r = _W - 14 - len(f"{dim.dim_score:.2f}")
        print(f"  {dim.label} Score = {dim.dim_score:.2f} / 100"
              f"{'':>{max(1,_W-40-len(f'{dim.dim_score:.2f}'))}} "
              f"contribution = {dim.dim_score:.1f} × {dim.final_weight:.0%} = {dim.contribution:.2f} pts")

    # ── Final tally ──────────────────────────────────────────────────
    _hr("═")
    print(f"\n  FINAL SCORE CALCULATION")
    _hr("─")

    # Print each dimension contribution
    for dim in result.audit_dims:
        if dim.key == "risk_penalty":
            continue
        label_pad = f"  {dim.label} Score"
        print(f"  {dim.label + ' Score':<34}"
              f"{dim.dim_score:>6.2f} × {dim.final_weight:.0%}"
              f"  =  {dim.contribution:>6.2f} pts")

    print(f"  {'':─<64}")
    print(f"  {'Raw Weighted Sum':<34}{'':>10}       {result.raw_sum:>6.2f} pts")
    risk_dim = next(d for d in result.audit_dims if d.key == "risk_penalty")
    print(f"  {'Risk Penalty':<34}{'':>14}    - {result.risk_penalty:>5.2f} pts")
    net = result.raw_sum - result.risk_penalty
    print(f"  {'Net (before circuit)':<34}{'':>14}     {net:>6.2f} pts")

    if result.circuit_triggered:
        print(f"\n  ⚡ CIRCUIT BREAKER triggered → × {result.risk_multiplier}")
        print(f"     {net:.2f} × {result.risk_multiplier}  =  {result.final_score:.2f}")

    _hr("═")

    # Rating badge
    RATING_ICONS = {
        "Buy":       "⭐  BUY",
        "Hold":      "✅  HOLD",
        "Watchlist": "👀  WATCHLIST",
        "Avoid":     "🚫  AVOID",
    }
    icon = RATING_ICONS.get(result.rating, result.rating)
    score_bar = _bar(result.final_score)
    print(f"\n  FINAL SCORE  :  {result.final_score:>6.2f} / 100   {score_bar}")
    print(f"  RATING       :  {icon}")
    if result.circuit_triggered:
        print(f"  NOTE         :  Rating capped at Watchlist/Avoid due to circuit breaker")
    print()
    _hr("═")
    print()


def _bar(score: float, width: int = 30) -> str:
    filled = int(score / 100 * width)
    if score >= 70:
        ch = "█"
    elif score >= 55:
        ch = "▓"
    elif score >= 40:
        ch = "▒"
    else:
        ch = "░"
    return "[" + ch * filled + "·" * (width - filled) + f"] {score:.1f}"


# ─────────────────────────────────────────────────────────────────────
# Batch summary table
# ─────────────────────────────────────────────────────────────────────

def print_batch_summary(results: list[tuple[ScoreResult, dict]]):
    print("\n" + "═" * _W)
    print("  BATCH SUMMARY")
    print("═" * _W)
    hdr = (f"  {'TICKER':<8}{'SECTOR':<12}{'VAL':>6}{'GRW':>6}"
           f"{'QLT':>6}{'AI':>6}{'EXP':>6}{'MOM':>6}"
           f"{'RISK-':>7}{'FINAL':>8}  RATING")
    print(hdr)
    print("─" * _W)
    for r, _ in sorted(results, key=lambda x: x[0].final_score, reverse=True):
        circ = "⚡" if r.circuit_triggered else "  "
        scores = r.dim_scores
        print(
            f"  {r.ticker:<8}{r.sector:<12}"
            f"{scores.get('valuation',0):>6.1f}"
            f"{scores.get('growth',0):>6.1f}"
            f"{scores.get('quality',0):>6.1f}"
            f"{scores.get('ai_exposure',0):>6.1f}"
            f"{scores.get('expectation_gap',0):>6.1f}"
            f"{scores.get('momentum',0):>6.1f}"
            f"{r.risk_penalty:>7.1f}"
            f"{r.final_score:>8.2f}  {circ}{r.rating}"
        )
    print("─" * _W)


# ─────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────

def _all_tickers() -> list[str]:
    return sorted(set(list(MOCK_STOCKS.keys()) +
                      list(QUANT_META.keys()) +
                      list(QUANT_STANDALONE.keys())))


def main():
    parser = argparse.ArgumentParser(
        description="Quant Multi-Factor Audit — hedge-fund grade scoring"
    )
    parser.add_argument("tickers", nargs="*", help="Ticker symbol(s), e.g. NVDA MSFT TSM")
    parser.add_argument("--no-live",  action="store_true",
                        help="Skip yfinance; use mock data only")
    parser.add_argument("--list",     action="store_true",
                        help="List all supported tickers and exit")
    parser.add_argument("--summary",  action="store_true",
                        help="Print batch summary table only (suppress per-ticker detail)")
    parser.add_argument("--csv",      metavar="FILE",
                        help="Export results to CSV (e.g. --csv results.csv)")
    args = parser.parse_args()

    if args.list:
        tickers = _all_tickers()
        print(f"\nSupported tickers ({len(tickers)}):")
        for i, t in enumerate(tickers):
            end = "\n" if (i + 1) % 10 == 0 else "  "
            print(f"{t:<8}", end=end)
        print()
        return

    if not args.tickers:
        parser.print_help()
        sys.exit(0)

    use_live = not args.no_live
    results: list[tuple[ScoreResult, dict]] = []
    tickers = [t.upper() for t in args.tickers]

    # Pre-fetch all tickers in parallel (skip for single ticker — no benefit)
    live_cache: dict[str, dict] | None = None
    if use_live and len(tickers) > 1:
        print(f"\n  Pre-fetching {len(tickers)} tickers via yfinance (parallel)…")
        import time as _time
        _t0 = _time.perf_counter()
        live_cache = fetch_yfinance_batch(tickers)
        _elapsed = _time.perf_counter() - _t0
        print(f"  Done in {_elapsed:.1f}s\n")

    for ticker in tickers:
        print(f"\n{'─'*_W}")
        print(f"  Processing: {ticker}  ({'live yfinance' if use_live else 'mock-only'})")
        print(f"{'─'*_W}")

        raw  = merge_data(ticker, use_live=use_live, live_cache=live_cache)
        data = clean_data(raw)

        if not data:
            print(f"  [ERROR] No data found for {ticker} — skipping")
            continue

        result = score_ticker(ticker, data)

        if not args.summary:
            print_audit_report(result, data)
        else:
            bad_n = len(result.bad_fields)
            print(f"  剔除壞字段 {bad_n} 個" + (f": {result.bad_fields}" if bad_n else " ✓"))
            print(f"  FINAL SCORE = {result.final_score:.2f}  |  {result.rating}")

        results.append((result, data))

    if len(results) > 1:
        print_batch_summary(results)

    if args.csv and results:
        export_csv(results, args.csv)


def export_csv(results: list[tuple[ScoreResult, dict]], path: str):
    """
    导出增强版 CSV：
    - 列名含双语说明（英文机读 + 中文含义）
    - 关键原始字段显示数值 + 来源标注
        [yf] = yfinance 实时拉取
        [mk] = mock_data 手动估算（季报后更新）
        [cal] = 由其他字段计算推导
        [--] = 缺失/未知
    """
    import csv

    def _src(data: dict, field: str) -> str:
        live_fields = data.get("_live_fields", [])
        if field in live_fields:
            return "[yf]"
        if data.get(field) is not None:
            return "[mk]"
        return "[--]"

    def _fmt(data: dict, field: str, pct: bool = False, decimals: int = 2) -> str:
        v = data.get(field)
        src = _src(data, field)
        if v is None:
            return f"n/a {src}"
        if pct:
            return f"{v*100:.1f}% {src}"
        return f"{round(v, decimals)} {src}"

    rows = []
    for r, data in sorted(results, key=lambda x: x[0].final_score, reverse=True):
        live_fields = data.get("_live_fields", [])
        row = {
            # ── 标识 ─────────────────────────────────────────────────────
            "ticker":                           r.ticker,
            "company_公司名":                   r.company_name,
            "sector_板块":                      r.sector,

            # ── 六维得分 (0-100) ─────────────────────────────────────────
            "val_估值得分(PEG/EV/ERG/PE/FCFYld)":  round(r.dim_scores.get("valuation",       0), 1),
            "grw_成长得分(营收/EPS/FCF/指引增速)":  round(r.dim_scores.get("growth",          0), 1),
            "qlt_质量得分(毛利率/FCF率/ROIC/负债)": round(r.dim_scores.get("quality",         0), 1),
            "ai_AI暴露得分(AI营收/平台/订单占比)":   round(r.dim_scores.get("ai_exposure",     0), 1),
            "exp_预期差得分(超预期营收EPS指引)":     round(r.dim_scores.get("expectation_gap", 0), 1),
            "mom_动量得分(RSI14/价格vs200日均)":    round(r.dim_scores.get("momentum",        0), 1),

            # ── 风险 & 综合 ──────────────────────────────────────────────
            "risk_风险扣分(max20,Beta/回撤/负债)":  round(r.risk_penalty, 1),
            "final_综合得分(0-100)":               round(r.final_score,  2),
            "rating_评级":                         r.rating,
            "circuit_熔断(Beta>2.2且回撤>35%或DE>1.8)": "YES" if r.circuit_triggered else "",

            # ── 原始估值字段 ─────────────────────────────────────────────
            "raw_peg_市盈增长比(越低越便宜)":        _fmt(data, "peg_ratio"),
            "raw_ev_sales_EV营收比":                _fmt(data, "ev_sales"),
            "raw_forward_pe_远期市盈率":             _fmt(data, "forward_pe"),
            "raw_fcf_yield_FCF收益率(越高越好)":     _fmt(data, "fcf_yield", pct=True),

            # ── 原始成长字段 ─────────────────────────────────────────────
            "raw_rev_growth_营收同比增速":           _fmt(data, "revenue_growth_yoy", pct=True),
            "raw_eps_growth_EPS同比增速":            _fmt(data, "eps_growth_yoy",     pct=True),
            "raw_fwd_rev_guide_NTM营收指引增速":     _fmt(data, "next_year_revenue_growth_est", pct=True),

            # ── 原始质量字段 ─────────────────────────────────────────────
            "raw_gross_margin_毛利率":              _fmt(data, "gross_margin",  pct=True),
            "raw_fcf_margin_FCF利润率":             _fmt(data, "fcf_margin",    pct=True),
            "raw_roic_投入资本回报率":               _fmt(data, "roic",          pct=True),
            "raw_de_ratio_债务权益比(含可转债)":     _fmt(data, "debt_to_equity"),
            "raw_nrr_净收入留存率(SaaS/Cyber)":     _fmt(data, "net_revenue_retention"),

            # ── 原始动量/风险字段 ────────────────────────────────────────
            "raw_rsi14_RSI14(45-65最佳)":           _fmt(data, "rsi_14"),
            "raw_vs200ma_价格偏离200日均(%)":        _fmt(data, "price_vs_200dma", pct=True),
            "raw_beta_贝塔系数":                    _fmt(data, "beta"),
            "raw_max_dd_1y_1年最大回撤":            _fmt(data, "max_drawdown_1y", pct=True),

            # ── 数据质量 ─────────────────────────────────────────────────
            "live_fields_实时字段数":               len(live_fields),
            "bad_fields_剔除字段":                  "; ".join(r.bad_fields) if r.bad_fields else "",
        }
        rows.append(row)

    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig = Excel BOM
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  ✓ CSV exported → {path}  ({len(rows)} rows, {len(fieldnames)} columns)")


if __name__ == "__main__":
    main()
