---
name: fisher-company-analysis
description: >
  用 Philip Fisher《Common Stocks and Uncommon Profits》十五要点 + scuttlebutt
  方法做估值之后的公司结构分析——这家公司值不值得持有多年，而不是现在贵不贵。
  适用于：门①估值出分之后的结构复核、持仓论点的定期重估、财报后判断论点是否
  破裂、决定一只票能不能进核心仓。不适用于：定价/择时/期权结构（那是门②门③）。
---

# Fisher 公司结构分析（ENERGREX 版）

## 这个 skill 在系统里的位置

门①出的是**分数**：六维得分、company_score、评级。分数回答"这个价格贵不贵、
成长配不配得上估值"。

Fisher 回答的是完全不同的一个问题：**这家公司值不值得持有很多年。**

两者的关系不是并列，是**否决关系**：

```
门① 估值分数  →  Fisher 结构分析  →  门② 工具选择
   （多少钱）      （值不值得拥有）      （怎么买）
                        ↓
                  不及格 → 不进核心仓，
                  分数再高也只能做短期价差
```

**Fisher 分只减仓，不加仓。** 这是这个 skill 最重要的一条使用纪律，理由见
下面「§4 为什么不给综合分」。

---

## 1. 十五要点（原文，Fisher 1958）

分成三组，不是按原书顺序，而是按**能不能从财报观测**分组——这是本版对原
框架最实质的改动，理由见 §3。

### A 组：可从 SEC 财报观测（6 条）

| # | 原文问题 | ENERGREX 里的观测口径 |
|---|---|---|
| 1 | Does the company have products or services with sufficient market potential to make possible a sizable increase in sales for at least several years? | `scoring/company_structure.py::growth_attribution()` 的增量归因 + `next_year_revenue_growth_est`、`ai_revenue_exposure_pct` |
| 3 | How effective are the company's research and development efforts in relation to its size? | R&D/收入（XBRL `ResearchAndDevelopmentExpense`）+ `roic`。**注意 Fisher 自己建议的 research/sales 是个坏指标**，见 §3.6 |
| 5 | Does the company have a worthwhile profit margin? | `gross_margin` / `operating_margin` / `fcf_margin` |
| 6 | What is the company doing to maintain or improve profit margins? | 毛利率与营业利润率的**时序斜率**，不是当期值。涨价撑起来的还是效率撑起来的，要拆 |
| 10 | How good are the company's cost analysis and accounting controls? | 营业利润率的**季度波动率** + 是否有财报重述（XBRL 重述标记 / 8-K Item 4.02） |
| 13 | In the foreseeable future, will the growth of the company require sufficient equity financing so that the larger number of shares then outstanding will largely cancel the existing stockholders' benefit from this anticipated growth? | 稀释后股本时序（`sec_fundamental_evidence.py` 已在取 `diluted_shares`）+ SBC/收入 + 回购是否只是抵消稀释 |

### B 组：可半观测（需要财报电话会/8-K，能做但要人确认，4 条）

| # | 原文问题 | 半观测口径 |
|---|---|---|
| 9 | Does the company have depth to its management? | **当代替换**：关键人物依赖度。10-K 风险因素里是否点名单一高管；核心技术高管离职公告（8-K Item 5.02）频率 |
| 12 | Does the company have a short-range or long-range outlook in regard to profits? | R&D + capex 占经营性现金流的比例 vs 回购占比。长期取向的公司在下行期不砍研发 |
| 14 | Does the management talk freely to investors about its affairs when things are going well but "clam up" when troubles and disappointments occur? | **可量化**：miss 的季度 vs beat 的季度，电话会 Q&A 环节时长、分析师提问数、是否撤回/停止给指引。这是 Fisher 十五点里最容易被忽略却最可测的一条 |
| 15 | Does the company have a management of unquestionable integrity? | 财报重述历史、SEC comment letter、内部人在高位的集中抛售（Form 4）、关联交易 |

### C 组：只能靠 scuttlebutt（5 条）

| # | 原文问题 |
|---|---|
| 2 | Does the management have a determination to continue to develop products or processes that will still further increase total sales potentials when the growth potentials of currently attractive product lines have largely been exploited? |
| 4 | Does the company have an above-average sales organization? |
| 7 | Does the company have outstanding labor and personnel relations? |
| 8 | Does the company have outstanding executive relations? |
| 11 | Are there other aspects of the business somewhat peculiar to the industry involved, which will give the investor important clues as to how outstanding the company may be in relation to its competition? |

**C 组绝对不许用财报数据推测后打分。** 见 §4。

---

## 2. Scuttlebutt 的当代规则

Fisher 1958 年的 scuttlebutt 是打电话问竞争对手、客户、供应商、前员工、
行业协会、领域内的科学家。今天这些渠道**既更容易也更危险**：

**可用，但要标注偏差**
- Glassdoor / 领英 / 前员工播客 → 对应 #7 #8。**严重选择偏差**：不满的人
  才去写评价。只看**趋势**（评分在变好还是变坏）和**具体事由**，不看绝对值。
