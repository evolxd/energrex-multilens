"""Cross-validation tests for spread portfolio calculation logic.

Covers every calculation bug that was fixed in _build_spread_portfolios:
  1. Credit spread pnl_pct uses max_loss (risk capital), not max_profit (credit)
  2. Diagonal spread max_loss = net debit for proper diagonal (long far + short near)
  3. Diagonal spread max_loss = None for improper diagonal
  4. Naked short put max_loss = strike × 100 × qty (not None / "unlimited")
  5. Naked short call max_loss = None (truly unlimited)
  6. Credit spread breakeven formula (put and call variants)

All arithmetic is verified with hand-computed expected values documented inline.
"""

import ast
import pathlib
import sys
import tempfile
import types
import unittest

# ── Mock Streamlit ────────────────────────────────────────────────────────────
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

import account.db as account_db
from account.options_repository import save_options_positions

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCT = "test_calc_acct"

# OCC date codes:
#   far  2027-06-18 → 270618
#   near 2026-09-19 → 260919


def _pos(symbol, qty, direction, unit_cost, current_price=None, strike=None, expiry=None):
    from account.options import parse_occ
    parsed = parse_occ(symbol)
    return {
        "symbol": symbol,
        "quantity": qty,
        "direction": direction,
        "unit_cost": unit_cost,
        "current_price": current_price,
        "market_value": None,
        "day_pnl": None,
        "total_pnl": None,
        "strike": strike if strike is not None else parsed.get("strike"),
        "expiry": expiry or parsed.get("expiry"),
    }


