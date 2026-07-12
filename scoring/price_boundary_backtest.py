"""
ENERGREX price-boundary validation and backtest scaffold.

This script audits whether the price-sensitivity / entry-boundary module has
enough point-in-time history to claim predictive evidence. If forward labels are
available, it also summarizes outcomes by boundary band. With the current
single-day score_snapshots.csv, the expected status is ENGINEERING_ONLY.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from dataclasses import dataclass

import numpy as np
import pandas as pd


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOTS = ROOT / "data" / "score_snapshots.csv"
DEFAULT_OUT = pathlib.Path(__file__).with_name("price_boundary_backtest_report.json")

PREDICTIVE_GATES = {
    "min_snapshots": 100,
    "min_tickers": 5,
    "min_calendar_days": 252,
    "min_oos_observations": 30,
}

BOUNDARY_BANDS = [
    ("avoid", 0, 45),
    ("watch", 45, 60),
    ("reasonable_entry", 60, 70),
    ("margin_of_safety", 70, 101),
]


@dataclass
class GateResult:
    name: str
    value: float | int | bool
    required: float | int | bool | str
    passed: bool


def _band_for_score(score: float) -> str:
    for label, lo, hi in BOUNDARY_BANDS:
        if lo <= score < hi:
            return label
    return "unknown"


def _find_forward_return_column(df: pd.DataFrame) -> str | None:
    candidates = [
        "forward_return_252d",
        "forward_return_126d",
        "forward_return_63d",
        "forward_return_21d",
        "fwd_return_252d",
        "fwd_return_126d",
        "fwd_return_63d",
        "fwd_return_21d",
    ]
    return next((c for c in candidates if c in df.columns), None)


def _safe_float_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def load_snapshots(path: pathlib.Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Snapshot file not found: {path}")
    df = pd.read_csv(path)
    required = {"date", "ticker", "final_score", "price"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Snapshot file missing required columns: {missing}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["final_score"] = _safe_float_series(df["final_score"])
    df["price"] = _safe_float_series(df["price"])
    df = df.dropna(subset=["date", "ticker", "final_score", "price"])
    df["boundary_band"] = df["final_score"].apply(_band_for_score)
    return df.sort_values(["date", "ticker"]).reset_index(drop=True)


def gate_audit(df: pd.DataFrame, fwd_col: str | None) -> tuple[str, list[GateResult]]:
    n_snapshots = len(df)
    n_tickers = int(df["ticker"].nunique())
    calendar_days = int((df["date"].max() - df["date"].min()).days) if n_snapshots else 0

    split_idx = int(n_snapshots * 0.7)
    oos = df.iloc[split_idx:].copy() if n_snapshots else df
    if fwd_col:
        oos_obs = int(_safe_float_series(oos[fwd_col]).notna().sum())
    else:
        oos_obs = 0

    gates = [
        GateResult("min_snapshots", n_snapshots, PREDICTIVE_GATES["min_snapshots"],
                   n_snapshots >= PREDICTIVE_GATES["min_snapshots"]),
        GateResult("min_tickers", n_tickers, PREDICTIVE_GATES["min_tickers"],
                   n_tickers >= PREDICTIVE_GATES["min_tickers"]),
        GateResult("min_calendar_days", calendar_days, PREDICTIVE_GATES["min_calendar_days"],
                   calendar_days >= PREDICTIVE_GATES["min_calendar_days"]),
        GateResult("forward_return_labels", bool(fwd_col), "present", bool(fwd_col)),
        GateResult("min_oos_observations", oos_obs, PREDICTIVE_GATES["min_oos_observations"],
                   oos_obs >= PREDICTIVE_GATES["min_oos_observations"]),
    ]

    status = "PREDICTIVE_VALIDATION_READY" if all(g.passed for g in gates) else "ENGINEERING_ONLY"
    return status, gates


def summarize_forward_returns(df: pd.DataFrame, fwd_col: str | None) -> dict:
    if not fwd_col:
        return {
            "available": False,
            "reason": "No forward-return label column found.",
        }

    work = df.copy()
    work[fwd_col] = _safe_float_series(work[fwd_col])
    work = work.dropna(subset=[fwd_col])
    if work.empty:
        return {
            "available": False,
            "reason": f"Forward-return column {fwd_col} has no usable observations.",
        }

    band_order = [b[0] for b in BOUNDARY_BANDS]
    by_band = {}
    for band in band_order:
        vals = work.loc[work["boundary_band"] == band, fwd_col].dropna()
        by_band[band] = {
            "sample_size": int(len(vals)),
            "avg_return": round(float(vals.mean()), 6) if len(vals) else None,
            "median_return": round(float(vals.median()), 6) if len(vals) else None,
            "hit_rate": round(float((vals > 0).mean()), 6) if len(vals) else None,
        }

    avg_seq = [by_band[b]["avg_return"] for b in band_order if by_band[b]["avg_return"] is not None]
    monotonic = all(avg_seq[i] <= avg_seq[i + 1] for i in range(len(avg_seq) - 1)) if len(avg_seq) >= 2 else None

    return {
        "available": True,
        "forward_return_column": fwd_col,
        "by_band": by_band,
        "monotonic_avg_return": monotonic,
        "note": "Overlapping forward-return windows reduce observation independence.",
    }


def run(snapshot_path: pathlib.Path, out_path: pathlib.Path) -> dict:
    df = load_snapshots(snapshot_path)
    fwd_col = _find_forward_return_column(df)
    status, gates = gate_audit(df, fwd_col)
    forward_summary = summarize_forward_returns(df, fwd_col)

    report = {
        "module": "price_boundary",
        "status": status,
        "effective_validation_rate": round(sum(g.passed for g in gates) / len(gates), 4),
        "predictive_gates": [
            {
                "name": g.name,
                "value": g.value,
                "required": g.required,
                "passed": g.passed,
            }
            for g in gates
        ],
        "snapshot_file": str(snapshot_path),
        "snapshot_count": int(len(df)),
        "ticker_count": int(df["ticker"].nunique()),
        "date_min": df["date"].min().date().isoformat() if len(df) else None,
        "date_max": df["date"].max().date().isoformat() if len(df) else None,
        "boundary_band_counts": df["boundary_band"].value_counts().to_dict(),
        "forward_return_summary": forward_summary,
        "standard": {
            "thresholds": {
                "observation_price": "Final Score >= 45",
                "reasonable_entry_price": "Final Score >= 60",
                "margin_of_safety_price": "Final Score >= 70",
            },
            "disclaimer": "Boundary labels are decision boundaries, not automatic trade instructions.",
        },
    }

    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots", type=pathlib.Path, default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    report = run(args.snapshots, args.out)
    print(json.dumps({
        "status": report["status"],
        "effective_validation_rate": report["effective_validation_rate"],
        "snapshot_count": report["snapshot_count"],
        "ticker_count": report["ticker_count"],
        "date_min": report["date_min"],
        "date_max": report["date_max"],
        "report": str(args.out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

