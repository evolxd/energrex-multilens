# ENERGREX 统一门户架构设计 v1

> 目标：把现在散在两个仓库、三种形态（Streamlit 多页 / 静态 HTML 看板 / CLI+MCP）的所有功能，
> 收进**一个页面、多个入口**。本文从系统架构师角度给出信息架构、路由、数据流、技术选型和迁移路线。

---

## 0. 设计目标与硬约束

### 目标
1. **单一入口页**：打开一个 URL，就能到达系统的任何功能，不用记 8 个 `.html` 文件名 + 5 个 Streamlit 页 + 一堆命令。
2. **入口按"五步"组织**：用户已经建立的心智模型是"评分 → 误价发现 → 仓位管理 → 对冲纪律 → 执行验证"。门户导航必须复用它，不另造一套分类。
3. **一次启动**：延续 `Open-ENERGREX.ps1` 的"只记一条命令"。
4. **状态全局可见**：数据质量、账户口径核对、风险红旗、上次刷新时间——任何页面都能一眼看到，不用点进去才发现数据是旧的。

### 硬约束（不可协商）
| 约束 | 对门户的含义 |
|---|---|
| **系统禁止自动下单** | 门户里不存在"下单"按钮；一切写操作止于"建议 / 预警 / 记录" |
| **内部 vs 对外必须物理隔离** | 对外看板（`investor_dashboard`）不是门户的一个 tab，是**独立构建产物**；门户对外版由脱敏管线单独生成，绝不靠"前端隐藏 div"来隔离 |
| **只做多（Long Only）** | 入口文案与筛选默认多头视角，不提供做空工作流 |
| **两个代码仓库保持物理分离** | `ai_valuation`（研究）+ `ENERGREX期权量化系统`（风控运营）各自独立部署；门户是**聚合外壳**，不把两边代码合并进一个仓库 |
| **本地优先（local-first）** | 门户默认跑在本机；对外那份才上 GitHub Pages |

---

## 1. 现状盘点（要收编的东西）

### 1.1 研究侧 —— `C:\Users\evolx\ai_valuation`（仓库 `energrex-multilens`）

| 形态 | 名称 | 作用 | 数据源 |
|---|---|---|---|
| Streamlit 页 | `home.py` 作战室 | 状态栏 + 简报 + 账户 + 快捷入口 | `data/energrex.db`, `mispricing_cases.jsonl` |
| Streamlit 页 | `pages/1_📊_AI_估值评分.py` | 排行榜 + 个股六维报告 + 价格温度带 | `results_validated.csv` |
| Streamlit 页 | `pages/2_📈_期权分析.py` | 期权策略分析 | 期权链 API |
| Streamlit 页 | `pages/3_🏦_账户监控.py` | 账户净值 / 现金流 / 敞口 | `energrex.db` positions |
| Streamlit 页 | `pages/4_🔎_误价研究.py` | 误价案例立案 / 门控 / 证据链 | `mispricing_cases.jsonl`（SHA-256 哈希链） |
| Streamlit 页 | `pages/5_⚖️_仓位管理.py` | 硬约束看板 + 影子建议 | positions + `score_snapshots.csv` |
| CLI | `python quant_audit.py TICKER` | 单票逐字段公式展开（调试评分） | 六层数据合并 |
| CLI | `python refresh_scores.py` | 批量刷新评分，写回 CSV | 全量 |
| CLI | `python cross_validate_data.py` | 多来源交叉核实，出 `data_validation_report.json` | mock+overrides vs yfinance |
| MCP | `scoring/mcp_server.py` | `get_stock_score` / `get_valuation_report` / `list_universe` | 复用 `merge_data()` |
| 引擎（无 UI） | `scoring/quant_engine.py` | 六维评分 + 风险扣分 + 熔断 | — |
| 引擎（无 UI） | `scoring/mispricing_engine.py` | 误价发现（预筛 + 五门 + 六路径） | — |
| 引擎（无 UI） | `scoring/drift_detector.py` | 季度基准漂移检测 | `score_snapshots.csv` |
| 引擎（无 UI） | `scoring/price_boundary_backtest.py` | 价格温度带的预测性验证 | 快照历史 |
| 校验（无 UI） | `validation/validators/score_validator.py` | 用 `formula.py` 独立重算，抓 CSV 漂移 | — |

