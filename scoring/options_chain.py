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



# ── Firstrade（CDP 附着已登录 Chrome，走真实内部 JSON 接口）──────────────
#
# 接口是 2026-09-06 现场用 Chrome DevTools Network 面板 + Claude in Chrome
# 的 javascript_tool 反复试出来的（不是猜的、Firstrade 也没有公开文档）：
#
#   GET  /app/api/option-chain?m=get_exp_dates&root_symbol={TICKER}
#        -> [{"expDate":"20260918","dayLeft":12,"expType":"M"|"W"}, ...]
#
#   GET  /app/api/option-chain?m=get_oc&root_symbol={TICKER}
#            &exp_date={YYYYMMDD}&chains_range=A
#        -> [{"strike":25,"class":"C"|"P","optSymbol":"PLTR260918C00025000",
#             "bid":...,"ask":...,"last":...,"vol":...,"openInt":...}, ...]
#        （没有 IV/greeks —— 那是下面单独一个接口）
#
#   POST /app/api/option-greeks   body {"type":"chain",
#            "root_symbol":{TICKER},"exp_date":{YYYYMMDD},"chains_range":"A"}
#        -> [{"symbol":"PLTR260918C00025000","iv":"3.406359",
#             "delta":"0.999695","gamma":...,"theta":...,"vega":...,
#             "rho":...}, ...]
#
# 两边用 optSymbol/symbol 关联。这两个接口都需要已登录的 session cookie，
# 只能走 CDP 附着已登录 Chrome 这条路（跟 account_monitor.py 抓余额/持仓
# 用的是同一个 9222 端口和附着方式），不能像 MarketData.app 那样裸
# requests + API key 调 —— 所以这里独立实现一份 CDP 附着逻辑，而不是导入
# account_monitor.py（那个文件顶层就会触发一次 Streamlit 页面渲染）。
#
# und_px（标的现价）没有复用 Firstrade 自己的报价接口 —— 那个接口 URL 里
# 要求带 account 号做参数，没必要让这两个函数依赖某个具体账户；直接用
# yfinance 取现价，跟这个代码库其它地方取现价的方式一致。
#
# 已用真实 PLTR 期权链验证过端到端可用（見 2026-09-06 的 commit）。

_CDP_ADDR        = "localhost:9222"
_FT_ORIGIN       = "https://invest.firstrade.com"
_FT_OPTIONS_PAGE = f"{_FT_ORIGIN}/app/trade/options"

_FETCH_EXP_DATES_JS = r"""
var ticker = arguments[0];
var cb = arguments[arguments.length - 1];
fetch('/app/api/option-chain?m=get_exp_dates&root_symbol=' + ticker, {credentials: 'include'})
  .then(function(r) { return r.json(); })
  .then(function(j) { cb({ok: true, data: j}); })
  .catch(function(e) { cb({ok: false, error: String(e)}); });
"""

_FETCH_CHAIN_JS = r"""
var ticker = arguments[0];
var expCompact = arguments[1];
var cb = arguments[arguments.length - 1];
Promise.all([
  fetch('/app/api/option-chain?m=get_oc&root_symbol=' + ticker + '&exp_date=' + expCompact + '&chains_range=A',
        {credentials: 'include'}).then(function(r) { return r.json(); }),
  fetch('/app/api/option-greeks', {
    method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({type: 'chain', root_symbol: ticker, exp_date: expCompact, chains_range: 'A'})
  }).then(function(r) { return r.json(); }),
]).then(function(results) {
  var oc = results[0], gk = results[1];
  var gkMap = {};
  for (var i = 0; i < gk.length; i++) gkMap[gk[i].symbol] = gk[i];
  var out = oc.map(function(row) {
    var g = gkMap[row.optSymbol] || {};
    return {
      symbol: row.optSymbol, side: row.class === 'C' ? 'call' : 'put',
      strike: row.strike, bid: row.bid, ask: row.ask, last: row.last,
      volume: row.vol, oi: row.openInt,
      iv: g.iv != null ? parseFloat(g.iv) : null,
      delta: g.delta != null ? parseFloat(g.delta) : null,
      gamma: g.gamma != null ? parseFloat(g.gamma) : null,
      theta: g.theta != null ? parseFloat(g.theta) : null,
      vega: g.vega != null ? parseFloat(g.vega) : null,
    };
  });
  cb({ok: true, data: out});
}).catch(function(e) { cb({ok: false, error: String(e)}); });
"""


def _cdp_reachable() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{_CDP_ADDR}/json", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _get_cdp_driver():
    """附着到 CDP 9222 的已登录 Chrome。跟 account_monitor.py._get_driver
    是同一种附着方式（debuggerAddress），独立实现是为了不 import
    account_monitor.py（它顶层会跑一次 st.set_page_config 之类的副作用）。"""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        return None
    try:
        opts = Options()
        opts.add_experimental_option("debuggerAddress", _CDP_ADDR)
        return webdriver.Chrome(options=opts)
    except Exception:
        return None


