"""Golden (characterization) tests for account.spread_pairing.build_spread_portfolios(df, today)
(see docs/architecture/spread_pairing_contracts_draft.py for the Protocol).

Phase 3 update (docs/REFACTORING_WORKFLOW.md): the pairing/recognition logic
that used to live inside account_monitor.py's private helpers has physically
moved to account/spread_pairing.py (no streamlit import, no I/O). This file
originally drove those helpers through an AST-sliced account_monitor.py
namespace via a hand-written harness that mirrored the orchestration body;
that harness is gone now -- this file calls the real, moved
`account.spread_pairing.build_spread_portfolios` directly. Before the move,
every scenario below was verified byte-for-byte identical between the harness
and the real acct_id entrypoint, and the snapshots did not need to change
when the import was swapped -- exactly the point of writing the tests this
way in Phase 2.

test_harness_matches_real_entrypoint now cross-checks the moved module against
account_monitor.py's thin wrapper `_build_spread_portfolios(acct_id)` (via a
throwaway SQLite DB) for one scenario per category, proving the wrapper still
delegates correctly after the move.

Behavior-preservation decision (user, 2026-09-22): a prior draft of this file's
sibling contract document (spread_pairing_contracts_draft.py) claimed missing
DataFrame columns raise KeyError in the current code. That claim was NOT
verified before being written and is WRONG -- see test_missing_optional_columns_*
below, which characterize what the code actually does: `_parse_spread_legs`
reads every field via `row.get(...)`, so missing optional numeric columns
silently default (unit_cost -> 0.0, cur_price/total_pnl/delta/iv -> None) and a
row with no usable `symbol` is silently dropped. No exception is raised on
missing columns. This file locks in that real (arguably still worth revisiting
later) behavior instead of a fictional crash; introducing real input validation
is explicitly out of scope for this refactor and must be a separate, later task.

Determinism: `today` is passed explicitly as a parameter -- no freezegun, no
global clock touched. This is a direct payoff of the new contract (arch.md
"无副作用纯内存"): once `_load_options_positions` is bypassed, the whole
pipeline has zero implicit-time dependency. Data is built directly as in-memory
pd.DataFrame objects -- no SQLite for the golden cases (SQLite is used only in
the cross-check test, to talk to the real acct_id-based entry point).

Regenerate snapshots ONLY on the unmodified original code:
    UPDATE_GOLDEN=1 python -m pytest tests/golden/test_spread_pairing_contract_golden.py -q
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

import pandas as pd
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
from account.options_repository import save_options_positions  # noqa: E402
from account.spread_pairing import build_spread_portfolios  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = pathlib.Path(__file__).parent / "snapshots_contract"
TODAY = datetime.date(2026, 9, 21)   # matches tests/golden/test_build_spread_portfolios_golden.py

# Expiries relative to TODAY (same table as the sibling acct_id-based golden file)
FAR = "270618"     # 2027-06-18, DTE 270  -> comfortably LOW / MEDIUM
MID = "261218"     # 2026-12-18, DTE 88
D14 = "261005"     # DTE 14 -> CRITICAL boundary (<= 14)
D15 = "261006"     # DTE 15 -> HIGH
D21 = "261012"     # DTE 21 -> HIGH boundary (<= 21)
D22 = "261013"     # DTE 22 -> LOW / MEDIUM
PAST = "260919"    # DTE -2 (already expired)


def _sym(root, yymmdd, cp, strike):
    return f"{root}{yymmdd}{cp}{int(round(strike * 1000)):08d}"


def _row(symbol, qty, direction, unit_cost, current_price=None, total_pnl=None,
         delta=None, iv=None):
    """One row shaped exactly like what `SELECT * FROM options_positions` would
    hand `_parse_spread_legs` via pandas -- only the columns it actually reads."""
    return {
        "symbol": symbol, "quantity": qty, "direction": direction,
        "unit_cost": unit_cost, "current_price": current_price,
        "total_pnl": total_pnl, "delta": delta, "iv": iv,
    }


def _vert(root, exp, cp, long_k, short_k, long_cost, short_cost,
          long_px, short_px, qty=1):
    return [
        _row(_sym(root, exp, cp, long_k), qty, "long", long_cost, long_px),
        _row(_sym(root, exp, cp, short_k), -qty, "short", short_cost, short_px),
    ]


# name -> list[row dict]; same scenarios (same symbols/strikes/costs) as
# tests/golden/test_build_spread_portfolios_golden.py, restated as DataFrame
# rows instead of SQLite inserts, so the two suites are directly comparable.
SCENARIOS: dict[str, list[dict]] = {
    "empty": [],
    "vertical_bull_call_debit": _vert("NVDA", FAR, "C", 100, 120, 12.5, 5.25, 14.0, 6.1),
    "vertical_bear_call_credit": _vert("AMD", FAR, "C", 160, 150, 3.2, 7.9, 2.1, 5.5),
    "vertical_bull_put_credit": _vert("AVGO", FAR, "P", 280, 300, 6.4, 11.3, 4.0, 8.2, qty=3),
    "vertical_bear_put_debit": _vert("ARM", FAR, "P", 400, 350, 8.0, 4.0, 6.0, 1.0),
    "vertical_partial_long_remainder": [
        _row(_sym("MU", FAR, "C", 90), 2, "long", 10.0, 11.5),
        _row(_sym("MU", FAR, "C", 110), -1, "short", 4.0, 3.3),
    ],
    "vertical_partial_short_remainder": [
        _row(_sym("TSM", FAR, "P", 200), 1, "long", 9.0, 8.0),
        _row(_sym("TSM", FAR, "P", 180), -3, "short", 5.0, 4.4),
    ],
    "vertical_one_long_two_shorts": [
        _row(_sym("META", FAR, "C", 500), 2, "long", 40.0, 42.0),
        _row(_sym("META", FAR, "C", 550), -1, "short", 22.0, 21.0),
        _row(_sym("META", FAR, "C", 600), -1, "short", 11.0, 10.5),
    ],
    "vertical_dte_boundaries": (
        _vert("AAA", D14, "C", 10, 12, 1.0, 0.4, 1.1, 0.3)
        + _vert("BBB", D15, "C", 10, 12, 1.0, 0.4, 1.1, 0.3)
        + _vert("CCC", D21, "P", 12, 10, 0.5, 1.2, 0.4, 1.0)
        + _vert("DDD", D22, "P", 12, 10, 0.5, 1.2, 0.4, 1.0)
        + _vert("EEE", PAST, "C", 10, 12, 1.0, 0.4, 0.0, 0.0)
    ),
    "calendar_same_strike": [
        _row(_sym("PLTR", MID, "C", 150), -1, "short", 6.0, 5.0),
        _row(_sym("PLTR", FAR, "C", 150), 1, "long", 18.0, 19.5),
    ],
    "diagonal_debit": [
        _row(_sym("CRWD", MID, "C", 480), -2, "short", 9.5, 7.0),
        _row(_sym("CRWD", FAR, "C", 400), 2, "long", 70.0, 76.0),
    ],
    "diagonal_credit": [
        _row(_sym("NOW", MID, "P", 900), -1, "short", 40.0, 38.0),
        _row(_sym("NOW", FAR, "P", 700), 1, "long", 25.0, 24.0),
    ],
    "diagonal_reversed": [
        _row(_sym("SNOW", MID, "C", 200), 1, "long", 12.0, 9.0),
        _row(_sym("SNOW", FAR, "C", 230), -1, "short", 15.0, 16.0),
    ],
    "diagonal_near_dte_critical_and_review": [
        _row(_sym("PANW", D14, "C", 200), -1, "short", 3.0, 2.0),
        _row(_sym("PANW", FAR, "C", 190), 1, "long", 30.0, 31.0),
        _row(_sym("PANW", D21, "P", 170), -1, "short", 2.5, 2.4),
        _row(_sym("PANW", FAR, "P", 160), 1, "long", 14.0, 13.0),
    ],
    "naked_all_kinds": [
        _row(_sym("LC", FAR, "C", 50), 2, "long", 4.0, 5.5),
        _row(_sym("LP", D14, "P", 50), 1, "long", 1.5, 0.9),
        _row(_sym("LQ", D21, "P", 50), 1, "long", 1.5, 1.2),
        _row(_sym("SP", FAR, "P", 80), -1, "short", 3.0, 2.2),
        _row(_sym("SQ", D14, "P", 80), -2, "short", 1.0, 1.6),
        _row(_sym("SC", FAR, "C", 90), -1, "short", 2.0, 2.5),
        _row(_sym("SD", D21, "C", 90), -1, "short", 0.8, 0.5),
    ],
    "no_current_price_falls_back_to_total_pnl": [
        _row(_sym("ONTO", FAR, "C", 150), 1, "long", 20.0, None, 125.0),
        _row(_sym("ONTO", FAR, "C", 170), -1, "short", 9.0, None, -40.0),
        _row(_sym("FTNT", FAR, "P", 90), -1, "short", 3.0, None, None),
    ],
    "mixed_missing_current_price": [
        _row(_sym("MRVL", FAR, "C", 80), 1, "long", 9.0, 10.0),
        _row(_sym("MRVL", FAR, "C", 95), -1, "short", 4.0, None, 55.0),
    ],
    "legacy_positive_qty_with_short_direction": [
        _row(_sym("QCOM", FAR, "C", 170), 1, "long", 11.0, 12.0),
        _row(_sym("QCOM", FAR, "C", 190), 1, "short", 5.0, 4.5),
    ],
    "positive_qty_with_wider_sell_synonym_direction": [
        # account/options.py::signed_quantity recognizes a wider vocabulary
        # than the old inline "direction == 'short'" check this module used
        # to have (see tests/test_signed_quantity.py); this scenario proves
        # the widened vocabulary flows through build_spread_portfolios too,
        # not just the isolated signed_quantity() unit tests.
        _row(_sym("QCOM", FAR, "C", 170), 1, "long", 11.0, 12.0),
        _row(_sym("QCOM", FAR, "C", 190), 1, "卖出", 5.0, 4.5),
    ],
    "skipped_and_unparseable_rows": [
        _row("GARBAGE", 1, "long", 1.0, 1.0),
        _row(_sym("ZERO", FAR, "C", 10), 0, "long", 1.0, 1.0),
        _row("BADM271318C00050000", 1, "long", 2.0, 2.5),
    ],
    "unit_cost_missing": [
        _row(_sym("NOCOST", FAR, "C", 25), 1, "long", None, 1.0),
    ],
    "greeks_present": [
        _row(_sym("GRK", FAR, "C", 100), 1, "long", 6.0, 6.6, delta=0.61, iv=0.45),
        _row(_sym("GRK", FAR, "C", 110), -1, "short", 2.5, 2.2, delta=-0.33, iv=0.41),
    ],
    "vertical_two_longs_one_short": [
        _row(_sym("INTC", FAR, "C", 30), 1, "long", 4.0, 4.4),
        _row(_sym("INTC", FAR, "C", 32), 1, "long", 3.0, 3.1),
        _row(_sym("INTC", FAR, "C", 40), -1, "short", 1.0, 0.7),
    ],
    "diagonal_long_remainder_two_shorts": [
        _row(_sym("ADBE", MID, "C", 400), 2, "long", 30.0, 33.0),
        _row(_sym("ADBE", FAR, "C", 420), 1, "long", 40.0, 41.0),
        _row(_sym("ADBE", D22, "C", 450), -1, "short", 5.0, 4.0),
        _row(_sym("ADBE", "261106", "C", 460), -1, "short", 6.0, 5.5),
    ],
    "multi_underlying_sort_order": (
        _vert("ZZZ", FAR, "C", 10, 12, 1.0, 0.4, 1.1, 0.3)
        + _vert("AAB", FAR, "C", 10, 12, 1.0, 0.4, 1.1, 0.3)
        + [_row(_sym("MMM", D14, "C", 20), 1, "long", 1.0, 0.5),
           _row(_sym("BBC", FAR, "P", 20), -1, "short", 1.0, 0.8),
           _row(_sym("AAC", FAR, "C", 20), 1, "long", 1.0, 1.2)]
    ),
    "mixed_everything_one_underlying": [
        _row(_sym("MIX", FAR, "C", 100), 2, "long", 10.0, 11.0),
        _row(_sym("MIX", FAR, "C", 120), -1, "short", 4.0, 3.0),
        _row(_sym("MIX", MID, "C", 130), -1, "short", 2.0, 1.5),
        _row(_sym("MIX", MID, "P", 90), 1, "long", 3.0, 2.0),
        _row(_sym("MIX", D15, "P", 85), -2, "short", 1.0, 1.4),
    ],
    # ── Missing-column characterization (see module docstring) ──────────────
    "missing_optional_columns_silently_default": [
        {"symbol": _sym("GAP", FAR, "C", 50), "quantity": 1, "direction": "long"},
        # no unit_cost / current_price / total_pnl / delta / iv columns at all
    ],
    "missing_symbol_column_drops_all_rows": [
        {"quantity": 1, "direction": "long", "unit_cost": 5.0},
        # no `symbol` column -> row.get("symbol","") == "" -> _parse_occ fails -> skipped
    ],
}


@pytest.fixture(scope="module")
def ns():
    """AST-slice account_monitor.py (only the pre-st.set_page_config portion) and
    exec it into a namespace, redirecting the DB path first so _init_db() never
    touches the real data/energrex.db.

    Post-move (Phase 3), this is only needed by test_harness_matches_real_entrypoint,
    to reach the thin wrapper `_build_spread_portfolios(acct_id)` and
    `_load_options_positions` that still live in account_monitor.py. The pairing
    logic itself is imported directly from account.spread_pairing -- see the
    module-level import above."""
    tmp_dir = tempfile.mkdtemp()
    original = account_db.DB_PATH
    account_db.DB_PATH = pathlib.Path(tmp_dir) / "contract_probe.db"
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
        exec(compile(filtered, str(ROOT / "account_monitor.py"), "exec"), namespace)  # runs _init_db() on the temp DB
        yield namespace
    finally:
        account_db.DB_PATH = original


def _load_df(rows: list[dict]) -> pd.DataFrame:
    """Build the DataFrame the way a real caller will hand it in.

    account.options_repository.load_options_positions() runs
    `SELECT * FROM options_positions WHERE account_id=? ORDER BY expiry, symbol`.
    Every row this test suite (and its sibling acct_id-based suite) inserts has
    `expiry=NULL` (the code re-derives expiry from the OCC symbol instead of
    trusting the stored column -- see account.spread_pairing._parse_spread_legs),
    so with the primary sort key constant/NULL across all rows, `symbol` alone
    is the effective, empirically-verified tie-breaker. Skipping this sort was
    caught by test_harness_matches_real_entrypoint: the `li`/`si` positional
    index embedded in each leg depends on row order, so an unsorted synthetic
    DataFrame produces snapshots that a real (sorted) caller would never
    actually reproduce.
    """
    df = pd.DataFrame(rows)
    if "symbol" in df.columns:
        df = df.sort_values("symbol", kind="stable").reset_index(drop=True)
    return df


def _canonical(result) -> str:
    return json.dumps(result, ensure_ascii=False, indent=1, allow_nan=True) + "\n"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_golden(name):
    df = _load_df(SCENARIOS[name])
    actual = _canonical(build_spread_portfolios(df, TODAY))

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
    on_disk = {p.stem for p in SNAPSHOT_DIR.glob("*.json")}
    assert on_disk == set(SCENARIOS)


def test_thin_wrapper_still_delegates_correctly(ns):
    """Proves account_monitor.py's thin wrapper `_build_spread_portfolios(acct_id)`
    still produces exactly what the moved `account.spread_pairing.build_spread_portfolios`
    produces: for one scenario per category, seed the SAME rows into a throwaway
    DB, then compare the wrapper (via freezegun pinning date.today() to TODAY,
    since the wrapper still calls datetime.date.today() internally) against a
    direct call fed with the EXACT DataFrame `_load_options_positions` returns
    for that account -- not an independently-reconstructed DataFrame, so no
    assumption about row order needs to be made or kept in sync by hand."""
    from freezegun import freeze_time

    for name in ("vertical_bull_call_debit", "diagonal_debit", "naked_all_kinds"):
        acct = f"crosscheck_{name}"
        save_options_positions(acct, [
            {**row, "strike": None, "expiry": None, "market_value": None, "day_pnl": None}
            for row in SCENARIOS[name]
        ])
        real_df = ns["_load_options_positions"](acct)
        with freeze_time(TODAY.isoformat()):
            via_wrapper = ns["_build_spread_portfolios"](acct)
        via_direct_import = build_spread_portfolios(real_df, TODAY)
        assert _canonical(via_wrapper) == _canonical(via_direct_import), (
            f"{name}: account_monitor.py's thin wrapper no longer matches "
            "account.spread_pairing.build_spread_portfolios -- the wrapper has drifted"
        )
