# ENERGREX Protective Put Governance Standard

This standard governs QQQ/SMH protective put usage inside the account-risk
system.

## Principle

Protective puts are temporary insurance. They are not long-term investment
assets.

The system must evaluate the discipline of the hedge, not whether the trader
felt afraid when buying it.

## Allowed Use

Protective puts are allowed only when at least one system-risk trigger is active:

- Beta-Delta exposure is above the configured portfolio target
- VIX spike or volatility shock
- QQQ/SMH trend break
- near-term event-risk cluster

If no trigger is active, a long put hedge is treated as idle negative carry and
must be reviewed for exit.

## QQQ Protection Trigger Standard

Do not use moving averages or VIX as standalone trade orders. They are inputs to
a hedge review.

Required inputs:

- QQQ current price
- QQQ 50-day moving average
- QQQ 200-day moving average
- VIX level
- portfolio Beta-Delta percentage
- target Beta-Delta percentage
- event-risk flag when relevant

VIX regime:

- `VIX <= 15`: low-IV window. Options may be cheaper, but low IV alone is not a
  hedge trigger.
- `15 < VIX <= 20`: neutral-IV window. Hedge only with a clear portfolio or
  trend trigger; prefer spreads.
- `VIX > 20`: high-IV / stress window. Naked long puts are expensive; prefer
  spreads, rolls, or exposure reduction.

Protection levels:

```text
No hedge        = no sufficient system-risk trigger
Light protection = target 10%-15% Beta-Delta risk coverage
Heavy protection = target 25%-35% Beta-Delta risk coverage
```

Coverage means the percentage of portfolio Beta-Delta exposure to offset. It
does not mean spending 15% or 30% of account equity on options.

Default cost budgets:

- Light protection: max 0.3%-0.5% of account equity per campaign.
- Heavy protection: max 0.8%-1.0% of account equity per campaign.

If required protection cannot fit the budget, reduce portfolio exposure instead
of buying symbolic or oversized puts.

## Strike Selection Standard

The old draft formula is acceptable only as a starting template, not as an
automatic order generator.

Light protection template:

```text
Trigger: QQQ below MA50 AND portfolio/event trigger is active
Buy put:  approximately 4% below QQQ spot
Sell put: approximately 8% of QQQ spot below the buy-put strike
DTE:      45-90 days
```

Heavy protection template:

```text
Trigger: QQQ below MA200 OR confirmed major trend break
Buy put:  approximately 8% below QQQ spot
Sell put: approximately 16% of QQQ spot below the buy-put strike
DTE:      45-90 days
```

Strike outputs must be rounded to liquid option strikes, normally $5 increments
for QQQ. Before execution, validate bid/ask spread, open interest, and expected
premium budget.

## Audit of the Draft Function

Keep:

- VIX regime as a cost-awareness layer.
- MA50 and MA200 as trend-risk inputs.
- Put spread as default structure.
- Separate light and heavy protection levels.

Change:

- `VIX <= 15` is not automatically a "golden entry"; it is only a cheaper-cost
  window if another hedge trigger exists.
- QQQ below MA50 alone should not force a hedge; require portfolio exposure,
  event risk, or confirmed market weakness.
- QQQ below MA200 should trigger heavy hedge review, but still respect cost,
  liquidity, and position-size rules.
- The function must return structured data, not only print instructions.
- Every hedge must include exit review: DTE <= 21, trigger removed, or budget
  breach.

## Preferred Structure

For small accounts, the preferred structure is a debit put spread:

```text
Buy higher-strike put
Sell lower-strike put
Same underlying and expiry
```

Reason: the spread caps protection, but materially reduces premium decay.

Naked long puts are allowed only as short-term, trigger-specific insurance. They
should not be held as a standing hedge.

## Budget Rules

Default campaign budget:

```text
Max current hedge premium = 1.0% of account equity
```

If the hedge budget is exceeded, the system should prefer:

1. reduce position size or Beta exposure;
2. use a cheaper put spread;
3. skip the hedge.

Do not keep buying protection after the budget is exhausted.

## Holding Rules

- DTE <= 21: close, roll, or explicitly let expire by plan.
- Naked long put DTE > 45: violation unless there is a documented short-term
  event hedge reason.
- Put spread DTE > 120: review the event window and cost.
- No active trigger: idle hedge violation.

## Status Labels

- `VALID_HEDGE`: trigger exists, structure is controlled, budget is within limit.
- `REVIEW`: allowed but needs documented trigger, budget, or exit plan.
- `VIOLATION`: violates hedge discipline; review or exit.
- `MISSING_HEDGE`: system-risk trigger is active but no QQQ/SMH long put hedge
  exists.
- `NO_HEDGE_NEEDED`: no active trigger and no hedge needed.

## Post-Trade Review

Every protective put campaign should be reviewed with:

- trigger at entry;
- structure chosen: naked put or spread;
- premium cost as percentage of equity;
- DTE at entry and exit;
- whether it reduced forced-selling risk;
- whether cash/position reduction would have been cheaper.

The correct lesson from a decayed long put is not "never hedge"; it is:

```text
Do not hold insurance after the risk trigger is gone.
```
