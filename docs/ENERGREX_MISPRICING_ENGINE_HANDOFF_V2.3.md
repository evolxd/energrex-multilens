# ENERGREX MISPRICING ENGINE — HANDOFF V2.3

## 1. 模块定位

新模块名称：**Energrex Mispricing & Special Situations Engine**。

职责：

- 发现市场误价、隐藏资产、回购时间机器、临时困境和清算套利候选；
- 执行硬门控；
- 形成一句话变体认知与可证伪协议；
- 将通过的标的交给现有五维、估值、IDI、组合与期权模块。

禁止事项：

- 不作为第六维；
- 不保留第二个最终投资总分；
- 不重复计算估值；
- 不因符合多条机会路径重复加分；
- Gate 失败不可被其他高分覆盖。

## 2. 目标数据流

```text
股票池 / 用户输入
  → Mispricing path router
  → P1–P5 Gate + research-priority score
  → PASS / NEEDS_EVIDENCE / FAIL
  → existing five-dimension engine
  → Valuation Engine + Reverse DCF
  → IDI final decision
  → portfolio / options expression router
  → KPI + Kill Thesis monitoring
  → exit attribution and case library
```

## 3. 已开发第一阶段

分支：`feature/mispricing-engine-v2-3`

新增：

- `scoring/mispricing_engine.py`
- `tests/test_mispricing_engine.py`
- `docs/MISPRICED_COMPOUNDER_HIDDEN_ASSET_SKILL_V2.3.md`
- 本文件

第一阶段提供领域模型、Gate 规则、清算价值计算、付款方经济学警告和价格/事实反对分类。尚未接入 Streamlit UI、CSV pipeline 或现有 IDI。

## 4. 核心接口

### MispricingAssessment

输入：ticker、主路径、变体认知、五大支柱、付款方经济学、清算价值、坚持与证伪协议。

输出：

- `overall_gate()`
- `discovery_score()`：只用于研究优先级；Gate FAIL 时返回 `None`
- `quick_reject_reasons()`

### PayerEconomics

负责使用者与付款方分离、定价权、支付刚性、付款方信用与集中度、政策依赖、成本转嫁、回款周期、坏账风险和合理杠杆判断。

### LiquidationInputs

负责普通股价值瀑布：

- `common_equity_recovery()`
- `discount_to_recovery()`
- `annualized_return_if_realized()`

### ConvictionProtocol

负责区分：

- `HOLD_DISCIPLINE`
- `PRICE_OPPOSITION_REVIEW_FACTS`
- `REASSESS`
- `EXIT`

## 5. 建议持久化 Schema

```yaml
mispricing:
  version: "2.3"
  primary_path: null
  secondary_paths: []
  market_consensus: null
  implied_expectation: null
  variant_view: null
  key_evidence: []
  evidence_confidence: null
  next_validation_date: null

payer_economics:
  end_user: null
  economic_payers: []
  price_setter: null
  payment_necessity: null
  payer_credit_quality: null
  payer_concentration: null
  policy_dependency: null
  cost_pass_through_ability: null
  collection_cycle_days: null
  bad_debt_risk: null
  payer_mix_trend: null
  leverage_support_assessment: null

liquidation_value:
  cash_and_equivalents: 0
  marketable_securities: 0
  receivables: 0
  receivables_recovery_rate: 0
  inventory: 0
  inventory_recovery_rate: 0
  property_and_equipment_sale_value: 0
  subsidiary_and_stake_value: 0
  hidden_asset_value: 0
  total_debt: 0
  lease_liabilities: 0
  pension_and_legal_claims: 0
  preferred_and_minority_claims: 0
  tax_and_transaction_cost: 0
  cash_burn_until_realization: 0
  market_cap: 0
  timeline_months: 0

conviction_protocol:
  thesis_core_facts: []
  allowed_price_drawdown_pct: null
  reassessment_triggers: []
  mandatory_exit_triggers: []
  add_on_weakness_conditions: []
  prohibited_actions: []
  maximum_waiting_months: null

monitoring:
  - pillar_id: null
    kpi: null
    baseline: null
    warning_threshold: null
    fail_threshold: null
    action_on_warning: null
    action_on_fail: null
```

