"""Stock positions must come off the Firstrade xlsx, not go missing.

The bug this covers: `_parse_positions_xlsx()` only ever read the options
sheet, and its caller decided whether to fall back to the stock-scraping JS
path with `if not new_rows:`. Any account holding options made that condition
false forever, so the stock table stayed frozen at whatever the last
`export*.csv` import wrote. Measured impact on the real account: the hard-limit
dashboard reported a 15.4% largest single position with headroom to spare while
the actual largest was 19.6%, over the 18% cap.

Parsing lives in `account/positions_xlsx.py` rather than `account_monitor.py`
because importing the latter executes a whole Streamlit page.

The sheet layout here mirrors a real Firstrade export -- including its mixed
cell types (Market Value float, Total Cost comma string, % Gain/Loss percent
string) -- but the holdings are invented.
"""

import sys
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


STOCK_HEADER = [
    "Symbol", "Quantity", "Actions", "Description", "Last Price", "$ Chg",
    "% Chg", "Bid", "Bid Size", "Ask", "Ask Size", "Volume", "Prev Close",
    "Day Open", "Day High", "Day Low", "52WK High", "52WK Low",
    "Market Value", "Day Chg $", "Unit Cost", "Total Cost", "$ Gain/Loss",
    "% Gain/Loss",
]
OPTION_HEADER = ["Symbol", "Description", "Quantity", "Last Price", "Market Value"]


def _stock_row(symbol, qty, description, market_value, unit_cost, total_cost,
               gain, gain_pct):
    row = [None] * len(STOCK_HEADER)
    row[0], row[1], row[3] = symbol, qty, description
    row[18], row[20], row[21] = market_value, unit_cost, total_cost
    row[22], row[23] = gain, gain_pct
    return row


@pytest.fixture
def firstrade_xlsx(tmp_path):
    wb = openpyxl.Workbook()
    stocks = wb.active
    stocks.title = "Stocks ETFs"
    stocks.append(STOCK_HEADER)
    # Mixed cell types on purpose: this is how Firstrade actually exports.
    stocks.append(_stock_row("AMD", 2, "Advanced Micro Devices Inc.",
                             993.44, 518, "1,036.00", -42.56, "-4.11%"))
    stocks.append(_stock_row("ETHU", 350, "2x Ether ETF",
                             9694.97, 24.5, 8575.0, 1119.97, "13.06%"))
    stocks.append(_stock_row("VST", 20, "Vistra Corp.",
                             2819.40, 120.0, 2400.0, 419.40, "17.47%"))

    options = wb.create_sheet("Options")
    options.append(OPTION_HEADER)
    options.append(["NVDA270617C00230000", "NVIDIA Corporation", 1, 24.45, 2445.0])

    path = tmp_path / "positions.xlsx"
    wb.save(path)
    return path


def _parse(path):
    from account.positions_xlsx import parse_stocks_xlsx
    return parse_stocks_xlsx(path)


def test_every_stock_row_is_parsed(firstrade_xlsx):
    rows = _parse(firstrade_xlsx)
    assert [r["symbol"] for r in rows] == ["AMD", "ETHU", "VST"]


def test_mixed_cell_types_are_all_normalised_to_numbers(firstrade_xlsx):
    amd = next(r for r in _parse(firstrade_xlsx) if r["symbol"] == "AMD")
    assert amd["quantity"] == 2
    assert amd["market_value"] == pytest.approx(993.44)
    assert amd["cost_basis"] == pytest.approx(1036.00)      # "1,036.00" string
    assert amd["unrealized_pnl"] == pytest.approx(-42.56)
    assert amd["unrealized_pnl_pct"] == pytest.approx(-4.11)  # "-4.11%" string
    assert amd["position_type"] == "stock"
    assert amd["description"] == "Advanced Micro Devices Inc."


