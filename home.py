"""
ENERGREX — 应用入口（六重门导航壳）

2026-09-08 六重门重建：这个文件以前既是导航又是作战室仪表盘，跟
Streamlit 自动生成的原生导航（根据 pages/ 文件夹）同时存在，侧边栏里
出现两套导航（用户截图指出的问题）。现在这个文件只做一件事——用
st.navigation() 按六重门分组声明页面并接管导航，不再渲染任何仪表盘
内容；原来的作战室仪表盘搬进了 war_room.py（跟其它每个页面一样，自己
管自己的 st.set_page_config()——一个 Streamlit 进程里只能有一次
set_page_config，不能在这个入口文件里也调用一次）。

st.navigation() 会自动接管侧边栏导航（不用再手写 page_link 列表），
所以以前 pages/ 目录下 1/2/3/6/8 那几个"只是 compile+exec 真实模块"的
薄包装文件已经删掉——直接把 st.Page() 指向真实模块文件即可。
"""
import os
import pathlib

import streamlit as st

_ROOT = pathlib.Path(__file__).parent

# ── .env 加载（每个页面各自也会加载一次；这里加载一次是为了保证从
#    home.py 入口第一次进任何页面时，环境变量已经就位）──────────────
_env = _ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# 品牌头——画在 st.navigation() 的导航列表上方（在 pg.run() 之前写进
# st.sidebar 的内容会排在导航列表前面）。
with st.sidebar:
    st.markdown(
        "<div style='font-size:18px;font-weight:800;letter-spacing:1.5px;"
        "color:#00D4AA;padding:2px 0 6px'>⚡ ENERGREX</div>",
        unsafe_allow_html=True,
    )
    st.divider()

pg = st.navigation({
    "作战室": [
        st.Page("war_room.py", title="作战室", icon="🏠", default=True),
    ],
    "① 估值发现": [
        st.Page("app.py", title="AI 估值评分", icon="📊"),
        st.Page("pages/4_🔎_误价研究.py", title="误价与特殊机会", icon="🔎"),
    ],
    "② 工具选择": [
        st.Page("options_module.py", title="期权分析", icon="📈"),
        st.Page("bull_put_spread_module.py", title="期权价差评分（Put）", icon="🎯"),
        st.Page("bull_call_spread_module.py", title="Bull Call Spread 评分", icon="📐"),
        st.Page("pages/7_🌐_期权价差全市场筛选.py", title="期权价差全市场筛选", icon="🌐"),
    ],
    "③ 仓位测算": [
        st.Page("pages/5_⚖️_仓位管理.py", title="仓位管理", icon="⚖️"),
    ],
    "④ 下场前验证": [
        st.Page("pre_trade_check.py", title="下单前检查", icon="✅"),
    ],
    "⑤ 纪律": [
        st.Page("discipline_dashboard.py", title="纪律看板", icon="🛡️"),
    ],
    "⑥ 绩效评估": [
        st.Page("account_monitor.py", title="账户监控", icon="🏦"),
    ],
})
pg.run()
