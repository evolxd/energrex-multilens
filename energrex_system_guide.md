# ENERGREX 估值系统 · 快速上手指南

给一个全新打开这个仓库的人（或 AI 会话）看的：这套系统是什么、怎么跑、
数据从哪来、最容易踩的坑在哪。深挖历史/每次改动的来龙去脉看 `HANDOFF.md`；
这份文件只负责让你 5 分钟内建立正确的心智模型。

## 1. 这是什么

给约 93 只美股 AI 相关公司打分的个人研究工具（Streamlit Dashboard）。
六个维度打分：估值 / 成长 / 质量 / AI暴露 / 预期差 / 动量，外加一个风险扣分
和熔断机制，最后合成一个 0-100 的 `final_score` + 评级标签。

不是给外部投资人用的产品，是个人研究工具——所有结论都设计成"不给可执行
指令，只给信号强度"，这是贯穿整套系统的设计哲学，改动时不要违背它。

## 2. 三种打开方式

| 方式 | 命令 | 用途 |
|---|---|---|
| 网页 Dashboard | `streamlit run home.py --server.port 8502` | 人工浏览排行榜/个股详情/期权/账户/误价研究 |
| 命令行审计 | `python quant_audit.py TICKER` | 逐字段公式展开，调试某只票的分怎么算出来的 |
| 对话（MCP） | 项目根目录 `.mcp.json` 已配置 | 任何支持 MCP 的 AI 客户端可直接调用 `get_stock_score` / `get_valuation_report` / `list_universe`，见 `scoring/mcp_server.py` |

VSCode 打开这个文件夹会自动识别 `.vscode/` 里配好的任务和调试配置，不用记命令。

## 3. 核心架构——两套并行的评分系统（最容易踩的坑）

**这是整个仓库最重要的一件事，漏掉这个会持续产生 bug。**

### 3a. 排行榜真实评分链路
`refresh_scores.py` → `scoring/quant_engine.py::score_ticker()` → 写入
`results_validated.csv` → `app.py` 排行榜页读这个 CSV 显示。

- `scoring/quant_data.py` 的 `QUANT_META`（`sector_tag` 字符串: Hardware/SaaS/
  Cybersecurity）决定用哪套 `SECTOR_BASELINES`
- `scoring/mock_data.py` 的 `MOCK_STOCKS` 是数据兜底层
- `scoring/user_overrides.json` 是最高优先级人工核对层——**但只信任
  `status: "verified"` 的值**，`"pending"` 占位符会被 `scoring/
  input_verification.py::trusted_override_value()` 拒绝，不会覆盖已有数据

### 3b. 个股页叙事文案链路
`app.py` 直接 `import` `scoring/scoring_engine.py` 的 `get_category` /
`WEIGHT_CONFIG` / `calc_damodaran_report`。这是**另一套独立的分类体系**
（`CompanyCategory` 枚举: AI_CHIP/AI_SOFTWARE/CYBERSECURITY/SEMI_EQUIP/
MEGA_TECH，配自己的 `TICKER_CATEGORY` 字典），驱动 Damodaran 估值纪律框、
投资摘要文案、`scoring/investor_lenses.py` 的 8 张多维解读卡片。

**新增一只股票必须两处都加**（`quant_data.py QUANT_META` 和
`scoring_engine.py TICKER_CATEGORY`），漏一处会让这只票在个股页被静默
误判成错误的公司类型（历史上真实发生过：ASML/GEV 被漏归到 AI_SOFTWARE，
造成 SaaS 型假设套在半导体设备公司头上）。

### 3c. final_score ≠ company_score，不要混着报

`final_score`（排行榜大字显示的那个数）= 六维加权 − 风险扣分，**再乘上
熔断系数（0.75，如果触发）**。熔断触发条件：`beta > 2.2 且 |最大回撤| >
35%`，或 `D/E > 1.8`。

