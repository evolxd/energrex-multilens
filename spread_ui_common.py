"""
spread_ui_common.py — bull_put_spread_module.py / bull_call_spread_module.py
共用的数据源选择 + 到期日拉取 UI，以及两个页面各自 render() 里逐字重复的
DTE 计算 + 发布日风险标签。

2026-09-09 抽出来：这段"选MarketData还是Firstrade + 输入标的 + 拉到期日 +
拉不到时报错"逐字复制在那两个文件里，跟价差是put还是call没有任何关系，
纯粹是"怎么拿到一个标的的期权到期日列表"这个通用问题，抽成一个函数。

2026-09-24 追加 dte()/release_risk_label()：这两个函数之前各自在 Put/Call
两个页面里独立重复了一份（先是闭包，后来提升成模块级私有函数），逐字节
相同（release_risk_label 只有 docstring 措辞不同）。跟 pick_source_and_
expirations 一样去掉下划线、放在这里共享，不再维护两份。
"""
import datetime
import pathlib
import sys

import streamlit as st

_ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(_ROOT / "scoring"))

from options_chain import (               # noqa: E402
    fetch_chain_firstrade,
    fetch_chain_marketdata,
    fetch_expirations_firstrade,
    fetch_expirations_marketdata,
)
import macro_calendar                     # noqa: E402


def dte(exp_str: str, today: datetime.date) -> int:
    return (datetime.date.fromisoformat(exp_str) - today).days


def release_risk_label(expiration: str, today: datetime.date) -> str:
    """已知宏观发布日中，落在[今天, expiration]窗口内的那些 -- 不代表"发布
    结果好坏"（预期值/一致预期本项目没有免费可靠来源，见
    scoring/macro_calendar.py 顶部说明），只代表"这段窗口里有一次已知会放大
    已实现波动率的日程事件"。Bull Put / Bull Call 两个价差评分页共用。"""
    return macro_calendar.release_risk_label(today, expiration)


def pick_source_and_expirations(ticker_key: str):
    """渲染数据源选择 + 标的代码输入，拉可用到期日列表。

    标的为空或拉不到到期日时直接 st.stop()（跟原来两个文件的行为一致，
    调用方后面的代码不需要再检查这两种情况）。

    ticker_key: st.text_input 的 session_state key，两个页面各自传自己的
        （"bps_ticker"/"bcs_ticker"），避免共享同一个输入框状态。

    返回 (ticker, fetch_chain, expirations)。
    """
    col_src, col_ticker = st.columns([1, 2])
    with col_src:
        source = st.selectbox("期权链数据源", ["MarketData.app（已接入）", "Firstrade（已接入 · 需本机 CDP 已登录）"])
    with col_ticker:
        ticker = st.text_input("标的代码", placeholder="NVDA", key=ticker_key).strip().upper()

    if source.startswith("Firstrade"):
        fetch_expirations, fetch_chain = fetch_expirations_firstrade, fetch_chain_firstrade
        st.caption(
            "走 Firstrade 真实内部接口（2026-09-06 现场抓包确认），需要本机有一个 "
            "`start_chrome.bat` 启动、CDP 9222、已登录 Firstrade 的 Chrome 在跑——"
            "跟 `account_monitor.py` 抓真实持仓用的是同一个自动化 profile。没连上/未登录时，"
            "下面拉取到期日会直接显示空列表（不是报错崩溃），按提示先启动那个 Chrome 并登录即可。"
        )
    else:
        fetch_expirations, fetch_chain = fetch_expirations_marketdata, fetch_chain_marketdata

    if not ticker:
        st.info("输入一个标的代码开始。")
        st.stop()

    with st.spinner(f"拉取 {ticker} 可用到期日…"):
        expirations = fetch_expirations(ticker)

    if not expirations:
        if source.startswith("Firstrade"):
            st.error(f"没拉到 {ticker} 的期权到期日列表——确认代码正确、该标的有期权，或者 CDP 9222 "
                      "那个 Chrome（`start_chrome.bat`）没启动/没登录 Firstrade。")
        else:
            st.error(f"没拉到 {ticker} 的期权到期日列表——确认代码正确、该标的有期权，或 MarketData.app "
                      "API key 配置正常（.env 里的 MARKETDATA_API_KEY）。")
        st.stop()

    return ticker, fetch_chain, expirations
