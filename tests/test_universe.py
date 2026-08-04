"""Tests for the canonical ticker universe, including the coverage contract.

The gap this exists to close: refresh_scores.py took its targets from the
results file it writes, so the universe was defined by its own output.
TICKER_CATEGORY classified 95 tickers while 87 were scored, and the eight in
between were invisible.
"""

import csv
from pathlib import Path

import pytest

from scoring.universe import (
    FUNDAMENTAL_DIMENSIONS,
    blank_row,
    canonical_universe,
    chain_of,
    is_unscoreable,
    missing_from,
    refresh_targets,
    unclassified_in,
)

ROOT = Path(__file__).parents[1]
RESULTS = ROOT / "results_validated.csv"


def scored_tickers() -> set[str]:
    if not RESULTS.exists():
        return set()
    with RESULTS.open(encoding="utf-8-sig", newline="") as handle:
        return {row["ticker"].strip() for row in csv.DictReader(handle) if row.get("ticker")}


# ── The universe itself ────────────────────────────────────────────────

def test_universe_is_non_trivial_and_sorted():
    universe = canonical_universe()
    assert len(universe) > 50
    assert universe == sorted(universe)
    assert len(universe) == len(set(universe))


def test_every_universe_member_has_a_chain():
    """Chain concentration limits read this mapping; a member without one
    would be silently excluded from the concentration reading."""
    assert [t for t in canonical_universe() if not chain_of(t)] == []


def test_chain_lookup_is_case_insensitive_and_safe():
    first = canonical_universe()[0]
    assert chain_of(first.lower()) == chain_of(first)
    assert chain_of("NOT_A_TICKER") is None


# ── The coverage contract ──────────────────────────────────────────────

def test_every_classified_ticker_is_actually_scored():
    """The contract. Classifying a ticker and never scoring it is the exact
    failure that hid ACLS, AMBA, CFLT, CYBR, S, TTD, VEEV and ZM."""
    missing = missing_from(scored_tickers())
    assert missing == [], (
        "分类表里有但从未评分：" + ", ".join(missing)
        + "。跑 refresh_scores.py 把它们纳入，或从 TICKER_CATEGORY 移除。"
    )


def test_every_scored_ticker_is_classified():
    """The reverse gap. An unmapped holding understates chain concentration."""
    extra = unclassified_in(scored_tickers())
    assert extra == [], "已评分但无产业链分类：" + ", ".join(extra)


# ── Refresh targeting ──────────────────────────────────────────────────

def test_targets_include_universe_members_never_scored():
    """The bug in one line: a ticker that has no row must still be a target,
    otherwise it can never acquire one."""
    targets = refresh_targets(scored=["AAPL"])
    assert set(canonical_universe()) <= set(targets)


def test_targets_keep_scored_tickers_missing_from_the_universe():
    """A mapping edit must not silently drop a name that is already tracked."""
    targets = refresh_targets(scored=["AAPL", "LEGACY_NAME"])
    assert "LEGACY_NAME" in targets


def test_explicit_request_is_honoured_even_for_an_unknown_ticker():
    assert refresh_targets(scored=["AAPL"], requested=["SNDK"]) == ["SNDK"]


def test_explicit_request_is_normalised_and_deduplicated():
    assert refresh_targets(scored=[], requested=[" sndk ", "SNDK", ""]) == ["SNDK"]


# ── Blank rows ─────────────────────────────────────────────────────────

def test_blank_row_fills_identity_and_leaves_data_empty():
    columns = ["ticker", "company_公司名", "sector_板块", "raw_current_price_yf", "final_综合得分(0-100)"]
    known = canonical_universe()[0]
    row = blank_row(known, columns)
    assert row["ticker"] == known
    assert row["company_公司名"] == known
    assert row["sector_板块"] == chain_of(known)
    # Nothing is guessed: data fields stay empty for the refresh to fill.
    assert row["raw_current_price_yf"] == ""
    assert row["final_综合得分(0-100)"] == ""


def test_blank_row_covers_every_column_so_the_csv_stays_rectangular():
    columns = [f"col_{i}" for i in range(20)] + ["ticker"]
    row = blank_row("SNDK", columns)
    assert set(row) == set(columns)


def test_blank_row_leaves_sector_empty_for_an_unmapped_ticker():
    row = blank_row("NOT_A_TICKER", ["ticker", "sector_板块"])
    assert row["sector_板块"] == ""


@pytest.mark.parametrize("raw", ["sndk", " SNDK ", "Sndk"])
def test_blank_row_normalises_the_ticker(raw):
    assert blank_row(raw, ["ticker"])["ticker"] == "SNDK"


# ── Unscoreable tickers ────────────────────────────────────────────────

def test_a_delisted_ticker_is_unscoreable_not_merely_low():
    """CYBR and CFLT both scored 40.0 with no data at all. Every dimension
    falls back to 50, so the result reads like a genuine mediocre assessment
    rather than an absence of one."""
    assert is_unscoreable(
        ["valuation", "growth", "quality", "ai_exposure", "expectation_gap", "momentum"]
    )


def test_partial_gaps_are_still_scoreable():
    """A missing AI-exposure input is a gap in one dimension, not an absent
    company. TSM, S, VEEV and others sit here and must keep their scores."""
    assert not is_unscoreable(["ai_exposure", "expectation_gap"])
    assert not is_unscoreable(["momentum"])
    assert not is_unscoreable([])


def test_all_three_fundamentals_are_required_to_disqualify():
    for dim in FUNDAMENTAL_DIMENSIONS:
        partial = FUNDAMENTAL_DIMENSIONS - {dim}
        assert not is_unscoreable(partial), f"缺 {dim} 一项不应判定为无数据"
