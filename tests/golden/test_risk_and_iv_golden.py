"""Golden (characterization) tests for account_monitor._compute_risk_snapshot
and _compute_iv_regime, ahead of moving their pure cores into account/risk.py
(see docs/architecture/risk_and_iv_architecture.md, Phase 2 of
docs/REFACTORING_WORKFLOW.md).

These lock the CURRENT unmodified implementations' behavior via the real
functions inside account_monitor.py (reached through the same AST-slice
technique as the sibling spread-pairing golden tests), not an invented
contract implementation -- there is nothing to import yet, only a draft.

"真实返回结果" is interpreted as "what the real, unmodified code path
produces against synthetic data with every non-deterministic source pinned" --
NOT live production account data or live network calls. Two reasons:
  1. _compute_risk_snapshot makes real network calls (_fetch_underlying_prices,
     _get_atm_iv_batch); a golden snapshot built from a live call would not be
     reproducible and would violate cleancode-skills' determinism rule.
  2. A snapshot built from a real account would put real leverage/positions/
     NAV into git history.

Determinism sources pinned, all newly discovered while reading the real code
(none of this was assumed):
  - `_BETA_SPY` / `_RISK_LIMITS`: computed at account_monitor.py exec time
    from disk (data/beta_cache.json) and datetime.datetime.now() -- both
    would silently drift the baseline run-to-run if left alone. Overridden
    to fixed dicts on the exec'd namespace before any test call.
  - `_fetch_underlying_prices` / `_get_atm_iv_batch`: real network calls to
    a market-data provider. Overridden to fixed lookup tables on the exec'd
    namespace.
  - `datetime.datetime.now()` inside _compute_risk_snapshot (used for
    data_age_hours): frozen via freezegun.
  - `_load_beta_cache()` / `_load_risk_limits()` also read disk files and
    call datetime.now() at import time; since we override the globals they
    produce AFTER exec, their own non-determinism never reaches the snapshot.

Regenerate ONLY on the unmodified original code:
    UPDATE_GOLDEN=1 python -m pytest tests/golden/test_risk_and_iv_golden.py -q
A missing snapshot fails the test; it is never created silently.
"""

import ast
import datetime
import json
import os
import pathlib
import sys
import tempfile
import types

import pytest
from freezegun import freeze_time

# ── Streamlit stub (account_monitor imports streamlit at module level) ────────
_st = types.ModuleType("streamlit")
_st.session_state = {}


def _cache_dec(*a, **kw):
    fn = a[0] if (a and callable(a[0])) else None

    def deco(f):
        return f

    return deco(fn) if fn else deco


_st.cache_resource = _cache_dec
_st.cache_data = _cache_dec
_noop = lambda *a, **kw: None
for _n in [
    "set_page_config", "markdown", "write", "info", "error", "warning",
    "success", "caption", "divider", "spinner", "toast", "rerun",
    "columns", "metric", "button", "selectbox", "radio", "tabs",
    "expander", "container", "header", "subheader", "title",
    "page_link", "file_uploader", "dataframe", "plotly_chart",
    "stop", "form", "form_submit_button", "empty", "progress",
    "number_input", "text_input", "checkbox", "multiselect",
    "date_input", "time_input", "color_picker", "slider",
    "balloons", "snow", "exception",
]:
    setattr(_st, _n, _noop)
_st.sidebar = types.SimpleNamespace(
    **{n: _noop for n in [
        "markdown", "write", "info", "error", "warning", "success",
        "caption", "divider", "button", "selectbox", "file_uploader",
        "title", "header", "radio", "number_input", "text_input",
        "checkbox", "multiselect",
    ]}
)
sys.modules["streamlit"] = _st

import account.db as account_db  # noqa: E402
import account.repository as account_repository  # noqa: E402
from account.options import parse_occ, signed_quantity  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = pathlib.Path(__file__).parent / "snapshots_risk_iv"
FROZEN_NOW = "2026-09-23T12:00:00-04:00"   # US/Eastern, matches account.repository's ET convention
ACCT = "golden_acct"

