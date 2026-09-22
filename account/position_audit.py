"""持仓行本身有没有问题——不联网、不算 Greeks，只看这些数字自洽不自洽。

这个模块存在的理由是 2026-09-21 那张截图：同一块面板上，Beta-Delta 是
**净多头 +356%**，而压力测试说大盘跌 20% 账户**赚 $98,640（+182% 净值）**。
这两个数来自同一个函数、同一批持仓，不可能同时对。旁边还有一个 $103,600 的
权利金——账户净值一共 $54,158。

这类事故的共同点是：每一步代码都跑通了，没有异常、没有报错，错的是**喂进去
的那批行**。所以要有一个只盯行本身的检查：符号对不对、数量级对不对、有没有
重复。它回答的是"该不该信这批数"，不是"风险有多大"。

判定全是纯函数，方便钉测试；取数和打印在 scripts/diagnose_positions.py。
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Literal, Sequence

from account.options import direction_carries_side, parse_occ, signed_quantity

Severity = Literal["error", "warn", "info"]

#: 单张合约的乘数。
MULT = 100.0

#: unit_cost / current_price 超过这个倍数，多半是把"每张成本"（已经乘过 100）
#: 写进了"每股成本"这一列。取 20 而不是 100：真实的浮盈浮亏很少到 20 倍，
#: 而写错单位一定是 100 倍左右，中间这段空得很开。
_PER_CONTRACT_RATIO = 20.0


@dataclasses.dataclass(frozen=True)
class Finding:
    code: str
    symbol: str
    detail: str
    severity: Severity = "error"


def _f(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def rows_read_backwards(rows: Iterable[dict]) -> list[dict]:
    """在符号归一之前，会被下游读反方向的那些行。

    条件是 quantity 为正、direction 明说卖出。风险快照、组合 Greeks、对冲
    宽度检查三条路以前都直接用 raw quantity，这些行在它们眼里全是买入。
    """
    out = []
    for r in rows:
        qty = _f(r.get("quantity"))
        direction = r.get("direction")
        if qty > 0 and signed_quantity(qty, direction) < 0:
            out.append(r)
    return out


def audit_option_rows(rows: Sequence[dict], *, equity: float) -> list[Finding]:
    """逐行体检。返回的顺序是 error → warn → info，同级按出现顺序。

    `equity` 用来判断数量级：一个 $54,158 的账户里不可能有 $103,600 的
    已付权利金。equity <= 0 时跳过所有跟净值比的检查（没有可比的基准，
    报出来只会是噪音）。
    """
    findings: list[Finding] = []
    seen: dict[str, int] = {}

    for r in rows:
        sym = str(r.get("symbol") or "").upper().strip()
        raw_qty = _f(r.get("quantity"))
        direction = r.get("direction")
        qty = signed_quantity(raw_qty, direction)
        unit_cost = _f(r.get("unit_cost"))
        price = _f(r.get("current_price"))
        parsed = parse_occ(sym)

        seen[sym] = seen.get(sym, 0) + 1

        if not parsed:
            findings.append(Finding(
                "SYMBOL_NOT_OCC", sym,
                "不是合法的 OCC 代号，解析不出标的/行权价/到期日；"
                "下游会拿整个代号当标的名，beta 和现价都会取不到"))
            continue

        if raw_qty == 0:
            findings.append(Finding(
                "ZERO_QUANTITY", sym, "张数是 0——平掉的仓应该删行或确认它是占位行",
                "info"))
            continue

        if raw_qty > 0 and qty < 0:
            findings.append(Finding(
                "SIGN_STORED_SPLIT", sym,
                f"张数存成 {raw_qty:+.0f} + direction='{direction}'。"
                f"归一后是 {qty:+.0f}（卖出）。风险快照/组合 Greeks/对冲宽度"
                f"三条路以前直接用 quantity，把这一行读成了买入"))
        elif raw_qty > 0 and not direction_carries_side(direction):
            findings.append(Finding(
                "SIDE_UNVERIFIABLE", sym,
                f"张数 {raw_qty:+.0f} 为正，direction='{direction}' 说的是期权类型"
                f"不是买卖方向。这一行的方向只有 quantity 一个来源，写错了"
                f"没有第二处能对出来——对着券商单子确认一下是买是卖",
                "warn"))

        if unit_cost > 0 and price > 0 and unit_cost / price > _PER_CONTRACT_RATIO:
            findings.append(Finding(
                "COST_LOOKS_PER_CONTRACT", sym,
                f"单位成本 ${unit_cost:,.2f} 是现价 ${price:,.2f} 的 "
                f"{unit_cost / price:.0f} 倍。多半是把每张成本（已乘 100）写进了"
                f"每股成本这一列——最大亏损和盈亏会一起放大 100 倍"))

        if equity > 0 and qty > 0 and unit_cost > 0:
            premium = unit_cost * abs(qty) * MULT
            if premium > equity:
                findings.append(Finding(
                    "PREMIUM_EXCEEDS_EQUITY", sym,
                    f"买方已付权利金 ${premium:,.0f} 超过账户净值 ${equity:,.0f}。"
                    f"买期权的钱是已经付掉的，不可能超过净值——张数或单位成本有一个是错的"))

        if equity > 0 and qty < 0 and parsed["option_type"] == "put":
            assigned = parsed["strike"] * abs(qty) * MULT
            if assigned > equity * 3:
                findings.append(Finding(
                    "SHORT_PUT_NOTIONAL_HEAVY", sym,
                    f"卖出 put 的行权总额 ${assigned:,.0f} 是净值的 "
                    f"{assigned / equity:.1f} 倍（标的归零时的最大亏损）",
                    "warn"))

    for sym, n in seen.items():
        if n > 1:
            findings.append(Finding(
                "DUPLICATE_SYMBOL", sym,
                f"同一个代号有 {n} 行。options_positions 对 (account_id, symbol) "
                f"有唯一约束，出现重复说明有行绕过了正常写入路径；敞口会被算 {n} 遍"))

    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(findings, key=lambda f: order[f.severity])


def sign_flip_impact(rows: Sequence[dict]) -> dict:
    """符号归一影响多少行、多少张、多少名义金额。

    用来回答"这就是那张截图的原因吗"：如果一行都没被翻，那 BD 和压力测试
    的矛盾另有来源，别把这次修正当成结论。
    """
    flipped = rows_read_backwards(rows)
    contracts = sum(abs(_f(r.get("quantity"))) for r in flipped)
    notional = 0.0
    for r in flipped:
        parsed = parse_occ(str(r.get("symbol") or "").upper())
        if parsed:
            notional += parsed["strike"] * abs(_f(r.get("quantity"))) * MULT
    return {
        "rows": len(flipped),
        "symbols": [str(r.get("symbol") or "").upper() for r in flipped],
        "contracts": contracts,
        "strike_notional": notional,
    }
