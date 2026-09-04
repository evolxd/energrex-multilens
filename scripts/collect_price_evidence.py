"""Collect and reconcile point-in-time market-price evidence."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scoring.valuation_source_adapters import collect_current_price_evidence  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="ENERGREX price evidence collector")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = collect_current_price_evidence(args.ticker)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        target = pathlib.Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if result["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
