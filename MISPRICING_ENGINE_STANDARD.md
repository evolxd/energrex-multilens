# ENERGREX Mispricing Discovery Engine Standard v2.6

## Purpose

The engine answers whether a specific, falsifiable market mispricing deserves
full ENERGREX research. It does not calculate a second score, fair value,
position size, or order.

## Core contract

Every case must state:

```text
Market believes X.
We believe Y.
Because Z.
We are wrong if W occurs.
```

Every case selects one primary path:

1. `GREAT_BUSINESS_STUMBLE`
2. `BUYBACK_TIME_MACHINE`
3. `HIDDEN_SCARCE_ASSET`
4. `CAPITAL_ALLOCATION_RERATING`
5. `IMPLIED_EXPECTATIONS_GAP`
6. `LIQUIDATION_AND_ASSET_ARBITRAGE`

Secondary paths add context but never add points.

Every case also declares one `style_lineage`. Style lineage explains the
economic source of return and never changes a score.

As of V2.5, every case must also declare `value_capture` and `survival_gate`
-- structured, evidence-backed numbers behind the VALUE_CAPTURE and SURVIVAL
gates, not just a hand-asserted PASS/FAIL. Before V2.5 the schema never
declared either field, so `mispricing_adapters.build_valuation_request()` --
which has always read `case.get("value_capture")` and
`case.get("survival_gate")` -- silently received `{}` for every case that
ever existed; the two gates were judgment calls with no numbers behind them.
`value_capture` states a common-equity waterfall (gross value minus net debt,
preferred claims, minority interest, lease/pension/legal obligations,
maintenance obligations, and tax/transaction cost); `survival_gate` states
the liquidity runway, near-term debt maturities, interest coverage, and
bear-case cash burn a SURVIVAL PASS is supposed to be true of. Both use the
same `{value, evidence_ids}` field convention as `liquidation_value`.

As of V2.6, every case must also declare `conviction_protocol` (see
"Principle adherence and falsification protocol" below) -- a pre-committed,
written-at-thesis-creation record of what price weakness is tolerable, what
fact changes force an exit, and what capital-structure, liquidity, position,
and portfolio limits govern this specific thesis. It does not score.

## Quick reject

V2.4 runs a polarity-aware quick reject before the gates:

- positive proof fields must be `true`;
- risk fields such as forced refinancing and dilution must be `false`;
- missing or `null` fields route to `NEEDS_EVIDENCE`;
- explicit hard failures route to `REJECT_P3`.

Unknown values never default to PASS.

## Payer economics

Every case identifies the end user, economic payers, price setter and leverage
support assessment. High payer credit quality does not imply pricing power or
unlimited leverage. Payer evidence is reused across business truth, survival,
value capture and valuation without becoming a score of its own. (The
`LIQUIDATION` gate added in V2.4 is a separate, computed gate for liquidation
math — see below — and is not payer economics gaining a dedicated score.)

## Liquidation and asset arbitrage

The liquidation path calculates common-equity recovery only after deducting
debt, leases, pension and legal claims, preferred and minority claims, taxes,
wind-down costs and cash burn. Book value is not accepted as recovery value.
Missing claims must be supplied explicitly; the calculator never assumes zero.

The liquidation gate requires verified title, supported recovery rates,
bounded hidden liabilities and cash burn, clear common-equity priority, a
catalyst or control path, and annualized return above the system hurdle.

As of V2.4, `LIQUIDATION` is a formal `GateName` member, equal in standing to
the other five gates: it is computed by
`scoring/mispricing_special_situations.py`, folded into the case's `gates`
list, and subject to `HARD_REJECTION_GATES` (a `FAIL` status routes to
`REJECT_P3`, exactly like `SURVIVAL` or `MISPRICING`). Unlike the other five
gates, it is engine-computed rather than hand-declared: a case does not add a
`LIQUIDATION` entry to its own `gates` array — the engine synthesizes it from
`liquidation_value` and appends it after parsing the five declared gates.

`LIQUIDATION` is required only when `primary_path` is
`LIQUIDATION_AND_ASSET_ARBITRAGE`; non-liquidation cases still declare exactly
five gates. Every one of the sixteen asset and claim figures under
`liquidation_value` must cite `evidence_ids` (no bare numbers) — expired,
missing, or conflicted evidence on any single figure reopens the gate to
`NEEDS_EVIDENCE`, the same reopening discipline the other five gates already
enforce. The seven `liquidation_gate` boolean checks (title, recovery rates,
hidden liabilities, cash burn, priority, catalyst, hurdle) reuse that same
evidence pool rather than requiring independent citations.

