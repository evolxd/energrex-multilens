#!/usr/bin/env python3
"""核对 TICKER_CIK 里每个编号真的属于那只票。

为什么需要这个：CIK 错一位不会报错。`fetch_xbrl_facts` 照样返回 200，
`_quarterly_segment_revenues` 照样解析出分部收入，页面照样画出图——只不过
画的是另一家公司的财报。这类错误没有任何症状，只能主动去核。

2026-09-21 补进表里的 9 个 CIK 都不是一手核对来的：取数环境连不上 sec.gov
（出口代理对 www/efts/data.sec.gov 三个域一律 403 拒绝 CONNECT，不是缺
User-Agent，是网络策略），只能靠 Alpha Vantage 返回的编号 + 公司名旁证，
SPCX 那条更是靠检索出来的多条间接证据凑的。这个脚本就是留给能连 EDGAR 的
机器跑的那一步。

用法：

    set SEC_USER_AGENT=ENERGREX research your@email.com     ← Windows
    export SEC_USER_AGENT="ENERGREX research your@email.com" ← macOS/Linux
    python scripts/verify_cik.py

SEC 要求 User-Agent 里带可联系的邮箱，否则拒绝请求——这不是这个项目的规定。

退出码 0 = 全部对得上；1 = 有对不上的（详情打在表里）。
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scoring.edgar_fetcher import TICKER_CIK, _BASE, _get  # noqa: E402

#: SEC 每秒最多 10 次请求，官方要求。留足余量。
_SLEEP = 0.15


def verdict(ticker: str, payload: dict | None) -> tuple[str, str, str]:
    """(状态, SEC 登记的公司名, 说明)。

    纯函数，不联网——所以判定规则能被测试钉住，不用打桩 SEC。

    判据是 SEC 自己登记的 tickers 列表里有没有这只票，不是拿公司名去模糊
    匹配：名字会变（Rudolph Technologies → Onto Innovation、Facebook → Meta），
    ticker 列表是 SEC 维护的结构化字段，比名字可靠。
    """
    if payload is None:
        return "取数失败", "", "连不上 EDGAR 或该 CIK 不存在——这一条没核到，不等于对"

    name = str(payload.get("name") or "").strip()
    tickers = [str(t).strip().upper() for t in (payload.get("tickers") or [])]

    if not tickers:
        # 有些实体（尤其刚上市的）submissions 里 tickers 可能是空的
        return "无法判定", name, "SEC 没登记 ticker 列表，只能人工看公司名对不对"
    if ticker.upper() in tickers:
        return "✓", name, ""
    return "✗ 不匹配", name, f"SEC 登记的是 {'/'.join(tickers)}，不是 {ticker}"


def fetch(cik: str) -> dict | None:
    r = _get(f"{_BASE}/submissions/CIK{cik}.json", timeout=30)
    return r.json() if r else None


def main() -> int:
    if not os.getenv("SEC_USER_AGENT", "").strip():
        print("没有设 SEC_USER_AGENT，SEC 会拒绝所有请求。见本文件开头的用法。")
        return 2

    rows, bad = [], 0
    for ticker, cik in sorted(TICKER_CIK.items()):
        status, name, note = verdict(ticker, fetch(cik))
        if status.startswith("✗") or status == "取数失败":
            bad += 1
        rows.append((ticker, cik, status, name[:44], note))
        time.sleep(_SLEEP)

    # 下限 16：全部取数失败时公司名一列全是空串，宽度会塌成 0，表头
    # 「SEC 登记的公司名」直接跟「说明」粘在一起。
    width = max([len(r[3]) for r in rows] + [16])
    print(f"\n{'标的':<7}{'CIK':<13}{'状态':<10}{'SEC 登记的公司名':<{width + 2}}说明")
    print("─" * (36 + width + 20))
    for ticker, cik, status, name, note in rows:
        print(f"{ticker:<7}{cik:<13}{status:<10}{name:<{width + 2}}{note}")

    print()
    if bad:
        print(f"有 {bad} 条没对上。对不上的从 scoring/edgar_fetcher.py 的 "
              f"TICKER_CIK 里删掉或改正——错的 CIK 比没有 CIK 更危险，"
              f"它会安静地画出另一家公司的财报。")
    else:
        print(f"{len(rows)} 条全部对上。")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
