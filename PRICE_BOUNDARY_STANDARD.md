# ENERGREX Price Temperature Band Standard

This standard governs the price-temperature-band module in the single-stock
valuation report. The module converts valuation-score sensitivity into a
readable cold-to-hot price map.

## Required Output

Every single-stock valuation report must show price boundaries when the needed
inputs are available:

- current price
- watch-zone upper boundary: highest price where Valuation Score is at least 60
- suitable-zone upper boundary: highest price where Valuation Score is at least 75
- low-price-zone upper boundary: highest price where Valuation Score is at least 80
- sensitivity table showing score changes under price shocks
- visual price-temperature chart showing three boundary lines, four zones, and
  one current-price line

If the module cannot calculate a boundary, it must show the reason. Do not
invent a price.

## Calculation Standard

- Boundaries must be calculated by changing price and recomputing price-driven
  valuation ratios with the same scoring engine used by the report.
- Single-stock reports must use a wide simulated price grid, normally 5%-400%
  of current price, so expensive stocks can still display the lower valuation
  bands when those bands are reachable.
- Price-driven fields include current price, forward P/E, PEG, EV/Sales,
  EV/EBITDA, and FCF yield when denominators are available.
- Non-price fundamentals must remain unchanged in the scenario simulation.
- Boundary labels are decision boundaries, not automatic trade instructions.

## Price-Temperature Chart Standard

The chart must be derived from a simulated price grid using the same scoring
engine. Labels map to valuation-score bands:

- Valuation Score >= 80: low-price zone
- 75 <= Valuation Score < 80: suitable zone
- 60 <= Valuation Score < 75: watch zone
- Valuation Score < 60: high-price zone

Visual implementation must follow `design_spec.dm` / `DESIGN_SPEC.md` tokens:

- background: `#0b1f35` / `#102b49`
- low-price zone: `#3ee8bd`
- suitable zone: `#56d9ff`
- watch zone: `#ffd166`
- high-price zone: `#ff6077`
- text: `#eef7ff`, muted labels: `#b5c7dc` / `#8ea8c3`
- borders: `rgba(142,190,235,0.22)`
- spacing must use the 8pt rhythm where possible

Do not introduce colors outside the design spec merely to distinguish bands.
Use opacity, labels, and boundary lines instead.

Chart readability rules:

- write zone labels inside the four bands when there is room;
- plot area should show only color bands, the valuation-score curve, boundary
  guide lines, and the current-price marker;
- the visible x-axis should focus on current price and reachable boundary prices,
  while the calculation grid may remain wider for boundary discovery.
- the rightmost line among the three boundary lines and current-price line should
  sit at about 85% of plot width when possible.

Do not label a zone as a guaranteed buy. If informal wording such as "no-brainer
dip-buy" is used in private notes, public reports should use "low-price zone"
instead.

Temperature semantics:

- colder zones mean valuation score improves as price falls;
- warmer zones mean valuation pressure rises as price increases;
- the current-price marker must be visually distinct from simulated boundary
  markers;
- the report should show boundary cards for current price, low-price-zone upper
  boundary, suitable-zone upper boundary, watch-zone upper boundary, and current
  valuation score.

## Required Methodology Disclosure

Every report that displays price zones must explain how the scores are derived:

- price zones are scenario simulations, not fortune-telling;
- the model holds non-price fundamentals constant;
- each simulated price recomputes Forward P/E, PEG, EV/Sales, EV/EBITDA, and
  FCF yield when denominators are available;
- the same scoring engine then recomputes Valuation Score, with Final Score kept
  only as context;
- zones are invalidated or must be refreshed when growth, margins, interest
  rates, share count, financial statement data, or market risk appetite changes;
- unless predictive validation gates pass, the report must state that the
  boundaries are not proven by backtested win rate.

## Validation Standard

The module is not considered predictively validated unless all gates pass:

- at least 100 point-in-time snapshots
- at least 5 tickers
- at least 252 calendar days of snapshot history
- at least 30 out-of-sample observations after chronological split
- forward return labels for at least one horizon
- no look-ahead data
- monotonicity check across boundary bands

Before these gates pass, reports must label the module:

```text
ENGINEERING_ONLY / INSUFFICIENT_REAL_HISTORY
```

## Backtest Metrics

Backtests should report:

- sample count by boundary band
- average forward return by band
- median forward return by band
- hit rate by band
- max drawdown proxy by band when available
- monotonicity of returns from weaker to stronger boundary bands
- chronological in-sample / out-of-sample split

## Change Control

Any change to scoring thresholds, valuation ratio recalculation, or rating
bands must rerun the price-boundary standard check and backtest.
