"""The canonical list of tickers the system is supposed to cover.

Until now there was no such list. `refresh_scores.py` read its targets from
`results_validated.csv` -- the file it then writes -- so the universe was
defined by its own output. A ticker only ever got scored if a row for it
already existed, and nothing created that row.

The consequences were silent. TICKER_CATEGORY classified 95 tickers while
only 87 were ever scored: ACLS, AMBA, CFLT, CYBR, S, TTD, VEEV and ZM sat
classified and unscored indefinitely. ZM was even present in MOCK_STOCKS and
still never entered. And because there was no entry point at all, adding a
name meant hand-editing a results file, which is why the memory and storage
side of the AI chain held only MU while SanDisk, Western Digital and Seagate
were absent.

TICKER_CATEGORY is the universe. It already carries the industry-chain
mapping every downstream consumer needs, so making it the single source
avoids inventing a second list that can drift from it.
"""

from __future__ import annotations

from typing import Iterable

try:
    from .scoring_engine import TICKER_CATEGORY
except ImportError:  # pragma: no cover - direct import path
    from scoring_engine import TICKER_CATEGORY


def canonical_universe() -> list[str]:
    """Every ticker the system intends to score, sorted for stable output."""
    return sorted(TICKER_CATEGORY)


def chain_of(ticker: str) -> str | None:
    category = TICKER_CATEGORY.get(ticker.strip().upper())
    return category.value if category else None


def missing_from(scored: Iterable[str]) -> list[str]:
    """Universe members that never got scored -- the silent gap."""
    return sorted(set(canonical_universe()) - set(scored))


def unclassified_in(scored: Iterable[str]) -> list[str]:
    """Scored tickers with no chain mapping.

    These are not harmless: chain-concentration limits read the mapping, so an
    unmapped holding silently understates concentration.
    """
    return sorted(set(scored) - set(canonical_universe()))


def refresh_targets(scored: Iterable[str], requested: Iterable[str] | None = None) -> list[str]:
    """Which tickers a refresh should process.

    The union of the canonical universe and whatever is already scored: the
    universe so new names enter, the existing rows so nothing already tracked
    is dropped by a mapping edit. An explicit request is honoured as given,
    including for a ticker that has never been scored -- that is the whole
    point of having an entry point.
    """
    universe = set(canonical_universe()) | set(scored)
    if requested:
        wanted = [t.strip().upper() for t in requested if t and t.strip()]
        return sorted(set(wanted))
    return sorted(universe)


# The dimensions that describe the business itself. If all three defaulted,
# nothing was fetched and nothing was on file.
FUNDAMENTAL_DIMENSIONS = frozenset({"valuation", "growth", "quality"})


def is_unscoreable(placeholder_dims: Iterable[str]) -> bool:
    """Whether a score should be treated as meaningless rather than low.

    The engine always returns a number: every dimension falls back to 50 when
    its inputs are missing, so a delisted ticker lands around 40 and reads
    exactly like a genuine, mediocre assessment. CYBR and CFLT both did.
    """
    return FUNDAMENTAL_DIMENSIONS <= set(placeholder_dims)


def blank_row(ticker: str, columns: Iterable[str]) -> dict:
    """A skeleton results row for a ticker that has never been scored.

    Every field is left empty so the refresh fills it from live data; nothing
    is guessed. `company` and `sector` get placeholders only so the row is
    identifiable before its first successful fetch.
    """
    row = {column: "" for column in columns}
    row["ticker"] = ticker.strip().upper()
    for column in row:
        lowered = str(column).lower()
        if lowered.startswith("company"):
            row[column] = ticker.strip().upper()
        elif lowered.startswith("sector"):
            row[column] = chain_of(ticker) or ""
    return row