## Evidence integrity

Gate evidence must resolve to records containing:

- claim and traceable source;
- source tier;
- `VERIFIED`, `PENDING`, or `CONFLICTED` status;
- `as_of`, `verified_at`, and optional `expires_at`.

The engine calculates confidence. Users cannot override it:

```text
HIGH   = validity >= 95% and strong-source rate >= 80%
MEDIUM = validity >= 85% and strong-source rate >= 60%
LOW    = otherwise
```

Expired, missing, pending, conflicted, or future-dated evidence automatically
reopens a claimed `PASS` gate as `NEEDS_EVIDENCE`.

## Gates and routing

| Gate | Failure handling | Declared or computed |
|---|---|---|
| `BUSINESS_TRUTH` | Reject | Hand-declared |
| `SURVIVAL` | Reject | Hand-declared |
| `MISPRICING` | Reject | Hand-declared |
| `VALUE_CAPTURE` | Reject | Hand-declared |
| `PRICE_ODDS` | Wait for price | Hand-declared |
| `LIQUIDATION` | Reject | Computed; only present when `primary_path` is `LIQUIDATION_AND_ASSET_ARBITRAGE` |

A case's `gates` array always declares the first five. `LIQUIDATION` is never
part of that array — the engine computes and appends it, so a non-liquidation
case has five gates and a liquidation case has six.

Hand-declared does not mean unstructured. As of V2.5, `SURVIVAL` and
`VALUE_CAPTURE` must each cite the matching `survival_gate` /
`value_capture` object (Core contract above); the engine still does not
derive the gate's PASS/FAIL from those numbers the way it derives
`LIQUIDATION`, but the numbers now exist and reach the downstream valuation
request, which they did not before.

```text
Hard failure -> REJECT_P3
Missing evidence -> WATCH_P2
Price failure -> WAIT_FOR_PRICE
All pass + HIGH -> DEEP_RESEARCH_P0
All pass + MEDIUM -> DEEP_RESEARCH_P1
All pass + LOW -> WATCH_P2
```

## Executable monitoring

Natural-language Kill Thesis conditions are invalid. Each rule contains:

```text
rule_id + gate + metric + operator + threshold
+ consecutive_periods + action
```

Operators are `LT`, `LTE`, `GT`, `GTE`, `EQ`, `NE`, and `BETWEEN`. Allowed
actions are `REVIEW`, `PAUSE_ADD`, `DOWNGRADE`, `REDUCE`, and `EXIT`.
The engine never emits `BUY`.

At least one rule must test a non-price assumption. A price alert alone is not
a closed thesis.

A rule may optionally declare a second, milder tier: `warning_threshold` +
`action_on_warning`, alongside the required `threshold` + `action` (the fail
tier). Both warning fields must be declared together or not at all, are only
valid with `LT`/`LTE`/`GT`/`GTE` (`BETWEEN`/`EQ`/`NE` have no well-defined
milder side), and `warning_threshold` must sit on the side of `threshold`
that the metric crosses first as it degrades (greater for `LT`/`LTE`, lesser
for `GT`/`GTE`). The fail tier is checked first; if it does not trigger, the
warning tier is checked against the same observation window. This lets a
thesis distinguish "drifting, worth a REVIEW" from "broken, EXIT" on a single
metric instead of forcing every rule into one all-or-nothing trigger.

V2.4 routes monitoring results through a case state machine. Price-only
opposition results in `HOLD` unless a pre-authorized add condition exists.
Fact, capital-structure, liquidity, portfolio or option-limit failures can
force review, freeze adds, revaluation, reduction or exit.

## Principle adherence and falsification protocol

`conviction_protocol` does not score. It is written once, at thesis creation,
and read thereafter -- so that a real drawdown gets mechanically routed
against a standing decision instead of re-litigated from scratch under
pressure. It exists because price opposition and fact opposition require
different responses (see "Executable monitoring"): the protocol is where the
case commits, in writing and before the market disagrees, to which is which
for this specific thesis.

