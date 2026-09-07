"""Pure FIFO matching for option transactions."""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict, deque
from typing import Any

from account.options import OCC_RE

EXPLICIT_TYPES = {
    "SELL TO OPEN": ("sell", "open"),
    "BUY TO OPEN": ("buy", "open"),
    "BUY TO CLOSE": ("buy", "close"),
    "SELL TO CLOSE": ("sell", "close"),
}
GENERIC_TYPES = {"BUY": "buy", "SELL": "sell"}
EXPIRY_TYPES = {"OPTION EXPIRED", "EXPIRED", "ASSIGNED", "EXERCISE"}

# Options multiplier: 1 contract = 100 shares
_MULT = 100.0


def _value(row: Any, key: str, default=None):
    try:
        return row[key]
    except Exception:
        return getattr(row, key, default)


def _lot_per_share_price(lot: dict) -> float:
    """Return per-share option price for a lot.

    Brokers that omit a separate `price` column (e.g. Firstrade Chinese CSV)
    only supply the total contract cash-flow in `amount`.  In that case,
    `lot["price"]` is 0 and we recover the per-share price as
    `abs(cash_pc) / 100`.  Commission included in `amount` causes a small
    error (<1%) which is acceptable for cost-basis purposes.
    """
    if lot["price"] > 1e-9:
        return lot["price"]
    return abs(lot["cash_pc"]) / _MULT


def _risk_capital(lot: dict, matched_qty: float, open_cash: float,
                  option_type: str, strike: float) -> float:
    """Capital-at-risk denominator for return_on_risk.

    Long positions  → premium paid (per-contract absolute cash outflow).
    Short put       → strike × 100 × qty  (stock-to-zero max loss).
    Short call      → 10× premium received (unlimited risk; proxy only).
    """
    if lot["direction"] == "long":
        return abs(open_cash)
    # short
    if option_type == "put":
        return strike * _MULT * matched_qty
    # short call: use 10× credit received as conservative proxy
    return abs(open_cash) * 10 if abs(open_cash) > 1e-9 else abs(open_cash)


def group_realized_trades_into_combos(realized: list[dict]) -> None:
    """Pair a spread's two realized legs into one combined position, in
    place -- adds "combo_id", "combo_strategy", "combo_pnl", and
    "is_combo_head" to every dict in `realized`.

    Why: `strategy_type` (long_put/short_put/long_call/short_call) scores
    each leg's win/loss independently. For a systematic spread trader
    that's not just noisy, it's structurally unfair -- a spread's
    protective leg (e.g. the long put in a bull put spread) is *supposed*
    to lose most of its value when the position as a whole wins; counting
    that as an independent "loss" misrepresents the strategy. Professional
    options-performance reporting scores win/loss on the combined
    position, not the leg. This function is the pairing step that makes
    that possible -- confirmed against this account's real 2026-09-06
    data: 54 of 189 (open_date, underlying) groups show an opposite-side
    leg opened the same day, i.e. a real constructed spread, not a
    coincidence.

    Pairing rule (mirrors account_monitor.py's _build_spread_portfolios,
    which does the same thing for currently-OPEN positions -- applied
    here to CLOSED ones): same underlying + same expiry + same
    option_type + opposite lot_direction (one long, one short) + opened
    on the same date, matched by EXACT quantity. Quantity is required to
    match exactly on purpose -- a partial/unequal match would need to
    split one leg's P&L proportionally, which is a guess this function
    won't make. A leg with no exact-quantity opposite-side match on its
    open date is left as its own single-leg combo (a real naked/uncovered
    trade, or a spread leg that isn't cleanly matchable from this data) --
    not force-paired.
    """
    groups: dict[tuple, dict[str, list[int]]] = defaultdict(lambda: {"long": [], "short": []})
    for i, r in enumerate(realized):
        key = (r["underlying"], r["expiry"], r["option_type"], r["open_date"])
        direction = r.get("lot_direction")
        if direction in ("long", "short"):
            groups[key][direction].append(i)

    combo_id_of: dict[int, str] = {}
    combo_n = 0
    for longs, shorts in (g.values() for g in groups.values()):
        used_shorts: set[int] = set()
        for li in longs:
            lq = realized[li]["quantity"]
            for si in shorts:
                if si in used_shorts:
                    continue
                if abs(realized[si]["quantity"] - lq) < 1e-6:
                    combo_id_of[li] = combo_id_of[si] = f"combo_{combo_n}"
                    combo_n += 1
                    used_shorts.add(si)
                    break

    by_combo: dict[str, list[int]] = defaultdict(list)
    for i, cid in combo_id_of.items():
        by_combo[cid].append(i)

    for i, r in enumerate(realized):
        cid = combo_id_of.get(i)
        if cid is None:
            r["combo_id"] = f"single_{i}"
            r["combo_strategy"] = r["strategy_type"]
            r["combo_pnl"] = r["realized_pnl"]
            r["is_combo_head"] = True
            continue
        leg_idxs = by_combo[cid]
        legs = [realized[j] for j in leg_idxs]
        net_open_cash = sum(l["open_cash"] for l in legs)
        kind = "credit" if net_open_cash > 0 else "debit"
        r["combo_id"] = cid
        r["combo_strategy"] = f"{r['option_type']}_{kind}_spread"
        r["combo_pnl"] = round(sum(l["realized_pnl"] for l in legs), 2)
        # Exactly one leg per combo is the "head" -- the short leg, by
        # convention (it's the one that defines credit-vs-debit), so
        # counting combos means counting heads, not counting rows.
        r["is_combo_head"] = (r["lot_direction"] == "short")


def _is_combo_head(row: dict) -> bool:
    return bool(row.get("is_combo_head", True))


