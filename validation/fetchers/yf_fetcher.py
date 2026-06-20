"""
yfinance historical data fetcher (validation layer)
====================================================
Recalculates momentum metrics from scratch using 1-year daily close prices.
Results are cached as pickle to avoid repeated downloads.
"""

from __future__ import annotations
import logging
import pickle
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent / "cache" / "raw"


def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"yf_hist_{ticker}.pkl"


def _is_fresh(path: Path, ttl_hours: int = 24) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=ttl_hours)


def get_history(ticker: str, period: str = "1y", ttl_hours: int = 24) -> pd.DataFrame | None:
    cache = _cache_path(ticker)
    if _is_fresh(cache, ttl_hours):
        try:
            return pickle.loads(cache.read_bytes())
        except Exception:
            pass
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=period, auto_adjust=True)
        if hist.empty:
            return None
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(hist))
        return hist
    except Exception as e:
        logger.warning("yfinance history %s: %s", ticker, e)
        return None


def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
    delta = prices.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    ag    = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    al    = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs    = ag / al.replace(0, 1e-10)
    return float((100.0 - 100.0 / (1.0 + rs)).iloc[-1])


def _calc_max_drawdown(prices: pd.Series) -> float:
    roll_max = prices.cummax()
    dd = (prices - roll_max) / roll_max
    return float(dd.min())


def calc_momentum(ticker: str, ttl_hours: int = 24) -> dict:
    """
    Returns dict with:
      price_vs_200dma  (decimal, e.g. 0.08 = 8% above)
      rsi_14
      max_drawdown_1y  (negative decimal, e.g. -0.22)
      momentum_score   (0-100, same formula as quant_engine)
    Returns empty dict on failure.
    """
    hist = get_history(ticker, ttl_hours=ttl_hours)
    if hist is None or hist.empty or "Close" not in hist.columns:
        return {}

    closes = hist["Close"].dropna()
    if len(closes) < 30:
        return {}

    result: dict = {}
    cur = float(closes.iloc[-1])

    # 200DMA
    n = min(200, len(closes))
    ma200 = float(closes.tail(n).mean())
    dev = (cur - ma200) / ma200
    if -1.0 < dev < 5.0:
        result["price_vs_200dma"] = round(dev, 4)

    # RSI-14
    if len(closes) >= 15:
        try:
            result["rsi_14"] = round(_calc_rsi(closes, 14), 2)
        except Exception:
            pass

    # 1Y max drawdown
    result["max_drawdown_1y"] = round(_calc_max_drawdown(closes), 4)

    # Replicate momentum score from quant_engine
    score = _momentum_score(
        result.get("price_vs_200dma"),
        result.get("rsi_14"),
    )
    if score is not None:
        result["momentum_score"] = round(score, 2)

    return result


def _momentum_score(vs200: float | None, rsi: float | None) -> float | None:
    """Mirrors quant_engine score_momentum() logic."""
    entries = []

    if vs200 is not None:
        # normalize_score: best=0.20, worst=-0.30, dir=positive
        best, worst = 0.20, -0.30
        s = max(0.0, min(100.0, (vs200 - worst) / (best - worst) * 100))
        entries.append((s, 0.50))

    if rsi is not None:
        # piecewise RSI scoring (from quant_engine)
        if 45 <= rsi <= 65:
            s = 80 + (rsi - 45) / 20.0 * 20   # 80-100
        elif 65 < rsi <= 80:
            s = 80 - (rsi - 65) / 15.0 * 50   # 80→30
        elif rsi > 80:
            s = 5
        elif 30 <= rsi < 45:
            s = 40 + (rsi - 30) / 15.0 * 40   # 40-80
        else:  # <30 extreme oversold
            s = 60 + (30 - rsi) / 30.0 * 20   # 60-80
        entries.append((s, 0.50))

    if not entries:
        return None
    total_w = sum(w for _, w in entries)
    return sum(s * w for s, w in entries) / total_w
