"""持仓历史压力测试 — position_stress_test.py
=================================
仓位硬约束（单股/产业链上限）现在是圆整的百分比数字，没有接回过任何真实历史
场景验证过"这个上限在真正的下跌行情里到底能不能保护你"。这个脚本把当前真实
持仓（account.db）的净敞口，套进三段真实历史压力窗口（2022加息熊市、2020
COVID崩盘、2018 Q4），看每个标的自己的历史最大回撤，按当前净敞口占比重新
加权后估算组合层面的回撤。

⚠️ 两个必须读的局限：
1. 数据覆盖缺口不做假装：ARM(2023上市)/PLTR(2020上市)/SNDK(2025重新分拆
   上市)这类近期标的，在较早的压力窗口里没有真实历史数据——脚本会明确报告
   哪些标的被排除、排除的部分占了多少净敞口，不会用同行业/同板块的其他股票
   顶替，那样等于制造一个看起来完整实则编造的数字。
2. 这是线性近似，不是真正的期权组合压力测试：真实持仓大部分是期权价差
   （买卖权组合），实际盈亏是非线性的（takes into account strike/到期/
   隐含波动率变化），不是标的价格跌多少、敞口就跌多少。这里把"净敞口(按
   市值)"当成线性股票仓位来估算，结果只能当作方向性参考，不是精确的期权
   压力测算——真正要做后者需要对每张期权重新定价，工作量大得多，这里没做。

用法:
  python scoring/position_stress_test.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import yfinance as yf

from account.db import db
from scoring.position_exposure import compute_exposures

OUT_PATH = pathlib.Path(__file__).with_name("position_stress_report.json")

WINDOWS = {
    "2022_rate_hike_bear": ("2022-01-03", "2022-10-14"),
    "2020_covid_crash": ("2020-02-19", "2020-03-23"),
    "2018_q4_selloff": ("2018-10-01", "2018-12-24"),
}


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
    print(f"当前净敞口 (排除QQQ对冲): {weights}")
    print(f"现金比例: {exposures.cash_pct:.1f}%\n")

    full_hist = {}
    for t in tickers:
        try:
            full_hist[t] = yf.Ticker(t).history(period="max")["Close"]
        except Exception as e:
            print(f"  {t}: 拉取失败 ({e})")
            full_hist[t] = pd.Series(dtype=float)

    report = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "current_weights_pct_excl_qqq": weights,
        "cash_pct": round(exposures.cash_pct, 2),
        "note": "线性近似，未对期权非线性payoff建模——见本文件docstring局限2。",
        "windows": {},
    }

    for key, (start, end) in WINDOWS.items():
        covered = []
        for t in tickers:
            h = full_hist.get(t, pd.Series(dtype=float))
            if h.empty or h.index.min().date() > pd.Timestamp(start).date():
                continue
            window = h[(h.index >= start) & (h.index <= end)]
            if len(window) < 2:
                continue
            dd = float(window.iloc[-1] / window.max() - 1.0)
            covered.append((t, dd))

        missing = [t for t in tickers if t not in [c[0] for c in covered]]
        covered_weight = sum(weights[t] for t, _ in covered)
        result = {
            "covered_tickers": {t: round(dd, 4) for t, dd in covered},
            "missing_tickers_no_history": missing,
            "missing_weight_pct": round(sum(weights[t] for t in missing), 2),
        }
        if covered:
            renorm = sum(weights[t] for t, _ in covered)
            port_dd = sum(weights[t] / renorm * dd for t, dd in covered)
            result["portfolio_drawdown_estimate"] = round(port_dd, 4)
            result["covered_weight_pct"] = round(covered_weight, 2)
        report["windows"][key] = result

        print(f"=== {key} ({start} -> {end}) ===")
        print(f"  覆盖: {[t for t,_ in covered]} (净敞口 {covered_weight:.1f}%)")
        if missing:
            print(f"  缺数据排除: {missing} (净敞口 {result['missing_weight_pct']:.1f}%)")
        if covered:
            print(f"  组合层面估算回撤 (仅覆盖部分重新归一化): {result['portfolio_drawdown_estimate']*100:+.1f}%")
        print()

    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存: {OUT_PATH}")


if __name__ == "__main__":
    main()