### 1.2 运营侧 —— `C:\Users\evolx\Documents\ENERGREX期权量化系统`（仓库 `energrex-risk-monitor`）

| 形态 | 名称 | 作用 |
|---|---|---|
| 静态 HTML | `control_center.html` | 现有的"系统入口"页（本设计要取代 / 升级它） |
| 静态 HTML | `dashboard.html` | 内部风险看板（真实持仓 / Greeks / 数据质量红旗） |
| 静态 HTML | `investor_dashboard.html` | **对外**投资人看板（脱敏，单独构建） |
| 静态 HTML | `trading_performance.html` | 交易成绩：滚动 20/50 胜率、ENERGREX vs QQQ |
| 静态 HTML | `discipline_review.html` | 退出纪律复核（+50/+100/−40/−60% 触发） |
| 静态 HTML | `valuation_dashboard.html` | 本地估值看板（读研究侧产物的镜像） |
| 静态 HTML | `valuation_review.html` | 估值数据人工复核 |
| 表单 | `daily_update_form.html` | 每日收盘后录入券商口径 |
| 脚本 | `Run-ENERGREX-DailyUpdate.ps1` | 25 步日更流水线（8 步固定决策顺序） |
| 脚本 | `Open-ENERGREX.ps1` | 唯一启动命令 |
| 脚本 | ~50 个 `Update-*.ps1` | 单项刷新（MarketData / IVRegime / EventRisk / KellyStats …） |
| 治理文档 | `Wind_Risk_Protocol.md` | 风控红线（杠杆 4.0 / Beta-Delta ±35% / 压力 8-12-15% / 回撤 20-30%） |
| 治理文档 | `docs/00–03` | 系统核心 / 数据标准 / 日常 runbook / 误价引擎接口 |

### 1.3 数据存储（门户需要读的）
```
ai_valuation/results_validated.csv        排行榜评分
ai_valuation/data/energrex.db             SQLite：持仓、快照、账户
ai_valuation/data/mispricing_cases.jsonl  误价案例，SHA-256 哈希链，只追加
ai_valuation/data/score_snapshots.csv     评分时点快照（验证层要攒到 ≥252 天）
ENERGREX期权量化系统/data/risk_history.csv          风险时间序列
ENERGREX期权量化系统/data/data_quality_report.csv   数据质量旗标
ENERGREX期权量化系统/data/event_calendar.csv        财报 / 事件
ENERGREX期权量化系统/data/trading_performance_*     成绩曲线
ENERGREX期权量化系统/Portfolio_Config.json          真实持仓（私有，绝不对外）
```

---

## 2. 信息架构（IA）—— 一页里怎么分

### 2.1 顶层导航 = 五步 + 两个横切区

```
┌─ 全局状态条（常驻，任何视图都在顶部）────────────────────────────┐
│  ● 数据质量 PASS/REVIEW   ● 账户口径 已核对 3h 前   ● 风险 2 红旗   │
│  ● 上次日更 2026-08-28 16:10   ● 熔断标的 1   ● 误价在研 2         │
└──────────────────────────────────────────────────────────────┘

左侧导航栏（分组）：
  研究
    ① 评分 Scoring          → 排行榜 / 个股六维报告 / 逐字段审计 / 漂移
    ② 误价发现 Mispricing    → 案例列表 / 立案 / 门控与证据链 / 盲测
  动手买入
    ③ 仓位管理 Position      → 硬约束看板 / Kelly 上限 / 影子建议
    ④ 对冲纪律 Hedging       → 期权分析 / IV regime / QQQ 对冲复核 / 退出纪律
    ⑤ 执行与验证 Verify      → 账户监控 / 交易成绩 vs QQQ / 每日录入
  ─────────
  运维台 Ops（横切）         → 日更触发 / 数据质量 / API 与输入文件 / 券商口径核对器 / 事件日历
  对外 Public（受控，单独构建）→ 投资人看板预览（只读脱敏镜像）
```

