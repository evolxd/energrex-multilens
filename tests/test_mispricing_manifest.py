import json
from collections import Counter
from datetime import date
from pathlib import Path


MANIFEST = Path("data/mispricing/backtests/case_manifest.v2.3.json")
FORBIDDEN = {"outcome", "outcome_label", "realized_return", "post_reveal_notes"}


def _payload():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_has_frozen_v23_contract():
    payload = _payload()
    assert payload["version"] == "2.3"
    assert payload["status"] == "mixed_registry_evidence_collection_and_sealed"
    assert payload["rules"]["result_fields_locked_until_reveal"] is True
    assert payload["rules"]["point_in_time_sources_required"] is True


def test_manifest_has_at_least_12_unique_slots_and_four_paths():
    payload = _payload()
    cases = payload["cases"]
    ids = [case["case_id"] for case in cases]
    paths = {case["path"] for case in cases}
    assert len(cases) >= payload["rules"]["minimum_cases"]
    assert len(ids) == len(set(ids))
    assert len(paths) >= payload["rules"]["minimum_paths"]


def test_unassigned_slots_remain_empty_and_leak_free():
    for case in _payload()["cases"]:
        assert FORBIDDEN.isdisjoint(case)
        if case["status"] == "UNASSIGNED":
            assert case["ticker"] is None
            assert case["evidence_cutoff"] is None
            assert case["reveal_date"] is None
            assert case["snapshot_file"] is None


def test_evidence_collection_cases_have_future_reveal_and_source_ledgers():
    assigned = [case for case in _payload()["cases"] if case["status"] == "EVIDENCE_COLLECTION"]
    assert len(assigned) == 3
    assert {case["ticker"] for case in assigned} == {"FUTU", "VSAT", "HRBR"}
    for case in assigned:
        cutoff = date.fromisoformat(case["evidence_cutoff"])
        reveal = date.fromisoformat(case["reveal_date"])
        assert cutoff < reveal
        path = Path(case["snapshot_file"])
        assert path.exists()
        ledger = json.loads(path.read_text(encoding="utf-8"))
        assert ledger["case_id"] == case["case_id"]
        assert ledger["ticker"] == case["ticker"]
        assert ledger["outcome_fields_locked"] is True
        assert ledger["status"] == "EVIDENCE_COLLECTION"
        assert ledger["point_in_time_sources"]
        assert FORBIDDEN.isdisjoint(ledger)


def test_sealed_dva_case_points_to_locked_snapshot_and_ledger():
    sealed = [case for case in _payload()["cases"] if case["status"] == "SEALED_UNRESOLVED"]
    assert len(sealed) == 1
    case = sealed[0]
    assert case["case_id"] == "BB-001"
    assert case["ticker"] == "DVA"
    assert date.fromisoformat(case["evidence_cutoff"]) < date.fromisoformat(case["reveal_date"])

    snapshot = json.loads(Path(case["snapshot_file"]).read_text(encoding="utf-8"))
    ledger = json.loads(Path(case["source_ledger"]).read_text(encoding="utf-8"))
    assert snapshot["status"] == "SEALED"
    assert snapshot["sealed"] is True
    assert snapshot["sealed_decision"] == "REJECT_AT_CUTOFF"
    assert snapshot["published_discovery_score"] is None
    assert ledger["status"] == "SEALED_UNRESOLVED"
    assert ledger["outcome_fields_locked"] is True
    assert FORBIDDEN.isdisjoint(snapshot)
    assert FORBIDDEN.isdisjoint(ledger)


def test_minimum_unresolved_ratio_is_preserved():
    cases = _payload()["cases"]
    unresolved = [case for case in cases if case["status"] in {"UNASSIGNED", "EVIDENCE_COLLECTION", "SEALED_UNRESOLVED"}]
    assert len(unresolved) / len(cases) >= _payload()["rules"]["minimum_unresolved_ratio"]


def test_registry_is_not_dominated_by_one_path():
    counts = Counter(case["path"] for case in _payload()["cases"])
    assert max(counts.values()) <= len(_payload()["cases"]) / 2
