"""Detect price/fundamentals basis mismatches before they reach a report.

Price-driven valuation ratios get recomputed from raw balance-sheet fields.
When those fields are not on the same basis as the price -- a different
currency, a different unit, a share count that counts something else -- the
recomputed value is meaningless and nothing raises.

TSM is the live example. The ADR price arrives in USD while revenue, EBITDA
and net debt arrive in TWD, so enterprise value lands around -4.4e11, every
valuation ratio reads "infinitely cheap", the valuation score pins at 97, and
no price anywhere in a 5%-400% simulated grid ever crosses a band. The chart
looked merely odd; the inputs were incoherent.

The check: a row already carries a verified value for each ratio at the
current price, so recomputing at the current price must reproduce it.
"""

from __future__ import annotations

from typing import Mapping

# Only these two ratios are usable as evidence. Measured across the tracked
# universe, the recomputed and carried values agree to a median of 1.00x on
# both, with a legitimate worst case of 2.28x (ev_sales) and 1.56x
# (forward_pe); the only sign flip anywhere is TSM.
#
# peg_ratio drifts up to 14.7x and fcf_yield up to 119x on perfectly good
# rows, because in each case the two sources are not measuring the same
# quantity. Including them refuses roughly half the universe.
BASIS_CHECK_FIELDS = ("ev_sales", "forward_pe")

# Clear of the 2.28x legitimate worst case, while still catching a currency
# substitution, which moves a ratio by an order of magnitude or more.
BASIS_MAX_DRIFT = 5.0


def basis_conflicts(
    carried: Mapping[str, object],
    recomputed: Mapping[str, object],
    fields: tuple[str, ...] = BASIS_CHECK_FIELDS,
    max_drift: float = BASIS_MAX_DRIFT,
) -> list[str]:
    """Return the ratios whose recomputation cannot be reconciled with the
    value carried on the row.

    A sign flip is always a conflict: no amount of staleness turns a positive
    enterprise value negative. Otherwise the two must agree within max_drift
    in either direction.
    """
    conflicts: list[str] = []
    for field in fields:
        left = carried.get(field)
        right = recomputed.get(field)
        if left is None or right is None:
            continue
        try:
            left = float(left)
            right = float(right)
        except (TypeError, ValueError):
            continue
        if left == 0.0 or right == 0.0:
            # A genuine zero carries no ratio information either way.
            continue
        if (left > 0) != (right > 0):
            conflicts.append(field)
            continue
        drift = abs(right) / abs(left)
        if drift > max_drift or drift < 1.0 / max_drift:
            conflicts.append(field)
    return conflicts


def basis_conflict_reason(conflicts: list[str]) -> str:
    """Operator-facing explanation, per PRICE_BOUNDARY_STANDARD.md: when a
    boundary cannot be calculated, show the reason rather than invent one."""
    return (
        "原始财务字段与股价基准不一致（"
        + "、".join(conflicts)
        + "），价格情景重算结果不可信，故不出图。"
        "最常见原因是财报本币与股价币种不同（例如美股 ADR 价格配本币报表）。"
    )
