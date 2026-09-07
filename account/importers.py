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


def parse_date(value: Any, dayfirst: bool = False) -> str | None:
    """Parse common Firstrade/export date formats into ISO `YYYY-MM-DD`.

    `dayfirst=True` is for Firstrade's Chinese-locale transactions export
    (the "日期" column). Confirmed against real downloaded files
    (2026-09-06): this convention is NOT constant -- files from mid-June
    2026 use M/D/YYYY (e.g. "6/12/2026" = June 12, matching that file's own
    mtime) while a file from mid-July 2026 uses D/M/YYYY (e.g. "14/07/2026"
    = July 14). Firstrade's own export apparently changed format sometime
    in between. Callers must decide `dayfirst` per file/batch --
    see `infer_dayfirst_from_zh_dates` -- not hardcode either direction.
    Before this fix, "%m/%d/%Y" was tried first unconditionally:
    unambiguous DD/MM dates (day > 12) failed every format in this list
    and silently became None, and ambiguous ones (day <= 12) silently
    parsed with day and month swapped -- no error
    either way, just a wrong or missing date. Every other caller keeps the
    exact same default behavior (dayfirst=False, "%m/%d/%Y" tried first).
    """
    slash_fmts = ("%d/%m/%Y", "%m/%d/%Y") if dayfirst else ("%m/%d/%Y", "%d/%m/%Y")
    for fmt in slash_fmts + ("%Y-%m-%d", "%b %d, %Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return _dt.datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except (TypeError, ValueError):
            pass
    return None


def infer_dayfirst_from_zh_dates(date_values, today: _dt.date | None = None) -> bool:
    """Decide day-first vs month-first for one batch of "日期"-column
    A/B/YYYY strings. Needed because this isn't a fixed convention -- see
    parse_date's docstring: different downloads of the same Firstrade
    Chinese-locale export have used different orders. Assumes one
    file/batch is internally consistent (true of every real sample seen
    so far).

    Two-stage decision:
      1. Any row that resolves the ambiguity on its own (a component > 12
         can only be the day) settles it for the whole batch.
      2. If every row is genuinely ambiguous (day and month both <= 12
         throughout -- a real case: one sampled file has this for all 38
         rows), fall back to "a real transaction history never contains a
         future trade": try both interpretations against every date in the
         batch and keep whichever produces no date after `today`. Only
         defaults outright to day-first (the more recently observed
         convention) if that test doesn't discriminate either.
    """
    for v in date_values:
        parts = str(v).strip().split("/")
        if len(parts) != 3:
            continue
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if a > 12:
            return True    # first component can only be a day -> day-first
        if b > 12:
            return False   # second component can only be a day -> month-first

    today = today or _dt.date.today()
    values = [str(v) for v in date_values]

    def _has_future_date(guess_dayfirst: bool) -> bool:
        for v in values:
            iso = parse_date(v, dayfirst=guess_dayfirst)
            if iso and _dt.date.fromisoformat(iso) > today:
                return True
        return False

    day_first_ok   = not _has_future_date(True)
    month_first_ok = not _has_future_date(False)
    if day_first_ok and not month_first_ok:
        return True
    if month_first_ok and not day_first_ok:
        return False
    return True


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

    # Firstrade's account-history export in English UI mode: same site,
    # same "Date/Transaction/Quantity/Description/Symbol/Account Type/
    # Price/Amount" columns as the Chinese-locale one, just not translated
    # -- confirmed 2026-09-06 against a real "Year to date" download.
    # Distinctive enough (5 columns, "account type" + "transaction"
    # together) not to collide with an unrelated English transactions CSV.
    firstrade_en_transaction_signals = {
        "date", "transaction", "description", "symbol", "account type",
    }

    if len(raw_cols & cn_transaction_signals) >= 3:
        return "transactions"
    if len(raw_cols & cn_position_signals) >= 2:
        return "positions"
    if len(raw_cols & chinese_transaction_signals) >= 3:
        return "transactions"
    if len(raw_cols & chinese_position_signals) >= 2:
        return "positions"
    if len(normalized_cols & firstrade_en_transaction_signals) >= 4:
        return "transactions"
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
    "symbol": "代号",   # every real export sampled (2025-06 through 2026-07)
                        # uses "代号"; "代码" here (until 2026-09-06) never
                        # matched any real column, so symbol silently came
                        # back blank for every Chinese-format transaction.
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
    # Firstrade's English-locale export uses the same "Transaction" column
    # but with generic Bought/Sold labels -- never "X to Open/Close" like
    # the Chinese-locale format's "买进开仓" etc. (confirmed 2026-09-06:
    # a real English "Year to date" download has zero open/close-qualified
    # labels across 685 rows, options included). account/fifo.py's
    # GENERIC_TYPES path already infers open vs. close from net position
    # state for plain BUY/SELL, so mapping straight to that (not inventing
    # a fake "BUY TO OPEN") is correct, not a downgrade.
    "Bought":          "BUY",
    "Sold":            "SELL",
    "Other":           "OTHER",
    "Dividend":        "DIVIDEND",
    "Interest":        "INTEREST",
    "Margin Interest": "INTEREST",
    "Withdraw":        "WITHDRAWAL",
    "Deposit":         "DEPOSIT",
    "被行权": "ASSIGNED",
}


def parse_transactions_csv_rows(df: pd.DataFrame) -> list[dict]:
    """Parse a broker transactions DataFrame into normalized transaction rows."""
    col = {str(c).lower().strip(): c for c in df.columns}
    is_zh = "日期" in df.columns          # Chinese-locale Firstrade export
    is_ft_en = not is_zh and {"date", "transaction", "account type"} <= set(col)
    date_col = "日期" if is_zh else (col.get("date") if is_ft_en else None)
    # Day-first vs month-first isn't fixed for this export, in either UI
    # language -- see infer_dayfirst_from_zh_dates's docstring -- decide it
    # once per batch from this file's own dates rather than assuming
    # either direction.
    dayfirst = infer_dayfirst_from_zh_dates(df[date_col]) if date_col else False

    def get_value(row, *keys: str) -> str:
        for key in keys:
            if key in col:
                value = row.get(col[key], "")
                if pd.notna(value) and str(value).strip():
                    return str(value).strip()
            zh = ZH_ALIAS.get(key)
            # 代号/代码：两种拼法都试一遍，不假设导出格式以后不会再变——
            # 之前只认"代码"一种拼法，而真实文件从头到尾用的都是"代号"，
            # 结果 symbol 一直是空的（见 2026-09-06 的修复记录）。
            zh_candidates = (zh,) if isinstance(zh, str) else (zh or ())
            if key == "symbol":
                zh_candidates = ("代号", "代码")
            for zh_col in zh_candidates:
                if zh_col and zh_col in df.columns:
                    value = row.get(zh_col, "")
                    if pd.notna(value) and str(value).strip():
                        return str(value).strip()
        return ""

    rows: list[dict] = []
    for _, row in df.iterrows():
        trade_date = parse_date(
            get_value(row, "trade date", "run date", "date", "transaction date"),
            dayfirst=dayfirst,
        )
        raw_type = get_value(row, "action", "type", "activity type", "transaction type", "transaction")
        txn_type = ZH_TYPE_MAP.get(raw_type.strip(), raw_type.upper())
        if not trade_date and not txn_type:
            continue
        rows.append({
            "trade_date": trade_date,
            "settlement_date": parse_date(get_value(row, "settlement date"), dayfirst=dayfirst) or "",
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
