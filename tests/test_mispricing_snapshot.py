import json
from pathlib import Path

import pytest

from scoring.mispricing_snapshot import load_snapshot, validate_snapshot_dict

DRAFT_SNAPSHOTS = [
    Path("data/mispricing/backtests/TT-001_FUTU/snapshot.json"),
    Path("data/mispricing/backtests/HA-001_VSAT/snapshot.json"),
    Path("data/mispricing/backtests/LA-001_HRBR/snapshot.json"),
]
DVA_SNAPSHOT = Path("data/mispricing/backtests/BB-001_DVA/snapshot.json")


def test_remaining_first_wave_snapshots_load_as_unsealed_drafts():
    metas = [load_snapshot(path) for path in DRAFT_SNAPSHOTS]
    assert {meta.case_id for meta in metas} == {"TT-001", "HA-001", "LA-001"}
    assert all(meta.status == "DRAFT" and not meta.sealed for meta in metas)


def test_dva_snapshot_is_sealed_and_unresolved():
    meta = load_snapshot(DVA_SNAPSHOT)
    payload = json.loads(DVA_SNAPSHOT.read_text(encoding="utf-8"))
    assert meta.case_id == "BB-001"
    assert meta.status == "SEALED"
    assert meta.sealed is True
    assert payload["overall_gate"] == "FAIL"
    assert payload["published_discovery_score"] is None
    assert payload["sealed_decision"] == "REJECT_AT_CUTOFF"
    assert "outcome" not in payload
    assert "realized_return" not in payload


def test_snapshot_rejects_outcome_leakage():
    payload = json.loads(DRAFT_SNAPSHOTS[0].read_text(encoding="utf-8"))
    payload["outcome"] = "success"
    with pytest.raises(ValueError, match="outcome fields are forbidden"):
        validate_snapshot_dict(payload)


def test_sealing_requires_market_data_kill_thesis_and_monitoring():
    payload = json.loads(DRAFT_SNAPSHOTS[0].read_text(encoding="utf-8"))
    payload["status"] = "SEALED"
    payload["sealed"] = True
    with pytest.raises(ValueError, match="complete market data"):
        validate_snapshot_dict(payload)


def test_sealed_pass_pillar_requires_evidence():
    payload = json.loads(DRAFT_SNAPSHOTS[0].read_text(encoding="utf-8"))
    payload["status"] = "SEALED"
    payload["sealed"] = True
    payload["market_data"].update({"price": 1, "shares_diluted": 1, "market_cap": 1})
    payload["kill_thesis"] = [{"metric": "x", "operator": "<", "threshold": 0, "action": "EXIT"}]
    payload["monitoring"] = [{"metric": "x", "frequency": "quarterly"}]
    payload["pillars"][0]["gate"] = "PASS"
    with pytest.raises(ValueError, match="PASS pillar requires evidence"):
        validate_snapshot_dict(payload)


def test_cutoff_must_precede_reveal():
    payload = json.loads(DRAFT_SNAPSHOTS[0].read_text(encoding="utf-8"))
    payload["reveal_date"] = payload["evidence_cutoff"]
    with pytest.raises(ValueError, match="before reveal_date"):
        validate_snapshot_dict(payload)
