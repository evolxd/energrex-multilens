"""
Unified Scoring Formulas — Single Source of Truth
===================================================
Both quant_engine.py and validation/validators/score_validator.py
must import scoring constants and functions from this module.
Never duplicate these formulas elsewhere.

Three-layer architecture:
  Layer 1: base_score          — static weighted dims minus risk, circuit applied
  Layer 2: dynamic_adjustment  — currently 0; reserved for future live signals
  Layer 3: final_score         = base_score + dynamic_adjustment  (clamped 0-100)
"""

from __future__ import annotations

# ── Circuit breaker constants ────────────────────────────────────────────
CIRCUIT_BETA_THRESH      = 2.2
CIRCUIT_DRAWDOWN_THRESH  = 0.35   # abs magnitude of max_drawdown_1y
CIRCUIT_DE_THRESH        = 1.80
CIRCUIT_MULTIPLIER       = 0.75

RISK_MAX_PENALTY         = 20.0   # penalty points = raw_risk_0_to_1 × 20

# ── Dimension weights (must sum to 1.00) ─────────────────────────────────
DIM_WEIGHTS: dict[str, float] = {
    "valuation":       0.20,
    "growth":          0.25,
    "quality":         0.15,
    "ai_exposure":     0.20,
    "expectation_gap": 0.10,
    "momentum":        0.10,
}


# ── Layer 1 ──────────────────────────────────────────────────────────────

def compute_base_score(
    dim_scores: dict[str, float],
    risk_penalty: float,
    circuit_triggered: bool,
) -> float:
    """
    Static base score.

    = clamp( (weighted_dim_sum - risk_penalty) × circuit_mult, 0, 100 )

    Args:
        dim_scores:        {dim_key: score_0_to_100}
        risk_penalty:      penalty points (0–20), already computed
        circuit_triggered: whether circuit breaker fired
    """
    weighted_sum = sum(
        dim_scores.get(dim, 50.0) * w for dim, w in DIM_WEIGHTS.items()
    )
    mult = CIRCUIT_MULTIPLIER if circuit_triggered else 1.0
    return max(0.0, min(100.0, round((weighted_sum - risk_penalty) * mult, 4)))


# ── Layer 2 ──────────────────────────────────────────────────────────────

def compute_dynamic_adjustment(data: dict | None = None) -> float:
    """
    Dynamic adjustment layer.
    Currently 0 — reserved for future real-time signals (insider buying,
    sentiment overlay, earnings-day revision, etc.).
    """
    return 0.0


# ── Layer 3 ──────────────────────────────────────────────────────────────

def compute_final_score(base_score: float, dynamic_adj: float) -> float:
    """final_score = clamp(base_score + dynamic_adj, 0, 100)"""
    return max(0.0, min(100.0, round(base_score + dynamic_adj, 2)))


# ── Graded mismatch classification ───────────────────────────────────────

def classify_diff(abs_diff: float) -> str:
    """
    Classify absolute difference between CSV score and recalculated score.

    Returns:
      "minor_diff"    abs < 0.5  — rounding or trivial fp noise
      "review_low"    0.5–2.0    — small data or formula divergence
      "review_medium" 2.0–5.0    — meaningful divergence, investigate
      "review_high"   >= 5.0     — serious mismatch, likely formula error
    """
    if abs_diff < 0.5:  return "minor_diff"
    if abs_diff < 2.0:  return "review_low"
    if abs_diff < 5.0:  return "review_medium"
    return "review_high"


# ── Diff reason codes ─────────────────────────────────────────────────────

def explain_diff(
    dim_deltas: dict[str, float],
    live_count: int = 0,
    missing_fields: list[str] | None = None,
) -> str:
    """
    Infer the most likely reason for the observed score difference.

    Returns one of:
      rounding_difference         trivially small delta
      live_data_drift             live yfinance momentum fields used
      null_or_zero_handling       missing fields drove neutral defaults
      field_mapping_error         single dim has outsized delta
      data_input_changed          multiple dims diverged simultaneously
      formula_version_mismatch    formula changed but CSV not re-run
      unknown
    """
    if missing_fields is None:
        missing_fields = []

    total_delta = sum(abs(v) for v in dim_deltas.values())

    if total_delta < 0.5:
        return "rounding_difference"

    # Momentum dimension is the primary live-data field
    mom_delta = abs(dim_deltas.get("momentum", 0))
    if live_count > 0 and mom_delta > 1.0:
        return "live_data_drift"

    # CSV was generated with substantial live data; any multi-dim divergence
    # is expected staleness, not a formula error.
    if live_count >= 5 and len(dim_deltas) >= 1:
        return "live_data_drift"

    if missing_fields:
        return "null_or_zero_handling"

    if len(dim_deltas) == 1:
        return "field_mapping_error"

    if len(dim_deltas) >= 2:
        return "data_input_changed"

    return "unknown"
