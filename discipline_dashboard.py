"""
ENERGREX — 门⑤ 纪律看板（占位页）

设计已经定稿（docs/DISCIPLINE_GATE_DESIGN.md，2026-09-08），代码还没写。
这个占位页只是让六重门导航里门⑤这一格点得进去，不是空白 404，同时把
设计文档链接摆出来，免得以后忘了去哪找。
"""
import pathlib
import streamlit as st

_ROOT = pathlib.Path(__file__).parent

st.set_page_config(page_title="ENERGREX · 纪律看板", page_icon="🛡️", layout="wide")

import _sidebar as _sb
_sb.render()

_G = "#00D4AA"; _A = "#FFB347"; _SURF = "#0F1923"; _BDR = "#1E2D3D"
_MUT = "#8B9BB4"; _TXT = "#E2E8F0"

st.markdown(
    f"<div style='background:{_SURF};border:1px solid {_BDR};border-radius:10px;"
    f"padding:11px 20px;display:flex;justify-content:space-between;align-items:center;'>"
    f"<span style='font-size:17px;font-weight:800;letter-spacing:1px;color:{_G}'>"
    f"🛡️ 纪律看板</span>"
    f"<span style='font-size:12px;color:{_MUT}'>门⑤ 纪律</span>"
    f"</div>",
    unsafe_allow_html=True,
)

st.markdown(
    f"<div style='background:{_SURF};border:1px dashed {_BDR};border-radius:8px;"
    f"padding:20px;margin-top:16px;'>"
    f"<div style='color:{_A};font-weight:700;font-size:14px;margin-bottom:8px;'>"
    f"设计已定，代码还没写</div>"
    f"<div style='color:{_MUT};font-size:13px;line-height:1.7;'>"
    f"要做的事：记录止盈/止损/到期/对冲纪律这四类信号出现后你有没有真的响应"
    f"（有对应交易记录才算 <code>acted</code>，信号自己消失不算），按维度算"
    f"响应率、平均响应天数、未处理最久的一条——不合成一个总分，分维度分开看。"
    f"<br><br>完整设计见 <code>docs/DISCIPLINE_GATE_DESIGN.md</code>。"
    f"</div></div>",
    unsafe_allow_html=True,
)
