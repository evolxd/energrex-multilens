"""Tests for account/discipline.py -- 门⑤纪律 signal recording/scoring.

Covers the three cases the design doc (docs/DISCIPLINE_GATE_DESIGN.md §6)
calls out as the minimum: acted vs self_resolved, the UNIQUE constraint not
duplicating a same-day signal, and expired_unhandled for the 到期处理
dimension. Plus a couple more (overdue window, score aggregation) since
those are the parts most likely to have an off-by-one.
"""
import datetime
import pathlib
import tempfile
import unittest

import account.db as account_db
from account import discipline as disc

ACCT = "test_disc_acct"


class DisciplineTests(unittest.TestCase):

    _tmp = None
    _original_db_path = None

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._original_db_path = account_db.DB_PATH
        account_db.DB_PATH = pathlib.Path(cls._tmp.name) / "test_discipline.db"
        account_db.init_db()

    @classmethod
    def tearDownClass(cls):
        account_db.DB_PATH = cls._original_db_path
        if cls._tmp:
            cls._tmp.cleanup()

    def setUp(self):
        conn = account_db.db()
        conn.execute("DELETE FROM discipline_signals WHERE account_id=?", (ACCT,))
        conn.execute("DELETE FROM option_realized_trades WHERE account_id=?", (ACCT,))
        conn.execute("DELETE FROM options_positions WHERE account_id=?", (ACCT,))
        conn.commit()
        conn.close()

    # ── helpers ──────────────────────────────────────────────────────

    def _seed_open_signal(self, dimension, symbol, first_seen_date, detail="test"):
        conn = account_db.db()
        conn.execute(
            "INSERT INTO discipline_signals "
            "(account_id, dimension, symbol, first_seen_date, last_seen_date, "
            " detail, status) VALUES (?,?,?,?,?,?,'open')",
            (ACCT, dimension, symbol, first_seen_date, first_seen_date, detail),
        )
        conn.commit()
        conn.close()

    def _seed_realized_trade(self, symbol, close_date):
        conn = account_db.db()
        conn.execute(
            "INSERT INTO option_realized_trades "
            "(account_id, underlying, symbol, close_date) VALUES (?,?,?,?)",
            (ACCT, symbol[:4], symbol, close_date),
        )
        conn.commit()
        conn.close()

    def _seed_option_position(self, symbol, expiry):
        conn = account_db.db()
        conn.execute(
            "INSERT INTO options_positions (account_id, symbol, expiry) VALUES (?,?,?)",
            (ACCT, symbol, expiry),
        )
        conn.commit()
        conn.close()

    def _status_of(self, symbol, dimension=None):
        conn = account_db.db()
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT * FROM discipline_signals WHERE account_id=? AND symbol=?"
            + (" AND dimension=?" if dimension else ""),
            (ACCT, symbol) + ((dimension,) if dimension else ()),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    # ── new signal insertion ────────────────────────────────────────

    def test_new_signal_recorded_as_open(self):
        signals = [{"symbol": "PLTR260101P00050000", "dimension": "止损纪律",
                    "detail": "多期权亏损 55%，建议止损"}]
        summ = disc.record_and_resolve_signals(ACCT, signals, None,
                                                today=datetime.date(2026, 1, 1))
        self.assertEqual(summ["new"], 1)
        row = self._status_of("PLTR260101P00050000")
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["first_seen_date"], "2026-01-01")

    # ── acted vs self_resolved ──────────────────────────────────────

    def test_acted_when_matching_realized_trade_exists_after_first_seen(self):
        self._seed_open_signal("止损纪律", "AVGO260201P00300000", "2026-01-05")
        self._seed_realized_trade("AVGO260201P00300000", "2026-01-08")
        # signal no longer firing this sync (position closed) -> current=[]
        summ = disc.record_and_resolve_signals(ACCT, [], None,
                                                today=datetime.date(2026, 1, 10))
        self.assertEqual(summ["acted"], 1)
        self.assertEqual(summ["self_resolved"], 0)
        row = self._status_of("AVGO260201P00300000")
        self.assertEqual(row["status"], "acted")
        self.assertEqual(row["response_days"], 3)  # 01-08 - 01-05

    def test_self_resolved_when_no_matching_trade_found(self):
        self._seed_open_signal("止损纪律", "NVDA260201P00400000", "2026-01-05")
        # no realized_trades row at all -- market recovered on its own
        summ = disc.record_and_resolve_signals(ACCT, [], None,
                                                today=datetime.date(2026, 1, 10))
        self.assertEqual(summ["acted"], 0)
        self.assertEqual(summ["self_resolved"], 1)
        row = self._status_of("NVDA260201P00400000")
        self.assertEqual(row["status"], "self_resolved")
        self.assertIsNone(row["response_days"])

    def test_a_close_trade_before_first_seen_date_does_not_count_as_acted(self):
        """A realized trade that predates the signal isn't a response to it."""
        self._seed_realized_trade("QQQ260201P00500000", "2026-01-02")
        self._seed_open_signal("止损纪律", "QQQ260201P00500000", "2026-01-05")
        summ = disc.record_and_resolve_signals(ACCT, [], None,
                                                today=datetime.date(2026, 1, 10))
        self.assertEqual(summ["acted"], 0)
        self.assertEqual(summ["self_resolved"], 1)

    # ── expired_unhandled ────────────────────────────────────────────

    def test_expiry_dte_reaching_zero_marks_expired_unhandled(self):
        self._seed_open_signal("到期处理", "SPCX260110C00020000", "2026-01-05")
        self._seed_option_position("SPCX260110C00020000", "2026-01-10")
        # still firing today (DTE=0), but expiry has arrived
        current = [{"symbol": "SPCX260110C00020000", "dimension": "到期处理",
                    "detail": "距到期 0 天"}]
        summ = disc.record_and_resolve_signals(ACCT, current, None,
                                                today=datetime.date(2026, 1, 10))
        self.assertEqual(summ["expired_unhandled"], 1)
        row = self._status_of("SPCX260110C00020000", "到期处理")
        self.assertEqual(row["status"], "expired_unhandled")

    def test_expiry_not_yet_reached_stays_open(self):
        self._seed_open_signal("到期处理", "TSM260115C00200000", "2026-01-10")
        self._seed_option_position("TSM260115C00200000", "2026-01-15")
        current = [{"symbol": "TSM260115C00200000", "dimension": "到期处理",
                    "detail": "距到期 5 天"}]
        summ = disc.record_and_resolve_signals(ACCT, current, None,
                                                today=datetime.date(2026, 1, 10))
        self.assertEqual(summ["expired_unhandled"], 0)
        row = self._status_of("TSM260115C00200000", "到期处理")
        self.assertEqual(row["status"], "open")

    # ── UNIQUE constraint / no duplicate same-day insert ─────────────

    def test_same_signal_same_day_does_not_duplicate(self):
        signals = [{"symbol": "META260101C00600000", "dimension": "止盈纪律",
                    "detail": "空期权盈利 60%"}]
        summ1 = disc.record_and_resolve_signals(ACCT, signals, None,
                                                 today=datetime.date(2026, 1, 1))
        summ2 = disc.record_and_resolve_signals(ACCT, signals, None,
                                                 today=datetime.date(2026, 1, 1))
        self.assertEqual(summ1["new"], 1)
        self.assertEqual(summ2["new"], 0)  # already open, just updates last_seen_date
        conn = account_db.db()
        n = conn.execute(
            "SELECT COUNT(*) FROM discipline_signals WHERE account_id=? AND symbol=?",
            (ACCT, "META260101C00600000"),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)

    # ── overdue window ────────────────────────────────────────────────

    def test_get_overdue_signals_only_flags_past_the_window(self):
        # 止损纪律 window = 2 days
        self._seed_open_signal("止损纪律", "AMD260101P00100000", "2026-01-01")  # old
        self._seed_open_signal("止损纪律", "INTC260109P00030000", "2026-01-09")  # fresh
        overdue = disc.get_overdue_signals(ACCT, today=datetime.date(2026, 1, 10))
        symbols = {o["symbol"] for o in overdue}
        self.assertIn("AMD260101P00100000", symbols)
        self.assertNotIn("INTC260109P00030000", symbols)

    def test_get_overdue_signals_skips_到期处理_dimension(self):
        """到期处理 has no fixed-day window -- it's either open (not yet due)
        or already expired_unhandled (no longer open), never 'overdue-open'."""
        self._seed_open_signal("到期处理", "GOOG260201C00150000", "2026-01-01")
        overdue = disc.get_overdue_signals(ACCT, today=datetime.date(2026, 1, 20))
        self.assertEqual([o for o in overdue if o["dimension"] == "到期处理"], [])

    # ── score aggregation ────────────────────────────────────────────

    def test_compute_discipline_scores_response_rate_and_sample_size(self):
        # 2 acted, 1 self_resolved for 止损纪律 within the window
        for i, (status, resp_days) in enumerate([("acted", 2), ("acted", 4), ("self_resolved", None)]):
            conn = account_db.db()
            conn.execute(
                "INSERT INTO discipline_signals "
                "(account_id, dimension, symbol, first_seen_date, last_seen_date, "
                " status, resolved_date, response_days) VALUES (?,?,?,?,?,?,?,?)",
                (ACCT, "止损纪律", f"SYM{i}", "2026-01-05", "2026-01-05",
                 status, "2026-01-08", resp_days),
            )
            conn.commit()
            conn.close()

        scores = disc.compute_discipline_scores(
            ACCT, since=datetime.date(2026, 1, 1), until=datetime.date(2026, 1, 31))
        s = scores["止损纪律"]
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["response_rate"], 2 / 3)
        self.assertAlmostEqual(s["avg_response_days"], 3.0)  # (2+4)/2
        self.assertEqual(s["since"], "2026-01-01")
        self.assertEqual(s["until"], "2026-01-31")


