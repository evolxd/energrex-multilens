"""Broker import parsing helpers.

These helpers are intentionally side-effect free. They can be reused by the
Streamlit account monitor, CLI import scripts, and future tests without opening
the account database.
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import re
import shutil
from typing import Any

import pandas as pd


def parse_money(value: Any) -> float | None:
    """Parse broker money/percent cells such as `$1,234`, `(15.2)`, or `--`."""
    if value is None or str(value).strip() in ("", "--", "N/A", "n/a", "nan"):
        return None
    text = (
        str(value)
        .replace("$", "")
        .replace(",", "")
        .replace("+", "")
        .replace("%", "")
        .strip()
    )
    negative = text.startswith("(") or text.startswith("-")
    text = text.replace("(", "").replace(")", "").replace("-", "").strip()
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    return -parsed if negative else parsed


def parse_date(value: Any) -> str | None:
    """Parse common Firstrade/export date formats into ISO `YYYY-MM-DD`."""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return _dt.datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except (TypeError, ValueError):
            pass
    return None


def detect_csv_type(df: pd.DataFrame) -> str:
    """Return `positions`, `transactions`, or `unknown` for a broker CSV."""
    normalized_cols = {str(c).lower().strip() for c in df.columns}
    raw_cols = {str(c).strip() for c in df.columns}

    position_signals = {
        "shares",
        "share price",
        "quantity",
        "market value",
        "unrealized gain/loss",
        "cost basis",
        "avg cost",
    }
    transaction_signals = {
        "action",
        "run date",
        "trade date",
        "activity",
        "net amount",
        "settlement date",
        "commission",
    }

    chinese_transaction_signals = {"日期", "交易类别", "金额", "说明", "代码"}
    chinese_position_signals = {"市值", "持仓", "成本", "持股数", "未实现"}

    cn_transaction_signals = {"日期", "交易类别", "金额", "说明", "代码"}
    cn_position_signals = {"市值", "持仓", "成本", "持股数", "未实现"}

    if len(raw_cols & cn_transaction_signals) >= 3:
        return "transactions"
    if len(raw_cols & cn_position_signals) >= 2:
        return "positions"
    if len(raw_cols & chinese_transaction_signals) >= 3:
        return "transactions"
    if len(raw_cols & chinese_position_signals) >= 2:
        return "positions"
    if len(normalized_cols & position_signals) >= 2:
        return "positions"
    if len(normalized_cols & transaction_signals) >= 2:
        return "transactions"
    return "unknown"


def parse_positions_csv_rows(df: pd.DataFrame) -> list[dict]:
    """Parse a broker positions DataFrame into normalized position rows."""
    col = {str(c).lower().strip(): c for c in df.columns}

    def get_value(row, *keys: str) -> str:
        for key in keys:
            if key in col:
                value = row.get(col[key], "")
                if pd.notna(value) and str(value).strip():
                    return str(value).strip()
        return ""

    rows: list[dict] = []
    for _, row in df.iterrows():
        symbol = get_value(row, "symbol", "ticker").upper()
        if not symbol or symbol in ("SYMBOL", "TOTAL"):
            continue
        desc = get_value(row, "description", "security name", "name")
        is_option = bool(re.search(r"\d{6}[CP]\d+", symbol)) or (
            "call" in desc.lower() or "put" in desc.lower()
        )
        rows.append({
            "symbol": symbol,
            "position_type": "option" if is_option else "stock",
            "quantity": parse_money(get_value(row, "shares", "quantity", "qty")),
            "cost_basis": parse_money(
                get_value(row, "cost basis", "avg cost", "average cost")
            ),
            "market_value": parse_money(
                get_value(row, "market value", "current value", "value")
            ),
            "unrealized_pnl": parse_money(
                get_value(row, "unrealized gain/loss", "gain/loss", "unrealized gain")
            ),
            "unrealized_pnl_pct": parse_money(
                get_value(row, "% gain/loss", "gain/loss %", "unrealized gain/loss %")
            ),
            "description": desc,
        })
    return rows


ZH_ALIAS = {
    "trade date": "日期",
    "date": "日期",
    "action": "交易类别",
    "type": "交易类别",
    "quantity": "数量",
    "description": "说明",
    "symbol": "代码",
    "price": "价格",
    "amount": "金额",
}

ZH_TYPE_MAP = {
    "卖出开仓": "SELL TO OPEN",
    "买进开仓": "BUY TO OPEN",
    "卖出平仓": "SELL TO CLOSE",
    "买进平仓": "BUY TO CLOSE",
    "卖出": "SELL",
    "买进": "BUY",
    "股息": "DIVIDEND",
    "存入": "DEPOSIT",
    "取出": "WITHDRAWAL",
    "转入": "TRANSFER IN",
    "转出": "TRANSFER OUT",
    "到期": "EXPIRED",
    "利息收入": "INTEREST",
    "利息": "INTEREST",
    "其他": "OTHER",
    "手续费": "FEE",
    "交易费": "FEE",
    "期权到期": "OPTION EXPIRED",
    "行权": "EXERCISE",
    "被行权": "ASSIGNED",
}


def parse_transactions_csv_rows(df: pd.DataFrame) -> list[dict]:
    """Parse a broker transactions DataFrame into normalized transaction rows."""
    col = {str(c).lower().strip(): c for c in df.columns}

    def get_value(row, *keys: str) -> str:
        for key in keys:
            if key in col:
                value = row.get(col[key], "")
                if pd.notna(value) and str(value).strip():
                    return str(value).strip()
            zh = ZH_ALIAS.get(key)
            if zh and zh in df.columns:
                value = row.get(zh, "")
                if pd.notna(value) and str(value).strip():
                    return str(value).strip()
        return ""

    rows: list[dict] = []
    for _, row in df.iterrows():
        trade_date = parse_date(
            get_value(row, "trade date", "run date", "date", "transaction date")
        )
        raw_type = get_value(row, "action", "type", "activity type", "transaction type")
        txn_type = ZH_TYPE_MAP.get(raw_type.strip(), raw_type.upper())
        if not trade_date and not txn_type:
            continue
        rows.append({
            "trade_date": trade_date,
            "settlement_date": parse_date(get_value(row, "settlement date")) or "",
            "type": txn_type,
            "symbol": get_value(row, "symbol", "ticker").upper(),
            "description": get_value(row, "description", "security description", "activity"),
            "quantity": parse_money(get_value(row, "quantity", "shares", "qty")),
            "price": parse_money(get_value(row, "price")),
            "amount": parse_money(get_value(row, "amount", "net amount", "total")),
        })
    return rows


def import_positions_csv(
    df: pd.DataFrame,
    acct_id: str,
    *,
    save_positions,
    save_balance,
) -> int:
    rows = parse_positions_csv_rows(df)
    if rows:
        save_positions(acct_id, rows)
        total_market_value = sum(row["market_value"] or 0 for row in rows)
        if total_market_value > 0:
            save_balance(acct_id, {"total_equity": total_market_value})
    return len(rows)


def import_transactions_csv(
    df: pd.DataFrame,
    acct_id: str,
    *,
    save_transactions,
) -> int:
    rows = parse_transactions_csv_rows(df)
    if rows:
        save_transactions(acct_id, rows)
    return len(rows)


def process_csv_file(
    src: pathlib.Path,
    *,
    acct_id: str,
    latest_csv: pathlib.Path,
    save_positions,
    save_transactions,
    save_balance,
    logger=None,
) -> dict:
    """Read, classify, parse, and persist a broker CSV via supplied callbacks."""
    dest = latest_csv
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
        if logger:
            logger.info(f"Copied {src.name} -> {dest}")
    elif logger:
        logger.info(f"Using existing {dest}")

    df = None
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            df = pd.read_csv(dest, encoding=enc, thousands=",")
            break
        except Exception:
            pass
    if df is None or df.empty:
        return {"ok": False, "reason": "unable to read CSV"}

    df = df.dropna(how="all")
    csv_type = detect_csv_type(df)
    if logger:
        logger.info(f"CSV type detected: {csv_type} ({len(df)} rows)")

    if csv_type == "positions":
        count = import_positions_csv(
            df,
            acct_id,
            save_positions=save_positions,
            save_balance=save_balance,
        )
        return {"ok": True, "type": "positions", "rows": count, "file": src.name}
    if csv_type == "transactions":
        count = import_transactions_csv(
            df,
            acct_id,
            save_transactions=save_transactions,
        )
        return {"ok": True, "type": "transactions", "rows": count, "file": src.name}

    position_count = import_positions_csv(
        df,
        acct_id,
        save_positions=save_positions,
        save_balance=save_balance,
    )
    transaction_count = import_transactions_csv(
        df,
        acct_id,
        save_transactions=save_transactions,
    )
    return {
        "ok": True,
        "type": "auto",
        "rows": position_count + transaction_count,
        "file": src.name,
    }
