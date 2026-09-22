# AI成长股估值评分系统

## 项目概述
针对美股AI相关公司（NVDA、AVGO、PLTR、PANW、CRWD 等）的综合估值评分工具。
不是简单看PE，而是综合判断：当前价格贵不贵 / 成长是否匹配估值 / AI暴露是否真实 / 是否存在预期差。

## 目录结构
```
ai_valuation/
├── app.py                        ← Streamlit Dashboard 主程序（4个页面）
├── requirements.txt              ← Python依赖
├── CLAUDE.md                     ← 本文件（Claude Code项目说明）
└── scoring/
    ├── scoring_engine.py         ← 核心评分引擎（所有子分公式）
    ├── mock_data.py              ← 10只股票Mock数据（每季财报后更新）
    ├── score_audit.py            ← 逐行审计工具（命令行版）
    └── SCORING_FORMULA.md        ← 评分公式完整文档（含机构锚点）
```

## 启动方式
```powershell
# 推荐：先查后起（杀掉残留实例 + 确认端口空 + 报告分支/其它克隆）
powershell -ExecutionPolicy Bypass -File scripts\restart_app.ps1
```
```bash
# 安装依赖（第一次）
pip install -r requirements.txt

# 手动启动（注意：入口是 home.py，不是 app.py——app.py 只是门①那一页）
streamlit run home.py

# 命令行审计某只股票
cd scoring && python3 score_audit.py NVDA
```

## 评分系统核心设计
- **6个子分**：估值 / 成长 / 质量 / AI暴露 / 预期差 / 风险扣分
- **连续函数**：linear_clamp / inverse_clamp，无阶梯跳变
- **4种公司类型**：AI芯片 / AI软件SaaS / 网络安全 / 半导体设备，各自不同权重
- **final_score** = 加权子分合计 - risk_penalty（最大扣20分）

## 当前10只股票
NVDA AVGO MRVL（AI芯片）
PLTR SNOW NOW（AI软件）
PANW CRWD FTNT（网络安全）
ONTO（半导体设备）

## 当前开发进度
- [x] 评分引擎 scoring_engine.py
- [x] Mock数据 mock_data.py（10只股票）
- [x] 命令行审计工具 score_audit.py
- [x] Streamlit Dashboard app.py（4页面）
- [x] mock_data.py yfinance基准更新（2026-06-10，10只股票全量）
- [x] 季度基准漂移自动检测（drift_detector.py，第六步完成）
- [x] yfinance 实时接入（启动自动并行拉取+坏字段过滤+时效显示，第七步完成）
- [x] FastAPI + PostgreSQL 后端（混合模式+APScheduler+10端点+历史图表，第八步完成）
- [x] SEC EDGAR AI暴露字段自动提取（edgar_fetcher.py，3/10 tickers有效：NVDA/MRVL/PLTR；其余fallback mock，第九步完成）

## 修改指南
| 要改什么 | 改哪个文件 | 注意事项 |
|--------|---------|--------|
| 评分阈值（best/worst值） | scoring_engine.py | 改 linear_clamp 参数 |
| 各类型公司权重 | scoring_engine.py WEIGHT_CONFIG | 确保每类权重之和=1.00 |
| 股票数据 | scoring/mock_data.py | AI暴露字段每季财报后手动更新 |
| 新增股票 | mock_data.py + scoring_engine.py TICKER_CATEGORY | 两处都要加 |
| Dashboard UI | app.py | 4个页面：排行榜/单股详情/对比/审计 |
| 超限后的减仓指令 | account/rebalance.py | 顺序结算，别改成"每条限额各算各的"——同一笔仓位会被卖好几遍 |
| QQQ/SMH 对冲分段 | account/hedge_split.py | SEMI_CHAINS 决定哪些标的算半导体；两段相加必须等于要对冲的总量 |
| 对冲方案（张数/行权价） | account/risk.py compute_index_hedge_plan | QQQ 走 compute_qqq_hedge_plan 包装（宽度固定$35/$60）；新标的不填宽度，按现价比例推 |
| 对冲宽度检查 | account/hedge_width.py | 情景参数要跟 risk.py STRESS_SHOCKS 对齐，两处问的必须是同一个跌幅 |
| 新增 SEC 覆盖（CIK） | scoring/edgar_fetcher.py TICKER_CIK | 加完必须跑 `python scripts/verify_cik.py`（要设 SEC_USER_AGENT）——CIK 错一位不报错，只会安静地画出另一家公司的财报 |
| beta 口径 | account/beta_regression.py | 压力测试用下跌日 beta，测不够精确（t<2）才退回全样本；**别把 _refresh_beta_spy 改回读 yf.info["beta"]**，那是厂商全样本值，会把下跌日口径在 8 天内全部覆盖掉 |
| 期权张数的符号 | account/options.py signed_quantity | 读 options_positions 的 quantity **一律过它**：xlsx 导入存带符号张数，Chrome 抓取存 abs+direction，手动录入的 direction 列写的却是 Call/Put。直接用 raw quantity 会把卖出读成买入——BD 符号反掉、压力测试里崩盘变赚钱 |
| 快照/简报的"多久以前" | account/snapshot_age.py | gen_time 是不带时区的**纽约**墙上钟点，别拿 datetime.now() 去减 |
| 持仓数看着不对劲 | scripts/diagnose_positions.py | 只读、不联网，逐行摊开查符号/数量级/重复；判定在 account/position_audit.py。股票表的重复行用 check_dupes.py（那是追加表，判定方式不同） |

## 重要说明
- **AI暴露字段**：NVDA/MRVL/PLTR 由 edgar_fetcher.py 自动从SEC 10-Q/8-K提取（confidence H/M）；其余7只仍为手动mock（confidence L）
- **edgar_fetcher.py** 每周自动刷新（APScheduler weekly job），结果缓存在 scoring/edgar_cache.json
- **估值风险valuation_risk**是0~1的手动评估值，最主观的字段
- **Rule of 40 统一用FCF口径**：revenue_growth_yoy + fcf_margin（不用EBITDA）
- 数据来源标注：绿=yfinance实时，蓝=计算推导，黄=手动，红=付费API（暂Mock）

## 机构基准锚点来源
- PEG阈值：Peter Lynch + MS 2026芯片报告
- ERG（EV/Rev÷增长）：Meritech Capital 2026
- Rule of 40 / NRR：SaaS Capital / BVP Nasdaq Cloud Index
- 网安估值：Windsor Drake Cyber Valuation Q1 2026
- 半导体设备：MS 2026 Semiconductor Equipment Report
