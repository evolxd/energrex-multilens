"""End-to-end black-box characterization tests for refresh_scores.refresh_all
-- the writer behind the two "phantom drift" CSVs traced earlier this
session (results_validated.csv / data/score_snapshots.csv), and the
largest (218 lines), most branch-dense (53 branches), and previously
ZERO-coverage function in the repo.

Written BEFORE any decomposition, against the CURRENT, unmodified
implementation. The real IO seams are pinned to fixed values so this never
makes a live yfinance/FRED call and never touches the real project files:
  - `refresh_scores.fetch_portfolio_live_parallel` -- yfinance bulk fetch
  - `refresh_scores._fetch_momentum_bulk` -- yfinance price-history fetch
  - `refresh_scores.macro_data.resolve_current_risk_free_rate` -- treasury
    yield fetch
  - `refresh_scores._ROOT` is repointed at a throwaway temp directory so the
    `user_overrides.json` read (`_ROOT/"scoring"/"user_overrides.json"`)
    never touches the real file. Safe to repoint: refresh_all's only other
    use of `_ROOT` is the *default* csv_path (`_CSV_OUT`), which every call
    here overrides explicitly.
  - `scoring.kelly_snapshot_logger._SNAPSHOT_PATH` repointed the same way
    tests/test_kelly_snapshot_logger.py already does.
  - wall clock (`last_refreshed`) frozen with freezegun.

One synthetic 3-row CSV covers the three real data paths a ticker can take:
  - NVDA: in MOCK_STOCKS, live_bulk/momentum both empty -> mock data alone
    supplies the fundamentals (Layer 3), momentum stays absent.
  - TSM: NOT in MOCK_STOCKS, live_bulk supplies realistic fundamentals ->
    Layer 4 (yfinance merge) is the primary data source.
  - AVGO: in MOCK_STOCKS, plus a synthetic user_overrides.json entry ->
    Layer 6 (highest-priority manual override) wins over mock data.

`refresh_all` requires every real score/raw column to already exist in the
CSV (it does NOT create score columns for a from-scratch file, only the
handful of "denominator"/AI-profile columns listed in _FUNDAMENTAL_CSV_COLS/
_PROFILE_CSV_COLS/_SPLIT_CSV_COLS -- confirmed empirically while building
this fixture, a bare `ticker`-only CSV silently produces an output with no
score columns at all). So the fixture uses the real 77-column header and
`scoring.universe.blank_row` (the same helper refresh_all itself uses for
newcomer rows) to build well-formed starting rows.

Regenerate ONLY on the unmodified original code:
    UPDATE_GOLDEN=1 python -m pytest tests/test_refresh_scores.py -q
A missing snapshot fails the test; it is never created silently.
"""
import json
import os
import pathlib

import pandas as pd
import pytest
from freezegun import freeze_time

import refresh_scores as rs
# NOT `from scoring import kelly_snapshot_logger`: refresh_scores.py adds
# scoring/ directly to sys.path and does `from kelly_snapshot_logger import
# log_snapshot` (a BARE, unqualified import). Importing the same file again
# as `scoring.kelly_snapshot_logger` creates a SECOND, independent module
# object under a different sys.modules key -- patching ITS _SNAPSHOT_PATH
# does nothing to the bare copy refresh_all's log_snapshot() actually reads
# from, so the write silently lands on the real project file instead of the
# test's tmp path. (Caught the hard way: an earlier draft of this fixture
# briefly wrote fabricated scores into the real data/score_snapshots.csv
# under today's date before this was fixed -- reverted immediately.)
# The bare import below reuses the exact same module object refresh_scores
# is bound to, since `import refresh_scores as rs` above already ran its
# sys.path.insert and import, populating sys.modules["kelly_snapshot_logger"].
import kelly_snapshot_logger
from scoring.universe import blank_row

SNAPSHOT_DIR = pathlib.Path(__file__).parent / "golden" / "snapshots_refresh_scores"
FROZEN_NOW = "2026-09-24T12:00:00"
TICKERS = ["NVDA", "TSM", "AVGO"]

