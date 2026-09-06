"""Option chain data sources — extracted from options_module.py so a second
consumer (the Bull Put Spread scorer) can fetch chains without importing a
module that executes a full Streamlit page as a side effect of import.

Two data sources are meant to live here:
  - MarketData.app (implemented, already used by options_module.py's live
    "期权分析" page — this file is a lossless extraction of what used to be
    defined inline there, not a rewrite).
  - Firstrade (NOT implemented — see fetch_chain_firstrade below for why).
"""
from __future__ import annotations

import datetime
import os
import pathlib

import pandas as pd
import requests
import streamlit as st

# ── .env 加载 ─────────────────────────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parent.parent
_env  = _ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_MD_BASE = "https://api.marketdata.app/v1"
_MD_KEY  = os.environ.get("MARKETDATA_API_KEY", "")


def _md_get(path: str, params: dict | None = None, timeout: int = 20) -> dict | None:
    """向 MarketData.app 发起 GET 请求，正确处理 203/404/402。"""
    if not _MD_KEY:
        return {"s": "error", "errmsg": "MARKETDATA_API_KEY 未配置"}
    p = {"token": _MD_KEY}
    if params:
        p.update(params)
    try:
        r = requests.get(f"{_MD_BASE}{path}", params=p, timeout=timeout)
        # 402 = 超出计划配额
        if r.status_code == 402:
            return {"s": "no_data", "errmsg": "402 — 此接口超出当前 API 计划配额"}
        # 404 = API 用来表示"该查询无数据"（合法响应，返回 JSON）
        if r.status_code == 404:
            return r.json()
        # 203 = 正常成功响应（与 200 语义相同）
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        return {"s": "error", "errmsg": "请求超时（>20s）"}
    except requests.exceptions.RequestException as e:
        return {"s": "error", "errmsg": str(e)}


def _ts_to_date(ts) -> str:
    """将 Unix timestamp（int）转为 YYYY-MM-DD 字符串。"""
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


def _parse_chain(data: dict) -> pd.DataFrame:
    """将 MarketData.app 期权链响应（并行数组）解析为 DataFrame。"""
    n = len(data.get("strike", []))
    if n == 0:
        return pd.DataFrame()

    def arr(key, default=None):
        v = data.get(key, [default] * n)
        return v if len(v) == n else [default] * n

    df = pd.DataFrame({
        "symbol":  arr("optionSymbol"),
        "exp_ts":  arr("expiration"),          # Unix timestamp (int)
        "dte":     arr("dte"),
        "side":    arr("side"),
        "strike":  arr("strike"),
        "bid":     arr("bid"),
        "ask":     arr("ask"),
        "mid":     arr("mid"),
        "last":    arr("last"),
        "volume":  arr("volume"),
        "oi":      arr("openInterest"),
        "iv":      arr("iv"),
        "delta":   arr("delta"),
        "gamma":   arr("gamma"),
        "theta":   arr("theta"),
        "vega":    arr("vega"),
        "itm":     arr("inTheMoney"),
        "und_px":  arr("underlyingPrice"),
    })

    # 数值列转换
    for c in ["strike", "bid", "ask", "mid", "last", "iv",
              "delta", "gamma", "theta", "vega", "und_px"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["volume", "oi", "dte"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

    # Unix timestamp → 日期字符串
    df["exp"] = df["exp_ts"].apply(
        lambda v: _ts_to_date(v) if pd.notna(v) and v else "—")
    df["iv_pct"] = (df["iv"] * 100).round(2)

    return df.drop(columns=["exp_ts"])


@st.cache_data(ttl=600, show_spinner=False)
def fetch_expirations_marketdata(ticker: str) -> list[str]:
    """拉取可用到期日列表（字符串格式 YYYY-MM-DD）。"""
    data = _md_get(f"/options/expirations/{ticker.upper()}/")
    if not data or data.get("s") != "ok":
        return []
    return sorted(data.get("expirations", []))


@st.cache_data(ttl=60, show_spinner=False)
def fetch_chain_marketdata(ticker: str, expiration: str, strike_limit: int = 40) -> pd.DataFrame:
    """拉取指定到期日的完整期权链（Call + Put），来自 MarketData.app。"""
    data = _md_get(
        f"/options/chain/{ticker.upper()}/",
        {"expiration": expiration, "strikeLimit": strike_limit},
    )
    if not data or data.get("s") != "ok":
        return pd.DataFrame()
    return _parse_chain(data)


def fetch_expirations_firstrade(ticker: str) -> list[str]:
    """NOT IMPLEMENTED.

    Firstrade's option-chain page has never been scraped by this codebase --
    grep confirms account_monitor.py's Chrome-CDP automation only ever
    touched /app/balance, /app/history, /app/positions. There's no observed
    page structure, no known JSON endpoint, and no prior art here to extract
    from, unlike those three pages. Writing DOM-scraping code against markup
    nobody has actually looked at would just be a guess dressed up as a
    working function -- it would silently return nothing (or crash) against
    the real page.

    To implement this for real, whoever has a logged-in Firstrade session
    needs to either (a) share the option-chain page's URL + a sample of its
    HTML/JSON (view-source, or the Network tab response if it's fetched via
    XHR — XHR would be far more reliable to parse than an HTML table), or
    (b) pair with a session that has local Chrome-CDP access to Firstrade so
    the page can be inspected directly the way the balance/positions pages
    presumably were.
    """
    raise NotImplementedError(
        "Firstrade 期权链尚未实现 — 这个代码库里从未抓取过 Firstrade 的期权链页面，"
        "没有可参考的页面结构或接口。需要你提供该页面的 URL + HTML/JSON 样本"
        "（或在能连到你本机已登录 Firstrade 的 Chrome CDP 的会话里现场抓取），"
        "才能写出真正能用的解析代码，而不是猜一个大概率解析不出东西的版本。"
    )


def fetch_chain_firstrade(ticker: str, expiration: str) -> pd.DataFrame:
    """NOT IMPLEMENTED — see fetch_expirations_firstrade's docstring."""
    raise NotImplementedError(
        "Firstrade 期权链尚未实现，原因同 fetch_expirations_firstrade。"
    )