- 客户侧的公开信号 → 对应 #4 #11。G2/Gartner Peer Insights 的续约意愿、
  开发者论坛里的迁移讨论、招聘岗位里透露的技术栈。
- 供应链公开信号 → 台积电/ASML 的产能评论、同业财报里点名的客户。

**不可用**
- 任何从公司内部人处获得的未公开信息。你不是有合规墙的机构，**没有
  Chinese Wall 保护你**。听到了就不能交易，这比没听到更糟。
- 付费专家网络（GLG 等）转述的当季经营数据。

**方法纪律**
1. 每条 scuttlebutt 必须记录：来源类型、日期、可证伪性（"如果这是错的，
   我会在哪里看到相反证据"）。
2. 同一结论至少两个**独立**来源。同一篇报道被三家转载是一个来源。
3. scuttlebutt 结论写进 `data/mispricing_cases.jsonl`（门①误价研究页的哈
   希链），跟系统里其他论点走同一套留痕，不另开一本账。

---

## 3. 对 Fisher 原框架的六条修改（顶级基金视角）

这一节是**本版与原书的差异清单**，不是 Fisher 说的。每条都写明为什么改。

### 3.1 十五点必须分否决项和打分项

Fisher 只说"可以在少数几点上不及格，不能在很多点上不及格"，没给权重。
这在组合管理里没法用——它把一个组合决策交给了主观加总。

**改为：**
- **#15（诚信）和 #13（稀释）是一票否决。**
  诚信有问题时，前面 14 点的**输入本身**不可信——你是在用可能被操纵的财报
  去评估 14 个维度。这不是"扣分"，是整个分析失效。
  稀释否决的理由不同：#13 是唯一一条**直接作用于每股价值**的，其余 14 条
  作用于企业价值。一家公司可以在企业层面优秀而在每股层面把你稀释掉。
- 其余 13 点是打分项。
- **A 组不及格 ≠ C 组不及格。** A 组有数据支撑，可以下结论；C 组只能给
  "已评估/未评估"。

### 3.2 #7 #8 #9 在当代美股大科技上几乎无差别，必须替换

用 1958 年工业公司的标准去评 NVDA/MSFT/GOOGL：劳资关系全优（高薪高福利）、
高管关系全优（没有公开内斗）、管理层深度全优（板凳很深）。**十五点里有三
点恒定满分，等于白问。**

当代真正有区分度的替代问题：
- #7 → **核心技术人员流失**，不是劳资关系。AI 研究员的去向是公开的。
- #8 → **高管离职的时点**，不是关系融洽。在坏消息前离职是信号。
- #9 → **关键人物依赖**。Jensen Huang 之于 NVDA、Lisa Su 之于 AMD，是
  Fisher 说的"管理层深度不足"的当代形态——公司越成功，这个风险越隐蔽。

### 3.3 必须挂时效，不能一次评估长期有效

Fisher 的判断是"此刻是不是好公司"。你的系统有 point-in-time 纪律
（`VALUATION_EVIDENCE_STANDARD.md`、`sec_fundamental_evidence.py` 的
acceptance date 门控），Fisher 分必须挂同样的时效：

- A 组：跟财报走，每季度重算。
- B 组：跟财报电话会走，每季度重看。
- C 组：**6 个月过期**。半年前的 scuttlebutt 不是证据，是记忆。
- 过期的点标"未评估"，不沿用旧结论。

### 3.4 #6 要拆"涨价撑的"还是"效率撑的"

Fisher 说他对只靠提价维持利润率的公司持怀疑态度。这句话可以做成硬口径：

```
毛利率变动 ≈ 价格效应 + 成本效应 + 结构效应
```

结构效应用 `company_structure.py::growth_attribution()` 拿掉（高毛利分部
占比上升会抬高综合毛利率，这既不是涨价也不是降本）。剩下的部分如果全靠
提价，在需求转弱时会最先崩。

### 3.5 #1 的"市场潜力"必须落到增量归因，不是 TAM

TAM（总可寻址市场）是卖方最爱的数字，也是最没有约束力的。Fisher 问的是
"能不能带来好几年的显著销售增长"——这个问题在财报上的形态是：

- 增量来自几个分部？（`growth_attribution`）
- 最大贡献分部增速归零，整体增速掉到多少？（`growth_without_top`）
- 结构在不在挪？（`max_share_swing`）

**NVDA 的例子**：`revenue_growth_yoy = 0.85` 是一个通过 #1 的数字。但如果
增量的 95% 来自一个分部，#1 的真实答案是"这几年的增长取决于一件事继续发生"，
不是"市场潜力充足"。两者在评分引擎里是同一个数，在 Fisher 这里必须分开。

### 3.6 #3 的量化建议（研发费 / 销售额）本身是坏指标