_HEADER = [
    'ticker', 'company_公司名', 'sector_板块',
    'val_估值得分(PEG/EV/ERG/PE/FCFYld)', 'grw_成长得分(营收/EPS/FCF/指引增速)',
    'qlt_质量得分(毛利率/FCF率/ROIC/负债)', 'ai_AI暴露得分(AI营收/平台/订单占比)',
    'exp_预期差得分(超预期营收EPS指引)', 'mom_动量得分(RSI14/价格vs200日均)',
    'risk_风险扣分(max20,Beta/回撤/负债)', 'final_综合得分(0-100)', 'rating_评级',
    'circuit_熔断(Beta>2.2且回撤>35%或DE>1.8)',
    'raw_peg_市盈增长比(越低越便宜)', 'raw_ev_sales_EV营收比', 'raw_forward_pe_远期市盈率',
    'raw_fcf_yield_FCF收益率(越高越好)', 'raw_rev_growth_营收同比增速', 'raw_eps_growth_EPS同比增速',
    'raw_fwd_rev_guide_NTM营收指引增速', 'raw_gross_margin_毛利率', 'raw_fcf_margin_FCF利润率',
    'raw_roic_投入资本回报率', 'raw_de_ratio_债务权益比(含可转债)', 'raw_nrr_净收入留存率(SaaS/Cyber)',
    'raw_rsi14_RSI14(45-65最佳)', 'raw_vs200ma_价格偏离200日均(%)', 'raw_beta_贝塔系数',
    'raw_max_dd_1y_1年最大回撤', 'live_fields_实时字段数', 'bad_fields_剔除字段',
    'validation_status', 'validation_confidence', 'source_conflict', 'formula_mismatch',
    'formula_diff_level', 'formula_diff_abs', 'formula_diff_reason',
    'base_score_recalculated', 'dynamic_adjustment_recalculated', 'final_score_recalculated',
    'sector_confidence', 'sector_suggested', 'raw_data_conflict', 'momentum_recalculated',
    'human_review_required', 'validation_notes',
    'source_urls_yf', 'source_urls_fmp', 'source_urls_sec', 'source_urls_10k',
    'last_refreshed',
    'raw_current_price_yf', 'raw_shares_out_yf', 'raw_fwd_eps_yf', 'raw_ev_snap_yf',
    'raw_price_at_ev_snap_yf', 'raw_net_debt_yf', 'raw_rev_ttm_yf', 'raw_fcf_ttm_yf',
    'raw_ebitda_ttm_yf', 'raw_capex_yf', 'raw_depr_yf', 'raw_rd_yf', 'raw_op_inc_yf',
    'aiprofile_AI角色', 'aiprofilekey_AI角色代码', 'aiexposure_AI暴露基础',
    'aibonus_AI加速器', 'aibasis_AI分类依据', 'weighted_基础加权', 'airaw_原始AI信号',
    'placeholder_dims_全占位维度', 'company_score_公司质量分(不受熔断影响)',
    'circuit_label_熔断分项',
    'ai_行业内百分位(同sector_tag内排名)', 'ai_行业内百分位_样本量提示',
]

_USER_OVERRIDES = {
    "AVGO": {
        "valuation_risk": {
            "value": 0.42, "status": "verified",
            "source": "characterization test fixture", "verified_at": "2026-09-01",
        },
    },
}

# Snapshot columns: enough to prove each of the three layering scenarios
# actually took effect, without asserting every one of the 77 raw columns.
_WATCHED_COLUMNS = [
    "ticker", "final_综合得分(0-100)", "rating_评级",
    "aiprofile_AI角色", "aiprofilekey_AI角色代码",
    "placeholder_dims_全占位维度", "bad_fields_剔除字段",
    "raw_current_price_yf", "raw_beta_贝塔系数", "raw_rsi14_RSI14(45-65最佳)",
    "company_score_公司质量分(不受熔断影响)", "circuit_label_熔断分项",
    "last_refreshed",
]


def _fake_live_bulk(tickers, max_workers=5):
    return {
        "NVDA": {"_errors": []},
        "TSM": {
            "_errors": [],
            "current_price": 210.0, "forward_pe": 28.5, "peg_ratio": 1.8,
            "beta": 1.15, "gross_margin": 0.56, "revenue_growth_yoy": 0.31,
        },
        "AVGO": {"_errors": []},
    }


def _fake_momentum_bulk(tickers, workers=5):
    return {
        "NVDA": {"rsi_14": 58.2, "price_vs_200dma": 0.12, "max_drawdown_1y": -0.22},
        "TSM": {},
        "AVGO": {},
    }


def _fake_risk_free_rate():
    return 0.04, "fixed test rate"


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False, default=str) + "\n"


