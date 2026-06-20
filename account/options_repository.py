"""Repository helpers for option positions and realized option trades."""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import pandas as pd

from account.db import db
from account.options import OCC_RE, parse_occ

ET = ZoneInfo("America/New_York")


def derive_open_options(acct_id: str) -> list[dict]:
    """Return current open option positions from the authoritative positions table.

    This intentionally does not reconstruct positions from transactions. Broker
    history can be incomplete, while `options_positions` is populated from the
    reconciled Firstrade XLSX/scrape import and preserves signed quantity,
    unit_cost, market_value, and long/short side.
    """
    df = load_options_positions(acct_id)
    if df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or not OCC_RE.match(symbol):
            continue
        parsed = parse_occ(symbol)
        quantity = int(row.get("quantity") or 0)
        direction = str(row.get("direction") or "").strip().lower()
        if direction not in {"long", "short"}:
            direction = "short" if quantity < 0 else "long"
        result.append({
            "symbol": symbol,
            "direction": direction,
            "option_type": parsed.get("option_type"),
            "call_put": parsed.get("call_put"),
            "strike": row.get("strike") if row.get("strike") is not None else parsed.get("strike"),
            "expiry": row.get("expiry") or parsed.get("expiry"),
            "quantity": quantity,
            "unit_cost": row.get("unit_cost"),
            "current_price": row.get("current_price"),
            "market_value": row.get("market_value"),
            "day_pnl": row.get("day_pnl"),
            "total_pnl": row.get("total_pnl"),
        })
    return sorted(result, key=lambda item: item["expiry"] or "")


def load_options_positions(acct_id: str) -> pd.DataFrame:
    conn = db()
    df = pd.read_sql_query(
        "SELECT * FROM options_positions WHERE account_id=? ORDER BY expiry, symbol",
        conn,
        params=(acct_id,),
    )
    conn.close()
    return df


def save_options_positions(acct_id: str, rows: list[dict]) -> None:
    conn = db()
    now = _dt.datetime.now(ET).isoformat()
    for row in rows:
        conn.execute(
            """
            INSERT INTO options_positions
              (account_id,symbol,direction,strike,expiry,quantity,
               unit_cost,current_price,market_value,day_pnl,total_pnl,last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account_id,symbol) DO UPDATE SET
              direction=excluded.direction, strike=excluded.strike,
              expiry=excluded.expiry, quantity=excluded.quantity,
              unit_cost=excluded.unit_cost, current_price=excluded.current_price,
              market_value=excluded.market_value, day_pnl=excluded.day_pnl,
              total_pnl=excluded.total_pnl, last_updated=excluded.last_updated
            """,
            (
                acct_id,
                row.get("symbol"),
                row.get("direction"),
                row.get("strike"),
                row.get("expiry"),
                row.get("quantity"),
                row.get("unit_cost"),
                row.get("current_price"),
                row.get("market_value"),
                row.get("day_pnl"),
                row.get("total_pnl"),
                now,
            ),
        )
    conn.commit()
    conn.close()


def delete_options_position(acct_id: str, symbol: str) -> None:
    conn = db()
    conn.execute(
        "DELETE FROM options_positions WHERE account_id=? AND symbol=?",
        (acct_id, symbol),
    )
    conn.commit()
    conn.close()


def update_option_market_snapshot(
    acct_id: str,
    symbol: str,
    *,
    price: float,
    market_value: float,
    day_pnl: float,
    total_pnl: float | None,
    iv: float | None = None,
    delta: float | None = None,
    gamma: float | None = None,
    theta: float | None = None,
    vega: float | None = None,
) -> None:
    """Update latest option quote fields and record IV history when available."""
    now = _dt.datetime.now(ET).isoformat()
    conn = db()
    conn.execute(
        """
        UPDATE options_positions
        SET current_price=?, market_value=?, day_pnl=?, total_pnl=?,
            iv=?, delta=?, gamma=?, theta=?, vega=?, last_updated=?
        WHERE account_id=? AND symbol=?
        """,
        (
            round(price, 4),
            round(market_value, 2),
            round(day_pnl, 2),
            round(total_pnl, 2) if total_pnl is not None else None,
            iv,
            delta,
            gamma,
            theta,
            vega,
            now,
            acct_id,
            symbol,
        ),
    )
    if iv is not None:
        match = OCC_RE.match(symbol)
        underlying = match.group(1) if match else symbol
        conn.execute(
            "INSERT INTO iv_history (account_id, timestamp, symbol, underlying, iv) "
            "VALUES (?,?,?,?,?)",
            (
                acct_id,
                _dt.datetime.now(_dt.timezone.utc).isoformat(),
                symbol,
                underlying,
                iv,
            ),
        )
    conn.commit()
    conn.close()


