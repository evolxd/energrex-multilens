"""
Financial data validator
========================
Checks:
  1. Numeric range anomalies (unit errors, impossible values)
  2. Cross-source consistency (CSV raw_ columns vs FMP vs Finnhub vs SEC)
  3. Score-metric conflicts (high score but bad underlying data)
  4. AI exposure suspicious check
"""

from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Hard range limits ──────────────────────────────────────────────────

RANGES: dict[str, tuple[float, float]] = {
    "revenue_growth":   (-0.80, 3.00),   # -80% to +300%
    "gross_margin":     (-0.20, 1.00),   # -20% to 100%
    "operating_margin": (-1.00, 0.80),
    "net_margin":       (-2.00, 0.80),
    "fcf_margin":       (-1.00, 0.80),
    "roic":             (-1.00, 3.00),
    "de_ratio":         (-50.0, 50.0),
    "ev_sales":         (0.0,   200.0),
    "ev_ebitda":        (-500., 1000.),
    "pe_ratio":         (0.0,   2000.),
    "peg_ratio":        (-50.0, 200.0),
    "beta":             (-3.0,  10.0),
    "rsi_14":           (0.0,   100.0),
}

# ── Score-metric conflict rules ────────────────────────────────────────

def _pct(v: Any) -> float | None:
    """Parse a value that may be in decimal (0.35) or percentage (35.0) form.
    We normalise everything to decimal."""
    if v is None:
        return None
    try:
        f = float(str(v).replace("%", "").strip())
        # Heuristic: if |value| > 2 it's likely already in %, convert to decimal
        if abs(f) > 2.0:
            return f / 100.0
        return f
    except (ValueError, TypeError):
        return None


def _parse_raw(cell: str | None) -> float | None:
    """Parse CSV raw_ cell like '35.5% [yf]' or '1.87 [mk]' into float."""
    if not cell or str(cell).startswith("n/a"):
        return None
    try:
        token = str(cell).split()[0].replace("%", "")
        return float(token)
    except (ValueError, IndexError):
        return None


def _source_tag(cell: str | None) -> str:
    """Extract [yf]/[mk]/[--] tag from raw_ cell."""
    if not cell:
        return "[--]"
    parts = str(cell).split()
    return parts[-1] if len(parts) > 1 else "[--]"


def check_ranges(ticker: str, metrics: dict[str, float | None]) -> list[str]:
    """Return list of anomaly strings for out-of-range values."""
    anomalies: list[str] = []
    for field, (lo, hi) in RANGES.items():
        v = metrics.get(field)
        if v is None:
            continue
        if not (lo <= v <= hi):
            anomalies.append(
                f"[RANGE] {field}={v:.4g} outside [{lo},{hi}]"
            )
    return anomalies


