"""Macro economic data — ACTUAL (realized) values only.

Two data sources:
  - Yahoo Finance (^TNX) for the 10Y Treasury yield -- a live tradable quote,
    always "current", no concept of actual-vs-expected the way a scheduled
    release has.
  - FRED (Federal Reserve Economic Data, api.stlouisfed.org) for CPI / Core
    CPI / PCE / Core PCE / unemployment / nonfarm payrolls -- official
    realized data only.

What this module does NOT do, and cannot do from a free source: consensus /
expected values for any of these releases (the "surprise" half of an
actual-vs-expected comparison). That data is commercially sold (Bloomberg,
Trading Economics, Econoday) — BLS/BEA/Fed/Yahoo only publish what actually
happened, never what economists forecast beforehand. Every function here
returns ACTUAL/REALIZED figures only; do not bolt a fabricated "expected"
number onto these results.

Needs a free FRED API key (https://fred.stlouisfed.org/docs/api/api_key.html)
in .env as FRED_API_KEY, or the FRED_API_KEY environment variable. Without
one, every FRED-backed function raises MacroDataError rather than silently
returning a placeholder -- there is no safe neutral value for "current CPI"
the way normalize_score has a neutral 50 for a missing scoring input.

⚠️ Written in a sandbox whose egress proxy blocks both api.stlouisfed.org
and Yahoo Finance (confirmed with live 403s on both) -- none of this has
been exercised against a real response, only against hand-built mock JSON
in tests/test_macro_data.py. Needs a real run from a machine with network
access before the numbers it returns can be trusted.
"""
from __future__ import annotations

import os
import pathlib

import requests

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_env = _ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
_FRED_KEY  = os.environ.get("FRED_API_KEY", "")


class MacroDataError(Exception):
    """A macro data fetch could not return a real, verified value. Callers
    should catch this and fall back / surface staleness -- never substitute
    a guessed number silently."""


def _fred_observations(series_id: str, *, limit: int = 15) -> list[dict]:
    """Most recent `limit` observations for a FRED series, oldest first.

    FRED's own `limit` truncates from the start of whatever sort_order was
    requested -- asking for ascending order with a limit gives you the
    OLDEST points, not the most recent. Request descending (most recent
    first), then reverse locally to the natural oldest-first order the rest
    of this module assumes.
    """
    if not _FRED_KEY:
        raise MacroDataError("FRED_API_KEY 未配置 (.env 或环境变量)")
    params = {
        "series_id":  series_id,
        "api_key":    _FRED_KEY,
        "file_type":  "json",
        "sort_order": "desc",
        "limit":      limit,
    }
    try:
        r = requests.get(_FRED_BASE, params=params, timeout=15)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise MacroDataError(f"FRED 请求失败 ({series_id}): {e}") from e
    data = r.json()
    obs = data.get("observations", [])
    if not obs:
        raise MacroDataError(f"FRED 返回空数据 ({series_id})")
    return list(reversed(obs))


def _valid_points(obs: list[dict]) -> list[tuple[str, float]]:
    """(date, value) pairs, dropping FRED's '.' missing-value markers."""
    return [(row["date"], float(row["value"])) for row in obs
            if row.get("value") not in (None, ".", "")]


def _latest_value_and_date(obs: list[dict]) -> tuple[float, str]:
    valid = _valid_points(obs)
    if not valid:
        raise MacroDataError("所有观测值都是缺失标记 '.'")
    date, value = valid[-1]
    return value, date


def _yoy_pct_change(obs: list[dict], periods: int = 12) -> tuple[float, str]:
    """YoY %% change of the latest valid observation vs. the one `periods`
    monthly steps earlier. Assumes the series has no gaps between valid
    points (true historically for CPIAUCSL/CPILFESL/PCEPI/PCEPILFE, all
    strictly monthly) -- a gappy series would silently compare the wrong
    two points, so this is not safe to reuse on an arbitrary series without
    checking that assumption first.
    """
    valid = _valid_points(obs)
    if len(valid) <= periods:
        raise MacroDataError(f"数据点不足 {periods + 1} 个，无法算同比（只有 {len(valid)} 个）")
    latest_date, latest_val = valid[-1]
    base_date, base_val = valid[-1 - periods]
    if base_val == 0:
        raise MacroDataError("基期值为0，无法计算同比%")
    return round((latest_val / base_val - 1) * 100, 2), latest_date


def fetch_cpi_yoy() -> dict:
    """CPI（全项，季调，CPIAUCSL）同比% —— 最常引用的"通胀率"口径。"""
    pct, date = _yoy_pct_change(_fred_observations("CPIAUCSL"))
    return {"metric": "CPI YoY", "value_pct": pct, "date": date, "source": "FRED:CPIAUCSL"}


def fetch_core_cpi_yoy() -> dict:
    """核心CPI（剔除食品与能源，CPILFESL）同比%。"""
    pct, date = _yoy_pct_change(_fred_observations("CPILFESL"))
    return {"metric": "Core CPI YoY", "value_pct": pct, "date": date, "source": "FRED:CPILFESL"}


