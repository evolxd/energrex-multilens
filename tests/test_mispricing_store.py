import json
from pathlib import Path

import pytest

from scoring.mispricing_engine import GateStatus
from scoring.mispricing_store import SCHEMA_VERSION, assessment_from_dict, load_assessment


FIXTURE = Path("data/mispricing/FUTU.template.json")


def test_futu_template_loads_but_cannot_publish_score():
    assessment = load_assessment(FIXTURE)
    assert assessment.ticker == "FUTU"
    assert assessment.overall_gate() is GateStatus.NEEDS_EVIDENCE
    assert assessment.discovery_score() is None
    assert assessment.payer_economics is not None
    assert "leverage" in assessment.payer_economics.leverage_support_assessment.lower()


def test_loader_rejects_unknown_schema_version():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["version"] = "9.9"
    with pytest.raises(ValueError, match="unsupported mispricing schema version"):
        assessment_from_dict(payload)


def test_loader_rejects_missing_required_field():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del payload["variant_view"]
    with pytest.raises(ValueError, match="missing required field: variant_view"):
        assessment_from_dict(payload)


def test_loader_rejects_non_array_evidence():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["pillars"][0]["evidence"] = "not-a-list"
    with pytest.raises(ValueError, match="evidence must be a JSON array"):
        assessment_from_dict(payload)


def test_loader_rejects_duplicate_pillars_before_ui_use():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["pillars"][-1] = dict(payload["pillars"][0])
    with pytest.raises(ValueError, match="Duplicate pillars"):
        assessment_from_dict(payload)


def test_schema_version_is_frozen_for_v23():
    assert SCHEMA_VERSION == "2.3"