def check_score_conflicts(csv_row: dict) -> list[str]:
    """
    Detect logical conflicts between dimension scores and underlying raw metrics.
    csv_row keys come from our enhanced CSV columns.
    """
    conflicts: list[str] = []

    def score(col_fragment: str) -> float | None:
        for k, v in csv_row.items():
            if k.startswith(col_fragment):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    return None
        return None

    def raw(col_fragment: str) -> float | None:
        for k, v in csv_row.items():
            if k.startswith("raw_") and col_fragment in k:
                return _parse_raw(v)
        return None

    val_score    = score("val_")
    grw_score    = score("grw_")
    qlt_score    = score("qlt_")
    ai_score     = score("ai_")

    ev_sales     = raw("ev_sales")
    rev_growth   = raw("rev_growth")         # % in CSV (e.g. 35.0)
    gross_margin = raw("gross_margin")       # % in CSV (e.g. 74.1)
    fcf_margin   = raw("fcf_margin")         # % in CSV
    roic         = raw("roic")               # % in CSV

    # Convert % columns back to decimal for comparison
    if rev_growth  is not None and abs(rev_growth)  > 2: rev_growth   /= 100
    if gross_margin is not None and abs(gross_margin) > 2: gross_margin /= 100
    if fcf_margin   is not None and abs(fcf_margin)   > 2: fcf_margin   /= 100
    if roic         is not None and abs(roic)         > 2: roic         /= 100

    # Valuation conflict: high val score but very expensive EV/S
    if val_score is not None and val_score > 90 and ev_sales is not None and ev_sales > 30:
        conflicts.append(
            f"[VAL-CONFLICT] val_score={val_score:.1f}>90 but EV/Sales={ev_sales:.1f}>30"
        )

    # Growth conflict: high growth score but low actual growth
    if grw_score is not None and grw_score > 90 and rev_growth is not None and rev_growth < 0.10:
        conflicts.append(
            f"[GRW-CONFLICT] grw_score={grw_score:.1f}>90 but rev_growth={rev_growth*100:.1f}%<10%"
        )

    # Quality conflict: high quality score but negative margins
    neg_margins = sum([
        1 for m in [gross_margin, fcf_margin, roic]
        if m is not None and m < 0
    ])
    if qlt_score is not None and qlt_score > 85 and neg_margins >= 2:
        conflicts.append(
            f"[QLT-CONFLICT] qlt_score={qlt_score:.1f}>85 but {neg_margins}/3 margins are negative"
        )

    return conflicts


def cross_source_check(
    ticker: str,
    csv_row: dict,
    fmp_ratios: dict,
    fmp_income: dict,
    finnhub_metrics: dict,
    sec_financials: dict,
) -> list[str]:
    """
    Compare key metrics across sources. Flag discrepancies > threshold.
    Returns list of conflict strings.
    """
    conflicts: list[str] = []

    def _csv_raw(frag: str) -> float | None:
        for k, v in csv_row.items():
            if k.startswith("raw_") and frag in k:
                return _parse_raw(v)
        return None

    def _check(field_label: str, csv_val: float | None, ext_val: float | None,
               source: str, threshold: float = 0.15, is_pct: bool = False):
        if csv_val is None or ext_val is None:
            return
        if is_pct:
            # ext source may be in % (e.g. Finnhub grossMarginTTM = 75.3)
            if abs(ext_val) > 2:
                ext_val /= 100
            if abs(csv_val) > 2:
                csv_val /= 100
        diff = abs(csv_val - ext_val)
        if diff > threshold:
            conflicts.append(
                f"[CROSS-SRC] {field_label}: CSV={csv_val:.3g} vs {source}={ext_val:.3g} "
                f"(diff={diff:.3g})"
            )

    csv_gm  = _csv_raw("gross_margin")
    csv_rg  = _csv_raw("rev_growth")
    csv_fcfm = _csv_raw("fcf_margin")

    # vs FMP
    if fmp_income:
        _check("gross_margin", csv_gm, fmp_income.get("gross_margin_calc"), "FMP", 0.08, True)
        _check("rev_growth",   csv_rg, fmp_income.get("revenue_growth_yoy"), "FMP", 0.10, True)
    if fmp_ratios:
        _check("gross_margin", csv_gm, fmp_ratios.get("gross_margin"), "FMP-ratios", 0.08, True)

    # vs Finnhub (metrics already in %)
    if finnhub_metrics:
        _check("gross_margin", csv_gm, finnhub_metrics.get("gross_margin"), "Finnhub", 0.08, True)
        _check("rev_growth",   csv_rg, finnhub_metrics.get("revenue_growth_yoy"), "Finnhub", 0.10, True)

    # vs SEC XBRL (gross_margin_sec is decimal 0-1; csv_gm is %; is_pct=True normalises both)
    # Wider threshold (0.15) for SEC because XBRL period mismatches are common.
    # Skip entirely for tickers where GrossProfit XBRL is known to be unreliable.
    if sec_financials and ticker not in _KNOWN_BAD_SEC_MARGINS:
        _check("gross_margin", csv_gm, sec_financials.get("gross_margin_sec"), "SEC", 0.15, True)

    return conflicts


