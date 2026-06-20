"""
Quant Multi-Factor Scoring Engine
==================================
Six-dimension weighted scoring with sector-specific baselines,
de-duplicated quality factors, forward-ERG smoothing, momentum,
and a non-linear risk circuit breaker.

Dimensions:
  ① Valuation       20% — PEG, EV/EBITDA, ERG(fwd), ForwardPE, FCFYield
  ② Growth          25% — RevYoY, EPSYoY, FCFGrowth, NTM Guidance
  ③ Quality         15% — GrossMargin, FCFMargin, ROIC, D/E (no RevGrowth re-use)
  ④ AI Exposure     20% — sector-specific intensity fields
  ⑤ Expectation Gap 10% — rev/eps beat, guidance beat, earnings reaction
  ⑥ Momentum        10% — Price vs 200DMA, RSI-14 piecewise

Risk Penalty (subtracted, max 20 pts):
  Beta × 25% + Vol30d × 20% + ValuationRisk × 25% + Liquidity × 15% + MaxDD × 15%
  → RawRisk * 20 = penalty

Circuit Breaker:
  IF (beta > 2.2 AND max_drawdown_abs > 0.35) OR de_ratio > 1.8:
      FINAL *= 0.75, rating capped at Watchlist
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from formula import (
    DIM_WEIGHTS,
    CIRCUIT_BETA_THRESH, CIRCUIT_DRAWDOWN_THRESH,
    CIRCUIT_DE_THRESH, CIRCUIT_MULTIPLIER, RISK_MAX_PENALTY,
    compute_base_score, compute_dynamic_adjustment, compute_final_score,
)
from scoring_engine import calc_wacc, get_category


# ─────────────────────────────────────────────────────────────────────
# A. Sector-Specific Baselines
# ─────────────────────────────────────────────────────────────────────
SECTOR_BASELINES: dict[str, dict[str, dict]] = {
    "Hardware": {
        # ── ① Valuation ──────────────────────────────────────────────
        "peg":            {"best": 0.50,  "worst": 3.50,  "dir": "negative"},
        "ev_ebitda":      {"best": 10.0,  "worst": 55.0,  "dir": "negative"},
        "erg":            {"best": 0.10,  "worst": 2.50,  "dir": "negative"},
        "forward_pe":     {"best": 12.0,  "worst": 45.0,  "dir": "negative"},
        "fcf_yield":      {"best": 0.050, "worst": 0.005, "dir": "positive"},
        # ── ② Growth ────────────────────────────────────────────────
        "rev_growth":     {"best": 0.60,  "worst": -0.05, "dir": "positive"},
        "eps_growth":     {"best": 1.00,  "worst": -0.20, "dir": "positive"},
        "fcf_growth":     {"best": 0.80,  "worst": -0.20, "dir": "positive"},
        "ntm_guidance":   {"best": 0.40,  "worst": -0.05, "dir": "positive"},
        # ── ③ Quality (de-dup: no rev_growth) ───────────────────────
        "gross_margin":   {"best": 0.70,  "worst": 0.40,  "dir": "positive"},
        "fcf_margin":     {"best": 0.30,  "worst": 0.00,  "dir": "positive"},
        "roic":           {"best": 0.25,  "worst": -0.05, "dir": "positive"},  # Damodaran: ROIC-WACC 超额回报
        "de_ratio":       {"best": 0.00,  "worst": 2.50,  "dir": "negative"},
        "capex_rev":      {"best": 0.05,  "worst": 0.25,  "dir": "negative"},
        # ── ④ AI Exposure ────────────────────────────────────────────
        "ai_rev_pct":     {"best": 0.90,  "worst": 0.10,  "dir": "positive"},
        "datacenter_pct": {"best": 0.90,  "worst": 0.15,  "dir": "positive"},
        "ai_backlog":     {"best": 0.90,  "worst": 0.15,  "dir": "positive"},
        "adv_pkg_pct":    {"best": 0.60,  "worst": 0.02,  "dir": "positive"},
    },
    "SaaS": {
        # ── ① Valuation ──────────────────────────────────────────────
        "peg":            {"best": 1.00,  "worst": 7.00,  "dir": "negative"},
        "ev_ebitda":      {"best": 20.0,  "worst": 160.0, "dir": "negative"},
        "erg":            {"best": 0.15,  "worst": 3.50,  "dir": "negative"},
        "forward_pe":     {"best": 20.0,  "worst": 90.0,  "dir": "negative"},
        "fcf_yield":      {"best": 0.030, "worst": 0.002, "dir": "positive"},
        # ── ② Growth ────────────────────────────────────────────────
        "rev_growth":     {"best": 0.40,  "worst": 0.00,  "dir": "positive"},
        "eps_growth":     {"best": 0.60,  "worst": -0.20, "dir": "positive"},
        "fcf_growth":     {"best": 0.50,  "worst": -0.20, "dir": "positive"},
        "ntm_guidance":   {"best": 0.30,  "worst": -0.05, "dir": "positive"},
        # ── ③ Quality (de-dup: no rev_growth) ───────────────────────
        "gross_margin":   {"best": 0.85,  "worst": 0.60,  "dir": "positive"},
        "fcf_margin":     {"best": 0.35,  "worst": -0.10, "dir": "positive"},
        "roic":           {"best": 0.20,  "worst": -0.15, "dir": "positive"},  # Damodaran: ROIC-WACC 超额回报
        "de_ratio":       {"best": 0.00,  "worst": 2.50,  "dir": "negative"},
        "nrr":            {"best": 1.30,  "worst": 1.00,  "dir": "positive"},
        # ── ④ AI Exposure ────────────────────────────────────────────
        "ai_rev_pct":     {"best": 0.80,  "worst": 0.05,  "dir": "positive"},
        "ai_platform_pct":{"best": 0.80,  "worst": 0.05,  "dir": "positive"},
        "ai_backlog":     {"best": 0.80,  "worst": 0.05,  "dir": "positive"},
        "arr_growth":     {"best": 0.40,  "worst": 0.00,  "dir": "positive"},
    },
    "Cybersecurity": {
        # ── ① Valuation ──────────────────────────────────────────────
        "peg":            {"best": 1.50,  "worst": 6.00,  "dir": "negative"},
        "ev_ebitda":      {"best": 25.0,  "worst": 120.0, "dir": "negative"},
        "erg":            {"best": 0.20,  "worst": 4.00,  "dir": "negative"},
        "forward_pe":     {"best": 25.0,  "worst": 70.0,  "dir": "negative"},
        "fcf_yield":      {"best": 0.025, "worst": 0.003, "dir": "positive"},
        # ── ② Growth ────────────────────────────────────────────────
        "rev_growth":     {"best": 0.35,  "worst": 0.05,  "dir": "positive"},
        "eps_growth":     {"best": 0.50,  "worst": -0.20, "dir": "positive"},
        "fcf_growth":     {"best": 0.40,  "worst": -0.20, "dir": "positive"},
        "ntm_guidance":   {"best": 0.25,  "worst": 0.00,  "dir": "positive"},
        # ── ③ Quality (de-dup: no rev_growth) ───────────────────────
        "gross_margin":   {"best": 0.82,  "worst": 0.65,  "dir": "positive"},
        "fcf_margin":     {"best": 0.30,  "worst": -0.05, "dir": "positive"},
        "roic":           {"best": 0.15,  "worst": -0.10, "dir": "positive"},  # Damodaran: ROIC-WACC 超额回报
        "de_ratio":       {"best": 0.00,  "worst": 2.50,  "dir": "negative"},
        "nrr":            {"best": 1.20,  "worst": 1.05,  "dir": "positive"},
        # ── ④ AI Exposure ────────────────────────────────────────────
        "ai_rev_pct":     {"best": 0.60,  "worst": 0.05,  "dir": "positive"},
        "cyber_ai_pct":   {"best": 0.60,  "worst": 0.05,  "dir": "positive"},
        "ai_backlog":     {"best": 0.70,  "worst": 0.05,  "dir": "positive"},
        "arr_growth":     {"best": 0.30,  "worst": 0.00,  "dir": "positive"},
    },
}

# DIM_WEIGHTS, CIRCUIT_*, RISK_MAX_PENALTY → imported from formula.py


# ─────────────────────────────────────────────────────────────────────
# Data classes for audit trail
# ─────────────────────────────────────────────────────────────────────
@dataclass
class AuditEntry:
    field_name: str
    raw_value:  Any
    formula:    str
    score:      float
    weight:     float        # weight within this dimension (0–1)
    missing:    bool = False
    note:       str  = ""


@dataclass
class AuditDimension:
    label:       str          # e.g. "① VALUATION"
    key:         str          # e.g. "valuation"
    entries:     list[AuditEntry] = field(default_factory=list)
    dim_score:   float = 0.0
    final_weight: float = 0.0  # DIM_WEIGHTS value
    contribution: float = 0.0  # dim_score * final_weight


@dataclass
class ScoreResult:
    ticker:              str
    sector:              str
    company_name:        str
    dim_scores:          dict[str, float]
    audit_dims:          list[AuditDimension]
    risk_penalty:        float
    circuit_triggered:   bool
    circuit_reason:      str
    risk_multiplier:     float
    raw_sum:             float    # weighted dim sum before penalty & circuit
    base_score:          float    # Layer 1: (raw_sum - penalty) × circuit_mult, clamped
    dynamic_adjustment:  float    # Layer 2: currently 0, reserved
    final_score:         float    # Layer 3: base_score + dynamic_adjustment
    rating:              str
    bad_fields:          list[str]


# ─────────────────────────────────────────────────────────────────────
# B. Core Normalization with hard cap/floor
# ─────────────────────────────────────────────────────────────────────

def normalize_score(
    value: float,
    best: float,
    worst: float,
    direction: str = "positive",
) -> float:
    """
    Linear interval mapping capped to [0.0, 100.0].

    positive: score = (value - worst) / (best - worst) * 100
    negative: score = (worst - value) / (worst - best) * 100

    Scores > 100 → 100.0 (cap)
    Scores < 0   → 0.0   (floor)
    """
    if best == worst:
        return 50.0
    if direction == "positive":
        raw = (value - worst) / (best - worst) * 100.0
    else:
        raw = (worst - value) / (worst - best) * 100.0
    return max(0.0, min(100.0, raw))


def _fmt_raw(v: Any) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:.1f}"
        if abs(v) >= 1:
            return f"{v:.4f}"
        return f"{v:.4f}"
    return str(v)


def _b(baselines: dict, key: str) -> dict:
    """Safe baseline lookup; returns neutral defaults if key missing."""
    return baselines.get(key, {"best": 1.0, "worst": 0.0, "dir": "positive"})


# ─────────────────────────────────────────────────────────────────────
# E. Momentum helpers
# ─────────────────────────────────────────────────────────────────────

def _score_rsi(rsi: float) -> tuple[float, str]:
    """
    Piecewise RSI scoring:
      RSI [45, 65]  → 80–100  optimal uptrend, not overbought
      RSI [30, 45)  → 40–80   mildly oversold
      RSI (65, 80)  → 20–80   approaching overbought
      RSI > 80      → 5       extreme overbought — fade risk
      RSI < 30      → 10–40   extreme oversold — downtrend risk
    """
    if rsi > 80:
        score = 5.0
        note = f"RSI {rsi:.1f} > 80 → extreme overbought → {score:.1f}"
    elif rsi > 65:
        score = 80.0 - (rsi - 65.0) / 15.0 * 60.0
        score = max(20.0, score)
        note = f"RSI {rsi:.1f} in (65,80) → elevated → {score:.1f}"
    elif rsi >= 45:
        score = 80.0 + (rsi - 45.0) / 20.0 * 20.0
        score = min(100.0, score)
        note = f"RSI {rsi:.1f} in [45,65] → optimal → {score:.1f}"
    elif rsi >= 30:
        score = 40.0 + (rsi - 30.0) / 15.0 * 40.0
        note = f"RSI {rsi:.1f} in [30,45) → oversold → {score:.1f}"
    else:
        score = 10.0 + rsi / 30.0 * 30.0
        note = f"RSI {rsi:.1f} < 30 → extreme oversold → {score:.1f}"
    return round(score, 1), note


# ─────────────────────────────────────────────────────────────────────
# Helpers: weighted combination with formula string
# ─────────────────────────────────────────────────────────────────────

def _weighted_dim(entries: list[AuditEntry]) -> tuple[float, str]:
    """Return (dim_score, formula_str) from a list of AuditEntry."""
    parts_vals: list[tuple[float, float, str]] = []
    total_w = 0.0
    for e in entries:
        if not e.missing:
            total_w += e.weight
            parts_vals.append((e.score, e.weight, e.field_name))

    if total_w == 0:
        return 50.0, "all fields missing → neutral 50"

    # Rescale weights to sum to 1 if some fields missing
    score = sum(s * (w / total_w) for s, w, _ in parts_vals)
    formula = " + ".join(
        f"{s:.1f}×{w/total_w:.2f}[{n}]" for s, w, n in parts_vals
    )
    return round(min(100.0, max(0.0, score)), 2), formula


# ─────────────────────────────────────────────────────────────────────
# D. ① Valuation Score
#    ERG uses Forward RevG to smooth single-quarter noise
# ─────────────────────────────────────────────────────────────────────

def score_valuation(data: dict, bl: dict) -> AuditDimension:
    dim = AuditDimension("① VALUATION", "valuation", final_weight=DIM_WEIGHTS["valuation"])
    b = lambda k: _b(bl, k)

    # PEG
    peg = data.get("peg_ratio")
    if peg is not None and peg > 0:
        cfg = b("peg")
        s = normalize_score(peg, cfg["best"], cfg["worst"], cfg["dir"])
        f = f"({cfg['worst']}-{peg:.4f})/({cfg['worst']}-{cfg['best']})*100"
        dim.entries.append(AuditEntry("PEG", peg, f, s, 0.25))
    else:
        dim.entries.append(AuditEntry("PEG", peg, "missing/invalid", 50.0, 0.25, missing=True))

    # EV/EBITDA
    ev_eb = data.get("ev_ebitda")
    if ev_eb is not None and ev_eb > 0:
        cfg = b("ev_ebitda")
        s = normalize_score(ev_eb, cfg["best"], cfg["worst"], cfg["dir"])
        f = f"({cfg['worst']}-{ev_eb:.2f})/({cfg['worst']}-{cfg['best']})*100"
        dim.entries.append(AuditEntry("EV/EBITDA", ev_eb, f, s, 0.20))
    else:
        dim.entries.append(AuditEntry("EV/EBITDA", ev_eb, "missing/invalid", 50.0, 0.20, missing=True))

    # ERG = EV/Rev ÷ Forward_RevG%  (D: forward metric to smooth noise)
    ev_rev = data.get("ev_sales") or data.get("ev_rev")
    fwd_g  = data.get("next_year_revenue_growth_est") or data.get("forward_rev_growth_est") or data.get("revenue_growth_yoy")
    if ev_rev is not None and fwd_g is not None and fwd_g > 0:
        erg = ev_rev / (fwd_g * 100.0)   # FwdRevG as percent integer
        cfg = b("erg")
        s = normalize_score(erg, cfg["best"], cfg["worst"], cfg["dir"])
        f = (f"EV/Rev({ev_rev:.2f})/FwdRevG({fwd_g*100:.1f}%)={erg:.4f} → "
             f"({cfg['worst']}-{erg:.4f})/({cfg['worst']}-{cfg['best']})*100")
        dim.entries.append(AuditEntry("ERG", erg, f, s, 0.30,
                                      note=f"EV/Rev={ev_rev:.2f}, FwdRevG={fwd_g*100:.1f}%"))
    else:
        dim.entries.append(AuditEntry("ERG", None, "EV/Rev or FwdRevG missing", 50.0, 0.30, missing=True))

    # Forward PE
    fpe = data.get("forward_pe")
    if fpe is not None and fpe > 0:
        cfg = b("forward_pe")
        s = normalize_score(fpe, cfg["best"], cfg["worst"], cfg["dir"])
        f = f"({cfg['worst']}-{fpe:.2f})/({cfg['worst']}-{cfg['best']})*100"
        dim.entries.append(AuditEntry("Forward PE", fpe, f, s, 0.15))
    else:
        dim.entries.append(AuditEntry("Forward PE", fpe, "missing/invalid", 50.0, 0.15, missing=True))

    # FCF Yield
    fcfy = data.get("fcf_yield")
    if fcfy is not None:
        cfg = b("fcf_yield")
        s = normalize_score(fcfy, cfg["best"], cfg["worst"], cfg["dir"])
        f = f"({fcfy*100:.2f}%-{cfg['worst']*100:.2f}%)/({cfg['best']*100:.2f}%-{cfg['worst']*100:.2f}%)*100"
        dim.entries.append(AuditEntry("FCF Yield", fcfy, f, s, 0.10,
                                      note=f"{fcfy*100:.2f}%"))
    else:
        dim.entries.append(AuditEntry("FCF Yield", None, "missing", 50.0, 0.10, missing=True))

    dim.dim_score, _ = _weighted_dim(dim.entries)
    dim.contribution = round(dim.dim_score * dim.final_weight, 2)
    return dim


# ─────────────────────────────────────────────────────────────────────
# ② Growth Score
# ─────────────────────────────────────────────────────────────────────

def score_growth(data: dict, bl: dict) -> AuditDimension:
    dim = AuditDimension("② GROWTH", "growth", final_weight=DIM_WEIGHTS["growth"])
    b = lambda k: _b(bl, k)

    for fname, field_key, w, label in [
        ("revenue_growth_yoy",          "rev_growth",   0.35, "Rev YoY"),
        ("eps_growth_yoy",               "eps_growth",   0.25, "EPS YoY"),
        ("fcf_growth_yoy",               "fcf_growth",   0.20, "FCF Growth"),
        ("next_year_revenue_growth_est", "ntm_guidance", 0.20, "NTM Guidance"),
    ]:
        v = data.get(fname)
        if v is not None:
            cfg = b(field_key)
            s = normalize_score(v, cfg["best"], cfg["worst"], cfg["dir"])
            if cfg["dir"] == "positive":
                f = f"({v*100:.1f}%-{cfg['worst']*100:.1f}%)/({cfg['best']*100:.1f}%-{cfg['worst']*100:.1f}%)*100"
            else:
                f = f"({cfg['worst']*100:.1f}%-{v*100:.1f}%)/({cfg['worst']*100:.1f}%-{cfg['best']*100:.1f}%)*100"
            dim.entries.append(AuditEntry(label, v, f, s, w, note=f"{v*100:.1f}%"))
        else:
            dim.entries.append(AuditEntry(label, None, "missing", 50.0, w, missing=True))

    dim.dim_score, _ = _weighted_dim(dim.entries)
    dim.contribution = round(dim.dim_score * dim.final_weight, 2)
    return dim


# ─────────────────────────────────────────────────────────────────────
# C. ③ Quality Score — de-duplicated (NO revenue growth reuse)
# ─────────────────────────────────────────────────────────────────────

def score_quality(data: dict, bl: dict, sector: str) -> AuditDimension:
    """
    Quality factors: GrossMargin, FCFMargin, ROIC, D/E.
    Hardware adds CapEx/Rev; SaaS adds NRR.
    Revenue growth is intentionally excluded (already in Growth score).
    """
    dim = AuditDimension("③ QUALITY", "quality", final_weight=DIM_WEIGHTS["quality"])
    b = lambda k: _b(bl, k)

    # Gross Margin
    gm = data.get("gross_margin")
    if gm is not None:
        cfg = b("gross_margin")
        s = normalize_score(gm, cfg["best"], cfg["worst"], cfg["dir"])
        f = f"({gm*100:.1f}%-{cfg['worst']*100:.0f}%)/({cfg['best']*100:.0f}%-{cfg['worst']*100:.0f}%)*100"
        dim.entries.append(AuditEntry("Gross Margin", gm, f, s, 0.30, note=f"{gm*100:.1f}%"))
    else:
        dim.entries.append(AuditEntry("Gross Margin", None, "missing", 50.0, 0.30, missing=True))

    # FCF Margin
    fm = data.get("fcf_margin")
    if fm is not None:
        cfg = b("fcf_margin")
        s = normalize_score(fm, cfg["best"], cfg["worst"], cfg["dir"])
        f = f"({fm*100:.1f}%-{cfg['worst']*100:.0f}%)/({cfg['best']*100:.0f}%-{cfg['worst']*100:.0f}%)*100"
        dim.entries.append(AuditEntry("FCF Margin", fm, f, s, 0.25, note=f"{fm*100:.1f}%"))
    else:
        dim.entries.append(AuditEntry("FCF Margin", None, "missing", 50.0, 0.25, missing=True))

    # ROIC-WACC 超额回报（Damodaran核心指标：创造价值 vs 消耗资本）
    roic     = data.get("roic")
    wacc_est = data.get("_wacc", 0.12)   # score_ticker() 预算注入
    if roic is not None:
        excess = roic - wacc_est
        cfg = b("roic")
        s = normalize_score(excess, cfg["best"], cfg["worst"], cfg["dir"])
        f = (f"ROIC-WACC: {roic*100:.1f}%-{wacc_est*100:.1f}%={excess*100:.1f}%"
             f" → clamp({cfg['worst']*100:.0f}%~{cfg['best']*100:.0f}%)")
        dim.entries.append(AuditEntry(
            "ROIC-WACC", excess, f, s, 0.25,
            note=f"{excess*100:.1f}%（{roic*100:.1f}%ROIC − {wacc_est*100:.1f}%WACC）"
        ))
    else:
        dim.entries.append(AuditEntry("ROIC-WACC", None, "missing", 50.0, 0.25, missing=True))

    # D/E ratio (includes convertibles per mock_data convention)
    de = data.get("debt_to_equity")
    if de is not None:
        cfg = b("de_ratio")
        s = normalize_score(de, cfg["best"], cfg["worst"], cfg["dir"])
        f = f"({cfg['worst']}-{de:.4f})/({cfg['worst']}-{cfg['best']})*100  [incl. converts]"
        dim.entries.append(AuditEntry("D/E (w/ converts)", de, f, s, 0.20))
    else:
        dim.entries.append(AuditEntry("D/E (w/ converts)", None, "missing", 50.0, 0.20, missing=True))

    # Sector-specific quality modifier
    if sector == "Hardware":
        cx = data.get("capex_rev")
        if cx is not None:
            cfg = b("capex_rev")
            s = normalize_score(cx, cfg["best"], cfg["worst"], cfg["dir"])
            f = f"({cfg['worst']*100:.0f}%-{cx*100:.1f}%)/({cfg['worst']*100:.0f}%-{cfg['best']*100:.0f}%)*100"
            dim.entries.append(AuditEntry("CapEx/Rev", cx, f, s, 0.10,
                                          note=f"Hardware-specific · {cx*100:.1f}%"))
    elif sector in ("SaaS", "Cybersecurity"):
        nrr = data.get("net_revenue_retention")
        if nrr is not None:
            cfg = b("nrr")
            s = normalize_score(nrr, cfg["best"], cfg["worst"], cfg["dir"])
            f = f"({nrr:.3f}-{cfg['worst']:.2f})/({cfg['best']:.2f}-{cfg['worst']:.2f})*100"
            label = "SaaS-specific" if sector == "SaaS" else "Cyber-specific"
            dim.entries.append(AuditEntry("NRR", nrr, f, s, 0.10,
                                          note=f"{label} · {nrr*100:.0f}%"))

    dim.dim_score, _ = _weighted_dim(dim.entries)
    dim.contribution = round(dim.dim_score * dim.final_weight, 2)
    return dim


# ─────────────────────────────────────────────────────────────────────
# ④ AI Exposure Score (sector-specific field set)
# ─────────────────────────────────────────────────────────────────────

def score_ai_exposure(data: dict, bl: dict, sector: str) -> AuditDimension:
    dim = AuditDimension("④ AI EXPOSURE", "ai_exposure", final_weight=DIM_WEIGHTS["ai_exposure"])
    b = lambda k: _b(bl, k)

    def _add(label: str, field: str, bkey: str, w: float):
        v = data.get(field)
        if v is not None:
            cfg = b(bkey)
            s = normalize_score(v, cfg["best"], cfg["worst"], cfg["dir"])
            f = f"({v*100:.1f}%-{cfg['worst']*100:.0f}%)/({cfg['best']*100:.0f}%-{cfg['worst']*100:.0f}%)*100"
            dim.entries.append(AuditEntry(label, v, f, s, w, note=f"{v*100:.1f}%"))
        else:
            dim.entries.append(AuditEntry(label, None, "missing/N/A", 50.0, w, missing=True))

    if sector == "Hardware":
        _add("AI Rev %",      "ai_revenue_exposure_pct",      "ai_rev_pct",      0.30)
        _add("DataCenter %",  "datacenter_exposure_pct",      "datacenter_pct",  0.35)
        _add("AI Backlog",    "ai_order_backlog_exposure",     "ai_backlog",      0.20)
        _add("Adv. Pkg %",    "advanced_packaging_exposure_pct","adv_pkg_pct",   0.15)
    elif sector == "Cybersecurity":
        _add("AI Rev %",      "ai_revenue_exposure_pct",       "ai_rev_pct",    0.25)
        _add("Cyber AI %",    "cybersecurity_ai_exposure_pct", "cyber_ai_pct",  0.35)
        _add("AI Backlog",    "ai_order_backlog_exposure",     "ai_backlog",    0.20)
        arr = data.get("arr_growth_yoy")
        if arr is not None:
            cfg = b("arr_growth")
            s = normalize_score(arr, cfg["best"], cfg["worst"], cfg["dir"])
            f = f"({arr*100:.1f}%-{cfg['worst']*100:.0f}%)/({cfg['best']*100:.0f}%-{cfg['worst']*100:.0f}%)*100"
            dim.entries.append(AuditEntry("ARR Growth", arr, f, s, 0.20,
                                          note=f"{arr*100:.1f}% YoY"))
        else:
            dim.entries.append(AuditEntry("ARR Growth", None, "missing", 50.0, 0.20, missing=True))
    else:  # SaaS (and unknown sector fallback)
        _add("AI Rev %",      "ai_revenue_exposure_pct",      "ai_rev_pct",      0.30)
        _add("AI Platform %", "software_ai_platform_exposure_pct","ai_platform_pct",0.30)
        _add("AI Backlog",    "ai_order_backlog_exposure",     "ai_backlog",      0.20)
        arr = data.get("arr_growth_yoy")
        if arr is not None:
            cfg = b("arr_growth")
            s = normalize_score(arr, cfg["best"], cfg["worst"], cfg["dir"])
            f = f"({arr*100:.1f}%-{cfg['worst']*100:.0f}%)/({cfg['best']*100:.0f}%-{cfg['worst']*100:.0f}%)*100"
            dim.entries.append(AuditEntry("ARR Growth", arr, f, s, 0.20,
                                          note=f"{arr*100:.1f}% YoY"))
        else:
            dim.entries.append(AuditEntry("ARR Growth", None, "missing", 50.0, 0.20, missing=True))

    dim.dim_score, _ = _weighted_dim(dim.entries)
    dim.contribution = round(dim.dim_score * dim.final_weight, 2)
    return dim


# ─────────────────────────────────────────────────────────────────────
# ⑤ Expectation Gap Score
# ─────────────────────────────────────────────────────────────────────

_EXP_GAP_BASELINES = {
    "actual_rev_beat":  {"best":  0.08, "worst": -0.05, "dir": "positive"},
    "actual_eps_beat":  {"best":  0.12, "worst": -0.08, "dir": "positive"},
    "guidance_beat":    {"best":  0.08, "worst": -0.06, "dir": "positive"},
    "earnings_reaction":{"best":  0.20, "worst": -0.10, "dir": "positive"},
}

def score_expectation_gap(data: dict) -> AuditDimension:
    dim = AuditDimension("⑤ EXPECTATION GAP", "expectation_gap",
                         final_weight=DIM_WEIGHTS["expectation_gap"])

    specs = [
        ("Rev vs Consensus",  "actual_revenue_vs_consensus", "actual_rev_beat",   0.30),
        ("EPS vs Consensus",  "actual_eps_vs_consensus",     "actual_eps_beat",   0.30),
        ("Guidance Beat",     "guidance_vs_consensus",       "guidance_beat",     0.25),
        ("Earnings Reaction", "earnings_reaction_score",     "earnings_reaction", 0.15),
    ]
    for label, fkey, bkey, w in specs:
        v = data.get(fkey)
        cfg = _EXP_GAP_BASELINES[bkey]
        if v is not None:
            s = normalize_score(v, cfg["best"], cfg["worst"], cfg["dir"])
            sign = "+" if v >= 0 else ""
            f = f"({sign}{v*100:.2f}%-{cfg['worst']*100:.2f}%)/({cfg['best']*100:.2f}%-{cfg['worst']*100:.2f}%)*100"
            dim.entries.append(AuditEntry(label, v, f, s, w, note=f"{sign}{v*100:.2f}%"))
        else:
            dim.entries.append(AuditEntry(label, None, "missing", 50.0, w, missing=True))

    dim.dim_score, _ = _weighted_dim(dim.entries)
    dim.contribution = round(dim.dim_score * dim.final_weight, 2)
    return dim


# ─────────────────────────────────────────────────────────────────────
# E. ⑥ Momentum Score — Price vs 200DMA + RSI-14
# ─────────────────────────────────────────────────────────────────────

def score_momentum(data: dict) -> AuditDimension:
    dim = AuditDimension("⑥ MOMENTUM", "momentum", final_weight=DIM_WEIGHTS["momentum"])

    # Price deviation from 200DMA
    dev = data.get("price_vs_200dma")
    if dev is not None:
        # best=+15% (strong uptrend), worst=-20% (downtrend)
        s = normalize_score(dev, best=0.15, worst=-0.20, direction="positive")
        sign = "+" if dev >= 0 else ""
        f = f"({sign}{dev*100:.2f}%-(-20%))/({15}%-(-20%))*100"
        dim.entries.append(AuditEntry("Price vs 200DMA", dev, f, s, 0.60,
                                      note=f"{sign}{dev*100:.2f}% vs MA200"))
    else:
        dim.entries.append(AuditEntry("Price vs 200DMA", None, "missing", 50.0, 0.60, missing=True))

    # RSI-14 piecewise scoring
    rsi = data.get("rsi_14")
    if rsi is not None:
        s, rsi_note = _score_rsi(rsi)
        dim.entries.append(AuditEntry("RSI-14", rsi, "piecewise: optimal=[45,65], >80=fade risk",
                                      s, 0.40, note=rsi_note))
    else:
        dim.entries.append(AuditEntry("RSI-14", None, "missing", 50.0, 0.40, missing=True))

    dim.dim_score, _ = _weighted_dim(dim.entries)
    dim.contribution = round(dim.dim_score * dim.final_weight, 2)
    return dim


# ─────────────────────────────────────────────────────────────────────
# F. ⑦ Risk Penalty with Non-linear Circuit Breaker
# ─────────────────────────────────────────────────────────────────────

_RISK_BASELINES = {
    "beta":           {"best": 0.80, "worst": 2.50, "dir": "negative"},  # inverted: high beta = bad
    "volatility_30d": {"best": 0.15, "worst": 0.70, "dir": "negative"},
    "valuation_risk": {"best": 0.10, "worst": 0.90, "dir": "negative"},
    "liquidity_risk": {"best": 0.02, "worst": 0.30, "dir": "negative"},
    "max_drawdown":   {"best": 0.10, "worst": 0.60, "dir": "negative"},  # abs magnitude
}

def score_risk_penalty(data: dict, de_ratio: float | None) -> tuple[AuditDimension, bool, str, float]:
    """
    Returns (dim, circuit_triggered, circuit_reason, risk_multiplier).
    RawRisk (0–1) * 20 = penalty points.

    Circuit Breaker conditions:
      (A) Beta > 2.2  AND  |MaxDrawdown| > 0.35
      (B) D/E > 1.8
    """
    dim = AuditDimension("⑦ RISK PENALTY", "risk_penalty", final_weight=0.0)

    specs = [
        ("Beta",           "beta",           "beta",           0.25),
        ("Vol 30d",        "volatility_30d", "volatility_30d", 0.20),
        ("Valuation Risk", "valuation_risk", "valuation_risk", 0.25),
        ("Liquidity Risk", "liquidity_risk", "liquidity_risk", 0.15),
        ("Max Drawdown 1y","max_drawdown_1y","max_drawdown",   0.15),
    ]

    for label, fkey, bkey, w in specs:
        v = data.get(fkey)
        if fkey == "max_drawdown_1y" and v is not None:
            v = abs(v)   # use magnitude regardless of sign
        cfg = _RISK_BASELINES[bkey]
        if v is not None:
            # For risk: normalize as "bad = high raw score" → higher raw_risk
            raw_risk = normalize_score(v, cfg["best"], cfg["worst"], cfg["dir"]) / 100.0
            f = f"({cfg['worst']}-{v:.4f})/({cfg['worst']}-{cfg['best']})*100"
            # Store the risk intensity (0–1) as score for aggregation
            dim.entries.append(AuditEntry(label, v, f,
                                          round(raw_risk * 100, 2), w))
        else:
            dim.entries.append(AuditEntry(label, None, "missing → default 0.3", 30.0, w, missing=True))

    # Aggregate raw risk (0–100 internally, convert to 0–1)
    raw_combined, _ = _weighted_dim(dim.entries)
    raw_combined_01 = raw_combined / 100.0
    penalty = round(raw_combined_01 * RISK_MAX_PENALTY, 2)
    dim.dim_score = penalty   # store penalty points for display

    # ── Non-linear Circuit Breaker ──────────────────────────────────
    beta = data.get("beta") or 0.0
    mdd  = abs(data.get("max_drawdown_1y") or 0.0)
    de   = de_ratio if de_ratio is not None else 0.0

    circuit = False
    reason  = ""
    if beta > CIRCUIT_BETA_THRESH and mdd > CIRCUIT_DRAWDOWN_THRESH:
        circuit = True
        reason  = (f"Beta={beta:.2f}>{CIRCUIT_BETA_THRESH} AND "
                   f"|MaxDD|={mdd*100:.1f}%>{CIRCUIT_DRAWDOWN_THRESH*100:.0f}%")
    if de > CIRCUIT_DE_THRESH:
        circuit = True
        reason  = (reason + (" | " if reason else "") +
                   f"D/E={de:.2f}>{CIRCUIT_DE_THRESH}")

    multiplier = CIRCUIT_MULTIPLIER if circuit else 1.0
    dim.contribution = -penalty
    return dim, circuit, reason, multiplier


# ─────────────────────────────────────────────────────────────────────
# Master scoring entry point
# ─────────────────────────────────────────────────────────────────────

def score_ticker(ticker: str, data: dict) -> ScoreResult:
    """
    Score a single ticker from a merged data dict.
    data must contain fields from both yfinance_fetcher and mock/quant_data.
    """
    sector = data.get("sector_tag", "Hardware")
    if sector not in SECTOR_BASELINES:
        sector = "Hardware"
    bl = SECTOR_BASELINES[sector]
    company = data.get("company_name", ticker)
    bad = data.get("_bad_fields", [])

    # Damodaran: 预算 WACC 供 score_quality 使用（ROIC-WACC 超额回报）
    if "_wacc" not in data:
        data["_wacc"] = calc_wacc(data, get_category(ticker))

    dims = [
        score_valuation(data, bl),
        score_growth(data, bl),
        score_quality(data, bl, sector),
        score_ai_exposure(data, bl, sector),
        score_expectation_gap(data),
        score_momentum(data),
    ]

    raw_sum = sum(d.dim_score * d.final_weight for d in dims)

    de = data.get("debt_to_equity")
    risk_dim, circuit, reason, multiplier = score_risk_penalty(data, de)
    penalty = risk_dim.dim_score

    dim_scores_dict = {d.key: d.dim_score for d in dims}
    base   = compute_base_score(dim_scores_dict, penalty, circuit)
    dynAdj = compute_dynamic_adjustment(data)
    final  = compute_final_score(base, dynAdj)

    # Rating
    if circuit:
        rating = "Watchlist" if final >= 50 else "Avoid"
    elif final >= 70:
        rating = "Buy"
    elif final >= 60:
        rating = "Hold"
    elif final >= 45:
        rating = "Watchlist"
    else:
        rating = "Avoid"

    return ScoreResult(
        ticker=ticker,
        sector=sector,
        company_name=company,
        dim_scores=dim_scores_dict,
        audit_dims=dims + [risk_dim],
        risk_penalty=penalty,
        circuit_triggered=circuit,
        circuit_reason=reason,
        risk_multiplier=multiplier,
        raw_sum=round(raw_sum, 2),
        base_score=base,
        dynamic_adjustment=dynAdj,
        final_score=final,
        rating=rating,
        bad_fields=bad,
    )
