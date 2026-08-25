import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from scoring.mispricing_adapters import build_valuation_request
from scoring.mispricing_engine import ResearchDecision, as_dict, evaluate_mispricing_case
from scoring.mispricing_monitor import (
    CaseState,
    evaluate_rules,
    route_case_state,
)
from scoring.mispricing_store import append_snapshot, read_chain, verify_chain

_SCHEMA_PATH = Path(__file__).parents[1] / "scoring" / "mispricing_contract.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


TODAY = dt.date(2026, 7, 23)


def valid_case():
    evidence = []
    for index, tier in enumerate(
        ["PRIMARY_FILING", "PRIMARY_FILING", "MODEL", "PRIMARY_FILING", "MODEL"],
        1,
    ):
        evidence.append(
            {
                "evidence_id": f"e{index}",
                "claim": f"Verified claim {index}",
                "source": f"source-{index}",
                "source_tier": tier,
                "status": "VERIFIED",
                "as_of": "2026-07-20",
                "verified_at": "2026-07-21",
                "expires_at": "2026-10-31",
            }
        )
    return {
        "schema_version": "2.5",
        "ticker": "FUTU",
        "primary_path": "GREAT_BUSINESS_STUMBLE",
        "secondary_paths": ["IMPLIED_EXPECTATIONS_GAP"],
        "style_lineage": "GREAT_BUSINESS_TEMPORARY_TROUBLE",
        "quick_reject": {
            "business_explainable_150_words": True,
            "payer_identified": True,
            "price_setter_identified": True,
            "core_cash_flow_verifiable": True,
            "liquidity_runway_36m": True,
            "forced_refinancing_risk": False,
            "forced_dilution_risk": False,
            "common_equity_capture_verifiable": True,
            "thesis_beyond_famous_holder_or_short_interest": True,
            "source_quality_sufficient": True,
        },
        "payer_economics": {
            "end_user": "investor",
            "economic_payers": ["trading customer", "margin customer"],
            "price_setter": "platform, competition, rates and regulators",
            "leverage_support_assessment": "Cyclical revenue does not support utility-like leverage.",
        },
        "thesis": {
            "market_believes": "监管事件会永久破坏国际增长。",
            "variant_view": "处罚主要是一次性，核心账户与资产增长仍在。",
            "because": "海外账户增长及正常化经营利润仍保持正增长。",
            "falsifiable_by": "监管扩散至主要海外市场或客户资产持续净流出。",
        },
        "evidence": evidence,
        "gates": [
            {"name": "BUSINESS_TRUTH", "status": "PASS", "reason": "可解释。", "evidence_ids": ["e1"]},
            {"name": "SURVIVAL", "status": "PASS", "reason": "可生存。", "evidence_ids": ["e2"]},
            {"name": "MISPRICING", "status": "PASS", "reason": "有预期差。", "evidence_ids": ["e3"]},
            {"name": "VALUE_CAPTURE", "status": "PASS", "reason": "归普通股。", "evidence_ids": ["e4"]},
            {"name": "PRICE_ODDS", "status": "PASS", "reason": "赔率通过。", "evidence_ids": ["e5"]},
        ],
        "monitor_rules": [
            {
                "rule_id": "accounts-decline",
                "gate": "BUSINESS_TRUTH",
                "metric": "funded_accounts_qoq",
                "operator": "LT",
                "threshold": 0,
                "consecutive_periods": 2,
                "action": "REVIEW",
            },
            {
                "rule_id": "share-count",
                "gate": "VALUE_CAPTURE",
                "metric": "diluted_share_change_yoy",
                "operator": "GTE",
                "threshold": 0,
                "consecutive_periods": 1,
                "action": "DOWNGRADE",
            },
        ],
        "value_capture": {
            "gross_business_and_asset_value": _valued(8000, ["e4"]),
            "net_debt": _valued(-1500, ["e4"]),
            "preferred_claims": _valued(0, ["e4"]),
            "minority_interest": _valued(0, ["e4"]),
            "lease_pension_legal_obligations": _valued(50, ["e4"]),
            "maintenance_obligations": _valued(0, ["e4"]),
            "tax_and_transaction_cost": _valued(100, ["e4"]),
            "common_equity_value_available": _valued(9350, ["e4"]),
            "realization_mechanisms": ["net buyback compounding"],
            "governance_or_control_path": "Management continues executing buybacks.",
        },
        "survival_gate": {
            "liquidity_runway_months": _valued(36, ["e2"]),
            "debt_due_12m": _valued(200, ["e2"]),
            "debt_due_36m": _valued(600, ["e2"]),
            "interest_coverage": _valued(8.0, ["e2"]),
            "bear_case_cash_burn": _valued(150, ["e2"]),
            "catalyst_delay_survivable": True,
            "status": "PASS",
            "failure_reason": "",
        },
    }


