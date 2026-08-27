"""层级拆解回测 — spread_decomposition_backtest.py
=================================
跟 kelly_backtest.py / weight_config_backtest.py 同一套近似回测方法（当前分数 +
该股票自己过去2年价格池化），但这里不是比较权重方案，是把 current_final 层层
拆解回 category_weighted，一层一层加回动量/风险扣分/AI加成/熔断乘数，看头尾
分档收益差(spread)在哪一层被拉低——用来定位"综合分数为什么跑输更简单的子分数"
这个问题具体出在哪个环节，而不是停留在"综合分不如单维度"这个笼统结论上。

背景（2026-08-27 发现）：weight_config_backtest.py 显示 current_final 的 spread
是负的，且比只用5维静态权重的 category_weighted 明显更差。本脚本把中间步骤拆
出来后发现：
  - 换成生产实际用的 ai_profile 权重表（跟 WEIGHT_CONFIG 不同）：影响很小
  - 加回动量维度：spread 转正——动量不是问题
  - 加回风险扣分：spread 从正转负——**风险扣分是目前定位到的主要拖累来源**
  - 加回AI加成/熔断乘数：小幅进一步走低，但不是主因
这个拆解本身还是近似回测（生存者偏差、非因果，见下方 methodology_caveat），
用于"往哪个方向查代码"的定位，不是最终定论。

用法:
  python scoring/spread_decomposition_backtest.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import yfinance as yf

from ai_profile import AI_CORE, PROFILE_WEIGHTS
from formula import CIRCUIT_MULTIPLIER
from kelly_backtest import quarterly_forward_returns
from scoring_engine import WEIGHT_CONFIG, get_category

ROOT = pathlib.Path(__file__).parent.parent
CSV_PATH = ROOT / "results_validated.csv"
OUT_PATH = pathlib.Path(__file__).with_name("spread_decomposition_report.json")

LOOKBACK = "2y"
N_TERTILES = 3

DIM_COLS = {
    "valuation": "val_估值得分(PEG/EV/ERG/PE/FCFYld)",
    "growth": "grw_成长得分(营收/EPS/FCF/指引增速)",
    "quality": "qlt_质量得分(毛利率/FCF率/ROIC/负债)",
    "ai_exposure": "ai_AI暴露得分(AI营收/平台/订单占比)",
    "expectation_gap": "exp_预期差得分(超预期营收EPS指引)",
    "momentum": "mom_动量得分(RSI14/价格vs200日均)",
    "risk_penalty": "risk_风险扣分(max20,Beta/回撤/负债)",
    "circuit": "circuit_熔断(Beta>2.2且回撤>35%或DE>1.8)",
    "ai_bonus": "aibonus_AI加速器",
}
FINAL_COL = "final_综合得分(0-100)"
PRICE_COL = "raw_current_price_yf"

METHODOLOGY_CAVEAT = (
    "近似回测，非因果验证：用【当前】各层分数 + 该股票自身近2年价格波动池化统计，"
    "不是\"历史某时点打分后的真实后续收益\"。存在生存者偏差，且跟 "
    "weight_config_backtest.py 共享同样的局限——见该文件顶部的完整方法论说明。"
    "本脚本的用途是定位\"哪一层拖累了排序\"，不是证明任何一层的长期有效性；"
    "尤其不该反过来拿这份报告的结果去反推调低某层权重——那是在拟合一个"
    "被生存者偏差污染的单一样本，跟这次要避免的错误是同一类错误。"
)

_MAIN_DIMS = ["valuation", "growth", "quality", "ai_exposure", "expectation_gap"]
_PW = PROFILE_WEIGHTS[AI_CORE]  # 目前四种 profile 权重表完全相同


def _load_universe() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH)
    needed = ["ticker", FINAL_COL, PRICE_COL, *DIM_COLS.values()]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"results_validated.csv 缺少列: {missing}")
    df = df[needed].dropna(subset=["ticker", FINAL_COL])
    df = df.rename(columns={FINAL_COL: "current_final", PRICE_COL: "price"})
    df = df.rename(columns={v: k for k, v in DIM_COLS.items()})
    return df.reset_index(drop=True)


def _category_weighted(row: pd.Series) -> float | None:
    try:
        w = WEIGHT_CONFIG[get_category(row["ticker"])]
    except Exception:
        return None
    if any(pd.isna(row[d]) for d in _MAIN_DIMS):
        return None
    return sum(row[d] * getattr(w, d) for d in _MAIN_DIMS)


def _profile_5dim(row: pd.Series) -> float | None:
    if any(pd.isna(row[d]) for d in _MAIN_DIMS):
        return None
    s = sum(_PW[d] for d in _MAIN_DIMS)
    return sum(row[d] * _PW[d] / s for d in _MAIN_DIMS)


def _profile_plus_momentum(row: pd.Series) -> float | None:
    dims = _MAIN_DIMS + ["momentum"]
    if any(pd.isna(row[d]) for d in dims):
        return None
    return sum(row[d] * _PW[d] for d in dims)


def _plus_momentum_minus_risk(row: pd.Series) -> float | None:
    base = _profile_plus_momentum(row)
    if base is None or pd.isna(row["risk_penalty"]):
        return None
    return base - row["risk_penalty"]


def _plus_bonus(row: pd.Series) -> float | None:
    base = _plus_momentum_minus_risk(row)
    if base is None:
        return None
    bonus = row["ai_bonus"] if not pd.isna(row["ai_bonus"]) else 0.0
    return base + max(0.0, bonus)


def _full_reconstruction(row: pd.Series) -> float | None:
    score = _plus_bonus(row)
    if score is None:
        return None
    if not pd.isna(row["circuit"]) and bool(row["circuit"]):
        score *= CIRCUIT_MULTIPLIER
    return max(0.0, min(100.0, score))


SCHEMES = [
    ("category_weighted", "静态WEIGHT_CONFIG, 5维, 无动量无风险", _category_weighted),
    ("profile_5dim", "真实ai_profile权重表, 5维, 无动量无风险", _profile_5dim),
    ("profile_plus_momentum", "+动量, 仍无风险扣分", _profile_plus_momentum),
    ("plus_momentum_minus_risk", "+动量+风险扣分, 无熔断/AI加成", _plus_momentum_minus_risk),
    ("plus_bonus", "...再+AI加成, 仍无熔断", _plus_bonus),
    ("full_reconstruction", "完整重建(+熔断乘数), 应≈current_final", _full_reconstruction),
]


def _fetch_forward_returns(tickers: list[str]) -> dict[str, list[float]]:
    returns: dict[str, list[float]] = {}
    print(f"共 {len(tickers)} 只股票，开始拉取 {LOOKBACK} 历史价格...")
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period=LOOKBACK)
            close = hist["Close"].dropna()
            rets = quarterly_forward_returns(close)
            if len(rets) >= 3:
                returns[t] = rets
        except Exception as e:
            print(f"  {t}: 拉取失败 ({e})")
    return returns


def _tertile_spread(df: pd.DataFrame, score_col: str, returns: dict[str, list[float]]) -> dict:
    valid = df.dropna(subset=[score_col])
    valid = valid[valid["ticker"].isin(returns.keys())]
    n = len(valid)
    if n < N_TERTILES * 3:
        return {"insufficient_universe": True, "n_tickers": n}
    ranked = valid.sort_values(score_col, ascending=False).reset_index(drop=True)
    top = ranked.iloc[: n // 3]["ticker"]
    bot = ranked.iloc[2 * n // 3:]["ticker"]
    top_rets = [r for t in top for r in returns[t]]
    bot_rets = [r for t in bot for r in returns[t]]
    return {
        "n_tickers": n,
        "spread": round(float(np.mean(top_rets) - np.mean(bot_rets)), 4),
    }


def main() -> None:
    df = _load_universe()
    for key, _label, fn in SCHEMES:
        df[f"scheme_{key}"] = df.apply(fn, axis=1)

    returns = _fetch_forward_returns(df["ticker"].tolist())
    print(f"获得可用收益样本的股票数: {len(returns)}\n")

    results = {
        "current_final (真实生产分数, 对照终点)": _tertile_spread(df, "current_final", returns),
    }
    for key, label, _fn in SCHEMES:
        results[f"{key} ({label})"] = _tertile_spread(df, f"scheme_{key}", returns)

    report = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "methodology_caveat": METHODOLOGY_CAVEAT,
        "results": results,
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'方案':70s}  spread")
    print("-" * 90)
    for name, r in results.items():
        if r.get("insufficient_universe"):
            print(f"{name:70s}  样本不足(n={r['n_tickers']})")
        else:
            print(f"{name:70s}  {r['spread']:+.4f}   n={r['n_tickers']}")
    print(f"\n已保存: {OUT_PATH}")


if __name__ == "__main__":
    main()
