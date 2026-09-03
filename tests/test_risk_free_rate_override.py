from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
from scoring_engine import (
    CompanyCategory,
    _EQUITY_RISK_PREMIUM,
    _RISK_FREE_RATE,
    _resolve_risk_free_rate,
    calc_damodaran_report,
    calc_wacc,
)


def test_resolve_risk_free_rate_falls_back_to_constant_when_absent():
    assert _resolve_risk_free_rate({}) == pytest.approx(_RISK_FREE_RATE)


def test_resolve_risk_free_rate_prefers_injected_live_value():
    assert _resolve_risk_free_rate({"_risk_free_rate": 0.0425}) == pytest.approx(0.0425)


def test_calc_wacc_unchanged_when_no_override_present():
    data = {"beta": 1.5, "net_debt": 0, "market_cap": 1e9}
    expected = round(_RISK_FREE_RATE + 1.5 * _EQUITY_RISK_PREMIUM, 4)
    assert calc_wacc(data, CompanyCategory.AI_CHIP) == pytest.approx(expected)


def test_calc_wacc_uses_live_override_when_present():
    data = {"beta": 1.5, "net_debt": 0, "market_cap": 1e9, "_risk_free_rate": 0.05}
    expected = round(0.05 + 1.5 * _EQUITY_RISK_PREMIUM, 4)
    assert calc_wacc(data, CompanyCategory.AI_CHIP) == pytest.approx(expected)


def test_calc_wacc_with_debt_also_uses_live_override_for_cost_of_debt():
    base = {"beta": 1.0, "net_debt": 4e8, "market_cap": 6e8}
    wacc_default = calc_wacc(base, CompanyCategory.SEMI_EQUIP)
    wacc_override = calc_wacc({**base, "_risk_free_rate": 0.08}, CompanyCategory.SEMI_EQUIP)
    assert wacc_override > wacc_default   # higher Rf raises both Ke and after-tax Kd


def test_calc_damodaran_report_cost_of_equity_uses_override():
    data = {"beta": 1.2, "roic": 0.15, "ev_sales": 10, "revenue_ttm": 1e8}
    report = calc_damodaran_report("TEST", {**data, "_risk_free_rate": 0.06}, CompanyCategory.AI_CHIP)
    expected = round(0.06 + 1.2 * _EQUITY_RISK_PREMIUM, 4)
    assert report["cost_of_equity"] == pytest.approx(expected)