# Fixed, synthetic stand-ins for the two network calls -- never a live quote.
_FIXED_PRICES = {"NVDA": 120.0, "AMD": 150.0, "AVGO": 290.0, "SPY": 560.0}
_FIXED_IV = {
    "NVDA": {"iv": 0.45, "src": "md"}, "AMD": {"iv": 0.55, "src": "md"},
    "AVGO": {"iv": 0.35, "src": "md"}, "SPY": {"iv": 0.15, "src": "md"},
}
_FIXED_BETA = {"NVDA": 1.8, "AMD": 2.1, "AVGO": 1.4, "SPY": 1.0}
_FIXED_RISK_LIMITS = {
    "max_leverage": 4.0, "max_beta_delta_ratio": 3.5,
    "stress_warning": 0.08, "stress_de_risk": 0.12, "stress_hard_stop": 0.15,
    "stress_20_hard_stop": 0.25,
    "drawdown_freeze": 0.20, "drawdown_de_risk": 0.30,
    "drawdown_start_date": "2026-06-01",
}


@pytest.fixture(scope="module")
def ns():
    """AST-slice account_monitor.py, exec into a namespace on a throwaway DB,
    then pin every module-level non-determinism source discovered while
    reading the real code (see module docstring)."""
    tmp_dir = tempfile.mkdtemp()
    original = account_db.DB_PATH
    account_db.DB_PATH = pathlib.Path(tmp_dir) / "risk_iv_probe.db"
    try:
        src = (ROOT / "account_monitor.py").read_text(encoding="utf-8-sig")
        cutoff = next((i + 1 for i, ln in enumerate(src.splitlines())
                       if "st.set_page_config" in ln), 99999)
        tree = ast.parse(src, filename="account_monitor.py")
        filtered = ast.Module(
            body=[n for n in tree.body if getattr(n, "lineno", 0) < cutoff],
            type_ignores=[])
        ast.fix_missing_locations(filtered)
        namespace = {"__file__": str(ROOT / "account_monitor.py"), "__name__": "account_monitor"}
        exec(compile(filtered, str(ROOT / "account_monitor.py"), "exec"), namespace)

        # Pin the two globals that are otherwise computed from disk + wall clock
        # at exec time -- see module docstring.
        namespace["_BETA_SPY"] = dict(_FIXED_BETA)
        namespace["_RISK_LIMITS"] = dict(_FIXED_RISK_LIMITS)
        # Replace the two network-calling wrappers with fixed lookup tables.
        namespace["_fetch_underlying_prices"] = (
            lambda tickers: {t: _FIXED_PRICES[t] for t in tickers if t in _FIXED_PRICES}
        )
        namespace["_get_atm_iv_batch"] = (
            lambda tickers: {t: _FIXED_IV[t] for t in tickers if t in _FIXED_IV}
        )
        yield namespace
    finally:
        account_db.DB_PATH = original


def _conn():
    return account_db.db()


def _seed_balance(acct, rows):
    """rows: list of (sync_time_iso, total_equity, cash_balance, margin_used,
    margin_available, margin_usage_pct, day_pnl)."""
    conn = _conn()
    for r in rows:
        conn.execute(
            "INSERT INTO account_balance (account_id, sync_time, total_equity, "
            "cash_balance, margin_used, margin_available, margin_usage_pct, day_pnl) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (acct, *r),
        )
    conn.commit()
    conn.close()


def _seed_transactions(acct, rows):
    """rows: list of (trade_date, type, symbol, amount)."""
    conn = _conn()
    for trade_date, ttype, symbol, amount in rows:
        conn.execute(
            "INSERT INTO transactions (account_id, trade_date, type, symbol, amount) "
            "VALUES (?,?,?,?,?)",
            (acct, trade_date, ttype, symbol, amount),
        )
    conn.commit()
    conn.close()


