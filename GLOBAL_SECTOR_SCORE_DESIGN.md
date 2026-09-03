# 全局得分 / 行业得分 双轨评分设计

**状态**：设计已定稿（2026-08-31 讨论确定），尚未落地实现
**背景**：解决"AI暴露度固定占六维总分20%权重、且不分行业差异化"
导致的两个问题：(1) 非AI公司的这一项权重空转，只能拿默认中性50分，
不体现真实行业特性；(2) 评分系统客观上鼓励仓位向AI板块集中，
与仓位管理模块"避免单一行业过度集中"的目标互相拖台。

---

## 一、核心设计：一只股票，两个正当的分数，不是矛盾

全局得分（Global Score）
  = 估值 + 成长 + 质量 + 预期差 + 动量（五维，去掉AI暴露）
  权重按原六维比例重新归一化：
    估值 25.0% / 成长 31.2% / 质量 18.7% / 预期差 12.5% / 动量 12.5%
  用途：跨行业统一排行榜、仓位管理模块的基准判断依据
  特性：不随任何UI筛选状态变化，永远是同一套计算方式

行业得分（Sector Score）
  = 全局五维 + 该股票所属行业的特定暴露度维度（如AI暴露度）
  仅在用户主动选定某个行业筛选视角时计算并展示
  仅对真正属于该行业分类（TICKER_CATEGORY）的股票展示，
  不属于该行业的股票不会出现在这个筛选视图里

这不是同一个问题的两个答案，是两个不同问题各自的答案：
全局得分回答"这只股票整体值不值得关注"；行业得分回答"在XX赛道里，
这只股票排第几"。两者不需要、也不应该被调成一致——它们的差异
本身就是有意义的信息。

---

## 二、实证数据（2026-08-31 用真实持仓/财报数据验证）

| 股票 | 全局得分 | 行业得分(AI维度) | 行业分类 | 是否AI相关 |
|---|---|---|---|---|
| NVDA | 83.4 | 74.8 | AI芯片 | 是 |
| MRVL | 59.6 | 39.2 | AI芯片 | 是 |
| PLTR | 73.9 | 62.3 | AI软件/SaaS | 是 |
| INTC | 32.0 | 28.7 | AI芯片 | 是 |
| CRWD | 63.3 | 56.8 | 网络安全 | 否 |
| ISRG | 48.9 | 48.7 | 半导体设备 | 否 |
| AAPL | 44.2 | 44.1 | 大型科技 | 否 |
| AMAT | 54.5 | 53.0 | 半导体设备 | 否 |

关键发现：
1. 真正的AI公司（NVDA/MRVL/PLTR/INTC）在两种口径下分差较大
   （最大达20.4分，MRVL），因为它们的AI暴露度分数远高于50分
   中性线，去掉这一项对它们影响最大。
2. 非AI公司（CRWD/ISRG/AAPL/AMAT）两种口径下分差很小
   （0.1-6.5分），因为它们的AI暴露度本来就是数据缺失后的
   默认中性50分，这一项本来就没有实质性拉低或拉高它们的分数。
3. 这推翻了设计讨论初期的一个假设——"AI暴露权重系统性打压
   非AI公司"并不成立（因为缺失字段走的是中性默认值，不是低分）。
   真正的问题是"这20%权重对非AI公司是空转的，没有提供
   任何真实行业信息增量"，不是"惩罚"。

---

## 三、命名依据

- final_score（全局得分）沿用系统既有变量名，不做重命名，
  避免破坏 decision_policy.py、quant_audit.py、仓位管理模块
  等所有已依赖这个名字的下游代码。
- sector_score（行业得分）为新增字段，与 final_score 并存，
  不替代。