# Columns refresh_all writes a real FLOAT into (score_updates' numeric
# fields + the two split_scores columns). Everything else refresh_all
# writes is a formatted STRING (raw_* via _fmt_yf, rating/ai-profile/
# placeholder/bad-fields/circuit-label/last_refreshed).
#
# This distinction matters only because of a pandas 3.0 quirk found while
# building this fixture: pandas 3.0 defaults an all-string CSV column to
# its new dedicated `str` dtype (not the old `object`), and assigning a
# mismatched-type value into either an all-`str` or an all-NaN-inferred
# `float64` column through `_safe_set`'s `df.loc[mask, col] = val` SILENTLY
# no-ops (the value never lands, no exception raised) instead of hitting
# `_safe_set`'s `except TypeError` fallback, which was clearly written
# against pre-3.0 pandas's more permissive `object`-dtype assignment
# behavior. In real production this basically never bites: an existing
# ticker's columns already carry real values of the right kind, which
# anchors the column's dtype long before any single-row edge case could
# flip it. It bit here only because this fixture starts from an
# all-blank 3-row file with no anchoring row -- flagged as a discovered
# finding in the final report, not fixed here (out of scope for the
# IO/pure-core split this refactor is doing).
_FLOAT_SCORE_COLUMNS = {
    'val_估值得分(PEG/EV/ERG/PE/FCFYld)', 'grw_成长得分(营收/EPS/FCF/指引增速)',
    'qlt_质量得分(毛利率/FCF率/ROIC/负债)', 'ai_AI暴露得分(AI营收/平台/订单占比)',
    'exp_预期差得分(超预期营收EPS指引)', 'mom_动量得分(RSI14/价格vs200日均)',
    'risk_风险扣分(max20,Beta/回撤/负债)', 'final_综合得分(0-100)',
    'circuit_熔断(Beta>2.2且回撤>35%或DE>1.8)',
    'airaw_原始AI信号', 'aibonus_AI加速器', 'weighted_基础加权',
    'company_score_公司质量分(不受熔断影响)',
}


@pytest.fixture(scope="module")
def refreshed(tmp_path_factory):
    """Runs refresh_all ONCE against the current, unmodified implementation,
    every IO seam pinned, and returns (returned_df, written_df, snapshot_df)."""
    tmp = tmp_path_factory.mktemp("refresh_scores_golden")
    csv_path = tmp / "results.csv"

    # blank_row() fills every non-ticker/company/sector column with "" --
    # fine for a REAL CSV (other rows anchor the column's dtype), but with
    # only 3 all-blank rows here that anchor is missing. Seed each column
    # with a value of the type it will actually receive (see
    # _FLOAT_SCORE_COLUMNS above) so pandas infers the right dtype from
    # the start, matching what an already-scored results_validated.csv
    # looks like.
    rows = []
    for t in TICKERS:
        row = blank_row(t, _HEADER)
        for col, val in row.items():
            if col == "ticker":
                continue
            if col in _FLOAT_SCORE_COLUMNS:
                row[col] = 0.0
            elif val == "":
                row[col] = "n/a [--]"
        rows.append(row)
    seed_df = pd.DataFrame(rows)
    seed_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    fake_root = tmp
    (fake_root / "scoring").mkdir(parents=True, exist_ok=True)
    (fake_root / "scoring" / "user_overrides.json").write_text(
        json.dumps(_USER_OVERRIDES), encoding="utf-8")
    fake_snapshot_path = tmp / "score_snapshots.csv"

    # Defense in depth after the real-file incident above: verify the patch
    # target really is the module object refresh_all's log_snapshot() reads
    # from, and that the path we're about to point it at is not anywhere
    # under the real project tree, before ever calling refresh_all.
    assert kelly_snapshot_logger.__dict__ is rs.log_snapshot.__globals__, (
        "kelly_snapshot_logger import is aliased differently than "
        "refresh_scores.log_snapshot's -- patching _SNAPSHOT_PATH here "
        "would silently miss and refresh_all would write the real file")
    project_root = pathlib.Path(__file__).resolve().parents[1]
    assert project_root not in fake_snapshot_path.resolve().parents, (
        f"refusing to point _SNAPSHOT_PATH at {fake_snapshot_path}, which is "
        "inside the real project tree")

    original_root = rs._ROOT
    original_fetch_live = rs.fetch_portfolio_live_parallel
    original_fetch_momentum = rs._fetch_momentum_bulk
    original_rf = rs.macro_data.resolve_current_risk_free_rate
    original_snapshot_path = kelly_snapshot_logger._SNAPSHOT_PATH
    try:
        rs._ROOT = fake_root
        rs.fetch_portfolio_live_parallel = _fake_live_bulk
        rs._fetch_momentum_bulk = _fake_momentum_bulk
        rs.macro_data.resolve_current_risk_free_rate = _fake_risk_free_rate
        kelly_snapshot_logger._SNAPSHOT_PATH = fake_snapshot_path

        with freeze_time(FROZEN_NOW):
            returned_df = rs.refresh_all(
                tickers=list(TICKERS), csv_path=csv_path, verbose=False,
            )
    finally:
        rs._ROOT = original_root
        rs.fetch_portfolio_live_parallel = original_fetch_live
        rs._fetch_momentum_bulk = original_fetch_momentum
        rs.macro_data.resolve_current_risk_free_rate = original_rf
        kelly_snapshot_logger._SNAPSHOT_PATH = original_snapshot_path

    written_df = pd.read_csv(csv_path, encoding="utf-8-sig")
    snapshot_df = (
        pd.read_csv(fake_snapshot_path, encoding="utf-8-sig")
        if fake_snapshot_path.exists() else None
    )
    return returned_df, written_df, snapshot_df


