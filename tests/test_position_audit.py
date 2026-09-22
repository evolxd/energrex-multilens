"""持仓行体检：符号、数量级、重复。

针对的是 2026-09-21 那张截图——BD 净多头 +356%，同一块面板上压力测试说跌
20% 赚 $98,640，旁边一个 $103,600 的权利金而账户净值 $54,158。每一步代码都
跑通了，错的是喂进去的行。
"""

import pytest

from account.position_audit import (
    Finding,
    audit_option_rows,
    rows_read_backwards,
    sign_flip_impact,
)

EQUITY = 54_158.0


def _codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


def _row(symbol, quantity, direction=None, unit_cost=None, current_price=None):
    return {"symbol": symbol, "quantity": quantity, "direction": direction,
            "unit_cost": unit_cost, "current_price": current_price}


def test_a_short_leg_stored_as_positive_qty_plus_direction_is_flagged():
    rows = [_row("AVGO260116P00335000", 2, "short", 12.0, 14.0)]
    findings = audit_option_rows(rows, equity=EQUITY)
    assert "SIGN_STORED_SPLIT" in _codes(findings)
    assert rows_read_backwards(rows) == rows


def test_an_already_signed_short_leg_is_not_flagged():
    """xlsx 导入存的就是带符号的张数，那是对的写法，不该报。"""
    rows = [_row("AVGO260116P00335000", -2, "short", 12.0, 14.0)]
    assert rows_read_backwards(rows) == []
    assert "SIGN_STORED_SPLIT" not in _codes(audit_option_rows(rows, equity=EQUITY))


def test_a_positive_row_whose_direction_says_call_cannot_be_verified():
    """手动录入写的是 Call/Put，那一列没有买卖信息。

    不能当成买入就放过——这时 quantity 的正负是唯一来源，写错了没有第二处
    能对出来。但也不能当成卖出去翻，那会把所有手工录入的买入腿翻反。
    """
    rows = [_row("AMD260116C00150000", 10, "Call", 103.60, 110.0)]
    codes = _codes(audit_option_rows(rows, equity=EQUITY))
    assert "SIDE_UNVERIFIABLE" in codes
    assert "SIGN_STORED_SPLIT" not in codes
    assert rows_read_backwards(rows) == []


def test_premium_larger_than_the_account_is_an_error():
    """$103,600 的已付权利金不可能出现在 $54,158 的账户里。"""
    rows = [_row("AMD260116C00150000", 10, "long", 103.60, 110.0)]
    findings = audit_option_rows(rows, equity=EQUITY)
    assert "PREMIUM_EXCEEDS_EQUITY" in _codes(findings)
    assert "103,600" in next(f for f in findings if f.code == "PREMIUM_EXCEEDS_EQUITY").detail


def test_a_normal_long_position_is_clean():
    rows = [_row("NVDA260116C00200000", 2, "long", 11.50, 13.20)]
    assert audit_option_rows(rows, equity=EQUITY) == []


def test_a_per_contract_cost_written_into_the_per_share_column_is_caught():
    """券商导出里 Cost 有时是整张的钱。差 100 倍，最大亏损跟着放大 100 倍。"""
    rows = [_row("NVDA260116C00200000", 1, "long", 1_150.0, 13.20)]
    codes = _codes(audit_option_rows(rows, equity=EQUITY))
    assert "COST_LOOKS_PER_CONTRACT" in codes


def test_ordinary_unrealised_gain_is_not_mistaken_for_a_unit_error():
    """翻了 5 倍的浮盈是真事，别报成写错单位——两者之间空得很开。"""
    rows = [_row("NVDA260116C00200000", 1, "long", 2.00, 10.00)]
    assert "COST_LOOKS_PER_CONTRACT" not in _codes(audit_option_rows(rows, equity=EQUITY))


def test_duplicate_symbols_are_reported_once_with_the_count():
    rows = [_row("NVDA260116C00200000", 1, "long", 11.5, 13.2),
            _row("NVDA260116C00200000", 1, "long", 11.5, 13.2)]
    findings = [f for f in audit_option_rows(rows, equity=EQUITY)
                if f.code == "DUPLICATE_SYMBOL"]
    assert len(findings) == 1
    assert "2 行" in findings[0].detail


def test_an_unparseable_symbol_stops_further_checks_on_that_row():
    """解析不出来就没有行权价和类型，后面的检查全是瞎算。"""
    findings = audit_option_rows([_row("NOT A SYMBOL", 1, "long", 5.0, 6.0)],
                                 equity=EQUITY)
    assert _codes(findings) == ["SYMBOL_NOT_OCC"]


def test_checks_against_equity_are_skipped_when_there_is_no_equity():
    """没有净值就没有可比的基准，报出来只是噪音。"""
    rows = [_row("AMD260116C00150000", 10, "long", 103.60, 110.0)]
    assert "PREMIUM_EXCEEDS_EQUITY" not in _codes(audit_option_rows(rows, equity=0))


def test_errors_sort_before_warnings_and_notes():
    rows = [_row("NVDA260116C00200000", 0, "long"),            # info
            _row("AMD260116C00150000", 10, "Call", 1.0, 1.0),  # warn
            _row("AVGO260116P00335000", 2, "short", 12.0, 14.0)]  # error
    sev = [f.severity for f in audit_option_rows(rows, equity=EQUITY)]
    assert sev == sorted(sev, key=["error", "warn", "info"].index)


def test_the_impact_summary_says_nothing_changed_when_nothing_is_wrong():
    """一行都没翻，就说明 BD 和压力的矛盾另有来源——别把这次修正当结论。"""
    rows = [_row("NVDA260116C00200000", 2, "long", 11.5, 13.2),
            _row("AVGO260116P00335000", -2, "short", 12.0, 14.0)]
    impact = sign_flip_impact(rows)
    assert impact["rows"] == 0
    assert impact["contracts"] == 0
    assert impact["strike_notional"] == 0


def test_the_impact_summary_adds_up_strike_notional_over_flipped_rows():
    rows = [_row("AVGO260116P00335000", 2, "short", 12.0, 14.0),
            _row("SMH260116P00500000", 1, "short", 20.0, 22.0),
            _row("NVDA260116C00200000", 3, "long", 11.5, 13.2)]
    impact = sign_flip_impact(rows)
    assert impact["rows"] == 2
    assert impact["contracts"] == 3
    assert impact["strike_notional"] == pytest.approx(335 * 2 * 100 + 500 * 1 * 100)
    assert impact["symbols"] == ["AVGO260116P00335000", "SMH260116P00500000"]