`company_score`（`scoring/score_split.py::company_score()`）= 五个基本面
维度重新归一化，**不含动量、不受熔断影响**——是"这家公司到底怎么样"的
真实读数。一只票熔断触发时，`final_score` 会被砸低，但 `company_score`
不会跟着动，两者分歧可能很大（INTC 案例：final_score 20.9 vs
company_score 31.4）。

`app.py` 排行榜熔断标的会同时显示这两个数；`decision_policy.py::
evaluate_decision()` 也会给熔断标的一个专门的 `🧾 熔断复核 · <分项>` 标签，
不会跟真正低质量的票混用同一套"⚠️高价观察/🚫回避"标签。

## 4. 数据质量已知坑（这次会话修过的，别再犯）

| 坑 | 表现 | 状态 |
|---|---|---|
| ROIC 实为 ROE | 一次性非现金冲销（减值/重组）直接砸穿净利润，误读成"ROIC很差" | 已修：`scoring/yfinance_fetcher.py` 改用 NOPAT/InvestedCapital |
| PEG 分母不封顶 | `eps_growth_yoy` 超过~90%时（GAAP噪音或真实爆发式增长），PEG 被除成接近0，估值分虚高 | 已修：`refresh_scores.py` 封顶在60%，记录进 `bad_fields` |
| `next_year_revenue_growth_est` 缺 `%` 转换 | CSV里存的"5.0%"读回来变成5.0（500%），ERG估值比率和成长分都被拉爆 | 已修：加进 `_PCT_FIELDS` |
| `refresh_scores.py` 整条链路跑不起来 | import 了从未创建过的 `scoring/input_verification.py` | 已修：补上该模块 |
| GAAP `eps_growth_yoy` 单次剧烈波动 | 一次性冲销把同比%冲成正负穿越，数学上就没意义 | 按 ticker 处理：加进 `yfinance_fetcher.py` 的 `KNOWN_BAD_FIELDS`，mock 里设 `None` 排除而不是编造数字（先例：INTC/MRVL） |

**这类问题大概率还没查完**——这次只系统性修了 PEG 分母封顶，个别票的
`eps_growth_yoy`/`fcf_growth_yoy` 是否可信仍需要逐票用 `get_valuation_report`
或 `quant_audit.py` 配合真实财报核实，不要盲信 `results_validated.csv`
里任何一个"看起来很便宜"的数字。

## 5. 关键函数速查

| 函数 | 位置 | 作用 |
|---|---|---|
| `score_ticker(ticker, data) -> ScoreResult` | `scoring/quant_engine.py` | 核心评分入口，六维打分+风险扣分+熔断判定 |
| `split_scores(dim_scores, ...) -> ScoreSplit` | `scoring/score_split.py` | 把 final_score 拆成 company/momentum/risk/circuit 四个独立读数 |
| `evaluate_decision(final_score, valuation_score, ...) -> Decision` | `scoring/decision_policy.py` | 排行榜实际显示的标签/是否可执行，叠加数据质量门槛和估值门槛，不是纯看分数 |
| `merge_data(ticker, use_live=True) -> dict` | `quant_audit.py` | 单票数据合并（yfinance实时→mock→standalone→QUANT_META），MCP Server 复用的就是这个 |
| `refresh_all(tickers=None) -> DataFrame` | `refresh_scores.py` | 批量刷新入口，写回 `results_validated.csv`，六层数据合并顺序见文件头注释 |
| `calc_damodaran_report(ticker, data, category) -> dict` | `scoring/scoring_engine.py` | 个股页 Damodaran 估值纪律框 + 价格温度带（观察区/合适区/低价区上限）背后的计算 |

## 6. 部署状态

- GitHub：`evolxd/energrex-multilens`，`master` 分支是当前最新（这次会话
  所有修复已合并）
- Render：`render.yaml` 已配置好（`branch: master`），部署这一步需要
  仓库所有者本人登录 render.com 手动完成（涉及账号授权，AI 无法代劳）
- 本地开发：`streamlit run home.py --server.port 8502`（不是默认的8501，
  历史上端口冲突过）
