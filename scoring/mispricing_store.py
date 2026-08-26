"""Append-only, hash-chained storage for mispricing decisions."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_record(payload: dict[str, Any], previous_hash: str) -> str:
    body = {"payload": payload, "previous_hash": previous_hash}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def read_chain(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}") from exc
    return records


def verify_chain(records: Iterable[dict[str, Any]]) -> tuple[bool, str]:
    previous_hash = ""
    for index, record in enumerate(records, 1):
        if record.get("previous_hash", "") != previous_hash:
            return False, f"record {index} has broken previous_hash"
        expected = _hash_record(record.get("payload", {}), previous_hash)
        if record.get("record_hash") != expected:
            return False, f"record {index} content hash mismatch"
        previous_hash = expected
    return True, ""


def latest_case_for(path: str | Path, ticker: str) -> dict[str, Any] | None:
    """Most recent snapshot for one ticker, or None if it has no thesis on file.

    The chain is a flat append-only log across all tickers, so "latest for
    this ticker" means "last record in file order whose payload.ticker
    matches" -- later snapshots supersede earlier ones for the same ticker,
    matching how the rest of this store treats an append as a new version
    rather than an edit in place.
    """
    ticker = ticker.strip().upper()
    match: dict[str, Any] | None = None
    for record in read_chain(path):
        payload = record.get("payload") or {}
        if str(payload.get("ticker") or "").strip().upper() == ticker:
            match = payload
    return match


def append_snapshot(path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    records = read_chain(target)
    valid, issue = verify_chain(records)
    if not valid:
        raise ValueError(f"Refusing to append to invalid chain: {issue}")
    previous_hash = records[-1]["record_hash"] if records else ""
    record = {
        "previous_hash": previous_hash,
        "record_hash": _hash_record(payload, previous_hash),
        "payload": payload,
    }
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record
