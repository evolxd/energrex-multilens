"""
半凯利仓位回测 — kelly_backtest.py
================================
用当前 Final Score 把 86 只股票分进 5 档（与 app.py 的评级门槛一致：
>=65 Strong Buy / 55-65 Buy / 45-55 Watch / 35-45 Expensive / <35 Avoid），
拉取每只股票近 2 年的历史价格，做季度滚动（63 个交易日、步长 21 天）远期收益抽样，
把同一档所有股票的收益样本池化，统计经验胜率/赔率，反推 Kelly 仓位建议。

输出: scoring/kelly_bands.json

⚠️ 方法论说明（必须读，也会原样写进输出 JSON 的 "methodology_caveat" 字段）：
这不是因果回测——不是"某只股票 N 个月前打了这个分，之后涨跌多少"，
而是用【当前】分档 + 该股票自己过去 2 年的价格波动，来近似"这一档股票历史上大概长什么样"。
存在生存者偏差：现在打高分的股票，很大程度上是因为过去基本面/股价表现好，
所以高分档的历史收益天然会偏乐观，赢率/赔率会比真实的"事前"预测能力更好看。
这是过渡方案。真正的前瞻式回测需要 kelly_snapshot_logger 积累几个月的
(ticker, 日期, score, 价格) 快照后才能做——见 scoring/kelly_snapshot_logger.py。

用法:
  python scoring/kelly_backtest.py
"""
import sys, json, pathlib, datetime
sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import yfinance as yf

ROOT     = pathlib.Path(__file__).parent.parent
CSV_PATH = ROOT / "results_validated.csv"
OUT_PATH = pathlib.Path(__file__).parent / "kelly_bands.json"

# 与 app.py `_score_to_rating` 完全一致的分档门槛
RATING_BANDS = [
    ("⭐ Strong Buy", 65, 101),
    ("✅ Buy",         55, 65),
    ("👀 Watch",       45, 55),
    ("⚠️ Expensive",   35, 45),
    ("🚫 Avoid",        0, 35),
]

WINDOW      = 63   # ~1 个季度的交易日数
STEP        = 21   # 滚动步长（~1 个月），窗口之间有重叠，样本数与独立性的折中
LOOKBACK    = "2y"
KELLY_CAP   = 0.25  # 单只股票半凯利建议仓位硬上限（不管公式算出多少，防止不现实的满仓建议）

METHODOLOGY_CAVEAT = (
    "近似回测，非因果验证：用【当前】Final Score 分档 + 该股票自身近2年价格波动池化统计，"
    "不是\"历史某时点打分后的真实后续收益\"。存在生存者偏差——现在的高分股，"
    "很大程度上是因为过去表现好才被打高分，所以高分档的历史赢率/赔率会比真实预测能力更好看。"
    "另外样本窗口整体处于牛市，各档win_rate都偏高，且低波动大盘股可能恰好落在中低分档，"
    "导致原始 half_kelly_capped 在分档间不单调（例如Expensive档比Buy档还高）——"
    "下游使用请一律读 half_kelly_monotonic 字段（已做跨档单调性修正），不要直接用 half_kelly_capped。"
    "这是过渡方案，待 kelly_snapshot_logger 积累几个月真实(日期,分数,价格)快照后，"
    "应切换到真正的前瞻式回测。"
)


def rating_for_score(score: float) -> str:
    for label, lo, hi in RATING_BANDS:
        if lo <= score < hi:
            return label
    return "🚫 Avoid"


def quarterly_forward_returns(close: pd.Series) -> list[float]:
    """滚动窗口的远期收益样本：ret[i] = close[i+WINDOW]/close[i] - 1"""
    vals = close.values
    n = len(vals)
    rets = []
    for i in range(0, n - WINDOW, STEP):
        p0, p1 = vals[i], vals[i + WINDOW]
        if p0 and p0 > 0:
            rets.append(p1 / p0 - 1.0)
    return rets


