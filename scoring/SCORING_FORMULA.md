# AI成长股估值评分公式文档

**版本**：v2.1 · 2026-06-10  
**适用文件**：`scoring_engine.py` · `drift_detector.py`

---

## 总体架构

```
final_score = Σ(子分 × 权重) − risk_penalty
            ∈ [0, 100]

子分 × 5：估值 / 成长 / 质量 / AI暴露 / 预期差
扣分 × 1：风险惩罚（上限 20 分）
```

所有子分使用**连续函数**（`linear_clamp` / `inverse_clamp`），无阶梯跳变。

---

## 评分工具函数

### linear_clamp

```
score = (value − worst) / (best − worst) × 100
result = clamp(score, 0, 100)
```

higher-is-better 场景：`value=best → 100分`，`value=worst → 0分`。

### inverse_clamp

lower-is-better 场景（如 PEG、EV/EBITDA），等价于：

```
score = (worst − value) / (worst − best) × 100
result = clamp(score, 0, 100)
```

---

## AI角色路由与顶层权重

所有公司均使用同一组可比较的顶层权重：估值 20%、成长 25%、质量 15%、AI维度 20%、预期差 10%、动量 10%。

- **AI核心型**：AI维度使用原始 AI 曝露评分。
- **AI赋能型、传统优质型、AI待验证型**：AI维度使用中性基线 50 分，避免把“AI收入占比低”误当作企业质量差；只有已验证的 AI 曝露可额外获得 0-5 分加速器。
- 不得把 AI 维度权重转移至估值或质量。该做法会系统性抬高高质量、低 AI 曝露公司的排名，除非未来经样本外回测证明有效。

原始 AI 信号、AI角色、加速器和最终评分必须同时保留在审计输出中。

### 估值完整性闸门

综合分衡量企业候选质量，不能单独推导出当前价格可执行。若同时满足远期 PE 不低于 40、EV/Sales 不低于 12、FCF 收益率不高于 2%，标记为“高估值待验证”：保留原始分数和质量排序，但不进入行动候选排序。该闸门是三项独立估值锚的一致性检查，不是对某个行业或公司的手动压分。

| 类型 | Ticker | 估值 | 成长 | 质量 | AI暴露 | 预期差 |
|------|--------|------|------|------|--------|--------|
| AI芯片 | NVDA AVGO MRVL | 0.25 | 0.25 | 0.20 | 0.20 | 0.10 |
| AI软件/SaaS | PLTR SNOW NOW | 0.20 | 0.30 | 0.25 | 0.15 | 0.10 |
| 网络安全 | PANW CRWD FTNT | 0.20 | 0.25 | 0.30 | 0.10 | 0.15 |
| 半导体设备 | ONTO | 0.30 | 0.20 | 0.25 | 0.15 | 0.10 |

各行权重之和 = 1.00（代码中有 assert 验证）。

---

## ① 估值分（valuation_score）

### 五个指标及设计原理

| 指标 | 公式 | 为什么独立保留 |
|------|------|--------------|
| PEG | PE ÷ 增长率 | Lynch基准，综合性最强 |
| EV/EBITDA | — | 芯片/设备首要；SaaS高SBC失真故权重低 |
| ERG | EV/Rev ÷ 增长率(%) | Meritech Capital SaaS/AI核心指标 |
| Forward PE | — | GS/MS/JPM均独立引用，PEG无法替代（口径不透明）|
| FCF Yield | FCF ÷ 市值 | 真实现金回报率，比E/P更准确 |

### ERG 单位修正（历史 bug，已修正）

```
# 错误（rev_growth=0.33 作为除数 → ERG=31.8，远超 worst=0.60）
erg_wrong = ev_sales / rev_growth          # 0.33 形式

# 正确（转换为百分点形式再除）
rev_g_pct = max(rev_growth * 100, 1.0)    # 33.0 形式
erg = ev_sales / rev_g_pct                # 例 19/85 = 0.224
```

### 各类型阈值与权重

**AI芯片**（权重合计 = 1.00）

