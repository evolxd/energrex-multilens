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
    # 2026-09-19 用户拍板扩大：MSFT/GOOGL/AMZN/META/AAPL(MEGA_TECH)和
    # PANW/CRWD/FTNT/ZS/OKTA(CYBERSECURITY)确实有真实、非平凡的AI暴露。
    # 已知风险：这几家的 ai_revenue_exposure_pct/ai_profit_exposure_pct
    # 目前仍是人工估算/代理值（"AI收入/利润暴露均值"或"AI平台/产业链代理
    # 值"），不像NVDA/MRVL/PLTR有SEC 10-Q/8-K自动提取支撑（confidence
    # H/M）。用户已知晓这个数据质量差距、明确要求现在就放开，不是等数据
    # 溯源升级之后再做——分类阈值(classify_ai_profile里的exposure>=0.30)
    # 没变，所以不是整个类目一次性通过：同一批公司里AAPL(0.19)/FTNT
    # (0.235)现有暴露值仍低于门槛，还是会走AI_ENABLED/行业暴露那条路。
    "MEGA_TECH",
    "CYBERSECURITY",
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
        # Non-core AI evidence is informative, but often relies on manual
        # revenue/profit allocation or product-feature proxies. It may prevent
        # an AI penalty through the neutral baseline, never create a second
        # additive reward until a separately audited evidence layer exists.
        key, bonus = AI_ENABLED, 0.0
    else:
        key, bonus = QUALITY_TRADITIONAL, 0.0

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


def score_ai_role(
    raw_ai_score: float,
    profile_key: str,
    peer_industry_score: float | None = None,
) -> float:
    """Keep AI core signals; route everyone else to a real industry-standing
    read when one exists, otherwise a neutral baseline.

    2026-09-18 用户拍板："永远只在经典AI股里去算AI暴露，其他的就算行业暴露"——
    不再用同一个中性50分把所有非核心AI公司糊在一起。profile_key能拿到
    AI_CORE的类目见CORE_ELIGIBLE_CATEGORIES（2026-09-19一度确认不扩大，
    同一天晚些时候用户又推翻这个决定，把MEGA_TECH/CYBERSECURITY加了进去，
    见该常量上的注释），非核心公司现在有了第二条出路：peer_industry_score
    ——由refresh_scores.py在批处理层用同类目quality_score百分位算出来的
    "本行业内地位"读数，同类目样本不够（<5家）时仍然是None，退回中性50。
    """
    if profile_key == AI_CORE:
        return float(raw_ai_score)
    if peer_industry_score is not None:
        return float(peer_industry_score)
    return AI_NEUTRAL_SCORE
