"""风险子项拆解回测 — risk_subcomponent_backtest.py
=================================
跟 spread_decomposition_backtest.py 同一条调查线的下一步：那份报告定位到
"风险扣分"这一层是 current_final 负spread的主要拖累来源，但"风险扣分"本身是
5个子项加权合成的（beta/波动率/估值风险/流动性风险/最大回撤），谁是真正的
拖累来源还没拆开看。这份脚本把5个子项各自单独当"安全分"来排，看安全档
（低风险）vs 危险档（高风险）的头尾分档远期收益差——用来确认具体是哪个
子项在跟远期收益反着来。

⚠️ 方法论说明（与同系列脚本同源，必须读）：
跟 weight_config_backtest.py / spread_decomposition_backtest.py 一样，这是
近似回测，存在生存者偏差，不是因果验证——见那两份文件顶部的完整说明。

发现（2026-08-27，93只票，2年历史）：
  beta            (实时yfinance)      spread=-0.1485  最大拖累
  volatility_30d  (静态mock_data.py)  spread=-0.1404  第二大拖累
  valuation_risk  (静态mock_data.py)  spread=+0.0701  方向正常
  liquidity_risk  (静态mock_data.py)  spread=-0.0762
  max_drawdown    (实时yfinance)      spread=+0.0661  方向正常

结论：真正的拖累来自 beta 和 volatility_30d，不是最初怀疑的主观估值风险字段
（valuation_risk 实际方向是对的）。但 beta/波动率跟远期收益负相关，在这段
AI概念股主导的强势上涨样本期里是有文献记载的正常现象（"低波动率异象"的
反向表现——高beta股票在动量驱动的牛市里跑赢，这个规律会随行情反转）。
**不建议因为这份报告就调低beta/波动率的权重**——这套风险扣分本来就是设计
用来在行情不利时保护你的，用一段单边上涨期的数据去削弱它，等于在拟合一个
特定行情样本，损害它本该发挥作用的那个时刻的保护力。真正该等的是
kelly_snapshot_logger 积累跨越不同市场环境（尤其包含一段真实下跌/震荡期）
的数据后，再看这套风险扣分在真正需要保护的时候有没有用。

用法:
  python scoring/risk_subcomponent_backtest.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import yfinance as yf

from kelly_backtest import quarterly_forward_returns
from mock_data import MOCK_STOCKS
from quant_engine import _RISK_BASELINES, normalize_score

ROOT = pathlib.Path(__file__).parent.parent
CSV_PATH = ROOT / "results_validated.csv"
OUT_PATH = pathlib.Path(__file__).with_name("risk_subcomponent_report.json")
LOOKBACK = "2y"

METHODOLOGY_CAVEAT = (
    "近似回测，非因果验证，与 weight_config_backtest.py / "
    "spread_decomposition_backtest.py 同源同局限（生存者偏差、非前瞻式）。"
    "beta/volatility_30d 跟远期收益负相关，在动量驱动的上涨样本期是预期内的"
    "现象，不代表这两项的风险扣分设计有误——不要仅凭这份报告调整它们的权重，"
    "见本文件顶部docstring的完整讨论。"
)

SOURCE = {
    "beta": "live(yfinance)",
    "volatility_30d": "static(mock_data.py)",
    "valuation_risk": "static(mock_data.py)",
    "liquidity_risk": "static(mock_data.py)",
    "max_drawdown": "live(yfinance)",
}


def _num(s):
    if pd.isna(s):
        return None
    m = re.search(r"[-+]?\d*\.?\d+", str(s))
    if not m:
        return None
    v = float(m.group())
    return v / 100.0 if "%" in str(s) else v


def _load_universe() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH)
    df = df.rename(columns={
        "raw_beta_贝塔系数": "beta",
        "raw_max_dd_1y_1年最大回撤": "max_drawdown",
        "final_综合得分(0-100)": "current_final",
    })
    df["beta"] = df["beta"].apply(_num)
    df["max_drawdown"] = df["max_drawdown"].apply(_num)
    df["volatility_30d"] = df["ticker"].map(lambda t: MOCK_STOCKS.get(t, {}).get("volatility_30d"))
    df["valuation_risk"] = df["ticker"].map(lambda t: MOCK_STOCKS.get(t, {}).get("valuation_risk"))
    df["liquidity_risk"] = df["ticker"].map(lambda t: MOCK_STOCKS.get(t, {}).get("liquidity_risk"))

    for comp, cfg in _RISK_BASELINES.items():
        v = df[comp].abs() if comp == "max_drawdown" else df[comp]
        df[f"safety_{comp}"] = v.apply(
            lambda x, cfg=cfg: normalize_score(x, cfg["best"], cfg["worst"], cfg["dir"])
            if pd.notna(x) else None
        )
    return df


def _fetch_forward_returns(tickers: list[str]) -> dict[str, list[float]]:
    returns: dict[str, list[float]] = {}
    print(f"共 {len(tickers)} 只股票，开始拉取 {LOOKBACK} 历史价格...")
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period=LOOKBACK)
            rets = quarterly_forward_returns(hist["Close"].dropna())
            if len(rets) >= 3:
                returns[t] = rets
        except Exception as e:
            print(f"  {t}: 拉取失败 ({e})")
    return returns


def _tertile_spread(df: pd.DataFrame, col: str, returns: dict[str, list[float]]) -> dict:
    valid = df.dropna(subset=[col])
    valid = valid[valid["ticker"].isin(returns.keys())]
    n = len(valid)
    if n < 9:
        return {"insufficient_universe": True, "n_tickers": n}
    ranked = valid.sort_values(col, ascending=False).reset_index(drop=True)  # 高安全分排前面
    top = ranked.iloc[: n // 3]["ticker"]
    bot = ranked.iloc[2 * n // 3:]["ticker"]
    top_r = [r for t in top for r in returns[t]]
    bot_r = [r for t in bot for r in returns[t]]
    return {"n_tickers": n, "spread": round(float(np.mean(top_r) - np.mean(bot_r)), 4)}


def main() -> None:
    df = _load_universe()
    returns = _fetch_forward_returns(df.dropna(subset=["current_final"])["ticker"].tolist())
    print(f"获得可用收益样本的股票数: {len(returns)}\n")

    results = {}
    for comp in _RISK_BASELINES:
        results[f"{comp} ({SOURCE[comp]})"] = _tertile_spread(df, f"safety_{comp}", returns)

    report = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "methodology_caveat": METHODOLOGY_CAVEAT,
        "results": results,
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'子项 (数据来源)':40s}  安全档-危险档收益差")
    print("-" * 65)
    for name, r in results.items():
        if r.get("insufficient_universe"):
            print(f"{name:40s}  样本不足(n={r['n_tickers']})")
        else:
            print(f"{name:40s}  {r['spread']:+.4f}   n={r['n_tickers']}")
    print(f"\n已保存: {OUT_PATH}")


if __name__ == "__main__":
    main()