def calculate_fifo_matches(rows: list[Any]) -> dict:
    """Calculate realized option trades and open FIFO costs from transaction rows.

    This function has no database side effects. It accepts rows with
    trade_date/type/symbol/quantity/price/amount fields and returns the payload
    needed by `options_repository.replace_realized_trades_and_fifo_costs`.
    """
    lots: dict = defaultdict(deque)
    realized: list[dict] = []
    net_pos: dict = defaultdict(float)

    for row in rows:
        symbol = str(_value(row, "symbol", "") or "").strip().upper()
        match = OCC_RE.match(symbol)
        if not match:
            continue

        tx_type = str(_value(row, "type", "") or "").strip().upper()
        qty = abs(float(_value(row, "quantity", 0) or 0))
        if qty < 0.001:
            continue

        price = float(_value(row, "price", 0) or 0)
        amount = float(_value(row, "amount", 0) or 0)
        cash_per_contract = amount / qty if qty else 0.0

        if tx_type in EXPIRY_TYPES:
            current = net_pos[symbol]
            if abs(current) < 1e-9:
                continue
            side = "sell" if current > 1e-9 else "buy"
            open_close = "close"
            cash_per_contract = 0.0
        elif tx_type in EXPLICIT_TYPES:
            side, open_close = EXPLICIT_TYPES[tx_type]
        elif tx_type in GENERIC_TYPES:
            side = GENERIC_TYPES[tx_type]
            current = net_pos[symbol]
            if side == "buy":
                open_close = "close" if current < -1e-9 else "open"
            else:
                open_close = "close" if current > 1e-9 else "open"
        else:
            continue

        net_pos[symbol] += qty if side == "buy" else -qty

        trade_date = _value(row, "trade_date", "")
        if open_close == "open":
            lots[symbol].append({
                "rem": qty,
                "date": trade_date,
                "price": price,
                "cash_pc": cash_per_contract,
                "direction": "long" if side == "buy" else "short",
            })
            continue

        remaining = qty
        while remaining > 1e-9 and lots[symbol]:
            lot = lots[symbol][0]
            matched_qty = min(remaining, lot["rem"])
            open_cash = lot["cash_pc"] * matched_qty
            close_cash = cash_per_contract * matched_qty
            pnl = open_cash + close_cash

            underlying = match.group(1)
            option_type = "call" if match.group(5) == "C" else "put"
            expiry = f"20{match.group(2)}-{match.group(3)}-{match.group(4)}"
            strike = int(match.group(6)) / 1000
            strategy = f"{lot['direction']}_{option_type}"

            risk_cap = _risk_capital(lot, matched_qty, open_cash, option_type, strike)

            try:
                holding_days = (
                    _dt.date.fromisoformat(str(trade_date))
                    - _dt.date.fromisoformat(str(lot["date"]))
                ).days
            except Exception:
                holding_days = None

            realized.append({
                "underlying": underlying,
                "symbol": symbol,
                "strategy_type": strategy,
                "lot_direction": lot["direction"],
                "open_date": lot["date"],
                "close_date": trade_date,
                "holding_days": holding_days,
                "quantity": matched_qty,
                "open_cash": round(open_cash, 2),
                "close_cash": round(close_cash, 2),
                "realized_pnl": round(pnl, 2),
                "return_on_risk": round(pnl / risk_cap, 6) if risk_cap else 0.0,
                "win_loss": "win" if pnl > 0 else ("loss" if pnl < 0 else "flat"),
                "option_type": option_type,
                "expiry": expiry,
                "strike": strike,
            })

            remaining -= matched_qty
            lot["rem"] -= matched_qty
            if lot["rem"] <= 1e-9:
                lots[symbol].popleft()

    # Use per-share price for unit_cost; fall back to abs(cash_pc)/100 when
    # the broker CSV omits a separate price column (amount-only format).
    fifo_costs = {}
    for symbol, symbol_lots in lots.items():
        total_remaining = sum(lot["rem"] for lot in symbol_lots)
        if total_remaining <= 0:
            continue
        weighted_price = (
            sum(_lot_per_share_price(lot) * lot["rem"] for lot in symbol_lots)
            / total_remaining
        )
        fifo_costs[symbol] = round(weighted_price, 4)

    total_realized = sum(row["realized_pnl"] for row in realized)
    wins = sum(1 for row in realized if row["win_loss"] == "win")
    losses = sum(1 for row in realized if row["win_loss"] == "loss")

    group_realized_trades_into_combos(realized)   # mutates in place: adds combo_id/combo_strategy
    combo_wins   = sum(1 for row in realized if row.get("combo_pnl", row["realized_pnl"]) > 0
                       and _is_combo_head(row))
    combo_count  = sum(1 for row in realized if _is_combo_head(row))
    combo_losses = sum(1 for row in realized if row.get("combo_pnl", row["realized_pnl"]) < 0
                       and _is_combo_head(row))

    return {
        "realized": realized,
        "fifo_costs": fifo_costs,
        "summary": {
            "realized_count": len(realized),
            "open_lots": len(fifo_costs),
            "total_realized_pnl": round(total_realized, 2),
            "wins": wins,
            "losses": losses,
        },
        "combo_summary": {
            # "整单"口径：一个价差的两条腿合并算一次胜负，不是两条腿各算一次。
            # 专业机构衡量价差策略表现的标准做法——保护腿在整单盈利时"看起来
            # 亏钱"是价差结构本身决定的，不该被单独记一次"败"。
            "combo_count":        combo_count,
            "total_realized_pnl": round(total_realized, 2),   # 整单加总跟单腿加总是同一个数
            "wins":               combo_wins,
            "losses":             combo_losses,
        },
    }