### 2.2 每个入口打开什么（catalog）

| 入口 | 归属步骤 | 打开的内容 | 现有实现 | 数据源 | 内/外 |
|---|---|---|---|---|---|
| 排行榜 | ① | ~93 只票六维评分表 + 筛选 | `pages/1` | `results_validated.csv` | 内 |
| 个股报告 | ① | 六维雷达 + 价格温度带 + Damodaran 框 + 8 镜头 | `pages/1` + `scoring_engine` | merge_data | 内 |
| 逐字段审计 | ① | 某票每个子分怎么算出来 | `quant_audit.py`（转 Web） | 六层合并 | 内 |
| 基准漂移 | ① | 价格没动但 ERG 暗中变贵的告警 | `drift_detector` | `score_snapshots.csv` | 内 |
| 评分自检 | ① | `formula.py` 独立重算 vs CSV 差异 | `score_validator` | — | 内 |
| 误价案例列表 | ② | 所有在研 / 已判定案例（P0–P3） | `pages/4` | `mispricing_cases.jsonl` | 内 |
| 立案 / 门控 | ② | 四句话契约 + 五门 + 证据链录入 | `pages/4` + `mispricing_engine` | JSONL | 内 |
| 盲测协议 | ② | 封存案例、揭晓日期、六种结果分类 | Draft PR#1（待移植） | `case_manifest.json` | 内 |
| 硬约束看板 | ③ | 杠杆 / Beta-Delta / 压力 / 回撤 现状 vs 上限 | `pages/5` | positions + `risk_history.csv` | 内 |
| Kelly 上限 | ③ | 1/3 Kelly 计算过程 + 样本充分性 | `pages/5` / `Update-KellyStats` | 闭合交易 | 内 |
| 影子建议 | ③ | 系统建议 vs 实际仓位（只记录不驱动） | `pages/5` | `score_snapshots.csv` | 内 |
| 期权分析 | ④ | 期权链 / 策略结构 / Greeks | `pages/2` | 期权 API | 内 |
| IV regime | ④ | IV 分位、财报前 IV crush 评估 | `Update-IVRegime` | 期权 API | 内 |
| QQQ 对冲复核 | ④ | 触发信号检查 + 对冲股数估算 + 预算 | `Wind_Risk_Protocol §4` | 组合 Beta-Delta | 内 |
| 退出纪律 | ④/⑤ | +50/+100/−40/−60% 复核触发 | `discipline_review.html` | 成交记录 | 内 |
| 账户监控 | ⑤ | 净值 / 现金流调整 NAV / 敞口 | `pages/3` + `dashboard.html` | `energrex.db` | 内 |
| 交易成绩 | ⑤ | 滚动 20/50 胜率、vs QQQ、pre-earnings 回测 | `trading_performance.html` | `trading_performance_*` | 内（脱敏后可外） |
| 每日录入 | ⑤ | 券商口径录入表单 | `daily_update_form.html` | 手填 | 内 |
| 日更触发 | Ops | 跑 `Run-ENERGREX-DailyUpdate.ps1` + 看 8 步决策日志 | 脚本 | — | 内 |
| 数据质量 | Ops | 四色标注 / 交叉核实报告 / 黄灯字段 | `cross_validate_data.py` | `data_validation_report.json` | 内 |
| API 与输入文件 | Ops | key 是否配、`export.csv` / `broker_marks.csv` 在不在 | `control_center.html` | 文件系统 | 内 |
| 券商口径核对器 | Ops | 账户净值 / 购买力 / 日盈亏 对齐 | `control_center.html` | 手填 vs 系统 | 内 |
| 事件日历 | Ops | 财报 T+3 / 布局 T+20 / lockup | `Update-EventRisk` | `event_calendar.csv` | 内 |
| 投资人看板 | Public | ENERGREX vs QQQ / NAV / 压力趋势 / 纪律表现 / 胜率 / 主题集中度 | `investor_dashboard.html` | **脱敏管线产物** | 外 |

