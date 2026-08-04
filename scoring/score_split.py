"""Report the score as three separate readings instead of one blended number.

The single final_score answers three unrelated questions at once and then
multiplies the answers together:

    (五个基本面维度 + 动量) 加权  −  风险扣分  ×  熔断乘数

Two consequences show up in the live data. MU reads 52.7 while its five
fundamental dimensions average 78.2 -- nothing about the company changed, its
beta crossed 2.2. APP reads 53.5 against a company score of 92.4, a 39-point
gap with nothing on screen to explain it. Fourteen of the eighty-seven
tracked tickers are currently circuit-broken and the interface never says so.

The circuit also fires for two unrelated reasons. IBM trips it on leverage
alone at a beta of 0.70 -- calmer than the index -- while ARM trips it on a
beta of 3.91. Collapsing both into one "数据待复核" label loses the only part
that matters.

Nothing here re-scores anything. Every input is already computed and stored
separately on ScoreResult; this module only stops multiplying them together.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from .formula import (
        CIRCUIT_BETA_THRESH,
        CIRCUIT_DE_THRESH,
        CIRCUIT_DRAWDOWN_THRESH,
        RISK_MAX_PENALTY,
    )
except ImportError:  # pragma: no cover - direct import path
    from formula import (
        CIRCUIT_BETA_THRESH,
        CIRCUIT_DE_THRESH,
        CIRCUIT_DRAWDOWN_THRESH,
        RISK_MAX_PENALTY,
    )

# The five dimensions that describe the business. Weights are the production
# ones; they sum to 0.90 because momentum holds the remaining 0.10, so they
# are renormalised here rather than silently understating every company.
COMPANY_WEIGHTS: dict[str, float] = {
    "valuation": 0.20,
    "growth": 0.25,
    "quality": 0.15,
    "ai_exposure": 0.20,
    "expectation_gap": 0.10,
}

VOLATILITY_CLAUSE = "波动"
LEVERAGE_CLAUSE = "杠杆"


@dataclass
class CircuitDetail:
    triggered: bool = False
    clauses: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if not self.triggered:
            return ""
        return "熔断 · " + " + ".join(self.clauses)

    @property
    def detail(self) -> str:
        return "；".join(self.reasons)


@dataclass
class ScoreSplit:
    company: float | None
    momentum: float | None
    risk: float | None
    circuit: CircuitDetail
    blended: float | None = None

    @property
    def company_vs_blended(self) -> float | None:
        """How much the blend moves the company reading. The gap is the part
        the single number hides."""
        if self.company is None or self.blended is None:
            return None
        return self.company - self.blended


def company_score(dim_scores: dict[str, float]) -> float | None:
    """Weighted business quality, renormalised over the dimensions present.

    Momentum is deliberately absent: where a price sits relative to its
    200-day average says nothing about whether the business is any good.
    """
    total_weight = 0.0
    total = 0.0
    for dim, weight in COMPANY_WEIGHTS.items():
        value = dim_scores.get(dim)
        if value is None:
            continue
        total += float(value) * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return total / total_weight


def momentum_score(dim_scores: dict[str, float]) -> float | None:
    value = dim_scores.get("momentum")
    return None if value is None else float(value)


def risk_score(risk_penalty: float | None, max_penalty: float = RISK_MAX_PENALTY) -> float | None:
    """Penalty points turned into a 0-100 reading where 100 is calm.

    The circuit is deliberately excluded. It is a threshold flag, not a
    magnitude, and averaging it into a smooth score is what made a
    seventeen-point cliff look like a gentle decline.
    """
    if risk_penalty is None or max_penalty <= 0:
        return None
    scaled = 100.0 - (float(risk_penalty) / max_penalty) * 100.0
    return max(0.0, min(100.0, scaled))


def circuit_detail(
    beta: float | None,
    drawdown_abs: float | None,
    de_ratio: float | None,
) -> CircuitDetail:
    """Which clause fired, kept separate because they mean different things.

    `drawdown_abs` is a magnitude: 0.39 for a 39% drawdown.
    """
    result = CircuitDetail()

    if (
        beta is not None
        and drawdown_abs is not None
        and beta > CIRCUIT_BETA_THRESH
        and abs(drawdown_abs) > CIRCUIT_DRAWDOWN_THRESH
    ):
        result.clauses.append(VOLATILITY_CLAUSE)
        result.reasons.append(
            f"Beta {beta:.2f} > {CIRCUIT_BETA_THRESH} 且 回撤 {abs(drawdown_abs):.1%} > "
            f"{CIRCUIT_DRAWDOWN_THRESH:.0%}"
        )

    if de_ratio is not None and de_ratio > CIRCUIT_DE_THRESH:
        result.clauses.append(LEVERAGE_CLAUSE)
        result.reasons.append(f"负债权益比 {de_ratio:.2f} > {CIRCUIT_DE_THRESH}")

    result.triggered = bool(result.clauses)
    return result


def split_scores(
    dim_scores: dict[str, float],
    *,
    risk_penalty: float | None = None,
    beta: float | None = None,
    drawdown_abs: float | None = None,
    de_ratio: float | None = None,
    blended: float | None = None,
) -> ScoreSplit:
    return ScoreSplit(
        company=company_score(dim_scores),
        momentum=momentum_score(dim_scores),
        risk=risk_score(risk_penalty),
        circuit=circuit_detail(beta, drawdown_abs, de_ratio),
        blended=blended,
    )
