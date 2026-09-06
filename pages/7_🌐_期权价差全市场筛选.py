"""ENERGREX — 期权价差全市场筛选（Bull Put / Bull Call，跨股票池排名前10）

跟"🎯 期权价差评分"页（单票手动输入）不是一回事：这里扫描一批股票，每只票
生成它自己最好的一个价差结构，再跨股票排出全池前10。

评分引擎不变：复用 scoring/bull_put_spread.py / scoring/bull_call_spread.py
（未做任何改动）。这里新增的只是"批量跑哪些票 + 怎么筛出候选池"这一层，
在 scoring/spread_universe_screener.py 里实现，测试见
tests/test_spread_universe_screener.py。

跟用户确认过的两条设计决定（2026-09-06）：
  1. 排名只看期权量化总分，不跟股票基本面维度分加权合成——每行只是把
     final_score和5个维度分标注在旁边做参考，不参与排序。
  2. 下面"候选池预筛选"是明确的v1占位方案（复用现成的 final_score/动量列），
     不是真正的"会不会急跌/会不会急涨"量化判断——那是用户下一步要单独做的，
     不要把这个占位跟那个搞混。
"""
import datetime
import sys
import pathlib

import pandas as pd
import streamlit as st

_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "scoring"))

from options_chain import (                # noqa: E402
    fetch_chain_marketdata,
    fetch_expirations_marketdata,
)
from spread_universe_screener import (      # noqa: E402
    load_universe,
    rank_call_screen_results,
    rank_put_screen_results,
    screen_bull_call_spreads,
    screen_bull_put_spreads,
    select_candidate_pool,
)

st.set_page_config(page_title="ENERGREX · 期权价差全市场筛选", page_icon="🌐", layout="wide")

_BG, _SURF, _BORDER = "#0A1628", "#0F1923", "#1E2D3D"
_TEXT, _MUTED = "#E2E8F0", "#8B9BB4"
_GOOD, _WARN, _BAD = "#00D4AA", "#FFB347", "#FF4B6E"

st.markdown(
    f"<h2 style='color:{_TEXT};margin-bottom:0'>🌐 期权价差全市场筛选</h2>"
    f"<div style='color:{_MUTED};font-size:12px;margin-bottom:16px'>"
    f"扫描一批股票，每只票留最好的一个价差结构，跨股票排出全池前10。"
    f"评分公式跟单票版「🎯 期权价差评分」完全一样，未做任何调整。</div>",
    unsafe_allow_html=True,
)

with st.expander("⚠️ 这个页面的假设/边界（点开看，别跳过）", expanded=False):
    st.markdown(
        """
- **排名只看期权量化总分**（ROM/ADR/Buffer或MoveNeeded/DTE四因子），不跟股票的基本面维度分
  （估值/成长/质量/AI暴露/预期差/final_score）加权合成——那几列只是**标注在旁边参考**，
  不影响排序。原因：一个"合成权重"如果是我编的，就是没有依据的数字，不如不合成。
- **候选池预筛选是v1占位方案**：目前就是"排除熔断/排除🚫风险较高评级，按final_score
  取前N名"（Bull Call 额外要求动量分不低于池内中位数）。这**不是**真正的"这只票短期
  会不会急跌/会不会急涨"的量化判断——那是你说的下一步要单独做的模块，这里先用现成的
  分数占位，模块做好后应该整个换掉这段筛选逻辑。
- 每只候选票都要对 MarketData.app 发起真实请求（到期日列表 + 期权链），池子越大越慢、
  越吃配额，先从小池子（20-30只）试。
- Net Credit / Net Debit 都按"保守可成交价"算：短腿按bid卖、长腿按ask买——不是更好看
  但不一定能成交的mid价。
        """
    )

tab_put, tab_call = st.tabs(["📉 Bull Put Spread（全市场）", "📈 Bull Call Spread（全市场）"])

try:
    universe_df = load_universe()
except FileNotFoundError:
    st.error("找不到 results_validated.csv——先跑一次 refresh_scores.py。")
    st.stop()


def _pool_preview(pool: pd.DataFrame):
    st.caption(f"候选池：{len(pool)} 只（按 final_score 排序，仅展示前10供检查）")
    st.dataframe(
        pool[["ticker", "final_综合得分(0-100)", "mom_动量得分(RSI14/价格vs200日均)", "rating_评级"]]
        .head(10).reset_index(drop=True),
        use_container_width=True, hide_index=True,
    )