def liquidation_evidence():
    return [
        {
            "evidence_id": "liq-balance-sheet",
            "claim": "Cash, marketable securities, receivables, inventory, PP&E and total debt.",
            "source": "Latest 10-Q balance sheet",
            "source_tier": "PRIMARY_FILING",
            "status": "VERIFIED",
            "as_of": "2026-07-20",
            "verified_at": "2026-07-21",
            "expires_at": "2026-10-31",
        },
        {
            "evidence_id": "liq-hidden-assets",
            "claim": "Independent appraisal of subsidiary stakes and hidden asset value.",
            "source": "Third-party appraisal report",
            "source_tier": "MODEL",
            "status": "VERIFIED",
            "as_of": "2026-07-15",
            "verified_at": "2026-07-18",
            "expires_at": "2026-10-31",
        },
        {
            "evidence_id": "liq-claims",
            "claim": "Lease, pension, legal, preferred, minority, tax and wind-down claims.",
            "source": "10-Q footnotes and legal disclosure",
            "source_tier": "PRIMARY_FILING",
            "status": "VERIFIED",
            "as_of": "2026-07-20",
            "verified_at": "2026-07-21",
            "expires_at": "2026-10-31",
        },
    ]


def _valued(value, evidence_ids):
    return {"value": value, "evidence_ids": list(evidence_ids)}


def liquidation_case():
    bs = ["liq-balance-sheet"]
    hidden = ["liq-hidden-assets"]
    claims = ["liq-claims"]
    return {
        "cash_and_equivalents": _valued(300, bs),
        "marketable_securities_recovery_value": _valued(120, bs),
        "receivables_gross": _valued(200, bs),
        "receivables_recovery_rate": _valued(0.80, bs),
        "inventory_recovery_value": _valued(90, bs),
        "property_and_equipment_conservative_sale_value": _valued(100, bs),
        "subsidiaries_and_stakes_value": _valued(40, hidden),
        "hidden_assets_value": _valued(30, hidden),
        "total_debt": _valued(250, bs),
        "lease_liabilities": _valued(70, claims),
        "pension_claims": _valued(20, claims),
        "legal_and_regulatory_claims": _valued(25, claims),
        "preferred_equity": _valued(0, claims),
        "minority_interest": _valued(15, claims),
        "tax_cost": _valued(20, claims),
        "transaction_and_wind_down_cost": _valued(30, claims),
        "cash_burn_until_realization": _valued(40, claims),
        "liquidation_timeline_months": 24,
        "current_market_cap": 220,
        "liquidation_gate": {
            "title_verified": True,
            "recovery_rates_supported": True,
            "hidden_liabilities_bounded": True,
            "cash_burn_bounded": True,
            "common_equity_priority_clear": True,
            "catalyst_or_control_path_present": True,
            "annualized_return_above_hurdle": True,
        },
    }


def liquidation_ready_case():
    """A GREAT_BUSINESS_STUMBLE base case switched onto the liquidation path,
    with liquidation evidence merged into the top-level evidence book."""
    case = valid_case()
    case["primary_path"] = "LIQUIDATION_AND_ASSET_ARBITRAGE"
    case["secondary_paths"] = []
    case["style_lineage"] = "GRAHAM_LIQUIDATION"
    case["quick_reject"]["asset_title_and_transferability_verifiable"] = True
    case["quick_reject"]["liquidation_hidden_liabilities_estimable"] = True
    case["evidence"].extend(liquidation_evidence())
    case["liquidation_value"] = liquidation_case()
    return case