## 6. 与现有模块职责边界

| 模块 | 新引擎职责 | 现有系统职责 |
|---|---|---|
| 五维评分 | 仅前置 Gate 与问题发现 | 正式质量评分 |
| Valuation Engine | 传递变体假设、熊基牛条件 | DCF、Reverse DCF、倍数、NAV/SOTP |
| IDI | 传递 Gate、误价置信度 | 唯一最终决策分数 |
| Portfolio/Kelly | 传递 Kill Thesis、期限、最大损失 | 仓位与组合风险预算 |
| Options | 传递催化剂窗口和价格路径 | 判断股票或有限风险价差表达 |

## 7. 快速拒绝逻辑

以下任一成立直接 FAIL：

- 生意与付款路径无法在 150 字内解释；
- 36 个月内高概率流动性/再融资危机；
- 隐藏资产或清算价值主要归债权人、优先股或少数股东；
- 价值兑现前持续亏损会吞噬资产；
- 回购主要抵消 SBC，净稀释股数不下降；
- 投资逻辑只剩大股东买入、做空比例或历史高价；
- 关键权利、牌照、频谱或资产权属不可验证；
- 催化剂时间与期权到期或资金期限不匹配。

## 8. 示例映射

### DaVita

主路径：现金流回购时间机器。

付款方机制：患者使用，政府医保与商业保险支付。高信用付款方和刚性需求提高回款稳定性，但报销费率、保险结构与劳动力成本决定真实杠杆上限。监控付款方结构、报销/成本差、利息覆盖、净回购率。

### FUTU

主路径：伟大公司短期困境 / 隐含预期错位。

付款方分散但收入受交易量、融资需求、利率和监管影响，不能套用公用事业式高杠杆逻辑。监控 funded accounts、客户资产、海外客户质量、监管范围、净股数变化。

### 清算价值候选

先计算保守回收，再扣全部优先索取权、兑现成本和现金消耗。必须给出兑现时间和年化收益；没有释放价值的控制权或催化剂时，不因账面折价自动通过。

## 9. 下一阶段实施

1. 新增 JSON 持久化层与示例数据；
2. 在单股详情页加入“误价发现”只读区块，不改现有五维总分；
3. 在 refresh pipeline 增加可选 mispricing enrichment，不污染 `results_validated.csv` 的现有字段；
4. 将 Gate 结果传入 IDI 决策层作为乘数/否决状态，而非加法分数；
5. 增加 FUTU、DVA、VSAT 与至少 5 个失败反例 fixture；
6. 执行 pytest、页面 smoke test、真实浏览器打印测试；
7. GitHub push 后按 `DEPLOYMENT_SYNC_STANDARD.md` 确认实际部署。

## 10. 验收标准

- 五大支柱必须且只能出现一次；
- FAIL 时研究优先级分为空；
- 清算价值必须穿透至普通股；
- 价格下跌不得自动触发 EXIT，事实证伪必须能触发 EXIT；
- 付款方审计必须同时显示稳定性与压价/政策风险；
- 页面不得显示第二个“最终投资总分”；
- 现有评分、Kelly、期权和打印测试不得回归；
- 未部署到实际服务器前不得标记完成。

## 11. 迁移与回滚

V2.1/V2.2 数据迁移时保留原五大支柱与 IDI，不重算历史分数。新增字段缺失时状态为 `NEEDS_EVIDENCE`，不得用默认值伪装已验证。

回滚方式：新引擎保持独立模块与 feature flag；关闭 UI 调用即可恢复旧路径，不删除已有五维或估值字段。
