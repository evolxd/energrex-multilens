"""Build a LIVE unified-valuation request from source observations."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scoring.valuation_evidence_pipeline import build_live_valuation_request  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="ENERGREX valuation evidence pipeline")
    parser.add_argument("--input", required=True, help="evidence pipeline input JSON")
    parser.add_argument("--output", help="optional envelope output JSON")
    parser.add_argument(
        "--collect-live-price",
        action="store_true",
        help="collect Yahoo/Polygon/MarketData observations before validation",
    )
    parser.add_argument(
        "--collect-sec",
        action="store_true",
        help="collect SEC companyfacts observations before validation",
    )
    parser.add_argument("--cik", help="SEC CIK required with --collect-sec")
    args = parser.parse_args()
    payload = json.loads(pathlib.Path(args.input).read_text(encoding="utf-8-sig"))
    observations = list(payload["observations"])
    price_collection = None
    if args.collect_live_price:
        from scoring.valuation_source_adapters import collect_current_price_evidence

        price_collection = collect_current_price_evidence(payload["ticker"])
        observations = [
            item for item in observations if item.get("field") != "current_price"
        ]
        observations.extend(price_collection["observations"])
    sec_collection = None
    if args.collect_sec:
        if not args.cik:
            parser.error("--cik is required with --collect-sec")
        from scoring.sec_fundamental_evidence import (
            collect_sec_fundamental_observations,
        )

        sec_collection = collect_sec_fundamental_observations(
            ticker=payload["ticker"],
            cik=args.cik,
        )
        observations = [
            item
            for item in observations
            if item.get("source_family") != "SEC_EDGAR_XBRL"
        ]
        observations.extend(sec_collection["observations"])
    envelope = build_live_valuation_request(
        valuation_case_id=payload["valuation_case_id"],
        ticker=payload["ticker"],
        profile=payload["profile"],
        as_of=payload["as_of"],
        observations=observations,
        scenarios=payload["scenarios"],
        realization_months=payload["realization_months"],
        scenario_probabilities=payload.get("scenario_probabilities"),
        dispersion_reconciliation=payload.get("dispersion_reconciliation", ""),
    )
    if price_collection is not None:
        envelope["price_collection"] = price_collection
    if sec_collection is not None:
        envelope["sec_collection"] = sec_collection
    rendered = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        target = pathlib.Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if envelope["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
