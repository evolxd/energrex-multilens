"""Step 1 of the two-engine unification plan (P2, ARCHITECTURE_REVIEW.md).

Runs scoring_engine.score_stock() and quant_engine.score_ticker() on the same
merged input data (quant_audit.merge_data(), use_live=False so this works
offline and is reproducible) for every ticker in the universe, and reports
how far their final scores diverge.

This script only measures. It does not change which engine app.py calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scoring"))

import pandas as pd  # noqa: E402

from quant_audit import merge_data  # noqa: E402
from scoring_engine import score_stock, TICKER_CATEGORY  # noqa: E402
from quant_engine import score_ticker  # noqa: E402


def audit(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for ticker in tickers:
        data = merge_data(ticker, use_live=False)

        try:
            se_result = score_stock(ticker, data)
            se_score = se_result["final_score"]
            se_rating = se_result["rating"]
        except Exception as exc:  # noqa: BLE001
            se_score, se_rating = None, f"ERROR: {exc}"

        try:
            qe_result = score_ticker(ticker, data)
            qe_score = qe_result.final_score
            qe_rating = qe_result.rating
        except Exception as exc:  # noqa: BLE001
            qe_score, qe_rating = None, f"ERROR: {exc}"

        diff = (
            round(se_score - qe_score, 2)
            if isinstance(se_score, (int, float)) and isinstance(qe_score, (int, float))
            else None
        )
        rows.append({
            "ticker": ticker,
            "scoring_engine_score": se_score,
            "scoring_engine_rating": se_rating,
            "quant_engine_score": qe_score,
            "quant_engine_rating": qe_rating,
            "diff_se_minus_qe": diff,
            "abs_diff": abs(diff) if diff is not None else None,
        })
    df = pd.DataFrame(rows)
    return df.sort_values("abs_diff", ascending=False, na_position="first")


def main() -> None:
    tickers = sorted(TICKER_CATEGORY.keys())
    df = audit(tickers)
    out_path = ROOT / "data" / "engine_divergence_audit.csv"
    df.to_csv(out_path, index=False)

    print(f"Audited {len(df)} tickers -> {out_path}")
    print()
    valid = df[df["abs_diff"].notna()]
    print(f"Valid comparisons: {len(valid)} / {len(df)}")
    if len(valid):
        print(f"Mean |diff|:   {valid['abs_diff'].mean():.2f}")
        print(f"Median |diff|: {valid['abs_diff'].median():.2f}")
        print(f"Max |diff|:    {valid['abs_diff'].max():.2f}")
        print(f">10pt diff:    {(valid['abs_diff'] > 10).sum()} tickers")
        print(f">20pt diff:    {(valid['abs_diff'] > 20).sum()} tickers")
    errors = df[df["scoring_engine_rating"].astype(str).str.startswith("ERROR")
                | df["quant_engine_rating"].astype(str).str.startswith("ERROR")]
    if len(errors):
        print(f"\nErrored tickers: {len(errors)}")
        print(errors[["ticker", "scoring_engine_rating", "quant_engine_rating"]].to_string(index=False))
    print()
    print("Top 20 by |diff|:")
    print(valid.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
