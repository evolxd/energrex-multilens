# AI Valuation System Architecture Review

Date: 2026-06-17

## Executive View

This project extends the original ENERGREX risk-monitoring idea into an interactive AI-growth stock valuation and account-monitoring application. Its strongest parts are the scoring model, the audit trail around scores, and the attempt to connect valuation, option exposure, and account risk in one workflow.

The main weakness is not the quant idea. It is the architecture: too much production logic, UI rendering, broker scraping, database mutation, scheduled work, and quantitative calculation are concentrated in a few very large files. That makes the system fast to prototype but fragile to maintain.

## Compared With ENERGREX

ENERGREX is a local-first options portfolio risk monitor. It is script-driven: import broker files, refresh market data, run checks, generate static dashboards and reports. It has a conservative operating model and avoids order placement.

This AI valuation system is app-driven. It uses Streamlit pages, a FastAPI backend, SQLAlchemy models, SQLite/Postgres-style database access, scheduled refreshes, live market data, SEC/EDGAR enrichment, and browser automation for Firstrade account monitoring.

In short:

- ENERGREX is better as a daily risk-control pipeline.
- AI Valuation is better as an exploratory interactive workstation.
- ENERGREX has cleaner operational boundaries.
- AI Valuation has broader ambition but more mixed concerns.

## Current Logical Flow

1. Stock universe starts from mock/static baseline data and user overrides.
2. Live data enriches selected fields through yfinance, Polygon, MarketData.app, and SEC/EDGAR where available.
3. The scoring engine calculates valuation, growth, quality, AI exposure, expectation gap, and risk penalty.
4. Streamlit renders leaderboard, detail, comparison, audit, options analysis, and account-monitoring pages.
5. Account-monitoring logic imports/scrapes broker data into SQLite tables.
6. Risk modules derive option positions, cost basis, realized trades, Greeks, IV regime, event risk, and hedge suggestions.
7. FastAPI exposes cache, score, override, and health endpoints, but the local Streamlit app still owns much of the product logic.

## Professional Assessment

### Strengths

- The scoring dimensions are financially coherent for AI-growth equities.
- The formula documentation is unusually strong for an MVP.
- The audit output is useful because it explains raw fields, normalized scores, weights, and final score composition.
- The system recognizes data-confidence issues instead of pretending every field is equally reliable.
- The account-monitoring layer tries to reuse the ENERGREX risk discipline around drawdown, options cost, event calendar, Greeks, and hedge pressure.

### Weaknesses

- `account_monitor.py` is a monolith and currently mixes database schema, imports, broker scraping, quantitative risk, Streamlit UI, scheduling, and recommendations.
- There are two scoring engines, `scoring_engine.py` and `quant_engine.py`, which increases the risk of divergent rankings.
- The project contains live secrets/state artifacts such as `.env`, SQLite databases, browser profiles, screenshots, logs, and generated reports at repository root.
- FastAPI and Streamlit overlap instead of having a clean ownership split.
- Many broad `except Exception` blocks silently continue, which is dangerous for account/risk calculations.
- The database strategy is inconsistent: the backend is written around `DATABASE_URL`, while the account monitor uses direct SQLite access.
- Several core data fields remain manual or mock-like, especially AI exposure, expectation gap, NRR, valuation risk, and some option/account reconstructions.

## Claude Level Evaluation

Claude's work is strong at rapid product assembly and domain-aware prototype design. It understood the business direction, produced a usable interface, built a scoring framework, and added auditability. That is above-average agent work.

From a professional software-engineering perspective, the level is closer to "capable prototype engineer" than "senior production architect." The system can demonstrate an idea, but it needs boundary cleanup, data governance, tests, and operational hardening before it should be treated as a trusted trading/risk platform.

## Optimization Plan

### P0: Protect The Project

- Keep `.env`, databases, logs, screenshots, browser profiles, broker exports, generated validation reports, and pycache out of version control.
- Keep account data local-only unless explicitly exported.
- Make data freshness and confidence visible wherever a score or risk signal is shown.

### P1: Split The Monolith

Recommended module split:

- `account/db.py`: connection, migrations, table creation.
- `account/importers.py`: CSV/XLSX import and normalization.
- `account/broker_sync.py`: browser/CDP automation only.
- `account/options.py`: OCC parsing, option price refresh, FIFO matching.
- `account/risk.py`: NAV, beta, Greeks, hedge pressure, scenarios.
- `account/recommendations.py`: exits, alerts, sell-call triggers, hedge plans.
- `pages/account_monitor.py`: Streamlit rendering only.

### P2: Unify Scoring

Choose one scoring engine as canonical. Keep the other only as a compatibility/audit adapter. Every displayed score should come from the same engine and include:

- data timestamp
- field source
- confidence level
- manual override marker
- score version

### P3: Harden Risk Calculations

- Replace silent exception swallowing with structured warnings written into a visible `data_quality_issues` table or panel.
- Add deterministic unit tests for option parsing, FIFO matching, Greeks, score normalization, and hedge-plan calculations.
- Store snapshots before and after imports so account reconstruction can be audited.

### P4: Clarify App Boundaries

