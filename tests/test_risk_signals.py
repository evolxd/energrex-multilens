"""Tests for account/risk_signals.py -- pure scan functions for 纪律架构 v2.

docs/DISCIPLINE_AND_REVIEW_ARCHITECTURE.md §1/§4/§7.
"""
import datetime

import pandas as pd

from account import risk_signals as rs


# ── underlying_of ────────────────────────────────────────────────

def test_underlying_of_parses_occ_and_passes_through_plain_tickers():
    assert rs.underlying_of("NVDA260117C00150000") == "NVDA"
    assert rs.underlying_of("nvda") == "NVDA"
    assert rs.underlying_of("ETHU") == "ETHU"


# ── hard_constraint_signals ─────────────────────────────────────

def test_hard_constraint_signals_maps_keys_and_uses_detail_as_symbol():
    breaches = [
        {"key": "single_stock_max", "label": "单股最高仓位", "limit": 18.0,
         "reading": 22.0, "overshoot": 4.0, "detail": "AVGO"},
        {"key": "chain_max", "label": "单产业链最高仓位", "limit": 30.0,
         "reading": 37.0, "overshoot": 7.0, "detail": "AI芯片"},
        {"key": "cash_floor_min", "label": "最低现金比例", "limit": 20.0,
         "reading": 6.0, "overshoot": 14.0, "detail": ""},
    ]
    out = rs.hard_constraint_signals(breaches)
    by_dim = {s["dimension"]: s["symbol"] for s in out}
    assert by_dim["单票超限"] == "AVGO"
    assert by_dim["集中度超限"] == "AI芯片"
    assert by_dim["现金底线"] == "PORTFOLIO"


def test_hard_constraint_signals_ignores_unknown_keys():
    assert rs.hard_constraint_signals([{"key": "something_else"}]) == []


# ── risk_snapshot_signals ───────────────────────────────────────

def test_risk_snapshot_bd_over_hard_limit():
    out = rs.risk_snapshot_signals({"beta_delta_ratio": 3.9}, max_bd=3.5)
    assert len(out) == 1 and out[0]["dimension"] == "杠杆超限"


def test_risk_snapshot_stress_over_redline():
    out = rs.risk_snapshot_signals({"beta_delta_ratio": 1.0, "stress_20_ratio": -0.18},
                                    stress_redline=0.15)
    assert any(s["dimension"] == "压力测试超红线" for s in out)


def test_risk_snapshot_red_mandatory_de_risk():
    out = rs.risk_snapshot_signals({"drawdown_status": "RED_MANDATORY_DE_RISK"})
    assert any(s["dimension"] == "强制去风险" for s in out)


def test_risk_snapshot_clean_returns_nothing():
    assert rs.risk_snapshot_signals(
        {"beta_delta_ratio": 1.2, "stress_20_ratio": -0.05, "drawdown_status": "GREEN"}
    ) == []


# ── traded_signals (门④) ────────────────────────────────────────

def _trade(sym, kind="BUY", d="2026-01-10"):
    return {"symbol": sym, "type": kind, "trade_date": datetime.date.fromisoformat(d),
            "quantity": 10}


def test_traded_signals_no_case_buy_flags():
    out = rs.traded_signals(
        [_trade("SOFI")], cases_on_file=set(), circuit_symbols=set(),
        hard_breach_dates=set(), negative_kelly_strategies=set())
    assert any(s["dimension"] == "无case交易" and s["symbol"] == "SOFI" for s in out)


def test_traded_signals_case_on_file_no_flag():
    out = rs.traded_signals(
        [_trade("NVDA")], cases_on_file={"NVDA"}, circuit_symbols=set(),
        hard_breach_dates=set(), negative_kelly_strategies=set())
    assert not any(s["dimension"] == "无case交易" for s in out)


def test_traded_signals_circuit_and_breach_date_and_negative_kelly():
    out = rs.traded_signals(
        [_trade("AI260117C00030000")],
        cases_on_file={"AI"},
        circuit_symbols={"AI"},
        hard_breach_dates={datetime.date(2026, 1, 10)},
        negative_kelly_strategies={"call_debit_spread"},
        strategy_of=lambda s: "call_debit_spread",
    )
    dims = {s["dimension"] for s in out}
    assert "熔断票交易" in dims
    assert "开仓恶化breach" in dims
    assert "偏离Kelly" in dims


def test_traded_signals_sell_does_not_trigger_buy_only_checks():
    out = rs.traded_signals(
        [_trade("SOFI", kind="SELL")], cases_on_file=set(), circuit_symbols={"SOFI"},
        hard_breach_dates={datetime.date(2026, 1, 10)}, negative_kelly_strategies=set())
    assert out == []


# ── F-10 (2026-09-10 审计): 买入平仓 / 指数对冲 不该算开仓违规 ─────

def _closing_trade(sym, d="2026-01-10"):
    t = _trade(sym, kind="BUY", d=d)
    t["is_closing"] = True
    return t


