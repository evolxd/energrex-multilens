"""
ENERGREX — Bull Put Spread 量化评分
====================================
方法论来源：用户上传的模板"期权价差量化评分系统_1.md"（2026-09-03）。
公式与评分权重/基准值见 scoring/bull_put_spread.py 顶部注释，与模板逐条对应，
未做任何调整。

数据源：
  - MarketData.app（已接入，复用 scoring/options_chain.py，与"期权分析"页
    同一套数据）
  - Firstrade（未接入 —— 这个代码库里从未抓取过 Firstrade 的期权链页面，
    没有可参考的页面结构。选择它会看到明确的说明而不是报错崩溃或假数据）
"""
import datetime
import sys
import pathlib

import pandas as pd
import streamlit as st

_ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(_ROOT / "scoring"))

from options_chain import (               # noqa: E402
    fetch_chain_marketdata,
    fetch_expirations_marketdata,
)
from bull_put_spread import (              # noqa: E402
    BullPutCandidate,
    breakeven,
    compute_adr,
    compute_breakeven_win_rate,
    compute_buffer_pct,
    compute_rom,
    max_loss,
    rank_candidates,
    score_bull_put_spread,
    spread_width,
)

st.set_page_config(page_title="ENERGREX · Bull Put Spread 评分", page_icon="🎯", layout="wide")

_BG, _SURF, _BORDER = "#0A1628", "#0F1923", "#1E2D3D"
_TEXT, _MUTED = "#E2E8F0", "#8B9BB4"
_GOOD, _WARN, _BAD, _BLUE = "#00D4AA", "#FFB347", "#FF4B6E", "#4FC3F7"

st.markdown(
    f"<h2 style='color:{_TEXT};margin-bottom:0'>🎯 Bull Put Spread 量化评分</h2>"
    f"<div style='color:{_MUTED};font-size:12px;margin-bottom:16px'>"
    f"方法论来自你上传的模板 · 公式/权重见页面底部\"评分算法说明\"</div>",
    unsafe_allow_html=True,
)

# ── 数据源选择 ────────────────────────────────────────────
col_src, col_ticker = st.columns([1, 2])
with col_src:
    source = st.selectbox("期权链数据源", ["MarketData.app（已接入）", "Firstrade（未接入）"])
with col_ticker:
    ticker = st.text_input("标的代码", placeholder="NVDA", key="bps_ticker").strip().upper()

if source.startswith("Firstrade"):
    st.warning(
        "Firstrade 期权链目前**没有实现**——这个代码库里从没抓取过 Firstrade 的期权链页面，"
        "`account_monitor.py` 里的 Chrome CDP 自动化只碰过余额/持仓/成交记录三个页面，"
        "没有可参考的期权链页面结构或接口。\n\n"
        "硬写一个没验证过的解析函数，大概率是猜错选择器、静默返回空数据或直接崩溃——"
        "不想给你一个看起来能用、实际跑不出真东西的假功能。\n\n"
        "要接入的话，需要你提供：该页面的 URL + 一份 HTML 或者（更好）打开浏览器开发者工具 "
        "Network 面板看它是不是走 XHR/JSON 接口、把那个响应样本发我；或者在一个能连到你本机 "
        "已登录 Firstrade 的 Chrome CDP 的会话里，现场带我看一眼页面结构。\n\n"
        "现在先用 MarketData.app 的数据源把评分功能跑起来——公式和排名逻辑跟数据源无关，"
        "以后接入 Firstrade 只需要换掉底层的 `fetch_chain_firstrade()` 实现，"
        "上面的评分代码完全不用动。"
    )
    st.stop()

fetch_expirations, fetch_chain = fetch_expirations_marketdata, fetch_chain_marketdata

if not ticker:
    st.info("输入一个标的代码开始。")
    st.stop()

with st.spinner(f"拉取 {ticker} 可用到期日…"):
    expirations = fetch_expirations(ticker)

if not expirations:
    st.error(f"没拉到 {ticker} 的期权到期日列表——确认代码正确、该标的有期权、或 MarketData.app "
              "API key 配置正常（.env 里的 MARKETDATA_API_KEY）。")
    st.stop()

today = datetime.date.today()


def _dte(exp_str: str) -> int:
    return (datetime.date.fromisoformat(exp_str) - today).days


exp_with_dte = sorted(
    ((e, _dte(e)) for e in expirations if _dte(e) > 0),
    key=lambda x: x[1],
)

# ── 4档 DTE 自动预选（模板建议 20天 / 30-35天 / 40-45天 / 50-60天）────
_TIERS = [(15, 25), (28, 37), (38, 47), (48, 65)]