def _watched(df: pd.DataFrame) -> dict:
    """`refresh_all` can legitimately write a true empty string (e.g.
    placeholder_dims_/bad_fields_/circuit_label_ when nothing applies) --
    but a CSV round-trip can't distinguish "" from "never written", so
    to_csv/read_csv always turns it back into NaN on load. Normalizing
    NaN -> "" here is about that file-format limitation, not something
    refresh_all itself needs to handle differently."""
    df = df.set_index("ticker")

    def _cell(t, c):
        v = df.loc[t, c]
        return "" if isinstance(v, float) and pd.isna(v) else v

    return {
        t: {c: _cell(t, c) for c in _WATCHED_COLUMNS if c != "ticker"}
        for t in TICKERS
    }


def test_returned_dataframe_matches_golden_snapshot(refreshed):
    returned_df, _, _ = refreshed
    actual = _canonical(_watched(returned_df))
    snapshot = SNAPSHOT_DIR / "returned_df.json"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not snapshot.exists():
        pytest.fail(f"missing golden snapshot {snapshot.name}; generate with "
                    "UPDATE_GOLDEN=1 on the UNMODIFIED original code")
    assert actual == snapshot.read_text(encoding="utf-8"), (
        "refresh_all's returned DataFrame differs from the golden snapshot -> behaviour changed")


def test_written_csv_matches_returned_dataframe(refreshed):
    """to_csv/read_csv round-trips the watched columns exactly (proves the
    file write is not silently dropping or reformatting anything)."""
    returned_df, written_df, _ = refreshed
    assert _canonical(_watched(written_df)) == _canonical(_watched(returned_df))


def test_snapshot_file_records_all_three_tickers(refreshed):
    _, _, snapshot_df = refreshed
    assert snapshot_df is not None, "log_snapshot did not write data/score_snapshots.csv"
    assert set(snapshot_df["ticker"]) == set(TICKERS)


def test_mock_covered_ticker_keeps_mock_price_when_live_bulk_is_empty(refreshed):
    """NVDA: live_bulk has no `current_price`, so MOCK_STOCKS' own price
    must survive Layer 3 -> Layer 4 untouched (not blanked or zeroed)."""
    returned_df, _, _ = refreshed
    row = returned_df.set_index("ticker").loc["NVDA"]
    assert row["raw_current_price_yf"] not in ("n/a [--]", "", None)


def test_ordinary_ticker_picks_up_live_bulk_fields(refreshed):
    """TSM: not in MOCK_STOCKS, so its current_price must come from the
    pinned live_bulk fixture (210.0), not fall back to "n/a"."""
    returned_df, _, _ = refreshed
    row = returned_df.set_index("ticker").loc["TSM"]
    assert row["raw_current_price_yf"] != "n/a [--]"
    assert "210" in str(row["raw_current_price_yf"])


def test_momentum_only_applied_to_the_ticker_it_was_fetched_for(refreshed):
    """Only NVDA's momentum fixture is non-empty; TSM/AVGO must not pick up
    NVDA's RSI by accident."""
    returned_df, _, _ = refreshed
    df = returned_df.set_index("ticker")
    assert "58.2" in str(df.loc["NVDA", "raw_rsi14_RSI14(45-65最佳)"])
    assert df.loc["TSM", "raw_rsi14_RSI14(45-65最佳)"] in ("n/a [--]", "") or pd.isna(
        df.loc["TSM", "raw_rsi14_RSI14(45-65最佳)"])


# ════════════════════════════════════════════════════════════════════════
# TDD unit tests for the pure functions to be extracted from refresh_all:
# build_ticker_data, score_updates_for_ticker, raw_updates_for_ticker,
# compute_momentum_from_prices. Written BEFORE extraction -- these
# functions do not exist yet on `rs` (ImportError/AttributeError is the
# expected red state until Step 4 extracts them.
# ════════════════════════════════════════════════════════════════════════
import numpy as np
import unittest

from scoring.quant_engine import AuditDimension, AuditEntry, ScoreResult
from scoring.score_split import CircuitDetail, ScoreSplit


