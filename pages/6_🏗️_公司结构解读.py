"""ENERGREX 公司结构解读 —— 估值出分之后的那一步。

门①的六维评分把一家公司压成一个点：`revenue_growth_yoy = 0.85` 对一家四条
腿各涨 21% 的公司，和一家增量 95% 来自单一分部的公司，是同一个数。但后者
的成长分建立在一件事继续发生上，脆弱度完全不同——而脆弱度决定了这个分数
能不能拿去放大仓位。

这一页展开那个点。定量部分（收入结构 / 增量归因 / 结构漂移）来自
scoring/company_structure.py，数据是 SEC XBRL 的分部收入，可核。定性部分
是 Fisher 十五要点，走 .claude/skills/fisher-company-analysis——那部分刻意
不在这里自动打分，理由见该 skill 的 §4。

2026-09-21：本页原来是 app.py 里的一个页内页。按用户要求提到侧边栏「①
估值发现」下、「误价与特殊机会」之后，成为独立页面。**app.py 里那一份已
经删掉，不是复制过来的**——这个项目已经因为作战室存在两份拷贝而反复出过
问题（改了一份、看的是另一份），同一个页面不留两处实现。
"""

from __future__ import annotations

import pathlib
import sys

import streamlit as st

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from _sidebar import render  # noqa: E402
from scoring.company_structure import assess as _assess_structure  # noqa: E402
from scoring.edgar_fetcher import TICKER_CIK  # noqa: E402
from scoring.exposure_context import non_scored_chain as _non_scored_chain  # noqa: E402
from scoring.scoring_engine import TICKER_CATEGORY  # noqa: E402

st.set_page_config(page_title="ENERGREX 公司结构解读", page_icon="🏗️", layout="wide")
render()

# 标的全集取自 TICKER_CATEGORY——它是这个系统的 universe 正源（CLAUDE.md
# 的修改指南里，"新增股票"要改的就是它）。不从评分 CSV 取，是因为结构分析
# 不需要分数，没必要为了一个下拉框把整张评分表读进来。
_UNIVERSE = sorted(TICKER_CATEGORY)

# 但 448 只里只有这些配了 CIK，其余的点进去只会看到"拉不到 SEC 数据"。
# 默认只列这些，免得在 437 个必然空白的选项里翻——想看没覆盖的也能切过去，
# 那时给出的仍然是"覆盖缺口"而不是"这家公司没有分部披露"。
_COVERED = sorted(set(TICKER_CIK) & set(TICKER_CATEGORY))


def _sync_selected_ticker(widget_key: str) -> None:
    """跟其它页共用 selected_ticker，这样从估值评分点进来时标的不会跳回默认。"""
    value = str(st.session_state.get(widget_key, "")).strip().upper()
    if value:
        st.session_state["selected_ticker"] = value
        st.query_params["ticker"] = value


# URL 里带 ?ticker=XXX 时优先用它（估值评分页的链接就是这么跳过来的）
_q = str(st.query_params.get("ticker", "")).strip().upper()
if _q in _UNIVERSE:
    st.session_state["selected_ticker"] = _q

st.markdown("## 结构分析 — 这个分数靠几条腿撑着")
st.markdown(
    "<div style='color:#8B9BB4;font-size:13px;margin-bottom:16px'>"
    "估值分数回答「贵不贵」，结构分析回答「值不值得拥有」。两者是否决关系："
    "结构不及格的票，分数再高也只能做短期价差，不进核心仓。"
    "</div>",
    unsafe_allow_html=True)

_only_covered = st.checkbox(
    f"只列有 SEC 分部数据的标的（{len(_COVERED)} / {len(_UNIVERSE)} 只）",
    value=True, key="struct_only_covered",
    help="其余标的没配 CIK，拉不到 XBRL 分部收入。取消勾选可以选到它们，"
         "但看到的会是「覆盖缺口」而不是分析结果。",
)
_options = _COVERED if (_only_covered and _COVERED) else _UNIVERSE

_sel_struct = st.session_state.get("selected_ticker")
if _sel_struct not in _options:
    _sel_struct = _options[0] if _options else None
if _sel_struct and st.session_state.get("struct_sel") not in _options:
    st.session_state["struct_sel"] = _sel_struct
