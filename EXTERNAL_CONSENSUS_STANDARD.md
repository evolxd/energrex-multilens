# ENERGREX External Consensus Standard

This standard governs external reference data such as Seeking Alpha ratings.
External consensus is a calibration layer, not a core scoring dimension.

## Purpose

External consensus answers one question:

```text
Is ENERGREX materially more optimistic or more cautious than visible outside opinion?
```

It does not answer whether the stock should be bought or sold.

## Current Fields

The Streamlit app supports these optional manual fields:

- `sa_quant_score`: Seeking Alpha Quant Rating, 0-5
- `sa_author_score`: Seeking Alpha Authors Rating, 0-5
- `sa_wall_street_score`: Seeking Alpha Wall Street Rating, 0-5

Use `0` when the value is unavailable or not checked.

Scale mapping:

- 1 = Strong Sell
- 2 = Sell
- 3 = Hold
- 4 = Buy
- 5 = Strong Buy

## Scoring Relationship

External consensus must not directly change:

- Final Score
- valuation score
- growth score
- quality score
- price-temperature bands
- Kelly position sizing

The app may convert the available 1-5 external average into a 0-100 reference
score only to measure disagreement with ENERGREX Final Score.

## Disagreement Rules

Let:

```text
External_Reference_Score = (Average_External_Rating - 1) / 4 * 100
Delta = External_Reference_Score - ENERGREX_Final_Score
```

Interpretation:

- `abs(Delta) < 12`: external opinion and ENERGREX are broadly aligned
- `Delta <= -12`: external opinion is materially more cautious
- `Delta >= 12`: external opinion is materially more optimistic

Machine-readable statuses:

- `NO_EXTERNAL_REFERENCE`: no external rating is entered
- `ALIGNED`: external opinion is broadly aligned with ENERGREX
- `EXTERNAL_MORE_CAUTIOUS`: external opinion is materially more cautious
- `EXTERNAL_MORE_BULLISH`: external opinion is materially more optimistic

## Disagreement Handling

External disagreement triggers review, not automatic score changes.

Rules:

- `ALIGNED`: no score action; keep the external reference record.
- `EXTERNAL_MORE_CAUTIOUS`: trigger disagreement audit. Recheck valuation
  risk, growth assumptions, margins, valuation multiples, event risk, and data
  freshness.
- `EXTERNAL_MORE_BULLISH`: trigger reverse disagreement audit. Recheck whether
  ENERGREX missed analyst upgrades, earnings recovery, market-share gains, AI
  exposure, or operating leverage.

Final Score may change only after a concrete data error, stale input, missing
input, or documented assumption error is found and corrected through the normal
data-edit / refresh process.

External consensus never directly changes:

- rating
- price-temperature bands
- position sizing
- report recommendation language

## Data Governance

External reference fields are optional. Missing external consensus must not
penalize a report.

When manually entering Seeking Alpha data:

- record the source note when possible;
- do not copy paid article text or proprietary commentary into reports;
- use only rating numbers/labels as reference metadata;
- refresh the value when the visible rating page changes materially;
- mark the source as `external_reference`, not as verified financial data.

## Report Language

Approved wording:

- "External reference"
- "External consensus check"
- "Seeking Alpha reference"
- "External opinion is more cautious / more optimistic / broadly aligned"

Avoid wording that implies endorsement or authority:

- "Seeking Alpha proves"
- "Seeking Alpha confirms"
- "The correct rating is"
- "Buy because SA says Buy"
