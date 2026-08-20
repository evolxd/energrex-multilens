# RXRX 数据修复任务 — 执行结果

对应任务：`CLAUDE_CODE_TASK_rxrx_data_fix.md`（该文件本身在另一个 Claude Code
会话 `/tmp/energrex-multilens` 里创建，未同步进本仓库，这份文档独立复现
了诊断结论并按其四步任务执行，验证结果自然收敛到相同的数字，见任务1）。

## 任务1：RXRX/SDGR/TEM 过时价格 + 衍生字段联动 ✅ 已完成

Web 核实（2026-08-18/19）后的真实数据：

| 代码 | 股价 | 流通股数 | 市值 | TTM营收 | 现金 | 净负债 | EV | EV/Sales | P/S |
|---|---|---|---|---|---|---|---|---|---|
| RXRX | $3.09 | 540,889,504 | $1.671B | $65.73M | ~$550M，debt minimal | ≈-$550M(净现金) | $1.121B | 17.06x | 25.42x |
| SDGR | $17.67 | 74,720,724 | $1.320B | $250M | $406.4M(Q1数字)，debt=$0 | -$406.4M(净现金) | $0.914B | 3.66x | 5.28x |
| TEM | $55.69 | 180,430,000 | $10.05B | ~$1.40B(源区间$1.36-1.432B) | $820.7M | +$352.1M(净负债，跟前两个不同) | $10.402B | 7.43x | 7.18x |

验证（改前后估值维度分数确实变化，跟任务文档描述的"分数没变=没修对"核对过）：

```
RXRX valuation=65.63 quality=16.07 final=30.67   ← quality分与原任务文档独立核实的16.07完全吻合
SDGR valuation=73.81 quality=23.21 final=41.0
TEM  valuation=72.42 quality=14.29 final=36.58
```

只改了 `current_price`/`market_cap`/`ev_sales`/`ps_ratio` 这四个我实际核实过的字段。
`revenue_growth_yoy`/`fcf_margin`/`operating_margin`/`roic`/`debt_to_equity`/`beta` 等
其余字段**没有动**——没核实过，不该假装核实过。

## 任务2：新增 Biotech 分类 ❌ 未完成（按任务文档的规定动作，不是失败）

尝试路径：Damodaran（NYU Stern）行业数据本来就是这套系统别处在用的方法论，
本应是最合适的真实来源。实际执行受阻：`pages.stern.nyu.edu` 被这次会话的
网络出口代理拦截（`EGRESS_BLOCKED`），搜索引擎摘要只返回个别公司的历史
毛利率数字（如 Regeneron 89%、Vertex 82%），给不出行业分布的 P10/P90 分位数，
也不构成一个可信来源。

按任务文档的明确指示："如果暂时找不到可靠的分布数据，宁可保留 RXRX/SDGR/TEM
在'SaaS'分类下……也不要用拍脑门数字填一个新分类"——已照做，未新增 Biotech
分类，未修改 sector_tag。诊断和踩坑记录已写入
`scoring/quant_data.py`（RXRX/SDGR/TEM 条目上方）和
`scoring/quant_engine.py`（`SECTOR_BASELINES` 定义上方）的代码注释里。

额外发现：即使拿到了行业分布数据，RXRX（临床期AI药物发现平台）、
SDGR（收入大头其实是软件授权，商业模式更接近SaaS）、TEM（$14亿TTM营收的
已商业化诊断业务）三者本身业务阶段/模式差异很大，单一 Biotech 分类未必
对三者都合适——这是后续如果要做这件事，需要先想清楚的问题。

## 任务3：应用 sector_tag=Biotech ⏭️ 按规定跳过

任务文档原文："仅当任务2完成时执行"——任务2未完成，此步骤不适用，没有改动。

## 任务4：全库 sector_tag 错配审计 ✅ 已完成，发现 11 + 8 个问题

方法：不是靠主观判断"这家公司像不像硬件"，而是拿 `quant_engine.py` 实际
解析出的 `sector_tag` 去跟 `scoring_engine.py` 自己的 `TICKER_CATEGORY`
分类交叉核对——两套分类体系本该一致，不一致的地方就是错配的强证据，
不是我的猜测。

**11 个确认错配**（`scoring_engine.py` 判定为 AI软件/SaaS 或网络安全，
但 `quant_engine.py` 那边因为没显式设置 `sector_tag`，默认落进了 Hardware）：

| 代码 | 公司 | scoring_engine分类 | quant_engine实际套用 |
|---|---|---|---|
| ACN | Accenture | AI软件/SaaS | Hardware |
| AFRM | Affirm Holdings | AI软件/SaaS | Hardware |
| EXLS | ExlService Holdings | AI软件/SaaS | Hardware |
| LUNR | Intuitive Machines | AI软件/SaaS | Hardware |
| NTNX | Nutanix | AI软件/SaaS | Hardware |
| S | SentinelOne | 网络安全 | Hardware |
| TTD | The Trade Desk | AI软件/SaaS | Hardware |
| TYL | Tyler Technologies | AI软件/SaaS | Hardware |
| U | Unity Software | AI软件/SaaS | Hardware |
| VEEV | Veeva Systems | AI软件/SaaS | Hardware |
| ZM | Zoom | AI软件/SaaS | Hardware |

这 11 个的修法在代码里已经有现成先例——`scoring/quant_data.py` 里本来就有
一段专门叫"错误归类为 Hardware → 修正为 SaaS"，已经收了 PATH/AI/SOUN/
RXRX/SDGR/TEM 六个同类修复，这 11 个是同一次清理**漏掉**的。

**8 个 MEGA_TECH（`scoring_engine.py` 有专门分类，`quant_engine.py` 三桶
体系完全没有对应桶，只能强行塞进 Hardware 或 SaaS）**：AAPL / AMZN /
GOOGL / IBM / META（塞进 Hardware）、MSFT / NFLX / ORCL（塞进 SaaS）。
这个不是"选错了桶"，是"压根没有适合的桶"，比上面11个更根本，工作量也
更大（等同于任务2缺 Biotech 桶的同类问题，但换成科技巨头）。

按任务文档要求，**只诊断列清单，没有现在就动手改**——11个的修复很机械
（照抄现成的先例模式加一行），但涉及11只票的分数会跟着变，值得单独确认
后再动手，不该顺手夹带。