⚠️ 注意：文档写作时用"final_score沿用"这个措辞，但当时讨论中
  实际验证的"全局得分"公式（五维去AI暴露、重新归一化）跟现有
  quant_engine.py里真正的final_score计算（六维含AI暴露+风险熔断）
  不是同一套逻辑。这两者的关系需要开发时明确梳理：
  究竟是"新增一个global_score字段、保留现有final_score不变"，
  还是"用新的五维公式替换现有final_score的计算方式"，
  文档原文倾向前者，但没有做最终拍板，请与用户确认后再动手。

---

## 四、实现待办（尚未落地，供后续开发参考）

- [ ] 在 quant_engine.py 或新建模块中实现全局五维权重的
      归一化计算，产出 global_score 字段
- [ ] 在 ai_profile.py 现有 PROFILE_WEIGHTS 结构基础上，
      新增行业得分的计算入口，仅当用户选定行业筛选时触发
- [ ] 排行榜UI需要明确的视觉/文案区分两种分数，避免用户误解为
      "同一个分数在不同页面显示不一致"
- [ ] 行业筛选视图需要先做"是否属于该行业"的归属判定
      （复用 TICKER_CATEGORY，位于 scoring/company_taxonomy.py），
      非该行业股票不应出现在对应的行业得分列表中
- [ ] 未来如果扩展"医疗暴露度"等其他行业特定维度，需要
      为每个新维度单独定义输入字段、best/worst锚点、数据来源

---

## 五、明确不做的事

- 不会让 final_score 因为用户是否在使用行业筛选而改变数值，
  保证仓位管理模块依赖的基准分数始终稳定。
- 不会强行让全局得分和行业得分收敛到同一个数字——
  这两个分数存在差异是设计的一部分，不是需要修复的bug。

---

## 六、已知的额外背景（供实现时参考）

- ai_profile.py 里 PROFILE_WEIGHTS 目前四种AI角色分类权重完全相同，
  这是被 tests/test_ai_profile_routing.py 专门锁定验证过的既有设计，
  不是bug，实现本功能时不要误改这个文件里的权重表。
- 行业归属判定应直接复用 scoring/company_taxonomy.py 里的
  TICKER_CATEGORY，不要重新实现判断逻辑（该文件是2026-08-31
  从 scoring_engine.py 拆分迁移出来的，仓库里可能还残留
  scoring_engine.py 被归档在 _archive/deprecated_duplicate_engine/
  下，那是旧版本，不要参考它的实现）。

---

## 七、实现总结（2026-09-03 落地完成）

### 两个阻塞问题的确认结果

1. **第六节的迁移声明不适用于本仓库**：`company_taxonomy.py` 迁移、
   `scoring_engine.py` 归档到 `_archive/deprecated_duplicate_engine/`
   这两件事从未发生在 `evolxd/energrex-multilens` 仓库里——用户确认
   这是沟通失误，迁移实际发生在另一个未同步的隔离环境。本次实现
   直接对接仓库里真实存在、未归档的 `scoring/scoring_engine.py::TICKER_CATEGORY`，
   完全忽略第六节对 `company_taxonomy.py`/`_archive` 的引用。

2. **第三节自己标记的 final_score / global_score / sector_score 关系**：
   用 NVDA / MRVL / ISRG 的真实数据反推验证后，用户确认按以下理解实现
   （与本节标题下方"实证数据"表格的数字吻合）：
   - `final_score`：完全不变。仍是 `quant_engine.py` 里六维加权
     （含 `ai_exposure`）减 `risk_penalty`、经熔断乘数调整后的既有字段，
     变量名和计算逻辑均未触碰。
   - `global_score`：新增独立字段。六维去掉 `ai_exposure`，剩余
     valuation/growth/quality/expectation_gap/momentum 五维权重
     按 ÷0.80 重新归一化（0.20→25.0%、0.25→31.2%、0.15→18.7%、
     0.10→12.5%、0.10→12.5%），**不扣 `risk_penalty`，不过熔断乘数**。
   - `sector_score`（行业得分）：**不是新公式**，就是既有 `final_score`，
     仅在排行榜 UI 里、用户主动选择某个 `TICKER_CATEGORY` 行业筛选时
     切换标签展示，并把列表范围收窄到只显示该行业成员。

