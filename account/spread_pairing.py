"""Pure, dependency-free option spread recognition engine.

Physically extracted from account_monitor.py's private helpers
(docs/REFACTORING_WORKFLOW.md Phase 3, 2026-09) to sever the coupling
between "which spreads exist" logic and the Streamlit UI + database layers
that used to surround it. See docs/architecture/account_monitor.md for the
before/after diagrams and docs/architecture/spread_pairing_contracts_draft.py
for the contract this module implements.

Behavior is unchanged from the pre-move implementation. Locked by:
  - tests/golden/test_build_spread_portfolios_golden.py, via the thin
    acct_id-based wrapper still kept in account_monitor.py
  - tests/golden/test_spread_pairing_contract_golden.py, directly against
    this module's public build_spread_portfolios()

This module must never import streamlit and must never perform I/O: callers
load the positions DataFrame and pass it in (dependency injection), per
arch.md's "无副作用纯内存" (side-effect-free, in-memory-only core) rule.

Recommendation strings, dict keys and all other user-facing text are
business output locked by the golden snapshots above -- they stay in
Chinese exactly as before. Only comments and docstrings were translated
during this move; no string literal that is part of the returned data was
touched.
"""

from __future__ import annotations

import datetime

import pandas as pd

from account.options import parse_occ as _parse_occ
from account.options import signed_quantity as _signed_quantity

_DTE_CRITICAL = 14
_DTE_REVIEW = 21


def _spread_days_to_expiry(exp_str, today) -> int:
    """Days remaining to expiry; returns 9999 if the date cannot be parsed."""
    try:
        return (datetime.date.fromisoformat(str(exp_str or "")) - today).days
    except Exception:
        return 9999


def _spread_leg_pnl(leg: dict, qty_frac: float = 1.0) -> float:
    """Per-leg P&L (scaled by qty_frac); falls back to total_pnl when there is no current price."""
    cp = leg.get("cur_price")
    uc = leg.get("unit_cost") or 0
    q = leg.get("qty", 0)
    if cp is None:
        return (leg.get("total_pnl") or 0) * qty_frac
    return (float(cp) - float(uc)) * q * 100 * qty_frac


def _parse_spread_legs(df: pd.DataFrame, today: datetime.date) -> list[dict]:
    """Parse options_positions rows into uniform leg dicts; skips rows that
    fail to parse as an OCC symbol or have zero quantity."""
    legs = []
    for _, row in df.iterrows():
        sym = str(row.get("symbol", "")).strip().upper()
        p = _parse_occ(sym)
        if not p:
            continue
        # abs(qty)+direction='short' (Chrome scrape) and signed qty (xlsx
        # import) both occur in this table; normalized in one place by
        # account.options.signed_quantity.
        qty = int(_signed_quantity(row.get("quantity"), row.get("direction")))
        if qty == 0:
            continue
        # read_sql_query yields NaN (not None) for NULLs in partially-filled REAL
        # columns, so check with pd.notna to treat both as missing.
        legs.append({
            "symbol":     sym,
            "underlying": p["root"],
            "direction":  p.get("option_type", p["direction"].lower()),   # "call" / "put"
            "strike":     p["strike"],
            "expiry":     p["expiry"],
            "qty":        qty,
            "unit_cost":  float(row["unit_cost"])     if pd.notna(row.get("unit_cost"))     else 0.0,
            "cur_price":  float(row["current_price"]) if pd.notna(row.get("current_price")) else None,
            "total_pnl":  float(row["total_pnl"])     if pd.notna(row.get("total_pnl"))     else None,
            "delta":      float(row["delta"])          if pd.notna(row.get("delta"))         else None,
            "iv":         float(row["iv"])             if pd.notna(row.get("iv"))            else None,
            "dte":        _spread_days_to_expiry(p["expiry"], today),
        })
    return legs


