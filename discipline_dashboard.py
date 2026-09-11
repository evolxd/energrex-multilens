"""
ENERGREX — 门⑤ 纪律看板（v2）

设计见 docs/DISCIPLINE_AND_REVIEW_ARCHITECTURE.md。记录/核对/打分的算法在
account/discipline.py + account/risk_signals.py，接到 _cascade.py 的账户
同步流程里——每次点"同步账户"，这个页面看到的数据才会更新，不是这里现拉。
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
_MUT = "#8B9BB4"; _TXT = "#E2E8F0"; _B = "#4FC3F7"

_ACCT = "account_1"
_WEIGHT_LABEL = {3: "高", 2: "中", 1: "低"}

st.markdown(
    f"<div style='background:{_SURF};border:1px solid {_BDR};border-radius:10px;"
    f"padding:11px 20px;display:flex;justify-content:space-between;align-items:center;'>"
    f"<span style='font-size:17px;font-weight:800;letter-spacing:1px;color:{_G}'>"
    f"🛡️ 纪律看板</span>"
    f"<span style='font-size:12px;color:{_MUT}'>门④ + 门⑤</span>"
    f"</div>",
    unsafe_allow_html=True,
)

_range_days = st.radio(
    "统计区间", options=[30, 90, 365], index=0,
    format_func=lambda d: f"近{d}天", horizontal=True, label_visibility="collapsed",
)
_until = datetime.date.today()
_since = _until - datetime.timedelta(days=_range_days)

_score = _disc.compute_discipline_score(_ACCT, since=_since, until=_until)
_rates = _disc.compute_discipline_scores(_ACCT, since=_since, until=_until)  # 4个v1维度的响应率细节

# ── 顶部：纪律分（百分制，90%及格） ──────────────────────────────────
_sc = _score["score"]
_grade = _score["grade"]
_grade_col = {"优秀": _G, "及格": _B, "警示": _A, "严重失守": _R}.get(_grade, _MUT)
_sc_str = f"{_sc:.0f}%" if _sc is not None else "—"
_c1, _c2 = st.columns([1, 2])
with _c1:
    st.markdown(
        f"<div style='background:{_SURF};border:1px solid {_grade_col};border-radius:12px;"
        f"padding:18px 22px;text-align:center'>"
        f"<div style='font-size:11px;color:{_MUT};text-transform:uppercase;letter-spacing:1px'>"
        f"纪律分 · 近{_range_days}天</div>"
        f"<div style='font-size:44px;font-weight:800;color:{_grade_col};line-height:1.1'>{_sc_str}</div>"
        f"<div style='font-size:13px;font-weight:700;color:{_grade_col}'>{_grade or '暂无数据'}</div>"
        f"<div style='font-size:10px;color:{_MUT};margin-top:4px'>"
        f"90% 及格 · {_score['n_events']} 个事件</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
with _c2:
    st.caption(
        f"统计区间 {_since.isoformat()} → {_until.isoformat()}。"
        f"纪律分 = Σ(事件分 × 维度权重) / Σ权重 × 100%，下限 0——没有负分，"
        f"严重程度靠三档权重（高3/中2/低1）表达。事件分：窗口内响应=上限"
        f"（门④违规上限0.75，其余1.0），迟响应按 0.15/天 衰减、满7天归0，"
        f"自然漂回/从没解决/到期未处理=0。还 open 且已超窗口的信号算临时 0 拖累，"
        f"直到了结。同步账户时才更新。"
    )
    if _score["pending_review"]:
        st.warning(f"🔎 有 {_score['pending_review']} 条信号待复核——下面「每周复核」逐条标注。")

# ── 分维度breakdown ────────────────────────────────────────────────
st.markdown(
    f"<div style='font-size:13px;font-weight:700;color:{_TXT};margin:14px 0 8px'>"
    f"分维度（进纪律分的 A 类）</div>",
    unsafe_allow_html=True,
)
_bd = _score["by_dimension"]
if not _bd:
    st.caption("这个区间内还没有任何进纪律分的事件——第一次真实同步之后才会有。")
else:
    _rows = []
    for dim in _disc.SCORED_DIMENSIONS:
        v = _bd.get(dim)
        if not v:
            continue
        _rows.append({
            "维度": dim,
            "权重": _WEIGHT_LABEL.get(v["weight"], v["weight"]),
            "维度分": f"{v['score']:.0f}%",
            "事件数": v["n"],
            "响应率(细)": (f"{_rates[dim]['response_rate']*100:.0f}%"
                         if dim in _rates and _rates[dim].get("response_rate") is not None
                         else "—"),
        })
    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)

st.divider()

# ── 当前未处理信号（还 open、已超窗口） ───────────────────────────────
st.markdown(
    f"<div style='font-size:13px;font-weight:700;color:{_TXT};margin-bottom:8px'>"
    f"当前未处理信号（还挂着、已超响应窗口，正在拖累纪律分）</div>",
    unsafe_allow_html=True,
)
_overdue = _disc.get_overdue_signals(_ACCT)
if _overdue:
    st.dataframe(pd.DataFrame([
        {"维度": o["dimension"], "标的": o["symbol"],
         "已开启天数": o["days_open"], "响应窗口": o["window"],
         "超期天数": o["days_open"] - o["window"]}
        for o in sorted(_overdue, key=lambda x: -(x["days_open"] - x["window"]))
    ]), use_container_width=True, hide_index=True)
else:
    st.markdown(
        f"<div style='color:{_G};padding:12px;background:{_G}11;"
        f"border-radius:6px;font-size:13px'>✅ 没有超期未处理的纪律信号</div>",
        unsafe_allow_html=True,
    )

st.divider()

# ── 每周复核（设计文档 §6） ────────────────────────────────────────
st.markdown(
    f"<div style='font-size:13px;font-weight:700;color:{_TXT};margin-bottom:4px'>"
    f"每周复核</div>",
    unsafe_allow_html=True,
)
st.caption(
    "把这段时间「迟响应 / 自然漂回 / 到期未处理」的信号逐条标注。"
    "「有意例外」要写理由——会把整条从纪律分里剔除（书面豁免）；"
    "「不认同信号」也剔除，同时提示去调那个维度的阈值；「漏了」不改分。"
)
_queue = _disc.get_review_queue(_ACCT, since=_since, until=_until)
if not _queue:
    st.markdown(
        f"<div style='color:{_G};padding:12px;background:{_G}11;"
        f"border-radius:6px;font-size:13px'>✅ 没有待复核的信号</div>",
        unsafe_allow_html=True,
    )
else:
    for q in _queue:
        with st.expander(
            f"{q['dimension']} · {q['symbol']} · {q['first_seen_date']} → "
            f"{q['status']}（事件分 {q['event_score']}）",
            expanded=False,
        ):
            st.caption(q["detail"] or "")
            with st.form(f"review_{q['id']}"):
                tag = st.radio("标注", _disc.REVIEW_TAGS, horizontal=True,
                               key=f"tag_{q['id']}")
                note = st.text_area("理由（选「有意例外」时必填）",
                                    key=f"note_{q['id']}", height=70)
                ok = st.form_submit_button("提交")
            if ok:
                if tag == "有意例外" and not note.strip():
                    st.error("「有意例外」必须写理由。")
                else:
                    _disc.set_review_annotation(q["id"], tag, note.strip())
                    st.success("已记录，重新计算纪律分…")
                    st.rerun()