| 指标 | best | worst | 权重 |
|------|------|-------|------|
| PEG | 0.5 | 2.5 | 0.25 |
| EV/EBITDA | 15 | 55 | 0.35 |
| ERG = EV/Rev ÷ RevG% | 0.15 | 0.80 | 0.15 |
| FCF Yield | 5% | 0% | 0.10 |
| Forward PE | 20 | 80 | 0.15 |

**AI软件/SaaS**（ERG 权重最高，反映 Meritech 定价逻辑）

| 指标 | best | worst | 权重 |
|------|------|-------|------|
| PEG | 0.5 | 2.5 | 0.15 |
| EV/EBITDA | 20 | 80 | 0.10 |
| ERG = EV/Rev ÷ RevG% | 0.10 | 0.60 | 0.50 |
| FCF Yield | 5% | 0% | 0.15 |
| Forward PE | 20 | 80 | 0.10 |

**网络安全**（EV/Revenue 直接使用，安全公司增长稳定、订阅质量高）

| 指标 | best | worst | 权重 |
|------|------|-------|------|
| PEG | 0.5 | 2.5 | 0.20 |
| EV/EBITDA | 20 | 80 | 0.15 |
| EV/Revenue（直接） | 5x | 25x | 0.40 |
| FCF Yield | 5% | 0% | 0.15 |
| Forward PE | 20 | 80 | 0.10 |

**半导体设备**（周期性强；EV/EBITDA 主导；EV/Revenue 直接使用）

| 指标 | best | worst | 权重 |
|------|------|-------|------|
| PEG | 0.5 | 2.5 | 0.25 |
| EV/EBITDA | 15 | 55 | 0.40 |
| EV/Revenue（直接） | 6x | 20x | 0.15 |
| FCF Yield | 5% | 0% | 0.10 |
| Forward PE | 20 | 80 | 0.10 |

> **机构锚点来源**：Peter Lynch PEG=1.0 / MS 2026芯片报告 / Meritech Capital 2026 ERG / GS/JPM Forward PE锚点

---

## ② 成长分（growth_score）

权重合计 = 1.00，各类型共用相同权重，仅阈值因类型不同：

| 指标 | 权重 | AI芯片阈值 | AI软件阈值 | 网安阈值 | 设备阈值 |
|------|------|-----------|-----------|---------|---------|
| 收入增长 YoY | 0.35 | 10%–70% | 10%–50% | ARR 10%–35% | -10%–30% |
| EPS增长 YoY | 0.20 | 0%–60%（通用） | ← | ← | ← |
| FCF增长 YoY | 0.15 | 0%–50%（通用） | ← | ← | ← |
| NTM收入增长预期 | 0.20 | 5%–50%（通用） | ← | ← | ← |
| 分析师上调动量30d | 0.10 | -50%–+50%（通用）| ← | ← | ← |

> 网络安全优先使用 `arr_growth_yoy`，缺失时回退到 `revenue_growth_yoy`。

---

## ③ 质量分（quality_score）

**Rule of 40 口径**：统一使用 FCF 口径（`revenue_growth_yoy + fcf_margin`），不使用 EBITDA。

```
rule_of_40 = (revenue_growth_yoy + fcf_margin) × 100   # 转换为百分点
```

各类型权重：

| 指标 | AI软件 | 网络安全 | AI芯片 | 设备 |
|------|--------|---------|--------|------|
| 毛利率 | 0.15 | 0.15 | 0.20 | 0.25 |
| FCF Margin | 0.20 | 0.25 | 0.30 | 0.25 |
| 营业利润率(Non-GAAP) | 0.05 | 0.05 | 0.05 | 0.05 |
| Rule of 40 | 0.25 | 0.20 | 0.20 | 0.15 |
| ROIC | 0.10 | 0.10 | 0.15 | 0.20 |
| Debt/Equity | 0.05 | 0.05 | 0.10 | 0.10 |
| 收入可预测性 | 0.05 | 0.05 | 0 | 0 |
| NRR净收入留存 | 0.15 | 0.15 | 0（中性50）| 0（中性50）|

