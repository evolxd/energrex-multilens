"""Expiry reminder for manually-curated mock_data.py fields.

Manual fields (AI exposure, valuation_risk, market_expectation_score, ROIC,
etc.) cannot be auto-refreshed from yfinance -- CLAUDE.md and
FIELD_AUDIT_METADATA already say so. This module does not remove that
dependency (it can't: the underlying numbers only exist because a human read
a filing or a web source). What it adds is traceability: a per-ticker
staleness check, so a report can say "this number may be out of date"
instead of silently presenting a stale manual estimate as current.

Each MOCK_STOCKS entry's free-text `_data_vintage` string is the only
existing per-ticker timestamp. It's unstructured prose, not a single ISO
field, so this module extracts every YYYY-MM-DD substring it contains and
treats the latest one as "last time any field on this ticker was touched" --
a deliberately conservative reading: a ticker that mixes one freshly-verified
field with older untouched ones (see DUOL's real vintage strings) still
reports the fresh date, so this signal only tells you the entry as a whole
was touched recently, not that every field on it was. Tickers with no
parseable date at all are their own category (NEEDS_REVIEW) rather than
being silently counted as fresh.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_data import MOCK_STOCKS, FIELD_AUDIT_METADATA  # noqa: E402

EARNINGS_CYCLE_DAYS = 90

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


class FreshnessStatus:
    FRESH = "FRESH"
    STALE = "STALE"
    NEEDS_REVIEW = "NEEDS_REVIEW"  # no parseable date in _data_vintage


@dataclass(frozen=True)
class FreshnessResult:
    ticker: str
    last_touched: date | None
    days_since: int | None
    status: str
    vintage_raw: str


def extract_latest_date(vintage: str) -> date | None:
    """Return the most recent YYYY-MM-DD substring in a vintage string.

    A vintage string can mention several dates ("2026-08-18 ... verified;
    all other fields still 2026-06-11 initial estimate"). The latest one is
    the most optimistic true reading of "last touched" -- still correct to
    treat as the touch date, just not proof every field shares it.
    """
    matches = _DATE_RE.findall(vintage or "")
    parsed = []
    for m in matches:
        try:
            parsed.append(datetime.strptime(m, "%Y-%m-%d").date())
        except ValueError:
            continue
    return max(parsed) if parsed else None


def manual_refresh_fields() -> set[str]:
    """Field names that FIELD_AUDIT_METADATA does not mark auto_refresh.

    These are the fields an expiry reminder actually matters for -- market
    fields tagged auto_refresh:True are kept current by yfinance regardless
    of how stale _data_vintage reads.
    """
    return {
        field for field, meta in FIELD_AUDIT_METADATA.items()
        if not meta.get("auto_refresh")
    }


def check_ticker_freshness(
    ticker: str,
    vintage: str,
    *,
    today: date | None = None,
    cycle_days: int = EARNINGS_CYCLE_DAYS,
) -> FreshnessResult:
    today = today or date.today()
    last_touched = extract_latest_date(vintage)
    if last_touched is None:
        return FreshnessResult(ticker, None, None, FreshnessStatus.NEEDS_REVIEW, vintage)
    days_since = (today - last_touched).days
    status = FreshnessStatus.STALE if days_since > cycle_days else FreshnessStatus.FRESH
    return FreshnessResult(ticker, last_touched, days_since, status, vintage)


def audit_universe(*, today: date | None = None) -> list[FreshnessResult]:
    results = []
    for ticker, data in MOCK_STOCKS.items():
        vintage = data.get("_data_vintage", "")
        results.append(check_ticker_freshness(ticker, vintage, today=today))
    return results


def main() -> None:
    results = audit_universe()
    fresh = [r for r in results if r.status == FreshnessStatus.FRESH]
    stale = [r for r in results if r.status == FreshnessStatus.STALE]
    review = [r for r in results if r.status == FreshnessStatus.NEEDS_REVIEW]

    print(f"Universe: {len(results)} tickers with a _data_vintage entry")
    print(f"  FRESH (<= {EARNINGS_CYCLE_DAYS}d):  {len(fresh)}")
    print(f"  STALE (> {EARNINGS_CYCLE_DAYS}d):   {len(stale)}")
    print(f"  NEEDS_REVIEW (no parseable date): {len(review)}")
    print()

    if stale:
        print(f"⚠️  STALE ({len(stale)}) -- sorted oldest first:")
        for r in sorted(stale, key=lambda r: r.days_since, reverse=True):
            print(f"  {r.ticker:6s}  last touched {r.last_touched}  ({r.days_since}d ago)")
        print()

    if review:
        print(f"🧾 NEEDS_REVIEW ({len(review)}) -- no ISO date found in _data_vintage:")
        for r in review:
            print(f"  {r.ticker:6s}  {r.vintage_raw!r}")
        print()

    missing = set(MOCK_STOCKS) - {r.ticker for r in results if r.vintage_raw}
    if missing:
        print(f"🚫 No _data_vintage field at all ({len(missing)}): {sorted(missing)}")


if __name__ == "__main__":
    main()
