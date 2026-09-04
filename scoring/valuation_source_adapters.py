"""Source-specific collection adapters for ENERGREX valuation evidence."""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Mapping

from scoring.valuation_evidence_pipeline import reconcile_field


PriceFetcher = Callable[[str], Mapping[str, Any]]


def _default_price_fetchers() -> dict[str, PriceFetcher]:
    # Lazy imports keep the evidence model usable in validation-only
    # environments where provider SDKs are not installed.
    def yahoo(ticker: str) -> Mapping[str, Any]:
        from scoring.yfinance_fetcher import fetch_price_evidence

        return fetch_price_evidence(ticker)

    def polygon(ticker: str) -> Mapping[str, Any]:
        from scoring.polygon_fetcher import fetch_price_evidence

        return fetch_price_evidence(ticker)

    def marketdata(ticker: str) -> Mapping[str, Any]:
        from scoring.marketdata_fetcher import fetch_price_evidence

        return fetch_price_evidence(ticker)

    return {
        "YAHOO_FINANCE": yahoo,
        "POLYGON": polygon,
        "MARKETDATA_APP": marketdata,
    }


def collect_current_price_evidence(
    ticker: str,
    *,
    fetchers: Mapping[str, PriceFetcher] | None = None,
    as_of: dt.datetime | None = None,
) -> dict[str, Any]:
    """Collect price observations and reconcile them without silent fallback."""
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        raise ValueError("ticker is required")
    selected = dict(fetchers or _default_price_fetchers())
    observations: list[dict[str, Any]] = []
    provider_results: list[dict[str, Any]] = []

    for configured_family, fetcher in selected.items():
        try:
            raw = dict(fetcher(symbol) or {})
            error = str(raw.get("_evidence_error") or "").strip()
            if error:
                provider_results.append(
                    {
                        "provider": configured_family,
                        "status": "UNAVAILABLE",
                        "reason": error,
                    }
                )
                continue
            actual_family = str(raw.get("source_family") or "").strip().upper()
            if actual_family != configured_family.strip().upper():
                provider_results.append(
                    {
                        "provider": configured_family,
                        "status": "INVALID",
                        "reason": (
                            f"source family mismatch: declared {actual_family or 'missing'}"
                        ),
                    }
                )
                continue
            observations.append(raw)
            provider_results.append(
                {
                    "provider": configured_family,
                    "status": "COLLECTED",
                    "reason": None,
                }
            )
        except Exception as exc:
            provider_results.append(
                {
                    "provider": configured_family,
                    "status": "ERROR",
                    "reason": str(exc),
                }
            )

    cutoff = as_of or dt.datetime.now(dt.timezone.utc)
    if cutoff.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    reconciliation = reconcile_field(
        "current_price",
        observations,
        expected_unit="USD/share",
        as_of=cutoff,
        max_age_days=3,
    )
    return {
        "ticker": symbol,
        "as_of": cutoff.astimezone(dt.timezone.utc).isoformat(),
        "status": reconciliation["status"],
        "record": reconciliation["record"],
        "observations": observations,
        "providers": provider_results,
        "reconciliation": reconciliation,
    }
