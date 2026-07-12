"""Single source of truth for ENERGREX score bands and decision gates.

Final Score describes overall candidate quality.  It is not a trade order.
An actionable conclusion additionally requires valuation alignment, valid data,
and no unresolved human-review flag.
"""

from __future__ import annotations

from dataclasses import dataclass


# Version 6 adds the valuation-integrity gate and invalidates cached decisions.
POLICY_VERSION = 6


SCORE_BANDS = (
    (80.0, "⭐ 综合强劲"),
    (65.0, "✅ 综合良好"),
    (50.0, "👀 综合中性"),
    (35.0, "⚠️ 谨慎评估"),
    (0.0, "🚫 风险较高"),
)


def score_band(final_score: float) -> str:
    """Return a non-actionable quality band for a 0-100 Final Score."""
    score = float(final_score or 0.0)
    for threshold, label in SCORE_BANDS:
        if score >= threshold:
            return label
    return "🚫 风险较高"


def data_gate_status(
    validity_rate: float | None,
    *,
    human_review_required: bool = False,
    validation_status: str | None = None,
) -> str:
    """Return PASS, PARTIAL, or REVIEW_REQUIRED under the 95% validity gate."""
    if human_review_required:
        return "REVIEW_REQUIRED"
    status = str(validation_status or "").upper()
    if status in {"REVIEW_REQUIRED", "CRITICAL", "FAIL", "FAILED"}:
        return "REVIEW_REQUIRED"
    rate = float(validity_rate or 0.0)
    if rate >= 0.95 and status in {"", "PASS"}:
        return "PASS"
    if rate >= 0.85:
        return "PARTIAL"
    return "REVIEW_REQUIRED"


@dataclass(frozen=True)
class Decision:
    label: str
    status: str
    actionable: bool
    reason: str
    score_band: str


def valuation_integrity_gate(
    forward_pe: float | None,
    ev_sales: float | None,
    fcf_yield: float | None,
) -> tuple[str, str]:
    """Detect a high-multiple contradiction without rewriting the quality score.

    A company can be excellent while still having a price that requires a
    separate valuation review. Requiring all three signals avoids a veto based
    on any single industry-specific multiple.
    """
    try:
        fpe = float(forward_pe) if forward_pe is not None else None
        evs = float(ev_sales) if ev_sales is not None else None
        fcfy = float(fcf_yield) if fcf_yield is not None else None
    except (TypeError, ValueError):
        return "PASS", ""

    if fpe is not None and evs is not None and fcfy is not None and (
        fpe >= 40.0 and evs >= 12.0 and fcfy <= 0.02
    ):
        return (
            "REVIEW_REQUIRED",
            "远期PE≥40、EV/Sales≥12且FCF收益率≤2%；优质基本面不能替代估值复核",
        )
    return "PASS", ""


def evaluate_decision(
    final_score: float,
    valuation_score: float | None,
    validity_rate: float | None,
    *,
    human_review_required: bool = False,
    validation_status: str | None = None,
    forward_pe: float | None = None,
    ev_sales: float | None = None,
    fcf_yield: float | None = None,
) -> Decision:
    """Apply quality, valuation, and evidence gates to the displayed conclusion."""
    final = float(final_score or 0.0)
    valuation = None if valuation_score is None else float(valuation_score)
    band = score_band(final)
    gate = data_gate_status(
        validity_rate,
        human_review_required=human_review_required,
        validation_status=validation_status,
    )
    valuation_gate, valuation_reason = valuation_integrity_gate(
        forward_pe, ev_sales, fcf_yield
    )

    if gate != "PASS":
        high_price = valuation is not None and valuation < 60
        return Decision(
            "🧾 数据待复核（高价区）" if high_price else "🧾 数据待复核",
            gate,
            False,
            (
                "数据有效率未达到95%或仍有关键字段待人工复核；当前估值分低于60"
                if high_price else
                "数据有效率未达到95%或仍有关键字段待人工复核"
            ),
            band,
        )
    if valuation is None:
        return Decision("🧾 估值待复核", "REVIEW_REQUIRED", False, "缺少可用估值分", band)
    if valuation_gate != "PASS":
        return Decision(
            "⚠️ 高估值待验证",
            "VALUATION_REVIEW",
            False,
            valuation_reason,
            band,
        )
    if valuation < 60:
        return Decision("⚠️ 高价观察", "HIGH_PRICE", False, "估值分低于60，价格门槛否决新增仓位", band)
    if final >= 80 and valuation >= 75:
        return Decision("⭐ 重点候选", "ALIGNED", True, "综合分与估值均通过高标准门槛", band)
    if final >= 65:
        return Decision("✅ 候选", "ALIGNED", True, "综合分不低于65且估值分不低于60", band)
    if final >= 50:
        return Decision("👀 观察", "WATCH", False, "综合分不足65，暂不形成可执行结论", band)
    if final >= 35:
        return Decision("⚠️ 谨慎", "CAUTION", False, "综合质量与风险回报不足", band)
    return Decision("🚫 回避", "AVOID", False, "综合分低于35", band)
