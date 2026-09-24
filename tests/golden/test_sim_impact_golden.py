"""Golden (characterization) tests for account_monitor._compute_sim_impact,
ahead of moving its pure core into account/risk.py -- the last remaining
orphan closure per .refactor_status.json PHASE_1_SCOUTING (see
_scratch_handoff_sim_impact.md and docs/REFACTORING_WORKFLOW.md).

These lock the CURRENT unmodified implementation's behavior via the real
function inside account_monitor.py, reached through the same AST-slice +
streamlit-stub technique as the sibling tests/golden/test_risk_and_iv_golden.py
suite -- not an invented contract implementation.

Determinism sources pinned (both newly confirmed while reading the real
code, a subset of the two the risk_snapshot/iv_regime golden suite pins):
  - `_BETA_SPY`: module-level global dict read via `.get(und, 1.0)` inside
    the close_underlying branch. Overridden to a fixed dict on the exec'd
    namespace before any test call.
  - `_fetch_underlying_prices`: real network call, batched once per call
    over the deduplicated set of underlyings. Overridden to a fixed lookup
    table on the exec'd namespace.
_compute_sim_impact itself never touches datetime/random/uuid/env, so no
freezegun is needed here (unlike the risk_snapshot/iv_regime suite).
`_bs_greeks` (account.risk.bs_greeks, used by the qqq_hedge branch) is
already a pure, deterministic math function -- not mocked, called for real.

The DB dependency (`_db()` + one SELECT against options_positions per
close_underlying action, scoped to `symbol LIKE '{underlying}%'`) is
exercised against a real throwaway sqlite DB seeded per scenario -- not
mocked -- since sqlite reads are deterministic and this SELECT is exactly
the dependency the planned refactor will hoist into an IO-prefetch shell
(deduplicated by underlying, see _scratch_handoff_sim_impact.md).

Regenerate ONLY on the unmodified original code:
    UPDATE_GOLDEN=1 python -m pytest tests/golden/test_sim_impact_golden.py -q
A missing snapshot fails the test; it is never created silently.
"""

import ast
import json
import os
import pathlib
import sys
import tempfile
import types

import pytest

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

ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = pathlib.Path(__file__).parent / "snapshots_sim_impact"

# Fixed, synthetic stand-ins for the one network call -- never a live quote.
_FIXED_PRICES = {"NVDA": 120.0, "AMD": 150.0, "QQQ": 560.0}
_FIXED_BETA = {"NVDA": 1.8, "AMD": 2.1}


@pytest.fixture(scope="module")
def ns():
    """AST-slice account_monitor.py, exec into a namespace on a throwaway DB,
    then pin the two module-level non-determinism sources discovered while
    reading the real code (see module docstring)."""
    tmp_dir = tempfile.mkdtemp()
    original = account_db.DB_PATH
    account_db.DB_PATH = pathlib.Path(tmp_dir) / "sim_impact_probe.db"
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

        # Pin the two globals that are otherwise computed from disk + a real
        # network call -- see module docstring.
        namespace["_BETA_SPY"] = dict(_FIXED_BETA)
        namespace["_fetch_underlying_prices"] = (
            lambda tickers: {t: _FIXED_PRICES[t] for t in tickers if t in _FIXED_PRICES}
        )
        yield namespace
    finally:
        account_db.DB_PATH = original


def _conn():
    return account_db.db()


def _seed_option_row(acct, symbol, *, quantity, current_price, delta=0.0, gamma=0.0,
                      theta=0.0, vega=0.0, market_value=0.0, direction="long"):
    """Seed one options_positions row with the greeks columns _compute_sim_impact
    actually selects (quantity/current_price/delta/gamma/theta/vega/market_value)
    -- a superset of the columns the sibling risk_snapshot golden suite's
    _seed_options helper fills, since that suite never reads greeks."""
    conn = _conn()
    conn.execute(
        "INSERT INTO options_positions (account_id, symbol, direction, strike, expiry, "
        "quantity, unit_cost, current_price, delta, gamma, theta, vega, market_value) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (acct, symbol, direction, 100.0, "2027-06-18", quantity, 0.0, current_price,
         delta, gamma, theta, vega, market_value),
    )
    conn.commit()
    conn.close()


def _canonical(result) -> str:
    return json.dumps(result, ensure_ascii=False, indent=1, sort_keys=False,
                       allow_nan=True, default=str) + "\n"


def _no_seed(acct):
    pass


def _seed_nvda_normal(acct):
    _seed_option_row(acct, "NVDA270618C00100000", quantity=2, current_price=10.0,
                      delta=0.5, gamma=0.01, theta=-0.05, vega=0.2, market_value=2000.0)


def _seed_zzzz_missing_price(acct):
    # "ZZZZ" is intentionally absent from _FIXED_PRICES -> S falls back to 0.0
    # inside _compute_sim_impact's `_und_p.get(und, 0.0)`.
    _seed_option_row(acct, "ZZZZ270618C00050000", quantity=3, current_price=2.5,
                      delta=0.4, gamma=0.02, theta=-0.03, vega=0.15, market_value=750.0)