def _closest_in_tier(lo: int, hi: int) -> str | None:
    in_tier = [e for e, d in exp_with_dte if lo <= d <= hi]
    if in_tier:
        return in_tier[len(in_tier) // 2]
    # tier 内没有到期日时，退而求其次找离 tier 中点最近的一个
    mid = (lo + hi) / 2
    if not exp_with_dte:
        return None
    return min(exp_with_dte, key=lambda x: abs(x[1] - mid))[0]


default_selection = sorted({e for e in (_closest_in_tier(lo, hi) for lo, hi in _TIERS) if e})

st.markdown(
    f"<div style='color:{_MUTED};font-size:11px;margin-top:8px'>"
    "模板建议覆盖 4 档到期日跨度（~20天 / 30-35天 / 40-45天 / 50-60天），"
    "已按此自动预选，可自行增减：</div>",
    unsafe_allow_html=True,
)
exp_labels = {f"{e}  (DTE {d})": e for e, d in exp_with_dte}
default_labels = [lbl for lbl, e in exp_labels.items() if e in default_selection]
selected_labels = st.multiselect("到期日", list(exp_labels.keys()), default=default_labels)
selected_exps = [exp_labels[lbl] for lbl in selected_labels]

col_w, col_otm = st.columns(2)
with col_w:
    widths = st.multiselect("价差宽度 Width（$）", [2.5, 5.0, 7.5, 10.0, 15.0], default=[5.0, 10.0])
with col_otm:
    otm_lo, otm_hi = st.slider(
        "短腿虚值幅度 OTM%（相对现价）", min_value=1, max_value=30, value=(3, 15),
        help="只在这个虚值区间内的行权价上生成短腿候选。太浅（<3%）容易被行权价占用，"
             "太深（>30%）权利金通常薄到没有实际意义。",
    )

st.caption(
    "Net Credit 假设：短腿按 bid 卖出、长腿按 ask 买入（保守的可成交估计，"
    "不是用 mid 价这种理论上更好看但不一定能成交的价格）。"
)

if not selected_exps or not widths:
    st.info("至少选一个到期日和一个价差宽度。")
    st.stop()

if st.button("🚀 生成 & 评分", type="primary"):
    all_candidates: list[BullPutCandidate] = []
    fetch_errors: list[str] = []

    with st.spinner("拉取期权链并生成候选价差…"):
        for exp in selected_exps:
            chain = fetch_chain(ticker, exp)
            if chain.empty:
                fetch_errors.append(f"{exp}: 期权链为空")
                continue
            puts = chain[chain["side"].astype(str).str.lower() == "put"].copy()
            if puts.empty:
                fetch_errors.append(f"{exp}: 没有 put 数据")
                continue
            stock_price = pd.to_numeric(puts["und_px"], errors="coerce").dropna()
            if stock_price.empty:
                fetch_errors.append(f"{exp}: 缺少现价 (underlyingPrice)")
                continue
            stock_price = float(stock_price.iloc[0])
            dte = int(puts["dte"].iloc[0]) if "dte" in puts.columns else _dte(exp)

            strikes = puts.set_index("strike")[["bid", "ask"]].sort_index()
            short_lo, short_hi = stock_price * (1 - otm_hi / 100), stock_price * (1 - otm_lo / 100)
            short_candidates = strikes[(strikes.index >= short_lo) & (strikes.index <= short_hi)]

            for short_strike, short_row in short_candidates.iterrows():
                short_bid = short_row["bid"]
                if pd.isna(short_bid) or short_bid <= 0:
                    continue
                for width in widths:
                    long_strike = round(short_strike - width, 2)
                    if long_strike not in strikes.index:
                        continue
                    long_ask = strikes.loc[long_strike, "ask"]
                    if pd.isna(long_ask) or long_ask <= 0:
                        continue
                    net_credit = round(float(short_bid) - float(long_ask), 4)
                    if net_credit <= 0:
                        continue
                    all_candidates.append(BullPutCandidate(
                        ticker=ticker, expiration=exp, dte=dte,
                        short_strike=float(short_strike), long_strike=float(long_strike),
                        net_credit=net_credit, stock_price=stock_price,
                    ))

    if fetch_errors:
        with st.expander(f"⚠️ {len(fetch_errors)} 个到期日拉取时有问题", expanded=False):
            for e in fetch_errors:
                st.text(e)

    if not all_candidates:
        st.error("没有生成出任何有效候选价差——检查一下 OTM 区间/宽度设置是否离谱，"
                  "或者该标的这几个到期日的期权链数据本身就很薄。")
        st.stop()

    ranked_all = rank_candidates(all_candidates, top_n=None)
    st.session_state["bps_ranked"] = ranked_all
    st.session_state["bps_ticker_scored"] = ticker

if "bps_ranked" in st.session_state and st.session_state.get("bps_ticker_scored") == ticker:
    ranked = st.session_state["bps_ranked"]

    def _row(s):
        c = s.candidate
        return {
            "到期日": c.expiration, "DTE": c.dte,
            "Short/Long": f"{c.short_strike:g}/{c.long_strike:g}",
            "Width": s.width, "Net Credit": round(c.net_credit, 2),
            "Max Profit": round(c.net_credit, 2), "Max Loss": s.max_loss,
            "Breakeven": s.breakeven,
            "ROM": f"{s.rom*100:.1f}%", "ADR": f"{s.adr*100:.0f}%",
            "Buffer%": f"{s.buffer_pct*100:.1f}%",
            "盈亏平衡胜率": f"{s.breakeven_win_rate*100:.1f}%",
            "ADR得分": s.score_adr, "Buffer得分": s.score_buffer,
            "ROM得分": s.score_rom, "DTE得分": s.score_dte,
            "总分": s.total_score,
        }

    st.markdown(f"#### 排名前十 · {ticker}（共 {len(ranked)} 个候选价差参与评分）")
    top10_df = pd.DataFrame([_row(s) for s in ranked[:10]])
    top10_df.insert(0, "排名", range(1, len(top10_df) + 1))
    st.dataframe(top10_df, use_container_width=True, hide_index=True)

    if ranked:
        best_adr = max(ranked, key=lambda s: s.adr)
        best_buf = max(ranked[:10] if len(ranked) >= 10 else ranked, key=lambda s: s.buffer_pct)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(
                f"<div style='background:{_SURF};border:1px solid {_BORDER};border-radius:6px;"
                f"padding:10px;font-size:12px;color:{_TEXT}'>"
                f"<b>追求高资金周转率</b>（短DTE、高ADR）<br>"
                f"<span style='color:{_MUTED}'>{best_adr.candidate.expiration} · "
                f"{best_adr.candidate.short_strike:g}/{best_adr.candidate.long_strike:g} · "
                f"ADR {best_adr.adr*100:.0f}% · 总分 {best_adr.total_score}</span></div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"<div style='background:{_SURF};border:1px solid {_BORDER};border-radius:6px;"
                f"padding:10px;font-size:12px;color:{_TEXT}'>"
                f"<b>追求更宽安全边际</b>（长DTE、高Buffer）<br>"
                f"<span style='color:{_MUTED}'>{best_buf.candidate.expiration} · "
                f"{best_buf.candidate.short_strike:g}/{best_buf.candidate.long_strike:g} · "
                f"Buffer {best_buf.buffer_pct*100:.1f}% · 总分 {best_buf.total_score}</span></div>",
                unsafe_allow_html=True,
            )

    st.markdown(
        f"<div style='color:{_MUTED};font-size:11px;margin-top:12px'>"
        "风险控制（模板 5.2）：亏损达到初始权利金的 100%–150% 时止损平仓；"
        "获利达到最大收益的 50%–70% 时平仓锁定利润。这是通用纪律参考，不是这个页面"
        "自动执行的规则。</div>",
        unsafe_allow_html=True,
    )

    st.markdown("#### 全部候选（按总分排序）")
    full_df = pd.DataFrame([_row(s) for s in ranked])
    full_df.insert(0, "排名", range(1, len(full_df) + 1))
    st.dataframe(full_df, use_container_width=True, hide_index=True, height=400)

    st.download_button(
        "⬇️ 导出 CSV",
        full_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{ticker}_bull_put_spread_{datetime.date.today().isoformat()}.csv",
        mime="text/csv",
    )

with st.expander("📐 评分算法说明（来自上传的模板，公式未做任何调整）"):
    st.markdown(
        """
**核心公式**
- ROM（保证金回报率）= Net Credit / (Width − Net Credit)
- ADR（日均年化回报率）= (ROM / DTE) × 365
- Buffer%（安全垫）= (股价 − (短腿行权价 − Net Credit)) / 股价
- 盈亏平衡胜率 = 1 − Net Credit / Width

**4因子评分权重（100分制）**
| 评分项 | 权重 | 基准值 | 满分条件 |
|---|---|---|---|
| ADR | 35% | 350% | `min(35, ADR/3.5 × 35)` |
| Buffer | 30% | 5% | `min(30, Buffer%/0.05 × 30)` |
| ROM | 25% | 40% | `min(25, ROM/0.40 × 25)` |
| DTE | 10% | 30-45天满分 | 区间内10分，否则8.5分 |

实现见 `scoring/bull_put_spread.py`，单元测试见 `tests/test_bull_put_spread.py`
（逐条对照模板 Section 6 的 Python 参考实现验证过）。
        """
    )