def _seed_options(acct, rows):
    """rows: list of dicts with symbol/direction/strike/expiry/quantity/
    unit_cost/current_price/market_value/iv (iv optional)."""
    conn = _conn()
    for r in rows:
        conn.execute(
            "INSERT INTO options_positions (account_id, symbol, direction, strike, "
            "expiry, quantity, unit_cost, current_price, market_value, iv) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (acct, r["symbol"], r.get("direction", "long"), r["strike"], r["expiry"],
             r["quantity"], r.get("unit_cost", 0.0), r.get("current_price"),
             r.get("market_value"), r.get("iv")),
        )
    conn.commit()
    conn.close()


def _seed_stocks(acct, rows, sync_time="2026-09-22T16:00:00-04:00"):
    """rows: list of (symbol, quantity, market_value). All stamped with the
    same sync_time -- _compute_risk_snapshot's stock query takes each
    symbol's single latest row, and with only one row per symbol here that's
    trivially satisfied."""
    conn = _conn()
    for symbol, qty, mv in rows:
        conn.execute(
            "INSERT INTO positions (account_id, sync_time, symbol, position_type, "
            "quantity, market_value) VALUES (?,?,?,?,?,?)",
            (acct, sync_time, symbol, "stock", qty, mv),
        )
    conn.commit()
    conn.close()


def _seed_iv_history(acct, rows):
    """rows: list of (symbol, iv, timestamp)."""
    conn = _conn()
    for symbol, iv, ts in rows:
        conn.execute(
            "INSERT INTO iv_history (account_id, timestamp, symbol, iv) VALUES (?,?,?,?)",
            (acct, ts, symbol, iv),
        )
    conn.commit()
    conn.close()


def _canonical(result) -> str:
    return json.dumps(result, ensure_ascii=False, indent=1, sort_keys=False,
                       allow_nan=True, default=str) + "\n"


def _clear_all(acct):
    conn = _conn()
    for t in ("account_balance", "transactions", "options_positions", "positions", "iv_history"):
        conn.execute(f"DELETE FROM {t} WHERE account_id=?", (acct,))
    conn.commit()
    conn.close()


def _gather_iv_inputs(acct):
    """Literal copy of what account_monitor.py's _compute_iv_regime wrapper
    will do post-extraction: run its two SELECTs, hand the rows straight to
    the pure core. Used to cross-check the new account.risk.compute_iv_regime
    against the golden snapshots captured from the OLD implementation."""
    conn = _conn()
    hist_rows = conn.execute(
        "SELECT symbol, iv FROM iv_history WHERE account_id=? ORDER BY timestamp",
        (acct,)).fetchall()
    cur_rows = conn.execute(
        "SELECT symbol, iv FROM options_positions "
        "WHERE account_id=? AND iv IS NOT NULL",
        (acct,)).fetchall()
    conn.close()
    return hist_rows, cur_rows


