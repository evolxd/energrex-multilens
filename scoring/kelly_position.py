"""
半凯利建议仓位查询 — kelly_position.py
=====================================
只做一件事：给一个 final_score，查 kelly_bands.json（由 kelly_backtest.py 生成）
返回半凯利建议仓位（0-1，已做跨档单调性修正、已封顶）。

不属于任何一个评分引擎（scoring_engine.py / quant_engine.py），
两边、app.py 都可以直接 import 这个模块使用，保持口径统一。
"""
import json, pathlib, functools

_BANDS_PATH = pathlib.Path(__file__).parent / "kelly_bands.json"

# 与 app.py `_score_to_rating` / kelly_backtest.py RATING_BANDS 完全一致
_RATING_BANDS = [
    ("⭐ Strong Buy", 65, 101),
    ("✅ Buy",         55, 65),
    ("👀 Watch",       45, 55),
    ("⚠️ Expensive",   35, 45),
    ("🚫 Avoid",        0, 35),
]


def _rating_for_score(score: float) -> str:
    for label, lo, hi in _RATING_BANDS:
        if lo <= score < hi:
            return label
    return "🚫 Avoid"


@functools.lru_cache(maxsize=1)
def _load_bands() -> dict | None:
    if not _BANDS_PATH.exists():
        return None
    try:
        return json.loads(_BANDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def kelly_meta() -> dict:
    """返回回测生成时间、方法论说明，供 UI 展示脚注用。没有回测数据时返回空字典。"""
    data = _load_bands()
    if not data:
        return {}
    return {
        "generated_at": data.get("generated_at"),
        "method":       data.get("method"),
        "caveat":       data.get("methodology_caveat"),
    }


def suggested_position_pct(final_score) -> float | None:
    """给 final_score，返回半凯利建议仓位（0-1，已单调性修正+封顶）。
    没有回测数据、分数为 None、或该档样本不足时返回 None（不瞎猜）。
    """
    if final_score is None:
        return None
    data = _load_bands()
    if not data:
        return None
    label = _rating_for_score(float(final_score))
    band  = data.get("bands", {}).get(label)
    if not band or band.get("insufficient_sample"):
        return None
    return band.get("half_kelly_monotonic")


def band_detail(final_score) -> dict | None:
    """返回该分数所在档的完整统计（胜率/赔率/样本数/股票列表），用于详情页展开说明。"""
    if final_score is None:
        return None
    data = _load_bands()
    if not data:
        return None
    label = _rating_for_score(float(final_score))
    band  = dict(data.get("bands", {}).get(label, {}))
    band["rating_label"] = label
    return band
