"""
input_verification.py — user_overrides.json 信任门
====================================================
user_overrides.json 里每个字段是 {value, status, verified_at, source} 形状。
`status` 曾经只是展示性元数据——refresh_scores.py 会不加区分地把它的 value
写入 data dict，一个 status:"pending" 的占位符会像 status:"verified" 的真实
核对值一样硬覆盖 mock_data.py 里已经存在的数据（HANDOFF.md §7.1 记录的原始
bug，此前靠手工删除坏的 override 条目临时止血）。

trusted_override_value() 是这个信任门：只有 status=="verified" 的 value 会
被采信，"pending"/"not_applicable"/未知状态一律拒绝，交由调用方继续使用
mock_data.py 或 CSV 里已有的值。
"""

from __future__ import annotations

from typing import Any, Optional


def trusted_override_value(field: str, entry: Any) -> Optional[float]:
    """
    entry 应为 {"value": ..., "status": ..., "verified_at": ..., "source": ...}。
    只有 status == "verified" 且 value 不为 None 时才返回该值；
    否则返回 None（调用方视为"拒绝该 override"，保留原有数据）。
    """
    if not isinstance(entry, dict):
        return None
    if entry.get("status") != "verified":
        return None
    value = entry.get("value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