class BuildTickerDataTests(unittest.TestCase):
    """Layer-priority tests using a fake ticker ("ZZZZ") and monkeypatched
    QUANT_META/QUANT_AI_EXPOSURE/MOCK_STOCKS, so these never depend on --
    or break when someone edits -- the real project data."""

    TICKER = "ZZZZ"

    def setUp(self):
        self._orig_quant_meta = rs.QUANT_META
        self._orig_quant_ai = rs.QUANT_AI_EXPOSURE
        self._orig_mock_stocks = rs.MOCK_STOCKS
        rs.QUANT_META = {}
        rs.QUANT_AI_EXPOSURE = {}
        rs.MOCK_STOCKS = {}

    def tearDown(self):
        rs.QUANT_META = self._orig_quant_meta
        rs.QUANT_AI_EXPOSURE = self._orig_quant_ai
        rs.MOCK_STOCKS = self._orig_mock_stocks

    def _row(self, **raw_fields):
        """A minimal CSV row + raw_col_map pair for _csv_baseline. Column
        names don't need to be the real production ones -- only that
        raw_col_map maps a field name to a column _csv_baseline can read
        back via the same prefix convention _build_col_maps uses."""
        cols = {f"raw_{k}_test": v for k, v in raw_fields.items()}
        row = pd.Series({"ticker": self.TICKER, **cols})
        raw_col_map = {k: f"raw_{k}_test" for k in raw_fields}
        return row, raw_col_map

    def test_csv_baseline_alone(self):
        row, raw_col_map = self._row(beta="1.500 [yf]")
        data, applied, rejected = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={}, momentum={}, overrides={}, risk_free_rate=None,
        )
        self.assertEqual(data.get("beta"), 1.5)
        self.assertEqual(applied, [])
        self.assertEqual(rejected, [])
        self.assertNotIn("_risk_free_rate", data)

    def test_quant_meta_overlays_csv_baseline(self):
        rs.QUANT_META = {self.TICKER: {"sector_tag": "半导体设备"}}
        row, raw_col_map = self._row(beta="1.5 [yf]")
        data, _, _ = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={}, momentum={}, overrides={}, risk_free_rate=None,
        )
        self.assertEqual(data["sector_tag"], "半导体设备")
        self.assertEqual(data["beta"], 1.5)

    def test_quant_ai_exposure_only_fills_missing_fields(self):
        rs.QUANT_AI_EXPOSURE = {
            self.TICKER: {"ai_revenue_exposure_pct": 0.30, "capex_rev": 0.10},
        }
        row, raw_col_map = self._row(capex_rev="0.05 [yf]")
        data, _, _ = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={}, momentum={}, overrides={}, risk_free_rate=None,
        )
        # Filled: wasn't in the CSV baseline at all.
        self.assertEqual(data["ai_revenue_exposure_pct"], 0.30)
        # NOT overwritten: capex_rev was already present from Layer 0.
        self.assertEqual(data["capex_rev"], 0.05)

    def test_mock_stocks_layer_priority_is_the_inverse_of_what_the_field_name_suggests(self):
        """Layer 3 replaces the whole dict with MOCK_STOCKS[ticker], then
        overlays back only the keys of `data` that are NOT yfinance-
        updatable. The net effect reads backwards on first glance:
          - `beta` (IS yfinance-updatable) -> excluded from the overlay ->
            mock's value wins over the CSV baseline (until Layer 4's real
            live data potentially updates it further).
          - `debt_to_equity` (NOT yfinance-updatable, e.g. yfinance omits
            convertible debt) -> INCLUDED in the overlay -> the CSV/prior-
            layer value wins over mock's, and nothing later can touch it.
        Confirmed by reading _YFINANCE_UPDATABLE directly rather than
        assumed -- this is exactly the kind of inverted-sounding rule a
        careless decomposition could silently flip."""
        rs.MOCK_STOCKS = {
            self.TICKER: {
                "beta": 9.9,
                "debt_to_equity": 0.42,
                "company_name": "Zzzz Corp",    # mock-only field, no CSV counterpart
            },
        }
        row, raw_col_map = self._row(beta="1.5 [yf]", debt_to_equity="0.10 [yf]")
        data, _, _ = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={}, momentum={}, overrides={}, risk_free_rate=None,
        )
        self.assertEqual(data["beta"], 9.9)              # mock wins (yfinance-updatable)
        self.assertEqual(data["debt_to_equity"], 0.10)   # CSV baseline wins (not yfinance-updatable)
        self.assertEqual(data["company_name"], "Zzzz Corp")

    def test_live_bulk_overrides_mock_for_yfinance_updatable_fields(self):
        """The actual purpose of the mock/live split: Layer 4 (real,
        fresh yfinance data) must win over Layer 3's mock fallback for a
        yfinance-updatable field."""
        rs.MOCK_STOCKS = {self.TICKER: {"beta": 9.9}}
        row, raw_col_map = self._row()
        data, _, _ = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={"beta": 1.1}, momentum={}, overrides={}, risk_free_rate=None,
        )
        self.assertEqual(data["beta"], 1.1)

    def test_live_bulk_cannot_override_a_non_yfinance_updatable_mock_field(self):
        """merge_live_into_mock's _NO_OVERRIDE guard: debt_to_equity is
        deliberately excluded from yfinance's authority (yfinance omits
        convertible debt) -- live data must not clobber mock's value."""
        rs.MOCK_STOCKS = {self.TICKER: {"debt_to_equity": 0.42}}
        row, raw_col_map = self._row()
        data, _, _ = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={"debt_to_equity": 0.99}, momentum={}, overrides={},
            risk_free_rate=None,
        )
        self.assertEqual(data["debt_to_equity"], 0.42)

    def test_live_bulk_updates_yfinance_fields_via_merge(self):
        row, raw_col_map = self._row()
        data, _, _ = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={"current_price": 123.45}, momentum={}, overrides={},
            risk_free_rate=None,
        )
        self.assertEqual(data["current_price"], 123.45)

    def test_momentum_overwrites_only_non_none_values(self):
        row, raw_col_map = self._row(rsi_14="40.0 [yf]")
        data, _, _ = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={}, momentum={"rsi_14": 65.0, "max_drawdown_1y": None},
            overrides={}, risk_free_rate=None,
        )
        self.assertEqual(data["rsi_14"], 65.0)
        self.assertNotIn("max_drawdown_1y", data)

    def test_trusted_override_wins_over_every_other_layer(self):
        rs.MOCK_STOCKS = {self.TICKER: {"debt_to_equity": 0.42}}
        row, raw_col_map = self._row()
        data, applied, rejected = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={"debt_to_equity": 0.20}, momentum={},
            overrides={"debt_to_equity": {"value": 0.99, "status": "verified"}},
            risk_free_rate=None,
        )
        self.assertEqual(data["debt_to_equity"], 0.99)
        self.assertEqual(applied, ["debt_to_equity"])
        self.assertEqual(rejected, [])

    def test_untrusted_override_is_rejected_and_does_not_change_data(self):
        row, raw_col_map = self._row(beta="1.5 [yf]")
        data, applied, rejected = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={}, momentum={}, overrides={"beta": {"value": 9.9, "status": "pending"}},
            risk_free_rate=None,
        )
        self.assertEqual(data["beta"], 1.5)
        self.assertEqual(applied, [])
        self.assertEqual(rejected, ["beta"])

    def test_risk_free_rate_injected_when_present(self):
        row, raw_col_map = self._row()
        data, _, _ = rs.build_ticker_data(
            self.TICKER, row, raw_col_map,
            live={}, momentum={}, overrides={}, risk_free_rate=0.041,
        )
        self.assertEqual(data["_risk_free_rate"], 0.041)


