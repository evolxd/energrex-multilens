"""Strict point-in-time SEC XBRL extraction for formal valuation fields."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable, Mapping


REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
)
DILUTED_SHARE_TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"
NET_DEBT_TAG = "NetDebt"

# Ratios must be backed by a primary filing and are applied only to weighted
# average ordinary-share facts. The normalized result is a traded-unit
# equivalent share count suitable for a price quoted per ADS.
ADS_NORMALIZATIONS = {
    "FUTU": {
        "ordinary_shares_per_ads": 8.0,
        "source_locator": (
            "https://www.sec.gov/Archives/edgar/data/1754581/"
            "000110465926043451/R1.htm"
        ),
        "source_id": "FUTU-2025-20F-ADS-8",
    }
}


def _timestamp(value: Any, field: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def _date_end(value: Any) -> dt.datetime:
    try:
        day = dt.date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("fact end must be an ISO date") from exc
    return dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=dt.timezone.utc)


def _finite(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("fact value must be finite")
    return number


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _acceptance_map(submissions: Mapping[str, Any]) -> dict[str, dt.datetime]:
    recent = submissions.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber") or []
    accepted = recent.get("acceptanceDateTime") or []
    result: dict[str, dt.datetime] = {}
    for accession, timestamp in zip(accessions, accepted):
        try:
            result[str(accession)] = _timestamp(timestamp, "acceptanceDateTime")
        except ValueError:
            continue
    return result


def _entries(
    companyfacts: Mapping[str, Any],
    tag: str,
    unit: str,
    acceptance: Mapping[str, dt.datetime],
    as_of: dt.datetime,
) -> list[dict[str, Any]]:
    raw = (
        companyfacts.get("facts", {})
        .get("us-gaap", {})
        .get(tag, {})
        .get("units", {})
        .get(unit, [])
    )
    result = []
    for item in raw:
        accession = str(item.get("accn") or "")
        available = acceptance.get(accession)
        if not accession or available is None or available > as_of:
            continue
        if item.get("form") not in {"10-Q", "10-K", "20-F", "40-F"}:
            continue
        try:
            value = _finite(item.get("val"))
            observed = _date_end(item.get("end"))
        except (TypeError, ValueError):
            continue
        if observed > available:
            continue
        result.append(
            {
                **dict(item),
                "_value": value,
                "_observed": observed,
                "_available": available,
                "_accession": accession,
                "_tag": tag,
            }
        )
    return result


def _quarterly_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_end: dict[str, dict[str, Any]] = {}
    for item in entries:
        start = item.get("start")
        end = item.get("end")
        if not start or not end:
            continue
        try:
            duration = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
        except ValueError:
            continue
        if not 60 <= duration <= 125:
            continue
        prior = by_end.get(end)
        if prior is None or item["_available"] > prior["_available"]:
            by_end[end] = item
    return [by_end[key] for key in sorted(by_end)]


def _duration_days(item: Mapping[str, Any]) -> int | None:
    start = item.get("start")
    end = item.get("end")
    if not start or not end:
        return None
    try:
        return (dt.date.fromisoformat(str(end)) - dt.date.fromisoformat(str(start))).days
    except ValueError:
        return None


def _annual_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one latest-filed 300-400 day fact per fiscal end."""
    by_end: dict[str, dict[str, Any]] = {}
    for item in entries:
        duration = _duration_days(item)
        if duration is None or not 300 <= duration <= 400:
            continue
        prior = by_end.get(str(item["end"]))
        if prior is None or item["_available"] > prior["_available"]:
            by_end[str(item["end"])] = item
    return [by_end[key] for key in sorted(by_end)]