NRR阈值：worst=90%，best=135%（SaaS Capital 2026基准）  
Rule of 40阈值：SaaS/网安 worst=20,best=70；芯片/设备 worst=10,best=50

> **机构锚点**：SaaS Capital / BVP Nasdaq Cloud Index / Meritech Capital 2026

---

## ④ AI暴露分（ai_exposure_score）

> ⚠️ **所有 AI 暴露字段为手动整理**，无法从 yfinance 自动获取，需每季财报后更新 mock_data.py。

**AI芯片**

| 字段 | worst | best | 权重 |
|------|-------|------|------|
| AI收入占比 | 10% | 85% | 0.30 |
| AI增长贡献占比 | 10% | 80% | 0.20 |
| 数据中心收入占比 | 25% | 85% | 0.35 |
| 先进封装暴露 | 5% | 60% | 0.15 |

**AI软件/SaaS**

| 字段 | worst | best | 权重 |
|------|-------|------|------|
| AI收入占比 | 10% | 85% | 0.30 |
| AI增长贡献占比 | 10% | 80% | 0.25 |
| AI平台化程度 | 10% | 70% | 0.30 |
| AI订单积压暴露 | 5% | 60% | 0.15 |

**网络安全**

| 字段 | worst | best | 权重 |
|------|-------|------|------|
| AI收入占比 | 10% | 85% | 0.25 |
| AI增长贡献占比 | 10% | 80% | 0.25 |
| AI利润占比 | 10% | 85% | 0.20 |
| 网安AI暴露 | 5% | 60% | 0.30 |

**半导体设备**

| 字段 | worst | best | 权重 |
|------|-------|------|------|
| AI收入占比 | 10% | 85% | 0.25 |
| AI增长贡献占比 | 10% | 80% | 0.30 |
| AI利润占比 | 10% | 85% | 0.15 |
| 先进封装暴露 | 10% | 70% | 0.30 |

---

## ⑤ 预期差分（expectation_gap_score）

| 指标 | worst | best | 权重 |
|------|-------|------|------|
| 实际收入 vs 预期 | -8% | +10% | 0.30 |
| 实际EPS vs 预期 | -10% | +15% | 0.25 |
| 指引 vs 市场预期 | -10% | +12% | 0.25 |
| 财报后次日涨跌幅 | -15% | +20% | 0.10 |
| 市场预期高低（反向）| 0 | 1 | 0.10 |

`market_expectation_score`：0=市场预期极低（好），1=市场预期极高（坏）。计算时取反：`mkt_score = linear_clamp(1 - mkt_expect, 0, 1)`。

---

## AI角色前置分类（评分路由层）

AI角色不是额外的第七个加权维度，而是决定维度权重的前置分类：

| AI角色 | 判断标准 | AI处理 |
|---|---|---|
| AI核心型 | AI收入/利润暴露≥30%，且属于AI核心业务类别 | AI维度权重20%，不另加奖励 |
| AI赋能型 | AI暴露10%–30%，或传统行业的较高AI暴露 | AI维度使用中性50分；AI加速器0–5分 |
| 传统优质型 | AI暴露<10% | AI维度使用中性50分；AI加速器0–3分 |
| AI待验证 | 缺少可用暴露证据 | AI维度使用中性50分，不加分，等待核验 |

AI暴露优先使用AI收入与AI利润暴露的平均值；缺失时才使用平台、网安、
数据中心或先进封装暴露代理。非AI核心公司不把移除的AI权重重新分配给质量、
估值或动量；这样可以避免高质量公司因权重迁移而被不合理推到榜首。低波动只
减少风险扣分，不作为质量加分。

---

## ⑥ 风险扣分（risk_penalty）

```
raw_penalty = beta_comp×0.25 + vol_comp×0.20 + val_comp×0.25
            + conc_comp×0.15 + dd_comp×0.15

risk_penalty = clamp(raw_penalty × 20, 0, 20)
```

