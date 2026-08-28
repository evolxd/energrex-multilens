"""评分方法论健康检查 — 趋势记录
=================================
weight_config_backtest.py / spread_decomposition_backtest.py /
risk_subcomponent_backtest.py 这三个脚本各自只覆盖写自己的 *_report.json，
每次重跑都会丢掉上一次的数字——单独看任何一次报告都答不出"这个数字是在变好
还是在变差"。这个模块只做一件事：每次那三个脚本跑完，把关键数字追加进
methodology_trend.csv 的新一行，不覆盖旧行。

追踪的核心参数是"头尾分档收益差"（tertile spread）——评分把未来涨得好/差的
股票分开的能力，不是评分本身高不高。

用法（在三个backtest脚本各自main()末尾调用）：
    from methodology_trend import append_trend_row
    append_trend_row({"current_final_spread": -0.0701, ...})
"""
from __future__ import annotations

import csv
import pathlib
from datetime import datetime

TREND_LOG = pathlib.Path(__file__).with_name("methodology_trend.csv")

FIELDS = [
    "checked_date",
    "current_final_spread",
    "category_weighted_spread",
    "growth_only_spread",
    "valuation_only_spread",
    "profile_plus_momentum_spread",
    "plus_momentum_minus_risk_spread",
    "beta_spread",
    "volatility_30d_spread",
    "valuation_risk_spread",
    "liquidity_risk_spread",
    "max_drawdown_spread",
]


def append_trend_row(values: dict[str, float | None]) -> None:
    """Merge into today's row, not a fresh row per call.

    The three backtest scripts each compute a different subset of these
    fields and are meant to be run together (see
    Update-WeeklyMethodologyChecks.ps1) -- three separate calls the same
    day should produce one row with all their fields filled in, not three
    sparse rows that have to be mentally stitched back together to see
    one point in time. Missing keys stay blank rather than backfilled or
    guessed -- a script that only computes some of these fields should
    never silently claim a value for the ones it didn't check.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    rows = read_trend()
    row_by_date = {r["checked_date"]: r for r in rows}
    row = row_by_date.get(today, {"checked_date": today})
    for k in FIELDS:
        if k == "checked_date":
            continue
        if values.get(k) is not None:
            row[k] = values[k]
    row_by_date[today] = row

    with TREND_LOG.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for date in sorted(row_by_date):
            writer.writerow({k: row_by_date[date].get(k, "") for k in FIELDS})


def read_trend() -> list[dict]:
    if not TREND_LOG.exists():
        return []
    with TREND_LOG.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = read_trend()
    if not rows:
        print("还没有记录。三个backtest脚本跑一次之后这里就会有数据。")
    else:
        print(f"共 {len(rows)} 条记录：\n")
        for r in rows:
            print(f"  {r['checked_date']}  current_final_spread={r.get('current_final_spread')}")
