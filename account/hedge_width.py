"""对冲的宽度够不够——它在你要防的那个跌幅上还赔不赔钱。

`hedge_governance` 查的是保护**贵不贵**（成本率）和**放太久没有**（DTE）。
两样都合格的保护，仍然可能在真正需要它的时候一分钱都不多赔——因为一个
put 价差只在一个**窗口**里起作用：

    长腿行权价 ──────────── 短腿行权价
         ↑                      ↑
      开始有内在价值          赔付打满，再跌也不多赔

窗口之外，上面没起效、下面已封顶。把两个行权价换算成"大盘要跌多少"
（除以标的的 beta），就能直接跟压力测试的情景比。

2026-09-20 的实测，两个对冲的窗口都窄得惊人：

    QQQ 690/630（β=1.31）  大盘 -13.4% 才起效，-18.9% 就打满 → 窗口 5.5 个点
    SMH 525/500（β=1.77）  大盘  -4.7% 起效，  -7.1% 就打满 → 窗口 2.4 个点

SMH 那个价差只有 4.4% 宽，最大赔付 $2,500，在 $53,230 的账户上是 4.7%。
两个窗口之间（大盘 -7.1% 到 -13.4%）还有一段谁都不管的真空。压力测试
在 -10% 上亏 21.8%、-20% 上亏 37.1%，原因就在这里——而成本率和 DTE 这
两项检查全部合格。

**这个检查不评价"该不该对冲"**，只回答"已经买的这份保护，在你自己设定
的压力情景上还起不起作用"。要不要调整是人的判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from account.hedge_governance import HEDGE_UNDERLYINGS

#: 起效点比这个情景还深，就等于在这个情景上完全没有保护。
#: 默认用 -10%（压力测试的主要档位，stress_hard_stop 盯的就是它）。
PRIMARY_SCENARIO = -0.10

#: 最深的governed情景，stress_20_hard_stop 盯的档位。
DEEP_SCENARIO = -0.20

#: 有效窗口窄于这么多个大盘百分点，就算"窗口过窄"——跌幅只要越过它，
#: 这份保护就退化成一笔固定赔付，不再随行情走。
MIN_USEFUL_WINDOW_PCT = 0.05

#: 最大赔付低于净值的这个比例，这份保护在组合层面不成规模。
MIN_MATERIAL_PAYOFF_PCT = 0.03


@dataclass(frozen=True)
class HedgeWindow:
    """一个保护性结构的有效区间，换算到"大盘跌多少"。"""

    underlying: str
    long_strike: float
    short_strike: float | None
    contracts: int
    spot: float
    beta: float
    expiry: str | None = None

    @property
    def is_capped(self) -> bool:
        return self.short_strike is not None

    @property
    def width(self) -> float | None:
        if self.short_strike is None:
            return None
        return self.long_strike - self.short_strike

    @property
    def width_pct_of_spot(self) -> float | None:
        w = self.width
        return w / self.spot if w is not None and self.spot else None

    @property
    def activation_pct(self) -> float | None:
        """长腿转为实值所需的**大盘**跌幅（负数）。

        这之前这份保护不是完全没用——它还有 delta 和 vega，跌的时候会涨一点
        ——但它的价值全是时间价值，会随时间流失。把它叫"起效点"是就内在
        价值而言的，页面上也这么标。
        """
        if not self.spot or not self.beta:
            return None
        return (self.long_strike / self.spot - 1.0) / self.beta

    @property
    def cap_pct(self) -> float | None:
        """赔付打满所需的大盘跌幅（负数）。裸多头 put 不封顶，返回 None。"""
        if self.short_strike is None or not self.spot or not self.beta:
            return None
        return (self.short_strike / self.spot - 1.0) / self.beta

    @property
    def useful_window_pct(self) -> float | None:
        """有效区间有多宽（大盘百分点）。裸多头是无限宽，返回 None。"""
        a, c = self.activation_pct, self.cap_pct
        if a is None or c is None:
            return None
        return abs(c - a)

    @property
    def max_payoff(self) -> float | None:
        w = self.width
        if w is None:
            return None
        return w * 100.0 * abs(self.contracts)

    def payoff_at(self, market_move: float) -> float | None:
        """在给定大盘跌幅下，这个结构的到期内在赔付。

        到期口径，不含时间价值——压力测试用的是 BS 重新定价，两者会有差；
        这里要回答的是"结构上限在哪"，那是到期口径的问题。
        """
        if not self.spot or not self.beta:
            return None
        und = self.spot * (1.0 + self.beta * market_move)
        intrinsic = max(self.long_strike - und, 0.0)
        if self.short_strike is not None:
            intrinsic -= max(self.short_strike - und, 0.0)
        return intrinsic * 100.0 * abs(self.contracts)


@dataclass
class WidthFinding:
    code: str
    severity: str          # "WARN" | "INFO"
    underlying: str
    message: str


@dataclass
class WidthReport:
    windows: list[HedgeWindow] = field(default_factory=list)
    findings: list[WidthFinding] = field(default_factory=list)
    uncovered_bands: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_hedges(self) -> bool:
        return bool(self.windows)


def _normalise(leg: dict) -> dict:
    """接受 account_monitor 的腿结构（sym/qty/type/strike）或已规范化的。"""
    root = str(leg.get("root") or leg.get("underlying") or "").upper()
    if not root:
        sym = str(leg.get("sym") or leg.get("symbol") or "").upper()
        try:
            from account.options import parse_occ
            root = (parse_occ(sym) or {}).get("root", "") or ""
        except Exception:
            root = ""
    raw_type = leg.get("option_type") or leg.get("type") or ""
    kind = str(raw_type).lower()
    if kind in ("p", "put"):
        kind = "put"
    elif kind in ("c", "call"):
        kind = "call"
    return {
        "root": root,
        "option_type": kind,
        "strike": float(leg.get("strike") or 0.0),
        "quantity": float(leg.get("quantity", leg.get("qty")) or 0.0),
        "expiry": leg.get("expiry"),
    }


def build_windows(
    legs: list[dict],
    *,
    spot_map: dict[str, float],
    beta_map: dict[str, float],
    underlyings=HEDGE_UNDERLYINGS,
) -> list[HedgeWindow]:
    """把 put 腿配成价差，算出每个结构的有效窗口。

    配对规则跟 hedge_governance._find_matching_short_put 一致：同标的、同
    到期、行权价**低于**长腿的那些空头里取最高的一个。行权价高于长腿的
    空头 put 是credit spread（看涨结构），不是保护，不参与配对。

    配不到空头的长腿就是裸多头，不封顶——那是好事，不是缺陷。
    """
    normalised = [_normalise(l) for l in legs or []]
    scoped = [l for l in normalised
              if l["root"] in underlyings and l["option_type"] == "put"]

    longs = [l for l in scoped if l["quantity"] > 0]
    shorts = [l for l in scoped if l["quantity"] < 0]
    windows: list[HedgeWindow] = []

    # 长腿从高行权价往低配，保证最贵的保护先拿到最近的那条空头腿
    for lng in sorted(longs, key=lambda l: -l["strike"]):
        root = lng["root"]
        spot = float(spot_map.get(root) or 0.0)
        beta = float(beta_map.get(root) or 0.0)
        candidates = [
            s for s in shorts
            if s["root"] == root
            and s["expiry"] == lng["expiry"]
            and s["strike"] < lng["strike"]
            and s["quantity"] < 0
        ]
        match = max(candidates, key=lambda s: s["strike"]) if candidates else None
        if match is not None:
            match["quantity"] += min(abs(match["quantity"]), abs(lng["quantity"]))
            if match["quantity"] >= 0:
                shorts = [s for s in shorts if s is not match]
        windows.append(HedgeWindow(
            underlying=root,
            long_strike=lng["strike"],
            short_strike=match["strike"] if match is not None else None,
            contracts=int(abs(lng["quantity"])) or 1,
            spot=spot,
            beta=beta,
            expiry=lng["expiry"],
        ))
    return windows


def _uncovered_bands(windows: list[HedgeWindow],
                     deepest: float) -> list[tuple[float, float]]:
    """两份保护之间、以及最深那份打满之后，没人管的区段。

    裸多头 put 起效之后一路有效，所以它的区间一直延伸到 deepest。

    **从最浅的那个起效点开始算，不从 0 开始。** 0 到第一个起效点之间必然
    是空的——OTM 保护本来就不在平值起效，把它报成"缺口"等于每份对冲都
    会挨一条，而那正是 ACTIVATES_TOO_DEEP 单独在判的事（它拿账户自己的
    压力线做标准，比"离 0 有多远"有意义）。重复报只会把真正的缺口淹掉。

    一个区间都算不出来（缺 beta 或缺现价）时返回空，不返回"全程无保护"
    ——那是没算出来，不是查出来了。
    """
    covered = []
    for w in windows:
        a = w.activation_pct
        if a is None or a > 0:
            continue
        c = w.cap_pct if w.cap_pct is not None else deepest
        lo, hi = max(min(a, c), deepest), min(max(a, c), 0.0)
        if lo < hi:
            covered.append((lo, hi))
    if not covered:
        return []

    covered.sort(key=lambda x: -x[1])          # 从浅到深
    gaps: list[tuple[float, float]] = []
    cursor = covered[0][1]                     # 最浅的起效点，不是 0
    for lo, hi in covered:
        if hi < cursor:                         # cursor 和 hi 之间是空的
            gaps.append((hi, cursor))
        cursor = min(cursor, lo)
    if cursor > deepest:
        gaps.append((deepest, cursor))
    return gaps


def assess_width(
    legs: list[dict],
    *,
    spot_map: dict[str, float],
    beta_map: dict[str, float],
    equity: float,
    primary_scenario: float = PRIMARY_SCENARIO,
    deep_scenario: float = DEEP_SCENARIO,
    underlyings=HEDGE_UNDERLYINGS,
) -> WidthReport:
    """已有保护在 governed 压力情景上还起不起作用。

    `primary_scenario` / `deep_scenario` 传压力限额盯的那两个档位（默认
    -10% / -20%），这样判定用的是账户自己的线，不是另一套写死的阈值。
    """
    report = WidthReport()
    report.windows = build_windows(
        legs, spot_map=spot_map, beta_map=beta_map, underlyings=underlyings)

    if not report.windows:
        report.notes.append(
            "持仓里没有 " + "/".join(sorted(underlyings)) +
            " 的多头 put，没有可检查的指数保护。这是「没有保护」，"
            "不是「保护合格」。"
        )
        return report

    for w in report.windows:
        if not w.spot or not w.beta:
            report.notes.append(
                f"{w.underlying}：缺现价或 beta，算不出它对应的大盘跌幅区间。"
            )
            continue

        label = (f"{w.underlying} {w.long_strike:.0f}/{w.short_strike:.0f}"
                 if w.is_capped else f"{w.underlying} {w.long_strike:.0f} 裸多头")
        act, cap = w.activation_pct, w.cap_pct

        # ① 在主情景上根本还没起效
        if act is not None and act < primary_scenario:
            report.findings.append(WidthFinding(
                code="ACTIVATES_TOO_DEEP", severity="WARN", underlying=w.underlying,
                message=(
                    f"{label}：要大盘跌 {abs(act) * 100:.1f}% 长腿才有内在价值，"
                    f"而压力线盯的是 {abs(primary_scenario) * 100:.0f}%。"
                    f"在那个情景上这份保护拿不出内在价值，只剩会流失的时间价值。"
                ),
            ))

        # ② 在最深情景之前就打满
        if cap is not None and cap > deep_scenario:
            report.findings.append(WidthFinding(
                code="CAPS_BEFORE_DEEP_SCENARIO", severity="WARN",
                underlying=w.underlying,
                message=(
                    f"{label}：大盘跌 {abs(cap) * 100:.1f}% 赔付就打满"
                    f"（{w.width:.0f} 点宽，最多赔 ${w.max_payoff:,.0f}），"
                    f"再跌到 {abs(deep_scenario) * 100:.0f}% 一分钱都不多赔。"
                    f"买回这条空头腿就能解除封顶。"
                ),
            ))

        # ③ 有效窗口太窄
        win = w.useful_window_pct
        if win is not None and win < MIN_USEFUL_WINDOW_PCT:
            report.findings.append(WidthFinding(
                code="WINDOW_TOO_NARROW", severity="WARN", underlying=w.underlying,
                message=(
                    f"{label}：有效区间只有大盘 {abs(act) * 100:.1f}% → "
                    f"{abs(cap) * 100:.1f}%，宽 {win * 100:.1f} 个点。"
                    f"跌幅一越过它，这份保护就退化成一笔固定赔付，不再随行情走。"
                ),
            ))

        # ④ 赔付规模在组合层面不成比例
        payoff = w.max_payoff
        if payoff is not None and equity > 0:
            share = payoff / equity
            if share < MIN_MATERIAL_PAYOFF_PCT:
                report.findings.append(WidthFinding(
                    code="PAYOFF_IMMATERIAL", severity="INFO", underlying=w.underlying,
                    message=(
                        f"{label}：最大赔付 ${payoff:,.0f} 只有净值的 "
                        f"{share * 100:.1f}%，组合层面不成规模。"
                    ),
                ))

    report.uncovered_bands = _uncovered_bands(report.windows, deep_scenario)
    for lo, hi in report.uncovered_bands:
        if abs(hi - lo) < 0.005:
            continue
        report.findings.append(WidthFinding(
            code="UNCOVERED_BAND", severity="WARN", underlying="—",
            message=(
                f"大盘 {abs(hi) * 100:.1f}% → {abs(lo) * 100:.1f}% 这一段，"
                f"没有任何一份保护还在随行情增加赔付。"
            ),
        ))

    return report
