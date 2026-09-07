"""Regression tests for account/importers.py's Chinese-locale Firstrade
transactions CSV path.

Both bugs fixed here (2026-09-06) were discovered by re-auditing why the
"交易绩效" tab showed no closed trades after 2026-06-15 despite the user
trading daily: parse_transactions_csv_rows() silently produced
trade_date=None and symbol="" for essentially every row of the real
downloaded CSV (817/1881 and 973/1881 rows respectively in the live DB),
because (1) no date format tried "%d/%m/%Y" even though this export is
DD/MM/YYYY, not MM/DD/YYYY, and (2) ZH_ALIAS mapped "symbol" to the
Chinese header "代码", which no real export ever uses -- every sample
file (spanning 2025-06 through 2026-07) uses "代号" instead.
"""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from account.importers import (
    ZH_ALIAS,
    detect_csv_type,
    infer_dayfirst_from_zh_dates,
    parse_date,
    parse_transactions_csv_rows,
)


def _en_df(rows):
    """Build a DataFrame with Firstrade's English-UI account-history
    header: Date,Transaction,Quantity,Description,Symbol,Account Type,
    Price,Amount. Discovered 2026-09-06: same site, same underlying data,
    just not translated -- the automation Chrome happened to render in
    English for this download instead of Chinese."""
    cols = ["Date", "Transaction", "Quantity", "Description", "Symbol",
            "Account Type", "Price", "Amount"]
    return pd.DataFrame(rows, columns=cols)


def _zh_df(rows):
    """Build a DataFrame with the real Chinese Firstrade transactions
    header, in column order, from (日期,交易类别,数量,说明,代号,账户类别,价格,金额)
    tuples."""
    cols = ["日期", "交易类别", "数量", "说明", "代号", "账户类别", "价格", "金额"]
    return pd.DataFrame(rows, columns=cols)


# ── parse_date: dayfirst bug ────────────────────────────────────────────

def test_parse_date_defaults_to_month_first_unchanged():
    """Every other caller (positions CSV, English-locale exports) must see
    identical behavior to before this fix."""
    assert parse_date("07/14/2026") == "2026-07-14"   # unambiguous M/D
    assert parse_date("03/05/2026") == "2026-03-05"   # ambiguous, M/D wins


def test_parse_date_dayfirst_handles_unambiguous_day_over_12():
    """This is the case that silently returned None before the fix --
    "%m/%d/%Y" can never match day=14 as a month."""
    assert parse_date("14/07/2026", dayfirst=True) == "2026-07-14"
    assert parse_date("31/12/2025", dayfirst=True) == "2025-12-31"


def test_parse_date_dayfirst_fixes_the_silent_swap_case():
    """This is the more dangerous case: day<=12 used to parse "successfully"
    with day and month silently swapped, no error raised."""
    assert parse_date("07/08/2026", dayfirst=True) == "2026-08-07"   # Aug 7, not Jul 8
    assert parse_date("06/12/2026", dayfirst=True) == "2026-12-06"   # Dec 6, not Jun 12


def test_parse_date_dayfirst_still_accepts_iso_and_other_formats():
    assert parse_date("2026-07-14", dayfirst=True) == "2026-07-14"


# ── infer_dayfirst_from_zh_dates: the format isn't even constant ───────
#
# Confirmed against real downloaded files (2026-09-06): Firstrade's own
# Chinese-locale export changed date order between two real downloads --
# a mid-June 2026 file uses M/D/YYYY, a mid-July 2026 file uses D/M/YYYY.
# A single hardcoded convention silently produced trade dates months in
# the future for whichever file didn't match it.

def test_unambiguous_day_over_12_forces_dayfirst_true():
    assert infer_dayfirst_from_zh_dates(["14/07/2026", "6/12/2026"]) is True


def test_unambiguous_second_component_over_12_forces_dayfirst_false():
    assert infer_dayfirst_from_zh_dates(["6/12/2026", "12/25/2026"]) is False


