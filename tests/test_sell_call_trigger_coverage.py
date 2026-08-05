"""The sell-call alert must not fire on a long call that is already covered.

A long call held against short calls at higher strikes is a bull call spread,
not a naked long. The alert read `quantity > 0` straight from SQL, could not
see the short legs, and told the holder to sell a call they had already sold.

_build_spread_portfolios had done correct multi-leg matching all along, and
was even fixed for exactly this in b4fa836. The alert simply never consulted
it. These tests pin both directions: silence when covered, and still firing
when genuinely naked, because a fix that only silenced the false alert would
be indistinguishable from switching the alert off.
"""

import ast
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import account.db as account_db  # noqa: E402


def _load_module(tmp_db: pathlib.Path):
    account_db.DB_PATH = tmp_db

    stub = types.ModuleType("streamlit")

    def _noop(*args, **kwargs):
        return _noop

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __call__(self, *a, **k):
            return self

        def __getattr__(self, name):
            return _noop

    stub.__getattr__ = lambda name: _noop
    for attr in ("cache_data", "cache_resource"):
        setattr(stub, attr, lambda *a, **k: (lambda fn: fn))
    stub.session_state = {}
    stub.sidebar = _Ctx()
    sys.modules["streamlit"] = stub

    src = (ROOT / "account_monitor.py").read_text(encoding="utf-8-sig")
    ui_line = next(
        (i + 1 for i, line in enumerate(src.splitlines()) if "st.set_page_config" in line),
        99999,
    )
    tree = ast.parse(src, filename="account_monitor.py")
    filtered = ast.Module(
        body=[n for n in tree.body if getattr(n, "lineno", 0) < ui_line],
        type_ignores=[],
    )
    ast.fix_missing_locations(filtered)
    module = types.ModuleType("account_monitor_under_test")
    module.__file__ = str(ROOT / "account_monitor.py")
    exec(compile(filtered, "account_monitor.py", "exec"), module.__dict__)
    return module


# Synthetic fixtures: round numbers, a ticker that is not in the tracked
# universe, and a profit percentage comfortably over the 50% trigger.
# (symbol, qty, strike, unit_cost, current_price, total_pnl, delta)
LONG_100 = ("TESTCO270115C00100000", 2, 100.0, 10.00, 20.00, 2000.0, 0.57)
SHORT_150 = ("TESTCO270115C00150000", -1, 150.0, 5.00, 8.00, -300.0, 0.32)
SHORT_160 = ("TESTCO270115C00160000", -1, 160.0, 4.00, 6.00, -200.0, 0.30)


class SellCallTriggerCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._original = account_db.DB_PATH
        cls.mod = _load_module(pathlib.Path(cls._tmp.name) / "coverage.db")

    @classmethod
    def tearDownClass(cls):
        account_db.DB_PATH = cls._original
        try:
            cls._tmp.cleanup()
        except (PermissionError, OSError):
            pass  # Windows keeps the sqlite handle briefly; the temp dir is disposable.

    def seed(self, rows):
        conn = account_db.db()
        conn.execute("DELETE FROM options_positions")
        for sym, qty, strike, cost, price, pnl, delta in rows:
            conn.execute(
                "INSERT INTO options_positions (account_id, symbol, direction,"
                " strike, expiry, quantity, unit_cost, current_price,"
                " market_value, total_pnl, delta, iv)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("T", sym, "long" if qty > 0 else "short", strike, "2027-01-15",
                 qty, cost, price, qty * price * 100, pnl, delta, 40.0),
            )
        conn.commit()
        conn.close()

    def test_fully_covered_long_call_raises_no_alert(self):
        """Two longs against two shorts is two spreads, not a naked leg."""
        self.seed([LONG_100, SHORT_150, SHORT_160])
        self.assertEqual(self.mod._uncovered_long_call_symbols("T"), set())
        self.assertEqual(self.mod._check_sell_call_triggers("T"), [])

    def test_genuinely_naked_long_call_still_alerts(self):
        """Without this the fix would be indistinguishable from disabling the
        alert outright."""
        self.seed([LONG_100])
        self.assertEqual(
            self.mod._uncovered_long_call_symbols("T"), {"TESTCO270115C00100000"}
        )
        triggers = self.mod._check_sell_call_triggers("T")
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["sym"], "TESTCO270115C00100000")

    def test_partially_covered_long_call_still_alerts(self):
        """Two longs against one short leaves one contract uncovered."""
        self.seed([
            ("OTHRCO270115C00200000", 2, 200.0, 20.00, 40.00, 4000.0, 0.56),
            ("OTHRCO270115C00260000", -1, 260.0, 4.00, 6.00, -200.0, 0.32),
        ])
        self.assertEqual(
            self.mod._uncovered_long_call_symbols("T"), {"OTHRCO270115C00200000"}
        )
        self.assertEqual(len(self.mod._check_sell_call_triggers("T")), 1)

    def test_short_calls_are_never_alert_candidates(self):
        self.seed([SHORT_150])
        self.assertEqual(self.mod._check_sell_call_triggers("T"), [])

    def test_puts_are_never_alert_candidates(self):
        self.seed([("TESTCO261030P00080000", 1, 80.0, 10.00, 14.00, 400.0, -0.39)])
        self.assertEqual(self.mod._check_sell_call_triggers("T"), [])

    def test_empty_account_is_handled(self):
        self.seed([])
        self.assertEqual(self.mod._uncovered_long_call_symbols("T"), set())
        self.assertEqual(self.mod._check_sell_call_triggers("T"), [])


if __name__ == "__main__":
    unittest.main()
