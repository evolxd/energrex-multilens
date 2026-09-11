"""
ENERGREX — 门④ 下场前验证

2026-09-10：用户指出"下场前验证"其实已经有内容——`account/risk.py::
build_recommendations()` 第3段"新开仓候选"（AI评分≥70 + IV regime匹配 +
风险余量允许）一直混在作战室"今日操作简报"里，跟持仓管理建议堆在一起，
没人特意去看。这段逻辑抽成了独立的 `new_opportunity_candidates()`，这个
页面直接调它单独展示——不是新写的东西，是把已经在跑的逻辑挪到它真正该
在的门。

只是候选池的初筛，不是可以直接下单的建议：没看硬约束余量（仓位管理页
门③才有）、没看 Kelly 建议仓位（同样在门③）。开仓前建议接着去门③过一遍。
"""
import pathlib

import pandas as pd
import streamlit as st

_ROOT = pathlib.Path(__file__).parent

st.set_page_config(page_title="ENERGREX · 下单前检查", page_icon="✅", layout="wide")

import _sidebar as _sb
_sb.render()

_G = "#00D4AA"; _A = "#FFB347"; _SURF = "#0F1923"; _BDR = "#1E2D3D"
_MUT = "#8B9BB4"; _TXT = "#E2E8F0"

_ACCT = "account_1"

st.markdown(
    f"<div style='background:{_SURF};border:1px solid {_BDR};border-radius:10px;"
    f"padding:11px 20px;display:flex;justify-content:space-between;align-items:center;'>"
    f"<span style='font-size:17px;font-weight:800;letter-spacing:1px;color:{_G}'>"
    f"✅ 下单前检查</span>"
    f"<span style='font-size:12px;color:{_MUT}'>门④ 下场前验证</span>"
    f"</div>",
    unsafe_allow_html=True,
)

try:
    import _cascade
    _am = _cascade._get_am()
    from account.risk import new_opportunity_candidates

    _snap = _am["_compute_risk_snapshot"](_ACCT)
    _iv   = _am["_compute_iv_regime"](_ACCT)
    _ai   = _am["_load_ai_scores"]()
    _held = {p["underlying"] for p in _am["_build_spread_portfolios"](_ACCT)}

    _candidates = new_opportunity_candidates(
        risk_snapshot=_snap, iv_regime=_iv, ai_scores=_ai, held_underlyings=_held,
    )
    _error = None
except Exception as _exc:
    _candidates, _snap, _iv = [], {}, {}
    _error = str(_exc)

if _error:
    st.warning(f"读取候选数据失败：{_error}")
elif _snap.get("error"):
    st.info("尚未读到可用的账户净值——先到「账户监控」页同步 Firstrade。")
else:
    _leverage = _snap.get("leverage_delta") or _snap.get("leverage") or 0.0
    st.caption(
        f"当前 Delta 杠杆 {_leverage:.2f}x · IV Regime {_iv.get('status', 'NO_DATA')} · "
        f"已持仓 {len(_held)} 个标的——杠杆超过硬上限 75% 时不产生新候选（风险余量不够，"
        f"先去仓位管理页处理，不是来加新仓的时候）。"
    )

    if not _candidates:
        st.markdown(
            f"<div style='color:{_MUT};padding:12px;background:{_SURF};"
            f"border:1px dashed {_BDR};border-radius:6px;font-size:13px'>"
            f"当前没有候选——杠杆余量不够，或者没有 AI 评分 ≥70 且还没持仓的标的。"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div style='font-size:13px;font-weight:700;color:{_TXT};margin:12px 0 8px'>"
            f"新开仓候选（AI评分 ≥70，按分数排序，最多3个）</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(pd.DataFrame([
            {"标的": c["标的"], "AI评分": c["AI评分"], "建议方向": c["行动建议"],
             "触发原因": c["触发原因"]}
            for c in _candidates
        ]), use_container_width=True, hide_index=True)

        st.markdown(
            f"<div style='background:{_SURF};border:1px dashed {_BDR};border-radius:8px;"
            f"padding:14px 16px;margin-top:12px;'>"
            f"<div style='color:{_A};font-weight:700;font-size:13px;margin-bottom:6px'>"
            f"这只是初筛，不是仓位建议</div>"
            f"<div style='color:{_MUT};font-size:12.5px;line-height:1.7'>"
            f"没看硬约束余量（单票/产业链集中度还有多少空间）、没看 Kelly 建议张数——"
            f"这两个都在 <b style='color:{_TXT}'>⚖️ 仓位管理</b>（门③）。选中一个标的后"
            f"先去那页看能不能下、下多大，再去 <b style='color:{_TXT}'>🎯 期权价差工具</b>"
            f"（门②）算具体价差。"
            f"</div></div>",
            unsafe_allow_html=True,
        )

st.divider()
st.caption(
    "门④和门⑤的分工：门④（这页）往前看——我要开新仓，有没有初步候选、"
    "现在是不是开新仓的合适时机；门⑤往回看——过去系统提醒过的事，我有没有"
    "真的照做。门⑤那套「开仓恶化breach/无case交易/偏离Kelly」的事后检测见 "
    "🛡️ 纪律看板，设计见 docs/DISCIPLINE_AND_REVIEW_ARCHITECTURE.md。"
)