# ════════════════════════════════════════════════════════════════════════
# Tab 1: Bull Put Spread
# ════════════════════════════════════════════════════════════════════════
with tab_put:
    st.markdown("#### 候选池 + 参数")
    c1, c2, c3 = st.columns(3)
    with c1:
        pool_n_put = st.slider("候选池大小（按final_score取前N）", 10, 100, 30, step=5, key="put_pool_n")
    with c2:
        dte_lo, dte_hi = st.slider("DTE 窗口（天）", 15, 90, (30, 60), key="put_dte")
    with c3:
        widths_put = st.multiselect("价差宽度 Width（$）", [2.5, 5.0, 7.5, 10.0, 15.0],
                                     default=[5.0, 10.0], key="put_widths")
    otm_lo, otm_hi = st.slider("短腿虚值幅度 OTM%（相对现价）", 1, 30, (3, 15), key="put_otm")

    pool_put = select_candidate_pool(universe_df, top_n=pool_n_put)
    _pool_preview(pool_put)

    if st.button("🚀 扫描 Bull Put Spread 全池", type="primary", key="run_put"):
        if not widths_put:
            st.warning("至少选一个价差宽度。")
            st.stop()
        tickers = pool_put["ticker"].tolist()
        progress = st.progress(0.0, text="扫描中…")
        done = {"n": 0}

        def _fetch_exps(t):
            done["n"] += 1
            progress.progress(min(done["n"] / len(tickers), 1.0), text=f"扫描中… {t}（{done['n']}/{len(tickers)}）")
            return fetch_expirations_marketdata(t)

        scores = screen_bull_put_spreads(
            tickers, _fetch_exps, fetch_chain_marketdata,
            dte_lo=dte_lo, dte_hi=dte_hi, widths=widths_put,
            otm_lo_pct=otm_lo, otm_hi_pct=otm_hi,
        )
        progress.empty()
        st.session_state["put_screen_scores"] = scores
        st.session_state["put_screen_pool_key"] = tuple(tickers)

    scores = st.session_state.get("put_screen_scores")
    if scores is not None and st.session_state.get("put_screen_pool_key") == tuple(pool_put["ticker"].tolist()):
        n_hit = sum(1 for v in scores.values() if v is not None)
        n_miss = len(scores) - n_hit
        st.caption(f"{n_hit} 只有可用价差数据，{n_miss} 只在该DTE窗口/宽度设置下没有生成出候选（不是打了0分，是没数据）")
        ranked = rank_put_screen_results(scores, universe_df, top_n=10)
        if ranked.empty:
            st.info("没有任何候选价差通过筛选——试试放宽DTE窗口或OTM范围。")
        else:
            display = ranked.copy()
            display["net_credit"] = display["net_credit"].round(2)
            display["rom"] = (display["rom"] * 100).round(1).astype(str) + "%"
            display["adr"] = (display["adr"] * 100).round(0).astype(str) + "%"
            display["buffer_pct"] = (display["buffer_pct"] * 100).round(1).astype(str) + "%"
            st.markdown("#### 全市场排名前10")
            st.dataframe(display, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ 导出 CSV", ranked.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"bull_put_spread_universe_top10_{datetime.date.today().isoformat()}.csv",
                mime="text/csv", key="dl_put",
            )


# ════════════════════════════════════════════════════════════════════════
# Tab 2: Bull Call Spread
# ════════════════════════════════════════════════════════════════════════
with tab_call:
    st.markdown("#### 候选池 + 参数")
    c1, c2, c3 = st.columns(3)
    with c1:
        pool_n_call = st.slider("候选池大小（按final_score取前N，且动量≥池内中位数）", 10, 100, 30, step=5, key="call_pool_n")
    with c2:
        expiry_year = st.number_input("到期年份", min_value=2026, max_value=2030, value=2026, key="call_year")
        month_labels = {9: "9月", 10: "10月", 11: "11月", 12: "12月"}
        sel_months = st.multiselect("到期月份", list(month_labels.keys()),
                                     default=[9, 10, 11, 12],
                                     format_func=lambda m: month_labels[m], key="call_months")
    with c3:
        widths_call = st.multiselect("价差宽度 Width（$）", [5.0, 10.0, 15.0, 20.0, 25.0],
                                      default=[10.0, 20.0], key="call_widths")
    mny_lo, mny_hi = st.slider(
        "长腿虚实值范围（相对现价%，负=实值ITM，正=虚值OTM）", -30, 30, (-5, 5), key="call_moneyness",
        help="长腿（买入的那条腿）行权价相对现价的位置。短腿=长腿+宽度，自动算出。",
    )

    pool_call = select_candidate_pool(universe_df, top_n=pool_n_call, require_above_median_momentum=True)
    _pool_preview(pool_call)

    if st.button("🚀 扫描 Bull Call Spread 全池", type="primary", key="run_call"):
        if not widths_call or not sel_months:
            st.warning("至少选一个价差宽度和一个到期月份。")
            st.stop()
        tickers = pool_call["ticker"].tolist()
        progress = st.progress(0.0, text="扫描中…")
        done = {"n": 0}

        def _fetch_exps(t):
            done["n"] += 1
            progress.progress(min(done["n"] / len(tickers), 1.0), text=f"扫描中… {t}（{done['n']}/{len(tickers)}）")
            return fetch_expirations_marketdata(t)

        scores = screen_bull_call_spreads(
            tickers, _fetch_exps, fetch_chain_marketdata,
            expiry_year=int(expiry_year), expiry_months=tuple(sel_months), widths=widths_call,
            long_moneyness_lo_pct=mny_lo, long_moneyness_hi_pct=mny_hi,
        )
        progress.empty()
        st.session_state["call_screen_scores"] = scores
        st.session_state["call_screen_pool_key"] = tuple(tickers)

    scores = st.session_state.get("call_screen_scores")
    if scores is not None and st.session_state.get("call_screen_pool_key") == tuple(pool_call["ticker"].tolist()):
        n_hit = sum(1 for v in scores.values() if v is not None)
        n_miss = len(scores) - n_hit
        st.caption(f"{n_hit} 只有可用价差数据，{n_miss} 只在所选到期月份/宽度设置下没有生成出候选")
        ranked = rank_call_screen_results(scores, universe_df, top_n=10)
        if ranked.empty:
            st.info("没有任何候选价差通过筛选——试试放宽到期月份、虚实值范围或宽度设置。")
        else:
            display = ranked.copy()
            display["net_debit"] = display["net_debit"].round(2)
            display["rom"] = (display["rom"] * 100).round(1).astype(str) + "%"
            display["adr"] = (display["adr"] * 100).round(0).astype(str) + "%"
            display["move_needed_pct"] = (display["move_needed_pct"] * 100).round(1).astype(str) + "%"
            st.markdown("#### 全市场排名前10")
            st.dataframe(display, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ 导出 CSV", ranked.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"bull_call_spread_universe_top10_{datetime.date.today().isoformat()}.csv",
                mime="text/csv", key="dl_call",
            )