Fisher 建议用 research cost / total sales 来量化研发效率。这个比率**方向
不明**：高，既可能是研发投入强，也可能是研发效率低（花得多产出少）。

**改用产出侧代理：**
- 新产品/新分部收入占比（`company_structure` 的分部时序能看到新分部出现）
- R&D 的增量资本回报：`Δ营业利润 / 滞后 2–3 年的累计 R&D`
- 比率本身只作为**规模对照**（同业里投入是不是明显偏低），不作为效率判断

---

## 4. 为什么不给一个 Fisher 综合分

最容易做、也最该拒绝做的事，是把十五点各打 1–10 分加总成一个 "Fisher Score = 112/150"。

不做的三个理由：

1. **C 组不可观测。** 给不可观测的东西打分，等于让语言模型根据财报语气编
   一个数。这正是这个系统在 `get_category()` 里明确拒绝过的错误——COST 曾
   经因为默认成 AI_SOFTWARE 而算出"看着正常、实际毫无意义的分"。同样的错
   不能在这里重犯一遍。
2. **不可回测。** 定性判断没有历史标签，算不出预测力。而这个系统的既定原
   则是（门③页面原话）："评分的预测力尚未通过验证门槛，用它放大敞口等于
   给噪声加杠杆。" Fisher 分连门①那套六维分的验证水平都达不到，更不能拿
   去定仓位。
3. **加总会掩盖否决项。** #15 不及格的公司，加总分可能仍有 100/150。

**所以输出的是一张分组结论表 + 一个二元的核心仓资格，不是一个分数。**

| 结论 | 含义 | 对仓位的作用 |
|---|---|---|
| `VETO` | #13 或 #15 不及格 | 不持有；已持有则进门③减仓清单 |
| `NOT_CORE` | A 组有 ≥2 条明确不及格 | 可做短期价差，不进核心仓，不加仓 |
| `CORE_ELIGIBLE` | A 组通过，B 组无红旗 | 允许进核心仓（仍受门③硬约束封顶） |
| `UNASSESSED` | A 组数据不足 | **不是通过。** 按 NOT_CORE 处理 |

`UNASSESSED` 和 `CORE_ELIGIBLE` 必须分开——把"没查出问题"写成"没问题"，
是这类分析最贵的错误。

---

## 5. 执行流程

### Step 0 — 先确认估值分已经出了
Fisher 分析是**估值之后**的一步。没有门①的分数就做结构分析，等于在不知道
价格的情况下讨论值不值得买。
```
mcp__energrex-valuation__get_stock_score(ticker)
```
拿到 `company_score`（不受熔断影响的真实五维质量分）和六维拆分。
**注意区分 `final_score` 和 `company_score`**——前者被熔断乘数压过。

### Step 1 — A 组：拉结构事实
```python
from scoring.edgar_fetcher import fetch_xbrl_facts, _quarterly_segment_revenues
from scoring.company_structure import assess

facts = fetch_xbrl_facts(cik)
result = assess(ticker, _quarterly_segment_revenues(facts))
```
`result.flags` 是发现的结构问题，`result.notes` 是这次分析本身的限制。
**两者不能混着报。**

对照 §1 A 组的 6 条逐条给结论。每条写：口径、数值、通过/不通过、依据的
filing。

### Step 2 — B 组：半观测的 4 条
需要读财报电话会记录和 8-K。给不出依据的标"未评估"，不猜。

### Step 3 — C 组：scuttlebutt
按 §2 的规则。**这一步没做就是没做**，在输出里明确写"C 组未执行"，不要
用 A/B 组的结论去暗示 C 组也没问题。

### Step 4 — 出结论
按 §4 的四档给一个资格判定，附上：
- 否决项状态（#13 #15）
- A 组逐条表
- B 组逐条表（含未评估项）
- C 组执行情况
- **"什么会让我改变这个判断"** —— 每个不及格项写出可证伪的复核触发条件

### Step 5 — 写回系统
结论写进 `data/mispricing_cases.jsonl`（哈希链留痕），门③仓位管理页的
「持仓论点监控」会自动把它纳入监控。这样 Fisher 判断不是一次性的文档，
而是会在论点破裂时主动报警的东西。

---

## 6. 常见误用

- ❌ **拿 Fisher 分去加仓。** 只减不加，见 §4。
- ❌ **对没有分部披露的公司说"结构健康"。** 那是看不出，不是健康。
- ❌ **用 TAM 回答 #1。** 用增量归因。
- ❌ **把 Glassdoor 评分当 #7 的答案。** 只看趋势和具体事由。
- ❌ **给 ETF/杠杆产品做 Fisher 分析。** QQQ/SMH/ETHU 没有"管理层诚信"
  这个维度，`scoring/exposure_context.py::_NON_SCORED_CHAINS` 里的标的
  一律跳过。
- ❌ **跨公司比较 XBRL 分部名。** 分部成员名是公司自定义的，
  `DataCenterMember` 在两家公司之间不可比。只做同一公司内部的时序比较。