def test_fully_ambiguous_batch_falls_back_to_no_future_dates():
    """Real case: one sampled file (export (1).csv) has day<=12 in every
    single one of its 38 rows -- "6/12/2026" etc. Both readings are
    individually valid dates; only one keeps every date <= today."""
    values = ["6/12/2026", "6/11/2026", "6/10/2026", "6/9/2026"]
    # If today is 2026-09-06, day-first would read "6/12" as Dec 6 (future);
    # month-first reads it as Jun 12 (real, in the past). Must pick month-first.
    today = __import__("datetime").date(2026, 9, 6)
    assert infer_dayfirst_from_zh_dates(values, today=today) is False


def test_fully_ambiguous_batch_prefers_dayfirst_when_neither_is_future():
    """When the tiebreaker itself can't discriminate (both readings land
    safely in the past), default to day-first -- the more recently
    observed convention."""
    today = __import__("datetime").date(2030, 1, 1)
    values = ["6/12/2026"]
    assert infer_dayfirst_from_zh_dates(values, today=today) is True


# ── ZH_ALIAS / symbol column name ───────────────────────────────────────

def test_zh_alias_symbol_maps_to_the_real_column_name():
    """代号, not 代码 -- every real sampled export (2025-06 through 2026-07)
    uses 代号; this assertion pins the regression directly, not just via
    the parsing behavior below."""
    assert ZH_ALIAS["symbol"] == "代号"


def test_detect_csv_type_recognizes_the_real_chinese_header():
    df = _zh_df([("14/07/2026", "买进开仓", "1", "desc", "MU270115C01190000",
                   "融资", "182.78", "-18,278.02")])
    assert detect_csv_type(df) == "transactions"


# ── parse_transactions_csv_rows: end-to-end against real-shaped rows ────

def test_option_open_trade_gets_real_date_and_symbol():
    df = _zh_df([
        ("14/07/2026", "买进开仓", "1",
         "MU 01/15/2027 1190.000 C Micron Technology Inc.",
         "MU270115C01190000", "融资", "182.78", "-18,278.02"),
    ])
    rows = parse_transactions_csv_rows(df)
    assert len(rows) == 1
    r = rows[0]
    assert r["trade_date"] == "2026-07-14"
    assert r["symbol"] == "MU270115C01190000"
    assert r["type"] == "BUY TO OPEN"
    assert r["amount"] == pytest.approx(-18278.02)


def test_stock_close_trade_with_ambiguous_date_follows_the_batch_convention():
    """One row's date is genuinely ambiguous (06/12) on its own; a sibling
    row in the same batch is unambiguous (14/07 -> day-first) and must
    settle the whole file's convention -- the old bug swapped day/month
    silently, per-row, with no such batch-level consistency check at all."""
    df = _zh_df([
        ("14/07/2026", "买进开仓", "1", "anchor row, unambiguous day-first",
         "MU270115C01190000", "融资", "1.00", "-100.00"),
        ("06/12/2026", "卖出", "-5",
         "GITLAB INC CLASS A COMMON STOCK UNSOLICITED EXEC TIME: 2026-12-06 12:10:15 S/D: 12/07/2026",
         "GTLB", "融资", "33.70", "168.49"),
    ])
    rows = parse_transactions_csv_rows(df)
    gtlb = next(r for r in rows if r["symbol"] == "GTLB")
    assert gtlb["trade_date"] == "2026-12-06"   # day-first: day=06, month=12


def test_dividend_row_gets_its_symbol_not_blank():
    df = _zh_df([
        ("30/06/2026", "股息", "0",
         "VISTRA CORP COMMON STOCK CASH DIV ON 10 SHS REC 06/22/26 PAY 06/30/26",
         "VST", "融资", "0.00", "2.29"),
    ])
    r = parse_transactions_csv_rows(df)[0]
    assert r["trade_date"] == "2026-06-30"
    assert r["symbol"] == "VST"
    assert r["type"] == "DIVIDEND"


def test_margin_interest_row_legitimately_has_no_symbol():
    """Not every row has a symbol -- margin interest/interest income rows
    genuinely leave 代号 blank in the source data. This must stay blank,
    not get some fabricated placeholder."""
    df = _zh_df([
        ("16/06/2026", "融资利息费用", "0",
         "FROM 05/16 THRU 06/15 @12 % BAL 20,627- AVBAL 492",
         "", "融资", "0.00", "-5.09"),
    ])
    r = parse_transactions_csv_rows(df)[0]
    assert r["trade_date"] == "2026-06-16"
    assert r["symbol"] == ""
    # "融资利息费用" isn't in ZH_TYPE_MAP (that's a separate, pre-existing
    # gap, not part of this fix) -- it passes through as the raw Chinese
    # string. Not asserting a specific mapped type here on purpose.
    assert r["type"]


