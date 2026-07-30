# ENERGREX Mispricing Discovery Engine Standard v2.4

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

V2.4 routes monitoring results through a case state machine. Price-only
opposition results in `HOLD` unless a pre-authorized add condition exists.
Fact, capital-structure, liquidity, portfolio or option-limit failures can
force review, freeze adds, revaluation, reduction or exit.

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

## V2.4 downstream adapters

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