def update_option_unit_cost(
    acct_id: str,
    symbol: str,
    *,
    unit_cost: float,
    total_pnl: float | None,
) -> None:
    """Update FIFO-derived unit cost and dependent total P&L."""
    conn = db()
    conn.execute(
        """
        UPDATE options_positions
        SET unit_cost=?, total_pnl=?, last_updated=?
        WHERE account_id=? AND symbol=?
        """,
        (
            round(unit_cost, 4),
            round(total_pnl, 2) if total_pnl is not None else None,
            _dt.datetime.now(ET).isoformat(),
            acct_id,
            symbol,
        ),
    )
    conn.commit()
    conn.close()


def replace_realized_trades_and_fifo_costs(
    acct_id: str,
    *,
    realized: list[dict],
    fifo_costs: dict,
) -> None:
    """Replace realized option trades and update FIFO-derived open costs."""
    symbol_realized_pnl: dict[str, float] = {}
    for row in realized:
        symbol = row["symbol"]
        symbol_realized_pnl[symbol] = symbol_realized_pnl.get(symbol, 0.0) + row["realized_pnl"]

    now = _dt.datetime.now(ET).isoformat()
    conn = db()
    try:
        conn.execute("DELETE FROM option_realized_trades WHERE account_id=?", (acct_id,))
        for row in realized:
            conn.execute(
                """
                INSERT INTO option_realized_trades
                  (account_id, underlying, symbol, strategy_type, lot_direction,
                   open_date, close_date, holding_days, quantity, open_cash, close_cash,
                   realized_pnl, return_on_risk, win_loss, option_type, expiry, strike, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    acct_id,
                    row["underlying"],
                    row["symbol"],
                    row["strategy_type"],
                    row["lot_direction"],
                    row["open_date"],
                    row["close_date"],
                    row["holding_days"],
                    row["quantity"],
                    row["open_cash"],
                    row["close_cash"],
                    row["realized_pnl"],
                    row["return_on_risk"],
                    row["win_loss"],
                    row["option_type"],
                    row["expiry"],
                    row["strike"],
                    now,
                ),
            )

        for symbol, unit_cost in fifo_costs.items():
            realized_pnl = symbol_realized_pnl.get(symbol)
            pos = conn.execute(
                "SELECT current_price, quantity FROM options_positions "
                "WHERE account_id=? AND symbol=?",
                (acct_id, symbol),
            ).fetchone()
            total_pnl = None
            if pos and pos["current_price"] is not None:
                total_pnl = round(
                    (float(pos["current_price"]) - unit_cost)
                    * int(pos["quantity"] or 0)
                    * 100,
                    2,
                )
            conn.execute(
                """
                UPDATE options_positions
                SET unit_cost=?, realized_pnl=?, total_pnl=?, last_updated=?
                WHERE account_id=? AND symbol=?
                """,
                (
                    unit_cost,
                    round(realized_pnl, 2) if realized_pnl is not None else None,
                    total_pnl,
                    now,
                    acct_id,
                    symbol,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_realized_trades(acct_id: str) -> pd.DataFrame:
    conn = db()
    df = pd.read_sql_query(
        "SELECT underlying, symbol, strategy_type, combo_strategy, lot_direction, "
        "open_date, close_date, holding_days, quantity, open_cash, close_cash, "
        "realized_pnl, return_on_risk, win_loss, option_type, expiry, strike "
        "FROM option_realized_trades "
        "WHERE account_id=? ORDER BY close_date DESC",
        conn,
        params=(acct_id,),
    )
    conn.close()
    return df


def save_portfolio_greeks_snapshot(
    acct_id: str,
    *,
    total_delta: float,
    total_gamma: float,
    total_theta: float,
    total_vega: float,
    n_contracts: int,
) -> None:
    conn = db()
    conn.execute(
        """
        INSERT INTO portfolio_greeks_history
          (account_id, timestamp, total_delta, total_gamma, total_theta, total_vega, n_contracts)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            acct_id,
            _dt.datetime.now(ET).isoformat(),
            total_delta,
            total_gamma,
            total_theta,
            total_vega,
            n_contracts,
        ),
    )
    conn.commit()
    conn.close()


def load_latest_portfolio_greeks_snapshot(acct_id: str):
    conn = db()
    row = conn.execute(
        "SELECT total_delta, n_contracts FROM portfolio_greeks_history "
        "WHERE account_id=? ORDER BY timestamp DESC LIMIT 1",
        (acct_id,),
    ).fetchone()
    conn.close()
    return row
