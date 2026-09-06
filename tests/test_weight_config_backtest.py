import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
from weight_config_backtest import _simplex_grid, _tertile_stats, grid_search_optimal_weights


DIMS = ["valuation", "growth", "quality", "ai_exposure", "expectation_gap"]


def test_simplex_grid_combo_count_matches_stars_and_bars():
    combos = list(_simplex_grid(DIMS, step=0.2))
    assert len(combos) == math.comb(round(1 / 0.2) + 4, 4)


def test_simplex_grid_every_combo_sums_to_one():
    for combo in _simplex_grid(DIMS, step=0.2):
        assert combo.keys() == set(DIMS)
        assert sum(combo.values()) == pytest.approx(1.0)


def _synthetic_universe(n=15, seed=1):
    rng = np.random.default_rng(seed)
    tickers = [f"T{i}" for i in range(n)]
    df = pd.DataFrame({"ticker": tickers})
    for d in DIMS:
        df[d] = rng.uniform(0, 100, size=n)
    returns = {t: list(rng.normal(0, 0.05, size=3)) for t in tickers}
    return df, returns


def test_grid_search_spread_matches_tertile_stats_reference():
    # grid_search_optimal_weights vectorizes the same tertile-spread metric
    # _tertile_stats computes per-scheme -- every returned spread must be
    # bit-identical to recomputing that one combination the slow way.
    df, returns = _synthetic_universe()
    top = grid_search_optimal_weights(df, returns, step=0.25, top_n=200)
    assert len(top) > 0

    for weights, spread_fast in top:
        scored = df.copy()
        scored["w_score"] = sum(scored[d] * weights[d] for d in DIMS)
        spread_ref = _tertile_stats(scored, "w_score", returns)["top_minus_bottom_spread"]
        assert spread_fast == pytest.approx(spread_ref, abs=1e-9)


def test_grid_search_recovers_the_dimension_correlated_with_returns():
    # Returns constructed to depend only on 'growth' -- the optimizer should
    # concentrate weight there, not spread it evenly or favor another dim.
    rng = np.random.default_rng(0)
    tickers = [f"T{i}" for i in range(20)]
    df = pd.DataFrame({"ticker": tickers})
    for d in DIMS:
        df[d] = rng.uniform(0, 100, size=len(tickers))
    returns = {
        t: list(rng.normal(loc=(df.loc[i, "growth"] - 50) / 1000, scale=0.001, size=4))
        for i, t in enumerate(tickers)
    }

    top = grid_search_optimal_weights(df, returns, step=0.1, top_n=1)
    best_weights = top[0][0]
    assert best_weights["growth"] >= 0.5


def test_grid_search_returns_empty_below_minimum_universe_size():
    df, returns = _synthetic_universe(n=3)
    assert grid_search_optimal_weights(df, returns, step=0.25) == []
