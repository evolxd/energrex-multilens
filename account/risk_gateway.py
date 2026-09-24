"""Shared, non-Streamlit gateway for account.risk's pure risk/IV computations.

account_monitor.py cannot be normally `import`ed (it runs `st.set_page_config()`
at module level), which is why external callers used to reach its
`_compute_risk_snapshot`/`_compute_iv_regime` wrappers through
`_cascade._get_am()`'s AST-slice-and-exec reflection dict. This module owns
the same DB queries, network calls and on-disk config reads those wrappers
used to do internally, so account_monitor.py's own wrappers and every
external call site can import `get_risk_snapshot`/`get_iv_regime` directly
instead (see docs/architecture/risk_and_iv_architecture.md, Phase 4).

Deliberate deviation from the old caching behaviour: `_cascade._get_am()`
kept `_RISK_LIMITS`/`_BETA_SPY` frozen until account_monitor.py's own mtime
changed (an incidental side effect of the AST-exec cache, not a designed
staleness window). This module re-reads the risk-limits registry and beta
cache on every call instead -- strictly fresher, never staler, and simpler
than trying to replicate a cache keyed to an unrelated file's mtime.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib

from account.beta_quality import beta_overrides as _derived_beta_overrides
from account.db import db as _db
from account.marketdata import fetch_underlying_prices, get_atm_iv_batch
from account.options import parse_occ, signed_quantity
from account.repository import load_latest_balance
from account.risk import IVRegimeSnapshot, RiskSnapshotInputs, RiskSnapshotResult
from account.risk import compute_iv_regime as _compute_iv_regime_pure
from account.risk import compute_risk_snapshot as _compute_risk_snapshot_pure

_log = logging.getLogger("energrex.account.risk_gateway")

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MD_KEY = os.environ.get("MARKETDATA_API_KEY", "")

# ── Beta 硬编码兜底值（yfinance 失效时使用）── 跟 account_monitor.py 的
# _BETA_BASE 保持同一份数据；两边各自维护是因为 account_monitor.py 那份
# 还要喂给 _refresh_beta_spy() 的运行时更新循环，这里只需要只读的合并结果。
_BETA_BASE = {
    "ARM": 2.95, "AVGO": 2.13, "META": 1.48, "PLTR": 1.81, "VST": 1.52,
    "FCX": 2.14, "MRVL": 2.61, "NVDA": 1.83, "PANW": 1.09, "CRWV": 2.84,
    "QQQ": 1.31, "NOK": 1.42, "CRWD": 1.50, "DDOG": 1.70, "NET": 1.65,
    "AMD": 1.90, "TSLA": 2.30, "MSFT": 1.20, "GOOGL": 1.15, "AMZN": 1.25,
    "SMCI": 2.20, "MU": 1.60, "SNOW": 2.10, "ORCL": 1.10, "ZS": 1.55,
    "SPY": 1.00, "IWM": 1.10, "GLD": 0.05,
    "SMH": 1.77,
}
_BETA_BASE.update(_derived_beta_overrides())

_BETA_CACHE_PATH = _ROOT / "data" / "beta_cache.json"
_BETA_CACHE_DAYS = 8


def _load_beta_cache() -> dict:
    """Exact copy of account_monitor.py's _load_beta_cache(): read
    beta_cache.json; empty/stale/corrupt -> {}."""
    try:
        raw = json.loads(_BETA_CACHE_PATH.read_text(encoding="utf-8"))
        age = (datetime.datetime.now() -
               datetime.datetime.fromisoformat(raw["updated_at"])).days
        if age <= _BETA_CACHE_DAYS:
            return {k: float(v) for k, v in raw.get("betas", {}).items()}
    except Exception:
        pass
    return {}


def _load_beta_map() -> dict[str, float]:
    """Equivalent of account_monitor.py's module-level _BETA_SPY, re-read
    fresh on every call instead of cached for the process lifetime."""
    return {**_BETA_BASE, **_load_beta_cache()}


def _load_risk_limits() -> dict:
    """Exact copy of account_monitor.py's _load_risk_limits(): 7 risk-snapshot
    limits from the governed registry (position_limits.jsonl)."""
    from scoring.position_limits import effective_risk_limits as _eff_risk_limits
    try:
        from scoring.mispricing_store import read_chain as _read_chain
        _records = _read_chain(_ROOT / "data" / "position_limits.jsonl")
    except Exception:
        _records = []
    raw = _eff_risk_limits(_records, datetime.datetime.now())
    return {
        "max_leverage":         raw["max_leverage"],
        "max_beta_delta_ratio": raw["max_beta_delta_ratio"],
        "stress_warning":       raw["stress_warning"] / 100.0,
        "stress_de_risk":       raw["stress_de_risk"] / 100.0,
        "stress_hard_stop":     raw["stress_hard_stop"] / 100.0,
        "stress_20_hard_stop":  raw["stress_20_hard_stop"] / 100.0,
        "drawdown_freeze":      raw["drawdown_freeze"] / 100.0,
        "drawdown_de_risk":     raw["drawdown_de_risk"] / 100.0,
        "drawdown_start_date":  "2026-06-01",
    }


def gather_iv_regime_inputs(acct_id: str) -> tuple[list, list]:
    """Same two SELECTs account_monitor.py's _compute_iv_regime wrapper runs."""
    conn = _db()
    hist_rows = conn.execute(
        "SELECT symbol, iv FROM iv_history WHERE account_id=? ORDER BY timestamp",
        (acct_id,)).fetchall()
    cur_rows = conn.execute(
        "SELECT symbol, iv FROM options_positions "
        "WHERE account_id=? AND iv IS NOT NULL",
        (acct_id,)).fetchall()
    conn.close()
    return hist_rows, cur_rows


