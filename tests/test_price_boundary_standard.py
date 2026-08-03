"""Required-output contract from PRICE_BOUNDARY_STANDARD.md.

The standard lists what every single-stock report must display. Nothing
enforced that list, so a layout pass silently dropped the price-shock
sensitivity table while _price_sensitivity_report went on computing the
numbers and throwing them away. The report stopped meeting its own
standard and stayed that way across several sessions.

These are text contracts over app.py rather than rendering tests: app.py is
a Streamlit script, so importing it would execute the page. That is the same
trade-off test_print_layout_contract.py already makes.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8-sig")
STANDARD = (ROOT / "PRICE_BOUNDARY_STANDARD.md").read_text(encoding="utf-8-sig")


def test_standard_still_demands_a_sensitivity_table():
    """If this requirement is ever dropped, drop the tests below with it."""
    assert "sensitivity table showing score changes under price shocks" in STANDARD


@pytest.mark.parametrize(
    "header",
    ["价格变化", "假设价格", "综合分档", "估值分", "Fwd PE", "EV/Sales"],
)
def test_sensitivity_table_renders_each_column(header):
    assert f">{header}</th>" in APP


def test_sensitivity_rows_are_displayed_not_just_computed():
    """The regression was a computed-then-discarded report, so assert the
    rows actually reach the page."""
    assert '_price_sensitivity_report(' in APP
    assert 'for _r in _ps["rows"]:' in APP
    assert "{_rows_html}" in APP


def test_sensitivity_headline_quantifies_a_ten_percent_move():
    assert "价格每变动 10%" in APP
    assert "sensitivity_10pct" in APP


@pytest.mark.parametrize(
    "ratio",
    ["Forward PE", "PEG", "EV/Sales", "EV/EBITDA", "FCF Yield"],
)
def test_methodology_names_every_recomputed_ratio(ratio):
    """Standard, 'Required Methodology Disclosure': the report must say which
    ratios each simulated price recomputes."""
    assert ratio in APP


def test_methodology_states_non_price_fundamentals_are_held_constant():
    """Accept either wording. The point is that the page says fundamentals
    are held fixed, not that it says so in one particular sentence; pinning
    the exact phrasing would make this fail on harmless copy edits."""
    assert any(
        phrase in APP
        for phrase in ("非价格基本面保持不变", "基本面假设不变的前提下")
    )


def test_report_does_not_claim_a_backtested_win_rate():
    """Standard: until the validation gates pass, the report must say the
    boundaries are not proven by backtested win rate."""
    assert "未声明为已回测验证胜率" in APP


def test_boundaries_are_not_labelled_as_trade_instructions():
    """Standard: 'Boundary labels are decision boundaries, not automatic
    trade instructions.'"""
    assert "不是自动交易指令" in APP