def _gather_risk_snapshot_inputs(ns, acct, now):
    """Literal copy of what account_monitor.py's _compute_risk_snapshot
    wrapper will do post-extraction: every DB query + global read the OLD
    function used to do internally, packaged into a RiskSnapshotInputs dict
    for the new account.risk.compute_risk_snapshot."""
    bal = account_repository.load_latest_balance(acct)
    risk_limits = ns["_RISK_LIMITS"]
    beta_map = ns["_BETA_SPY"]

    conn = _conn()
    dd_start = str(risk_limits.get("drawdown_start_date", "2026-06-01"))
    nav_rows = conn.execute(
        "SELECT DATE(sync_time) AS d, total_equity FROM account_balance "
        "WHERE account_id=? AND DATE(sync_time)>=? ORDER BY sync_time", (acct, dd_start)).fetchall()
    cf_rows = conn.execute(
        "SELECT trade_date, SUM(amount) AS cf FROM transactions "
        "WHERE account_id=? AND type IN ('提款','存款','DEPOSIT','WITHDRAWAL') "
        "GROUP BY trade_date", (acct,)).fetchall()
    # quantity must go through signed_quantity -- mirrors account_monitor.py's
    # wrapper / account.risk_gateway's own fix for the same table.
    opts = [
        {"symbol": r["symbol"],
         "quantity": signed_quantity(r["quantity"], r["direction"]),
         "current_price": r["current_price"], "market_value": r["market_value"],
         "strike": r["strike"], "expiry": r["expiry"]}
        for r in conn.execute(
            "SELECT symbol, quantity, direction, current_price, market_value, "
            "strike, expiry FROM options_positions "
            "WHERE account_id=? AND current_price IS NOT NULL", (acct,))
    ]
    stks = conn.execute(
        "SELECT symbol, quantity, market_value FROM positions p1 "
        "WHERE p1.account_id=? AND p1.position_type='stock' "
        "AND p1.sync_time = (SELECT MAX(p2.sync_time) FROM positions p2 "
        "WHERE p2.account_id=p1.account_id AND p2.symbol=p1.symbol)",
        (acct,)).fetchall()
    conn.close()

    underlyings = set()
    for o in opts:
        parsed = parse_occ(str(o["symbol"] or "").upper())
        if parsed:
            underlyings.add(parsed["root"])
    stock_syms = {str(s["symbol"] or "").upper() for s in stks if s["symbol"]}
    price_lookup_syms = underlyings | stock_syms
    und_prices = ns["_fetch_underlying_prices"](tuple(sorted(price_lookup_syms))) if price_lookup_syms else {}
    iv_map = ns["_get_atm_iv_batch"](tuple(sorted(underlyings))) if underlyings else {}

    return {
        "balance": bal,
        "nav_rows": nav_rows,
        "cashflow_rows": cf_rows,
        "option_positions": opts,
        "stock_positions": stks,
        "underlying_prices": und_prices,
        "iv_map": iv_map,
        "beta_map": beta_map,
        "risk_limits": risk_limits,
        "now": now,
    }


# ════════════════════════════════════════════════════════════════
# _compute_iv_regime
# ════════════════════════════════════════════════════════════════

IV_SCENARIOS = {
    "no_data": lambda acct: None,  # nothing seeded at all
    "insufficient_history": lambda acct: _seed_iv_history(
        acct, [("NVDA270618C00100000", 0.40, "2026-09-20T10:00:00")]
    ) or _seed_options(acct, [dict(symbol="NVDA270618C00100000", strike=100, expiry="2027-06-18",
                                    quantity=1, current_price=5.0, iv=0.42)]),
    "normal_sufficient_history": lambda acct: (
        _seed_iv_history(acct, [("NVDA270618C00100000", 0.30 + i * 0.01, f"2026-08-{10+i:02d}T10:00:00")
                                 for i in range(25)])
        or _seed_options(acct, [dict(symbol="NVDA270618C00100000", strike=100, expiry="2027-06-18",
                                      quantity=1, current_price=5.0, iv=0.42)])
    ),
    "extreme_iv": lambda acct: (
        _seed_iv_history(acct, [("AMD270618C00150000", 0.20 + i * 0.005, f"2026-08-{10+i:02d}T10:00:00")
                                 for i in range(25)])
        or _seed_options(acct, [dict(symbol="AMD270618C00150000", strike=150, expiry="2027-06-18",
                                      quantity=1, current_price=8.0, iv=0.35)])
    ),
    "low_iv": lambda acct: (
        _seed_iv_history(acct, [("AVGO270618C00280000", 0.30 + i * 0.01, f"2026-08-{10+i:02d}T10:00:00")
                                 for i in range(25)])
        or _seed_options(acct, [dict(symbol="AVGO270618C00280000", strike=280, expiry="2027-06-18",
                                      quantity=1, current_price=12.0, iv=0.28)])
    ),
    "mixed_portfolio_extreme_dominates": lambda acct: (
        _seed_iv_history(acct, [("NVDA270618C00100000", 0.30 + i * 0.01, f"2026-08-{10+i:02d}T10:00:00")
                                 for i in range(25)]
                          + [("AMD270618C00150000", 0.20 + i * 0.005, f"2026-08-{10+i:02d}T10:00:00")
                             for i in range(25)])
        or _seed_options(acct, [
            dict(symbol="NVDA270618C00100000", strike=100, expiry="2027-06-18",
                 quantity=1, current_price=5.0, iv=0.42),
            dict(symbol="AMD270618C00150000", strike=150, expiry="2027-06-18",
                 quantity=1, current_price=8.0, iv=0.35),
        ])
    ),
}