# ── English-UI Firstrade export: a third real format variant ───────────
#
# Discovered 2026-09-06 while re-auditing the sync gap the user flagged:
# the exact same site, logged into the exact same account, produced this
# entirely different column-name shape depending on the browser's UI
# language at download time (Date/Transaction/Quantity/Description/
# Symbol/Account Type/Price/Amount, generic Bought/Sold with no Open/
# Close qualifier). A live "Year to date" download (685 rows, 2026-01-02
# through 2026-09-04) is what surfaced this -- it wasn't hypothetical.

def test_detect_csv_type_recognizes_the_english_header():
    df = _en_df([("04/09/2026", "Bought", "50", "2x Ether ETF", "ETHU",
                   "Margin", "26.00", "-1,300.00")])
    assert detect_csv_type(df) == "transactions"


def test_english_stock_buy_gets_real_date_and_symbol():
    df = _en_df([
        ("04/09/2026", "Bought", "50", "2x Ether ETF", "ETHU",
         "Margin", "26.00", "-1,300.00"),
    ])
    r = parse_transactions_csv_rows(df)[0]
    assert r["trade_date"] == "2026-09-04"   # day-first, same as the Chinese export
    assert r["symbol"] == "ETHU"
    assert r["type"] == "BUY"
    assert r["amount"] == pytest.approx(-1300.00)


def test_english_option_row_maps_bought_sold_to_buy_sell_not_bought_sold():
    """Bought/Sold must become BUY/SELL exactly -- account/fifo.py's
    GENERIC_TYPES only recognizes those two literal strings; "BOUGHT"
    (raw_type.upper()'s fallback, pre-fix) would silently match nothing
    and drop the trade from FIFO matching entirely."""
    df = _en_df([
        ("03/09/2026", "Bought", "1",
         "CALL AVGO 06/17/27 380 BROADCOM INC UNSOLICITED OPEN CONTRACT S/D: 09/04/2026",
         "AVGO270617C00380000", "Margin", "42.80", "-4,280.02"),
        ("03/09/2026", "Sold", "-1",
         "CALL AVGO 06/17/27 560 BROADCOM INC UNSOLICITED OPEN CONTRACT S/D: 09/04/2026",
         "AVGO270617C00560000", "Margin", "11.40", "1,139.95"),
    ])
    rows = parse_transactions_csv_rows(df)
    assert rows[0]["type"] == "BUY"
    assert rows[1]["type"] == "SELL"


def test_english_dividend_and_interest_rows_map_correctly():
    df = _en_df([
        ("30/06/2026", "Dividend", "0", "VISTRA CORP CASH DIV", "VST",
         "Margin", "0.00", "2.29"),
        ("16/06/2026", "Margin Interest", "0", "FROM 05/16 THRU 06/15", "",
         "Margin", "0.00", "-5.09"),
    ])
    rows = parse_transactions_csv_rows(df)
    assert rows[0]["type"] == "DIVIDEND" and rows[0]["symbol"] == "VST"
    assert rows[1]["type"] == "INTEREST" and rows[1]["symbol"] == ""


def test_english_batch_infers_dayfirst_from_its_own_dates_too():
    """Same per-batch inference as the Chinese path -- an unambiguous date
    (day > 12) in the English file settles the whole batch."""
    df = _en_df([
        ("14/07/2026", "Bought", "1", "unambiguous anchor row", "MU270115C01190000",
         "Margin", "1.00", "-100.00"),
        ("06/12/2026", "Sold", "-5", "ambiguous row", "GTLB",
         "Margin", "33.70", "168.49"),
    ])
    rows = parse_transactions_csv_rows(df)
    gtlb = next(r for r in rows if r["symbol"] == "GTLB")
    assert gtlb["trade_date"] == "2026-12-06"   # day-first: day=06, month=12