# Tickers where SEC XBRL GrossProfit concept returns unreliable data.
# These companies either use non-standard XBRL tags or their P&L structure
# causes the fetcher to pick up the wrong line item.
_KNOWN_BAD_SEC_MARGINS: set[str] = {
    "NFLX",   # streaming P&L has no standard GrossProfit XBRL tag
    "KLAC",   # equipment co — SEC GrossProfit tag picks up wrong line
    "LRCX",   # same — SEC returns ~3% vs actual ~47%
    "AMZN",   # segment P&L causes GrossProfit to be product-only (~0.8%)
    "HON",    # Honeywell conglomerate — XBRL returns ~9.5% vs actual ~36%
    "GE",     # GE Aerospace — complex segment P&L, SEC returns ~15% vs ~31%
}


def check_ai_exposure_suspicious(
    ticker: str,
    csv_row: dict,
    ai_keyword_count: int,
) -> str | None:
    """
    Flag if ai_exposure score is high but company description has few AI keywords.
    Returns warning string or None.
    """
    ai_score = None
    for k, v in csv_row.items():
        if k.startswith("ai_AI暴露") or k == "ai_exposure":
            try:
                ai_score = float(v)
                break
            except (ValueError, TypeError):
                pass

    if ai_score is None or ai_score <= 75:
        return None
    if ai_keyword_count >= 3:
        return None   # description confirms AI involvement

    return (
        f"[AI-SUSPICIOUS] ai_exposure={ai_score:.1f}>75 "
        f"but only {ai_keyword_count} AI keywords found in description"
    )


def validate(
    ticker: str,
    csv_row: dict,
    fmp_profile: dict,
    fmp_ratios: dict,
    fmp_income: dict,
    finnhub_metrics: dict,
    sec_financials: dict,
    ai_keyword_count: int,
) -> dict:
    """
    Full financial validation for one ticker.
    Returns dict with anomalies, conflicts, and raw_data_conflict bool.
    """
    all_notes: list[str] = []

    # 1. Range checks on CSV raw values
    csv_metrics = {
        "ev_sales":       _parse_raw(next((v for k, v in csv_row.items() if "ev_sales" in k), None)),
        "revenue_growth": _parse_raw(next((v for k, v in csv_row.items() if "rev_growth" in k), None)),
        "gross_margin":   _parse_raw(next((v for k, v in csv_row.items() if "gross_margin" in k and "raw_" in k), None)),
        "peg_ratio":      _parse_raw(next((v for k, v in csv_row.items() if "peg" in k and "raw_" in k), None)),
        "beta":           _parse_raw(next((v for k, v in csv_row.items() if "beta" in k and "raw_" in k), None)),
        "rsi_14":         _parse_raw(next((v for k, v in csv_row.items() if "rsi14" in k), None)),
    }
    # Pct-back conversion for margin fields
    for f in ["revenue_growth", "gross_margin"]:
        v = csv_metrics.get(f)
        if v is not None and abs(v) > 2:
            csv_metrics[f] = v / 100

    all_notes += check_ranges(ticker, csv_metrics)

    # 2. Score-metric conflicts
    all_notes += check_score_conflicts(csv_row)

    # 3. Cross-source comparison
    all_notes += cross_source_check(
        ticker, csv_row, fmp_ratios, fmp_income, finnhub_metrics, sec_financials
    )

    # 4. AI exposure check — skip when no FMP description to scan keywords against
    has_description = bool(fmp_profile.get("description", "").strip())
    ai_warn = check_ai_exposure_suspicious(ticker, csv_row, ai_keyword_count) if has_description else None
    if ai_warn:
        all_notes.append(ai_warn)

    raw_data_conflict = any(
        n.startswith("[CROSS-SRC]") or n.startswith("[RANGE]")
        for n in all_notes
    )

    return {
        "raw_data_conflict": raw_data_conflict,
        "anomaly_count":     len(all_notes),
        "notes":             all_notes,
    }
