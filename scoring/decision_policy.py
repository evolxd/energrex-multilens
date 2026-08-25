"""Single source of truth for ENERGREX score bands and decision gates.

Final Score describes overall candidate quality.  It is not a trade order.
An actionable conclusion additionally requires valuation alignment, valid data,
and no unresolved human-review flag.
"""

from __future__ import annotations

from dataclasses import dataclass


# Version 7 removes non-core AI accelerators, adds a distinct circuit-breaker
# label, and invalidates cached decisions/scores.
POLICY_VERSION = 7


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
    circuit_label: str = "",
) -> Decision:
    """Apply quality, valuation, and evidence gates to the displayed conclusion.

    circuit_label: non-empty when the risk circuit breaker fired (e.g. "波动"
    or "杠杆", see scoring/score_split.py). The breaker already crushed
    final_score by a flat multiplier before this function ever sees it, so
    once data validity and valuation-integrity are confirmed fine, a
    circuit-triggered ticker gets its own label instead of being silently
    relabeled "⚠️ 高价观察" / "🚫 回避" by the generic score thresholds --
    those thresholds describe business quality, not "we don't trust this
    number because volatility/leverage tripped a threshold cliff".
    """
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
    if circuit_label:
        return Decision(
            f"🧾 熔断复核 · {circuit_label}",
            "CIRCUIT",
            False,
            f"风险熔断（{circuit_label}）：综合分已被熔断乘数压低，不代表业务质量判断，需人工复核",
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


def apply_mispricing_gate(
    decision: Decision,
    mispricing_decision: str | None,
    *,
    blocking_reasons: list[str] | tuple[str, ...] = (),
) -> Decision:
    """Apply optional mispricing eligibility without changing any score."""
    status = str(mispricing_decision or "").strip().upper()
    if not status or status in {"DEEP_RESEARCH_P0", "DEEP_RESEARCH_P1"}:
        return decision

    reason = "; ".join(str(item) for item in blocking_reasons if str(item).strip())
    if status == "WAIT_FOR_PRICE":
        return Decision(
            "等待价格",
            "WAIT_FOR_PRICE",
            False,
            reason or "误价逻辑可能成立，但当前价格赔率门未通过",
            decision.score_band,
        )
    if status == "WATCH_P2":
        return Decision(
            "误价证据待复核",
            "MISPRICING_REVIEW",
            False,
            reason or "误价案例仍缺少有效证据或置信度不足",
            decision.score_band,
        )
    if status == "REJECT_P3":
        return Decision(
            "误价逻辑排除",
            "MISPRICING_REJECT",
            False,
            reason or "至少一道误价硬门失败",
            decision.score_band,
        )
    return Decision(
        "误价状态未知",
        "MISPRICING_REVIEW",
        False,
        f"不支持的误价决策状态: {status}",
        decision.score_band,
    )


def apply_v23_case_gate(
    decision: Decision,
    mispricing_decision: str | None,
    *,
    formal_valuation_status: str | None,
    formal_price_gate: str | None,
    blocking_reasons: list[str] | tuple[str, ...] = (),
) -> Decision:
    """Close the V2.3 decision stack without changing Final Score.

    The legacy valuation score may rank price attractiveness, but a V2.3
    mispricing case is not actionable until a formal multi-method valuation
    result has passed the separate valuation contract.
    """
    gated = apply_mispricing_gate(
        decision,
        mispricing_decision,
        blocking_reasons=blocking_reasons,
    )
    if not str(mispricing_decision or "").strip():
        return gated
    if not gated.actionable:
        return gated

    valuation_status = str(formal_valuation_status or "").strip().upper()
    if valuation_status != "PASS":
        return Decision(
            "正式估值待复核",
            "FORMAL_VALUATION_REVIEW",
            False,
            "V2.3 案例缺少通过数据门和双方法门的统一估值结果",
            gated.score_band,
        )
    price_gate = str(formal_price_gate or "").strip().upper()
    if price_gate != "PASS":
        return Decision(
            "等待价格或评估有限风险表达",
            "FORMAL_PRICE_WAIT",
            False,
            "正式价格门未通过；不得用估值分或期权直接绕过",
            gated.score_band,
        )
    return gated
