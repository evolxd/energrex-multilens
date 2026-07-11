"""
每日 Score+价格快照记录 — kelly_snapshot_logger.py
=================================================
每次 refresh_scores.py 刷新完 results_validated.csv 后调用一次，
把 (日期, ticker, final_score, rating, price) 追加进 data/score_snapshots.csv。

目的：kelly_backtest.py 现在只能用"当前分数 + 该股票历史价格"做近似回测（有生存者偏差）。
攒够几个月的真实快照后，就能做"某天的分数 -> N个月后的真实收益"这种真正有因果意义的回测，
到时候把 kelly_backtest.py 换成读这份快照，不再需要 kelly_bands.json 里的 methodology_caveat。

去重规则：同一天同一 ticker 只保留最后一次快照（同日多次刷新不会重复堆积）。
"""
import pathlib
import pandas as pd
import datetime

_SNAPSHOT_PATH = pathlib.Path(__file__).parent.parent / "data" / "score_snapshots.csv"

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


def _parse_raw_cell(cell) -> float | None:
    """兼容 results_validated.csv 里 "211 [yf]" 这类带来源标注的单元格（同 app.py 的解析约定）。"""
    if cell is None or pd.isna(cell) or str(cell).strip().startswith("n/a"):
        return None
    try:
        return float(str(cell).split()[0].replace("%", ""))
    except Exception:
        return None


def log_snapshot(df: pd.DataFrame,
                  ticker_col: str = "ticker",
                  score_col: str = "final_综合得分(0-100)",
                  price_col: str = "raw_current_price_yf") -> int:
    """从已刷新的 results_validated.csv DataFrame 里取快照，追加写入 score_snapshots.csv。
    返回本次写入的行数。任何字段缺失都跳过该行，不抛异常（不能因为快照失败影响主刷新流程）。
    """
    if ticker_col not in df.columns or score_col not in df.columns:
        return 0

    today = datetime.date.today().isoformat()
    rows = []
    for _, r in df.iterrows():
        ticker = r.get(ticker_col)
        score  = r.get(score_col)
        price  = _parse_raw_cell(r.get(price_col)) if price_col in df.columns else None
        if ticker is None or pd.isna(score):
            continue
        rows.append({
            "date":   today,
            "ticker": ticker,
            "final_score": float(score),
            "rating": _rating_for_score(float(score)),
            "price":  price,
        })
    if not rows:
        return 0

    new_df = pd.DataFrame(rows)

    _SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
    if _SNAPSHOT_PATH.exists():
        old_df = pd.read_csv(_SNAPSHOT_PATH)
        combined = pd.concat([old_df, new_df], ignore_index=True)
        # 同一天同一ticker只留最后一条（本次刷新的）
        combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
    else:
        combined = new_df

    combined = combined.sort_values(["date", "ticker"]).reset_index(drop=True)
    combined.to_csv(_SNAPSHOT_PATH, index=False, encoding="utf-8-sig")
    return len(new_df)