struct_ticker = st.selectbox(
    "选择股票", _options, key="struct_sel",
    on_change=_sync_selected_ticker, args=("struct_sel",),
)

_non_scored = _non_scored_chain(struct_ticker)
if _non_scored:
    st.info(
        f"{struct_ticker} 是 {_non_scored} 类标的（ETF/杠杆产品），"
        "没有「管理层诚信」「分部构成」这些维度，结构分析不适用。"
    )
else:
    @st.cache_data(ttl=86400, show_spinner="从 SEC XBRL 拉分部收入…")
    def _segment_revenues(ticker: str):
        """XBRL 分部收入，缓存一天——10-Q/10-K 不是日内会变的东西。"""
        from scoring.edgar_fetcher import (
            TICKER_CIK, _quarterly_segment_revenues, fetch_xbrl_facts,
        )
        cik = TICKER_CIK.get(ticker.upper())
        if not cik:
            return None, "no_cik"
        facts = fetch_xbrl_facts(cik)
        if not facts:
            return None, "no_facts"
        return _quarterly_segment_revenues(facts), None

    _seg, _seg_err = _segment_revenues(struct_ticker)

    if _seg_err == "no_cik":
        st.warning(
            f"{struct_ticker} 不在 `edgar_fetcher.TICKER_CIK` 的映射表里，"
            "拉不到 SEC 数据。补上 CIK 就能用——这是覆盖缺口，不是这家公司"
            "没有分部披露。"
        )
        _seg = None
    elif _seg_err == "no_facts":
        st.warning(
            "SEC EDGAR 没返回数据（可能是限流或网络不通）。这是取数失败，"
            "不是「结构没问题」。"
        )
        _seg = None

    _struct = _assess_structure(struct_ticker, _seg or {})

    # ── 定量层 ────────────────────────────────────────────────
    st.markdown("### ① 收入结构（SEC XBRL，可核）")

    if not _struct.is_analyzable:
        for _n in _struct.notes:
            st.info(_n)
        st.caption(
            "结论是「看不出结构」，不是「结构健康」—— 两者必须分开，"
            "把没查出问题写成没问题是这类分析最贵的错误。"
        )
    else:
        _mix = _struct.mix
        _m1, _m2, _m3, _m4 = st.columns(4)
        _m1.metric("披露分部数", f"{_mix.n_segments}")
        _m2.metric("等效分部数", f"{_mix.effective_segments:.1f}",
                   help="1/HHI。「实际靠几条腿站着」——四个分部但一个占 90%，"
                        "等效分部数约 1.2。")
        _m3.metric("最大分部占比", f"{_mix.top_share * 100:.0f}%",
                   delta=_mix.top_segment or "—", delta_color="off")
        _m4.metric("期末", _mix.period)

        import pandas as _pd_st
        st.dataframe(
            _pd_st.DataFrame([
                {"分部": _k, "收入(百万美元)": f"{_v / 1e6:,.0f}",
                 "占比": f"{_v / _mix.total * 100:.1f}%"}
                for _k, _v in sorted(_mix.segments.items(), key=lambda x: -x[1])
            ] + ([{"分部": "未分配/抵消", "收入(百万美元)": f"{_mix.residual / 1e6:,.0f}",
                   "占比": f"{_mix.residual_share * 100:.1f}%"}]
                 if abs(_mix.residual_share) >= 0.005 else [])),
            use_container_width=True, hide_index=True,
        )

        # ── 增量归因 ──────────────────────────────────────────
        _attr = _struct.attribution
        if _attr is None:
            st.caption(
                "分部收入不足四个季度，算不了同比增量归因。"
                "（不拿三期前凑合——季节性强的生意里那样算出的"
                "「增长」混着季节因素，比不算更误导。）"
            )
        else:
            st.markdown("### ② 增量归因 — 多出来的收入是谁贡献的")
            _g = _attr.total_growth
            _a1, _a2, _a3 = st.columns(3)
            _a1.metric("整体同比",
                       f"{_g * 100:+.0f}%" if _g is not None else "—",
                       delta=f"{_attr.prior_period} → {_attr.period}",
                       delta_color="off")
            _top = _attr.top_contributor
            _share = _attr.share_of_growth(_top) if _top else None
            _a2.metric("最大贡献分部",
                       _top.segment if _top else "—",
                       delta=f"占增量 {_share * 100:.0f}%" if _share is not None else "增量为负",
                       delta_color="off")
            _wo = _attr.growth_without_top
            _a3.metric("该分部增速归零后",
                       f"{_wo * 100:+.0f}%" if _wo is not None else "—",
                       help="纯算术，不是预测：把最大贡献分部的当期收入换回"
                            "上期水平，其余不动。回答「这个增长故事有多依赖"
                            "一件事继续发生」。")

            st.dataframe(
                _pd_st.DataFrame([
                    {
                        "分部": _c.segment,
                        "上年同期": f"{_c.prior / 1e6:,.0f}",
                        "本期": f"{_c.current / 1e6:,.0f}",
                        "增量": f"{_c.delta / 1e6:+,.0f}",
                        "自身增速": (f"{_c.own_growth * 100:+.0f}%"
                                     if _c.own_growth is not None else "新增"),
                        "占整体增量": (
                            f"{_attr.share_of_growth(_c) * 100:.0f}%"
                            if _attr.share_of_growth(_c) is not None else "—"),
                    }
                    for _c in _attr.ranked
                ]),
                use_container_width=True, hide_index=True,
            )

        # ── 结构漂移 ──────────────────────────────────────────
        if len(_struct.drift) >= 2:
            st.markdown("### ③ 结构漂移 — 还是不是同一门生意")
            _drift_df = _pd_st.DataFrame(
                [{**{"期末": _m.period},
                  **{_k: round(_v * 100, 1) for _k, _v in _m.shares.items()}}
                 for _m in _struct.drift]
            ).set_index("期末")
            st.area_chart(_drift_df, height=220)
            st.caption(
                "各分部占净收入的百分比。占比稳定的公司，用历史比率"
                "（ROIC/毛利率/PEG）外推是合理的；占比每季度挪几个点的公司，"
                "去年的 ROIC 跟明年的 ROIC 讲的不是同一门生意。"
            )

    # ── 结论与限制 ────────────────────────────────────────────
    if _struct.flags:
        st.markdown("### 结构警示")
        for _f in _struct.flags:
            st.warning(_f)
    elif _struct.is_analyzable:
        st.success("定量结构层没有发现警示项。定性层（Fisher）仍需单独评估。")

    if _struct.is_analyzable and _struct.notes:
        with st.expander("这次分析本身的限制"):
            for _n in _struct.notes:
                st.caption(f"· {_n}")

    # ── 定性层入口 ────────────────────────────────────────────
    st.markdown("### ④ 定性结构（Fisher 十五要点）")
    st.markdown(
        "<div style='background:#0A1628;border-left:3px solid #FFB74D;"
        "padding:12px 16px;border-radius:0 6px 6px 0;color:#8B9BB4;"
        "font-size:13px;line-height:1.8'>"
        "上面三节是能从 SEC 财报核出来的部分。Fisher 十五要点里还有 9 条"
        "<b>不能从财报观测</b>——销售组织、劳资关系、高管关系、二次增长曲线"
        "的决心、行业特异线索。<br><br>"
        "这一页<b>刻意不给这些点自动打分</b>：给不可观测的东西编一个数，"
        "正是这个系统在 <code>get_category()</code> 里明确拒绝过的错误"
        "（COST 曾因默认成 AI_SOFTWARE 而算出「看着正常、实际毫无意义的分」）。"
        "<br><br>"
        "定性评估走 skill，在对话里跑："
        "</div>",
        unsafe_allow_html=True)
    st.code(f"/fisher-company-analysis {struct_ticker}", language="text")
    st.caption(
        "skill 定义在 `.claude/skills/fisher-company-analysis/SKILL.md`。"
        "它会读上面这几节的定量结论，再逐条走十五要点，最后给一个"
        "**核心仓资格**（VETO / NOT_CORE / CORE_ELIGIBLE / UNASSESSED）"
        "而不是一个综合分 —— 分数不可回测就不能拿去定仓位，"
        "所以 Fisher 结论只减仓、不加仓。"
    )