class ScanAndHedgeSignalTests(unittest.TestCase):
    """Pure-function tests, no DB needed for hedge_governance_signals; a
    minimal DB round-trip for scan_pnl_dte_signals since it reads
    options_positions directly."""

    _tmp = None
    _original_db_path = None

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._original_db_path = account_db.DB_PATH
        account_db.DB_PATH = pathlib.Path(cls._tmp.name) / "test_disc_scan.db"
        account_db.init_db()

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

    def test_scan_pnl_dte_signals_thresholds_and_no_dedup(self):
        conn = account_db.db()
        # short put, +55% pnl on a $2.00 credit (basis=$200) -> 止盈纪律
        conn.execute(
            "INSERT INTO options_positions "
            "(account_id, symbol, quantity, unit_cost, total_pnl, expiry) "
            "VALUES (?,?,?,?,?,?)",
            (ACCT, "XYZ260101P00050000", -1, 2.00, 110.0, "2026-06-01"),
        )
        # same symbol also inside the DTE<=7 window -> should ALSO get a
        # separate 到期处理 signal for the same symbol (no folding/dedup,
        # unlike _cascade.py::_scan_exit_signals).
        conn.execute(
            "INSERT INTO options_positions "
            "(account_id, symbol, quantity, unit_cost, total_pnl, expiry) "
            "VALUES (?,?,?,?,?,?)",
            (ACCT, "ABC260107C00010000", -1, 1.00, 0.0, "2026-01-07"),
        )
        conn.commit()
        conn.close()

        sigs = disc.scan_pnl_dte_signals(ACCT, today=datetime.date(2026, 1, 1))
        by_symbol = {}
        for s in sigs:
            by_symbol.setdefault(s["symbol"], []).append(s["dimension"])
        self.assertIn("止盈纪律", by_symbol.get("XYZ260101P00050000", []))
        self.assertIn("到期处理", by_symbol.get("ABC260107C00010000", []))

    def test_hedge_governance_signals_no_hedge_needed_is_empty(self):
        self.assertEqual(disc.hedge_governance_signals({"status": "NO_HEDGE_NEEDED"}), [])
        self.assertEqual(disc.hedge_governance_signals(None), [])

    def test_hedge_governance_signals_violation_row_uses_its_symbol(self):
        gov = {
            "status": "VIOLATION",
            "summary": "bad",
            "rows": [
                {"symbol": "QQQ260601P00400000", "status": "VIOLATION",
                 "issues": [{"code": "NAKED_LONG_PUT_TOO_LONG"}]},
                {"symbol": "QQQ260601P00390000", "status": "VALID_HEDGE", "issues": []},
            ],
        }
        out = disc.hedge_governance_signals(gov)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "QQQ260601P00400000")
        self.assertEqual(out[0]["dimension"], "对冲纪律")

    def test_hedge_governance_signals_missing_hedge_uses_synthetic_symbol(self):
        gov = {"status": "MISSING_HEDGE", "summary": "trigger active, no hedge", "rows": []}
        out = disc.hedge_governance_signals(gov)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "QQQ_HEDGE_MISSING")


