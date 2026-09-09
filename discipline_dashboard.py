"""
ENERGREX — 门⑤ 纪律看板

设计见 docs/DISCIPLINE_GATE_DESIGN.md。记录/核对的算法在
account/discipline.py，接到 _cascade.py 的账户同步流程里——每次点"同步
账户"，这个页面看到的数据才会更新，不是这里现拉。
"""
import datetime
import pathlib

import pandas as pd
import streamlit as st

_ROOT = pathlib.Path(__file__).parent

st.set_page_config(page_title="ENERGREX · 纪律看板", page_icon="🛡️", layout="wide")

import _sidebar as _sb
_sb.render()

from account import discipline as _disc

_G = "#00D4AA"; _R = "#FF4B6E"; _A = "#FFB347"; _SURF = "#0F1923"; _BDR = "#1E2D3D"
_MUT = "#8B9BB4"; _TXT = "#E2E8F0"

_ACCT = "account_1"

st.markdown(
    f"<div style='background:{_SURF};border:1px solid {_BDR};border-radius:10px;"
    f"padding:11px 20px;display:flex;justify-content:space-between;align-items:center;'>"
    f"<span style='font-size:17px;font-weight:800;letter-spacing:1px;color:{_G}'>"
    f"🛡️ 纪律看板</span>"
    f"<span style='font-size:12px;color:{_MUT}'>门⑤ 纪律</span>"
    f"</div>",
    unsafe_allow_html=True,
)

# ── 统计区间选择——用户明确要求的"任何统计数字都要带起止时间和总样本量"，
#    区间本身也让用户选，不是我替他定死一个 ──────────────────────────
_range_days = st.radio(
    "统计区间", options=[30, 90, 365], index=0,
    format_func=lambda d: f"近{d}天", horizontal=True, label_visibility="collapsed",
)
_until = datetime.date.today()
_since = _until - datetime.timedelta(days=_range_days)

_scores = _disc.compute_discipline_scores(_ACCT, since=_since, until=_until)

st.caption(
    f"统计区间：{_since.isoformat()} → {_until.isoformat()}——不合成一个总分，"
    f"分维度并排看，止损纪律95%但对冲纪律40%平均成一个数只会把短板藏起来。"
)

# ── 四个维度卡片：响应率 / 平均响应天数 / 未处理最久一条 ─────────────
_cols = st.columns(4)
for _col, _dim in zip(_cols, _disc.DIMENSIONS):
    _s = _scores.get(_dim, {})
    _n = _s.get("n", 0)
    _rate = _s.get("response_rate")
    _avg  = _s.get("avg_response_days")
    _old_sym  = _s.get("oldest_open_symbol")
    _old_days = _s.get("oldest_open_days")

    _rate_str = f"{_rate*100:.0f}%" if _rate is not None else "—"
    _rate_col = (
        _G if _rate is not None and _rate >= 0.8 else
        _A if _rate is not None and _rate >= 0.5 else
        _R if _rate is not None else _MUT
    )
    _avg_str = f"{_avg:.1f}天" if _avg is not None else "—"

    with _col:
        st.markdown(
            f"<div style='background:{_SURF};border:1px solid {_BDR};"
            f"border-radius:10px;padding:14px 16px;min-height:172px'>"
            f"<div style='font-size:12.5px;font-weight:700;color:{_TXT};"
            f"margin-bottom:10px'>{_dim}</div>"
            f"<div style='font-size:10px;color:{_MUT};text-transform:uppercase;"
            f"letter-spacing:0.5px'>响应率 [n={_n}]</div>"
            f"<div style='font-size:24px;font-weight:700;color:{_rate_col};"
            f"margin-bottom:8px'>{_rate_str}</div>"
            f"<div style='font-size:10px;color:{_MUT};text-transform:uppercase;"
            f"letter-spacing:0.5px'>平均响应天数</div>"
            f"<div style='font-size:15px;font-weight:600;color:{_TXT};"
            f"margin-bottom:8px'>{_avg_str}</div>"
            f"<div style='font-size:10px;color:{_MUT};text-transform:uppercase;"
            f"letter-spacing:0.5px'>未处理最久</div>"
            f"<div style='font-size:12.5px;color:{_A if _old_sym else _MUT}'>"
            f"{f'{_old_sym} · {_old_days}天' if _old_sym else '无未处理信号'}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

st.divider()

# ── 当前未处理信号明细表 ───────────────────────────────────────────
st.markdown(
    f"<div style='font-size:13px;font-weight:700;color:{_TXT};margin-bottom:8px'>"
    f"当前未处理信号</div>",
    unsafe_allow_html=True,
)
_overdue = _disc.get_overdue_signals(_ACCT)
if _overdue:
    _df = pd.DataFrame([
        {"维度": o["dimension"], "标的": o["symbol"],
         "已开启天数": o["days_open"], "响应窗口": o["window"],
         "超期天数": o["days_open"] - o["window"]}
        for o in sorted(_overdue, key=lambda x: -(x["days_open"] - x["window"]))
    ])
    st.dataframe(_df, use_container_width=True, hide_index=True)
else:
    st.markdown(
        f"<div style='color:{_G};padding:12px;background:{_G}11;"
        f"border-radius:6px;font-size:13px'>✅ 没有超期未处理的纪律信号</div>",
        unsafe_allow_html=True,
    )

st.caption(
    "这张表只列\"已经超过响应窗口还没处理\"的——还在窗口内的open信号不算超期，"
    "不在这里显示。同步账户时如果有新的超期信号，侧边栏会顺手弹一下（方案A，"
    "见 docs/DISCIPLINE_GATE_DESIGN.md §9）。"
)
