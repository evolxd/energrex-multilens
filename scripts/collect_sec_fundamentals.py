"""Collect strict point-in-time SEC fundamental observations."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scoring.sec_fundamental_evidence import (  # noqa: E402
    collect_sec_fundamental_observations,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="ENERGREX SEC evidence collector")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = collect_sec_fundamental_observations(
        ticker=args.ticker,
        cik=args.cik,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        target = pathlib.Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if result["status"] in {"EXTRACTED", "PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
