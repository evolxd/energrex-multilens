"""Governance for portfolio limits: easy to tighten, hard to loosen.

The asymmetry is the whole design. Lowering a single-stock cap from 15% to
10% reduces risk, so it takes effect immediately. Raising it from 15% to 25%
increases risk, and the moment you most want to do that -- a position running
hard, a thesis feeling obvious -- is the moment the limit was written for.
So loosening has to survive five gates.

The most important gate is the breach lock. Without it the rest is theatre:
hold 22% of NVDA against a 15% cap, raise the cap to 25%, and you are
"compliant" again having changed nothing about the risk.

Note that the three limits do not loosen in the same direction. Single-stock
and chain caps are maxima, so loosening raises them. The cash floor is a
minimum, so loosening *lowers* it. Getting this backwards would put all the
friction on rebuilding your cash buffer and none on spending it.

The governance parameters below are deliberately module constants rather than
settings in the UI. Changing them requires editing this file and committing
it, which is exactly the amount of friction that a rule about resisting
impulses deserves.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

# ── Governance parameters ──────────────────────────────────────────────
# Long enough to outlast a news cycle and a full trading week. Emotional
# decisions rarely survive seven days; considered ones lose nothing by waiting.
COOLING_OFF_DAYS = 7

# A loosening may not move a limit more than this many percentage points at
# once. Paired with the rate limit below -- a step cap without a frequency cap
# is defeated by taking the step repeatedly.
MAX_LOOSEN_STEP_PCT = 5.0

# One loosening per limit per this many days.
LOOSEN_COOLDOWN_DAYS = 30

# A reason short enough to type without thinking is not a reason.
MIN_REASON_CHARS = 20

# A limit created minutes ago has not governed any decision yet, so it carries
# no commitment and a typo in it should be fixable. Once this window closes it
# is binding forever. This can only ever be used once per limit.
CORRECTION_WINDOW_HOURS = 24


LimitKind = Literal["max", "min"]


@dataclass(frozen=True)
class LimitSpec:
    key: str
    label: str
    kind: LimitKind
    help_text: str = ""

    def is_loosening(self, old_value: float, new_value: float) -> bool:
        """A max limit loosens upward; a min limit loosens downward."""
        if self.kind == "max":
            return new_value > old_value
        return new_value < old_value

    def is_breached(self, exposure: float, limit_value: float) -> bool:
        if self.kind == "max":
            return exposure > limit_value
        return exposure < limit_value


LIMIT_SPECS: tuple[LimitSpec, ...] = (
    LimitSpec(
        key="single_stock_max",
        label="单股最高仓位",
        kind="max",
        help_text="任何单一标的占总资产的上限。限制一次判断错误的破坏半径。",
    ),
    LimitSpec(
        key="chain_max",
        label="单产业链最高仓位",
        kind="max",
        help_text="同一产业链（AI芯片 / AI软件 / 网络安全 / 半导体设备 / 大型科技）合计上限。",
    ),
    LimitSpec(
        key="cash_floor_min",
        label="最低现金比例",
        kind="min",
        help_text="现金占总资产的下限。这是一条下限：放宽意味着调低。",
    ),
)

LIMIT_BY_KEY = {spec.key: spec for spec in LIMIT_SPECS}


@dataclass
class Verdict:
    allowed: bool
    direction: Literal["initial", "tighten", "loosen", "unchanged"]
    effective_at: dt.datetime | None = None
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def takes_effect_immediately(self) -> bool:
        return self.allowed and not self.blockers and self.effective_at is not None


def _parse(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def history_for(history: Iterable[Mapping[str, Any]], key: str) -> list[dict]:
    """Audit records for one limit, oldest first."""
    out = []
    for record in history:
        payload = record.get("payload", record)
        if payload.get("key") == key:
            out.append(dict(payload))
    return out


def effective_limit(
    history: Iterable[Mapping[str, Any]],
    key: str,
    now: dt.datetime,
) -> float | None:
    """The value that governs right now.

    A loosening recorded but still inside its cooling-off period does not
    govern yet -- that is the entire point of the delay, so this must read the
    last record whose effective_at has actually arrived.
    """
    governing = None
    for payload in history_for(history, key):
        effective_at = _parse(payload.get("effective_at"))
        if effective_at is not None and effective_at <= now:
            governing = payload.get("new_value")
    return governing


def pending_change(
    history: Iterable[Mapping[str, Any]],
    key: str,
    now: dt.datetime,
) -> dict | None:
    """A recorded loosening that has not taken effect yet, if any."""
    for payload in reversed(history_for(history, key)):
        effective_at = _parse(payload.get("effective_at"))
        if effective_at is not None and effective_at > now:
            return payload
    return None


def evaluate_change(
    key: str,
    new_value: float,
    *,
    reason: str,
    now: dt.datetime,
    history: Iterable[Mapping[str, Any]],
    exposure: float | None = None,
) -> Verdict:
    """Decide whether a limit change is allowed, and when it would take effect.

    `exposure` is the current reading of the metric this limit governs -- the
    largest single-stock weight, the largest chain weight, the cash ratio.
    Without it the breach lock cannot be enforced, so a loosening is refused.
    """
    spec = LIMIT_BY_KEY.get(key)
    if spec is None:
        return Verdict(allowed=False, direction="unchanged", blockers=[f"未知的限制项：{key}"])

    records = history_for(history, key)
    old_value = effective_limit(history, key, now)

    # ── First-ever setup, and the correction window that follows it ──
    if not records:
        return Verdict(
            allowed=True,
            direction="initial",
            effective_at=now,
            notes=[f"首次设定，立即生效。{CORRECTION_WINDOW_HOURS} 小时内可自由修正，之后受完整约束。"],
        )

    first_set_at = _parse(records[0].get("timestamp"))
    in_correction_window = (
        first_set_at is not None
        and now - first_set_at <= dt.timedelta(hours=CORRECTION_WINDOW_HOURS)
    )

    if old_value is None:
        old_value = records[-1].get("new_value")

    if new_value == old_value:
        return Verdict(allowed=False, direction="unchanged", blockers=["新值与当前生效值相同。"])

    # ── Tightening is always free ──
    if not spec.is_loosening(float(old_value), float(new_value)):
        return Verdict(
            allowed=True,
            direction="tighten",
            effective_at=now,
            notes=["收紧风险敞口，立即生效。"],
        )

    # ── Loosening ──
    if in_correction_window:
        return Verdict(
            allowed=True,
            direction="loosen",
            effective_at=now,
            notes=[f"仍在首次设定后的 {CORRECTION_WINDOW_HOURS} 小时修正窗口内，免除全部关卡。"],
        )

    blockers: list[str] = []

    # Gate 1: breach lock. The one that makes the rest mean anything.
    if exposure is None:
        blockers.append("无法读取当前持仓敞口，放宽一律拒绝（超限锁无法验证）。")
    elif spec.is_breached(float(exposure), float(old_value)):
        blockers.append(
            f"当前已超限（{exposure:.2f}% vs 上限 {float(old_value):.2f}%）。"
            "超限期间只能通过调整持仓回到线内，不能改线。"
        )

    # Gate 2: written justification.
    if len((reason or "").strip()) < MIN_REASON_CHARS:
        blockers.append(f"需要至少 {MIN_REASON_CHARS} 字的书面理由，将永久入链。")

    # Gate 3: step size.
    step = abs(float(new_value) - float(old_value))
    if step > MAX_LOOSEN_STEP_PCT:
        blockers.append(
            f"单次放宽 {step:.2f} 个百分点，超过上限 {MAX_LOOSEN_STEP_PCT:.0f}。"
        )

    # Gate 4: frequency. Without this, gate 3 is defeated by repetition.
    last_loosen_at = None
    for payload in records:
        if payload.get("direction") == "loosen":
            stamp = _parse(payload.get("timestamp"))
            if stamp is not None:
                last_loosen_at = stamp
    if last_loosen_at is not None:
        elapsed = now - last_loosen_at
        if elapsed < dt.timedelta(days=LOOSEN_COOLDOWN_DAYS):
            remaining = dt.timedelta(days=LOOSEN_COOLDOWN_DAYS) - elapsed
            blockers.append(
                f"距上次放宽不足 {LOOSEN_COOLDOWN_DAYS} 天，还需等待 {remaining.days} 天。"
            )

    # Gate 5: cooling-off. Not a blocker -- it sets when the change lands.
    if blockers:
        return Verdict(allowed=False, direction="loosen", blockers=blockers)

    return Verdict(
        allowed=True,
        direction="loosen",
        effective_at=now + dt.timedelta(days=COOLING_OFF_DAYS),
        notes=[
            f"通过全部关卡。{COOLING_OFF_DAYS} 天冷静期后生效；"
            "期间仍按旧上限执行。"
        ],
    )


def build_record(
    key: str,
    new_value: float,
    verdict: Verdict,
    *,
    reason: str,
    now: dt.datetime,
    exposure: float | None,
    old_value: float | None,
) -> dict:
    """The payload written to the hash-chained audit log."""
    return {
        "key": key,
        "old_value": old_value,
        "new_value": float(new_value),
        "direction": verdict.direction,
        "reason": (reason or "").strip(),
        "timestamp": now.isoformat(timespec="seconds"),
        "effective_at": verdict.effective_at.isoformat(timespec="seconds")
        if verdict.effective_at
        else None,
        "exposure_at_change": float(exposure) if exposure is not None else None,
    }
