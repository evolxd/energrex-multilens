# Codex Project Blueprint

## Goal

Turn the current Claude-built stock and options quant prototype into a trusted, maintainable, and testable investment workstation.

The target is not a full rewrite. The strategy is controlled refactoring: keep the app usable, isolate core logic into modules, fix known behavioral bugs, and add tests before removing legacy code.

## Current Position

The system already has useful product value:

- stock valuation and ranking workflow
- account monitor UI
- Firstrade position and transaction imports
- option position tracking
- option quote refresh
- FIFO realized P&L
- portfolio Greeks
- risk triggers
- QQQ hedge planning
- architecture review documentation

The main weakness is that much of the runtime still flows through `account_monitor.py`, which mixes UI, database access, market data, risk math, broker parsing, triggers, and recommendations.

## Engineering Principle

The project should move from "large working script" to "small testable modules."

Every important number should eventually answer four questions:

- Where did the data come from?
- When was it last updated?
- What formula or rule produced it?
- Can we test that rule without opening Streamlit?

## Current Module Map

### `account/db.py`

Owns SQLite paths, connection creation, and idempotent table initialization.

Long-term role: database boundary and migrations.

### `account/options.py`

Owns OCC option-symbol parsing and option-specific helpers.

Current important fixes:

- `parse_occ()` now exposes `option_type` and `call_put`.
- legacy `direction` remains only for compatibility.
- `option_market_value()` returns signed market value, so short positions remain negative.

Long-term role: option symbol, type, side, and contract math primitives.

### `account/importers.py`

Owns CSV parsing and normalization for broker exports.

Long-term role: importer boundary, with no Streamlit dependency.

### `account/repository.py`

Owns account balance, NAV, stock positions, and transaction persistence/read helpers.

Long-term role: account data access boundary.

### `account/options_repository.py`

Owns option positions, option market snapshots, FIFO cost persistence, realized trades, and portfolio Greeks snapshots.

Current important fixes:

- `derive_open_options()` reads authoritative `options_positions`, not transactions.
- position side (`long`/`short`) is separate from option type (`call`/`put`).

Long-term role: option data access boundary.

### `account/fifo.py`

Owns pure FIFO matching.

It has no database side effects. It accepts transaction rows and returns realized trades, FIFO costs, and summary metrics.

Long-term role: deterministic realized P&L engine.

### `account/marketdata.py`

Owns MarketData.app and yfinance reads:

- option quotes
- underlying prices
- spot price batch
- VIX snapshot
- ATM IV lookup

Long-term role: market-data adapter layer, with failures made visible rather than silently swallowed.

### `account/risk.py`

Owns risk math and trigger rules:

- Black-Scholes Greeks
- position-level Greeks
- portfolio Greeks aggregation
- Delta drift trigger
- VIX spike trigger

Long-term role: portfolio risk engine.

### `tests/`

Current no-dependency `unittest` suite:

- `test_fifo.py`
- `test_options.py`
- `test_risk.py`

Current status: 12 tests passing.

Long-term role: fast guardrail before every refactor.

## Current Known Issues

### 1. `account_monitor.py` Still Contains Zombie Code

Several legacy implementations remain in the file and are later overridden by imports. Examples include old DB helpers, old repository helpers, and old risk functions.

This is not ideal, but removal should be staged because helper scripts such as `_run_sync.py` may depend on name ordering or AST slicing.

Plan:

1. Add tests first.
2. Identify duplicate functions one group at a time.
3. Confirm no helper script depends on the old body.
4. Delete in small batches.
5. Run tests after each deletion.

### 2. Imports Are Scattered

Imports currently appear throughout the file because they were introduced as safe overrides after legacy definitions.

This was useful for low-risk migration, but it is not the desired final style.

Plan:

1. Once zombie code is removed, move stable module imports toward the top.
2. Keep local imports only where they intentionally avoid heavy optional dependencies.

### 3. `direction` Naming Is Historically Ambiguous

Old code used `direction` to mean option type (`Call`/`Put`) in some places and position side (`long`/`short`) in others.

Current mitigation:

- use `option_type` or `call_put` for call/put
- use `direction` only for position side where possible
- keep legacy `direction` from `parse_occ()` only for compatibility

Long-term plan:

- rename persisted position side to `position_side` in future schema migration
- keep `direction` as a deprecated alias during transition

### 4. Data Quality Is Not Visible Enough

Many broad exception handlers still silently continue.

Plan:

- collect parse and quote failures into a visible data-quality panel
- distinguish stale, missing, manual, and live data
- show data timestamps on score and risk outputs

## Testing Strategy

The immediate test target is not broad UI coverage. It is fast deterministic protection for core calculations.

Current command:

```powershell
python -m unittest discover -s tests -v
```

Priority tests:

- OCC parsing
- signed option market value
- derive-open-options semantics
- FIFO matching
- Black-Scholes Greeks
- position Greeks
- portfolio Greeks aggregation
- Delta drift trigger
- VIX spike trigger

Next tests to add:

- spread pairing logic
- QQQ hedge plan math
- risk snapshot stress calculation
- importer CSV type detection and normalization
- repository temp-DB round trips

