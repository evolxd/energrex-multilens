# 门⑤ 纪律看板设计 —— 信号响应记录 + 分维度打分

## 0. 背景

审"六重门"重建的落地页 mockup 时，用户问了一句关键的话："我现在没有验证和纪律吗，
就是要统计每天的指引我是否调整了。对不同维度进行打分，对我的行为进行监控。"

查代码后确认：**门⑤纪律现在是空的，不是藏起来了，是真的没建**。已经存在的三处
最接近的东西，全都只看"此刻"，没有一处记录"这个信号是哪天出现的、你有没有真的
处理、拖了几天"：

| 现有信号来源 | 函数 | 只做了什么 | 缺什么 |
|---|---|---|---|
| 止盈/止损/到期提醒 | `_cascade.py::_scan_exit_signals()` | 每次同步现算现扔 | 没有历史记录，不知道"昨天的提醒你有没有处理" |
| 对冲纪律检查 | `account/hedge_governance.py::evaluate_protective_put_hedges()` | 只评估当前这一刻是否违规 | 没有历史表，不知道"违规了几天才修" |
| 持仓论点监控 | `scoring/mispricing_monitor.py` (经 `home.py::_load_thesis_alerts`) | 每次刷新现查现显示 | 同上，没有响应记录 |

本设计只做一件事：给这三类信号加一层**记录 + 回头核对 + 分维度打分**，把"系统提醒过你"
和"你有没有真的听"这两件事分开记下来，而不是只看"问题现在还在不在"。

**门④和门⑤的分工**（避免以后混）：
- 门④下场前验证：往前看，"我要开新仓，有没有过硬约束" —— 一次性检查，发生在下单前
- 门⑤纪律：往回看，"过去这段时间，系统提醒过的事我有没有真的做" —— 持续记录 + 打分，
  发生在下单后、跨天跨周

---

## 1. 核心设计难点：怎么判断"已处理"，而不是"问题恰好自己消失了"

这是本设计最容易做错的地方，必须先讲清楚。

天真的做法是："这个信号今天还在不在数据库扫描结果里"——不在了就算"已处理"。
这是错的：止损信号可能因为**股价自己反弹**、pnl_pct 掉回阈值以下而不再触发，
这种情况下用户什么都没做，是市场自己救回来的，不该算作"纪律执行"。

**正确定义**：一个信号只有在能找到**对应的真实交易记录**（`option_realized_trades`
里同一个 symbol、在信号首次出现之后的平仓/减仓记录）时，才算"已响应"（`acted`）；
如果信号自己不再触发但找不到匹配交易，算"自然消失"（`self_resolved`）——这是两种
不同的结果，分开记，`self_resolved` 不计入纪律执行率的分子。

---

## 2. 维度定义

**本轮做 4 个**（响应窗口数字用户已确认，按下面的值实现，不是占位）：

| 维度 | 触发来源 | 触发条件 | "及时响应"窗口 |
|---|---|---|---|
| 止盈纪律 | `_scan_exit_signals` | 空期权盈利≥50% 或 多期权盈利≥100% | 2个交易日 |
| 止损纪律 | `_scan_exit_signals` | 多期权亏损≤-50% | 2个交易日 |
| 到期处理 | `_scan_exit_signals` | DTE≤7 | 必须在 DTE=0 前处理完（到期未平仓算最差情况：`expired_unhandled`） |
| 对冲纪律 | `hedge_governance.evaluate_protective_put_hedges` | 返回状态非 `NO_HEDGE_NEEDED`/正常 | 1个交易日（风险敞口类问题更紧急） |

**论点纪律——用户明确决定这轮不做**（`mispricing_monitor.thesis_state_for_ticker`
SEVERE/WATCH 转变触发，"响应"的定义比其它四个模糊，SEVERE 状态下减仓和清仓都算
合理响应，不是非黑即白），留到二期，`discipline_signals` 表和记录算法这轮不覆盖这个
维度，以后加维度只是多一行触发逻辑，不用改表结构。