def _vertical_economics(ll: dict, sl: dict, qty) -> dict:
    """Vertical spread type, strike width, debit/credit direction, max P&L
    and breakeven (ll = long leg, sl = short leg)."""
    direction = ll["direction"]
    if direction == "call":
        stype = "Bull Call Spread" if ll["strike"] < sl["strike"] else "Bear Call Spread"
    else:
        stype = "Bear Put Spread"  if ll["strike"] > sl["strike"] else "Bull Put Spread"

    low_s  = min(ll["strike"], sl["strike"])
    high_s = max(ll["strike"], sl["strike"])
    width  = round(high_s - low_s, 4)

    # positive net = debit paid; negative = credit received
    net_ps   = ll["unit_cost"] - sl["unit_cost"]
    net_tot  = round(net_ps * qty * 100, 2)
    is_debit = net_ps > 0

    if is_debit:
        max_profit = round((width - abs(net_ps)) * qty * 100, 2)
        max_loss   = round(abs(net_tot), 2)
        if direction == "call":
            breakeven = round(ll["strike"] + abs(net_ps), 4)
        else:
            breakeven = round(ll["strike"] - abs(net_ps), 4)
    else:
        max_profit = round(abs(net_tot), 2)
        max_loss   = round((width - abs(net_ps)) * qty * 100, 2)
        if direction == "call":
            breakeven = round(sl["strike"] + abs(net_ps), 4)
        else:
            breakeven = round(sl["strike"] - abs(net_ps), 4)

    return {"stype": stype, "low_s": low_s, "high_s": high_s, "width": width,
            "net_ps": net_ps, "net_tot": net_tot, "is_debit": is_debit,
            "max_profit": max_profit, "max_loss": max_loss, "breakeven": breakeven}


def _vertical_recommendation(econ: dict, pnl: float, dte_v: int) -> tuple:
    """(pnl_pct, recommendation text) for a vertical spread: debit spreads are
    scored against cost, credit spreads against risk capital."""
    net_tot, max_profit = econ["net_tot"], econ["max_profit"]
    max_loss, breakeven = econ["max_loss"], econ["breakeven"]
    if econ["is_debit"]:
        basis   = abs(max_loss)
        pnl_pct = round(pnl / basis * 100, 1) if basis else 0
        rec = (f"净权利金 ${abs(net_tot):,.0f}（付），"
               f"当前盈亏 ${pnl:+,.0f}（{pnl_pct:+.0f}% on cost），"
               f"最大盈利 ${max_profit:,.0f}，盈亏平衡点 ${breakeven:,.2f}")
    else:
        # Credit spread: pnl_pct = P&L / max loss (risk capital), not / premium received
        basis   = abs(max_loss)
        pnl_pct = round(pnl / basis * 100, 1) if basis else 0
        rec = (f"收权利金 ${abs(net_tot):,.0f}，"
               f"当前盈亏 ${pnl:+,.0f}（{pnl_pct:+.0f}% on risk），"
               f"最大亏损 ${max_loss:,.0f}，盈亏平衡点 ${breakeven:,.2f}")

    if dte_v <= _DTE_CRITICAL:
        rec = f"🚨 DTE={dte_v}天，立即决策展期或平仓！" + rec
    elif dte_v <= _DTE_REVIEW:
        rec = f"⚠️ DTE={dte_v}天，" + rec
    return pnl_pct, rec


def _make_vertical_portfolio(legs: list[dict], li: int, si: int) -> dict:
    """Assemble one vertical spread (li = long leg, si = short leg); legs are
    copied with the remaining quantity at call time."""
    ll = legs[li]   # long leg
    sl = legs[si]   # short leg
    qty = min(abs(ll["qty"]), abs(sl["qty"]))
    direction = ll["direction"]
    und  = ll["underlying"]
    econ = _vertical_economics(ll, sl, qty)

    long_frac  = qty / abs(ll["qty"])
    short_frac = qty / abs(sl["qty"])
    pnl = _spread_leg_pnl(ll, long_frac) + _spread_leg_pnl(sl, short_frac)

    dte_v = min(ll["dte"], sl["dte"])
    if dte_v <= _DTE_CRITICAL:
        risk_level = "CRITICAL"
    elif dte_v <= _DTE_REVIEW:
        risk_level = "HIGH"
    else:
        risk_level = "LOW"

    pnl_pct, rec = _vertical_recommendation(econ, pnl, dte_v)
    stype = econ["stype"]
    return {
        "id": f"{und}_{stype}_{ll['expiry']}",
        "type": stype, "underlying": und, "direction": direction,
        "legs": [dict(li=li, **ll), dict(li=si, **sl)],
        "spread_qty": qty,
        "expiry": ll["expiry"], "dte": dte_v,
        "low_strike": econ["low_s"], "high_strike": econ["high_s"], "strike_width": econ["width"],
        "is_debit": econ["is_debit"],
        "net_per_share": round(econ["net_ps"], 4), "net_total": econ["net_tot"],
        "max_profit": econ["max_profit"], "max_loss": econ["max_loss"],
        "breakeven": econ["breakeven"],
        "current_pnl": round(pnl, 2),
        "pnl_pct": pnl_pct,
        "risk_level": risk_level,
        "recommendation": rec,
    }


