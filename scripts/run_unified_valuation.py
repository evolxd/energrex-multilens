"""Run the ENERGREX unified valuation service from a JSON request."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scoring.unified_valuation import run_unified_valuation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="ENERGREX unified valuation")
    parser.add_argument("--input", required=True, help="valuation request JSON")
    parser.add_argument("--output", help="optional result JSON")
    args = parser.parse_args()
    request = json.loads(
        pathlib.Path(args.input).read_text(encoding="utf-8-sig")
    )
    result = run_unified_valuation(request)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        target = pathlib.Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if result["data_quality_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
