"""Point-in-time blind-case protocol for the Energrex mispricing engine.

This module prevents hindsight leakage. A case is not eligible for model
validation unless its evidence cutoff predates the reveal window and the
outcome remains hidden while the assessment is produced.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Sequence


class CaseOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    MIXED = "MIXED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class BlindCase:
    case_id: str
    ticker: str
    evidence_cutoff: date
    reveal_date: date
    primary_path: str
    source_snapshot_ids: Sequence[str]
    assessment_file: str
    expected_gate_before_reveal: str
    outcome: CaseOutcome = CaseOutcome.UNRESOLVED
    outcome_notes: str = ""

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.ticker.strip():
            raise ValueError("case_id and ticker are required")
        if self.reveal_date <= self.evidence_cutoff:
            raise ValueError("reveal_date must be after evidence_cutoff")
        if not self.source_snapshot_ids:
            raise ValueError("at least one frozen source snapshot is required")
        if not self.assessment_file.strip():
            raise ValueError("assessment_file is required")
        if self.outcome is not CaseOutcome.UNRESOLVED and not self.outcome_notes.strip():
            raise ValueError("resolved cases require outcome_notes")

    def is_blind_ready(self) -> bool:
        return self.outcome is CaseOutcome.UNRESOLVED


@dataclass(frozen=True)
class BlindCaseSet:
    cases: Sequence[BlindCase]

    def validate(self, *, minimum_cases: int = 12) -> None:
        if len(self.cases) < minimum_cases:
            raise ValueError(f"at least {minimum_cases} blind cases are required")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate blind case IDs are not allowed")
        paths = {case.primary_path for case in self.cases}
        if len(paths) < 4:
            raise ValueError("blind set must cover at least four opportunity paths")
        unresolved = [case for case in self.cases if case.is_blind_ready()]
        if len(unresolved) < minimum_cases // 2:
            raise ValueError("at least half the cases must remain unrevealed")

    def unresolved_cases(self) -> tuple[BlindCase, ...]:
        return tuple(case for case in self.cases if case.is_blind_ready())

    def resolved_cases(self) -> tuple[BlindCase, ...]:
        return tuple(case for case in self.cases if not case.is_blind_ready())