def _diagonal_risk_and_rec(near_leg: dict, far_leg: dict, is_proper: bool,
                           pnl: float, net_tot: float) -> tuple:
    """(risk_level, recommendation text) for a calendar/diagonal spread.
    Decision order: near leg critical -> reversed -> near leg needs review -> normal."""
    near_dte = near_leg["dte"]
    if near_dte <= _DTE_CRITICAL:
        risk_level = "CRITICAL"
        rec = (f"🚨 近月腿 DTE={near_dte}天 — 立即决策展期或平仓！"
               f"当前盈亏 ${pnl:+,.0f}")
    elif not is_proper:
        risk_level = "HIGH"
        rec = (f"⚠️ 买腿（{near_leg['expiry']}）早于卖腿（{far_leg['expiry']}）到期，"
               f"买腿失效后卖腿变裸空仓！当前盈亏 ${pnl:+,.0f}")
    elif near_dte <= _DTE_REVIEW:
        risk_level = "HIGH"
        rec = (f"⚠️ 近月腿 DTE={near_dte}天，制定展期计划；"
               f"当前盈亏 ${pnl:+,.0f}")
    else:
        risk_level = "MEDIUM"
        rec = (f"对角价差持有中，Theta 时间优势在近月卖腿；"
               f"当前盈亏 ${pnl:+,.0f}，净成本 ${abs(net_tot):,.0f}")
    return risk_level, rec


def _make_diagonal_portfolio(legs: list[dict], li: int, si: int) -> dict:
    """Assemble one calendar/diagonal spread (crossing expiries); proper form
    is short the near leg + long the far leg."""
    ll = legs[li]
    sl = legs[si]
    qty = min(abs(ll["qty"]), abs(sl["qty"]))
    direction = ll["direction"]
    und = ll["underlying"]

    # Identify near/far by expiry
    near_leg, far_leg = (ll, sl) if ll["expiry"] < sl["expiry"] else (sl, ll)
    is_proper = near_leg["qty"] < 0 and far_leg["qty"] > 0

    same_strike = abs(near_leg["strike"] - far_leg["strike"]) < 0.001
    stype = (("Calendar Spread" if same_strike else "Diagonal Spread (LEAPS)")
             if is_proper else "Reversed Diagonal (⚠️ 买腿先到期)")

    # net cost: positive = debit
    net_ps = (far_leg["unit_cost"] - near_leg["unit_cost"] if is_proper
              else near_leg["unit_cost"] - far_leg["unit_cost"])
    net_tot = round(net_ps * qty * 100, 2)

    long_frac  = qty / abs(ll["qty"])
    short_frac = qty / abs(sl["qty"])
    pnl = _spread_leg_pnl(ll, long_frac) + _spread_leg_pnl(sl, short_frac)
    risk_level, rec = _diagonal_risk_and_rec(near_leg, far_leg, is_proper, pnl, net_tot)

    # Proper diagonal (long far + short near) = net debit paid caps max loss
    diag_max_loss   = round(abs(net_tot), 2) if (is_proper and net_ps > 0) else None
    diag_pnl_pct    = round(pnl / diag_max_loss * 100, 1) if diag_max_loss else None

    return {
        "id": f"{und}_{stype}_{near_leg['expiry']}_vs_{far_leg['expiry']}",
        "type": stype, "underlying": und, "direction": direction,
        "legs": [dict(li=li, **ll), dict(li=si, **sl)],
        "spread_qty": qty,
        "expiry": near_leg["expiry"], "dte": near_leg["dte"],
        "near_expiry": near_leg["expiry"], "far_expiry": far_leg["expiry"],
        "near_strike": near_leg["strike"], "far_strike": far_leg["strike"],
        "is_proper": is_proper,
        "net_per_share": round(net_ps, 4), "net_total": net_tot,
        "max_profit": None, "max_loss": diag_max_loss, "breakeven": None,
        "current_pnl": round(pnl, 2), "pnl_pct": diag_pnl_pct,
        "risk_level": risk_level,
        "recommendation": rec,
    }


