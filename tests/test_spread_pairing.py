"""Tests for _build_spread_portfolios spread pairing logic.

Uses AST filtering (same technique as _run_sync.py) to load only the
function-definition portion of account_monitor.py without running any
Streamlit UI code.  A temporary SQLite database is used so the production
database is never touched.
"""

import ast
import pathlib
import sys
import tempfile
import types
import unittest

# ── Mock Streamlit before account_monitor is exec'd ──────────────────────────
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

# ── Now safe to import account packages ──────────────────────────────────────
import account.db as account_db
from account.options_repository import save_options_positions

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCT = "test_acct"


# ── Helper: minimal position dict accepted by save_options_positions ──────────
def _pos(symbol: str, qty: int, direction: str, unit_cost: float,
         current_price: float | None = None) -> dict:
    return {
        "symbol": symbol,
        "quantity": qty,
        "direction": direction,
        "unit_cost": unit_cost,
        "current_price": current_price,
        "market_value": None,
        "day_pnl": None,
        "total_pnl": None,
        "strike": None,
        "expiry": None,
    }


class SpreadPairingTests(unittest.TestCase):
    """
    Each test inserts positions into a temp DB, calls _build_spread_portfolios,
    and asserts on the returned portfolio list.

    OCC symbols used:
      - Far expiry  2027-06-18  → 270618  (DTE ≈ 365, well above CRITICAL/REVIEW)
      - Near expiry 2026-09-19  → 260919  (DTE ≈ 93, still above REVIEW=21)
    This keeps risk_level out of CRITICAL/HIGH so it does not interfere with
    type / pairing assertions.
    """

    _tmp = None
    _original_db_path = None
    _build = None

    @classmethod
    def setUpClass(cls):
        # Point account.db at a throw-away database for the whole test class.
        cls._tmp = tempfile.TemporaryDirectory()
        cls._original_db_path = account_db.DB_PATH
        account_db.DB_PATH = pathlib.Path(cls._tmp.name) / "test_spread.db"

        # AST-filter account_monitor.py: keep only nodes before st.set_page_config.
        src = (ROOT / "account_monitor.py").read_text(encoding="utf-8-sig")
        ui_line = next(
            (i + 1 for i, line in enumerate(src.splitlines())
             if "st.set_page_config" in line),
            99999,
        )
        tree = ast.parse(src, filename="account_monitor.py")
        filtered = ast.Module(
            body=[n for n in tree.body if getattr(n, "lineno", 0) < ui_line],
            type_ignores=[],
        )
        ast.fix_missing_locations(filtered)
        ns = {
            "__file__": str(ROOT / "account_monitor.py"),
            "__name__": "account_monitor",
        }
        # This exec calls _init_db() (line 67 of account_monitor.py),
        # creating all tables in the temp database.
        exec(compile(filtered, str(ROOT / "account_monitor.py"), "exec"), ns)
        cls._build = staticmethod(ns["_build_spread_portfolios"])

    @classmethod
    def tearDownClass(cls):
        account_db.DB_PATH = cls._original_db_path
        if cls._tmp:
            cls._tmp.cleanup()

    def setUp(self):
        conn = account_db.db()
        conn.execute("DELETE FROM options_positions WHERE account_id=?", (ACCT,))
        conn.commit()
        conn.close()

    def _save(self, rows: list[dict]) -> None:
        save_options_positions(ACCT, rows)

    # ── Vertical spread recognition ───────────────────────────────────────────

    def test_bear_put_spread_type_and_metrics(self):
        """Long higher-strike put + short lower-strike put → Bear Put Spread (debit).

        ARM 400P long  unit_cost=8.0  current_price=6.0
        ARM 350P short unit_cost=4.0  current_price=1.0

        net_per_share = 8.0 - 4.0 = 4.0  (debit)
        max_loss      = 4.0 × 1 × 100 = $400
        max_profit    = (50 - 4.0) × 1 × 100 = $4 600
        breakeven     = 400 - 4.0 = 396.0
        current_pnl   = (6-8)×1×100 + (1-4)×(-1)×100 = -200 + 300 = $100
        """
        self._save([
            _pos("ARM270618P00400000",  1, "long",  8.0, current_price=6.0),
            _pos("ARM270618P00350000", -1, "short", 4.0, current_price=1.0),
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertEqual(p["type"], "Bear Put Spread")
        self.assertTrue(p["is_debit"])
        self.assertEqual(p["underlying"], "ARM")
        self.assertEqual(p["low_strike"], 350.0)
        self.assertEqual(p["high_strike"], 400.0)
        self.assertAlmostEqual(p["net_per_share"], 4.0)
        self.assertAlmostEqual(p["max_loss"], 400.0)
        self.assertAlmostEqual(p["max_profit"], 4600.0)
        self.assertAlmostEqual(p["breakeven"], 396.0)
        self.assertAlmostEqual(p["current_pnl"], 100.0)

    def test_bull_call_spread_type_and_metrics(self):
        """Long lower-strike call + short higher-strike call → Bull Call Spread (debit).

        META 600C long  unit_cost=25.0
        META 800C short unit_cost=10.0

        net_per_share = 25.0 - 10.0 = 15.0  (debit)
        max_loss      = 15.0 × 1 × 100 = $1 500
        max_profit    = (200 - 15.0) × 1 × 100 = $18 500
        breakeven     = 600 + 15.0 = 615.0
        """
        self._save([
            _pos("META270618C00600000",  1, "long",  25.0),
            _pos("META270618C00800000", -1, "short", 10.0),
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertEqual(p["type"], "Bull Call Spread")
        self.assertTrue(p["is_debit"])
        self.assertAlmostEqual(p["net_per_share"], 15.0)
        self.assertAlmostEqual(p["max_loss"], 1500.0)
        self.assertAlmostEqual(p["max_profit"], 18500.0)
        self.assertAlmostEqual(p["breakeven"], 615.0)

    def test_bear_call_spread_credit(self):
        """Long higher-strike call + short lower-strike call → Bear Call Spread (credit).

        PLTR 190C long  unit_cost=3.0   (hedge leg)
        PLTR 150C short unit_cost=12.0  (income leg)

        net_per_share = 3.0 - 12.0 = -9.0  (credit)
        max_profit    = abs(-9.0 × 1 × 100) = $900
        max_loss      = (40 - 9.0) × 1 × 100 = $3 100
        breakeven     = 150 + 9.0 = 159.0
        """
        self._save([
            _pos("PLTR270618C00190000",  1, "long",   3.0),
            _pos("PLTR270618C00150000", -1, "short", 12.0),
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertEqual(p["type"], "Bear Call Spread")
        self.assertFalse(p["is_debit"])
        self.assertAlmostEqual(p["net_per_share"], -9.0)
        self.assertAlmostEqual(p["max_profit"], 900.0)
        self.assertAlmostEqual(p["max_loss"], 3100.0)
        self.assertAlmostEqual(p["breakeven"], 159.0)

    # ── Backward compatibility ────────────────────────────────────────────────

    def test_backward_compat_unsigned_qty_direction_short(self):
        """Old DB rows: qty=1 + direction='short' must be treated as the short leg.

        This is the bug that caused all spreads to appear as Naked positions
        before the signed-quantity fix.  The backward-compat path flips qty to
        negative when direction='short' and qty > 0.
        """
        self._save([
            _pos("PLTR270618C00190000", 1, "long",   3.0),
            _pos("PLTR270618C00150000", 1, "short", 12.0),  # ← old-style unsigned
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        # Should pair as Bear Call Spread, NOT two naked longs
        self.assertEqual(p["type"], "Bear Call Spread")
        self.assertFalse(p["is_debit"])

    # ── Naked fallback ────────────────────────────────────────────────────────

    def test_naked_long_put_no_counterpart(self):
        """Single long put with no matching short → Naked Long Put."""
        self._save([_pos("ARM270618P00400000", 1, "long", 8.0)])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "Naked Long Put")
        self.assertEqual(result[0]["underlying"], "ARM")

    def test_naked_short_call(self):
        """Single short call → Naked Short Call."""
        self._save([_pos("NVDA270618C00200000", -1, "short", 5.0)])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "Naked Short Call")

    # ── Cross-expiry matching ─────────────────────────────────────────────────

    def test_diagonal_spread_proper_near_short_far_long(self):
        """Same underlying + put + different expiry + near=short far=long → Diagonal Spread."""
        self._save([
            _pos("QQQ270618P00735000",  1, "long",  15.0),  # far  2027-06-18
            _pos("QQQ260919P00705000", -1, "short",  5.0),  # near 2026-09-19
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertIn("Diagonal", p["type"])
        self.assertTrue(p["is_proper"])
        self.assertEqual(p["near_expiry"], "2026-09-19")
        self.assertEqual(p["far_expiry"], "2027-06-18")

    # ── Partial matching ──────────────────────────────────────────────────────

    def test_partial_match_produces_spread_and_naked(self):
        """qty=2 long + qty=1 short → 1 spread (qty 1) + 1 Naked Long (qty 1)."""
        self._save([
            _pos("PLTR270618C00150000",  2, "long",  12.0),
            _pos("PLTR270618C00190000", -1, "short",  3.0),
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 2)
        types_ = {p["type"] for p in result}
        self.assertIn("Bull Call Spread", types_)
        self.assertIn("Naked Long Call", types_)
        spread = next(p for p in result if "Spread" in p["type"])
        self.assertEqual(spread["spread_qty"], 1)

    # ── Multi-underlying independence ─────────────────────────────────────────

    def test_two_underlyings_paired_independently(self):
        """Positions in different underlyings do not interfere with each other's pairing."""
        self._save([
            _pos("ARM270618P00400000",  1, "long",  8.0),
            _pos("ARM270618P00350000", -1, "short", 4.0),
            _pos("META270618C00600000",  1, "long",  25.0),
            _pos("META270618C00800000", -1, "short", 10.0),
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 2)
        by_und = {p["underlying"]: p["type"] for p in result}
        self.assertEqual(by_und["ARM"], "Bear Put Spread")
        self.assertEqual(by_und["META"], "Bull Call Spread")

    def test_empty_positions_returns_empty_list(self):
        """No positions → empty portfolio list, no error."""
        result = self._build(ACCT)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
