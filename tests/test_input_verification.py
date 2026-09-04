import datetime as dt
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scoring"))
sys.path.insert(0, str(ROOT / "validation" / "validators"))

from input_verification import (
    audit_override_book,
    audit_override_entry,
    trusted_override_value,
)
from score_validator import _parse_count


TODAY = dt.date(2026, 7, 13)


def test_verified_entry_requires_source_and_date():
    entry = {"value": 1.25, "status": "verified", "verified_at": None, "source": ""}
    result = audit_override_entry("peg_ratio", entry, today=TODAY)
    assert not result.trusted
    assert any("source" in issue for issue in result.issues)
    assert any("verified_at" in issue for issue in result.issues)


def test_pending_entry_never_affects_production_scoring():
    entry = {
        "value": 99,
        "status": "pending",
        "verified_at": "2026-07-13",
        "source": "SEC 10-Q",
    }
    assert trusted_override_value("forward_pe", entry) is None


def test_complete_verified_entry_is_trusted():
    entry = {
        "value": 22.5,
        "status": "verified",
        "verified_at": "2026-07-12",
        "source": "Company Q2 2026 earnings release",
    }
    assert audit_override_entry("forward_pe", entry, today=TODAY).trusted
    assert trusted_override_value("forward_pe", entry) == 22.5


def test_future_verification_date_is_rejected():
    entry = {
        "value": 0.42,
        "status": "verified",
        "verified_at": "2026-07-14",
        "source": "SEC 10-Q",
    }
    result = audit_override_entry("gross_margin", entry, today=TODAY)
    assert not result.trusted
    assert any("future" in issue for issue in result.issues)


def test_override_book_reports_trusted_rate():
    report = audit_override_book({
        "AAA": {
            "good": {
                "value": 1,
                "status": "verified",
                "verified_at": "2026-07-12",
                "source": "SEC 10-Q",
            },
            "bad": {"value": 2, "status": "verified", "verified_at": "2026-07-12", "source": ""},
        }
    })
    assert report["total_entries"] == 2
    assert report["trusted_entries"] == 1
    assert report["trusted_rate"] == 0.5


def test_validation_count_parser_accepts_csv_float_strings():
    assert _parse_count("18.0") == 18
    assert _parse_count("18") == 18
    assert _parse_count(18.0) == 18
    assert _parse_count("not-a-number") == 0
