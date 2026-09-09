"""
ENERGREX — 期权价差工具（门②工具选择，统一入口）

2026-09-09 六重门重建：把原来三个独立页面（期权价差评分Put/Bull Call Spread
评分/期权价差全市场筛选）合并成一个入口，用"范围"+"策略类型"两级切换代替
三条平铺的导航项。三个页面本身的逻辑一行没改——分别搬进了
bull_put_spread_module.py::render()、bull_call_spread_module.py::render()、
pages/7_🌐_期权价差全市场筛选.py::render()，这个文件只是照哪个切换选哪个
函数调，不重新实现任何评分/筛选逻辑。

"全市场扫描"模式下不需要再选一次策略类型——那个页面自己内部用 Put/Call 两个
tab 同时展示，跟"单票"模式下"一次只能算一个方向"的操作方式不一样，两种切换
不该被硬拗成同一套。
"""
import pathlib

import streamlit as st

_ROOT = pathlib.Path(__file__).parent

st.set_page_config(page_title="ENERGREX · 期权价差工具", page_icon="🎯", layout="wide")

import _sidebar as _sb
_sb.render()

_G = "#00D4AA"; _SURF = "#0F1923"; _BDR = "#1E2D3D"; _MUT = "#8B9BB4"; _TXT = "#E2E8F0"

st.markdown(
    f"<div style='background:{_SURF};border:1px solid {_BDR};border-radius:10px;"
    f"padding:11px 20px;display:flex;justify-content:space-between;align-items:center;'>"
    f"<span style='font-size:17px;font-weight:800;letter-spacing:1px;color:{_G}'>"
    f"🎯 期权价差工具</span>"
    f"<span style='font-size:12px;color:{_MUT}'>门② 工具选择</span>"
    f"</div>",
    unsafe_allow_html=True,
)

_scope = st.radio(
    "范围", options=["单票", "全市场扫描"], horizontal=True,
    help="单票：输入一个标的代码，算它自己所有候选价差的排名。"
         "全市场扫描：跨一批股票，每只票留最好的一个，排出全池前10。",
)

if _scope == "单票":
    _strategy = st.radio(
        "策略类型", options=["Put Credit Spread（看涨/收权利金）", "Call Debit Spread（看涨/付权利金）"],
        horizontal=True,
    )
    st.divider()
    if _strategy.startswith("Put Credit"):
        import bull_put_spread_module
        bull_put_spread_module.render()
    else:
        import bull_call_spread_module
        bull_call_spread_module.render()
else:
    st.divider()
    import sys
    sys.path.insert(0, str(_ROOT / "pages"))
    import importlib
    _screener = importlib.import_module("7_🌐_期权价差全市场筛选")
    _screener.render()