def _make_score_result(**overrides) -> ScoreResult:
    defaults = dict(
        ticker="ZZZZ", sector="半导体设备", company_name="Zzzz Corp",
        dim_scores={
            "valuation": 70.0, "growth": 60.0, "quality": 50.0,
            "ai_exposure": 40.0, "expectation_gap": 55.0, "momentum": 65.0,
        },
        audit_dims=[
            AuditDimension(
                label="① VALUATION", key="valuation",
                entries=[AuditEntry("peg_ratio", 1.2, "f(peg)", 70.0, 1.0, missing=False)],
            ),
            AuditDimension(
                label="④ AI EXPOSURE", key="ai_exposure",
                entries=[AuditEntry("ai_revenue_exposure_pct", None, "f(ai)", 50.0, 1.0, missing=True)],
            ),
        ],
        risk_penalty=3.5, circuit_triggered=False, circuit_reason="",
        risk_multiplier=1.0, raw_sum=58.0, base_score=54.5,
        dynamic_adjustment=0.0, final_score=54.5, rating="👀 综合中性",
        bad_fields=[], ai_profile_key="AI_UNVERIFIED", ai_profile_label="AI待验证",
        ai_profile_exposure=None, ai_accelerator_bonus=0.0,
        ai_profile_basis="缺少可用AI暴露数据", ai_raw_exposure_score=0.0,
        applied_weights={}, global_score=54.5,
    )
    defaults.update(overrides)
    return ScoreResult(**defaults)


def _make_split(company=62.0, clauses=None) -> ScoreSplit:
    return ScoreSplit(
        company=company, momentum=65.0, risk=3.5,
        circuit=CircuitDetail(triggered=bool(clauses), clauses=clauses or []),
        blended=54.5,
    )


