"""Characterization tests for _cascade._gather_v2_risk_signals (pre-refactor).

These lock the CURRENT, unmodified behaviour from the outside, before the
function is split into pure helpers plus a `_guarded` wrapper. Nothing here
asserts what the code "should" do; it asserts what it does today, including
quirks (marked QUIRK) that the refactor must preserve.

Determinism: wall clock frozen (freezegun), DB replaced by a throwaway sqlite
file, data files redirected to tmp_path, every account.risk_signals function
and every network/heavy collaborator replaced by a recorder at its module
boundary. Nothing touches the network or the real data directory.
"""
import datetime
import logging
import sys
import types
from pathlib import Path

import pandas as pd
import pytest
from freezegun import freeze_time

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import _cascade  # noqa: E402
import account.db as account_db  # noqa: E402
from account import risk_signals as rs  # noqa: E402

FROZEN = "2026-09-23 12:00:00"
LIMITS = {"max_beta_delta_ratio": 3.1, "max_leverage": 4.1, "stress_20_hard_stop": 0.27,
          "stress_hard_stop": 0.11}


class Rec:
    """Holds call records and knobs for the fakes."""

    def __init__(self):
        self.calls = {}
        self.traded_return = [{"tag": "C", "symbol": "ZZZ"}]

    def log(self, name, *a, **kw):
        self.calls.setdefault(name, []).append((a, kw))

    def n(self, name):
        return len(self.calls.get(name, []))

    def last(self, name):
        return self.calls[name][-1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "energrex.db"
    db_path.parent.mkdir()
    monkeypatch.setattr(account_db, "DB_PATH", db_path)
    account_db.init_db()
    monkeypatch.setattr(_cascade, "_DB", db_path)
    monkeypatch.setattr(_cascade, "_ROOT", tmp_path)
    monkeypatch.setattr(_cascade, "_get_am", lambda: {"_RISK_LIMITS": LIMITS})
    monkeypatch.setattr(sys, "path", list(sys.path))  # function prepends _ROOT

    rec = Rec()

    def _a(snap, **kw):
        rec.log("A", snap, **kw)
        return [{"tag": "A"}]

    def _hard(b):
        rec.log("hard", b)
        return [{"tag": "B"}]

    def _traded(new_trades, **kw):
        rec.log("traded", new_trades, **kw)
        return [dict(s) for s in rec.traded_return]

    def _pb(bars):
        rec.log("pb", bars)
        return {"pb": 1}

    def _compound(pnl, pb):
        rec.log("compound", pnl, pb)
        return [{"tag": "D"}]

    monkeypatch.setattr(rs, "risk_snapshot_signals", _a)
    monkeypatch.setattr(rs, "hard_constraint_signals", _hard)
    monkeypatch.setattr(rs, "traded_signals", _traded)
    monkeypatch.setattr(rs, "pullback_signals", _pb)
    monkeypatch.setattr(rs, "compound_zhiying_pullback_signals", _compound)

    import scoring.position_exposure as pe
    import scoring.position_limits as pl
    import scoring.mispricing_store as ms
    import account.discipline as disc
    import account.performance as perf

    def _expo(pos, equity, cash, chain_of, opts):
        rec.log("expo", pos, equity, cash, chain_of, opts)
        return "EXPO"

    def _breaches(expo, limits):
        rec.log("breaches", expo, limits)
        return [{"b": 1}]

    def _read_chain(path):
        rec.log("read_chain", path)
        return "RECS"

    monkeypatch.setattr(pe, "compute_exposures", _expo)
    monkeypatch.setattr(pe, "breaches", _breaches)
    monkeypatch.setattr(ms, "read_chain", _read_chain)
    monkeypatch.setattr(pl, "effective_limit", lambda recs, key, now: ("lim", key, now))
    monkeypatch.setattr(disc, "scan_pnl_dte_signals",
                        lambda acct: (rec.log("scan", acct), [{"pnl": 1}])[1])
    monkeypatch.setattr(perf, "compute_performance_stats", lambda acct: {"by_combo": {}})

    rec.tmp = tmp_path
    rec.db_path = db_path
    rec.mp = monkeypatch
    _install_yfinance(monkeypatch, rec, {})
    return rec


def _frame():
    idx = pd.date_range("2026-01-01", periods=3)
    return pd.DataFrame({"Open": [1.0, 2, 3], "High": [2.0, 3, 4], "Low": [0.5, 1, 2],
                         "Close": [1.5, 2.5, 3.5], "Volume": [10, 20, 30],
                         "Dividends": [0, 0, 0]}, index=idx)


def _install_yfinance(monkeypatch, rec, behaviours):
    """behaviours: ticker -> DataFrame | Exception | 'empty'."""
    class _T:
        def __init__(self, sym):
            self.sym = sym
            rec.log("yf_ticker", sym)

        def history(self, period):
            b = behaviours.get(self.sym, _frame())
            if isinstance(b, Exception):
                raise b
            if isinstance(b, str) and b == "empty":
                return pd.DataFrame()
            return b

    mod = types.ModuleType("yfinance")
    mod.Ticker = _T
    monkeypatch.setitem(sys.modules, "yfinance", mod)


def run(snap=None):
    with freeze_time(FROZEN):
        return _cascade._gather_v2_risk_signals(snap)


def tags(out):
    return [s.get("tag") for s in out]


def sql(env, stmt, params=()):
    import sqlite3
    c = sqlite3.connect(str(env.db_path))
    c.execute(stmt, params)
    c.commit()
    c.close()


def add_pos(env, symbol, mv, sync="2026-09-22T10:00:00", acct="account_1"):
    sql(env, "INSERT INTO positions (account_id, sync_time, symbol, market_value) "
             "VALUES (?,?,?,?)", (acct, sync, symbol, mv))


def add_opt(env, symbol, mv=0.0, acct="account_1"):
    sql(env, "INSERT INTO options_positions (account_id, symbol, market_value) "
             "VALUES (?,?,?)", (acct, symbol, mv))


def add_bal(env, sync, equity, cash, acct="account_1"):
    sql(env, "INSERT INTO account_balance (account_id, sync_time, total_equity, cash_balance) "
             "VALUES (?,?,?,?)", (acct, sync, equity, cash))


def add_txn(env, date, ttype, symbol, desc, qty=1, amount=-1.0, acct="account_1"):
    sql(env, "INSERT INTO transactions (account_id, trade_date, type, symbol, description, "
             "quantity, amount) VALUES (?,?,?,?,?,?,?)",
        (acct, date, ttype, symbol, desc, qty, amount))


def add_disc(env, dim, first, resolved, status, symbol="S", acct="account_1"):
    sql(env, "INSERT INTO discipline_signals (account_id, dimension, symbol, first_seen_date, "
             "last_seen_date, status, resolved_date) VALUES (?,?,?,?,?,?,?)",
        (acct, dim, symbol, first, first, status, resolved))


# ── Block A: risk snapshot signals ──────────────────────────────────────────

def test_block_a_passes_snapshot_and_limits_from_am_risk_limits(env):
    snap = {"equity": 1}
    run(snap)
    (a, kw) = env.last("A")
    assert a == (snap,)
    assert kw == {"max_bd": 3.1, "max_leverage": 4.1, "stress_redline": 0.27}


def test_block_a_none_snapshot_is_passed_through(env):
    run(None)
    assert env.last("A")[0] == (None,)


def test_output_order_is_a_b_c_d(env):
    add_pos(env, "NVDA", 1.0)
    assert tags(run()) == ["A", "B", "C", "D"]


# ── Block isolation: one failing block never removes the others ─────────────

def _boom(*a, **kw):
    raise RuntimeError("boom")


ISOLATION = {
    "A": ("risk_snapshot_signals: boom", "rs.risk_snapshot_signals"),
    "B": ("hard_constraint_signals: boom", "pe.compute_exposures"),
    "C": ("traded_signals: boom", "rs.traded_signals"),
    "D": ("pullback: boom", "rs.compound_zhiying_pullback_signals"),
}


@pytest.mark.parametrize("failing", sorted(ISOLATION))
def test_single_block_failure_is_isolated_and_logged(env, caplog, failing):
    import scoring.position_exposure as pe
    add_pos(env, "NVDA", 1.0)
    message, target = ISOLATION[failing]
    obj, attr = target.split(".")
    env.mp.setattr({"rs": rs, "pe": pe}[obj], attr, _boom)
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        out = run()
    assert sorted(tags(out)) == sorted(set("ABCD") - {failing})
    recs = [r for r in caplog.records if r.name == "energrex.cascade"]
    assert [(r.levelno, r.getMessage()) for r in recs] == [(logging.WARNING, message)]


def test_all_blocks_failing_returns_empty_list_without_raising(env, caplog):
    import scoring.position_exposure as pe
    add_pos(env, "NVDA", 1.0)
    for o, a in ((rs, "risk_snapshot_signals"), (pe, "compute_exposures"),
                 (rs, "traded_signals"), (rs, "compound_zhiying_pullback_signals")):
        env.mp.setattr(o, a, _boom)
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        assert run() == []
    assert len(caplog.records) == 4


def test_block_a_failure_when_am_lookup_fails(env, caplog):
    env.mp.setattr(_cascade, "_get_am", lambda: (_ for _ in ()).throw(KeyError("_RISK_LIMITS")))
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        out = run()
    assert "A" not in tags(out) and "B" in tags(out)
    assert caplog.messages[0].startswith("risk_snapshot_signals: ")


def test_block_c_db_failure_is_isolated(env, caplog):
    sql(env, "DROP TABLE transactions")
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        out = run()
    assert tags(out) == ["A", "B"]
    assert caplog.messages[0].startswith("traded_signals: ")


# ── Block B: hard constraints ───────────────────────────────────────────────

def test_block_b_reads_latest_position_per_symbol_across_all_accounts(env):
    add_pos(env, "NVDA", 100.0, sync="2026-09-21T10:00:00")
    add_pos(env, "NVDA", 200.0, sync="2026-09-22T10:00:00")
    add_pos(env, "AMD", 50.0, acct="account_2")  # QUIRK: no account filter
    run()
    pos = env.last("expo")[0][0]
    assert sorted(pos, key=lambda r: r["symbol"]) == [
        {"symbol": "AMD", "market_value": 50.0}, {"symbol": "NVDA", "market_value": 200.0}]


def test_block_b_reads_all_option_rows_and_hands_chain_of(env):
    from scoring.exposure_context import chain_of
    add_opt(env, "NVDA270618C00100000", 10.0)
    add_opt(env, "AMD270618C00150000", 20.0, acct="account_2")  # QUIRK: no account filter
    run()
    a = env.last("expo")[0]
    assert sorted(a[4], key=lambda r: r["symbol"]) == [
        {"symbol": "AMD270618C00150000", "market_value": 20.0},
        {"symbol": "NVDA270618C00100000", "market_value": 10.0}]
    assert a[3] is chain_of


def test_block_b_equity_and_cash_come_from_latest_balance_any_account(env):
    add_bal(env, "2026-09-22T10:00:00", 100000, 5000)
    add_bal(env, "2026-09-23T10:00:00", 120000, 0.0, acct="account_2")
    run()
    a = env.last("expo")[0]
    assert (a[1], a[2]) == (120000.0, 0.0)


@pytest.mark.parametrize("equity,cash,expected", [
    (0, 300.0, (None, 300.0)),        # zero equity is treated as missing
    (1000, None, (1000.0, None)),     # NULL cash stays None
])
def test_block_b_equity_cash_edge_values(env, equity, cash, expected):
    add_bal(env, "2026-09-22T10:00:00", equity, cash)
    run()
    a = env.last("expo")[0]
    assert (a[1], a[2]) == expected


def test_block_b_no_balance_row_gives_none_none(env):
    run()
    a = env.last("expo")[0]
    assert (a[1], a[2]) == (None, None)


def test_block_b_none_exposures_skips_limits_and_breaches(env):
    import scoring.position_exposure as pe
    env.mp.setattr(pe, "compute_exposures", lambda *a: None)
    out = run()
    assert env.n("read_chain") == env.n("breaches") == env.n("hard") == 0
    assert "B" not in tags(out)


def test_block_b_limits_built_from_all_limit_specs_with_frozen_now(env):
    from scoring.position_limits import LIMIT_SPECS
    run()
    assert env.last("read_chain")[0] == (env.tmp / "data" / "position_limits.jsonl",)
    expo, limits = env.last("breaches")[0]
    assert expo == "EXPO"
    now = datetime.datetime(2026, 9, 23, 12, 0, 0)
    assert limits == {s.key: ("lim", s.key, now) for s in LIMIT_SPECS}
    assert env.last("hard")[0] == ([{"b": 1}],)


# ── Block C: traded signals ─────────────────────────────────────────────────

def test_block_c_trade_query_window_type_account_and_closing_flag(env):
    add_txn(env, "2026-09-13", "BUY", "NVDA", "OPEN CONTRACT", 2, -1)        # cutoff day: kept
    add_txn(env, "2026-09-12", "BUY", "OLD", "x", 1, -2)                     # before cutoff
    add_txn(env, "2026-09-20", "buy", "LOWER", "x", 1, -3)                   # QUIRK: SQL IN is case-sensitive
    add_txn(env, "2026-09-20", "DIVIDEND", "DIV", "x", 1, -4)
    add_txn(env, "2026-09-20", "BUY", "OTHER", "x", 1, -5, acct="account_2")
    add_txn(env, "2026-09-21", "SELL", "AMD", "sold to closing contract", 3, -6)  # lowercase marker
    add_txn(env, "2026-09-22", "BUY", "AVGO", None, 1, -7)                   # NULL description
    add_txn(env, "garbage", "BUY", "BAD", "x", 1, -8)                        # QUIRK: string compare keeps it
    add_txn(env, None, "BUY", "NULLD", "x", 1, -9)                           # NULL date dropped by SQL
    run()
    trades = env.last("traded")[0][0]
    got = sorted(({**t, "trade_date": t["trade_date"] and t["trade_date"].isoformat()}
                  for t in trades), key=lambda t: t["symbol"])
    assert got == [
        {"symbol": "AMD", "type": "SELL", "quantity": 3.0, "trade_date": "2026-09-21", "is_closing": True},
        {"symbol": "AVGO", "type": "BUY", "quantity": 1.0, "trade_date": "2026-09-22", "is_closing": False},
        {"symbol": "BAD", "type": "BUY", "quantity": 1.0, "trade_date": None, "is_closing": False},
        {"symbol": "NVDA", "type": "BUY", "quantity": 2.0, "trade_date": "2026-09-13", "is_closing": False},
    ]


def test_block_c_default_kwargs(env):
    run()
    a, kw = env.last("traded")
    assert a == ([],)
    assert kw == {"cases_on_file": set(), "circuit_symbols": set(),
                  "hard_breach_dates": set(), "negative_kelly_strategies": set(),
                  "strategy_of": None}


def test_block_c_breach_date_expansion(env):
    add_disc(env, "单票超限", "2026-09-21T09:30:00", None, "open", "S1")
    add_disc(env, "集中度超限", "2026-09-01", "2026-09-03", "resolved", "S2")
    add_disc(env, "现金底线", "2026-09-05", None, "resolved", "S3")          # no end date -> skipped
    add_disc(env, "流动性天数", "garbage", None, "open", "S4")               # bad start -> skipped
    add_disc(env, "止盈纪律", "2026-09-10", None, "open", "S5")              # non-hard dimension
    add_disc(env, "单票超限", "2026-09-10", "2026-09-08", "resolved", "S6")  # start after end
    add_disc(env, "现金底线", "2026-08-30", "2026-09-01", "resolved", "S7")  # month crossing
    add_disc(env, "单票超限", "2026-09-15", None, "open", "S8", acct="account_2")
    run()
    dates = env.last("traded")[1]["hard_breach_dates"]
    assert sorted(d.isoformat() for d in dates) == [
        "2026-08-30", "2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03",
        "2026-09-21", "2026-09-22", "2026-09-23"]


def test_block_c_mispricing_cases_parsing_tolerates_bad_lines(env):
    (env.tmp / "data").joinpath("mispricing_cases.jsonl").write_text(
        '{"ticker": "nvda"}\n{"symbol": " amd "}\nnot json\n\n{"ticker": ""}\n{"foo": 1}\n'
        '{"ticker": "", "symbol": "tsla"}\n', encoding="utf-8")
    run()
    assert env.last("traded")[1]["cases_on_file"] == {"NVDA", "AMD", "TSLA"}


def test_block_c_absent_cases_file_gives_empty_set(env):
    run()
    assert env.last("traded")[1]["cases_on_file"] == set()


@pytest.mark.parametrize("make_bad", ["directory", "bad_bytes"])
def test_block_c_unreadable_cases_file_aborts_whole_block_and_logs(env, caplog, make_bad):
    p = env.tmp / "data" / "mispricing_cases.jsonl"
    if make_bad == "directory":
        p.mkdir()
    else:
        p.write_bytes(b"\xff\xfe\x00bad")
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        out = run()
    # QUIRK: this read is NOT inner-guarded, unlike the csv and kelly reads.
    assert tags(out) == ["A", "B"]
    assert env.n("traded") == 0
    assert caplog.messages[0].startswith("traded_signals: ")


def test_block_c_circuit_csv_truthy_values_bom_and_aliases(env):
    (env.tmp / "results_validated.csv").write_text(
        "ticker,circuit_triggered\nnvda,TRUE\namd,yes\navgo,是\nmsft,1\ngoog,false\n,true\n",
        encoding="utf-8-sig")
    run()
    assert env.last("traded")[1]["circuit_symbols"] == {"NVDA", "AMD", "AVGO", "MSFT"}


def test_block_c_circuit_csv_alias_column(env):
    (env.tmp / "results_validated.csv").write_text(
        "ticker,熔断\ntsla,是\nnflx,no\n", encoding="utf-8")
    run()
    assert env.last("traded")[1]["circuit_symbols"] == {"TSLA"}


def test_block_c_unreadable_circuit_csv_is_swallowed_silently(env, caplog):
    (env.tmp / "results_validated.csv").mkdir()
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        out = run()
    assert env.last("traded")[1]["circuit_symbols"] == set()
    assert "C" in tags(out) and caplog.records == []


def test_block_c_negative_kelly_strategies(env):
    import account.performance as perf
    env.mp.setattr(perf, "compute_performance_stats", lambda acct: {"by_combo": {
        "S0": {"kelly_f_shrunk": 0.0}, "S1": {"kelly_f_shrunk": -0.1},
        "S2": {"kelly_f_shrunk": 0.2}, "S3": {"kelly_f_shrunk": None}, "S4": {}}})
    run()
    assert env.last("traded")[1]["negative_kelly_strategies"] == {"S0", "S1"}


@pytest.mark.parametrize("stats", [None, {}, RuntimeError("boom")])
def test_block_c_kelly_missing_or_failing_is_swallowed_silently(env, caplog, stats):
    import account.performance as perf

    def fake(acct):
        if isinstance(stats, Exception):
            raise stats
        return stats
    env.mp.setattr(perf, "compute_performance_stats", fake)
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        out = run()
    assert env.last("traded")[1]["negative_kelly_strategies"] == set()
    assert "C" in tags(out) and caplog.records == []


# ── Block C: first_seen backfill ────────────────────────────────────────────

def _backfill(env, signal_symbol, txns):
    env.traded_return = [{"tag": "C", "symbol": signal_symbol}]
    for i, (date, ttype, sym, desc) in enumerate(txns):
        add_txn(env, date, ttype, sym, desc, 1, -float(i + 1))
    out = run()
    return [s for s in out if s.get("tag") == "C"][0]


def test_first_seen_is_earliest_non_closing_buy_of_same_underlying(env):
    s = _backfill(env, "NVDA", [
        ("2026-09-15", "BUY", "NVDA270618C00100000", "CLOSING CONTRACT"),  # closing: ignored
        ("2026-09-18", "BUY", "NVDA", "x"),
        ("2026-09-16", "BUY", "NVDA270618P00090000", "OPEN CONTRACT"),     # earliest real open
        ("2026-09-14", "SELL", "NVDA", "x"),                               # SELL ignored
        ("2026-09-14", "BUY", "AMD", "x"),                                 # other underlying
    ])
    assert s["first_seen"] == "2026-09-16"


def test_first_seen_absent_when_only_closing_buys_exist(env):
    s = _backfill(env, "NVDA", [("2026-09-15", "BUY", "NVDA", "closing contract")])
    assert "first_seen" not in s


def test_first_seen_absent_when_only_sells_exist(env):
    s = _backfill(env, "NVDA", [("2026-09-15", "SELL", "NVDA", "x")])
    assert "first_seen" not in s


def test_first_seen_ignores_opens_with_unparseable_date(env):
    s = _backfill(env, "NVDA", [("garbage", "BUY", "NVDA", "x"),
                                ("2026-09-20", "BUY", "NVDA", "x")])
    assert s["first_seen"] == "2026-09-20"


def test_first_seen_is_case_insensitive_on_symbols_and_markers(env):
    s = _backfill(env, "nvda", [("2026-09-19", "BUY", "nvda270618c00100000", "Open contract"),
                                ("2026-09-17", "BUY", "NvDa", "Closing Contract")])
    assert s["first_seen"] == "2026-09-19"


def test_first_seen_set_per_signal_independently(env):
    env.traded_return = [{"tag": "C", "symbol": "NVDA"}, {"tag": "C", "symbol": "AMD"},
                         {"tag": "C", "symbol": "TSLA"}]
    add_txn(env, "2026-09-19", "BUY", "NVDA", "x", 1, -1)
    add_txn(env, "2026-09-20", "BUY", "AMD270618C00150000", "x", 1, -2)
    out = [s for s in run() if s.get("tag") == "C"]
    assert [s.get("first_seen") for s in out] == ["2026-09-19", "2026-09-20", None]


# ── Block D: pullback + take-profit compound ────────────────────────────────

def test_block_d_underlying_collection_and_filtering(env):
    add_opt(env, "NVDA270618C00100000")
    add_opt(env, "AMD270618C00150000", acct="account_2")   # other account's option: excluded
    add_pos(env, " amd ", 1.0)
    add_pos(env, "测试", 1.0)                       # non-ascii dropped
    add_pos(env, "", 1.0)                                   # empty dropped
    add_pos(env, "TSLA", 1.0, acct="account_2")             # QUIRK: positions have no account filter
    run()
    assert sorted(a[0][0] for a in env.calls["yf_ticker"]) == ["AMD", "NVDA", "TSLA"]


def test_block_d_bars_are_lowercased_five_columns_with_reset_index(env):
    add_pos(env, "NVDA", 1.0)
    run()
    bars = env.last("pb")[0][0]
    assert list(bars) == ["NVDA"]
    df = bars["NVDA"]
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.RangeIndex)
    assert env.last("compound")[0] == ([{"pnl": 1}], {"pb": 1})
    assert env.last("scan")[0] == ("account_1",)


