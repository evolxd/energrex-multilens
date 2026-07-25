import json
from collections import Counter
from pathlib import Path


MANIFEST = Path("data/mispricing/backtests/case_manifest.v2.3.json")


def _payload():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_has_frozen_v23_contract():
    payload = _payload()
    assert payload["version"] == "2.3"
    assert payload["status"] == "candidate_registry_only"
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


def test_unassigned_registry_contains_no_outcome_leakage():
    payload = _payload()
    forbidden = {"outcome", "outcome_label", "realized_return", "post_reveal_notes"}
    for case in payload["cases"]:
        assert case["status"] == "UNASSIGNED"
        assert case["ticker"] is None
        assert case["evidence_cutoff"] is None
        assert case["reveal_date"] is None
        assert case["snapshot_file"] is None
        assert forbidden.isdisjoint(case)


def test_registry_is_not_dominated_by_one_path():
    counts = Counter(case["path"] for case in _payload()["cases"])
    assert max(counts.values()) <= len(_payload()["cases"]) / 2