每个 component ∈ [0, 1]（1=最高风险）：

实现约束：若复用返回“best=100 / worst=0”的质量标准化函数，风险强度必须取
`risk_component = 1 - safety_score / 100`。禁止把安全分直接当作风险强度；
2026-07 的回归测试要求每一项风险输入恶化时，`risk_penalty` 只能增加。

| 组件 | 计算方式 | worst | best |
|------|---------|-------|------|
| beta_comp | linear_clamp(beta, 0.5, 2.5) / 100 | beta=2.5 | beta=0.5 |
| vol_comp | linear_clamp(vol_30d, 20%, 90%) / 100 | vol=90% | vol=20% |
| val_comp | valuation_risk 直接使用（手动0~1） | 1.0 | 0.0 |
| conc_comp | (concentration_risk + liquidity_risk) / 2 | 1.0 | 0.0 |
| dd_comp | linear_clamp(max_dd_1y, 10%, 70%) / 100 | dd=70% | dd=10% |

最大扣分 = **20 分**。

---

## ⑦ 评分不确定性（score_uncertainty）

```
uncertainty = L字段数 × 1.5 + M字段数 × 0.5

置信度等级：A < 8分 / B < 14分 / C < 20分 / D ≥ 20分
score_range = [final_score − uncertainty, final_score + uncertainty]
```

- **L字段**（低置信度，±1.5分/字段）：AI暴露类、valuation_risk、market_expectation_score、concentration_risk 等手动字段
- **M字段**（中置信度，±0.5分/字段）：peg_ratio、ev_ebitda、ev_sales、forward_pe、fcf_yield 等财务指标

---

## ⑧ 评级规则

| final_score | 评级 |
|-------------|------|
| ≥ 80 | ⭐ 综合强劲 |
| ≥ 65 | ✅ 综合良好 |
| ≥ 50 | 👀 综合中性 |
| ≥ 35 | ⚠️ 谨慎评估 |
| < 35 | 🚫 风险较高 |

这里的评级只表示综合候选质量，不是交易指令。可执行结论还必须同时通过：

- 动态估值分不低于 60；
- 数据有效率不低于 95%；
- 没有关键字段或人工复核否决；
- 风险与组合约束允许新增仓位。

---

## ⑨ 季度基准漂移检测（drift_detector.py）

### 设计目的

防止季报后数据滑坡被忽视：
- 增速放缓导致 ERG 暗中恶化（价格未动但估值已贵）
- 市场情绪扭转导致 PEG/EV 倍数隐性跳升
- 每次 mock_data.py 更新后可立即看到 vs 上季度的偏离程度

### 监控字段与方向

| 字段 | 描述 | higher_is_better |
|------|------|-----------------|
| peg_ratio | PEG 倍数 | False（越低越好） |
| ev_ebitda | EV/EBITDA 倍数 | False |
| ev_sales | EV/Revenue 倍数 | False |
| forward_pe | 远期市盈率 | False |
| revenue_growth_yoy | 收入增长 YoY | True（越高越好） |
| fcf_yield | FCF Yield | True |

### 预警计算

```python
pct_change = (current − baseline) / abs(baseline)

severity:
  |pct_change| ≥ 50% → "critical"（严重）
  |pct_change| ≥ 25% → "warning"（警告）

direction:
  higher_is_better=False：pct_change > 0 → "worse"（变贵）
  higher_is_better=True：pct_change > 0 → "better"（改善）
```

### 基准快照（BASELINE_2026Q1）

每季财报后手动更新，旧快照保留为 `BASELINE_PREV`。当前活跃基准：`BASELINE_CURRENT = BASELINE_2026Q1`。