Use Streamlit as the UI layer and FastAPI as the service/data layer, or remove FastAPI until it is truly needed. The mixed model currently adds complexity without fully separating responsibilities.

## Immediate Changes Made

- Added `.gitignore` to protect secrets, databases, logs, screenshots, browser profile data, generated reports, and local cache files.
- Added this architecture review as a stable map for the next refactor pass.
- Added `account/db.py` as the account-monitor SQLite boundary for paths, connection creation, and idempotent table initialization.
- Added `account/options.py` for shared OCC option-symbol parsing.
- Updated `account_monitor.py` to route runtime database access and OCC parsing through the new account package while keeping legacy call sites stable.
- Added `account/importers.py` for side-effect-free broker import parsing helpers: money parsing, date parsing, and CSV type detection.
- Updated XLSX import parsing to reuse the shared money parser instead of maintaining a duplicate local parser.
- Moved the runtime CSV import flow into `account/importers.py` with callback-based persistence, so broker CSV parsing no longer depends directly on Streamlit or account-monitor internals.
- Added normalized row parsers for positions and transactions, plus English/Chinese CSV type detection tests during verification.
- Added `account/repository.py` for account balance, daily NAV, positions, and transactions persistence/read helpers.
- Updated `account_monitor.py` to route those repository calls through the new module while preserving existing function names for compatibility.
- Corrected `account/options_repository.py::derive_open_options` to read the authoritative `options_positions` table instead of reconstructing positions from potentially incomplete transactions.
- Standardized derived option position `direction` output to position side (`long`/`short`), keeping option type (`call`/`put`) derived from the OCC symbol.
- Added option market snapshot and unit-cost update helpers to `account/options_repository.py`, moving quote-refresh and unit-cost SQL writes out of `account_monitor.py` while preserving existing calculations.
- Added `replace_realized_trades_and_fifo_costs()` to `account/options_repository.py`, moving FIFO realized-trade replacement and open-position cost/P&L updates out of `account_monitor.py` in one transactional repository call.
- Added `account/fifo.py` for side-effect-free FIFO matching. `account_monitor.py` now reads transaction rows, calls `calculate_fifo_matches()`, and persists the result through `options_repository`.
- Removed the legacy in-file FIFO implementation from `account_monitor.py`; the file now keeps only the thin FIFO wrapper that delegates calculation to `account/fifo.py`.
- Added `account/marketdata.py` as the first dedicated market-data boundary for MarketData.app option quotes, yfinance underlying prices, spot prices, VIX snapshots, and near-30 DTE ATM IV lookup.
- Updated `account_monitor.py` to keep Streamlit cache wrappers while delegating those market-data reads to `account/marketdata.py`, preserving existing function names and call sites.
- Added `account/risk.py` with the shared Black-Scholes Greeks calculation and risk constants. `account_monitor.py` now routes `_bs_greeks()` through this module, giving Greeks dashboards, hedge plans, and spread simulations one testable formula implementation.
- Added `calculate_option_position_greeks()` to `account/risk.py` and updated `_compute_portfolio_greeks()` to delegate per-position Greeks math to it. The Streamlit layer still owns status labels, database reads/writes, and trigger composition, but the position-level calculation is now deterministic and independently testable.
- Added `summarize_portfolio_greeks()` to `account/risk.py` for portfolio totals, average delta, contract count, by-underlying delta exposure, top long/short contributors, and IV source counts. `_compute_portfolio_greeks()` now delegates both position math and portfolio aggregation to the risk module.
- Added portfolio Greeks snapshot persistence helpers to `account/options_repository.py`. `_compute_portfolio_greeks()` now loads the previous snapshot and saves the current snapshot through repository functions instead of embedding SQL for `portfolio_greeks_history`.
- Added `delta_drift_trigger()` to `account/risk.py` and routed the Greeks dashboard drift check through it. The UI still formats the alert message, but the trigger decision and drift metrics are now independently testable.
- Added `vix_spike_trigger()` to `account/risk.py` and routed the Greeks dashboard VIX spike check through it. The trigger threshold is now centralized while the Streamlit layer still owns localized alert copy.
- Fixed the option market-value sign bug in quote refresh: short option positions now keep negative signed market value through `option_market_value()` instead of using `abs(quantity)`.
- Clarified OCC parsing semantics by adding explicit `option_type` and `call_put` fields. Legacy `direction` remains only for compatibility, while derived open positions now expose position side (`long`/`short`) separately from option type (`call`/`put`).
- Added a no-dependency `unittest` suite under `tests/` covering FIFO matching, OCC parsing, signed option market value, derived open-option semantics, Black-Scholes Greeks, position Greeks, portfolio Greeks aggregation, Delta drift triggers, and VIX spike triggers. Current suite: 12 tests, all passing.
- Removed the legacy in-file `_bs_greeks()` implementation from `account_monitor.py`; `_bs_greeks` now resolves only to `account.risk.bs_greeks`. Tests remain passing after deletion.
- Removed legacy in-file market-data implementations from `account_monitor.py`; the file now keeps only Streamlit cache wrappers that delegate to `account/marketdata.py`. Tests remain passing after deletion.