---

## 3. 页面组成（外壳解剖）

```
┌────────────────────────────────────────────────────────────────┐
│  顶栏  ENERGREX  | 全局状态条（§2.1） | ⌘K 快速跳转 | 内部/对外 徽标 │
├──────────┬─────────────────────────────────────────────────────┤
│          │                                                     │
│  左导航   │            主内容区（Content Host）                    │
│  五步     │   加载被选入口的视图：                                  │
│  + 运维   │   · 原生组件（新写的）                                 │
│  + 对外   │   · 或 iframe 嵌现有 Streamlit 页 / 静态 html          │
│          │                                                     │
│          ├─────────────────────────────────────────────────────┤
│          │  右侧上下文栏（可折叠）                                 │
│          │  · 当前标的：DUOL  · 误价论点状态：REJECT_P3           │
│          │  · 相关告警 · "在其它步骤看这只票" 的深链              │
└──────────┴─────────────────────────────────────────────────────┘
```

要点：
- **全局状态条是架构级组件**，不是某页的装饰。它从一个 `/api/status` 聚合端点取数（见 §4），刷新周期 60s。任何视图打开时它都在。
- **主内容区是一个 Content Host**：能力有三档——① 原生渲染（新写的轻组件）；② `<iframe>` 嵌现有 Streamlit 页（`?embed=true`）；③ `<iframe>` 嵌静态 html。迁移期三种混用，长期向 ① 收敛。
- **右侧上下文栏**承载"跨步骤连续性"：选中一只票后，它在评分 / 误价 / 仓位里是同一个上下文，右栏给出深链，不用重新搜索。

---

## 4. 路由与全局状态

### 4.1 URL 方案（hash 路由，纯前端可行；换真前端时平滑升级为 path 路由）
```
#/score                      排行榜
#/score/DUOL                 个股报告
#/score/DUOL/audit           逐字段审计
#/score/drift                基准漂移
#/mispricing                 案例列表
#/mispricing/case/<id>       某案例门控与证据
#/position                   硬约束看板
#/position/kelly             Kelly 上限
#/hedging/options            期权分析
#/hedging/qqq-review         QQQ 对冲复核
#/verify/account             账户监控
#/verify/performance         交易成绩
#/ops/daily-run              日更触发 + 日志
#/ops/data-quality           数据质量
#/public                     对外看板预览（受控）
```
- **深链可分享**：`#/score/DUOL` 直接进 DUOL 报告。右栏的"跨步骤"按钮就是拼这些 hash。
- **参数**：`?date=2026-08-28`（时点回看）、`?embed=true`（被别的页嵌时去掉外壳）。

### 4.2 聚合状态端点（门户的中枢）
一个轻服务（FastAPI / 甚至一个定时生成的 `status.json`）暴露：
```
GET /api/status
{
  "data_quality": "REVIEW",              // 来自 cross_validate + data_quality_report.csv
  "account_reconcile": {"ok": true, "age_min": 187},
  "risk_flags": [{"ticker":"XXX","kind":"circuit_breaker"}],
  "last_daily_run": "2026-08-28T16:10:00-07:00",
  "mispricing_active": 2,
  "universe_count": 93,
  "surface": "internal"                  // internal | public
}
```
- 这个端点是**唯一允许跨两个仓库读数据的地方**（其它面板各读各的）。它把"系统现在健不健康"压成一个对象。
- `surface` 字段决定顶栏徽标和"对外"入口是否出现。

### 4.3 共享前端状态（跨步骤连续性）
一个极小的全局 store（`localStorage` + 内存）：
```
{ selectedTicker, asOfDate, lastVisitedByStep }
```
- 只存"上下文指针"，不缓存业务数据（业务数据每个面板自己取，避免脏读）。

---

## 5. 技术选型（三个方案 + 推荐）