class ScoreUpdatesForTickerTests(unittest.TestCase):
    def test_basic_field_mapping(self):
        result = _make_score_result()
        split = _make_split()
        out = rs.score_updates_for_ticker(result, split)
        su = out["score_updates"]
        self.assertEqual(su["ai_profile"], "AI待验证")
        self.assertEqual(su["ai_profile_key"], "AI_UNVERIFIED")
        self.assertEqual(su["weighted_score"], 58.0)
        self.assertEqual(su["valuation_score"], 70.0)
        self.assertEqual(su["growth_score"], 60.0)
        self.assertEqual(su["risk_penalty"], 3.5)
        self.assertEqual(su["final_score"], 54.5)
        self.assertEqual(su["rating"], "👀 综合中性")
        self.assertEqual(su["circuit"], 0.0)

    def test_ai_profile_exposure_formats_none_as_empty_string(self):
        result = _make_score_result(ai_profile_exposure=None)
        out = rs.score_updates_for_ticker(result, _make_split())
        self.assertEqual(out["score_updates"]["ai_profile_exposure"], "")

    def test_ai_profile_exposure_formats_float_to_four_decimals(self):
        result = _make_score_result(ai_profile_exposure=0.123456)
        out = rs.score_updates_for_ticker(result, _make_split())
        self.assertEqual(out["score_updates"]["ai_profile_exposure"], "0.1235")

    def test_circuit_triggered_maps_to_one(self):
        result = _make_score_result(circuit_triggered=True)
        out = rs.score_updates_for_ticker(result, _make_split())
        self.assertEqual(out["score_updates"]["circuit"], 1.0)

    def test_company_score_is_none_when_split_company_is_none(self):
        out = rs.score_updates_for_ticker(_make_score_result(), _make_split(company=None))
        self.assertIsNone(out["company_score"])

    def test_company_score_rounds_to_two_decimals(self):
        out = rs.score_updates_for_ticker(_make_score_result(), _make_split(company=62.346))
        self.assertEqual(out["company_score"], 62.35)

    def test_circuit_label_joins_clauses(self):
        out = rs.score_updates_for_ticker(
            _make_score_result(), _make_split(clauses=["波动", "杠杆"]))
        self.assertEqual(out["circuit_label"], "波动 + 杠杆")

    def test_circuit_label_empty_when_no_clauses(self):
        out = rs.score_updates_for_ticker(_make_score_result(), _make_split(clauses=[]))
        self.assertEqual(out["circuit_label"], "")

    def test_placeholder_dims_only_includes_dims_with_all_entries_missing(self):
        # From _make_score_result: "valuation" has one non-missing entry,
        # "ai_exposure" has one entry with missing=True -- only the latter
        # should show up as a placeholder dimension.
        out = rs.score_updates_for_ticker(_make_score_result(), _make_split())
        self.assertEqual(out["placeholder_dims"], "ai_exposure")

    def test_placeholder_dims_requires_ALL_entries_missing_not_just_one(self):
        """A dimension with 2+ entries where only SOME are missing must
        NOT count as a placeholder -- catches an `all()` vs `any()` mixup,
        which a single-entry-per-dimension fixture (like the other test
        here) cannot distinguish: any([x]) == all([x]) for a length-1
        list. Confirmed the hard way -- an earlier version of this test
        suite only had single-entry fixtures and did not catch a
        deliberate all()->any() swap; the end-to-end golden snapshot test
        did, which is exactly why that black-box layer exists alongside
        these unit tests."""
        result = _make_score_result(audit_dims=[
            AuditDimension(
                label="① VALUATION", key="valuation",
                entries=[
                    AuditEntry("peg_ratio", 1.2, "f(peg)", 70.0, 0.5, missing=False),
                    AuditEntry("ev_sales", None, "f(evs)", 50.0, 0.5, missing=True),
                ],
            ),
        ])
        out = rs.score_updates_for_ticker(result, _make_split())
        self.assertEqual(out["placeholder_dims"], "")

    def test_placeholder_dims_empty_when_a_dim_has_no_entries_at_all(self):
        """A dimension with an empty entries list must NOT count as
        "all missing" (all([]) is True in Python -- this is exactly the
        kind of vacuous-truth trap `if d.entries and all(...)` guards
        against, and worth locking explicitly)."""
        result = _make_score_result(audit_dims=[
            AuditDimension(label="① VALUATION", key="valuation", entries=[]),
        ])
        out = rs.score_updates_for_ticker(result, _make_split())
        self.assertEqual(out["placeholder_dims"], "")

    def test_bad_fields_joined_with_semicolon(self):
        result = _make_score_result(bad_fields=["peg_ratio(capped)", "ev_ebitda(negative)"])
        out = rs.score_updates_for_ticker(result, _make_split())
        self.assertEqual(out["bad_fields"], "peg_ratio(capped); ev_ebitda(negative)")

    def test_unscoreable_overrides_rating_to_no_data_label(self):
        # All fundamental dimensions (valuation/growth/quality) missing.
        result = _make_score_result(
            rating="👀 综合中性",
            audit_dims=[
                AuditDimension(label="① VALUATION", key="valuation",
                                entries=[AuditEntry("f", None, "x", 50.0, 1.0, missing=True)]),
                AuditDimension(label="② GROWTH", key="growth",
                                entries=[AuditEntry("f", None, "x", 50.0, 1.0, missing=True)]),
                AuditDimension(label="③ QUALITY", key="quality",
                                entries=[AuditEntry("f", None, "x", 50.0, 1.0, missing=True)]),
            ],
        )
        out = rs.score_updates_for_ticker(result, _make_split())
        self.assertTrue(out["is_unscoreable"])
        self.assertEqual(out["score_updates"]["rating"], "⛔ 无数据")

    def test_scoreable_ticker_keeps_its_own_rating(self):
        out = rs.score_updates_for_ticker(_make_score_result(rating="✅ 综合良好"), _make_split())
        self.assertFalse(out["is_unscoreable"])
        self.assertEqual(out["score_updates"]["rating"], "✅ 综合良好")