@pytest.mark.parametrize("name", sorted(IV_SCENARIOS))
def test_iv_regime_golden(ns, name):
    acct = f"iv_{name}"
    _clear_all(acct)
    IV_SCENARIOS[name](acct)
    result = ns["_compute_iv_regime"](acct)
    actual = _canonical(result)

    snapshot = SNAPSHOT_DIR / f"iv_{name}.json"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not snapshot.exists():
        pytest.fail(f"missing golden snapshot {snapshot.name}; generate with "
                    "UPDATE_GOLDEN=1 on the UNMODIFIED original code")
    assert actual == snapshot.read_text(encoding="utf-8"), (
        f"iv_regime output differs from golden snapshot {name} -> behaviour changed")


# ════════════════════════════════════════════════════════════════
# _compute_risk_snapshot
# ════════════════════════════════════════════════════════════════

def _healthy_account(acct):
    _seed_balance(acct, [
        ("2026-06-01T16:00:00-04:00", 100000.0, 20000.0, 0, 0, 0, 0),
        ("2026-09-22T16:00:00-04:00", 110000.0, 25000.0, 0, 0, 0, 500.0),
    ])
    _seed_transactions(acct, [])
    _seed_options(acct, [
        dict(symbol="NVDA270618C00100000", strike=100, expiry="2027-06-18",
             quantity=2, unit_cost=8.0, current_price=10.0, market_value=2000.0),
        dict(symbol="NVDA270618C00120000", strike=120, expiry="2027-06-18",
             quantity=-2, unit_cost=3.0, current_price=2.0, market_value=-400.0),
    ])
    _seed_stocks(acct, [("SPY", 10, 5600.0)])


def _zero_equity_account(acct):
    _seed_balance(acct, [("2026-09-22T16:00:00-04:00", 0.0, 0.0, 0, 0, 0, 0)])


def _stale_data_account(acct):
    # Same as healthy, but the latest sync is > 24h before FROZEN_NOW.
    _seed_balance(acct, [
        ("2026-06-01T16:00:00-04:00", 100000.0, 20000.0, 0, 0, 0, 0),
        ("2026-09-19T16:00:00-04:00", 110000.0, 25000.0, 0, 0, 0, 500.0),
    ])
    _seed_options(acct, [
        dict(symbol="SPY270618C00560000", strike=560, expiry="2027-06-18",
             quantity=1, unit_cost=20.0, current_price=22.0, market_value=2200.0),
    ])
    _seed_stocks(acct, [("SPY", 5, 2800.0)], sync_time="2026-09-19T16:00:00-04:00")


def _high_leverage_account(acct):
    # Large option notional relative to a small equity base -> should push
    # leverage/stress ratios toward the RED_HARD_STOP thresholds.
    _seed_balance(acct, [("2026-09-22T16:00:00-04:00", 20000.0, 5000.0, 0, 0, 0, -3000.0)])
    _seed_options(acct, [
        dict(symbol="AMD270618C00150000", strike=150, expiry="2027-06-18",
             quantity=20, unit_cost=10.0, current_price=9.0, market_value=18000.0),
    ])
    _seed_stocks(acct, [("AMD", 100, 15000.0)])