窗口数字是本次给出的默认建议，用户已确认按这个跑，用真实数据观察一段时间后如果
明显不合理再调，跟 K=20 一个套路。

---

## 3. 数据模型

新表 `discipline_signals`（加进 `account/db.py::init_db()`）：

```sql
CREATE TABLE IF NOT EXISTS discipline_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT NOT NULL,
    dimension       TEXT NOT NULL,   -- 止盈纪律/止损纪律/到期处理/对冲纪律/论点纪律
    symbol          TEXT NOT NULL,   -- 期权symbol，或对冲/论点信号对应的underlying
    first_seen_date TEXT NOT NULL,   -- 信号第一次出现的日期
    last_seen_date  TEXT NOT NULL,   -- 信号最近一次仍在触发的日期（同一信号持续存在时更新这个字段，不新建行）
    detail          TEXT,            -- 触发时的原始 reason 文本，供追溯
    status          TEXT NOT NULL,   -- open / acted / self_resolved / expired_unhandled
    resolved_date   TEXT,            -- 变成 acted/self_resolved/expired_unhandled 的日期
    resolved_via    TEXT,            -- acted 时：匹配到的交易描述（symbol+close_date）
    response_days   INTEGER,         -- resolved_date - first_seen_date，仅 acted 时有意义
    UNIQUE(account_id, dimension, symbol, first_seen_date)
);
```

`UNIQUE(account_id, dimension, symbol, first_seen_date)` 保证同一个信号持续存在时是
UPDATE `last_seen_date`，不是每次同步插入新行——这条跟 `option_realized_trades` 当初
没管好"整单去重"是同一类坑，这次从 schema 层面直接堵掉。

---

## 4. 记录/核对算法（每次账户同步时跑一遍，挂在 `_cascade.py` 的 sync 流程里）

```
每次同步：
  1. 跑三个现有扫描函数，拿到"当前正在触发"的信号列表（按维度分类）
  2. 对每条当前触发的信号：
       按 (account_id, dimension, symbol) 查 discipline_signals 里 status='open' 的行
       - 已存在 → 更新 last_seen_date = 今天
       - 不存在 → 插入新行，first_seen_date = last_seen_date = 今天，status='open'
  3. 对 discipline_signals 里所有 status='open' 但今天没有出现在"当前触发列表"里的行：
       在 option_realized_trades 里查同 symbol、close_date 在 first_seen_date 之后的记录
       - 查到 → status='acted', resolved_date=close_date, resolved_via=交易描述,
                response_days = resolved_date - first_seen_date
       - 查不到 → status='self_resolved', resolved_date=今天
  4. 到期处理维度额外规则：DTE 到 0 那天如果该行仍是 status='open'，
       直接标 status='expired_unhandled'（不用等下一轮同步判断有没有匹配交易）
```

---

## 5. 打分：分维度展示，不强行合成一个总分

跟这周 Kelly 分桶时学到的教训一样——**不要用一个数字掩盖不同维度间真实存在的差异**。
止损纪律 95% 但对冲纪律 40%，合成一个"纪律总分 67.5"毫无意义，反而把真正的短板藏起来。
所以只在维度内部聚合，维度之间并排展示，不加权平均成一个数。

每个维度、每个统计区间（比如"近30天"），展示：

```
响应率 = acted 数 / (acted + self_resolved + expired_unhandled) 总数     [n=X，起止日期 A→B]
平均响应天数 = avg(response_days)，仅统计 acted 的行
未处理中最久的一条 = 当前 status='open' 的行里 first_seen_date 最早的那条，距今几天
```

这三个数字（响应率、平均响应天数、未处理最久的一条）比单一分数更能回答用户的真实问题——
"我最近是不是开始拖延止损了"这种趋势，一个综合分看不出来，三个分开的数字能看出来。

---

## 6. 还缺什么代码才能跑起来

1. `account/db.py`：加 `discipline_signals` 表的 CREATE TABLE（+ ALTER TABLE 兜底，
   照抄这周 `combo_id`/`combo_strategy` 那次的模式）。