## Refactor Roadmap

### Phase 1: Stabilize Core Logic

Status: mostly underway.

Completed:

- options parsing helpers
- FIFO extraction
- risk math extraction
- market data extraction
- signed market-value bug fix
- initial tests

Next:

- add spread pairing tests
- add risk snapshot tests
- add repository temp-DB tests

### Phase 2: Remove Zombie Code

Goal: make `account_monitor.py` shorter, not just layered by overrides.

Candidate deletion groups:

- old DB helpers now owned by `account/db.py`
- old account repository helpers now owned by `account/repository.py`
- old option repository helpers now owned by `account/options_repository.py`
- old Black-Scholes and market-data helpers now owned by `account/risk.py` and `account/marketdata.py`

Rule: delete only after tests cover the behavior and helper scripts still run.

### Phase 3: Normalize Imports

Move stable imports to the top of `account_monitor.py` after duplicate code is removed.

Keep local imports only for:

- optional heavy dependencies
- Streamlit-only flows
- rare fallback integrations

### Phase 4: Split UI From Computation

Target shape:

- `account_monitor.py`: Streamlit page orchestration
- `account/risk.py`: calculations and triggers
- `account/recommendations.py`: exit, hedge, sell-call, and alert logic
- `account/broker_sync.py`: browser/CDP broker sync
- `account/marketdata.py`: quote providers
- `account/*_repository.py`: database boundaries

### Phase 5: Improve Data Governance

Add explicit metadata:

- source
- timestamp
- freshness
- confidence
- manual override flag
- data-quality issue list

This matters because a risk dashboard is only useful if stale or inferred values are visible.

## Professional Evaluation Of Current Direction

The architecture direction is now correct:

- domain logic is moving out of UI
- critical calculations are testable
- option side/type semantics are being clarified
- known bugs are being converted into tests

The main remaining weakness is cleanup discipline:

- old code remains too long
- imports are still scattered
- some naming still carries legacy ambiguity

The next major quality jump will not come from adding features. It will come from deleting replaced code safely.

## Immediate Next Step

Add tests for spread pairing and QQQ hedge plan math, then start deleting the first batch of zombie code from `account_monitor.py`.

Recommended first deletion target:

1. remove legacy `_bs_greeks()` after confirming all callers use `account.risk.bs_greeks` - done
2. run tests - done, 12 passing
3. remove legacy market-data functions that are now wrapped by `account/marketdata.py` - done
4. run tests again - done, 12 passing

The goal is to make the file smaller with every pass, not just more abstract.

---

## 股票多维智库分析 Prompt 模板

### 功能位置

`app.py` — 单股详情页（`page == "🔍 单股详情"`），Damodaran 分析区块之后、页面 3 注释之前（约第 1108 行）。

### 功能描述

用户选定股票后，系统从 `results_validated.csv` 读取该股实际评分数据，自动填入两个 Prompt 模板，用户直接复制发给 Claude 即可获得多框架交叉分析。

### 两个版本

| 版本 | 适用场景 | 字数 |
|------|---------|------|
| 完整版（📋） | 深度研究 / 买入决策 | ~500字 |
| 快速版（⚡） | 日常复盘 / 3分钟 | ~100字 |

### 自动填入的数据字段

| 字段 | 来源列 |
|------|-------|
| 代码 | ticker |
| 公司名 | company |
| 板块 | cat.value（CompanyCategory） |
| 估值分 | valuation_score |
| 成长分 | growth_score |
| 质量分 | quality_score |
| AI暴露分 | ai_exposure_score |
| 预期差分 | expectation_gap_score |
| 加权合计 | raw_score |
| 风险扣分 | risk_penalty |
| Final Score | final_score |
| 各维度权重 | WEIGHT_CONFIG[cat]（valuation/growth/quality/ai_exposure/expectation_gap） |

### 支持的 8 个分析框架

1. **Damodaran** — 内在价值 vs 市价，成长假设合理性
2. **Cathie Wood** — 指数级颠覆，5年目标价，TAM
3. **Jensen Huang** — AI算力需求，护城河，技术路线
4. **Howard Marks** — 风险/不确定性，市场周期位置
5. **Satya Nadella** — 云+AI平台化，生态整合，商业模式
6. **Mary Meeker** — 互联网/平台趋势，用户增长指标
7. **Peter Thiel** — 垄断优势，竞争护城河，秘密
8. **Charlie Munger Skill**（配合使用） — 心理偏差检验

### UI 实现

```python
_pv = st.radio("Prompt 版本", ["📋 完整版...", "⚡ 快速版..."], horizontal=True, key="prompt_ver")
_prompt_text = _prompt_full if "完整" in _pv else _prompt_quick
st.text_area("复制以下 Prompt 发给 Claude：", value=_prompt_text, height=320/180, key="prompt_output")
```

### 维护说明

- Prompt 文案在 `app.py` 单股详情页的 `_prompt_full` / `_prompt_quick` f-string 中修改
- 新增评分维度时同步更新两个 Prompt 的 `## 当前评分数据` 表格
- 框架列表可在 `st.caption` 底部提示行追加