def _drawdown_account(acct):
    _seed_balance(acct, [
        ("2026-06-01T16:00:00-04:00", 100000.0, 0, 0, 0, 0, 0),
        ("2026-07-01T16:00:00-04:00", 120000.0, 0, 0, 0, 0, 0),
        ("2026-08-01T16:00:00-04:00", 80000.0, 0, 0, 0, 0, 0),
        ("2026-09-22T16:00:00-04:00", 85000.0, 20000.0, 0, 0, 0, 0),
    ])
    _seed_options(acct, [
        dict(symbol="AVGO270618C00280000", strike=280, expiry="2027-06-18",
             quantity=1, unit_cost=12.0, current_price=11.0, market_value=1100.0),
    ])
    _seed_stocks(acct, [("AVGO", 10, 2900.0)])


def _missing_beta_fallback_account(acct):
    # Underlying not present in _FIXED_BETA/_FIXED_IV -- should surface in
    # iv_fallback_symbols and use whatever default the pure helpers apply.
    _seed_balance(acct, [("2026-09-22T16:00:00-04:00", 50000.0, 10000.0, 0, 0, 0, 0)])
    _seed_options(acct, [
        dict(symbol="ZZZZ270618C00050000", strike=50, expiry="2027-06-18",
             quantity=1, unit_cost=3.0, current_price=2.5, market_value=250.0),
    ])
    _seed_stocks(acct, [])


def _short_position_unsigned_quantity_account(acct):
    # Chrome-scrape write shape: abs(qty) + direction="short" -- exactly the
    # pattern that used to make a sold put read as a bought put (see
    # account/options.py::signed_quantity and tests/test_signed_quantity.py).
    # A short put LOSES money when the underlying crashes; before the sign
    # fix this scenario's stress_20/beta_delta would come out with the wrong
    # sign (a "profit" on a market crash).
    _seed_balance(acct, [("2026-09-22T16:00:00-04:00", 60000.0, 10000.0, 0, 0, 0, 0)])
    _seed_options(acct, [
        dict(symbol="NVDA270618P00100000", direction="short", strike=100, expiry="2027-06-18",
             quantity=2, unit_cost=8.0, current_price=10.0, market_value=-2000.0),
    ])
    _seed_stocks(acct, [])


RISK_SCENARIOS = {
    "zero_equity": _zero_equity_account,
    "healthy_account": _healthy_account,
    "stale_data": _stale_data_account,
    "high_leverage": _high_leverage_account,
    "drawdown": _drawdown_account,
    "missing_beta_fallback": _missing_beta_fallback_account,
    "short_position_unsigned_quantity": _short_position_unsigned_quantity_account,
}


@pytest.mark.parametrize("name", sorted(RISK_SCENARIOS))
def test_risk_snapshot_golden(ns, name):
    acct = f"risk_{name}"
    _clear_all(acct)
    RISK_SCENARIOS[name](acct)
    with freeze_time(FROZEN_NOW):
        result = ns["_compute_risk_snapshot"](acct)
    actual = _canonical(result)

    snapshot = SNAPSHOT_DIR / f"risk_{name}.json"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not snapshot.exists():
        pytest.fail(f"missing golden snapshot {snapshot.name}; generate with "
                    "UPDATE_GOLDEN=1 on the UNMODIFIED original code")
    assert actual == snapshot.read_text(encoding="utf-8"), (
        f"risk_snapshot output differs from golden snapshot {name} -> behaviour changed")


def test_every_scenario_has_a_snapshot():
    on_disk = {p.stem for p in SNAPSHOT_DIR.glob("*.json")}
    expected = {f"iv_{n}" for n in IV_SCENARIOS} | {f"risk_{n}" for n in RISK_SCENARIOS}
    assert on_disk == expected