def _naked_risk_and_rec(leg: dict, max_loss_naked, pnl: float) -> tuple:
    """(risk_level, recommendation text) for a naked position: long option /
    naked short put / naked short call, with a near-expiry prefix added on top."""
    is_long   = leg["qty"] > 0
    direction = leg["direction"]
    dte_v     = leg["dte"]
    if is_long:
        risk_level = "MEDIUM" if dte_v > _DTE_REVIEW else ("CRITICAL" if dte_v <= _DTE_CRITICAL else "HIGH")
        rec = f"买权持有，最大亏损权利金 ${max_loss_naked:,.0f}，当前盈亏 ${pnl:+,.0f}"
    elif direction == "put":
        risk_level = "CRITICAL" if dte_v <= _DTE_CRITICAL else "HIGH"
        rec = f"⚠️ 裸卖 Put，最大亏损 ${max_loss_naked:,.0f}（标的归零），当前盈亏 ${pnl:+,.0f}"
    else:
        risk_level = "CRITICAL" if dte_v <= _DTE_CRITICAL else "HIGH"
        rec = f"⚠️ 裸卖 Call，风险无限，当前盈亏 ${pnl:+,.0f}"

    if dte_v <= _DTE_CRITICAL:
        rec = f"🚨 DTE={dte_v}天 — 立即处理！" + rec
    elif dte_v <= _DTE_REVIEW:
        rec = f"⚠️ DTE={dte_v}天 — " + rec
    return risk_level, rec


def _make_naked_portfolio(legs: list[dict], idx: int) -> dict:
    """Assemble one unpaired leg into a naked-position entry."""
    leg = legs[idx]
    qty = leg["qty"]
    is_long  = qty > 0
    direction = leg["direction"]
    und = leg["underlying"]
    dte_v = leg["dte"]

    stype = f"Naked {'Long' if is_long else 'Short'} {direction.capitalize()}"
    pnl   = _spread_leg_pnl(leg)
    if is_long:
        max_loss_naked = round(leg["unit_cost"] * abs(qty) * 100, 2)
    elif direction == "put":
        # Naked short put: max loss = strike x 100 x contracts (underlying goes to 0)
        max_loss_naked = round(leg["strike"] * abs(qty) * 100, 2)
    else:
        max_loss_naked = None  # Naked short call: theoretically unlimited loss

    risk_level, rec = _naked_risk_and_rec(leg, max_loss_naked, pnl)

    return {
        "id": f"{und}_{stype}_{leg['expiry']}",
        "type": stype, "underlying": und, "direction": direction,
        "legs": [dict(li=idx, **leg)],
        "spread_qty": abs(qty),
        "expiry": leg["expiry"], "dte": dte_v,
        "net_per_share": leg["unit_cost"], "net_total": leg["unit_cost"] * abs(qty) * 100,
        "max_profit": None, "max_loss": max_loss_naked, "breakeven": None,
        "current_pnl": round(pnl, 2), "pnl_pct": None,
        "risk_level": risk_level,
        "recommendation": rec,
    }


def _consume_spread_pair(legs: list[dict], matched: list[bool],
                         li: int, si: int, sq) -> None:
    """After pairing sq contracts, deduct the remaining quantity on both legs
    (in place); a fully consumed leg is marked matched."""
    rem_l = abs(legs[li]["qty"]) - sq
    rem_s = abs(legs[si]["qty"]) - sq
    if rem_l <= 0:
        matched[li] = True
    else:
        legs[li]["qty"] = rem_l
    if rem_s <= 0:
        matched[si] = True
    else:
        legs[si]["qty"] = -rem_s