class V2ScoringTests(unittest.TestCase):
    """docs/DISCIPLINE_AND_REVIEW_ARCHITECTURE.md §2 -- 百分制纪律分、事件分
    时间衰减、门④违规上限 0.75、review_tag 剔除、超期 open 算临时 0。"""

    _tmp = None
    _original_db_path = None

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._original_db_path = account_db.DB_PATH
        account_db.DB_PATH = pathlib.Path(cls._tmp.name) / "test_disc_v2.db"
        account_db.init_db()

    @classmethod
    def tearDownClass(cls):
        account_db.DB_PATH = cls._original_db_path
        if cls._tmp:
            cls._tmp.cleanup()

    def setUp(self):
        conn = account_db.db()
        conn.execute("DELETE FROM discipline_signals WHERE account_id=?", (ACCT,))
        conn.commit()
        conn.close()

    def _seed_resolved(self, dimension, symbol, first_seen, status,
                       response_days=None, event_score=None, review_tag=None):
        conn = account_db.db()
        conn.execute(
            "INSERT INTO discipline_signals "
            "(account_id, dimension, symbol, first_seen_date, last_seen_date, "
            " status, resolved_date, response_days, event_score, review_tag) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ACCT, dimension, symbol, first_seen, first_seen, status,
             first_seen, response_days, event_score, review_tag),
        )
        conn.commit()
        conn.close()

    # ── event_score decay ──────────────────────────────────────────

    def test_event_score_in_window_is_full_cap(self):
        # 止损纪律 window=2; responded day 1 -> 1.0
        self.assertAlmostEqual(disc.event_score("止损纪律", "acted", 1), 1.0)

    def test_event_score_late_decays_linearly_015_per_day(self):
        # window 2, responded day 5 -> 3 days late -> 1.0 - 0.15*3 = 0.55
        self.assertAlmostEqual(disc.event_score("止损纪律", "acted", 5), 0.55)

    def test_event_score_late_past_seven_days_is_zero(self):
        self.assertEqual(disc.event_score("止损纪律", "acted", 12), 0.0)

    def test_men4_violation_cap_is_075(self):
        # 无case交易 window=5; responded within -> 0.75 not 1.0
        self.assertAlmostEqual(disc.event_score("无case交易", "acted", 3), 0.75)
        # men4 late: 0.75 - 0.15*days_late
        self.assertAlmostEqual(disc.event_score("无case交易", "acted", 7), 0.75 - 0.15 * 2)

    def test_event_score_non_acted_is_zero(self):
        for st in ("self_resolved", "expired_unhandled", "open"):
            self.assertEqual(disc.event_score("单票超限", st, None), 0.0)

    # ── compute_discipline_score aggregation ───────────────────────

    def test_score_is_weighted_and_percent(self):
        # 止损纪律 (weight 3): one acted-in-window event_score 1.0
        # 单票超限 (weight 2): one self_resolved event_score 0.0
        self._seed_resolved("止损纪律", "AAA", "2026-01-05", "acted",
                            response_days=1, event_score=1.0)
        self._seed_resolved("单票超限", "BBB", "2026-01-06", "self_resolved",
                            event_score=0.0)
        out = disc.compute_discipline_score(
            ACCT, since=datetime.date(2026, 1, 1), until=datetime.date(2026, 1, 31))
        # per-dim avg: 止损=1.0, 单票=0.0
        # weighted = (1.0*3 + 0.0*2) / (3+2) * 100 = 60.0
        self.assertAlmostEqual(out["score"], 60.0)
        self.assertEqual(out["grade"], "严重失守")
        self.assertEqual(out["n_events"], 2)

    def test_review_tag_deliberate_exception_excludes_the_row(self):
        self._seed_resolved("止损纪律", "AAA", "2026-01-05", "self_resolved",
                            event_score=0.0, review_tag="有意例外")
        self._seed_resolved("止损纪律", "CCC", "2026-01-06", "acted",
                            response_days=1, event_score=1.0)
        out = disc.compute_discipline_score(
            ACCT, since=datetime.date(2026, 1, 1), until=datetime.date(2026, 1, 31))
        # only the 1.0 event counts -> 100%
        self.assertAlmostEqual(out["score"], 100.0)
        self.assertEqual(out["n_events"], 1)

    def test_open_past_window_counts_as_provisional_zero(self):
        # 单票超限 window=2; open since 10 days ago -> provisional 0 drags score
        conn = account_db.db()
        conn.execute(
            "INSERT INTO discipline_signals "
            "(account_id, dimension, symbol, first_seen_date, last_seen_date, status) "
            "VALUES (?,?,?,?,?,'open')",
            (ACCT, "单票超限", "DDD", "2026-01-01", "2026-01-01"),
        )
        conn.commit()
        conn.close()
        out = disc.compute_discipline_score(
            ACCT, since=datetime.date(2026, 1, 1), until=datetime.date(2026, 1, 20))
        self.assertEqual(out["score"], 0.0)
        self.assertEqual(out["n_events"], 1)  # the provisional-0 open signal

    def test_open_within_window_not_yet_counted(self):
        conn = account_db.db()
        conn.execute(
            "INSERT INTO discipline_signals "
            "(account_id, dimension, symbol, first_seen_date, last_seen_date, status) "
            "VALUES (?,?,?,?,?,'open')",
            (ACCT, "单票超限", "EEE", "2026-01-19", "2026-01-19"),
        )
        conn.commit()
        conn.close()
        out = disc.compute_discipline_score(
            ACCT, since=datetime.date(2026, 1, 1), until=datetime.date(2026, 1, 20))
        # 1 day open < window 2 -> not counted, no scored events at all
        self.assertIsNone(out["score"])
        self.assertEqual(out["n_events"], 0)

    def test_zhiying_dimension_is_recorded_but_not_scored(self):
        self.assertIn("止盈纪律", disc.DIMENSIONS)
        self.assertNotIn("止盈纪律", disc.SCORED_DIMENSIONS)
        self._seed_resolved("止盈纪律", "AAA", "2026-01-05", "self_resolved",
                            event_score=0.0)
        out = disc.compute_discipline_score(
            ACCT, since=datetime.date(2026, 1, 1), until=datetime.date(2026, 1, 31))
        # 止盈 not scored -> no events
        self.assertEqual(out["n_events"], 0)


if __name__ == "__main__":
    unittest.main()
