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
    unit: str = "%"

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
    LimitSpec(
        key="liquidity_days_max",
        label="最大退出天数",
        kind="max",
        unit="天",
        help_text=(
            "任何单一标的，按不超过其日均成交额20%的节奏平仓，需要多少个交易日"
            "才能清完。管的不是「该不该持有这么多」，是「真要卖的时候卖不卖得掉」——"
            "一个仓位可能完全没超单股/产业链上限，但如果标的本身成交清淡，"
            "同样规模的仓位可能要花很多天才能不砸盘出清。"
        ),
    ),
)

# ── 风险快照类限额（杠杆/Beta-Delta/压力测试/回撤） ─────────────────────
# 2026-09-10 审计 F-08：这几条以前是 account_monitor.py 里的一个模块级
# 字典（_RISK_LIMITS），跟 account/risk.py::DEFAULT_RISK_LIMITS、
# account/risk_signals.py::risk_snapshot_signals() 的默认参数三处各写一份
# 同样的数字——没有变更记录，改哪一处都不会同步到另外两处。跟上面四条
# 仓位限额一样，走同一套哈希链治理（同一个 position_limits.jsonl 文件，
# key 不会撞，见 effective_risk_limits()）。

# 现状读数来自 account_monitor._compute_risk_snapshot()，不是
# scoring.position_exposure.Exposures——两者是完全不同的数据源（一个是
# 组合构成，一个是杠杆/压力测试快照），所以是独立的一组 LimitSpec，
# 不跟上面 LIMIT_SPECS 混在同一个循环里。
#
# 单位约定：杠杆/Beta-Delta 两条是原始倍数（4.0 就是 4.0x，读数和限额
# 直接比，不做换算）；压力测试/回撤三条用百分点存储和展示（15.0 表示
# 15%），跟上面四条仓位限额的展示习惯一致——但 account/risk.py 和
# account/risk_signals.py 里实际做比较的函数（classify_stress_status
# 等）吃的是小数（0.15），换算只在 account_monitor.py 读取限额时做一次，
# 不改动那些比较函数本身，避免把单位错误引入真正判断超没超限的代码。
RISK_SNAPSHOT_LIMIT_SPECS: tuple[LimitSpec, ...] = (
    LimitSpec(
        key="max_leverage", label="Delta杠杆上限", kind="max", unit="x",
        help_text="总敞口（Delta口径）/净值的杠杆倍数上限。",
    ),
    LimitSpec(
        key="max_beta_delta_ratio", label="Beta-Delta比率上限", kind="max", unit="x",
        help_text="Beta加权Delta敞口/净值——跟大盘的等效敞口倍数上限。",
    ),
    LimitSpec(
        key="stress_warning", label="压力测试-警示线", kind="max", unit="%",
        help_text="大盘跌10%情景下的损失占净值比例，达到这条线触发黄色警示。",
    ),
    LimitSpec(
        key="stress_de_risk", label="压力测试-去风险线", kind="max", unit="%",
        help_text="大盘跌10%情景下的损失占净值比例，达到这条线建议主动去风险。",
    ),
    LimitSpec(
        key="stress_hard_stop", label="压力测试-硬止损线", kind="max", unit="%",
        help_text="大盘跌10%情景下的损失占净值比例，达到这条线是强制止损级别的警戒。",
    ),
    LimitSpec(
        key="drawdown_freeze", label="回撤-冻结新仓线", kind="max", unit="%",
        help_text="账户实际回撤（时间加权，剔除出入金）达到这条线，冻结新增风险。",
    ),
    LimitSpec(
        key="drawdown_de_risk", label="回撤-强制去风险线", kind="max", unit="%",
        help_text="账户实际回撤达到这条线，强制去风险。",
    ),
)

RISK_SNAPSHOT_LIMIT_BY_KEY = {spec.key: spec for spec in RISK_SNAPSHOT_LIMIT_SPECS}

# 通用查找表——两组限额的并集。evaluate_change()/变更历史展示这类只
# 关心"这个 key 对应哪个 spec"的代码走这个，不用关心它属于哪一组；
# 只有需要"专门列出仓位限额那4条"或"专门列出风险快照那7条"的地方
# （现状卡片的两个循环）才分别用 LIMIT_SPECS / RISK_SNAPSHOT_LIMIT_SPECS。
LIMIT_BY_KEY = {spec.key: spec for spec in (*LIMIT_SPECS, *RISK_SNAPSHOT_LIMIT_SPECS)}

# 2026-09-10 之前硬编码在三处的默认值，原样保留在这里作为"注册表里还没有
# 这条记录时"的兜底——只有第一次跑、jsonl 里那个 key 还没有任何记录时才
# 会用到；一旦通过 evaluate_change/append_snapshot 写过一条 initial 记录，
# 就永远从 jsonl 读，不再看这个字典。
RISK_SNAPSHOT_LIMIT_DEFAULTS: dict[str, float] = {
    "max_leverage":          4.0,
    "max_beta_delta_ratio":  3.5,
    "stress_warning":        8.0,
    "stress_de_risk":        12.0,
    "stress_hard_stop":      15.0,
    "drawdown_freeze":       20.0,
    "drawdown_de_risk":      30.0,
}


def effective_risk_limits(
    history: Iterable[Mapping[str, Any]],
    now: dt.datetime,
) -> dict[str, float]:
    """当前生效的 7 条风险快照类限额，注册表里没有记录的 key 用硬编码
    默认值兜底（未设定 ≠ 不设限，这几条从一开始就有安全默认值，跟仓位
    限额那 4 条"未设定就不评估"的哲学不同——杠杆/压力这几条不能没有
    默认上限）。返回的是百分点/倍数原始存储单位，不是 account_monitor.py
    比较函数要的小数——调用方自己按需要 /100。
    """
    out = dict(RISK_SNAPSHOT_LIMIT_DEFAULTS)
    for key in RISK_SNAPSHOT_LIMIT_BY_KEY:
        value = effective_limit(history, key, now)
        if value is not None:
            out[key] = float(value)
    return out


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
