"""Betas derived here by regression, kept with the evidence behind them.

`_BETA_BASE` in account_monitor.py is a bare symbol -> float map, and anything
missing from it silently falls back to 1.0 in
`account.risk.compute_portfolio_stress_test` (`beta_map.get(sym, 1.0)`). That
default is invisible in the risk snapshot: a holding whose beta was never
measured looks exactly like one measured at 1.00.

It bit two of the largest exposures at once. On 2026-09-19 SPCX and ETHU were
both absent from the table and both running at beta 1.0, while their actual
market sensitivity is roughly 2.6 and 3.4 -- Beta-Delta was understating the
portfolio, in the direction that makes a leveraged book look calmer than it is.

Vendor numbers were no help. Firstrade reported 25.14 for SPCX, which is not a
beta anyone should use: the volatility ratio caps beta at 7.30 even under
perfect correlation, so 25 is arithmetically impossible as a regression result.
Alpha Vantage returned None for both. So they are measured here instead, and
the measurement is stored with the sample that produced it.

The R² is the part worth reading. Both names clear a beta above 2.5 while the
market explains under a fifth of their variance -- beta describes how they move
*when the market moves*, and most of what they do is not that. A hedge sized off
beta alone will under-perform on both of these.

Method: ordinary least squares of daily simple returns against SPY, implemented
in account/beta_regression.py. The values below are seeds; `_refresh_beta_spy()`
re-measures weekly and writes the fit into data/beta_cache.json, which
`load_measured()` prefers. Seeds only apply until the first refresh completes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# Below this, beta is a weak description of the holding and the risk snapshot
# says so rather than presenting the number as equivalent to a well-fit one.
LOW_CONFIDENCE_R2 = 0.30


@dataclass(frozen=True)
class DerivedBeta:
    symbol: str
    beta: float
    r_squared: float
    std_error: float
    sample_start: str
    sample_end: str
    observations: int
    note: str

    @property
    def is_low_confidence(self) -> bool:
        return self.r_squared < LOW_CONFIDENCE_R2

    def describe(self) -> str:
        return (f"{self.symbol} β={self.beta:.2f} (R²={self.r_squared:.2f}, "
                f"{self.observations}日 {self.sample_start}~{self.sample_end})")


DERIVED_BETAS: dict[str, DerivedBeta] = {
    # IPO 2026-06-12. The first five sessions are price discovery, not market
    # sensitivity -- 2026-06-12 traded 522M shares and 06-16 ranged 195->225.64.
    # Including them pulls beta to 3.25; dropping them gives 2.57, and the
    # estimate then holds across every cut tried (skip 3 -> 2.62, skip 5 -> 2.57,
    # skip 10 -> 2.72, skip 15 -> 2.63). That stability is why 2.6 is trusted
    # and 3.25 is not: an estimate that moved with the window would mean the
    # regression was fitting the IPO, not the stock.
    "SPCX": DerivedBeta(
        symbol="SPCX", beta=2.57, r_squared=0.163, std_error=0.75,
        sample_start="2026-06-22", sample_end="2026-09-18", observations=62,
        note="剔除上市后前5个交易日（定价发现期）；年化波动 73.6%",
    ),
    # No IPO effect -- ETHU has a long history -- but the window is matched to
    # SPCX so the two are comparable. Firstrade's 5.44 sits outside the 95%
    # interval [0.88, 4.87]; a 2x ether ETF is violent (104.7% annualised) but
    # that violence is mostly crypto, not the S&P.
    "ETHU": DerivedBeta(
        symbol="ETHU", beta=3.43, r_squared=0.163, std_error=0.79,
        sample_start="2026-04-29", sample_end="2026-09-18", observations=99,
        note="2倍做多以太币ETF；非新上市，用完整窗口不剔除起始日",
    ),
    # 2026-09-21：KLAC/ONTO/PATH 此前同样不在 _BETA_BASE 里，$7,324 的现货一
    # 直按 1.0 处理。这三个用的窗口跟上面两个**不一样**，原因值得写下来：
    #
    # 先按 SPCX/ETHU 那套（近 5 个月日收益）算，得到 KLAC 3.37、ONTO 3.80，
    # 跟券商报的 1.45/1.58 差了一倍多。换成 5 年月收益再算，得到 1.43/1.57
    # ——几乎逐位复现券商的数，说明分歧不是谁算错了，是窗口不同。
    #
    # 决定性的一步是分别算上涨日和下跌日：**下跌日的 beta 在两个窗口下几乎
    # 一致**（KLAC 5个月 2.33 / 5年 2.27；ONTO 2.08 / 2.72），而全样本 beta
    # 差一倍。也就是说短窗那个 3.37 是被 AI capex 行情里的**上涨日**撑起来
    # 的，不是真实的下行敏感度。用它会在错误的方向上高估。
    #
    # 所以这三个取 5 年月度口径，跟 _BETA_BASE 其余条目、跟 SMH 的 1.77 用
    # 同一把尺子。宁可整张表口径一致，也不要为三个标的做局部最优——压力测试
    # 读的是同一张表，混口径的错会比偏低几个点更难查。
    #
    # 遗留问题（不是这三个标的的问题，是整张表的）：压力测试做的是
    # `beta × 负的冲击`，严格说该用下跌日 beta。全表现在用的都是全样本
    # beta，对 KLAC/ONTO 而言这会低估约 0.7~1.1 个 beta 点。要不要整张表
    # 换成下跌日口径，是需要单独决定的事。
    "KLAC": DerivedBeta(
        symbol="KLAC", beta=1.43, r_squared=0.248, std_error=0.326,
        sample_start="2021-10-29", sample_end="2026-09-18", observations=60,
        note="5年月度口径（与_BETA_BASE其余条目一致）；已按2026-06-12的10:1拆股复权。"
             "同期下跌日β=2.27，近5个月日频全样本β=3.37（上涨日撑起来的，未采用）",
    ),
    "ONTO": DerivedBeta(
        symbol="ONTO", beta=1.57, r_squared=0.203, std_error=0.410,
        sample_start="2021-10-29", sample_end="2026-09-18", observations=60,
        note="5年月度口径；同期下跌日β=2.72，近5个月日频全样本β=3.80（未采用）",
    ),
    # PATH 是三个里唯一一个"测了也还是 1.0"的：R²=0.079，标准误 0.444，
    # 95% 区间 [0.12, 1.86] 把 1.0 稳稳包在里面。写进来的价值不在于数字变了，
    # 而在于它从"没人测过、静默取 1.0"变成"测过、确实约等于 1.0"——下次有人
    # 看到 PATH 的 beta 是 1，能查到这是结论不是缺省。
    "PATH": DerivedBeta(
        symbol="PATH", beta=0.99, r_squared=0.079, std_error=0.444,
        sample_start="2021-10-29", sample_end="2026-09-18", observations=60,
        note="5年月度口径；R²=0.08，与大盘基本无关，95%区间[0.12,1.86]涵盖1.0——"
             "这是测出来的1.0，不是缺省的1.0",
    ),
}


def _cache_path():
    import pathlib
    return pathlib.Path(__file__).resolve().parent.parent / "data" / "beta_cache.json"


def load_measured() -> dict[str, DerivedBeta]:
    """The committed seeds, overlaid with whatever the weekly refresh measured.

    `_refresh_beta_spy()` re-runs the regression and writes its fits into
    `data/beta_cache.json`, so these numbers track the market instead of
    freezing at whatever they were the day they were written. The seeds below
    stay as the offline fallback: a machine that has never completed a refresh
    still gets a measured beta rather than the silent 1.0.
    """
    measured = dict(DERIVED_BETAS)
    try:
        import json

        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
        for symbol, fit in (raw.get("fits") or {}).items():
            skipped = fit.get("skipped_initial", 0)
            measured[symbol] = DerivedBeta(
                symbol=symbol,
                beta=float(fit["beta"]),
                r_squared=float(fit.get("r_squared", 0.0)),
                std_error=float(fit.get("std_error", 0.0)),
                sample_start=str(fit.get("sample_start", "")),
                sample_end=str(fit.get("sample_end", "")),
                observations=int(fit.get("observations", 0)),
                note=(f"每周自动回归；剔除起始 {skipped} 个交易日"
                      if skipped else "每周自动回归"),
            )
    except Exception:
        pass            # 缓存缺失/损坏就用种子值，不该因此没有 beta
    return measured


def beta_overrides() -> dict[str, float]:
    """Symbol -> beta, for merging into the main table."""
    return {symbol: entry.beta for symbol, entry in load_measured().items()}


def low_confidence_held(symbols: Iterable[str]) -> list[DerivedBeta]:
    """The held symbols whose beta is a weak fit, worst first.

    Only reports names actually in the portfolio: a warning about a holding you
    do not have is noise, and noise is how the 1.0 default went unnoticed.
    """
    held = {str(s).strip().upper() for s in symbols if s}
    found = [entry for symbol, entry in load_measured().items()
             if symbol in held and entry.is_low_confidence]
    return sorted(found, key=lambda e: e.r_squared)