# ════════════════════════════════════════════════════════════════
# 阶段三核对：新的纯内核（account.risk.compute_iv_regime /
# compute_risk_snapshot）跟阶段二锁定的黄金快照逐字节一致，证明搬家过程
# 没有夹带任何行为变更。account_monitor.py 里的旧实现此时还没被删除。
# ════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", sorted(IV_SCENARIOS))
def test_new_iv_regime_core_matches_golden_snapshot(name):
    from account.risk import compute_iv_regime as new_compute_iv_regime

    acct = f"iv_{name}"
    _clear_all(acct)
    IV_SCENARIOS[name](acct)
    hist_rows, cur_rows = _gather_iv_inputs(acct)
    result = new_compute_iv_regime(hist_rows, cur_rows)
    actual = _canonical(result)

    snapshot = SNAPSHOT_DIR / f"iv_{name}.json"
    assert actual == snapshot.read_text(encoding="utf-8"), (
        f"新的 account.risk.compute_iv_regime 在场景 {name} 下跟阶段二锁定的"
        "黄金快照不一致 -- 搬家过程中夹带了行为变更。")


@pytest.mark.parametrize("name", sorted(RISK_SCENARIOS))
def test_new_risk_snapshot_core_matches_golden_snapshot(ns, name):
    from account.risk import compute_risk_snapshot as new_compute_risk_snapshot

    acct = f"risk_{name}"
    _clear_all(acct)
    RISK_SCENARIOS[name](acct)
    with freeze_time(FROZEN_NOW):
        inputs = _gather_risk_snapshot_inputs(
            ns, acct, datetime.datetime.now(datetime.timezone.utc))
        result = new_compute_risk_snapshot(inputs)
    actual = _canonical(result)

    snapshot = SNAPSHOT_DIR / f"risk_{name}.json"
    assert actual == snapshot.read_text(encoding="utf-8"), (
        f"新的 account.risk.compute_risk_snapshot 在场景 {name} 下跟阶段二锁定的"
        "黄金快照不一致 -- 搬家过程中夹带了行为变更。")


# ════════════════════════════════════════════════════════════════
# 用户要求的重点：`today` 参数注入是否真的等价于现状的隐式 datetime.date.today()
# ════════════════════════════════════════════════════════════════

def test_stress_test_today_injection_is_equivalent_to_implicit_today():
    """新契约打算把 compute_portfolio_stress_test 现在没传的 `today` 参数显式
    传上（等于 inputs["now"].date()）。这个测试直接验证：在同一个被冻结的
    日期下，`today=None`（现状的隐式行为）和 `today=<冻结日期>`（新契约的
    显式行为）产出是否逐字节一致。如果不一致，说明"顺手传上"其实是一次
    行为变更，必须在阶段三前提交给用户重新决策，不能当作零风险的整理。
    """
    from account.risk import compute_portfolio_stress_test

    stocks = [{"symbol": "SPY", "quantity": 10, "market_value": 5600.0}]
    options = [{"symbol": "SPY270618C00560000", "quantity": 1, "current_price": 22.0,
                "market_value": 2200.0, "strike": 560.0, "expiry": "2027-06-18"}]
    kwargs = dict(
        underlying_prices={"SPY": 560.0},
        iv_map={"SPY": {"iv": 0.15, "src": "md"}},
        beta_map={"SPY": 1.0},
    )
    frozen_date = datetime.date(2026, 9, 23)

    with freeze_time(FROZEN_NOW):
        implicit = compute_portfolio_stress_test(stocks, options, **kwargs)  # today=None
        explicit = compute_portfolio_stress_test(stocks, options, today=frozen_date, **kwargs)

    assert implicit == explicit, (
        "compute_portfolio_stress_test(today=None) 在冻结时间下的输出，跟显式传入 "
        "today=当天日期 不一致——'顺手把 today 传上' 不是零风险整理，是一次真实的行为"
        "变更，必须单独决策，不能在契约迁移里静默夹带。"
    )