2. 新模块 `account/discipline.py`：
   - `record_and_resolve_signals(acct_id, today=None)` —— 上面第4节的算法
   - `compute_discipline_scores(acct_id, since=None, until=None) -> dict[dimension, stats]`
3. `_cascade.py`：sync 流程里调用 `record_and_resolve_signals`（跟现有 `_scan_exit_signals`
   同一批数据源，不用重新拉数据）。
4. 门⑤新页面（`pages/9_🛡️_纪律看板.py` 或类似命名）：每个维度一张卡片
   （响应率/平均响应天数/未处理最久一条），加一张"当前未处理信号"明细表。
5. 测试：至少覆盖"acted vs self_resolved 判断正确"、"UNIQUE 约束不重复插入同一信号"、
   "到期未处理正确标记 expired_unhandled"这三类。

---

## 7. 已拍板的决定（2026-09-08）

1. ~~论点纪律要不要先做~~——**决定：这轮不做**，见 §2。
2. ~~响应窗口数字对不对~~——**决定：按 §2 的数字实现，暂不调整**。
3. **未处理信号需要主动推送提醒**——**用户明确要"推送"，不是只在门⑤页面被动展示**。
   这个待定的是"用什么机制推"，见 §9。

---

## 9. 推送机制——已定：方案 A

用户选定 **方案 A：同步后弹提示**——不脱离手动同步这个动作，每次点"同步账户"，
如果发现未处理的纪律信号（`discipline_signals` 里 status='open' 且超过对应维度
响应窗口的行），在同步成功的提示里一起弹出来，跟这周投资者看板"每次你手动同步后
我顺手推一次"是同一个模式，不新建独立于 Streamlit 之外的进程。

实现位置：`_sidebar.py::render_status()` 里 `_casc.run_sync_cascade()` 成功后
的 `st.success(...)` 那一段——现在已经在拼 净值/BD/盈亏/出场信号 这几个数字，
加一项"纪律提醒 N 条"，超过响应窗口的信号数按维度列出来（比如"止损纪律：PLTR
已超期3天未处理"），不新增页面、不新增进程。

以下是决策前考虑过的另外两条路径，供以后如果要"真正独立于是否打开app"的推送时参考：

| 方案 | 触发时机 | 依赖 | 工作量 |
|---|---|---|---|
| A. 同步后弹提示 | 只在你手动点"同步账户"之后，如果发现未处理信号，弹一个醒目提示（不是新技术，`_sidebar.py::render_status()` 里同步成功后已经在弹 `st.success`，加一段判断即可） | 无新依赖，纯 Streamlit 里加一段逻辑 | 小 |
| B. 本机定时检查 | 不管你有没有打开 app，到点了（比如每天收盘后）自动检查一次 `data/energrex.db`，用 Windows 通知弹一下 | 需要一个独立于 Streamlit 之外常驻/定时跑的脚本（Windows 计划任务或类似机制），读本地 SQLite 是只读操作，不需要重新连 Firstrade | 中 |
| C. Claude Code 定时任务推送 | 同 B 的触发时机，但用这个环境本身已有的定时任务能力去查 `data/energrex.db`（只读）+ 推送给你，不用另外写 Windows 计划任务 | 需要设置一个跑在 Claude Code 里的 schedule，不属于 `ai_valuation` 这个repo的代码，是运维层面的配置 | 中，但不占用这个项目的代码 |

---

## 10. 变更记录

- **2026-09-08**：首次成稿，随后同一天用户确认：论点纪律这轮不做、响应窗口数字
  照定不改、需要主动推送提醒。§9 三条路径给出后用户选定 **方案 A（同步后弹提示）**。
  至此所有设计决策已定，可以开始写代码：`account/db.py` 加表、新建
  `account/discipline.py`、接入 `_cascade.py`、`_sidebar.py` 加提醒展示、
  门⑤新页面、测试。
