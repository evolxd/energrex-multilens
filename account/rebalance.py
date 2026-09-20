"""把「超限」翻译成「卖哪只、卖多少股」。

门③仓位管理此前只回答"超没超线"，超线时给出的指示是「超限期间无法调高
该限额，只能调整持仓」——这句话本身不可执行：调哪一只、调多少、调完还
超不超，一个数字都没有。这个模块补上缺的那一步。

三件事决定了它不能是"每条限额各算各的"：

1. **一笔减仓同时满足多条限额。** NVDA 占 22%（单票上限 15%）同时把
   「AI芯片」这条产业链顶到 48%（上限 40%）。先减 NVDA 到 15% 之后，产业链
   只剩 41%，还差 1 个点——而不是最初看到的 8 个点。各算各的会让人把同一
   笔仓位卖三遍。
2. **卖出会同时抬高现金比例。** 为了单票和产业链卖掉的钱，本来就落进现金
   里；先算完前两条，现金下限的缺口往往已经自己补上了。
3. **超出的部分要从最集中的地方削起。** 同一条产业链里三只票，从占比最大
   的那只往下削（削平到同一水平线为止），比按比例摊派更能降集中度，动到的
   标的也更少。

所以这里是顺序结算的：流动性 → 单票 → 产业链 → 现金下限，每一步都在前一步
的结果上继续算。

股票数按现价向上取整——宁可多卖一股落到线内，也不要算出一个刚好卡在线上
的股数，成交价差一点就又超了。只有期权腿撑起来的敞口不会编出股数来：期权
要减哪一腿、平仓还是滚动，是这个模块没有信息回答的问题，它只报出还差多少
钱要从期权那边出。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from scoring.position_exposure import DEFAULT_MAX_PCT_OF_ADV, UNCLASSIFIED, Exposures

#: 小于这个金额的减仓不出指令——四舍五入噪声，不是真的要下单。
MIN_TRIM_DOLLARS = 50.0


@dataclass(frozen=True)
class Trim:
    """一条可执行的减仓指令。"""

    symbol: str
    dollars: float
    reasons: tuple[str, ...]
    shares: int | None = None
    option_dollars: float = 0.0
    from_pct: float = 0.0
    to_pct: float = 0.0

    @property
    def needs_manual_option_leg(self) -> bool:
        return self.option_dollars >= MIN_TRIM_DOLLARS


@dataclass
class RebalancePlan:
    trims: list[Trim] = field(default_factory=list)
    cash_pct_before: float = 0.0
    cash_pct_after: float = 0.0
    proceeds: float = 0.0
    unresolved: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.trims


def _waterfill(values: dict[str, float], excess: float) -> dict[str, float]:
    """从最大的那些标的往下削平，合计削掉 `excess`。

    削到一条水平线 L 为止：所有高于 L 的削到 L，低于 L 的不动。这样动到的
    标的最少，而且削的正是集中度最高的那几只。`excess` 超过总额时全削光。
    """
    if excess <= 0 or not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: -kv[1])
    total = sum(v for _, v in ordered if v > 0)
    if excess >= total:
        return {s: v for s, v in ordered if v > 0}

    running = 0.0
    cuts: dict[str, float] = {}
    for k in range(1, len(ordered) + 1):
        running += max(ordered[k - 1][1], 0.0)
        level = (running - excess) / k
        next_value = ordered[k][1] if k < len(ordered) else float("-inf")
        if level >= next_value:
            for symbol, value in ordered[:k]:
                cut = value - level
                if cut > 0:
                    cuts[symbol] = cut
            return cuts
    return cuts


def plan_rebalance(
    exposures: Exposures,
    limits: dict[str, float | None],
    *,
    prices: dict[str, float] | None = None,
    stock_value: dict[str, float] | None = None,
    avg_dollar_volume: dict[str, float] | None = None,
    max_pct_of_adv: float = DEFAULT_MAX_PCT_OF_ADV,
) -> RebalancePlan:
    """所有硬约束都回到线内所需要的减仓清单。

    `prices` {标的: 现价} 用来把金额换成股数，缺的标的只给金额。
    `stock_value` {标的: 现货市值} 用来区分"这笔能靠卖股票减掉"和"这笔在
    期权腿里"；不传就假定全是股票。
    """
    equity = exposures.total_equity
    if not equity or equity <= 0:
        return RebalancePlan()

    prices = prices or {}
    avg_dollar_volume = avg_dollar_volume or {}

    value = {s: pct / 100.0 * equity for s, pct in exposures.by_ticker_pct.items()}
    start_value = dict(value)
    cash = exposures.cash_pct / 100.0 * equity
    reasons: dict[str, list[str]] = {}
    unresolved: list[str] = []

    def record(symbol: str, amount: float, reason: str) -> float:
        """削减 `symbol`，返回实际削掉的金额（不会削成负的）。"""
        available = value.get(symbol, 0.0)
        cut = min(amount, available)
        if cut <= 0:
            return 0.0
        value[symbol] = available - cut
        bucket = reasons.setdefault(symbol, [])
        if reason not in bucket:
            bucket.append(reason)
        return cut

    # ── 1. 流动性：单只的市值必须能在限定天数内出得掉 ──────────────────
    liquidity_days = limits.get("liquidity_days_max")
    if liquidity_days is not None and liquidity_days > 0:
        for symbol, adv in avg_dollar_volume.items():
            if symbol not in value or not adv or adv <= 0:
                continue
            cap = float(liquidity_days) * adv * max_pct_of_adv
            if value[symbol] > cap:
                record(symbol, value[symbol] - cap, "流动性天数")

    # ── 2. 单票集中度 ────────────────────────────────────────────────
    single_max = limits.get("single_stock_max")
    if single_max is not None:
        cap = float(single_max) / 100.0 * equity
        for symbol in list(value):
            if value[symbol] > cap:
                record(symbol, value[symbol] - cap, "单票集中度")

    # ── 3. 产业链集中度（在单票已经削过之后重算）────────────────────────
    chain_max = limits.get("chain_max")
    if chain_max is not None:
        cap = float(chain_max) / 100.0 * equity
        members: dict[str, dict[str, float]] = {}
        for symbol, remaining in value.items():
            chain = exposures.chain_by_ticker.get(symbol, UNCLASSIFIED)
            members.setdefault(chain, {})[symbol] = remaining
        for chain, holdings in members.items():
            total = sum(v for v in holdings.values() if v > 0)
            if total <= cap:
                continue
            for symbol, cut in _waterfill(holdings, total - cap).items():
                record(symbol, cut, f"产业链集中度（{chain}）")

    # ── 4. 现金下限（前三步卖出的钱已经进了现金）────────────────────────
    proceeds_so_far = sum(start_value[s] - value[s] for s in value)
    cash_floor = limits.get("cash_floor_min")
    if cash_floor is not None:
        need = float(cash_floor) / 100.0 * equity - (cash + proceeds_so_far)
        if need > MIN_TRIM_DOLLARS:
            raised = 0.0
            for symbol, cut in _waterfill(dict(value), need).items():
                raised += record(symbol, cut, "现金下限")
            if need - raised > MIN_TRIM_DOLLARS:
                unresolved.append(
                    f"现金下限还差 ${need - raised:,.0f}：把所有持仓全部清掉也补不满，"
                    "这条限额本身要么定得不现实，要么净值读数有问题。"
                )

    # ── 组装 ────────────────────────────────────────────────────────
    trims: list[Trim] = []
    for symbol in sorted(value, key=lambda s: -(start_value[s] - value[s])):
        dollars = start_value[symbol] - value[symbol]
        if dollars < MIN_TRIM_DOLLARS:
            continue
        stock_part = (
            min(dollars, float(stock_value.get(symbol, 0.0)))
            if stock_value is not None
            else dollars
        )
        price = prices.get(symbol)
        shares = math.ceil(stock_part / price) if price and price > 0 and stock_part > 0 else None
        trims.append(
            Trim(
                symbol=symbol,
                dollars=round(dollars, 2),
                reasons=tuple(reasons.get(symbol, ())),
                shares=shares,
                option_dollars=round(max(dollars - stock_part, 0.0), 2),
                from_pct=start_value[symbol] / equity * 100.0,
                to_pct=value[symbol] / equity * 100.0,
            )
        )

    proceeds = sum(t.dollars for t in trims)
    return RebalancePlan(
        trims=trims,
        cash_pct_before=exposures.cash_pct,
        cash_pct_after=(cash + proceeds) / equity * 100.0,
        proceeds=round(proceeds, 2),
        unresolved=unresolved,
    )
