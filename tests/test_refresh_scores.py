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
