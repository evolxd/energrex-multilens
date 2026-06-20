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
    }
