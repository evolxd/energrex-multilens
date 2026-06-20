"""
季度基准漂移检测
================
将当前市场数据与上季度基准快照对比，
当核心估值指标偏离超过阈值时触发预警。

用途：防止季报后数据滑坡（如增速放缓导致 ERG 暗中恶化）
      以及市场情绪扭转导致 PEG/EV 倍数隐性跳升。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
# 2026-Q1 基准快照（建立于 mock_data 初始值）
# 每季更新：季报后手动替换 BASELINE_CURRENT，
# 旧快照移入 BASELINE_PREV。
# ─────────────────────────────────────────────
BASELINE_2026Q1: dict[str, dict] = {
    "NVDA": {
        "peg_ratio": 0.90, "ev_ebitda": 32.6, "ev_sales": 17.5,
        "forward_pe": 27.0, "revenue_growth_yoy": 0.85, "fcf_yield": 0.0243,
    },
    "AVGO": {
        "peg_ratio": 0.92, "ev_ebitda": 24.0, "ev_sales": 13.0,
        "forward_pe": 22.0, "revenue_growth_yoy": 0.44, "fcf_yield": 0.038,
    },
    "PLTR": {
        "peg_ratio": 2.20, "ev_ebitda": 155.0, "ev_sales": 38.0,
        "forward_pe": 120.0, "revenue_growth_yoy": 0.36, "fcf_yield": 0.008,
    },
    "PANW": {
        "peg_ratio": 1.50, "ev_ebitda": 62.0, "ev_sales": 12.5,
        "forward_pe": 52.0, "revenue_growth_yoy": 0.15, "fcf_yield": 0.024,
    },
    "CRWD": {
        "peg_ratio": 1.85, "ev_ebitda": 105.0, "ev_sales": 22.0,
        "forward_pe": 95.0, "revenue_growth_yoy": 0.25, "fcf_yield": 0.012,
    },
    "FTNT": {
        "peg_ratio": 1.10, "ev_ebitda": 35.0, "ev_sales": 8.5,
        "forward_pe": 32.0, "revenue_growth_yoy": 0.14, "fcf_yield": 0.032,
    },
    "NOW": {
        "peg_ratio": 1.70, "ev_ebitda": 72.0, "ev_sales": 20.0,
        "forward_pe": 58.0, "revenue_growth_yoy": 0.22, "fcf_yield": 0.018,
    },
    "ONTO": {
        "peg_ratio": 1.20, "ev_ebitda": 22.0, "ev_sales": 7.5,
        "forward_pe": 30.0, "revenue_growth_yoy": 0.22, "fcf_yield": 0.030,
    },
    "MRVL": {
        "peg_ratio": 0.62, "ev_ebitda": 28.0, "ev_sales": 10.0,
        "forward_pe": 22.0, "revenue_growth_yoy": 0.48, "fcf_yield": 0.025,
    },
    "SNOW": {
        "peg_ratio": 2.80, "ev_ebitda": 185.0, "ev_sales": 10.5,
        "forward_pe": 155.0, "revenue_growth_yoy": 0.33, "fcf_yield": 0.006,
    },
}

BASELINE_CURRENT = BASELINE_2026Q1  # 当前活跃基准


# ─────────────────────────────────────────────
# 漂移预警条目
# ─────────────────────────────────────────────
@dataclass
class DriftAlert:
    ticker:    str
    field:     str
    baseline:  float
    current:   float
    pct_change: float     # (current - baseline) / baseline
    severity:  str        # "warning" (>25%) | "critical" (>50%)
    direction: str        # "worse" | "better"

    def label(self) -> str:
        sign = "+" if self.pct_change > 0 else ""
        return f"{self.ticker} {self.field}: {sign}{self.pct_change*100:.0f}%"


# 漂移监控字段及方向（higher_is_better=False 表示越低越好，涨了就是变贵）
_DRIFT_FIELDS: dict[str, tuple[bool, str]] = {
    "peg_ratio":          (False, "PEG 倍数"),
    "ev_ebitda":          (False, "EV/EBITDA"),
    "ev_sales":           (False, "EV/Sales"),
    "forward_pe":         (False, "Forward PE"),
    "revenue_growth_yoy": (True,  "收入增长 YoY"),
    "fcf_yield":          (True,  "FCF Yield"),
}

# 预警阈值（相对于基准的百分比变化幅度）
WARN_THRESHOLD     = 0.25   # ≥25% → warning
CRITICAL_THRESHOLD = 0.50   # ≥50% → critical


def calc_drift_alerts(
    current_data: dict[str, dict],
    baseline: dict[str, dict] | None = None,
    warn_pct: float = WARN_THRESHOLD,
    crit_pct: float = CRITICAL_THRESHOLD,
) -> list[DriftAlert]:
    """
    比较 current_data 和 baseline，返回超出阈值的 DriftAlert 列表。

    Parameters
    ----------
    current_data : {ticker: data_dict}   — 当前活跃数据（可含实时/override）
    baseline     : {ticker: {field: val}} — 基准快照（默认用 BASELINE_CURRENT）
    warn_pct     : 警告阈值（0.25 = 25%）
    crit_pct     : 严重阈值（0.50 = 50%）
    """
    if baseline is None:
        baseline = BASELINE_CURRENT

    alerts: list[DriftAlert] = []
    for ticker, data in current_data.items():
        base = baseline.get(ticker, {})
        if not base:
            continue

        for field, (higher_is_better, _label) in _DRIFT_FIELDS.items():
            cur_val  = data.get(field)
            base_val = base.get(field)
            if cur_val is None or base_val is None or base_val == 0:
                continue

            pct = (cur_val - base_val) / abs(base_val)
            abs_pct = abs(pct)
            if abs_pct < warn_pct:
                continue

            # 判断是变好还是变坏
            if higher_is_better:
                direction = "better" if pct > 0 else "worse"
            else:
                direction = "worse" if pct > 0 else "better"

            severity = "critical" if abs_pct >= crit_pct else "warning"
            alerts.append(DriftAlert(
                ticker=ticker,
                field=field,
                baseline=base_val,
                current=float(cur_val),
                pct_change=pct,
                severity=severity,
                direction=direction,
            ))

    # 按严重程度和幅度排序
    alerts.sort(key=lambda a: (0 if a.severity == "critical" else 1, -abs(a.pct_change)))
    return alerts


def summarize_drift(alerts: list[DriftAlert]) -> dict[str, list[DriftAlert]]:
    """按 ticker 分组漂移预警"""
    result: dict[str, list[DriftAlert]] = {}
    for a in alerts:
        result.setdefault(a.ticker, []).append(a)
    return result