# `today` 唯一真正影响输出的路径是 dte = max(0, (exp_date - today).days)——
# dte>0 时才会算 Greeks/冲击定价，dte<=0 时整条 Greeks 分支被跳过（视为
# 到期/已过期）。所以只用一个"远期到期"的 SPY 期权覆盖不了这个参数——
# 必须专门造 dte 恰好跨越 0/1 边界、以及已过期的场景来"硬核比对"。
_TODAY_DTE_CASES = {
    "expires_today_dte_zero": "2026-09-23",       # exp_date == today -> dte=0，跳过 Greeks 分支
    "expires_tomorrow_dte_one": "2026-09-24",     # dte=1，刚好越过 0 的边界，Greeks 分支开启
    "already_expired_five_days_ago": "2026-09-18",  # exp_date < today -> max(0, negative) 钳位到 0
    "far_dated": "2027-06-18",                    # 原始烟雾测试用的远期案例，一并纳入同一套断言
}


@pytest.mark.parametrize("case_name", sorted(_TODAY_DTE_CASES))
def test_stress_test_today_injection_equivalence_across_dte_boundaries(case_name):
    """在 dte=0 / dte=1 / 已过期 三个会让 `today` 真正改变分支走向的边界上，
    分别验证 today=None 和 today=<冻结日期> 是否逐字节一致。只测一个远期
    到期案例是测不到这条边界的——Greeks 分支只有在 dte<=0 时才会被跳过。
    """
    from account.risk import compute_portfolio_stress_test

    expiry = _TODAY_DTE_CASES[case_name]
    stocks = [{"symbol": "SPY", "quantity": 10, "market_value": 5600.0}]
    options = [{"symbol": "SPY270618C00560000", "quantity": 1, "current_price": 22.0,
                "market_value": 2200.0, "strike": 560.0, "expiry": expiry}]
    kwargs = dict(
        underlying_prices={"SPY": 560.0},
        iv_map={"SPY": {"iv": 0.15, "src": "md"}},
        beta_map={"SPY": 1.0},
    )
    frozen_date = datetime.date(2026, 9, 23)

    with freeze_time(FROZEN_NOW):
        implicit = compute_portfolio_stress_test(stocks, options, **kwargs)
        explicit = compute_portfolio_stress_test(stocks, options, today=frozen_date, **kwargs)

    assert implicit == explicit, (
        f"dte 边界场景 {case_name}（expiry={expiry}）下 today=None 与 "
        "today=冻结日期 的输出不一致——这正是 today 参数唯一会改变分支走向的地方。"
    )


def test_stress_test_today_injection_equivalence_with_multiple_expiries():
    """多腿、到期日不同（含一条已过期、一条今天到期、一条远期）的组合，
    同时覆盖 nearest_expiry_date/nearest_expiry_sym 的选取逻辑是否也在
    today 注入前后保持一致。
    """
    from account.risk import compute_portfolio_stress_test

    stocks = [{"symbol": "SPY", "quantity": 5, "market_value": 2800.0}]
    options = [
        {"symbol": "SPY261010C00560000", "quantity": 1, "current_price": 1.0,
         "market_value": 100.0, "strike": 560.0, "expiry": "2026-09-18"},   # 已过期
        {"symbol": "SPY270618C00580000", "quantity": -2, "current_price": 15.0,
         "market_value": -3000.0, "strike": 580.0, "expiry": "2026-09-23"},  # dte=0
        {"symbol": "SPY271217C00600000", "quantity": 1, "current_price": 30.0,
         "market_value": 3000.0, "strike": 600.0, "expiry": "2027-12-17"},   # 远期
    ]
    kwargs = dict(
        underlying_prices={"SPY": 560.0},
        iv_map={"SPY": {"iv": 0.18, "src": "md"}},
        beta_map={"SPY": 1.2},
    )
    frozen_date = datetime.date(2026, 9, 23)

    with freeze_time(FROZEN_NOW):
        implicit = compute_portfolio_stress_test(stocks, options, **kwargs)
        explicit = compute_portfolio_stress_test(stocks, options, today=frozen_date, **kwargs)

    assert implicit == explicit, (
        "多腿混合到期日场景下 today=None 与 today=冻结日期 的输出不一致"
        "（含 nearest_expiry_date/nearest_expiry_sym 的选取）。"
    )
