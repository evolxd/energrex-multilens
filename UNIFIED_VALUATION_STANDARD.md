# ENERGREX Unified Valuation Standard v1.0

## Purpose

The unified valuation service is the only owner of formal bear, base, bull,
probability-weighted and reverse-valuation outputs. It supplies P5 and
Dimension 1. It does not calculate IDI, position size, an option order or a
second investment score.

## Profiles

| Profile | Primary methods |
|---|---|
| `MATURE_PROFITABLE` | DCF and forward P/E |
| `HIGH_GROWTH` | DCF and EV/Sales |
| `FINANCIAL` | justified P/B and residual income |
| `ASSET_NAV` | adjusted NAV and liquidation recovery |

Using an inappropriate method is a routing error. In particular:

- banks and insurers must not use EV/Sales as a primary method;
- asset opportunities must value common equity after all claims and costs;
- growth companies cannot use PEG as a formal primary method;
- a historical or peer multiple cannot replace a cash-flow or asset method.

## Point-in-time evidence gate

Every required field is a record containing:

```text
value + unit + source + source_type
+ observed_at + available_at
+ VERIFIED status + secondary source
```

Rules:

- `available_at` cannot be later than the valuation timestamp;
- stale data fails according to the field-specific freshness limit;
- missing units, sources, verification or cross-checks fail the field;
- every required profile field is critical;
- formal PASS requires at least 95% validity and no critical veto;
- `ENGINEERING_ONLY` inputs can never support an actionable result.

## Scenario discipline

Bear, base and bull are mandatory. Every scenario must include an explicit
`assumption_basis`. Scenario values must satisfy:

```text
bear <= base <= bull
```

Probabilities must sum to one. The default is 25% bear, 50% base and 25% bull.

## Method governance

- at least two independent methods must pass;
- each method must carry at least 20% weight;
- method outputs are retained separately for audit;
- dispersion above 35% requires a written reconciliation;
- method names without calculated results do not count;
- a legacy `valuation_score` cannot close the formal price gate.

## Reverse valuation

The service reports what the current price requires:

- operating companies: implied constant revenue growth under the base margin path;
- financials: implied normalized ROE from current P/B;
- NAV cases: implied gross-asset recovery rate.

Reverse valuation is an expectation audit, not a target-price method.

## Price gate

After the formal data and method gates pass:

```text
PASS:
  base upside >= 25%
  and base-upside / bear-downside >= 2.0

WAIT:
  base value > current price
  but the PASS thresholds are not met

FAIL:
  base value <= current price
  or any formal valuation gate fails
```

A price PASS enters portfolio review. It never creates an order.

## Files

- `scoring/unified_valuation.py`
- `scoring/unified_valuation_request.schema.json`
- `scoring/mispricing_adapters.py`
- `scripts/run_unified_valuation.py`
- `tests/test_unified_valuation.py`

## CLI

```powershell
python .\scripts\run_unified_valuation.py `
  --input .\valuation_request.json `
  --output .\valuation_result.json
```

Exit code is zero only when the formal valuation status is PASS.
