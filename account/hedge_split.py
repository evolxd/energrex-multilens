"""把要对冲掉的 Beta-Delta 拆成「半导体那一半」和「其余」。

门③仓位管理此前只会说「Beta-Delta 超限」，然后停在那里。要变成可执行的
指令，缺的不只是"买几张"——还缺"买哪个"：

* QQQ 覆盖面广、流动性最好、权利金最便宜，但一个半导体权重很高的账户在
  回撤里跌得比 QQQ 狠，只买 QQQ put 会留下一截对冲不到的基差。
* SMH 跟半导体那一段几乎同涨同跌，基差小得多，但它只对冲得了半导体，对
  账户里的软件/网安/大科技那一段没有意义。

所以两者不是二选一，也不能各按全额买一遍——那是把同一笔敞口对冲两次。
正确的做法是先按产业链把超出目标的那部分 Beta-Delta 切开，半导体的一段
交给 SMH，剩下的交给 QQQ，两段加起来正好等于要对冲的总量。

现有的 QQQ/SMH 保护腿本身也带负的 Beta-Delta，它们按各自的产业链落进对应
的那一段（SMH put 落在半导体侧、QQQ put 落在广基侧），所以这里算出来的是
「还差多少」，不是「一共要买多少」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 用 SMH 对冲的产业链。「AI芯片」里混着 ANET/CSCO/DELL/TSLA 这些并非纯
#: 半导体的标的——它们跟 SMH 的相关性没有 NVDA/AMD/MU 那么高，是这个口径
#: 已知的近似之处，页面上会把归进这一段的标的逐个列出来，让人能自己判断。
SEMI_CHAINS = frozenset({"AI芯片", "半导体设备", "对冲(半导体)"})

SEMI_HEDGE_UNDERLYING = "SMH"
BROAD_HEDGE_UNDERLYING = "QQQ"


@dataclass(frozen=True)
class HedgeSplit:
    """按产业链切开之后，每个对冲工具各自要扛的 Beta-Delta。"""

    equity: float
    total_bd: float
    target_bd: float
    bd_to_hedge: float
    semi_bd: float
    broad_bd: float
    semi_share: float
    semi_bd_to_hedge: float
    broad_bd_to_hedge: float
    semi_symbols: list[str] = field(default_factory=list)
    broad_symbols: list[str] = field(default_factory=list)

    @property
    def needs_hedge(self) -> bool:
        return self.bd_to_hedge > 0

    @property
    def semi_pct_of_equity(self) -> float:
        return self.semi_bd / self.equity * 100.0 if self.equity else 0.0

    @property
    def broad_pct_of_equity(self) -> float:
        return self.broad_bd / self.equity * 100.0 if self.equity else 0.0


def split_hedge_need(
    beta_delta_by_underlying: dict[str, float],
    *,
    equity: float,
    target_bd_ratio: float,
    chain_of,
    total_bd: float | None = None,
    semi_chains=SEMI_CHAINS,
) -> HedgeSplit:
    """按 `chain_of` 把逐标的 Beta-Delta 分成半导体/其余两段，再按同样的
    比例切开"超出目标的那部分"。

    `total_bd` 用来对齐风险快照里那个已经四舍五入过的合计值；不传就直接
    把逐标的的数加起来。两者应当只差一个舍入量——真差得多，说明快照和
    逐标的拆分读的不是同一批持仓，调用方应当先查这个，而不是照用结果。

    切分用的是 Beta-Delta 的占比，不是市值占比：要对冲的是方向性敞口，
    一只 beta 2.5 的票在这里本来就该按 2.5 倍的分量参与分摊。

    半导体那一段为负（保护腿已经超过了现货）时 share 夹到 0——此时再往
    SMH 上加保护是在建反向头寸，不是对冲。
    """
    semi_bd = 0.0
    broad_bd = 0.0
    semi_symbols: list[str] = []
    broad_symbols: list[str] = []

    for symbol, bd in (beta_delta_by_underlying or {}).items():
        name = (symbol or "").strip().upper()
        if not name:
            continue
        chain = chain_of(name)
        if chain in semi_chains:
            semi_bd += float(bd)
            semi_symbols.append(name)
        else:
            broad_bd += float(bd)
            broad_symbols.append(name)

    resolved_total = float(total_bd) if total_bd is not None else semi_bd + broad_bd
    target_bd = target_bd_ratio * equity
    bd_to_hedge = resolved_total - target_bd

    positive_total = max(semi_bd, 0.0) + max(broad_bd, 0.0)
    if positive_total > 0:
        semi_share = max(semi_bd, 0.0) / positive_total
    else:
        semi_share = 0.0

    if bd_to_hedge > 0:
        semi_need = bd_to_hedge * semi_share
        broad_need = bd_to_hedge - semi_need
    else:
        semi_need = broad_need = 0.0

    return HedgeSplit(
        equity=float(equity),
        total_bd=resolved_total,
        target_bd=target_bd,
        bd_to_hedge=bd_to_hedge,
        semi_bd=semi_bd,
        broad_bd=broad_bd,
        semi_share=semi_share,
        semi_bd_to_hedge=semi_need,
        broad_bd_to_hedge=broad_need,
        semi_symbols=sorted(semi_symbols),
        broad_symbols=sorted(broad_symbols),
    )
