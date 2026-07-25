"""Point-in-time research snapshot contract for forward blind tests."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

REQUIRED_SECTIONS = {
    "market_data",
    "capital_structure",
    "business_evidence",
    "market_expectations",
    "pillars",
    "kill_thesis",
    "monitoring",
}


@dataclass(frozen=True)
class SnapshotMeta:
    case_id: str
    ticker: str
    evidence_cutoff: date
    reveal_date: date
    status: str
    sealed: bool


def _required(payload: Mapping[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValueError(f"missing required field: {key}")
    return payload[key]


def validate_snapshot_dict(payload: Mapping[str, Any]) -> SnapshotMeta:
    if str(_required(payload, "version")) != "2.3":
        raise ValueError("snapshot version must be 2.3")
    case_id = str(_required(payload, "case_id")).strip()
    ticker = str(_required(payload, "ticker")).strip().upper()
    if not case_id or not ticker:
        raise ValueError("case_id and ticker are required")
    cutoff = date.fromisoformat(str(_required(payload, "evidence_cutoff")))
    reveal = date.fromisoformat(str(_required(payload, "reveal_date")))
    if cutoff >= reveal:
        raise ValueError("evidence_cutoff must be before reveal_date")
    status = str(_required(payload, "status"))
    if status not in {"DRAFT", "SEALED"}:
        raise ValueError("status must be DRAFT or SEALED")
    sealed = bool(_required(payload, "sealed"))
    if sealed != (status == "SEALED"):
        raise ValueError("sealed flag must match status")

    missing_sections = REQUIRED_SECTIONS - set(payload)
    if missing_sections:
        raise ValueError(f"missing required sections: {sorted(missing_sections)}")

    forbidden = {"outcome", "realized_return", "post_reveal_notes", "outcome_label"}
    if forbidden.intersection(payload):
        raise ValueError("outcome fields are forbidden before reveal")

    market_data = payload["market_data"]
    for key in ("price", "shares_diluted", "market_cap"):
        if key not in market_data:
            raise ValueError(f"market_data missing {key}")
    if sealed and any(market_data[key] is None for key in ("price", "shares_diluted", "market_cap")):
        raise ValueError("SEALED snapshot requires complete market data")

    pillars = payload["pillars"]
    if not isinstance(pillars, list) or len(pillars) != 5:
        raise ValueError("snapshot must contain exactly five pillars")
    names = [item.get("name") for item in pillars]
    if len(set(names)) != 5:
        raise ValueError("snapshot pillars must be unique")
    if sealed:
        for item in pillars:
            if item.get("gate") not in {"PASS", "NEEDS_EVIDENCE", "FAIL"}:
                raise ValueError("invalid pillar gate")
            if item.get("gate") == "PASS" and not item.get("evidence"):
                raise ValueError("PASS pillar requires evidence")

    if sealed and not payload["kill_thesis"]:
        raise ValueError("SEALED snapshot requires kill thesis rules")
    if sealed and not payload["monitoring"]:
        raise ValueError("SEALED snapshot requires monitoring rules")

    return SnapshotMeta(case_id, ticker, cutoff, reveal, status, sealed)


def load_snapshot(path: str | Path) -> SnapshotMeta:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("snapshot root must be an object")
    return validate_snapshot_dict(payload)
