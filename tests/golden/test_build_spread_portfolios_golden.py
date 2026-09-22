"""Golden (characterization) tests for account_monitor._build_spread_portfolios.

Locks the *entire* current output -- every key, in insertion order, including
recommendation text, risk_level, dte and id -- so the function can be split
into private helpers without any behaviour change. Existing tests
(test_spread_pairing / test_spread_calculations) assert pairing and money
fields only; they would stay green if the text or risk levels drifted.

These snapshots record what the code does today, not what it should do.
If a snapshot looks wrong, report it -- do not "fix" it here.

Determinism:
  * today is frozen at 2026-09-21 (freezegun), so every DTE is fixed
  * all positions are synthetic and live in a throw-away SQLite DB
  * snapshots are UTF-8, LF, keys kept in insertion order (dict order is
    part of the contract: pd.DataFrame(portfolios) column order follows it)

Regenerate snapshots ONLY on the unmodified original code:
    UPDATE_GOLDEN=1 python -m pytest tests/golden -q
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
from account.options_repository import save_options_positions  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = pathlib.Path(__file__).parent / "snapshots"
FROZEN_TODAY = "2026-09-21"

# Expiries relative to FROZEN_TODAY
FAR = "270618"     # 2027-06-18, DTE 270  → comfortably LOW / MEDIUM
MID = "261218"     # 2026-12-18, DTE 88
D14 = "261005"     # DTE 14 → CRITICAL boundary (<= 14)
D15 = "261006"     # DTE 15 → HIGH
D21 = "261012"     # DTE 21 → HIGH boundary (<= 21)
D22 = "261013"     # DTE 22 → LOW / MEDIUM
PAST = "260919"    # DTE -2 (already expired)


def _sym(root, yymmdd, cp, strike):
    return f"{root}{yymmdd}{cp}{int(round(strike * 1000)):08d}"


def _pos(symbol, qty, direction, unit_cost, current_price=None, total_pnl=None):
    return {
        "symbol": symbol, "quantity": qty, "direction": direction,
        "unit_cost": unit_cost, "current_price": current_price,
        "market_value": None, "day_pnl": None, "total_pnl": total_pnl,
        "strike": None, "expiry": None,
    }


def _vert(root, exp, cp, long_k, short_k, long_cost, short_cost,
          long_px, short_px, qty=1):
    return [
        _pos(_sym(root, exp, cp, long_k), qty, "long", long_cost, long_px),
        _pos(_sym(root, exp, cp, short_k), -qty, "short", short_cost, short_px),
    ]


# name → (rows, extra SQL updates [(sql, params)])
SCENARIOS = {
    "empty": ([], []),
    "vertical_bull_call_debit": (
        _vert("NVDA", FAR, "C", 100, 120, 12.5, 5.25, 14.0, 6.1), []),
    "vertical_bear_call_credit": (
        _vert("AMD", FAR, "C", 160, 150, 3.2, 7.9, 2.1, 5.5), []),
    "vertical_bull_put_credit": (
        _vert("AVGO", FAR, "P", 280, 300, 6.4, 11.3, 4.0, 8.2, qty=3), []),
    "vertical_bear_put_debit": (
        _vert("ARM", FAR, "P", 400, 350, 8.0, 4.0, 6.0, 1.0), []),
    "vertical_partial_long_remainder": ([
        _pos(_sym("MU", FAR, "C", 90), 2, "long", 10.0, 11.5),
        _pos(_sym("MU", FAR, "C", 110), -1, "short", 4.0, 3.3),
    ], []),
    "vertical_partial_short_remainder": ([
        _pos(_sym("TSM", FAR, "P", 200), 1, "long", 9.0, 8.0),
        _pos(_sym("TSM", FAR, "P", 180), -3, "short", 5.0, 4.4),
    ], []),
    "vertical_one_long_two_shorts": ([
        _pos(_sym("META", FAR, "C", 500), 2, "long", 40.0, 42.0),
        _pos(_sym("META", FAR, "C", 550), -1, "short", 22.0, 21.0),
        _pos(_sym("META", FAR, "C", 600), -1, "short", 11.0, 10.5),
    ], []),
    "vertical_dte_boundaries": (
        _vert("AAA", D14, "C", 10, 12, 1.0, 0.4, 1.1, 0.3)
        + _vert("BBB", D15, "C", 10, 12, 1.0, 0.4, 1.1, 0.3)
        + _vert("CCC", D21, "P", 12, 10, 0.5, 1.2, 0.4, 1.0)
        + _vert("DDD", D22, "P", 12, 10, 0.5, 1.2, 0.4, 1.0)
        + _vert("EEE", PAST, "C", 10, 12, 1.0, 0.4, 0.0, 0.0), []),
    "calendar_same_strike": ([
        _pos(_sym("PLTR", MID, "C", 150), -1, "short", 6.0, 5.0),
        _pos(_sym("PLTR", FAR, "C", 150), 1, "long", 18.0, 19.5),
    ], []),
    "diagonal_debit": ([
        _pos(_sym("CRWD", MID, "C", 480), -2, "short", 9.5, 7.0),
        _pos(_sym("CRWD", FAR, "C", 400), 2, "long", 70.0, 76.0),
    ], []),
    "diagonal_credit": ([
        _pos(_sym("NOW", MID, "P", 900), -1, "short", 40.0, 38.0),
        _pos(_sym("NOW", FAR, "P", 700), 1, "long", 25.0, 24.0),
    ], []),
    "diagonal_reversed": ([
        _pos(_sym("SNOW", MID, "C", 200), 1, "long", 12.0, 9.0),
        _pos(_sym("SNOW", FAR, "C", 230), -1, "short", 15.0, 16.0),
    ], []),
    "diagonal_near_dte_critical_and_review": ([
        _pos(_sym("PANW", D14, "C", 200), -1, "short", 3.0, 2.0),
        _pos(_sym("PANW", FAR, "C", 190), 1, "long", 30.0, 31.0),
        _pos(_sym("PANW", D21, "P", 170), -1, "short", 2.5, 2.4),
        _pos(_sym("PANW", FAR, "P", 160), 1, "long", 14.0, 13.0),
    ], []),
    "naked_all_kinds": ([
        _pos(_sym("LC", FAR, "C", 50), 2, "long", 4.0, 5.5),
        _pos(_sym("LP", D14, "P", 50), 1, "long", 1.5, 0.9),
        _pos(_sym("LQ", D21, "P", 50), 1, "long", 1.5, 1.2),
        _pos(_sym("SP", FAR, "P", 80), -1, "short", 3.0, 2.2),
        _pos(_sym("SQ", D14, "P", 80), -2, "short", 1.0, 1.6),
        _pos(_sym("SC", FAR, "C", 90), -1, "short", 2.0, 2.5),
        _pos(_sym("SD", D21, "C", 90), -1, "short", 0.8, 0.5),
    ], []),
    "no_current_price_falls_back_to_total_pnl": ([
        _pos(_sym("ONTO", FAR, "C", 150), 1, "long", 20.0, None, 125.0),
        _pos(_sym("ONTO", FAR, "C", 170), -1, "short", 9.0, None, -40.0),
        _pos(_sym("FTNT", FAR, "P", 90), -1, "short", 3.0, None, None),
    ], []),
    "mixed_missing_current_price": ([
        _pos(_sym("MRVL", FAR, "C", 80), 1, "long", 9.0, 10.0),
        _pos(_sym("MRVL", FAR, "C", 95), -1, "short", 4.0, None, 55.0),
    ], []),
    "legacy_positive_qty_with_short_direction": ([
        _pos(_sym("QCOM", FAR, "C", 170), 1, "long", 11.0, 12.0),
        _pos(_sym("QCOM", FAR, "C", 190), 1, "short", 5.0, 4.5),
    ], []),
    "skipped_and_unparseable_rows": ([
        _pos("GARBAGE", 1, "long", 1.0, 1.0),
        _pos(_sym("ZERO", FAR, "C", 10), 0, "long", 1.0, 1.0),
        _pos("BADM271318C00050000", 1, "long", 2.0, 2.5),
    ], []),
    "unit_cost_missing": ([
        _pos(_sym("NOCOST", FAR, "C", 25), 1, "long", None, 1.0),
    ], []),
    "greeks_present": (
        _vert("GRK", FAR, "C", 100, 110, 6.0, 2.5, 6.6, 2.2),
        [("UPDATE options_positions SET delta=?, iv=? WHERE account_id=? AND symbol=?",
          (0.61, 0.45, "{acct}", _sym("GRK", FAR, "C", 100))),
         ("UPDATE options_positions SET delta=?, iv=? WHERE account_id=? AND symbol=?",
          (-0.33, 0.41, "{acct}", _sym("GRK", FAR, "C", 110)))]),
    # 2nd long finds the only short already consumed by the 1st → skips it
    "vertical_two_longs_one_short": ([
        _pos(_sym("INTC", FAR, "C", 30), 1, "long", 4.0, 4.4),
        _pos(_sym("INTC", FAR, "C", 32), 1, "long", 3.0, 3.1),
        _pos(_sym("INTC", FAR, "C", 40), -1, "short", 1.0, 0.7),
    ], []),
    # diagonal long with remainder walks on to the next short; a later long
    # then meets only consumed shorts
    "diagonal_long_remainder_two_shorts": ([
        _pos(_sym("ADBE", MID, "C", 400), 2, "long", 30.0, 33.0),
        _pos(_sym("ADBE", FAR, "C", 420), 1, "long", 40.0, 41.0),
        _pos(_sym("ADBE", D22, "C", 450), -1, "short", 5.0, 4.0),
        _pos(_sym("ADBE", "261106", "C", 460), -1, "short", 6.0, 5.5),
    ], []),
    "multi_underlying_sort_order": (
        _vert("ZZZ", FAR, "C", 10, 12, 1.0, 0.4, 1.1, 0.3)
        + _vert("AAB", FAR, "C", 10, 12, 1.0, 0.4, 1.1, 0.3)
        + [_pos(_sym("MMM", D14, "C", 20), 1, "long", 1.0, 0.5),
           _pos(_sym("BBC", FAR, "P", 20), -1, "short", 1.0, 0.8),
           _pos(_sym("AAC", FAR, "C", 20), 1, "long", 1.0, 1.2)], []),
    "mixed_everything_one_underlying": ([
        _pos(_sym("MIX", FAR, "C", 100), 2, "long", 10.0, 11.0),
        _pos(_sym("MIX", FAR, "C", 120), -1, "short", 4.0, 3.0),
        _pos(_sym("MIX", MID, "C", 130), -1, "short", 2.0, 1.5),
        _pos(_sym("MIX", MID, "P", 90), 1, "long", 3.0, 2.0),
        _pos(_sym("MIX", D15, "P", 85), -2, "short", 1.0, 1.4),
    ], []),
}


@pytest.fixture(scope="module")
def build():
    tmp = tempfile.TemporaryDirectory()
    original = account_db.DB_PATH
    account_db.DB_PATH = pathlib.Path(tmp.name) / "golden_spread.db"
    try:
        src = (ROOT / "account_monitor.py").read_text(encoding="utf-8-sig")
        cutoff = next((i + 1 for i, ln in enumerate(src.splitlines())
                       if "st.set_page_config" in ln), 99999)
        tree = ast.parse(src, filename="account_monitor.py")
        filtered = ast.Module(
            body=[n for n in tree.body if getattr(n, "lineno", 0) < cutoff],
            type_ignores=[])
        ast.fix_missing_locations(filtered)
        ns = {"__file__": str(ROOT / "account_monitor.py"), "__name__": "account_monitor"}
        exec(compile(filtered, str(ROOT / "account_monitor.py"), "exec"), ns)  # runs _init_db() on the temp DB
        yield ns["_build_spread_portfolios"]
    finally:
        account_db.DB_PATH = original
        tmp.cleanup()


def _seed(acct, rows, updates):
    if rows:
        save_options_positions(acct, rows)
    if updates:
        conn = account_db.db()
        for sql, params in updates:
            conn.execute(sql, tuple(acct if p == "{acct}" else p for p in params))
        conn.commit()
        conn.close()


def _canonical(result) -> str:
    # insertion order preserved on purpose (no sort_keys); NaN kept as NaN
    return json.dumps(result, ensure_ascii=False, indent=1, allow_nan=True) + "\n"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_golden(build, name):
    rows, updates = SCENARIOS[name]
    acct = f"golden_{name}"
    _seed(acct, rows, updates)
    with freeze_time(FROZEN_TODAY):
        actual = _canonical(build(acct))

    snapshot = SNAPSHOT_DIR / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not snapshot.exists():
        pytest.fail(f"missing golden snapshot {snapshot.name}; generate it with "
                    "UPDATE_GOLDEN=1 on the UNMODIFIED original code")
    assert actual == snapshot.read_text(encoding="utf-8"), (
        f"output differs from golden snapshot {name} -> behaviour changed")


def test_every_scenario_has_a_snapshot():
    """Guards against a scenario being added without its snapshot (or vice versa)."""
    on_disk = {p.stem for p in SNAPSHOT_DIR.glob("*.json")}
    assert on_disk == set(SCENARIOS)


def test_frozen_clock_is_what_the_function_sees(build):
    """If freezegun stopped reaching the exec'd namespace, every DTE would drift daily."""
    acct = "golden_clock_probe"
    _seed(acct, [_pos(_sym("CLK", D14, "C", 10), 1, "long", 1.0, 1.0)], [])
    with freeze_time(FROZEN_TODAY):
        (port,) = build(acct)
    assert port["dte"] == 14
