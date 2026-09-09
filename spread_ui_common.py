"""
spread_ui_common.py — bull_put_spread_module.py / bull_call_spread_module.py
共用的数据源选择 + 到期日拉取 UI。

2026-09-09 抽出来：这段"选MarketData还是Firstrade + 输入标的 + 拉到期日 +
拉不到时报错"逐字复制在那两个文件里，跟价差是put还是call没有任何关系，
纯粹是"怎么拿到一个标的的期权到期日列表"这个通用问题，抽成一个函数。
"""
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
