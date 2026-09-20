"""公司结构分析：估值分数底下那家公司，是靠什么撑起来的。

门①的六维评分回答"这个价格贵不贵、成长配不配得上估值"，用的是 43 个
比率字段（毛利率、PEG、ROIC、revenue_growth_yoy…）。比率有个共同的盲点：
**它们把一家公司压成了一个点，看不出这个点是靠几条腿站着的。**

NVDA 的 `revenue_growth_yoy = 0.85` 和一家四个分部各增长 21% 的公司，
在评分引擎眼里是同一个数。但前者 95% 的增量来自一个分部、那个分部又
高度依赖少数几个超大规模客户，后者是四条腿。同样的分数，完全不同的
脆弱度——而脆弱度决定了这个分数能不能拿去放大仓位。

这个模块回答比率回答不了的那部分：

1. **增量归因** —— 这一年多出来的收入，是哪几个分部贡献的。增长 85%
   不稀奇，"85% 里有 95% 来自一个分部"才是要看的东西。
2. **结构集中度** —— 用 HHI，不是"最大分部占比"。三个 33% 和一个 60%
   加两个 20%，最大占比分别是 33% 和 60%，但前者其实更脆（没有任何一
   条腿能扛事）——HHI 分得开，最大占比分不开。
3. **单点失效** —— 最大贡献分部增速归零，整体增速掉到多少。这是纯算术，
   不需要预测，但它把"增长故事"换算成了"这个故事有多依赖一件事继续发生"。
4. **结构漂移** —— 分部占比在过去几个季度是不是在变。占比稳定的公司，
   用历史比率外推是合理的；占比每季度挪 5 个点的公司，去年的 ROIC 跟
   明年的 ROIC 讲的不是同一门生意。

数据来自 `edgar_fetcher._quarterly_segment_revenues()` 已经在拉的那份
XBRL 分部收入（`{期末: {分部名: 金额}}`），本模块不自己联网——所有函数
都是纯函数，传进来什么算什么，这样测试里不需要打桩 SEC。

**已知的口径边界**（不藏在代码里，页面上也会标出来）：

- XBRL 的分部成员名是公司自己定义的（`DataCenterMember`、`ProductsMember`），
  不同公司之间不可比，同一公司改过分部口径的年份之间也不可比。这里只做
  同一公司内部的时序比较，不跨公司比。
- 分部之和常常不等于 Total（有未分配项、抵消项、口径差）。差额单独报成
  `residual`，不摊回各分部——摊回去等于替公司做了一个它自己没做的假设。
- 一家只报一个分部的公司（很多 SaaS）在这里得到的结论是"看不出结构"，
  不是"结构健康"。两者必须分得开。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: XBRL 里代表合计的那个键，`_quarterly_segment_revenues` 用它装无 segment 维度的事实。
TOTAL_KEY = "Total"

#: 低于这个占比的分部并进"其他"，避免把四舍五入噪声当成一条腿。
MIN_SEGMENT_SHARE = 0.005

#: 分部数少于这个值时，结构分析给不出有意义的结论（只报一个分部 = 看不出结构）。
MIN_SEGMENTS_FOR_STRUCTURE = 2


@dataclass(frozen=True)
class SegmentMix:
    """某一期的分部构成。"""

    period: str
    total: float
    segments: dict[str, float] = field(default_factory=dict)
    residual: float = 0.0

    @property
    def shares(self) -> dict[str, float]:
        if not self.total:
            return {}
        return {k: v / self.total for k, v in self.segments.items()}

    @property
    def n_segments(self) -> int:
        return len(self.segments)

    @property
    def top_segment(self) -> str | None:
        if not self.segments:
            return None
        return max(self.segments, key=lambda k: self.segments[k])

    @property
    def top_share(self) -> float:
        shares = self.shares
        return max(shares.values(), default=0.0)

    @property
    def hhi(self) -> float:
        """赫芬达尔指数（0~1）。1 = 全靠一个分部；1/n = n 条腿完全均等。

        用平方和而不是最大占比：三个 33% 的 HHI 是 0.33，一个 60% + 两个
        20% 的 HHI 是 0.44——后者更集中，而"最大占比"这个指标会说前者
        33% 比后者 60% 安全，方向正好反了一半。
        """
        if not self.total:
            return 0.0
        return sum(s * s for s in self.shares.values())

    @property
    def effective_segments(self) -> float:
        """等效分部数 = 1/HHI。「这家公司实际上靠几条腿站着」的连续版本。

        四个分部但一个占 90%，等效分部数 ≈ 1.2——数出来是 4 条腿，实际
        是 1 条腿加三根装饰。
        """
        h = self.hhi
        return 1.0 / h if h > 0 else 0.0

    @property
    def residual_share(self) -> float:
        return self.residual / self.total if self.total else 0.0


@dataclass(frozen=True)
class SegmentContribution:
    """一个分部对整体增量的贡献。"""

    segment: str
    prior: float
    current: float

    @property
    def delta(self) -> float:
        return self.current - self.prior

    @property
    def own_growth(self) -> float | None:
        """这个分部自己的同比增速。基数为 0 时是 None，不是无穷大。"""
        if self.prior <= 0:
            return None
        return self.delta / self.prior


@dataclass
class GrowthAttribution:
    """整体增长的来源拆解。"""

    period: str
    prior_period: str
    total_prior: float
    total_current: float
    contributions: list[SegmentContribution] = field(default_factory=list)

    @property
    def total_delta(self) -> float:
        return self.total_current - self.total_prior

    @property
    def total_growth(self) -> float | None:
        if self.total_prior <= 0:
            return None
        return self.total_delta / self.total_prior

    @property
    def ranked(self) -> list[SegmentContribution]:
        """按增量绝对贡献从大到小。"""
        return sorted(self.contributions, key=lambda c: -c.delta)

    def share_of_growth(self, contribution: SegmentContribution) -> float | None:
        """这个分部贡献了整体增量的百分之几。

        整体增量为 0 或为负时返回 None：分母接近 0 时这个比例会炸成几百
        个百分点，报出来的是噪声不是信息。收缩期该看的是谁在拖，用
        `delta` 的绝对值，不是这个比例。
        """
        if self.total_delta <= 0:
            return None
        return contribution.delta / self.total_delta

    @property
    def top_contributor(self) -> SegmentContribution | None:
        ranked = self.ranked
        return ranked[0] if ranked else None

    @property
    def growth_without_top(self) -> float | None:
        """最大贡献分部**增速归零**（不是收入归零）时，整体同比增速变成多少。

        这不是预测，是算术：把那个分部的当期收入换回上期水平，其余不动。
        回答的是「这个增长故事有多依赖一件事继续发生」。
        """
        top = self.top_contributor
        if top is None or self.total_prior <= 0:
            return None
        return (self.total_delta - top.delta) / self.total_prior


@dataclass
class StructureAssessment:
    """结构分析的完整结论。"""

    ticker: str
    mix: SegmentMix | None = None
    attribution: GrowthAttribution | None = None
    drift: list[SegmentMix] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_analyzable(self) -> bool:
        return (
            self.mix is not None
            and self.mix.n_segments >= MIN_SEGMENTS_FOR_STRUCTURE
        )


def _clean(period_revs: dict[str, float]) -> tuple[dict[str, float], float, float]:
    """(分部, 合计, 残差)。剔除 Total 键和占比过小的碎项。

    合计优先用公司自己报的 Total；没有 Total 就用分部之和——但这两种情况
    的残差含义不同，前者是真实的未分配额，后者恒为 0（因为分母就是分子
    加出来的），调用方靠 `residual` 分得开。
    """
    segments = {
        k: float(v) for k, v in period_revs.items()
        if k != TOTAL_KEY and v is not None
    }
    reported_total = period_revs.get(TOTAL_KEY)
    summed = sum(segments.values())
    total = float(reported_total) if reported_total else summed

    if total > 0:
        segments = {k: v for k, v in segments.items()
                    if abs(v) / total >= MIN_SEGMENT_SHARE}
        summed = sum(segments.values())

    return segments, total, total - summed


def segment_mix(seg_revs: dict[str, dict[str, float]],
                period: str | None = None) -> SegmentMix | None:
    """某一期的分部构成。`period` 不传就取最新一期。"""
    if not seg_revs:
        return None
    key = period or max(seg_revs)
    if key not in seg_revs:
        return None
    segments, total, residual = _clean(seg_revs[key])
    if total <= 0:
        return None
    return SegmentMix(period=key, total=total, segments=segments, residual=residual)


def growth_attribution(seg_revs: dict[str, dict[str, float]],
                       period: str | None = None,
                       periods_back: int = 4) -> GrowthAttribution | None:
    """同比增量归因。`periods_back=4` = 四个季度前（同比，不是环比）。

    找不到正好四期前的那一期就返回 None，而不是拿三期前凑合——季度性
    强的生意里，用环比或者隔三期比，算出来的"增长"里混着季节因素，
    比不算更误导。
    """
    if not seg_revs:
        return None
    periods = sorted(seg_revs)
    key = period or periods[-1]
    if key not in periods:
        return None
    index = periods.index(key)
    if index < periods_back:
        return None
    prior_key = periods[index - periods_back]

    current, total_cur, _ = _clean(seg_revs[key])
    prior, total_pri, _ = _clean(seg_revs[prior_key])
    if total_cur <= 0 or total_pri <= 0:
        return None

    names = set(current) | set(prior)
    contributions = [
        SegmentContribution(
            segment=name,
            prior=prior.get(name, 0.0),
            current=current.get(name, 0.0),
        )
        for name in sorted(names)
    ]
    return GrowthAttribution(
        period=key,
        prior_period=prior_key,
        total_prior=total_pri,
        total_current=total_cur,
        contributions=contributions,
    )


def structural_drift(seg_revs: dict[str, dict[str, float]],
                     n_periods: int = 8) -> list[SegmentMix]:
    """最近 n 期的分部构成，按时间正序。用来看占比在不在挪。"""
    if not seg_revs:
        return []
    out = []
    for key in sorted(seg_revs)[-n_periods:]:
        mix = segment_mix(seg_revs, key)
        if mix is not None:
            out.append(mix)
    return out


def max_share_swing(drift: list[SegmentMix]) -> tuple[str, float] | None:
    """漂移最大的那个分部，及其占比变动（当期 - 最早期，百分点的小数形式）。

    只看首尾两期，不看路径：结构分析问的是"现在这门生意跟当初是不是同
    一门"，中间来回震荡但回到原点的，比率外推仍然成立。
    """
    if len(drift) < 2:
        return None
    first, last = drift[0].shares, drift[-1].shares
    names = set(first) | set(last)
    if not names:
        return None
    swings = {n: last.get(n, 0.0) - first.get(n, 0.0) for n in names}
    worst = max(swings, key=lambda n: abs(swings[n]))
    return worst, swings[worst]


def assess(ticker: str, seg_revs: dict[str, dict[str, float]],
           *, periods_back: int = 4, n_periods: int = 8) -> StructureAssessment:
    """把上面几件事合成一份结论，并标出需要人看一眼的地方。

    flags 是"这个结构有值得警惕的地方"，notes 是"这次分析本身有什么限制"。
    两者刻意分开：把"数据不够所以看不出问题"混进"没发现问题"，是这类
    自动化分析最容易犯、也最贵的错。
    """
    result = StructureAssessment(ticker=ticker.upper())
    result.mix = segment_mix(seg_revs)

    if result.mix is None:
        result.notes.append(
            "SEC XBRL 里没取到分部收入——可能是公司不按分部披露，也可能是"
            "edgar_fetcher 没覆盖这个 ticker。结论是「看不出结构」，不是「结构没问题」。"
        )
        return result

    if result.mix.n_segments < MIN_SEGMENTS_FOR_STRUCTURE:
        result.notes.append(
            f"只报了 {result.mix.n_segments} 个分部，拆不出构成。单一分部披露"
            "在 SaaS 里很常见，这说明披露颗粒度不够，不说明业务集中或分散。"
        )

    if result.mix.residual_share >= 0.05:
        result.notes.append(
            f"分部之和比公司报的 Total 少 {result.mix.residual_share * 100:.1f}%"
            "（未分配/抵消项）。这部分没有摊回各分部——摊回去等于替公司做了"
            "一个它自己没做的假设。占比结论按此打折看。"
        )

    result.attribution = growth_attribution(seg_revs, periods_back=periods_back)
    result.drift = structural_drift(seg_revs, n_periods=n_periods)

    # ── 结构集中度 ────────────────────────────────────────────────
    if result.mix.n_segments >= MIN_SEGMENTS_FOR_STRUCTURE:
        eff = result.mix.effective_segments
        if eff < 1.5:
            result.flags.append(
                f"等效分部数 {eff:.1f}（报了 {result.mix.n_segments} 个分部，"
                f"最大的占 {result.mix.top_share * 100:.0f}%）——名义上多元，"
                "实际是单腿生意。按多元化公司给估值溢价在这里不成立。"
            )

    # ── 增长的单点依赖 ────────────────────────────────────────────
    attr = result.attribution
    if attr is not None:
        top = attr.top_contributor
        share = attr.share_of_growth(top) if top else None
        if top is not None and share is not None and share >= 0.80:
            without = attr.growth_without_top
            tail = (f"；该分部增速归零则整体同比增速从 "
                    f"{attr.total_growth * 100:.0f}% 掉到 {without * 100:.0f}%"
                    if attr.total_growth is not None and without is not None else "")
            result.flags.append(
                f"同比增量的 {share * 100:.0f}% 来自「{top.segment}」一个分部{tail}。"
                "成长分建立在一件事继续发生上，不是几件事的平均。"
            )
        if attr.total_growth is not None and attr.total_growth < 0:
            result.notes.append(
                "整体收入同比为负，增量归因的百分比不适用（分母为负时比例会"
                "反号）。这一期该看各分部 delta 的绝对值，谁在拖。"
            )

    # ── 结构漂移 ──────────────────────────────────────────────────
    swing = max_share_swing(result.drift)
    if swing is not None:
        name, change = swing
        if abs(change) >= 0.10:
            span = f"{result.drift[0].period} → {result.drift[-1].period}"
            result.flags.append(
                f"「{name}」占比在 {span} 间变动 {change * 100:+.0f} 个百分点。"
                "结构在挪，历史比率（ROIC/毛利率/PEG）外推到明年时，讲的"
                "已经不完全是同一门生意。"
            )

    return result
