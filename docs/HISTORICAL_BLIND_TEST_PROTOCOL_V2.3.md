# HISTORICAL BLIND TEST PROTOCOL V2.3

## Purpose

Test whether the Mispricing & Special Situations Engine can make useful decisions using only information available at a historical cutoff date. This protocol is designed to prevent hindsight leakage, cherry-picking and outcome-driven rescoring.

## Case lifecycle

1. **Assign** a ticker and opportunity path to an empty manifest slot.
2. **Freeze** an evidence cutoff date before collecting outcome data.
3. **Capture** only point-in-time sources available on or before that cutoff.
4. **Score** the five pillars, Gate status, variant view, payer economics, liquidation waterfall and conviction rules.
5. **Seal** the snapshot by commit SHA.
6. **Wait** until the predeclared reveal date.
7. **Reveal** outcome data and classify false positive, false negative, correct pass, correct reject or unresolved.
8. **Attribute** failure to data, model, valuation, timing, catalyst, capital structure, governance or implementation.

## Source rules

Allowed before reveal:

- filings, earnings releases and investor materials published by the cutoff;
- contemporaneous market prices, market capitalization and debt data;
- contemporaneous analyst expectations or clearly timestamped market consensus;
- contemporaneous regulatory and legal disclosures;
- archived news published by the cutoff.

Prohibited before reveal:

- later restatements not known at the cutoff;
- later management explanations;
- later transaction prices or asset-sale values;
- later bankruptcy, acquisition or regulatory outcomes;
- later price charts used to influence the original Gate or score.

## Required snapshot fields

- case ID, ticker, path and cutoff date;
- source list with publication dates;
- current price and market capitalization at cutoff;
- capital structure and maturity schedule at cutoff;
- five pillar Gate and score;
- market consensus and variant view;
- value-capture mechanism;
- payer economics where relevant;
- liquidation model and catalyst where relevant;
- metric-based reassessment and exit rules;
- maximum waiting period and reveal date;
- model version and git commit SHA.

## Outcome labels

- `CORRECT_PASS`
- `CORRECT_REJECT`
- `FALSE_POSITIVE`
- `FALSE_NEGATIVE`
- `TIMING_ERROR`
- `UNRESOLVED`

A profitable stock is not automatically a correct pass, and a falling stock is not automatically a false positive. Outcome classification must compare the original thesis facts and value-capture mechanism against what actually happened.

## Anti-cheating controls

- Result fields remain absent until reveal.
- The original snapshot is never overwritten; corrections require a new version.
- Case selection rationale is recorded before the outcome is inspected.
- At least half of the initial registry remains unresolved.
- Known successes and known failures cannot dominate the sample.
- Score thresholds cannot be changed mid-sample without preserving results under the old version.

## Promotion gate before UI or IDI integration

The engine is not eligible to influence IDI until:

- at least 12 cases are assigned and sealed;
- at least four opportunity paths are represented;
- at least four cases have reached reveal;
- at least two revealed cases are failures or rejects;
- all false positives and false negatives have written attribution;
- no critical schema or contract test is failing.