class MispricingEngineTests(unittest.TestCase):
    def test_verified_case_computes_high_confidence_and_p0(self):
        decision = evaluate_mispricing_case(valid_case(), today=TODAY)
        self.assertEqual(decision.decision, ResearchDecision.DEEP_RESEARCH_P0)
        self.assertEqual(decision.confidence.value, "HIGH")
        self.assertEqual(as_dict(decision)["engine_version"], "2.5")

    def test_expired_evidence_reopens_gate(self):
        case = valid_case()
        case["evidence"][0]["expires_at"] = "2026-07-01"
        decision = evaluate_mispricing_case(case, today=TODAY)
        self.assertEqual(decision.decision, ResearchDecision.WATCH_P2)
        self.assertEqual(decision.gates[0].status.value, "NEEDS_EVIDENCE")

    def test_manual_confidence_cannot_override_computed_confidence(self):
        case = valid_case()
        case["confidence"] = "HIGH"
        for item in case["evidence"]:
            item["source_tier"] = "MANUAL"
        decision = evaluate_mispricing_case(case, today=TODAY)
        self.assertEqual(decision.confidence.value, "LOW")
        self.assertEqual(decision.decision, ResearchDecision.WATCH_P2)

    def test_price_failure_waits_without_rejecting_business(self):
        case = valid_case()
        case["gates"][-1]["status"] = "FAIL"
        decision = evaluate_mispricing_case(case, today=TODAY)
        self.assertEqual(decision.decision, ResearchDecision.WAIT_FOR_PRICE)

    def test_survival_failure_is_hard_rejection(self):
        case = valid_case()
        case["gates"][1]["status"] = "FAIL"
        decision = evaluate_mispricing_case(case, today=TODAY)
        self.assertEqual(decision.decision, ResearchDecision.REJECT_P3)

    def test_primary_path_cannot_be_double_counted(self):
        case = valid_case()
        case["secondary_paths"].append("GREAT_BUSINESS_STUMBLE")
        with self.assertRaisesRegex(ValueError, "must not be repeated"):
            evaluate_mispricing_case(case, today=TODAY)

    def test_missing_quick_reject_fields_never_default_to_pass(self):
        case = valid_case()
        del case["quick_reject"]["source_quality_sufficient"]
        decision = evaluate_mispricing_case(case, today=TODAY)
        self.assertEqual(decision.decision, ResearchDecision.WATCH_P2)
        self.assertEqual(decision.quick_reject.status.value, "NEEDS_EVIDENCE")

    def test_checkbox_cannot_replace_payer_economics(self):
        case = valid_case()
        case["payer_economics"] = {}
        decision = evaluate_mispricing_case(case, today=TODAY)
        self.assertEqual(decision.decision, ResearchDecision.WATCH_P2)
        self.assertIn("payer_economics incomplete", decision.quick_reject.reason)

    def test_schema_version_is_mandatory(self):
        case = valid_case()
        del case["schema_version"]
        with self.assertRaisesRegex(ValueError, "schema_version"):
            evaluate_mispricing_case(case, today=TODAY)

    def test_quick_reject_failure_stops_research(self):
        case = valid_case()
        case["quick_reject"]["forced_refinancing_risk"] = True
        decision = evaluate_mispricing_case(case, today=TODAY)
        self.assertEqual(decision.decision, ResearchDecision.REJECT_P3)

    def test_liquidation_path_requires_conservative_common_equity_math(self):
        decision = evaluate_mispricing_case(liquidation_ready_case(), today=TODAY)
        payload = as_dict(decision)["liquidation_value"]
        self.assertAlmostEqual(payload["estimated_common_equity_recovery"], 370)
        self.assertAlmostEqual(payload["discount_to_market_cap"], 0.681818, places=6)

    def test_liquidation_gate_failure_cannot_be_scored_around(self):
        case = liquidation_ready_case()
        case["liquidation_value"]["liquidation_gate"]["hidden_liabilities_bounded"] = False
        decision = evaluate_mispricing_case(case, today=TODAY)
        self.assertEqual(decision.decision, ResearchDecision.REJECT_P3)

    def test_non_liquidation_case_has_five_gates_only(self):
        decision = evaluate_mispricing_case(valid_case(), today=TODAY)
        self.assertEqual(len(decision.gates), 5)
        self.assertNotIn("LIQUIDATION", [gate.name.value for gate in decision.gates])

    def test_liquidation_case_promotes_sixth_gate(self):
        decision = evaluate_mispricing_case(liquidation_ready_case(), today=TODAY)
        gate_names = [gate.name.value for gate in decision.gates]
        self.assertEqual(len(decision.gates), 6)
        self.assertIn("LIQUIDATION", gate_names)
        liquidation_gate = next(g for g in decision.gates if g.name.value == "LIQUIDATION")
        self.assertEqual(liquidation_gate.status.value, "PASS")
        self.assertEqual(decision.decision, ResearchDecision.DEEP_RESEARCH_P0)

    def test_stale_liquidation_field_evidence_reopens_gate(self):
        case = liquidation_ready_case()
        for record in case["evidence"]:
            if record["evidence_id"] == "liq-hidden-assets":
                record["expires_at"] = "2026-07-01"
        decision = evaluate_mispricing_case(case, today=TODAY)
        liquidation_gate = next(g for g in decision.gates if g.name.value == "LIQUIDATION")
        self.assertEqual(liquidation_gate.status.value, "NEEDS_EVIDENCE")
        self.assertEqual(decision.decision, ResearchDecision.WATCH_P2)

    def test_liquidation_field_without_evidence_ids_is_rejected(self):
        case = liquidation_ready_case()
        case["liquidation_value"]["hidden_assets_value"] = {"value": 30, "evidence_ids": []}
        with self.assertRaisesRegex(ValueError, "evidence_ids must cite"):
            evaluate_mispricing_case(case, today=TODAY)

    def test_monitor_rule_is_executable(self):
        decision = evaluate_mispricing_case(valid_case(), today=TODAY)
        rules = as_dict(decision)["monitor_rules"]
        results = evaluate_rules(
            rules,
            {"funded_accounts_qoq": [0.02, -0.01, -0.03], "diluted_share_change_yoy": [-0.02]},
        )
        self.assertTrue(results[0].triggered)
        self.assertFalse(results[1].triggered)
        self.assertEqual(route_case_state(results), CaseState.REVIEW_REQUIRED)

    def test_price_only_opposition_does_not_force_exit_or_add(self):
        self.assertEqual(
            route_case_state((), price_only_opposition=True),
            CaseState.HOLD,
        )
        self.assertEqual(
            route_case_state(
                (),
                price_only_opposition=True,
                add_pre_authorized=True,
            ),
            CaseState.ADD_IF_PREAUTHORIZED,
        )

    def test_hash_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cases.jsonl"
            append_snapshot(path, {"ticker": "FUTU", "version": 1})
            append_snapshot(path, {"ticker": "FUTU", "version": 2})
            records = read_chain(path)
            self.assertTrue(verify_chain(records)[0])
            tampered = copy.deepcopy(records)
            tampered[0]["payload"]["ticker"] = "ALTERED"
            self.assertFalse(verify_chain(tampered)[0])