def kelly_from_samples(rets: list[float]) -> dict:
    """经验胜率/赔率 -> 全凯利 / 半凯利"""
    n = len(rets)
    if n < 10:
        return {"sample_size": n, "insufficient_sample": True}

    wins   = [r for r in rets if r > 0]
    losses = [-r for r in rets if r < 0]
    win_rate = len(wins) / n
    avg_win  = float(np.mean(wins))   if wins   else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0

    if avg_loss <= 0 or win_rate <= 0:
        full_kelly = 0.0
    else:
        b = avg_win / avg_loss
        full_kelly = win_rate - (1 - win_rate) / b if b > 0 else 0.0

    full_kelly = float(np.clip(full_kelly, 0.0, 1.0))
    half_kelly = full_kelly / 2
    half_kelly_capped = float(np.clip(half_kelly, 0.0, KELLY_CAP))

    return {
        "sample_size":        n,
        "win_rate":           round(win_rate, 4),
        "avg_win":            round(avg_win, 4),
        "avg_loss":           round(avg_loss, 4),
        "payoff_ratio":       round(avg_win / avg_loss, 3) if avg_loss > 0 else None,
        "full_kelly":         round(full_kelly, 4),
        "half_kelly":         round(half_kelly, 4),
        "half_kelly_capped":  round(half_kelly_capped, 4),
        "insufficient_sample": False,
    }


def run_backtest():
    df = pd.read_csv(CSV_PATH)
    score_col = "final_综合得分(0-100)"
    df = df[["ticker", score_col]].dropna()
    df["rating"] = df[score_col].apply(rating_for_score)

    bucket_returns: dict[str, list] = {label: [] for label, _, _ in RATING_BANDS}
    bucket_tickers: dict[str, list] = {label: [] for label, _, _ in RATING_BANDS}
    per_ticker_note: dict[str, str] = {}

    print(f"共 {len(df)} 只股票，开始拉取 {LOOKBACK} 历史价格...")
    for _, row in df.iterrows():
        ticker = row["ticker"]
        rating = row["rating"]
        try:
            hist = yf.Ticker(ticker).history(period=LOOKBACK)
            close = hist["Close"].dropna()
            if len(close) < WINDOW + STEP:
                per_ticker_note[ticker] = "价格历史不足，跳过"
                continue
            rets = quarterly_forward_returns(close)
            bucket_returns[rating].extend(rets)
            bucket_tickers[rating].append(ticker)
            print(f"  {ticker:6s} rating={rating:14s} 样本数+{len(rets)}")
        except Exception as e:
            per_ticker_note[ticker] = f"拉取失败: {e}"
            print(f"  {ticker:6s} 失败: {e}")

    bands = {}
    for label, _, _ in RATING_BANDS:
        stats = kelly_from_samples(bucket_returns[label])
        stats["tickers"] = bucket_tickers[label]
        bands[label] = stats
        print(f"\n[{label}] 股票数={len(bucket_tickers[label])} "
              f"样本数={stats.get('sample_size')} "
              f"胜率={stats.get('win_rate')} "
              f"半凯利(封顶)={stats.get('half_kelly_capped')}")

    # ── 单调性修正 ────────────────────────────────────────────────
    # 原始回测样本噪音很大（例如低波动大盘股恰好落在低分档，win/loss比好看但不代表
    # "该给更大仓位"）。这里从最低档到最高档做累计最大值，强制"分数越高建议仓位不降低"，
    # 避免出现"Avoid档凯利仓位比Buy档还大"这种会误导实际操作的结果。
    ordered_labels = [label for label, _, _ in reversed(RATING_BANDS)]  # Avoid -> Strong Buy
    running_max = 0.0
    for label in ordered_labels:
        raw = bands[label].get("half_kelly_capped", 0.0) or 0.0
        running_max = max(running_max, raw)
        bands[label]["half_kelly_monotonic"] = round(running_max, 4)
        if abs(running_max - raw) > 1e-9:
            bands[label]["monotonic_adjusted"] = True
            print(f"  ⚠️ [{label}] 原始半凯利{raw}低于更低档，已上调为{running_max}（单调性修正）")

    report = {
        "generated_at":        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method":              f"窗口={WINDOW}交易日(~1季度)，步长={STEP}交易日，回看={LOOKBACK}，池化统计",
        "kelly_cap":           KELLY_CAP,
        "methodology_caveat":  METHODOLOGY_CAVEAT,
        "bands":               bands,
        "skipped":             per_ticker_note,
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已保存: {OUT_PATH}")
    return report


if __name__ == "__main__":
    run_backtest()