| Ticker | peg_ratio | ev_ebitda | ev_sales | forward_pe | rev_g_yoy | fcf_yield |
|--------|-----------|-----------|---------|-----------|-----------|-----------|
| NVDA | 0.90 | 32.6 | 17.5 | 27.0 | 85% | 2.43% |
| AVGO | 0.92 | 24.0 | 13.0 | 22.0 | 44% | 3.80% |
| PLTR | 2.20 | 155.0 | 38.0 | 120.0 | 36% | 0.80% |
| PANW | 1.50 | 62.0 | 12.5 | 52.0 | 15% | 2.40% |
| CRWD | 1.85 | 105.0 | 22.0 | 95.0 | 25% | 1.20% |
| FTNT | 1.10 | 35.0 | 8.5 | 32.0 | 14% | 3.20% |
| NOW | 1.70 | 72.0 | 20.0 | 58.0 | 22% | 1.80% |
| ONTO | 1.20 | 22.0 | 7.5 | 30.0 | 22% | 3.00% |
| MRVL | 0.62 | 28.0 | 10.0 | 22.0 | 48% | 2.50% |
| SNOW | 2.80 | 185.0 | 10.5 | 155.0 | 33% | 0.60% |

> 此为 mock_data.py 初始值快照（2026-Q1）。

### 2026-06-10 更新后漂移摘要

更新全量 yfinance 数据后，vs 2026-Q1基准：26 个 critical，13 个 warning。  
代表性偏离（Top 5）：

| Ticker | 字段 | 变化 | 方向 |
|--------|------|------|------|
| SNOW | fcf_yield | +248% | better（yield大幅提升）|
| FTNT | peg_ratio | +194% | worse（从1.1涨到3.2）|
| MRVL | ev_ebitda | +193% | worse（从28涨到82）|
| PANW | peg_ratio | +192% | worse（从1.5涨到4.4）|
| CRWD | peg_ratio | +190% | worse（从1.9涨到5.4）|

### Dashboard 集成

- **侧边栏**：红色 badge 显示 critical/warning 总数，可展开查看 Top 8 明细
- **排行榜**：ticker 旁显示 `⚡漂移`（critical）或 `△漂移`（warning）标签
- **单股详情**：有 critical 偏离时自动展开漂移 expander，逐字段显示基准→当前及变化百分比

### 下季度更新流程

```
1. 季报后更新 mock_data.py（H字段用yfinance，AI暴露字段手动）
2. 重启 Streamlit（确保模块缓存刷新）
3. 查看 Dashboard 漂移 badge 确认变化
4. 将旧 BASELINE_CURRENT 值手动移入 BASELINE_PREV（drift_detector.py）
5. 将 BASELINE_2026Q2 新快照填入，更新 BASELINE_CURRENT 指向
```

---

## 数据来源标注

| 颜色 | 含义 | 字段举例 |
|------|------|---------|
| 🟢 绿 | yfinance 实时 | price, market_cap, peg_ratio, ev_ebitda, forward_pe |
| 🔵 蓝 | 计算推导 | rule_of_40, score_range |
| 🟡 黄 | 手动整理（季报后更新）| ai_revenue_exposure_pct, valuation_risk, NRR |
| 🔴 红 | 付费API/Mock静态 | actual_eps_vs_consensus, guidance_vs_consensus |

### 重要数据注意事项

| 问题 | 受影响字段 | 处理方式 |
|------|-----------|---------|
| yfinance NVDA market_cap 错误 | market_cap | 保留手动值 $4.91T（yfinance因拆股前后股数问题返回~$485B）|
| CRWD ev_ebitda GAAP失真 | ev_ebitda | 保留Non-GAAP手动值105（GAAP因高SBC导致EBITDA≈0，yfinance返回2717x）|
| SNOW ev_ebitda 为负 | ev_ebitda | 保留Non-GAAP手动值185（GAAP EBITDA为负，yfinance返回-73.6x）|
| PLTR revenueGrowth 异常 | revenue_growth_yoy | 手动修正为39%（yfinance误返84.7%，实际Q1 FY25 YoY +39%）|
| MRVL earningsGrowth GAAP | eps_growth_yoy | 保留Non-GAAP手动值75%（GAAP因商誉摊销返回-80.4%）|
| NOW 5:1拆股（Q1 2025）| price, market_cap | 更新至拆股后价格$106（原$960为拆股前）|
