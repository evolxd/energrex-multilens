"""
Score recalculation validator
==============================
Re-runs the full quant_engine scoring pipeline — with live yfinance momentum
data injected — and compares against the CSV output.

Uses the same compute_base_score / compute_final_score from scoring/formula.py
as the main scoring engine, so formula drift is impossible.

Returns graded diff level instead of binary formula_mismatch:
  minor_diff     abs < 0.5   — rounding noise, not a real issue
  review_low     0.5–2.0     — small divergence
  review_medium  2.0–5.0     — investigate
  review_high    >= 5.0      — likely formula error

formula_mismatch = True only when diff_level != "minor_diff"
"""

from __future__ import annotations
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Path setup ──────────────────────────────────────────────────────────
_ROOT    = Path(__file__).parent.parent.parent   # ai_valuation/
_SCORING = _ROOT / "scoring"
for _p in [str(_ROOT), str(_SCORING)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Lazy engine + formula imports ───────────────────────────────────────
_ENGINE_LOADED = False
_merge_data    = None
_clean_data    = None
_score_ticker  = None
_classify_diff = None
_explain_diff  = None
_DIM_WEIGHTS   = None


def _load_engine() -> bool:
    global _ENGINE_LOADED, _merge_data, _clean_data, _score_ticker
    global _classify_diff, _explain_diff, _DIM_WEIGHTS
    if _ENGINE_LOADED:
        return True
    try:
        from quant_data import QUANT_META, QUANT_STANDALONE, QUANT_AI_EXPOSURE
        from mock_data   import MOCK_STOCKS
        from quant_engine import score_ticker as _st
        from formula import classify_diff, explain_diff, DIM_WEIGHTS

        _DIM_WEIGHTS = DIM_WEIGHTS

        def _merge(ticker: str) -> dict:
            if ticker in MOCK_STOCKS:
                base = dict(MOCK_STOCKS[ticker])
            elif ticker in QUANT_STANDALONE:
                base = dict(QUANT_STANDALONE[ticker])
            else:
                base = {}
            for k, v in QUANT_META.get(ticker, {}).items():
                base.setdefault(k, v)
            for k, v in QUANT_AI_EXPOSURE.get(ticker, {}).items():
                if base.get(k) is None:
                    base[k] = v
            base.setdefault("sector_tag", "Hardware")
            base.setdefault("_bad_fields", [])
            return base

        def _clean(data: dict) -> dict:
            return {k: v for k, v in data.items() if not k.startswith("_")}

        _merge_data    = _merge
        _clean_data    = _clean
        _score_ticker  = _st
        _classify_diff = classify_diff
        _explain_diff  = explain_diff
        _ENGINE_LOADED = True
        return True
    except Exception as e:
        logger.warning("score_validator: could not load quant engine: %s", e)
        return False


# ── Live momentum field names to inject ─────────────────────────────────
# These come from validation/fetchers/yf_fetcher.calc_momentum()
# and are exactly what quant_engine.score_momentum() consumes.
_LIVE_MOMENTUM_FIELDS = ("rsi_14", "price_vs_200dma", "max_drawdown_1y")

# ── CSV raw_ column fragment → (quant_engine_field, pct_to_decimal) ──────
# pct_to_decimal=True: CSV stores "85.2% [yf]" → inject as 0.852
# pct_to_decimal=False: CSV stores "18.97 [yf]" → inject as-is
_CSV_RAW_MAP: list[tuple[str, str, bool]] = [
    ("peg",           "peg_ratio",                    False),
    ("ev_sales",      "ev_sales",                     False),
    ("forward_pe",    "forward_pe",                   False),
    ("fcf_yield",     "fcf_yield",                    True),
    ("rev_growth",    "revenue_growth_yoy",           True),
    ("eps_growth",    "eps_growth_yoy",               True),
    ("fwd_rev_guide", "next_year_revenue_growth_est", True),
    ("gross_margin",  "gross_margin",                 True),
    ("fcf_margin",    "fcf_margin",                   True),
    ("roic",          "roic",                         True),
    ("de_ratio",      "de_ratio",                     False),
    ("nrr",           "nrr",                          False),
    ("rsi14",         "rsi_14",                       False),
    ("vs200ma",       "price_vs_200dma",              True),
    ("beta",          "beta",                         False),
    ("max_dd_1y",     "max_drawdown_1y",              True),
]


def _parse_raw_cell(cell: str | None) -> float | None:
    """Parse CSV raw_ cell like '35.5% [yf]' or '1.87 [mk]' into float; returns None for n/a."""
    if not cell or str(cell).strip().startswith("n/a"):
        return None
    try:
        token = str(cell).split()[0].replace("%", "")
        return float(token)
    except (ValueError, IndexError):
        return None


def extract_csv_live_fields(csv_row: dict) -> dict:
    """
    Extract all raw_ columns from a CSV row and return a dict of
    quant_engine field names → decimal values, ready to inject into
    recalculate().  Percentage fields are divided by 100.
    """
    result: dict = {}
    for frag, engine_field, pct_to_dec in _CSV_RAW_MAP:
        cell = next(
            (v for k, v in csv_row.items() if "raw_" in k and frag in k.lower()),
            None,
        )
        val = _parse_raw_cell(cell)
        if val is None:
            continue
        result[engine_field] = val / 100.0 if pct_to_dec else val
    return result


def recalculate(
    ticker:          str,
    live_momentum:   dict | None = None,
    live_csv_fields: dict | None = None,
) -> dict | None:
    """
    Re-score ticker using mock data, optionally with live yfinance fields injected.

    Args:
        ticker:        ticker symbol
        live_momentum: dict from yf_fetcher.calc_momentum() — if provided,
                       its rsi_14 / price_vs_200dma / max_drawdown_1y override
                       the mock values so momentum matches what the CSV used.

    Returns ScoreResult as dict, or None if engine unavailable.
    """
    if not _load_engine():
        return None
    try:
        raw = _merge_data(ticker)

        # Inject all live CSV fields first (lower priority than live_momentum)
        if live_csv_fields:
            for field, val in live_csv_fields.items():
                raw[field] = val

        # Inject live momentum fields — overrides csv fields (fresher snapshot)
        if live_momentum:
            for field in _LIVE_MOMENTUM_FIELDS:
                if field in live_momentum:
                    raw[field] = live_momentum[field]

        data   = _clean_data(raw)
        if not data:
            return None
        result = _score_ticker(ticker, data)

        return {
            "base_score":   round(result.base_score,       2),
            "dynamic_adj":  round(result.dynamic_adjustment, 2),
            "final_score":  round(result.final_score,      2),
            "valuation":    round(result.dim_scores.get("valuation",       50.0), 2),
            "growth":       round(result.dim_scores.get("growth",          50.0), 2),
            "quality":      round(result.dim_scores.get("quality",         50.0), 2),
            "ai_exposure":  round(result.dim_scores.get("ai_exposure",     50.0), 2),
            "exp_gap":      round(result.dim_scores.get("expectation_gap", 50.0), 2),
            "momentum":     round(result.dim_scores.get("momentum",        50.0), 2),
            "risk_penalty": round(result.risk_penalty, 2),
            "rating":       result.rating,
            "circuit":      result.circuit_triggered,
            "sector":       result.sector,
        }
    except Exception as e:
        logger.warning("score_validator recalculate %s: %s", ticker, e)
        return None


def validate(
    ticker:        str,
    csv_row:       dict,
    live_momentum: dict | None = None,
) -> dict:
    """
    Compare CSV scores against freshly recalculated scores.

    Args:
        ticker:        ticker symbol
        csv_row:       row dict from results.csv
        live_momentum: dict from yf_fetcher.calc_momentum() — injected into
                       recalculation so momentum matches the live CSV run.

    Returns:
        base_score_recalculated           float | None
        dynamic_adjustment_recalculated   float | None
        final_score_recalculated          float | None
        formula_diff_abs                  float | None
        formula_diff_level                str   ("minor_diff" | "review_low" | ...)
        formula_diff_reason               str   (reason code)
        formula_mismatch                  bool  (True when diff_level != "minor_diff")
        dim_deltas                        dict  {dim: csv_val - recalc_val}
        notes                             list[str]
    """
    if not _load_engine():
        return _unavailable_result()

    notes: list[str] = []

    # ── Extract CSV final score ──────────────────────────────────────────
    csv_final = _get_csv_score(csv_row, "final_")

    # ── Count live fields used in CSV run ────────────────────────────────
    live_count_key = next((k for k in csv_row if "live_fields" in k), None)
    live_count     = int(csv_row.get(live_count_key, 0) or 0) if live_count_key else 0

    # ── Recalculate with all CSV live fields injected ────────────────────
    live_csv = extract_csv_live_fields(csv_row)
    recalc = recalculate(ticker, live_momentum=live_momentum, live_csv_fields=live_csv)
    if recalc is None:
        return _unavailable_result("recalculate() returned None")

    recalc_final = recalc["final_score"]

    # ── Dimension deltas ────────────────────────────────────────────────
    _dim_map = {
        "val_":  "valuation",
        "grw_":  "growth",
        "qlt_":  "quality",
        "ai_":   "ai_exposure",
        "exp_":  "exp_gap",
        "mom_":  "momentum",
    }
    deltas: dict[str, float] = {}
    for col_frag, dim_key in _dim_map.items():
        csv_v    = _get_csv_score(csv_row, col_frag)
        recalc_v = recalc.get(dim_key)
        if csv_v is not None and recalc_v is not None:
            d = round(csv_v - recalc_v, 2)
            if abs(d) > 0.5:
                deltas[dim_key] = d
                notes.append(
                    f"[DIM-DELTA] {dim_key}: CSV={csv_v:.2f} vs recalc={recalc_v:.2f} (Δ={d:+.2f})"
                )

    # ── Classify diff ───────────────────────────────────────────────────
    if csv_final is not None:
        abs_diff   = round(abs(csv_final - recalc_final), 3)
        diff_level  = _classify_diff(abs_diff)
        diff_reason = _explain_diff(deltas, live_count)

        # For both live_data_drift and rounding_difference (accumulated small
        # per-dim diffs from fields not in CSV raw_ columns), only flag as
        # formula_mismatch when diff >= 5.0 — below that, the diff is
        # explainable by data staleness or missing AI/expectation fields.
        if diff_reason in ("live_data_drift", "rounding_difference") and live_count >= 5:
            mismatch = abs_diff >= 5.0
        else:
            mismatch = diff_level != "minor_diff"

        if mismatch:
            notes.append(
                f"[FORMULA-MISMATCH] level={diff_level} reason={diff_reason} "
                f"CSV={csv_final:.2f} recalc={recalc_final:.2f} (Δ={csv_final - recalc_final:+.2f})"
            )
        elif diff_reason == "live_data_drift" and abs_diff >= 0.5:
            notes.append(
                f"[LIVE-DRIFT-OK] final diff={abs_diff:.2f} within live-data tolerance, "
                f"not a formula error"
            )
    else:
        abs_diff    = None
        diff_level  = "minor_diff"
        diff_reason = "rounding_difference"
        mismatch    = False

    return {
        "base_score_recalculated":         recalc["base_score"],
        "dynamic_adjustment_recalculated": recalc["dynamic_adj"],
        "final_score_recalculated":        recalc_final,
        "formula_diff_abs":                abs_diff,
        "formula_diff_level":              diff_level,
        "formula_diff_reason":             diff_reason,
        "formula_mismatch":                mismatch,
        "dim_deltas":                      deltas,
        "notes":                           notes,
    }


# ── Helpers ──────────────────────────────────────────────────────────────

def _get_csv_score(row: dict, col_fragment: str) -> float | None:
    for k, v in row.items():
        if col_fragment in k:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return None


def _unavailable_result(msg: str = "quant engine unavailable, skipping recalculation") -> dict:
    return {
        "base_score_recalculated":         None,
        "dynamic_adjustment_recalculated": None,
        "final_score_recalculated":        None,
        "formula_diff_abs":                None,
        "formula_diff_level":              "minor_diff",
        "formula_diff_reason":             "unknown",
        "formula_mismatch":                False,
        "dim_deltas":                      {},
        "notes":                           [f"score_validator: {msg}"],
    }