def fetch_pce_yoy() -> dict:
    """PCE物价指数（PCEPI）同比% —— 美联储真正盯的通胀口径。"""
    pct, date = _yoy_pct_change(_fred_observations("PCEPI"))
    return {"metric": "PCE YoY", "value_pct": pct, "date": date, "source": "FRED:PCEPI"}


def fetch_core_pce_yoy() -> dict:
    """核心PCE（PCEPILFE）同比% —— FOMC 2%通胀目标锚定的口径。"""
    pct, date = _yoy_pct_change(_fred_observations("PCEPILFE"))
    return {"metric": "Core PCE YoY", "value_pct": pct, "date": date, "source": "FRED:PCEPILFE"}


def fetch_unemployment_rate() -> dict:
    """UNRATE 最新值（%），本身已是比率，不需要算同比。"""
    value, date = _latest_value_and_date(_fred_observations("UNRATE", limit=3))
    return {"metric": "Unemployment Rate", "value_pct": value, "date": date, "source": "FRED:UNRATE"}


def fetch_nonfarm_payrolls_change() -> dict:
    """PAYEMS 环比新增就业人数（千人）= 最新值 − 上月值（"新增非农"口径）。"""
    valid = _valid_points(_fred_observations("PAYEMS", limit=3))
    if len(valid) < 2:
        raise MacroDataError("PAYEMS 数据点不足，无法算环比新增")
    (_, prev_val), (latest_date, latest_val) = valid[-2], valid[-1]
    return {
        "metric": "Nonfarm Payrolls Change",
        "value_k": round(latest_val - prev_val, 1),
        "date": latest_date,
        "source": "FRED:PAYEMS",
    }


def fetch_treasury_10y_yield() -> dict:
    """10Y 美债收益率（%），来自 yfinance ^TNX（不经 FRED）。

    Yahoo 对 ^TNX 的报价是收益率×10（例如4.25%显示为42.5），这里换算回真实
    百分比。这是一个连续交易的报价，没有"发布日"或"预期值"的概念——跟
    CPI/非农这类有固定发布节奏、有一致预期的数据不是一回事。
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise MacroDataError("yfinance 未安装") from e
    try:
        hist = yf.Ticker("^TNX").history(period="5d")
    except Exception as e:
        raise MacroDataError(f"yfinance ^TNX 拉取失败: {e}") from e
    close = hist["Close"].dropna() if hist is not None and not hist.empty else None
    if close is None or close.empty:
        raise MacroDataError("^TNX 历史数据为空")
    value = round(float(close.iloc[-1]) / 10.0, 4)
    date = close.index[-1].strftime("%Y-%m-%d")
    return {"metric": "10Y Treasury Yield", "value_pct": value, "date": date, "source": "yfinance:^TNX"}


_SNAPSHOT_PATH = _ROOT / "scoring" / "macro_snapshot.json"


def save_treasury_snapshot(result: dict) -> None:
    """Persist a fetch_treasury_10y_yield() result so callers that can't
    (or shouldn't, for latency) hit yfinance on every page load -- e.g.
    app.py's single-stock detail page -- can still get a recent live rate
    instead of always falling back to the hardcoded constant.
    """
    import json
    _SNAPSHOT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def load_treasury_snapshot() -> dict | None:
    """The last snapshot written by save_treasury_snapshot(), or None if
    none exists yet or the file is unreadable/corrupt."""
    import json
    if not _SNAPSHOT_PATH.exists():
        return None
    try:
        return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def get_risk_free_rate_decimal() -> float | None:
    """Live 10Y yield as a decimal (e.g. 0.0425) from the persisted
    snapshot, or None if no snapshot exists / it's malformed. Callers
    should fall back to scoring_engine._RISK_FREE_RATE when this is None --
    it deliberately does not fall back to that constant itself, so callers
    stay in control of (and can log) when they're on stale/fallback data.
    """
    snap = load_treasury_snapshot()
    if not snap or snap.get("value_pct") is None:
        return None
    try:
        return round(float(snap["value_pct"]) / 100.0, 6)
    except (TypeError, ValueError):
        return None


_ALL_FETCHERS = {
    "cpi_yoy":                fetch_cpi_yoy,
    "core_cpi_yoy":            fetch_core_cpi_yoy,
    "pce_yoy":                 fetch_pce_yoy,
    "core_pce_yoy":            fetch_core_pce_yoy,
    "unemployment_rate":       fetch_unemployment_rate,
    "nonfarm_payrolls_change": fetch_nonfarm_payrolls_change,
    "treasury_10y_yield":      fetch_treasury_10y_yield,
}


def fetch_all_actuals() -> dict:
    """跑一遍全部指标；单个失败不影响其他，失败原因收进 '_errors'，
    不静默吞掉——调用方应该能看出"这项数据缺失"和"这项数据是0"的区别。
    """
    results: dict = {}
    errors: dict[str, str] = {}
    for key, fn in _ALL_FETCHERS.items():
        try:
            results[key] = fn()
        except MacroDataError as e:
            results[key] = None
            errors[key] = str(e)
    if errors:
        results["_errors"] = errors
    return results


if __name__ == "__main__":
    import json
    print(json.dumps(fetch_all_actuals(), ensure_ascii=False, indent=2))