def test_buy_to_close_does_not_flag_no_case():
    """Firstrade description 标了 CLOSING CONTRACT 的 BUY，是平掉卖出仓位，
    不是开新仓——不该因为"误价研究没建case"被扣分。"""
    out = rs.traded_signals(
        [_closing_trade("AVGO")], cases_on_file=set(), circuit_symbols=set(),
        hard_breach_dates=set(), negative_kelly_strategies=set())
    assert out == []


def test_buy_to_close_does_not_flag_breach_or_circuit_or_kelly():
    out = rs.traded_signals(
        [_closing_trade("AI260117C00030000")],
        cases_on_file=set(), circuit_symbols={"AI"},
        hard_breach_dates={datetime.date(2026, 1, 10)},
        negative_kelly_strategies={"call_debit_spread"},
        strategy_of=lambda s: "call_debit_spread",
    )
    assert out == []


def test_buy_to_open_without_is_closing_key_still_flags():
    """老数据路径没有 is_closing 字段——必须默认当开仓处理，不能因为
    这个新参数的缺席而静默漏检（向后兼容，不能变宽松）。"""
    out = rs.traded_signals(
        [_trade("SOFI")],  # 没有 is_closing 键
        cases_on_file=set(), circuit_symbols=set(),
        hard_breach_dates=set(), negative_kelly_strategies=set())
    assert any(s["dimension"] == "无case交易" for s in out)


def test_index_hedge_symbol_exempt_from_no_case_even_when_opening():
    """QQQ 这类系统自己会用来对冲的指数/ETF，开仓也不该被要求有case——
    这条豁免跟是否平仓无关，专门针对"无case交易"这一项检测。"""
    out = rs.traded_signals(
        [_trade("QQQ")], cases_on_file=set(), circuit_symbols=set(),
        hard_breach_dates=set(), negative_kelly_strategies=set())
    assert out == []


def test_index_hedge_exemption_only_covers_no_case_not_other_checks():
    """指数标的豁免的是"无case交易"，不是全部豁免——如果 QQQ 恰好也踩了
    熔断或硬约束超限，那两项检测该照样触发。"""
    out = rs.traded_signals(
        [_trade("QQQ")], cases_on_file=set(), circuit_symbols={"QQQ"},
        hard_breach_dates={datetime.date(2026, 1, 10)},
        negative_kelly_strategies=set())
    dims = {s["dimension"] for s in out}
    assert "无case交易" not in dims
    assert "熔断票交易" in dims
    assert "开仓恶化breach" in dims


def test_custom_index_hedge_symbols_override_default():
    out = rs.traded_signals(
        [_trade("MSTR")], cases_on_file=set(), circuit_symbols=set(),
        hard_breach_dates=set(), negative_kelly_strategies=set(),
        index_hedge_symbols={"MSTR"},
    )
    assert out == []


# ── pullback_signals (§7) ───────────────────────────────────────

def _bars(closes):
    n = len(closes)
    return pd.DataFrame({
        "open":  closes,
        "high":  [c * 1.01 for c in closes],
        "low":   [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    })


def test_pullback_flat_series_is_no_signal():
    res = rs.pullback_signals({"FLAT": _bars([100.0] * 80)})
    assert res["FLAT"]["tier"] is None


def test_pullback_sharp_recent_drop_is_major():
    # 60 days flat at 100, then a fast ~15% drop over the last 3 days
    closes = [100.0] * 77 + [95.0, 90.0, 85.0]
    res = rs.pullback_signals({"CRASH": _bars(closes)})
    assert res["CRASH"]["tier"] == "MAJOR"
    assert "3日快速下跌" in res["CRASH"]["checks"]


def test_pullback_too_few_bars_is_no_signal():
    res = rs.pullback_signals({"NEW": _bars([100.0] * 10)})
    assert res["NEW"]["tier"] is None


# ── compound_zhiying_pullback_signals ───────────────────────────

def test_compound_zhiying_plus_major_pullback():
    pnl_dte = [{"symbol": "NVDA260117C00150000", "dimension": "止盈纪律",
                "detail": "多期权盈利 120%"}]
    pullback = {"NVDA": {"tier": "MAJOR", "checks": ["3日快速下跌"]}}
    out = rs.compound_zhiying_pullback_signals(pnl_dte, pullback)
    assert len(out) == 1
    assert out[0]["dimension"] == "止盈回调复合"
    assert out[0]["symbol"] == "NVDA260117C00150000"


def test_compound_zhiying_without_major_pullback_is_empty():
    pnl_dte = [{"symbol": "NVDA260117C00150000", "dimension": "止盈纪律", "detail": "x"}]
    pullback = {"NVDA": {"tier": "WATCH", "checks": ["跌破20日均线"]}}
    assert rs.compound_zhiying_pullback_signals(pnl_dte, pullback) == []
