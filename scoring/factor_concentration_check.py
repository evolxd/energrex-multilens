"""持仓因子集中度检查 — factor_concentration_check.py
=================================
回答一个仓位限额框架本身没有回答的问题：把敞口分散在N只票里，是不是真的
分散了风险，还是这N只票的走势本质上被同一个宏观因子驱动，"分散"只是名义上的。

方法：读当前真实持仓（account.db，跟仓位管理页用的是同一份数据），排除QQQ
（这是对冲仓位，不是集中度意义上的"赌注"），拉每个标的2年日收益率，按当前
净敞口占比加权后做PCA，看第一主成分解释了多少组合方差——这个数字越高，说明
组合的波动越多来自同一个共同因子，而不是各标的独立的走势。

⚠️ 局限：只测了普通股价格相关性，没有对期权仓位的非线性payoff建模（大部分
真实持仓是期权价差，不是股票本身）——这是"标的相关性"层面的集中度检查，
不是完整的期权组合风险分析。

用法:
  python scoring/factor_concentration_check.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import yfinance as yf

from account.db import db
from scoring.position_exposure import compute_exposures

OUT_PATH = pathlib.Path(__file__).with_name("factor_concentration_report.json")
LOOKBACK = "2y"


def _load_current_exposures():
    conn = db()
    sync_time = conn.execute(
        "SELECT sync_time FROM positions ORDER BY sync_time DESC LIMIT 1"
    ).fetchone()
    sync_time = sync_time[0] if sync_time else None
    positions = (
        [dict(r) for r in conn.execute(
            "SELECT symbol, market_value FROM positions WHERE sync_time=?", (sync_time,)
        )] if sync_time else []
    )
    options = [dict(r) for r in conn.execute("SELECT symbol, market_value FROM options_positions")]
    bal = conn.execute(
        "SELECT total_equity, cash_balance FROM account_balance ORDER BY sync_time DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not bal:
        return None
    equity, cash = float(bal[0]), float(bal[1] or 0.0)
    return compute_exposures(positions, equity, cash, lambda s: None, options)


def main() -> None:
    exposures = _load_current_exposures()
    if exposures is None or not exposures.by_ticker_pct:
        print("没有可用的持仓数据，跳过。")
        return

    weights = {t: pct for t, pct in exposures.by_ticker_pct.items() if t != "QQQ"}
    tickers = sorted(weights)
    if len(tickers) < 3:
        print(f"标的数量太少(n={len(tickers)})，因子集中度分析没有意义，跳过。")
        return

    print(f"当前净敞口 (排除QQQ对冲): {weights}")
    print(f"拉取 {len(tickers)} 只标的 {LOOKBACK} 日线...")

    closes = {}
    for t in tickers:
        try:
            closes[t] = yf.Ticker(t).history(period=LOOKBACK)["Close"].dropna()
        except Exception as e:
            print(f"  {t}: 拉取失败 ({e})")

    df = pd.DataFrame(closes).dropna()
    rets = df.pct_change().dropna()
    if len(rets) < 30:
        print("有效交易日数太少，跳过。")
        return

    corr = rets.corr()
    w = np.array([weights[t] for t in tickers if t in rets.columns])
    cols = [t for t in tickers if t in rets.columns]
    w = w / w.sum()
    weighted_rets = rets[cols] * w
    cov = np.cov(weighted_rets.values.T)
    eigvals = np.linalg.eigvalsh(cov)[::-1]
    pc1_share = float(eigvals[0] / eigvals.sum()) if eigvals.sum() > 0 else None
    avg_corr = float((corr.values.sum() - len(cols)) / (len(cols) * (len(cols) - 1)))

    report = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": (
            "只测普通股价格相关性，没有对期权非线性payoff建模——见本文件顶部docstring局限说明。"
        ),
        "weights_pct_excl_qqq": weights,
        "correlation_matrix": corr.round(3).to_dict(),
        "pc1_variance_share": round(pc1_share, 4) if pc1_share is not None else None,
        "avg_pairwise_correlation": round(avg_corr, 4),
        "n_trading_days": len(rets),
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n两两相关系数矩阵:")
    print(corr.round(2).to_string())
    print(f"\n持仓加权第一主成分(PC1)解释组合方差: {pc1_share*100:.1f}%")
    print(f"平均两两相关系数: {avg_corr:.3f}")
    print(f"\n已保存: {OUT_PATH}")


if __name__ == "__main__":
    main()