def get_iv_regime(acct_id: str, min_samples: int = 20) -> IVRegimeSnapshot:
    hist_rows, cur_rows = gather_iv_regime_inputs(acct_id)
    return _compute_iv_regime_pure(hist_rows, cur_rows, min_samples)


def gather_risk_snapshot_inputs(acct_id: str) -> RiskSnapshotInputs:
    """Same 4 SQL queries + 2 network calls + global reads account_monitor.py's
    _compute_risk_snapshot wrapper does, packaged into RiskSnapshotInputs."""
    bal = load_latest_balance(acct_id)
    risk_limits = _load_risk_limits()
    beta_map = _load_beta_map()

    conn = _db()
    dd_start = str(risk_limits.get("drawdown_start_date", "2026-06-01"))
    nav_rows = conn.execute(
        "SELECT DATE(sync_time) AS d, total_equity FROM account_balance "
        "WHERE account_id=? AND DATE(sync_time)>=? ORDER BY sync_time", (acct_id, dd_start)).fetchall()
    cf_rows = conn.execute(
        "SELECT trade_date, SUM(amount) AS cf FROM transactions "
        "WHERE account_id=? AND type IN ('提款','存款','DEPOSIT','WITHDRAWAL') "
        "GROUP BY trade_date", (acct_id,)).fetchall()
    # quantity 必须过 signed_quantity：这张表里 xlsx 导入存带符号的张数，而
    # Chrome 抓取存 abs(张数) + direction='short'。直接用 raw quantity 的话，
    # 卖出的腿被当成买入的——Beta-Delta 符号反掉，压力测试里崩盘变成赚钱。
    # 见 account/options.py::signed_quantity。
    opts = [
        {"symbol": r["symbol"],
         "quantity": signed_quantity(r["quantity"], r["direction"]),
         "current_price": r["current_price"], "market_value": r["market_value"],
         "strike": r["strike"], "expiry": r["expiry"]}
        for r in conn.execute(
            "SELECT symbol, quantity, direction, current_price, market_value, "
            "strike, expiry FROM options_positions "
            "WHERE account_id=? AND current_price IS NOT NULL", (acct_id,))
    ]
    # positions 是逐次同步追加的快照表：必须每只股票只取最新一条（跟
    # account_monitor.py 的取数逻辑同口径，见那边注释里的重复计入事故）。
    stks = conn.execute(
        "SELECT symbol, quantity, market_value FROM positions p1 "
        "WHERE p1.account_id=? AND p1.position_type='stock' "
        "AND p1.sync_time = (SELECT MAX(p2.sync_time) FROM positions p2 "
        "WHERE p2.account_id=p1.account_id AND p2.symbol=p1.symbol)",
        (acct_id,)).fetchall()
    conn.close()

    underlyings = set()
    for o in opts:
        parsed = parse_occ(str(o["symbol"] or "").upper())
        if parsed:
            underlyings.add(parsed["root"])
    stock_syms = {str(s["symbol"] or "").upper() for s in stks if s["symbol"]}
    price_lookup_syms = underlyings | stock_syms
    und_prices = (fetch_underlying_prices(tuple(sorted(price_lookup_syms)), logger=_log)
                  if price_lookup_syms else {})
    iv_map = (get_atm_iv_batch(tuple(sorted(underlyings)), api_key=_MD_KEY)
              if underlyings else {})

    return {
        "balance":           bal,
        "nav_rows":          nav_rows,
        "cashflow_rows":     cf_rows,
        "option_positions":  opts,
        "stock_positions":   stks,
        "underlying_prices": und_prices,
        "iv_map":            iv_map,
        "beta_map":          beta_map,
        "risk_limits":       risk_limits,
        "now":               datetime.datetime.now(datetime.timezone.utc),
    }


def get_risk_snapshot(acct_id: str) -> RiskSnapshotResult:
    return _compute_risk_snapshot_pure(gather_risk_snapshot_inputs(acct_id))