def _match_vertical_spreads(legs: list[dict], matched: list[bool],
                            und_indices: list[int], portfolios: list[dict]) -> None:
    """Round 1: same underlying + same expiry + same option type -> vertical
    spread; results are appended to portfolios in pairing order."""
    exp_dir: dict[tuple, list[int]] = {}
    for i in und_indices:
        if matched[i]:
            continue
        exp_dir.setdefault((legs[i]["expiry"], legs[i]["direction"]), []).append(i)

    for (_exp, _dir), group in sorted(exp_dir.items()):
        longs  = [i for i in group if legs[i]["qty"] > 0]
        shorts = [i for i in group if legs[i]["qty"] < 0]
        for li in longs:
            if matched[li]:
                continue
            for si in shorts:
                if matched[si]:
                    continue
                port = _make_vertical_portfolio(legs, li, si)
                portfolios.append(port)
                _consume_spread_pair(legs, matched, li, si, port["spread_qty"])
                if matched[li]:
                    break


def _match_diagonal_spreads(legs: list[dict], matched: list[bool],
                            und_indices: list[int], portfolios: list[dict]) -> None:
    """Round 2: same underlying + same option type + different expiry ->
    calendar/diagonal spread (paired near-to-far by expiry)."""
    dir_groups: dict[str, list[int]] = {}
    for i in und_indices:
        if matched[i]:
            continue
        dir_groups.setdefault(legs[i]["direction"], []).append(i)

    for _dir, group in dir_groups.items():
        longs  = sorted([i for i in group if legs[i]["qty"] > 0], key=lambda i: legs[i]["expiry"])
        shorts = sorted([i for i in group if legs[i]["qty"] < 0], key=lambda i: legs[i]["expiry"])
        for li in longs:
            if matched[li]:
                continue
            for si in shorts:
                if matched[si]:
                    continue
                if legs[li]["expiry"] == legs[si]["expiry"]:
                    continue
                port = _make_diagonal_portfolio(legs, li, si)
                portfolios.append(port)
                _consume_spread_pair(legs, matched, li, si, port["spread_qty"])
                if matched[li]:
                    break


def build_spread_portfolios(df: pd.DataFrame, today: datetime.date) -> list[dict]:
    """Recognize spread/naked structures from a positions DataFrame.

    Priority: same underlying + same expiry + same type -> vertical spread;
    same underlying + different expiry + same type -> calendar/diagonal
    spread; everything else -> naked position. Returns a list sorted from
    highest to lowest risk level.

    This is the public contract entry point (see
    docs/architecture/spread_pairing_contracts_draft.py: SpreadPairingEngine).
    It performs no I/O -- `df` must already be loaded by the caller (typically
    account_monitor.py::_build_spread_portfolios, which reads it via
    account.options_repository.load_options_positions). `today` is an
    explicit parameter so this function never touches the system clock.
    """
    if df.empty:
        return []

    legs = _parse_spread_legs(df, today)
    matched = [False] * len(legs)
    portfolios: list[dict] = []

    # ── Main matching loop: per underlying, rounds 1 → 2 → 3 ─────
    und_groups: dict[str, list[int]] = {}
    for i, leg in enumerate(legs):
        und_groups.setdefault(leg["underlying"], []).append(i)

    for und, und_indices in sorted(und_groups.items()):
        _match_vertical_spreads(legs, matched, und_indices, portfolios)
        _match_diagonal_spreads(legs, matched, und_indices, portfolios)

        # Round 3: Remaining → naked
        for i in und_indices:
            if not matched[i] and abs(legs[i]["qty"]) > 0:
                portfolios.append(_make_naked_portfolio(legs, i))
                matched[i] = True

    # Sort: CRITICAL → HIGH → MEDIUM → LOW
    _RISK_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    portfolios.sort(key=lambda p: (_RISK_ORDER.get(p["risk_level"], 9), p["underlying"]))
    return portfolios


__all__ = ["build_spread_portfolios"]
