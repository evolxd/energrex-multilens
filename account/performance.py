"""
account/performance.py — 已实现交易绩效统计（胜率/盈亏/Kelly敞口候选输入）

2026-09-09 从 account_monitor.py 抽出来（之前是那个巨型UI脚本里的两个模块级
函数，只能靠 _cascade.py::_get_am() 的 AST 切片+exec 技巧从外部调用）。抽成
真正可 import 的模块，是因为门③仓位管理页面现在也要用这里的
win_rate_shrunk/kelly_f_shrunk/置信区间数据（用户拆分决定：绩效留门⑥账户
监控，仓位建议去门③），不能再靠那个只有 _cascade.py 在用的 AST 技巧。
account_monitor.py 现在从这里 import，逻辑一行没改。

设计背景见 docs/SIX_GATES_AND_EXPOSURE_DESIGN.md §2。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from account.options_repository import load_realized_trades as _load_realized_trades


def wilson_interval(p_hat: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion (胜率).

    与"收缩胜率"（K=20，往总体拉）是两个独立工具，回答不同问题：这个函数
    只用某一桶自己的 n 笔、自己的原始胜率算区间，不掺总体数据——掺了的话
    区间在数学上就不成立了（Wilson 区间的前提是 n 次独立伯努利试验的原始
    比例）。n 很小（1-2 笔）时区间会宽到接近 [0%, 100%]，这是正常现象，
    说明这个胜率确实不该信，不是 bug。
    """
    if n <= 0:
        return (0.0, 1.0)
    denom = 1 + z ** 2 / n
    center = (p_hat + z ** 2 / (2 * n)) / denom
    margin = z * ((p_hat * (1 - p_hat) / n + z ** 2 / (4 * n ** 2)) ** 0.5) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def compute_performance_stats(acct_id: str) -> dict | None:
    """从 option_realized_trades 计算全套绩效指标。数据为空返回 None。

    2026-09-06 起，胜率/盈亏/滚动统计的口径从"按单腿"改成"按整单（价差
    两腿合并）"——用户本人指出长期做价差的人被单腿口径伤到了：保护腿在
    整单盈利时被单独记一次"败"，跟专业机构衡量价差策略表现的方式不符。
    配对逻辑在 account/fifo.py 的 group_realized_trades_into_combos，写
    入 option_realized_trades 的 combo_id/combo_strategy 两列（这两列
    schema 里早就有，之前从来没人写进去）。单腿明细还在，就是不再是
    "总已实现盈亏/胜率"这些头部指标的口径。
    """
    df_legs = _load_realized_trades(acct_id)
    if df_legs.empty:
        return None

    df_legs["pnl"] = pd.to_numeric(df_legs["realized_pnl"], errors="coerce").fillna(0)
    # combo_id 为空的（老数据，重跑一次"运行 FIFO 分析"就会补上）退化成
    # "自己是自己的整单"，不阻断显示。
    _missing = df_legs["combo_id"].isna()
    if _missing.any():
        df_legs.loc[_missing, "combo_id"] = "legacy_single_" + df_legs.index[_missing].astype(str)
    df_legs["combo_strategy"] = df_legs["combo_strategy"].fillna(df_legs["strategy_type"])

    df = df_legs.groupby("combo_id", as_index=False).agg(
        underlying=("underlying", "first"),
        combo_strategy=("combo_strategy", "first"),
        open_date=("open_date", "min"),
        close_date=("close_date", "max"),
        pnl=("pnl", "sum"),
        legs=("combo_id", "count"),
    )

    df = df.sort_values("close_date").reset_index(drop=True)
    df["win"]             = (df["pnl"] > 0).astype(int)
    df["trade_num"]       = range(1, len(df) + 1)
    df["cumulative_pnl"]  = df["pnl"].cumsum()
    df["rolling20_wr"]    = df["win"].rolling(20, min_periods=1).mean()
    df["rolling20_avg"]   = df["pnl"].rolling(20, min_periods=1).mean()
    df["rolling50_wr"]    = df["win"].rolling(50, min_periods=1).mean()
    df["rolling50_avg"]   = df["pnl"].rolling(50, min_periods=1).mean()
    df["pnl_peak"]        = df["cumulative_pnl"].cummax()
    df["drawdown"]        = df["cumulative_pnl"] - df["pnl_peak"]

    wins   = df.loc[df["pnl"] > 0, "pnl"]
    losses = df.loc[df["pnl"] < 0, "pnl"]
    avg_win  = float(wins.mean())   if len(wins)   else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    pf = abs(avg_win / avg_loss) if avg_loss else None
    _pooled_win_rate = float(df["win"].mean())

    # 按整单的策略类型分组（credit/debit spread，或没配对上的裸腿）——
    # 这才是原来"按组合策略分组"那张表一直显示不出东西的原因：
    # combo_strategy 之前从来没被写进过 DB。
    #
    # avg_win/avg_loss/payoff_b/kelly_f 是给 docs/SIX_GATES_AND_EXPOSURE_DESIGN.md
    # §2 的期权价差敞口设计用的——那份文档里的赔率表格当时是手算的，这里补成
    # 正式代码。kelly_f = win_rate - (1-win_rate)/payoff_b，是"原始"Kelly（未打
    # 小样本折扣），折扣和硬约束封顶留给调用方（那份文档 §2.3/§2.4 还没写代码）。
    # close_date_min/max 跟 count 一起存进每个桶，是用户明确要求的："任何统计数字
    # 都要带起止时间和总样本量"——数据结构里直接带上，不是只在我说话时提一句。
    #
    # win_rate_shrunk/payoff_b_shrunk/kelly_f_shrunk：用户质疑"为什么小笔数要
    # 分开统计，不做总体统计"——分开统计是对的（不同价差类型结构性赔率不同，
    # 卖方/买方不是同一种赌注），但小样本桶（16笔、21笔）的具体数字确实噪音大。
    # 折中：贝叶斯收缩，往全账户总体（318笔量级，更可靠）拉一把，样本越小拉
    # 得越多。K_SHRINK 是"要攒多少笔真实战绩才能基本不理会总体平均"的虚拟
    # 样本量，用户本人选定 20（先前一度定过 30，2026-09-07 改回 20——见
    # docs/SIX_GATES_AND_EXPOSURE_DESIGN.md 变更记录）。
    K_SHRINK = 20
    by_combo = {}
    for cs, grp in df.groupby("combo_strategy"):
        _wins   = grp.loc[grp["pnl"] > 0, "pnl"]
        _losses = grp.loc[grp["pnl"] < 0, "pnl"]
        _n = len(grp)
        _win_rate = float((grp["pnl"] > 0).sum()) / _n if _n else 0.0
        _avg_win  = float(_wins.mean())   if len(_wins)   else 0.0
        _avg_loss = float(_losses.mean()) if len(_losses) else 0.0
        _payoff_b = abs(_avg_win / _avg_loss) if _avg_loss else None
        _kelly_f  = (_win_rate - (1 - _win_rate) / _payoff_b) if _payoff_b else None

        _win_rate_shrunk = (_n * _win_rate + K_SHRINK * _pooled_win_rate) / (_n + K_SHRINK)
        if _payoff_b is not None and pf is not None:
            _payoff_b_shrunk = (_n * _payoff_b + K_SHRINK * pf) / (_n + K_SHRINK)
        else:
            _payoff_b_shrunk = pf if pf is not None else _payoff_b
        _kelly_f_shrunk = (
            _win_rate_shrunk - (1 - _win_rate_shrunk) / _payoff_b_shrunk
        ) if _payoff_b_shrunk else None

        # 95% Wilson 区间——算在"原始胜率"上，不算在收缩胜率上（见
        # wilson_interval 的说明）。只回答"这一桶自己的样本量够不够撑起
        # 这个胜率数字"，跟收缩胜率是互补的两件事，不合并展示成一个数。
        _win_rate_ci_lower, _win_rate_ci_upper = wilson_interval(_win_rate, _n)

        by_combo[str(cs)] = {
            "count": _n, "win_rate": _win_rate,
            "win_rate_ci_lower": _win_rate_ci_lower,
            "win_rate_ci_upper": _win_rate_ci_upper,
            "avg_pnl": float(grp["pnl"].mean()), "total": float(grp["pnl"].sum()),
            "avg_win": _avg_win, "avg_loss": _avg_loss,
            "payoff_b": _payoff_b, "kelly_f": _kelly_f,
            "win_rate_shrunk": _win_rate_shrunk,
            "payoff_b_shrunk": _payoff_b_shrunk,
            "kelly_f_shrunk": _kelly_f_shrunk,
            "shrink_k": K_SHRINK,
            "close_date_min": str(grp["close_date"].min()),
            "close_date_max": str(grp["close_date"].max()),
        }

    by_und = {}
    for und, grp in df.groupby("underlying"):
        w = (grp["pnl"] > 0).sum()
        by_und[str(und)] = {
            "count": len(grp), "win_rate": w / len(grp),
            "avg_pnl": float(grp["pnl"].mean()), "total": float(grp["pnl"].sum()),
        }

    # 单腿口径分组——保留下来给想看细节的人参考，不再是头部指标。
    by_strat = {}
    for strat, grp in df_legs.groupby("strategy_type"):
        w = (grp["pnl"] > 0).sum()
        by_strat[str(strat)] = {
            "count": len(grp), "win_rate": w / len(grp),
            "avg_pnl": float(grp["pnl"].mean()), "total": float(grp["pnl"].sum()),
        }

    # EWMA 加权胜率（λ=0.94，近期权重高，按整单）
    _lam = 0.94
    _n   = len(df)
    _w   = np.array([_lam ** (_n - 1 - i) for i in range(_n)], dtype=float)
    _w  /= _w.sum()
    ewma_wr = float((_w * df["win"].values).sum())

    return {
        "df":            df,       # 整单口径——驱动上面所有头部指标/图表
        "df_legs":       df_legs,  # 单腿明细——给"逐笔交易明细"表用
        "total_pnl":     float(df["pnl"].sum()),
        "count":         len(df),
        "win_rate":      float(df["win"].mean()),
        "avg_pnl":       float(df["pnl"].mean()),
        "avg_win":       avg_win,
        "avg_loss":      avg_loss,
        "profit_factor": pf,
        "max_loss":      float(df["pnl"].min()),
        "max_gain":      float(df["pnl"].max()),
        "max_drawdown":  float(df["drawdown"].min()),
        "latest_wr20":   float(df["rolling20_wr"].iloc[-1]),
        "ewma_wr":       ewma_wr,
        "by_strat":      by_strat,
        "by_und":        by_und,
        "by_combo":      by_combo,
    }
