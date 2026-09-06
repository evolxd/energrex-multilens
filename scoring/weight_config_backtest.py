"""
5维评分权重回测 — weight_config_backtest.py
=================================
跟 kelly_backtest.py 同一套近似回测方法（当前分数 + 该股票自己过去2年价格池化），
但这里比较的不是"最终评级档"，而是几种不同的【权重方案】，看当前 WEIGHT_CONFIG
（scoring_engine.py 里按公司类型配的估值/成长/质量/AI暴露/预期差权重）比起
更简单的替代方案（等权、单一维度）是不是真的能把股票分得更开：

  1. current_final       — app 实际展示的 final_综合得分（含风险扣分/动量调整）
  2. category_weighted    — 只用 WEIGHT_CONFIG 按公司类型加权的5维合成，不含风险/动量调整
  3. equal_weighted       — 5维等权（各0.20），不分类型
  4. valuation_only / growth_only / quality_only / ai_exposure_only / expectation_gap_only
                          — 单一维度当排名依据，看哪个维度自己最有区分度

方法：每个方案把股票按分数分成三档（Top/Mid/Bottom tertile），
把同档股票各自过去2年的季度滚动远期收益池化，比较头尾两档的收益差（spread）。

⚠️ 方法论说明（与 kelly_backtest.py 同源，必须读，也原样写进输出 JSON 的
"methodology_caveat" 字段）：
这不是因果回测——用的是【当前】各维度分数 + 该股票自己过去2年的价格波动，
不是"某个历史时点打这个分，之后真实涨跌多少"。存在生存者偏差：现在打高分的
股票，很大程度上是因为过去基本面/股价表现好。
但这里所有方案共享同一批股票、同一批历史收益样本，只是分档方式不同——
生存者偏差对每个方案的沾染程度大致相同，所以方案之间的【相对】表现差异
（哪个方案的头尾分档收益差更大）比单个方案的绝对胜率数字更有参考意义。
即便如此，这仍然只能说明"在这批被生存者偏差污染的样本里，哪种分档方式看起来
区分度更高"，不能证明该权重配置对未来真的有预测力。真正的验证需要
kelly_snapshot_logger 积累的 (日期,ticker,各维度分数,价格) 真实历史快照
（见 scoring/kelly_backtest.py 顶部注释和 scoring/price_boundary_backtest.py 的
数据充分性门槛），目前 data/score_snapshots.csv 里的快照天数远不够。

用法:
  python scoring/weight_config_backtest.py
  python scoring/weight_config_backtest.py --optimize        # 额外网格搜索最优权重（step=0.05）
  python scoring/weight_config_backtest.py --optimize 0.02   # 更细粒度（组合数暴涨，会慢很多）

⚠️ 需要真实网络访问 yfinance.com 才能跑（拉取 LOOKBACK 历史价格）。在网络受限的
沙箱/云端会话里会直接报 403/连接失败——本仓库这次改动是在这样一个环境里做的，
没能力实际跑通它、拿到真实数字，只能在这写好代码，交给能访问 yfinance 的机器
（例如本机）去跑。
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import yfinance as yf

from kelly_backtest import quarterly_forward_returns
from scoring_engine import WEIGHT_CONFIG, get_category

ROOT      = pathlib.Path(__file__).parent.parent
CSV_PATH  = ROOT / "results_validated.csv"
OUT_PATH  = pathlib.Path(__file__).with_name("weight_config_backtest_report.json")

LOOKBACK  = "2y"   # 与 kelly_backtest.py 保持一致，便于结果互相印证
N_TERTILES = 3

DIMENSION_COLUMNS = {
    "valuation":       "val_估值得分(PEG/EV/ERG/PE/FCFYld)",
    "growth":          "grw_成长得分(营收/EPS/FCF/指引增速)",
    "quality":         "qlt_质量得分(毛利率/FCF率/ROIC/负债)",
    "ai_exposure":      "ai_AI暴露得分(AI营收/平台/订单占比)",
    "expectation_gap": "exp_预期差得分(超预期营收EPS指引)",
}
FINAL_SCORE_COLUMN = "final_综合得分(0-100)"
PRICE_COLUMN       = "raw_current_price_yf"

METHODOLOGY_CAVEAT = (
    "近似回测，非因果验证：用【当前】各维度分数 + 该股票自身近2年价格波动池化统计，"
    "不是\"历史某时点打分后的真实后续收益\"。存在生存者偏差——现在的高分股，"
    "很大程度上是因为过去表现好才被打高分。但本报告里所有方案共享同一批股票、"
    "同一批历史收益样本，只是分档方式不同，生存者偏差对每个方案的沾染程度大致相同——"
    "所以方案之间的【相对】表现差异（谁的头尾分档收益差更大）比绝对胜率数字更有参考意义，"
    "但仍不能证明该权重配置对未来真的有预测力。真正的验证需要 kelly_snapshot_logger "
    "积累几个月的真实(日期,各维度分数,价格)快照后才能做，目前 data/score_snapshots.csv "
    "里的快照天数远不够（详见 price_boundary_backtest.py 的数据充分性门槛）。"
)


def _load_universe() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH)
    needed = ["ticker", FINAL_SCORE_COLUMN, PRICE_COLUMN, *DIMENSION_COLUMNS.values()]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"results_validated.csv 缺少列: {missing}")
    df = df[needed].dropna(subset=["ticker", FINAL_SCORE_COLUMN])
    df = df.rename(columns={FINAL_SCORE_COLUMN: "current_final", PRICE_COLUMN: "price"})
    df = df.rename(columns={v: k for k, v in DIMENSION_COLUMNS.items()})
    return df.reset_index(drop=True)


def _category_weighted_score(row: pd.Series) -> float | None:
    try:
        weights = WEIGHT_CONFIG[get_category(row["ticker"])]
    except Exception:
        return None
    dims = ["valuation", "growth", "quality", "ai_exposure", "expectation_gap"]
    if any(pd.isna(row[d]) for d in dims):
        return None
    return (
        row["valuation"]       * weights.valuation
        + row["growth"]         * weights.growth
        + row["quality"]        * weights.quality
        + row["ai_exposure"]    * weights.ai_exposure
        + row["expectation_gap"] * weights.expectation_gap
    )


def _fetch_forward_returns(tickers: list[str]) -> tuple[dict[str, list[float]], dict[str, str]]:
    """每只股票只拉一次价格、算一次远期收益样本，供所有权重方案复用。"""
    returns: dict[str, list[float]] = {}
    notes: dict[str, str] = {}
    print(f"共 {len(tickers)} 只股票，开始拉取 {LOOKBACK} 历史价格（只拉一次，供所有权重方案复用）...")
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period=LOOKBACK)
            close = hist["Close"].dropna()
            rets = quarterly_forward_returns(close)
            if len(rets) < 3:
                notes[ticker] = "价格历史不足，跳过"
                continue
            returns[ticker] = rets
            print(f"  {ticker:6s} 样本数={len(rets)}")
        except Exception as e:
            notes[ticker] = f"拉取失败: {e}"
            print(f"  {ticker:6s} 失败: {e}")
    return returns, notes


def _tertile_stats(df: pd.DataFrame, score_col: str, returns: dict[str, list[float]]) -> dict:
    valid = df.dropna(subset=[score_col])
    valid = valid[valid["ticker"].isin(returns.keys())]
    if len(valid) < N_TERTILES * 3:
        return {"insufficient_universe": True, "n_tickers": len(valid)}

    ranked = valid.sort_values(score_col, ascending=False).reset_index(drop=True)
    n = len(ranked)
    edges = [0, n // 3, 2 * n // 3, n]
    labels = ["top_tertile", "mid_tertile", "bottom_tertile"]

    bands: dict[str, dict] = {}
    for label, lo, hi in zip(labels, edges[:-1], edges[1:]):
        chunk = ranked.iloc[lo:hi]
        pooled: list[float] = []
        for t in chunk["ticker"]:
            pooled.extend(returns[t])
        if pooled:
            wins = [r for r in pooled if r > 0]
            bands[label] = {
                "n_tickers":    len(chunk),
                "tickers":      chunk["ticker"].tolist(),
                "sample_size":  len(pooled),
                "win_rate":     round(len(wins) / len(pooled), 4),
                "mean_return":  round(float(np.mean(pooled)), 4),
                "median_return": round(float(np.median(pooled)), 4),
            }
        else:
            bands[label] = {"n_tickers": len(chunk), "tickers": chunk["ticker"].tolist(),
                            "sample_size": 0}

    top_mean = bands.get("top_tertile", {}).get("mean_return")
    bot_mean = bands.get("bottom_tertile", {}).get("mean_return")
    spread = (round(top_mean - bot_mean, 4)
              if top_mean is not None and bot_mean is not None else None)

    return {"n_tickers": len(valid), "bands": bands, "top_minus_bottom_spread": spread}


def _simplex_grid(dims: list[str], step: float = 0.05):
    """Yield every weight dict over `dims` on a step-granularity simplex
    (each weight a multiple of `step`, all weights summing to exactly 1.0).

    e.g. step=0.05 over 5 dims yields C(1/step + 4, 4) combinations
    (20040 for step=0.05) -- every way to split 1/step integer units among
    the 5 dims.
    """
    units = round(1.0 / step)

    def rec(remaining_units: int, remaining_dims: list[str]):
        if len(remaining_dims) == 1:
            yield {remaining_dims[0]: round(remaining_units * step, 10)}
            return
        d0, rest = remaining_dims[0], remaining_dims[1:]
        for k in range(remaining_units + 1):
            for tail in rec(remaining_units - k, rest):
                yield {d0: round(k * step, 10), **tail}

    yield from rec(units, dims)


def grid_search_optimal_weights(
    df: pd.DataFrame,
    returns: dict[str, list[float]],
    *,
    step: float = 0.05,
    top_n: int = 10,
) -> list[tuple[dict, float]]:
    """
    Grid-search the 5-dim weight simplex (valuation/growth/quality/
    ai_exposure/expectation_gap; step-granularity; always summing to 1.0)
    for the combination(s) that maximize the same top-minus-bottom tertile
    spread metric used to rank the discrete schemes in run_backtest().

    This does NOT turn the backtest into a real causal one -- it shares the
    exact same (current score, own-trailing-2y pooled quarterly return)
    proxy and the same survivorship-bias caveat (see METHODOLOGY_CAVEAT).
    It only widens the search from ~8 hand-picked schemes to every weight
    combination on the grid.

    Overfitting risk this adds on top of that caveat: with ~90 tickers and
    thousands of weight combinations tried, the single "best" combination is
    at real risk of being noise-fit to this particular universe/window
    rather than a genuinely better weighting -- report and read the top N,
    not just the winner, and treat a small margin between rank 1 and rank N
    as evidence the surface is flat (no real optimum), not as N candidates.
    """
    dims = ["valuation", "growth", "quality", "ai_exposure", "expectation_gap"]
    valid = df.dropna(subset=dims)
    valid = valid[valid["ticker"].isin(returns.keys())].reset_index(drop=True)
    n = len(valid)
    if n < N_TERTILES * 3:
        return []

    X = valid[dims].to_numpy(dtype=float)   # (n_tickers, 5)
    sum_ret = np.array([sum(returns[t]) for t in valid["ticker"]])
    cnt_ret = np.array([len(returns[t]) for t in valid["ticker"]])

    combos = list(_simplex_grid(dims, step=step))
    W = np.array([[w[d] for d in dims] for w in combos])   # (n_combos, 5)
    scores = X @ W.T                                          # (n_tickers, n_combos)

    edge_top, edge_bot = n // 3, 2 * n // 3
    results: list[tuple[dict, float]] = []
    for j in range(scores.shape[1]):
        order = np.argsort(-scores[:, j])   # descending
        top_idx, bot_idx = order[:edge_top], order[edge_bot:]
        top_n_ret, bot_n_ret = cnt_ret[top_idx].sum(), cnt_ret[bot_idx].sum()
        if top_n_ret == 0 or bot_n_ret == 0:
            continue
        # Round each mean to 4dp before subtracting, matching _tertile_stats'
        # convention exactly (it rounds mean_return per band, then rounds the
        # spread again) -- otherwise this can disagree with the discrete-scheme
        # spreads above by 1 in the last digit on values near a rounding edge.
        top_mean = round(float(sum_ret[top_idx].sum() / top_n_ret), 4)
        bot_mean = round(float(sum_ret[bot_idx].sum() / bot_n_ret), 4)
        results.append((combos[j], round(top_mean - bot_mean, 4)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_n]


def run_backtest(optimize_step: float | None = None) -> dict:
    df = _load_universe()
    df["category_weighted"] = df.apply(_category_weighted_score, axis=1)
    df["equal_weighted"] = df[["valuation", "growth", "quality", "ai_exposure", "expectation_gap"]].mean(axis=1)

    returns, skipped = _fetch_forward_returns(df["ticker"].tolist())

    schemes = {
        "current_final":            "current_final",
        "category_weighted":        "category_weighted",
        "equal_weighted":           "equal_weighted",
        "valuation_only":           "valuation",
        "growth_only":              "growth",
        "quality_only":             "quality",
        "ai_exposure_only":         "ai_exposure",
        "expectation_gap_only":     "expectation_gap",
    }

    results = {}
    for name, col in schemes.items():
        results[name] = _tertile_stats(df, col, returns)
        spread = results[name].get("top_minus_bottom_spread")
        print(f"\n[{name}] 头尾分档收益差(spread) = {spread}")

    ranked_by_spread = sorted(
        (
            (name, r["top_minus_bottom_spread"])
            for name, r in results.items()
            if r.get("top_minus_bottom_spread") is not None
        ),
        key=lambda x: x[1],
        reverse=True,
    )

    report = {
        "generated_at":        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method":              f"每方案把 {len(df)} 只股票按分数分3档(tertile)，"
                                f"池化每只股票自身近{LOOKBACK}的季度滚动远期收益，比较头尾分档均值差",
        "methodology_caveat":  METHODOLOGY_CAVEAT,
        "schemes":             results,
        "ranked_by_spread":    ranked_by_spread,
        "skipped":             skipped,
    }

    if optimize_step is not None:
        print(f"\n⏳ 网格搜索最优权重（step={optimize_step}）...")
        n_combos = math.comb(round(1 / optimize_step) + 4, 4)
        top_combos = grid_search_optimal_weights(df, returns, step=optimize_step, top_n=10)
        report["optimize_step"] = optimize_step
        report["optimizer_caveat"] = (
            f"在 methodology_caveat 的近似回测基础上叠加的额外风险：全网格搜索了 {n_combos} "
            f"种权重组合，参与打分的样本股票只有约 {len(df)} 只，"
            "\"最优\"组合有很大概率是在噪声上过拟合，不是真的更好的权重配置。"
            "请看前10名之间的spread差距有多小——差距越小，说明这个曲面越平，"
            "越不能只信第一名。"
        )
        report["top_weight_combos"] = [
            {"weights": w, "top_minus_bottom_spread": s} for w, s in top_combos
        ]
        print("\n网格搜索 Top 10 权重组合（仅供近似参考，见 optimizer_caveat）：")
        for w, s in top_combos:
            w_str = " / ".join(f"{k}={v:.2f}" for k, v in w.items())
            print(f"  spread={s:7.4f}  {w_str}")
        if top_combos:
            best_spread, worst_of_top = top_combos[0][1], top_combos[-1][1]
            print(f"\nTop1 vs Top10 spread差距: {round(best_spread - worst_of_top, 4)}"
                  f"（越小说明曲面越平，最优解越不可信）")

    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已保存: {OUT_PATH}")
    print("\n按头尾分档收益差排名（仅供近似参考，见 methodology_caveat）：")
    for name, spread in ranked_by_spread:
        print(f"  {name:25s} spread={spread}")

    from methodology_trend import append_trend_row
    append_trend_row({
        "current_final_spread": results.get("current_final", {}).get("top_minus_bottom_spread"),
        "category_weighted_spread": results.get("category_weighted", {}).get("top_minus_bottom_spread"),
        "growth_only_spread": results.get("growth_only", {}).get("top_minus_bottom_spread"),
        "valuation_only_spread": results.get("valuation_only", {}).get("top_minus_bottom_spread"),
    })
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="5维评分权重回测（近似方法，见文件顶部注释）")
    parser.add_argument(
        "--optimize", type=float, nargs="?", const=0.05, default=None, metavar="STEP",
        help="额外跑一次网格搜索找 spread 最大的权重组合，STEP 为权重粒度（默认0.05，"
             "即5%%档位；更小的STEP组合数按 C(1/STEP+4,4) 增长，0.05→20040组合，"
             "0.02→3162510组合会很慢，不建议低于0.02）",
    )
    args = parser.parse_args()
    run_backtest(optimize_step=args.optimize)
