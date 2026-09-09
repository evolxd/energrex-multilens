"""
ENERGREX — 门④ 下场前验证（占位页）

还没建。这一步该做的是"我要开新仓，有没有过硬约束"的一次性检查——硬约束
封顶（scoring/position_limits.py 已经在管着单票/产业链/现金底线/流动性）
跟仓位管理页面里的 Kelly 建议对比，下单前最后过一遍。归属层和具体设计还
没定，先给个占位页，不让六重门导航点进来是 404。
"""
import pathlib
import streamlit as st

_ROOT = pathlib.Path(__file__).parent

st.set_page_config(page_title="ENERGREX · 下单前检查", page_icon="✅", layout="wide")

import _sidebar as _sb
_sb.render()

_G = "#00D4AA"; _A = "#FFB347"; _SURF = "#0F1923"; _BDR = "#1E2D3D"
_MUT = "#8B9BB4"; _TXT = "#E2E8F0"

st.markdown(
    f"<div style='background:{_SURF};border:1px solid {_BDR};border-radius:10px;"
    f"padding:11px 20px;display:flex;justify-content:space-between;align-items:center;'>"
    f"<span style='font-size:17px;font-weight:800;letter-spacing:1px;color:{_G}'>"
    f"✅ 下单前检查</span>"
    f"<span style='font-size:12px;color:{_MUT}'>门④ 下场前验证</span>"
    f"</div>",
    unsafe_allow_html=True,
)

st.markdown(
    f"<div style='background:{_SURF};border:1px dashed {_BDR};border-radius:8px;"
    f"padding:20px;margin-top:16px;'>"
    f"<div style='color:{_A};font-weight:700;font-size:14px;margin-bottom:8px;'>"
    f"这一步还没建</div>"
    f"<div style='color:{_MUT};font-size:13px;line-height:1.7;'>"
    f"设想是：新开一笔仓位之前，把硬约束（<code>scoring/position_limits.py</code>"
    f"——单票/产业链集中度/现金底线/流动性天数）和仓位管理页面算出来的 Kelly "
    f"建议摆在一起过一遍，跟门⑤纪律不是一回事——门④往前看（要不要开这一笔），"
    f"门⑤往回看（过去开的仓有没有照系统的话处理）。<br><br>"
    f"具体要做成什么样，还没跟用户定下来，目前这一格只是六重门导航里的占位，"
    f"避免点进来是空白 404。"
    f"</div></div>",
    unsafe_allow_html=True,
)