def _run_on_firstrade_tab(driver, script: str, *args, timeout: float = 20):
    """新开一个标签页、导航到期权页面（同源，session cookie 才生效）、跑一段
    异步 JS、切回第一个窗口。不主动关闭新标签——跟 account_monitor.py
    ._close_tab 同样的取舍：Chrome 的 beforeunload 保护 + CDP 关闭标签会
    弄挂 WebDriver session，攒几个空标签远比 session 崩掉代价小。"""
    driver.switch_to.new_window("tab")
    try:
        driver.get(_FT_OPTIONS_PAGE)
        driver.set_script_timeout(timeout)
        return driver.execute_async_script(script, *args)
    finally:
        try:
            driver.switch_to.window(driver.window_handles[0])
        except Exception:
            pass


def fetch_expirations_firstrade(ticker: str) -> list[str]:
    """真实接口：GET /app/api/option-chain?m=get_exp_dates。需要本机 CDP
    9222 能连到一个已登录 Firstrade 的 Chrome（跟 account_monitor.py 用
    --remote-debugging-port=9222 启动的是同一个）。连不上/未登录/接口出错
    都返回空列表——调用方（页面）本来就把空列表当"没拉到"处理，不是这个
    函数专属的行为。"""
    if not _cdp_reachable():
        return []
    driver = _get_cdp_driver()
    if driver is None:
        return []
    try:
        result = _run_on_firstrade_tab(driver, _FETCH_EXP_DATES_JS, ticker.upper())
    except Exception:
        return []
    if not result or not result.get("ok"):
        return []
    out = []
    for row in result.get("data") or []:
        d = str(row.get("expDate", ""))
        if len(d) == 8:
            out.append(f"{d[0:4]}-{d[4:6]}-{d[6:8]}")
    return sorted(out)


def _build_firstrade_chain_df(
    rows: list[dict], expiration: str, spot: float | None,
    today: datetime.date | None = None,
) -> pd.DataFrame:
    """Pure transform: merged Firstrade rows (as produced by _FETCH_CHAIN_JS)
    -> the same column shape _parse_chain() produces for MarketData.app.
    Split out from fetch_chain_firstrade() so this logic is unit-testable
    without a live CDP/Selenium session -- the network/CDP half has no
    branching worth testing in isolation, this half has all of it."""
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    for c in ["strike", "bid", "ask", "last", "iv", "delta", "gamma", "theta", "vega"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["volume", "oi"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

    # mid：正常情况下 bid/ask 均值；bid 或 ask 缺失/非正时退回 last（跟
    # MarketData.app 那条路径的降级逻辑保持一致的"总要有个能用的数"原则）。
    df["mid"] = df[["bid", "ask"]].mean(axis=1, skipna=True)
    _bad_bid_ask = (df["bid"].isna() | (df["bid"] <= 0) | df["ask"].isna() | (df["ask"] <= 0))
    df.loc[_bad_bid_ask, "mid"] = df["last"]

    df["und_px"] = spot
    if spot:
        df["itm"] = ((df["side"] == "call") & (df["strike"] < spot)) | \
                    ((df["side"] == "put") & (df["strike"] > spot))
    else:
        df["itm"] = None

    today = today or datetime.date.today()
    dte = max(0, (datetime.date.fromisoformat(expiration) - today).days)
    df["dte"]    = dte
    df["exp"]    = expiration
    df["iv_pct"] = (df["iv"] * 100).round(2)
    return df


def fetch_chain_firstrade(ticker: str, expiration: str) -> pd.DataFrame:
    """真实接口：GET .../option-chain?m=get_oc + POST .../option-greeks，
    按 optSymbol 关联。标的现价单独用 yfinance 取（不复用 Firstrade 自己
    带 account 号参数的报价接口，见模块顶部说明）。返回的列名跟
    fetch_chain_marketdata()/_parse_chain() 一致，是同一个下游消费者的
    drop-in 替代，不是另一套 schema。"""
    if not _cdp_reachable():
        return pd.DataFrame()
    driver = _get_cdp_driver()
    if driver is None:
        return pd.DataFrame()

    exp_compact = expiration.replace("-", "")
    try:
        result = _run_on_firstrade_tab(driver, _FETCH_CHAIN_JS, ticker.upper(), exp_compact)
    except Exception:
        return pd.DataFrame()
    if not result or not result.get("ok"):
        return pd.DataFrame()
    rows = result.get("data") or []

    try:
        import yfinance as yf
        spot = float(yf.Ticker(ticker.upper()).fast_info.last_price or 0) or None
    except Exception:
        spot = None
    return _build_firstrade_chain_df(rows, expiration, spot)
