"""
每日 Score+价格快照记录 — kelly_snapshot_logger.py
=================================================
每次 refresh_scores.py 刷新完 results_validated.csv 后调用一次，
把 (日期, ticker, final_score, rating, price, 六维度分, 风险扣分, 熔断标记)
追加进 data/score_snapshots.csv。

目的：kelly_backtest.py 现在只能用"当前分数 + 该股票历史价格"做近似回测（有生存者偏差）。
攒够几个月的真实快照后，就能做"某天的分数 -> N个月后的真实收益"这种真正有因果意义的回测，
到时候把 kelly_backtest.py 换成读这份快照，不再需要 kelly_bands.json 里的 methodology_caveat。

2026-09-10 补：审计发现之前只存了 final_score，没法做时点正确的维度级验证
（哪个维度真的有预测力、风险扣分是不是在拖累而不是保护）——只能等新快照
攒出来，没法回填过去的。六个维度列 + 风险扣分 + 熔断标记，是
results_validated.csv 里已经算好的，直接搬过来存，不重新计算。

去重规则：同一天同一 ticker 只保留最后一次快照（同日多次刷新不会重复堆积）。
"""
import pathlib
import pandas as pd
import datetime

try:
    from .decision_policy import score_band
except ImportError:
    from decision_policy import score_band

_SNAPSHOT_PATH = pathlib.Path(__file__).parent.parent / "data" / "score_snapshots.csv"

# results_validated.csv 列名 -> 快照列名。六个维度分 + 风险扣分 + 熔断标记，
# 都是 refresh_scores.py 已经算好写进 CSV 的，这里只是原样搬一份存历史。
DIMENSION_SNAPSHOT_COLUMNS = {
    "val_估值得分(PEG/EV/ERG/PE/FCFYld)":     "valuation",
    "grw_成长得分(营收/EPS/FCF/指引增速)":      "growth",
    "qlt_质量得分(毛利率/FCF率/ROIC/负债)":     "quality",
    "ai_AI暴露得分(AI营收/平台/订单占比)":      "ai_exposure",
    "exp_预期差得分(超预期营收EPS指引)":        "expectation_gap",
    "mom_动量得分(RSI14/价格vs200日均)":       "momentum",
    "risk_风险扣分(max20,Beta/回撤/负债)":     "risk_penalty",
    "circuit_熔断(Beta>2.2且回撤>35%或DE>1.8)": "circuit_triggered",
}


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
    present_dim_cols = {c: name for c, name in DIMENSION_SNAPSHOT_COLUMNS.items()
                        if c in df.columns}
    rows = []
    for _, r in df.iterrows():
        ticker = r.get(ticker_col)
        score  = r.get(score_col)
        price  = _parse_raw_cell(r.get(price_col)) if price_col in df.columns else None
        if ticker is None or pd.isna(score):
            continue
        row = {
            "date":   today,
            "ticker": ticker,
            "final_score": float(score),
            "rating": score_band(float(score)),
            "price":  price,
        }
        for csv_col, snap_col in present_dim_cols.items():
            if snap_col == "circuit_triggered":
                # 这一列在 CSV 里是 "YES" / 空，不是 "123 [yf]" 这种带来源
                # 的数字单元格——空值在这里表示"没触发"，不是"不知道"（这
                # 一列只要存在，每行都真的算过熔断判定），所以空值映射成
                # False 而不是 None/NaN。
                raw = r.get(csv_col)
                v = (not pd.isna(raw)) and str(raw).strip().upper() == "YES"
            else:
                v = _parse_raw_cell(r.get(csv_col))
            row[snap_col] = v
        rows.append(row)
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