BASE_SNAP_DEFAULT = {
    "equity": 100_000.0,
    "beta_delta": 5000.0,
    "theta_per_day": -50.0,
    "stress_10": -2000.0,
    "stress_20": -4000.0,
    "vega_per_pt": 300.0,
}

_HPLAN_ZERO_N = {
    "plan_a": {"n_total": 0, "buy_strike": 550.0, "sell_strike": 530.0, "total_cost": 1000.0},
    "qqq_price": 560.0, "qqq_iv": 18.0, "b_qqq": 1.0, "plan_dte": 30,
}
_HPLAN_NORMAL = {
    "plan_a": {"n_total": 3, "buy_strike": 550.0, "sell_strike": 530.0, "total_cost": 900.0},
    "qqq_price": 560.0, "qqq_iv": 18.0, "b_qqq": 1.0, "plan_dte": 30,
}

# name -> (seed_fn, sim_actions, base_snap, hplan)
SCENARIOS = {
    "empty_actions": dict(
        seed=_no_seed, sim_actions=[],
        base_snap=BASE_SNAP_DEFAULT, hplan=None,
    ),
    "unknown_type_default_label": dict(
        seed=_no_seed, sim_actions=[{"type": "no_sim"}],
        base_snap=BASE_SNAP_DEFAULT, hplan=None,
    ),
    "unknown_type_custom_label": dict(
        seed=_no_seed, sim_actions=[{"type": "hold", "label": "部分平仓观察"}],
        base_snap=BASE_SNAP_DEFAULT, hplan=None,
    ),
    "close_underlying_normal": dict(
        seed=_seed_nvda_normal,
        sim_actions=[{"type": "close_underlying", "underlying": "NVDA"}],
        base_snap=BASE_SNAP_DEFAULT, hplan=None,
    ),
    "close_underlying_no_matching_positions": dict(
        seed=_no_seed,
        sim_actions=[{"type": "close_underlying", "underlying": "AMD"}],
        base_snap=BASE_SNAP_DEFAULT, hplan=None,
    ),
    "close_underlying_missing_price": dict(
        seed=_seed_zzzz_missing_price,
        sim_actions=[{"type": "close_underlying", "underlying": "ZZZZ"}],
        base_snap=BASE_SNAP_DEFAULT, hplan=None,
    ),
    "close_underlying_duplicate_underlying": dict(
        seed=_seed_nvda_normal,
        sim_actions=[
            {"type": "close_underlying", "underlying": "NVDA"},
            {"type": "close_underlying", "underlying": "NVDA"},
        ],
        base_snap=BASE_SNAP_DEFAULT, hplan=None,
    ),
    "qqq_hedge_none_hplan": dict(
        seed=_no_seed, sim_actions=[{"type": "qqq_hedge"}],
        base_snap=BASE_SNAP_DEFAULT, hplan=None,
    ),
    "qqq_hedge_error_hplan": dict(
        seed=_no_seed, sim_actions=[{"type": "qqq_hedge"}],
        base_snap=BASE_SNAP_DEFAULT, hplan={"error": "insufficient_data"},
    ),
    "qqq_hedge_zero_n": dict(
        seed=_no_seed, sim_actions=[{"type": "qqq_hedge"}],
        base_snap=BASE_SNAP_DEFAULT, hplan=_HPLAN_ZERO_N,
    ),
    "qqq_hedge_normal": dict(
        seed=_no_seed, sim_actions=[{"type": "qqq_hedge"}],
        base_snap=BASE_SNAP_DEFAULT, hplan=_HPLAN_NORMAL,
    ),
    "equity_zero_div_guard": dict(
        seed=_no_seed, sim_actions=[],
        base_snap={**BASE_SNAP_DEFAULT, "equity": 0.0}, hplan=None,
    ),
    "multi_action_stacking": dict(
        seed=_seed_nvda_normal,
        sim_actions=[
            {"type": "close_underlying", "underlying": "NVDA"},
            {"type": "qqq_hedge"},
        ],
        base_snap=BASE_SNAP_DEFAULT, hplan=_HPLAN_NORMAL,
    ),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_sim_impact_golden(ns, name):
    acct = f"sim_{name}"
    spec = SCENARIOS[name]
    spec["seed"](acct)
    result = ns["_compute_sim_impact"](acct, spec["sim_actions"], spec["base_snap"], spec["hplan"])
    actual = _canonical(result)

    snapshot = SNAPSHOT_DIR / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not snapshot.exists():
        pytest.fail(f"missing golden snapshot {snapshot.name}; generate with "
                    "UPDATE_GOLDEN=1 on the UNMODIFIED original code")
    assert actual == snapshot.read_text(encoding="utf-8"), (
        f"sim_impact output differs from golden snapshot {name} -> behaviour changed")


def test_every_scenario_has_a_snapshot():
    on_disk = {p.stem for p in SNAPSHOT_DIR.glob("*.json")}
    expected = set(SCENARIOS)
    assert on_disk == expected
