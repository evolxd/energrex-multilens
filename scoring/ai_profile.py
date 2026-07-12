"""AI-role routing layer for profile-aware ENERGREX scoring."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


AI_CORE = "AI_CORE"
AI_ENABLED = "AI_ENABLED"
QUALITY_TRADITIONAL = "QUALITY_TRADITIONAL"
AI_UNVERIFIED = "AI_UNVERIFIED"
AI_NEUTRAL_SCORE = 50.0

PROFILE_LABELS = {
    AI_CORE: "AI核心型",
    AI_ENABLED: "AI赋能型",
    QUALITY_TRADITIONAL: "传统优质型",
    AI_UNVERIFIED: "AI待验证",
}

PROFILE_WEIGHTS = {
    AI_CORE: {
        "valuation": 0.20,
        "growth": 0.25,
        "quality": 0.15,
        "ai_exposure": 0.20,
        "expectation_gap": 0.10,
        "momentum": 0.10,
    },
    AI_ENABLED: {
        "valuation": 0.20,
        "growth": 0.25,
        "quality": 0.15,
        "ai_exposure": 0.20,
        "expectation_gap": 0.10,
        "momentum": 0.10,
    },
    QUALITY_TRADITIONAL: {
        "valuation": 0.20,
        "growth": 0.25,
        "quality": 0.15,
        "ai_exposure": 0.20,
        "expectation_gap": 0.10,
        "momentum": 0.10,
    },
    AI_UNVERIFIED: {
        "valuation": 0.20,
        "growth": 0.25,
        "quality": 0.15,
        "ai_exposure": 0.20,
        "expectation_gap": 0.10,
        "momentum": 0.10,
    },
}

CORE_ELIGIBLE_CATEGORIES = {
    "AI_CHIP",
    "AI_SOFTWARE",
}


@dataclass(frozen=True)
class AIProfile:
    key: str
    label: str
    exposure: float | None
    bonus: float
    basis: str
    weights: dict[str, float]


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def estimate_ai_exposure(data: dict) -> tuple[float | None, str]:
    """Prefer revenue/profit exposure; use operating proxies only as fallback."""
    primary = [
        _number(data.get("ai_revenue_exposure_pct")),
        _number(data.get("ai_profit_exposure_pct")),
    ]
    primary = [value for value in primary if value is not None]
    if primary:
        return sum(primary) / len(primary), "AI收入/利润暴露均值"

    proxies = [
        _number(data.get("software_ai_platform_exposure_pct")),
        _number(data.get("cybersecurity_ai_exposure_pct")),
        _number(data.get("datacenter_exposure_pct")),
        _number(data.get("advanced_packaging_exposure_pct")),
    ]
    proxies = [value for value in proxies if value is not None]
    if proxies:
        return max(proxies), "AI平台/产业链代理值"
    return None, "缺少可用AI暴露数据"


def classify_ai_profile(data: dict, category_name: str) -> AIProfile:
    exposure, basis = estimate_ai_exposure(data)
    if exposure is None:
        key, bonus = AI_UNVERIFIED, 0.0
    elif exposure >= 0.30 and category_name in CORE_ELIGIBLE_CATEGORIES:
        key, bonus = AI_CORE, 0.0
    elif exposure >= 0.10:
        key = AI_ENABLED
        bonus = min(5.0, max(0.0, (exposure - 0.10) / 0.20 * 5.0))
    else:
        key = QUALITY_TRADITIONAL
        bonus = min(3.0, max(0.0, exposure / 0.10 * 3.0))

    weights = dict(PROFILE_WEIGHTS[key])
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"AI profile weights must sum to 1.0: {key}")
    return AIProfile(
        key=key,
        label=PROFILE_LABELS[key],
        exposure=exposure,
        bonus=round(bonus, 2),
        basis=basis,
        weights=weights,
    )


def score_ai_role(raw_ai_score: float, profile_key: str) -> float:
    """Keep AI core signals; give other business models a neutral AI baseline."""
    if profile_key == AI_CORE:
        return float(raw_ai_score)
    return AI_NEUTRAL_SCORE