| | 方案 A · 静态聚合壳 | 方案 B · Streamlit 收编（推荐主线） | 方案 C · 真前端 Shell + API 网关 |
|---|---|---|---|
| 外壳 | 一个手写 HTML（就是升级版 `control_center.html`）+ hash 路由 + iframe | 一个 Streamlit app，`home.py` 当外壳，`pages/*` 全并进来，静态 html 用 `components.iframe` 嵌 | React / Svelte 单页；Streamlit 页和静态页都作为被嵌资源 |
| 现有页怎么办 | 全 iframe，不改 | Streamlit 页原生并入；`.html` 看板 iframe | 全部 iframe 或逐步重写为组件 |
| 全局状态条 | 顶部固定 div 拉 `/api/status` | Streamlit 顶部容器 + `st.experimental_fragment` 定时刷新 | 一等公民组件 |
| 改动量 | 最小（1–2 天） | 中（1–2 周，主要是把运营侧 html 的关键数字重画成 Streamlit 组件） | 大（数周，要搭 API 网关做跨仓库聚合与鉴权） |
| 体验一致性 | 差（iframe 拼贴感、样式不统一、状态不联动） | 中上（研究侧原生统一；运营侧仍是嵌页） | 最好 |
| 内外隔离 | 靠"对外版单独构建"——可行 | 同左 | 可用运行时鉴权，但仍建议保留构建期隔离 |
| 风险 | iframe 深链、滚动、鉴权都别扭；长期技术债 | Streamlit 多页在页数多时导航变笨；`.ps1` 触发要包一层 | 前期投入大；两个 Streamlit/静态资源仍要维护 |

**推荐：B 作为主线，A 作为一周内可交付的过渡，C 作为 12 个月目标。**

理由：
1. 研究侧本来就是 Streamlit，`home.py` 已经是"作战室 + 快捷入口"的雏形——把它升级成正式外壳，是**顺水推舟**，不是另起炉灶。
2. 运营侧的 8 个 `.html` 大多是"读 CSV 画图表"，把其中**高频看的 3–4 个**（`dashboard` / `trading_performance` / `discipline_review`）的关键数字用 Streamlit 组件重画，剩下的先 iframe，收益/成本比最高。
3. `Run-ENERGREX-DailyUpdate.ps1` 用一个 `subprocess` 包装 + 流式日志展示即可，不需要重写脚本。
4. C 的核心价值是"API 网关统一鉴权 + 真正的跨仓库聚合"，但在只有一个用户、本地优先的当下，`/api/status` 一个端点 + 构建期隔离已经够用；等要给第二个人看、或要做移动端时再上 C。

---

## 6. 内部 / 对外隔离（架构级，不能靠前端藏）

```
                 ┌─────────────────────────┐
   内部门户       │  同一套外壳 + 全部入口     │  surface = internal
  (本机 :8501)   │  读真实持仓 / Greeks / 成本 │
                 └─────────────────────────┘
                              │  脱敏构建管线（build_public_investor_dashboard.py 扩展版）
                              ▼
                 ┌─────────────────────────┐
   对外门户       │  只含"对外"分组的入口       │  surface = public
 (GitHub Pages)  │  数据是脱敏快照，非实时      │
                 └─────────────────────────┘
```
规则：
- 对外门户是**构建产物**，不是给内部门户加 `?public=1`。构建时只打包对外允许的面板 + 脱敏后的数据快照。
- 脱敏白名单（沿用现有规则）：ENERGREX vs QQQ、NAV 趋势、压力趋势、纪律表现、胜率、主题集中度（不显示具体持仓）、数据质量 PASS/REVIEW。
- 脱敏黑名单（绝不进对外产物）：API key、账户号、order ID、具体持仓 / 期权代码、成本、Greeks、交易明细、未决数据质量内情、`Portfolio_Config.json` 任何字段。
- `.gitignore` 仍然是最后一道闸：`data/` `imports/` `logs/` `reports/` `Portfolio_Config.json` 永不进任何公开仓库。

---

## 7. 启动与部署

```powershell
# 唯一命令，不变
cd "C:\Users\evolx\Documents\ENERGREX期权量化系统"
.\Open-ENERGREX.ps1
```
`Open-ENERGREX.ps1` 的新职责：
1. 起研究侧 Streamlit 外壳 → `http://localhost:8501`（这就是统一门户）
2. 起 `/api/status` 轻服务（或先跑一次生成 `status.json`）
3. 打开浏览器到 `localhost:8501/#/score`