```text
thesis_core_facts + allowed_price_drawdown_range + allowed_market_opposition
+ fact_change_thresholds + mandatory_exit_triggers + add_on_weakness_conditions
+ prohibited_average_down_conditions + capital_structure_stop + liquidity_stop
+ position_limit + portfolio_loss_budget + evidence_owner + next_validation_date
```

`allowed_price_drawdown_range` states a tolerable band, not an automatic add
signal -- a drawdown inside the band still requires re-verifying core facts,
valuation, and portfolio risk before doing anything.

`fact_change_thresholds`, `capital_structure_stop`, and `liquidity_stop` each
reference a `rule_id` that must already exist in the case's own
`monitor_rules` (`capital_structure_stop` and `liquidity_stop` specifically
must reference a `SURVIVAL`-gated rule). This is deliberate: the protocol
does not define a second, parallel threshold system. The commitment is
enforced by the same executable rule it is written against, honoring the
"pre-purchase KPIs, thresholds, and Kill Thesis must use the same data as
post-purchase monitoring" principle -- a promise with no monitor rule behind
it is not a protocol, it is a note.

`mandatory_exit_triggers`, `add_on_weakness_conditions`, and
`prohibited_average_down_conditions` are free-text, because not every kill
condition reduces to a metric threshold (fraud, a blocked liquidation path,
data-integrity collapse). Averaging down is allowed only when every
`add_on_weakness_condition` holds; "already down," "averaging the cost
basis," and "it always comes back" are exactly the justifications this field
exists to block.

`position_limit` and `portfolio_loss_budget` use the same `{value,
evidence_ids}` convention as `value_capture`/`survival_gate`. They state the
position-sizing and loss-budget ceiling this specific thesis was underwritten
against; they do not replace `scoring/position_limits.py`, which governs
actual sizing decisions and its own asymmetric tightening/loosening rules.

## Immutable history

Research snapshots are append-only JSONL records chained by SHA-256 hashes.
Any historical edit breaks verification and blocks future appends.

## Downstream ownership

- Mispricing engine: path, thesis, evidence, gates, monitoring and priority.
- Five-dimensional system: quality, trend, options, events and portfolio risk.
- Valuation engine: DCF, reverse DCF, multiples, SOTP/NAV and scenario value.
- Decision policy: action eligibility without changing scores.
- Portfolio module: stock or defined-risk options and risk budget.

No mispricing case leaves existing coverage unchanged. A present case may
remove action eligibility but may never upgrade a base ENERGREX decision.

## V2.5 downstream adapters

`scoring/mispricing_adapters.py` enforces the hand-off boundary:

- the mispricing case sends assumptions, never a target price or valuation score;
- a formal valuation must identify its point-in-time source snapshot;
- at least two independent methods must each return a passing fair value;
- method dispersion above 35% requires an explicit reconciliation;
- `valuation_score` alone cannot close P5;
- a formal price PASS enters stock review, not an order;
- a price WAIT may enter defined-risk option review only after catalyst timing,
  maximum loss, break-even, upside cap, liquidity and portfolio limits pass;
- missing evidence, hard-gate failure or missing formal valuation blocks options.

`build_valuation_request()` forwards `case["survival_gate"]` as
`capital_structure` and `case["value_capture"]` as `value_capture_waterfall`.
Before V2.5 these were always `{}` (see Core contract above); a formal
valuation built on this request could not actually stress-test capital
structure or check that common equity captures the value the thesis claims,
even though the interface always looked like it was passing that data
through.

`apply_v23_case_gate` preserves the existing Final Score and score band. It
only removes action eligibility when the V2.3 mispricing, formal valuation or
formal price gate is unresolved.

Formal valuation calculation is owned by `scoring/unified_valuation.py` and
governed by `UNIFIED_VALUATION_STANDARD.md`.

## Files

- `scoring/mispricing_engine.py`
- `scoring/mispricing_evidence.py`
- `scoring/mispricing_monitor.py`
- `scoring/mispricing_store.py`
- `scoring/mispricing_adapters.py`
- `scoring/unified_valuation.py`
- `scoring/unified_valuation_request.schema.json`
- `scoring/mispricing_special_situations.py`
- `scoring/mispricing_contract.schema.json`
- `scoring/examples/futu_engineering_case.json`
- `scoring/examples/liquidation_engineering_case.json`
- `pages/4_🔎_误价研究.py`
- `tests/test_mispricing_engine.py`
- `tests/test_mispricing_adapters.py`