def _derived_fourth_quarters(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive Q4 only from same-start annual and nine-month facts.

    This is an auditable accounting identity, not an estimate. Both facts must
    already pass point-in-time availability checks and use the same XBRL tag
    and unit because ``entries`` is collected at that grain.
    """
    annuals = _annual_entries(entries)
    nine_months = [
        item
        for item in entries
        if (duration := _duration_days(item)) is not None and 240 <= duration <= 310
    ]
    derived: list[dict[str, Any]] = []
    for annual in annuals:
        candidates = []
        for ytd in nine_months:
            if ytd.get("start") != annual.get("start"):
                continue
            try:
                gap = (
                    dt.date.fromisoformat(str(annual["end"]))
                    - dt.date.fromisoformat(str(ytd["end"]))
                ).days
            except ValueError:
                continue
            if 60 <= gap <= 125 and annual["_value"] >= ytd["_value"]:
                candidates.append(ytd)
        if not candidates:
            continue
        ytd = max(candidates, key=lambda item: item["_available"])
        quarter_start = dt.date.fromisoformat(str(ytd["end"])) + dt.timedelta(days=1)
        derived.append(
            {
                **annual,
                "start": quarter_start.isoformat(),
                "_value": annual["_value"] - ytd["_value"],
                "_available": max(annual["_available"], ytd["_available"]),
                "_accessions": [ytd["_accession"], annual["_accession"]],
                "_derived_q4": True,
            }
        )
    return derived


def _lineage_accessions(items: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for item in items:
        for accession in item.get("_accessions") or [item["_accession"]]:
            if accession not in result:
                result.append(accession)
    return result


def _archive_locator(cik: str, accession: str) -> str:
    flat = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{flat}/"


def _observation(
    *,
    ticker: str,
    cik: str,
    field: str,
    value: float,
    unit: str,
    observed: dt.datetime,
    available: dt.datetime,
    retrieved: dt.datetime,
    accessions: list[str],
    extraction_method: str,
) -> dict[str, Any]:
    latest_accession = accessions[-1]
    lineage = "|".join(accessions)
    return {
        "field": field,
        "value": value,
        "unit": unit,
        "source": f"SEC EDGAR XBRL for {ticker.upper()}",
        "source_type": "PRIMARY",
        "source_family": "SEC_EDGAR_XBRL",
        "origin_family": "ISSUER_REPORTED_FINANCIALS",
        "lineage_id": f"SEC:{cik}:{field}:{lineage}",
        "source_locator": _archive_locator(cik, latest_accession),
        "observed_at": observed.isoformat(),
        "available_at": available.isoformat(),
        "retrieved_at": retrieved.isoformat(),
        "extraction_method": extraction_method,
    }


def extract_sec_companyfacts_observations(
    *,
    ticker: str,
    cik: str,
    companyfacts: Mapping[str, Any],
    submissions: Mapping[str, Any],
    as_of: dt.datetime | str,
    retrieved_at: dt.datetime | str,
) -> dict[str, Any]:
    """Extract only fields whose point-in-time construction is unambiguous."""
    cutoff = _timestamp(as_of, "as_of") if not isinstance(as_of, dt.datetime) else as_of
    retrieved = (
        _timestamp(retrieved_at, "retrieved_at")
        if not isinstance(retrieved_at, dt.datetime)
        else retrieved_at
    )
    if cutoff.tzinfo is None or retrieved.tzinfo is None:
        raise ValueError("as_of and retrieved_at must include timezone")
    cutoff = cutoff.astimezone(dt.timezone.utc)
    retrieved = retrieved.astimezone(dt.timezone.utc)
    if retrieved > cutoff:
        raise ValueError("retrieved_at cannot be after as_of")

    acceptance = _acceptance_map(submissions)
    observations: list[dict[str, Any]] = []
    issues: list[str] = []

    revenue_quarters: list[dict[str, Any]] = []
    revenue_annual: dict[str, Any] | None = None
    revenue_tag = None
    for tag in REVENUE_TAGS:
        entries = _entries(companyfacts, tag, "USD", acceptance, retrieved)
        discrete = _quarterly_entries(entries)
        by_end = {str(item["end"]): item for item in discrete}
        for derived in _derived_fourth_quarters(entries):
            by_end.setdefault(str(derived["end"]), derived)
        candidates = [by_end[key] for key in sorted(by_end)]
        if len(candidates) >= 4:
            revenue_quarters = candidates[-4:]
            revenue_tag = tag
            break
        annuals = _annual_entries(entries)
        if annuals and (
            revenue_annual is None
            or annuals[-1]["_observed"] > revenue_annual["_observed"]
        ):
            revenue_annual = annuals[-1]
            revenue_tag = tag
    if len(revenue_quarters) == 4:
        end_dates = [dt.date.fromisoformat(item["end"]) for item in revenue_quarters]
        gaps = [(right - left).days for left, right in zip(end_dates, end_dates[1:])]
        if all(70 <= gap <= 120 for gap in gaps):
            has_derived_q4 = any(item.get("_derived_q4") for item in revenue_quarters)
            observations.append(
                _observation(
                    ticker=ticker,
                    cik=cik,
                    field="revenue_ttm",
                    value=sum(item["_value"] for item in revenue_quarters),
                    unit="USD",
                    observed=revenue_quarters[-1]["_observed"],
                    available=max(item["_available"] for item in revenue_quarters),
                    retrieved=retrieved,
                    accessions=_lineage_accessions(revenue_quarters),
                    extraction_method=(
                        f"SEC companyfacts {revenue_tag}: sum four discrete "
                        "60-125 day fiscal quarters"
                        + (
                            "; Q4 derived from same-tag fiscal year minus "
                            "nine-month cumulative fact"
                            if has_derived_q4
                            else ""
                        )
                    ),
                )
            )
        else:
            issues.append("revenue_ttm: four discrete quarters are not contiguous")
    elif revenue_annual is not None:
        observations.append(
            _observation(
                ticker=ticker,
                cik=cik,
                field="revenue_ttm",
                value=revenue_annual["_value"],
                unit="USD",
                observed=revenue_annual["_observed"],
                available=revenue_annual["_available"],
                retrieved=retrieved,
                accessions=[revenue_annual["_accession"]],
                extraction_method=(
                    f"SEC companyfacts {revenue_tag}: latest reported "
                    "300-400 day fiscal-year revenue used as TTM"
                ),
            )
        )
    else:
        native_units = sorted(
            {
                unit
                for tag in REVENUE_TAGS
                for unit in (
                    companyfacts.get("facts", {})
                    .get("us-gaap", {})
                    .get(tag, {})
                    .get("units", {})
                )
                if unit != "USD"
            }
        )
        issues.append(
            "revenue_ttm: neither four auditable fiscal quarters nor a reported "
            "fiscal-year USD fact is available"
            + (
                f"; native units {native_units} require explicit FX normalization"
                if native_units
                else ""
            )
        )

    share_entries = _quarterly_entries(
        _entries(companyfacts, DILUTED_SHARE_TAG, "shares", acceptance, retrieved)
    )
    if share_entries:
        latest = share_entries[-1]
        normalization = ADS_NORMALIZATIONS.get(ticker.upper())
        share_value = latest["_value"]
        extraction_method = (
            "SEC companyfacts latest discrete-quarter weighted-average "
            "diluted shares"
        )
        accessions = [latest["_accession"]]
        if normalization:
            share_value /= normalization["ordinary_shares_per_ads"]
            extraction_method += (
                "; normalized to ADS-equivalent shares using "
                f"{normalization['ordinary_shares_per_ads']:g} ordinary shares per ADS"
            )
            # Keep the real SEC accession last because it determines the
            # navigable archive locator; the ratio filing remains in lineage.
            accessions = [normalization["source_id"], latest["_accession"]]
        observations.append(
            _observation(
                ticker=ticker,
                cik=cik,
                field="diluted_shares",
                value=share_value,
                unit="shares",
                observed=latest["_observed"],
                available=latest["_available"],
                retrieved=retrieved,
                accessions=accessions,
                extraction_method=extraction_method,
            )
        )
    else:
        issues.append("diluted_shares: discrete-quarter XBRL fact unavailable")

    net_debt_entries = _entries(
        companyfacts,
        NET_DEBT_TAG,
        "USD",
        acceptance,
        retrieved,
    )
    instant_net_debt = [item for item in net_debt_entries if not item.get("start")]
    if instant_net_debt:
        latest = max(
            instant_net_debt,
            key=lambda item: (item["_observed"], item["_available"]),
        )
        observations.append(
            _observation(
                ticker=ticker,
                cik=cik,
                field="net_cash",
                value=-latest["_value"],
                unit="USD",
                observed=latest["_observed"],
                available=latest["_available"],
                retrieved=retrieved,
                accessions=[latest["_accession"]],
                extraction_method="SEC companyfacts direct NetDebt fact, sign inverted",
            )
        )
    else:
        issues.append(
            "net_cash: direct NetDebt XBRL fact unavailable; "
            "cash, investments and debt are not mixed across taxonomies"
        )

    return {
        "ticker": ticker.upper(),
        "cik": str(cik),
        "as_of": cutoff.isoformat(),
        "observations": observations,
        "issues": issues,
        "status": "PARTIAL" if issues else "EXTRACTED",
    }


def issuer_report_observation(
    *,
    ticker: str,
    field: str,
    value: float,
    unit: str,
    period_end: str,
    published_at: str,
    retrieved_at: str,
    report_id: str,
    source_locator: str,
    extraction_method: str,
) -> dict[str, Any]:
    """Create a separate issuer-document extraction for SEC reconciliation."""
    symbol = _text(ticker, "ticker").upper()
    report = _text(report_id, "report_id")
    observed = _date_end(period_end)
    available = _timestamp(published_at, "published_at")
    retrieved = _timestamp(retrieved_at, "retrieved_at")
    if observed > available or available > retrieved:
        raise ValueError("issuer report timestamps are inconsistent")
    return {
        "field": _text(field, "field"),
        "value": _finite(value),
        "unit": _text(unit, "unit"),
        "source": f"{symbol} issuer report {report}",
        "source_type": "PRIMARY",
        "source_family": "ISSUER_IR_DOCUMENT",
        "origin_family": "ISSUER_REPORTED_FINANCIALS",
        "lineage_id": (
            f"ISSUER:{symbol}:{report}:{field}"
        ),
        "source_locator": _text(source_locator, "source_locator"),
        "observed_at": observed.isoformat(),
        "available_at": available.isoformat(),
        "retrieved_at": retrieved.isoformat(),
        "extraction_method": _text(extraction_method, "extraction_method"),
    }


def collect_sec_fundamental_observations(
    *,
    ticker: str,
    cik: str,
    as_of: dt.datetime | None = None,
    companyfacts_fetcher: Callable[[str], Mapping[str, Any] | None] | None = None,
    submissions_fetcher: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Fetch SEC payloads and return observations without promoting them."""
    if companyfacts_fetcher is None or submissions_fetcher is None:
        from scoring.edgar_fetcher import fetch_submissions, fetch_xbrl_facts

        companyfacts_fetcher = companyfacts_fetcher or fetch_xbrl_facts
        submissions_fetcher = submissions_fetcher or fetch_submissions
    companyfacts = companyfacts_fetcher(cik)
    submissions = submissions_fetcher(cik)
    retrieved = dt.datetime.now(dt.timezone.utc)
    cutoff = as_of or retrieved
    if cutoff.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if not companyfacts or not submissions:
        return {
            "ticker": ticker.upper(),
            "cik": str(cik),
            "as_of": cutoff.astimezone(dt.timezone.utc).isoformat(),
            "observations": [],
            "issues": ["SEC companyfacts or submissions payload unavailable"],
            "status": "UNAVAILABLE",
        }
    return extract_sec_companyfacts_observations(
        ticker=ticker,
        cik=cik,
        companyfacts=companyfacts,
        submissions=submissions,
        as_of=cutoff,
        retrieved_at=retrieved,
    )