class ValueCaptureAndSurvivalGateTests(unittest.TestCase):
    """V2.5: mispricing_adapters.build_valuation_request has always read
    case.get("value_capture") / case.get("survival_gate"), but the schema
    never declared either field, so every case fed it an empty dict and the
    SURVIVAL / VALUE_CAPTURE gates were asserted PASS/FAIL with no
    structured numbers behind the claim. These pin the fix at both layers:
    the schema now requires the fields, and the adapter now actually
    receives what it has been asking for."""

    def test_schema_rejects_a_case_missing_value_capture_or_survival_gate(self):
        case = valid_case()
        del case["value_capture"]
        errors = list(jsonschema.Draft202012Validator(_SCHEMA).iter_errors(case))
        self.assertTrue(
            any("value_capture" in str(e.message) for e in errors),
            "removing value_capture should fail validation, not pass silently",
        )

    def test_schema_accepts_the_example_cases(self):
        for name in ("futu_engineering_case.json", "liquidation_engineering_case.json"):
            path = Path(__file__).parents[1] / "scoring" / "examples" / name
            case = json.loads(path.read_text(encoding="utf-8"))
            errors = list(jsonschema.Draft202012Validator(_SCHEMA).iter_errors(case))
            self.assertEqual(errors, [], f"{name}: {[e.message for e in errors]}")

    def test_adapter_no_longer_receives_an_empty_dict(self):
        """The bug in one assertion: before this fix, both sides of this
        equality were {} for every case that had ever existed."""
        request = build_valuation_request(valid_case())
        self.assertNotEqual(request["capital_structure"], {})
        self.assertNotEqual(request["value_capture_waterfall"], {})

    def test_adapter_forwards_the_declared_survival_and_capture_values(self):
        request = build_valuation_request(valid_case())
        self.assertEqual(
            request["capital_structure"]["liquidity_runway_months"]["value"], 36
        )
        self.assertEqual(
            request["value_capture_waterfall"]["common_equity_value_available"]["value"],
            9350,
        )

    def test_liquidation_case_value_capture_matches_its_own_liquidation_waterfall(self):
        """The two example cases compute the same $370 recovery through two
        independent paths (calculate_liquidation_value's itemized waterfall,
        and value_capture's coarser one) as a cross-check that the example
        data is not just schema-valid but internally consistent."""
        path = Path(__file__).parents[1] / "scoring" / "examples" / "liquidation_engineering_case.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        vc = case["value_capture"]
        computed = (
            vc["gross_business_and_asset_value"]["value"]
            - vc["net_debt"]["value"]
            - vc["preferred_claims"]["value"]
            - vc["minority_interest"]["value"]
            - vc["lease_pension_legal_obligations"]["value"]
            - vc["maintenance_obligations"]["value"]
            - vc["tax_and_transaction_cost"]["value"]
        )
        self.assertEqual(computed, vc["common_equity_value_available"]["value"])
        self.assertEqual(computed, 370)


if __name__ == "__main__":
    unittest.main()
