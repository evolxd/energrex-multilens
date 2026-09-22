"""Option symbol parsing helpers."""

from __future__ import annotations

import re

OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def parse_occ(symbol: str) -> dict:
    """Parse an OCC option symbol.

    `direction` is kept for legacy callers and means option type, not position
    side. New code should prefer `option_type` or `call_put`.
    """
    match = OCC_RE.match(symbol.strip().upper().replace(" ", ""))
    if not match:
        return {}
    root, yy, mm, dd, cp, strike_raw = match.groups()
    option_type = "call" if cp == "C" else "put"
    return {
        "root": root,
        "option_type": option_type,
        "call_put": cp,
        "direction": "Call" if cp == "C" else "Put",
        "strike": int(strike_raw) / 1000,
        "expiry": f"20{yy}-{mm}-{dd}",
    }


def parse_occ_sym(symbol: str) -> dict | None:
    """Parse an OCC option symbol into the scraped-position shape."""
    parsed = parse_occ(symbol)
    if not parsed:
        return None
    return {
        "underlying": parsed["root"],
        "expiry": parsed["expiry"],
        "strike": parsed["strike"],
        "call_put": parsed["call_put"],
    }


def option_market_value(quantity: float, price: float, multiplier: int = 100) -> float:
    """Return signed option market value; short positions remain negative."""
    return float(quantity or 0) * float(price or 0) * multiplier


#: `options_positions.direction` 这一列在历史上被三套代码写过三种词汇：
#:
#:   * "long" / "short"  —— 持仓方向，Chrome 抓取和 xlsx 导入都写这个
#:   * "Call" / "Put"    —— **期权类型**，手动录入表格的下拉框写这个
#:                          （account_monitor.py 的 SelectboxColumn("方向")）
#:   * NULL / ""         —— 早期行
#:
#: 所以不能拿这一列当"方向"无条件用。只认明确表示卖出的那几个词，其余
#: 一律当作"这一列没有方向信息"，以 quantity 自己的正负为准。
_SHORT_MARKERS = frozenset({
    "short", "s", "sell", "sold", "sell_to_open", "sto", "write", "written",
    "空", "卖", "卖出", "卖开",
})
_LONG_MARKERS = frozenset({
    "long", "l", "buy", "bought", "buy_to_open", "bto",
    "多", "买", "买入", "买开",
})


def direction_carries_side(direction: object) -> bool:
    """这一行的 `direction` 到底说没说"买还是卖"。

    诊断用：值是 "Call"/"Put"/空 时它说的是期权类型或什么都没说，这时
    quantity 的正负是**唯一**的方向来源，写错了没有第二处能纠。
    """
    text = str(direction or "").strip().lower()
    return text in _SHORT_MARKERS or text in _LONG_MARKERS


def signed_quantity(quantity: object, direction: object = None) -> float:
    """张数，卖出为负——两种存法都归一到这一种。

    为什么需要这个：写入端不统一。`account/positions_xlsx.py` 存**带符号**
    的张数（卖出为负），而 Chrome 抓取那条路（`_parse_scraped_rows`）存
    `abs(qty)` 再把方向塞进 `direction` 列。同一张表里两种行并存。

    读的那一端更不统一：价差分析（account_monitor.py `_analyze_spreads`）
    做了兼容还原，而**风险快照、组合 Greeks、对冲宽度检查三条路都直接用
    raw quantity**。后果不是少算一点——是卖方被当成买方：卖出的 put 被读
    成买入的 put，于是 Beta-Delta 的符号反了，压力测试里"大盘崩盘"变成
    赚钱。BD 净多头 356% 和"跌 20% 赚 $98,640"能同时出现在一个页面上，
    就是这么来的。

    规则：`direction` 明确说卖出、而 quantity 又是正的，才翻成负；已经是
    负数的不再翻（否则带符号的行会被翻回正数，等于把 bug 换了个方向）。
    `direction` 是 "Call"/"Put"/空时不动 quantity——那一列这时说的是期权
    类型，不是方向。
    """
    try:
        qty = float(quantity or 0)
    except (TypeError, ValueError):
        return 0.0
    if qty > 0 and str(direction or "").strip().lower() in _SHORT_MARKERS:
        return -qty
    return qty