class RawUpdatesForTickerTests(unittest.TestCase):
    def test_present_non_percent_field_formats_via_fmt_yf(self):
        updates = rs.raw_updates_for_ticker({"forward_pe": 28.456}, "ZZZZ", set())
        self.assertEqual(updates["forward_pe"], "28.46 [yf]")

    def test_present_percent_field_formats_as_percentage(self):
        updates = rs.raw_updates_for_ticker({"fcf_yield": 0.0456}, "ZZZZ", set())
        self.assertEqual(updates["fcf_yield"], "4.6% [yf]")

    def test_missing_field_not_in_known_bad_fields_is_omitted(self):
        updates = rs.raw_updates_for_ticker({}, "ZZZZ", set())
        self.assertNotIn("peg_ratio", updates)

    def test_missing_field_in_known_bad_fields_is_marked_na(self):
        updates = rs.raw_updates_for_ticker({}, "ZZZZ", {"peg_ratio"})
        self.assertEqual(updates["peg_ratio"], "n/a [--]")

    def test_fundamental_denominator_fields_are_included(self):
        updates = rs.raw_updates_for_ticker(
            {"current_price": 123.4, "shares_outstanding": 1.5e9}, "ZZZZ", set())
        self.assertEqual(updates["current_price"], "123.4 [yf]")
        self.assertEqual(updates["shares_outstanding"], "1.5e+09 [yf]")

    def test_recomputed_ev_ebitda_is_included(self):
        updates = rs.raw_updates_for_ticker({"ev_ebitda": 15.678}, "ZZZZ", set())
        self.assertEqual(updates["ev_ebitda"], "15.68 [yf]")


class ComputeMomentumFromPricesTests(unittest.TestCase):
    def test_too_few_points_returns_empty(self):
        close = pd.Series([100.0] * 10)
        self.assertEqual(rs.compute_momentum_from_prices(close), {})

    def test_empty_series_returns_empty(self):
        self.assertEqual(rs.compute_momentum_from_prices(pd.Series([], dtype=float)), {})

    def test_strictly_increasing_series_has_zero_drawdown(self):
        """cummax() always equals the current price on a strictly
        increasing path, so (close - peak) / peak is 0 at every point --
        an exactly hand-verifiable case, not an approximation."""
        close = pd.Series(range(100, 300), dtype=float)
        result = rs.compute_momentum_from_prices(close)
        self.assertEqual(result["max_drawdown_1y"], 0.0)
        self.assertGreater(result["price_vs_200dma"], 0.0)

    def test_strictly_increasing_series_has_no_losses_so_rsi_is_nan(self):
        """A genuinely interesting characterization, not a bug fix target:
        with zero down days in the 14-day window, the rolling loss average
        is exactly 0, `loss.replace(0, np.nan)` turns that into NaN
        specifically to avoid a division-by-zero, and RSI comes out NaN --
        NOT 100 -- for a security that never had a single losing day. This
        is the original code's real behavior, locked as-is."""
        close = pd.Series(range(100, 300), dtype=float)
        result = rs.compute_momentum_from_prices(close)
        self.assertTrue(np.isnan(result["rsi_14"]))

    def test_symmetric_alternating_series_gives_rsi_near_fifty(self):
        """Equal average gain and average loss over the RSI window ->
        RS = 1 -> RSI = 50, a hand-verifiable midpoint case."""
        base = 100.0
        prices = [base]
        for i in range(40):
            prices.append(prices[-1] + (2.0 if i % 2 == 0 else -2.0))
        close = pd.Series(prices, dtype=float)
        result = rs.compute_momentum_from_prices(close)
        self.assertAlmostEqual(result["rsi_14"], 50.0, delta=1.0)

    def test_drawdown_from_a_known_peak_and_trough(self):
        # Rises to 200 (peak), then falls to 150 -> drawdown = (150-200)/200 = -0.25.
        close = pd.Series(list(range(100, 201)) + list(range(199, 149, -1)), dtype=float)
        result = rs.compute_momentum_from_prices(close)
        self.assertAlmostEqual(result["max_drawdown_1y"], -0.25, places=4)