对外：
```powershell
python .\scripts\build_public_portal.py     # 由 build_public_investor_dashboard.py 扩展
Copy-Item .\public\portal\* .\public_site\ -Recurse -Force
cd .\public_site ; git add . ; git commit -m "Update public portal" ; git push
```

---

## 8. 迁移路线图

| 阶段 | 交付 | 前置 | 工期估计 |
|---|---|---|---|
| **P0 导航合并** | 升级 `control_center.html` / `home.py` 为唯一入口页，五步分组 + 运维 + 对外，其余全 iframe 深链 | 无 | 1–3 天 |
| **P1 全局状态条** | `/api/status` 聚合端点 + 顶栏常驻状态；点红旗能跳到对应面板 | P0 | 2–4 天 |
| **P2 研究侧原生化** | `pages/1–5` 并入外壳，右侧上下文栏（跨步骤选中同一只票） | P1 | 1 周 |
| **P3 运营侧关键页重画** | `dashboard` / `trading_performance` / `discipline_review` 的核心数字改 Streamlit 组件；日更触发 + 流式日志 | P2 | 1 周 |
| **P4 对外门户构建管线** | `build_public_portal.py`，脱敏白/黑名单校验做成构建期断言 | P1 | 3–5 天 |
| **P5（可选）真前端 Shell** | React/Svelte 外壳 + API 网关统一鉴权；Streamlit/静态页作为被嵌资源逐步退役 | P2–P4 稳定后 | 数周 |

---

## 9. 风险与取舍

| 风险 | 说明 | 缓解 |
|---|---|---|
| iframe 拼贴的技术债 | P0/P1 大量 iframe，样式割裂、深链/滚动别扭、被嵌页各自鉴权 | 只在过渡期用；P2/P3 逐步换原生；被嵌 Streamlit 页统一加 `?embed=true` 去掉侧栏 |
| Streamlit 多页导航变笨 | 入口一多，Streamlit 原生侧栏会很长 | 用自定义左导航（五步分组）替代原生 `pages/` 侧栏；`pages/` 仅作路由载体 |
| 跨仓库数据读取耦合 | 门户同时读两个仓库的 CSV/DB，路径写死会脆 | 只允许 `/api/status` 跨仓库；各面板通过配置项拿数据根路径，不写死 |
| 脱敏靠人肉 | 对外产物可能漏字段 | 白/黑名单做成 `build_public_portal.py` 里的**构建期断言**，命中黑名单字段直接 fail 构建 |
| 日更脚本被门户误触发并发跑 | 用户点两次"日更" | 触发前检查锁文件 / 上次运行状态；跑动时按钮置灰并显示流式日志 |
| "评分驱动仓位"被门户 UI 暗示 | 把评分和仓位放一起，用户容易以为高分就该重仓 | 仓位面板显式标注"评分不驱动仓位（未验证）"；影子建议与硬约束看板视觉分区 |
| 单点启动脚本变复杂 | `Open-ENERGREX.ps1` 要起 3 个东西 | 每个子进程独立、失败不互相阻塞；脚本只负责拉起 + 打开浏览器，不做业务 |

---

## 10. 一页话总结

- **一个外壳**（升级版 `home.py`）+ **五步分组导航** + **两个横切区（运维 / 对外）**。
- **一个聚合端点** `/api/status` 供常驻状态条，也是唯一允许跨两仓库读数的地方。
- **hash 路由 + 可分享深链**，右侧上下文栏保证"同一只票在五步之间连续"。
- **对外是构建产物，不是一个开关**：脱敏白/黑名单做成构建期断言。
- **分五期迁移**：先合并导航（P0，几天），再上状态条（P1），再逐步把页面原生化（P2/P3），对外管线并行（P4），真前端是长期可选项（P5）。
- 两个代码仓库**不合并**，`Open-ENERGREX.ps1` 仍是唯一启动命令。
