"""Deciding what to migrate between two clones' databases.

Each checkout carries its own `data/energrex.db`. When ten configured accounts
and months of history turned out to be stranded in a second clone, the question
was not "how many rows" but "is this the same period twice or two halves of one
history" -- the first is a merge conflict, the second is simply missing data,
and a row count cannot tell them apart.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from extract_from_other_db import _verdict  # noqa: E402


def test_empty_target_means_take_it():
    verdict = _verdict(500, 0, "daily_nav", "2026-06-01~2026-07-30", "")
    assert verdict.startswith("✓")


def test_earlier_disjoint_history_is_the_case_worth_migrating():
    # The real shape of the problem: the old clone holds the start of the
    # record, this one only has what accumulated after the split.
    verdict = _verdict(60, 5, "daily_nav", "2026-06-01~2026-07-30",
                       "2026-09-15~2026-09-19")
    assert verdict.startswith("✓")


def test_overlapping_periods_are_referred_to_a_human():
    verdict = _verdict(60, 55, "transactions", "2026-06-01~2026-09-19",
                       "2026-06-15~2026-09-19")
    assert verdict.startswith("⚠")
    assert "人工" in verdict


def test_a_newer_source_is_flagged_rather_than_assumed_stale():
    # If the other clone is ahead, the assumption that "this copy is canonical"
    # is the thing to check before copying anything either way.
    verdict = _verdict(60, 5, "daily_nav", "2026-09-20~2026-09-25",
                       "2026-06-01~2026-07-30")
    assert verdict.startswith("⚠")


def test_an_empty_source_has_nothing_to_offer():
    assert _verdict(0, 100, "daily_nav", "", "2026-06-01~2026-09-19").startswith("—")


def test_briefings_are_never_worth_moving():
    # Regenerated from live positions in seconds; carrying a stale snapshot
    # across only risks showing yesterday's risk figures as today's.
    assert _verdict(900, 0, "daily_briefing", "2026-06-01~2026-09-19", "").startswith("✗")


def test_accounts_are_judged_by_count_not_by_period():
    # No date column, and the import matches on id and skips what exists, so
    # there is no overlap hazard to warn about.
    assert _verdict(10, 2, "accounts", "", "").startswith("✓")
    assert _verdict(2, 10, "accounts", "", "").startswith("—")