class SpreadCalcTests(unittest.TestCase):

    _tmp = None
    _original_db_path = None
    _build = None

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._original_db_path = account_db.DB_PATH
        account_db.DB_PATH = pathlib.Path(cls._tmp.name) / "test_calc.db"

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

    def _save(self, rows):
        save_options_positions(ACCT, rows)

    # ── Credit spread pnl_pct denominator ────────────────────────────────────

    def test_bull_put_spread_pnl_pct_uses_max_loss_not_max_profit(self):
        """Bear Put credit: pnl_pct = pnl / max_loss, NOT pnl / max_profit (credit).

        Bull Put Spread: short PLTR 150P at $12, long PLTR 100P at $3.
          net credit  = 12 - 3 = $9/share  → is_debit=False
          max_profit  = 9×1×100 = $900     (credit received)
          max_loss    = (50 - 9)×1×100 = $4,100  (capital at risk)
          width       = 150 - 100 = 50

        Position is now: long at $3 still, short closed at $1.
          long_pnl  = (3 - 3) × 1 × 100 = $0     (unchanged — current=unit_cost)
          short_pnl = (1 - 12) × (-1) × 100 = +$1,100
          current_pnl = $1,100

        pnl_pct should be 1100 / 4100 ≈ 26.8%, NOT 1100/900 ≈ 122%.
        """
        self._save([
            _pos("PLTR270618P00150000", -1, "short", 12.0, current_price=1.0),
            _pos("PLTR270618P00100000",  1, "long",   3.0, current_price=3.0),
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertEqual(p["type"], "Bull Put Spread")
        self.assertFalse(p["is_debit"])
        self.assertAlmostEqual(p["max_profit"], 900.0, places=1)
        self.assertAlmostEqual(p["max_loss"],   4100.0, places=1)

        pnl = p["current_pnl"]
        self.assertAlmostEqual(pnl, 1100.0, places=1)

        # pnl_pct must use max_loss (4100) as denominator
        expected_pct = round(1100.0 / 4100.0 * 100, 1)
        self.assertAlmostEqual(p["pnl_pct"], expected_pct, places=1,
                               msg="pnl_pct must be pnl/max_loss, not pnl/max_profit")

    def test_bear_call_spread_pnl_pct_uses_max_loss(self):
        """Bear Call credit: pnl_pct denominator = max_loss.

        Bear Call: short QQQ 470C at $5, long QQQ 510C at $1.
          net credit  = 5 - 1 = $4/share → is_debit=False
          max_profit  = 4×1×100 = $400
          max_loss    = (40 - 4)×1×100 = $3,600
          width       = 510 - 470 = 40

        Position: short now at $2, long now at $0.5.
          short_pnl = (2 - 5)×(-1)×100 = +$300
          long_pnl  = (0.5 - 1)×1×100  = -$50
          current_pnl = $250

        pnl_pct = 250 / 3600 ≈ 6.9%.
        """
        self._save([
            _pos("QQQ270618C00470000", -1, "short", 5.0, current_price=2.0),
            _pos("QQQ270618C00510000",  1, "long",  1.0, current_price=0.5),
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertEqual(p["type"], "Bear Call Spread")
        self.assertAlmostEqual(p["max_loss"],   3600.0, places=1)
        self.assertAlmostEqual(p["max_profit"],  400.0, places=1)
        self.assertAlmostEqual(p["current_pnl"], 250.0, places=1)

        expected_pct = round(250.0 / 3600.0 * 100, 1)
        self.assertAlmostEqual(p["pnl_pct"], expected_pct, places=1)

    # ── Credit spread breakeven ───────────────────────────────────────────────

    def test_bull_put_spread_breakeven(self):
        """Bull Put Spread breakeven = short_strike - net_credit_per_share.

        Short PLTR 150P at $12, long PLTR 100P at $3.
          net_credit = 9, short_strike = 150
          breakeven  = 150 - 9 = 141
        """
        self._save([
            _pos("PLTR270618P00150000", -1, "short", 12.0),
            _pos("PLTR270618P00100000",  1, "long",   3.0),
        ])
        p = self._build(ACCT)[0]
        self.assertAlmostEqual(p["breakeven"], 141.0, places=4)

    def test_bear_call_spread_breakeven(self):
        """Bear Call Spread breakeven = short_strike + net_credit_per_share.

        Short QQQ 470C at $5, long QQQ 510C at $1.
          net_credit = 4, short_strike = 470
          breakeven  = 470 + 4 = 474
        """
        self._save([
            _pos("QQQ270618C00470000", -1, "short", 5.0),
            _pos("QQQ270618C00510000",  1, "long",  1.0),
        ])
        p = self._build(ACCT)[0]
        self.assertAlmostEqual(p["breakeven"], 474.0, places=4)

    # ── Diagonal spread max_loss ──────────────────────────────────────────────

    def test_proper_diagonal_max_loss_equals_net_debit(self):
        """Proper diagonal (long far + short near): max_loss = net debit paid.

        QQQ long far 270618P00440000 unit_cost=15, short near 260919P00420000 unit_cost=5.
          net_ps  = far_cost - near_cost = 15 - 5 = 10 (debit)
          max_loss = 10 × 1 × 100 = $1,000
        Worst case: both legs expire worthless → lose the net debit.
        """
        self._save([
            _pos("QQQ270618P00440000",  1, "long",  15.0),   # far
            _pos("QQQ260919P00420000", -1, "short",  5.0),   # near
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertIn("Diagonal", p["type"])
        self.assertTrue(p["is_proper"])
        self.assertAlmostEqual(p["max_loss"], 1000.0, places=1,
                               msg="max_loss must be net_debit × qty × 100")

    def test_improper_diagonal_max_loss_is_none(self):
        """Improper diagonal (long near + short far): max_loss = None.

        Risk is unbounded because the hedge leg expires first.
        """
        self._save([
            _pos("QQQ260919P00420000",  1, "long",  15.0),   # near — WRONG side
            _pos("QQQ270618P00440000", -1, "short",  5.0),   # far
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertFalse(p["is_proper"])
        self.assertIsNone(p["max_loss"],
                          msg="Improper diagonal has undefined max_loss")

    def test_diagonal_pnl_pct_set_for_proper_debit(self):
        """Proper debit diagonal pnl_pct = current_pnl / max_loss × 100."""
        self._save([
            _pos("QQQ270618P00440000",  1, "long",  15.0, current_price=12.0),
            _pos("QQQ260919P00420000", -1, "short",  5.0, current_price=2.0),
        ])
        p = self._build(ACCT)[0]
        # long_pnl  = (12-15)×1×100 = -$300
        # short_pnl = (2-5)×(-1)×100 = +$300
        # current_pnl = $0
        self.assertAlmostEqual(p["current_pnl"], 0.0, places=1)
        # pnl_pct = 0 / 1000 × 100 = 0.0
        self.assertAlmostEqual(p["pnl_pct"], 0.0, places=1)

    # ── Naked short put max_loss ──────────────────────────────────────────────

    def test_naked_short_put_max_loss_equals_strike_times_100(self):
        """Naked short put: max_loss = strike × 100 × qty (stock-to-zero scenario).

        NOK270618P00004000: strike = 4.00
          max_loss = 4.00 × 100 × 1 = $400
        """
        self._save([
            _pos("NOK270618P00004000", -1, "short", 0.30),
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertEqual(p["type"], "Naked Short Put")
        self.assertAlmostEqual(p["max_loss"], 400.0, places=1,
                               msg="max_loss = strike(4) × 100 × 1 = $400")

    def test_naked_short_put_max_loss_scales_with_qty(self):
        """2-contract naked short put: max_loss = strike × 100 × 2."""
        self._save([
            _pos("QQQ270618P00350000", -2, "short", 2.0),
        ])
        result = self._build(ACCT)
        p = result[0]
        self.assertAlmostEqual(p["max_loss"], 350 * 100 * 2, places=1)

    def test_naked_short_call_max_loss_is_none(self):
        """Naked short call: max_loss = None (unlimited upside risk)."""
        self._save([
            _pos("PLTR270618C00200000", -1, "short", 3.0),
        ])
        result = self._build(ACCT)

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertEqual(p["type"], "Naked Short Call")
        self.assertIsNone(p["max_loss"],
                          msg="Short call has unlimited risk — max_loss must be None")

    def test_naked_long_put_max_loss_still_uses_unit_cost(self):
        """Long put: max_loss = unit_cost × qty × 100 (premium paid, unchanged)."""
        self._save([
            _pos("QQQ270618P00350000", 1, "long", 4.50),
        ])
        p = self._build(ACCT)[0]
        self.assertAlmostEqual(p["max_loss"], 4.50 * 1 * 100, places=1)


if __name__ == "__main__":
    unittest.main()