def test_block_d_per_ticker_failure_and_empty_history_are_skipped_silently(env, caplog):
    _install_yfinance(env.mp, env, {"BAD": RuntimeError("net"), "EMPTY": "empty"})
    for s in ("GOOD", "BAD", "EMPTY"):
        add_pos(env, s, 1.0)
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        out = run()
    assert list(env.last("pb")[0][0]) == ["GOOD"]
    assert "D" in tags(out) and caplog.records == []


def test_block_d_no_bars_means_no_pullback_and_no_compound(env):
    _install_yfinance(env.mp, env, {"NVDA": "empty"})
    add_pos(env, "NVDA", 1.0)
    out = run()
    assert env.n("pb") == env.n("compound") == env.n("scan") == 0
    assert "D" not in tags(out)


def test_block_d_no_underlyings_never_calls_yfinance(env):
    run()
    assert env.n("yf_ticker") == 0 and env.n("pb") == 0


def test_block_d_yfinance_import_failure_is_logged_with_its_own_prefix(env, caplog):
    env.mp.setitem(sys.modules, "yfinance", None)  # import raises ImportError
    add_pos(env, "NVDA", 1.0)
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        out = run()
    assert "D" not in tags(out) and env.n("pb") == 0
    assert [m.split(":")[0] for m in caplog.messages] == ["pullback yfinance"]


def test_block_d_scan_failure_is_caught_by_outer_guard(env, caplog):
    import account.discipline as disc
    env.mp.setattr(disc, "scan_pnl_dte_signals", _boom)
    add_pos(env, "NVDA", 1.0)
    with caplog.at_level(logging.WARNING, logger="energrex.cascade"):
        out = run()
    assert "D" not in tags(out)
    assert caplog.messages == ["pullback: boom"]