def test_option_rows_do_not_leak_into_the_stock_table(firstrade_xlsx):
    symbols = [r["symbol"] for r in _parse(firstrade_xlsx)]
    assert not any(s.startswith("NVDA27") for s in symbols)


def test_missing_stock_sheet_yields_nothing_rather_than_raising(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Options"
    wb.active.append(OPTION_HEADER)
    path = tmp_path / "options_only.xlsx"
    wb.save(path)
    assert _parse(path) == []


def test_closed_positions_get_a_zero_row_so_they_drop_out_of_exposure():
    from account.positions_xlsx import stock_snapshot_rows
    parsed = [{"symbol": "AMD", "position_type": "stock", "quantity": 2,
               "cost_basis": 1036.0, "market_value": 993.44,
               "unrealized_pnl": -42.56, "unrealized_pnl_pct": -4.11,
               "description": "Advanced Micro Devices Inc."}]

    rows = stock_snapshot_rows(parsed, previous_symbols=["AMD", "KLAC"])

    klac = next(r for r in rows if r["symbol"] == "KLAC")
    assert klac["quantity"] == 0
    assert klac["market_value"] == 0
    # compute_exposures() filters zero net value, so a sold-out name stops
    # counting toward single-stock and chain concentration.
    from scoring.position_exposure import compute_exposures
    exposures = compute_exposures(rows, total_equity=50_000, cash_balance=5_000,
                                  category_of=lambda s: "AI芯片")
    assert "KLAC" not in exposures.by_ticker_pct
    assert "AMD" in exposures.by_ticker_pct


def test_snapshot_keeps_every_current_holding():
    from account.positions_xlsx import stock_snapshot_rows
    parsed = [{"symbol": s, "position_type": "stock", "quantity": 1,
               "cost_basis": 1.0, "market_value": 1.0, "unrealized_pnl": 0.0,
               "unrealized_pnl_pct": 0.0, "description": s}
              for s in ("AMD", "ETHU", "VST")]
    rows = stock_snapshot_rows(parsed, previous_symbols=[])
    assert {r["symbol"] for r in rows} == {"AMD", "ETHU", "VST"}


# ── options sheet ────────────────────────────────────────────────────────────

def test_options_are_parsed_with_signed_quantities(firstrade_xlsx):
    from account.positions_xlsx import parse_options_xlsx
    rows = parse_options_xlsx(firstrade_xlsx)
    assert [r["symbol"] for r in rows] == ["NVDA270617C00230000"]
    leg = rows[0]
    assert leg["direction"] == "long"
    assert leg["quantity"] == 1
    assert leg["underlying"] == "NVDA"
    assert leg["strike"] == 230.0
    assert leg["expiry"] == "2027-06-17"


def test_short_legs_keep_their_negative_quantity(tmp_path):
    from account.positions_xlsx import parse_options_xlsx
    wb = openpyxl.Workbook()
    wb.active.title = "Options"
    wb.active.append(OPTION_HEADER)
    wb.active.append(["AVGO261016P00330000", "Broadcom Inc.", -3, 7.6, -2280.0])
    path = tmp_path / "short.xlsx"
    wb.save(path)

    leg = parse_options_xlsx(path)[0]
    assert leg["quantity"] == -3
    assert leg["direction"] == "short"
    assert leg["market_value"] == -2280.0


def test_stock_rows_do_not_leak_into_the_options_table(firstrade_xlsx):
    from account.positions_xlsx import parse_options_xlsx
    symbols = [r["symbol"] for r in parse_options_xlsx(firstrade_xlsx)]
    assert "AMD" not in symbols and "ETHU" not in symbols


def test_missing_options_sheet_yields_nothing(tmp_path):
    from account.positions_xlsx import parse_options_xlsx
    wb = openpyxl.Workbook()
    wb.active.title = "Stocks ETFs"
    wb.active.append(STOCK_HEADER)
    path = tmp_path / "stocks_only.xlsx"
    wb.save(path)
    assert parse_options_xlsx(path) == []