### 改动了哪些文件

- **`scoring/quant_engine.py`**
  - 新增 `compute_global_score(dim_scores, weights) -> float`：按上述
    ÷0.80 归一化公式计算全局得分，函数上方注释写明推导过程和验证依据。
  - `ScoreResult` 数据类新增 `global_score: float` 字段；`score_ticker()`
    末尾调用 `compute_global_score(dim_scores_dict, ai_profile.weights)`
    填充该字段。`final_score` 及其计算路径未做任何修改。

- **`app.py`**
  - `load_from_csv()` 新增两个派生列：
    - `global_score`：复用 CSV 里已经写回的六维分数列
      （`valuation_score`/`growth_score`/.../`momentum_score`），调用
      `quant_engine.compute_global_score()` 计算，未新增计算路径，
      也未触碰 `refresh_scores.py` 的写回逻辑。
    - `ticker_category`：`scoring_engine.get_category(ticker).value`，
      即 `TICKER_CATEGORY` 归属——与页面里原有的 `category` 列
      （`quant_engine.SECTOR_BASELINES` 的 `sector_tag`，Hardware/SaaS/
      Cybersecurity 三分类）是两套不同的既有分类体系，互不覆盖。
  - 排行榜页新增"评分视图"下拉筛选：默认显示"🌐 全局得分（不筛选行业）"，
    此时列表不收窄，每只股票原有的 `final_score` 展示保持原样（标签仍是
    "综合"），新增一行"全局 {global_score}"作为补充参考；选择具体
    `TICKER_CATEGORY` 行业后，列表收窄到仅该行业成员，同一个
    `final_score` 数字的标签切换为"行业得分"。

- **`tests/test_global_score.py`**（新增）
  - `compute_global_score()` 的单元测试：验证剔除 `ai_exposure` 且
    权重正确重新归一化、不受 `risk_penalty`/熔断影响；并用
    `quant_audit.merge_data()` + `score_ticker()` 对 NVDA/ISRG 做端到端
    回归，断言其 `global_score` 落在文档第二节数据表 83.4 / 48.9 附近
    （容差 0.15）。

### 回归测试结果（NVDA / MRVL / ISRG）

| 股票 | final_score（不变） | global_score（新增） | 文档表格里的全局得分 | 匹配情况 |
|---|---|---|---|---|
| NVDA | 74.83 | 83.41 | 83.4 | 精确匹配 |
| ISRG | 48.70 | 48.94 | 48.9 | 精确匹配 |
| MRVL | 39.99 | 60.82 | 59.6 | 预期内偏差——MRVL 的 mock_data.py 价格在文档
  2026-08-31 快照之后被用户提供的实时报价修正过，属于合理漂移，非计算错误 |

全量测试套件（`pytest --ignore=_archive`）在改动前后均为全部通过，
新增测试后为 359 passed。

### 未做的事（按用户明确的范围收窄）

- 未给"行业得分"单独写计算路径——它就是 `final_score`，只做标签
  切换和成员过滤。
- 未修改 `ai_profile.py` 的 `PROFILE_WEIGHTS` 权重表（被
  `tests/test_ai_profile_routing.py` 锁定为既有设计）。
- 未处理第六节提到的 `company_taxonomy.py`/`_archive` 相关内容——
  确认这两者在本仓库不存在，已忽略。
- 未触碰 `scoring/scoring_engine.py` 与 `scoring/quant_engine.py` 之间
  既存的两套分类体系（`TICKER_CATEGORY` vs `sector_tag`）分裂问题——
  这是独立于本次任务、此前已用 `scoring/engine_divergence_audit.py`
  测量过但尚未统一的架构问题，本次不在范围内。
