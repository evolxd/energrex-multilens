"""
ENERGREX — Bull Call Spread 量化评分
====================================
单票手动工具，跟 bull_put_spread_module.py（Bull Put Spread）是同一套设计：
你输入一个标的代码，它在这一只票上生成所有候选 Call 价差并打分排名，告诉你
具体选哪个行权价组合 + 哪个交割日。不扫全市场——全市场批量筛选见
pages/7_🌐_期权价差全市场筛选.py，那是另一个独立工具。

公式来源：put 版的 4 因子模型来自用户上传的模板"期权价差量化评分系统_1.md"
（2026-09-03），模板本身只覆盖 put。这里的 call 版是镜像构造，ROM/ADR 两项
和模板公式结构一致（结构不依赖信用价差还是借记价差），Buffer% 换成了"所需
涨幅%"（方向镜像，5% 阈值只是量级对称，不是独立验证过的数字），"盈亏平衡
胜率"直接砍掉不造——公式细节和取舍见 scoring/bull_call_spread.py 顶部注释。

到期日范围：用户明确要求"9-12月的交割日"（日历月份窗口，不是天数窗口），
默认预选落在这个窗口内的到期日，可自行增减。

数据源：MarketData.app（复用 scoring/options_chain.py，跟"期权分析"页、
Bull Put Spread 页同一套数据）。
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
from bull_call_spread import (             # noqa: E402
    BullCallCandidate,
    generate_call_candidates_from_chain,
    rank_candidates,
)
import macro_calendar                      # noqa: E402

st.set_page_config(page_title="ENERGREX · Bull Call Spread 评分", page_icon="📐", layout="wide")

_BG, _SURF, _BORDER = "#0A1628", "#0F1923", "#1E2D3D"
_TEXT, _MUTED = "#E2E8F0", "#8B9BB4"
_GOOD, _WARN, _BAD, _BLUE = "#00D4AA", "#FFB347", "#FF4B6E", "#4FC3F7"

st.markdown(
    f"<h2 style='color:{_TEXT};margin-bottom:0'>📐 Bull Call Spread 量化评分</h2>"
    f"<div style='color:{_MUTED};font-size:12px;margin-bottom:16px'>"
    f"Bull Put Spread 模板的镜像构造（非模板原生覆盖）· 默认到期日窗口 9-12月 · "
    f"公式/取舍见页面底部\"评分算法说明\"</div>",
    unsafe_allow_html=True,
)

ticker = st.text_input("标的代码", placeholder="NVDA", key="bcs_ticker").strip().upper()

if not ticker:
    st.info("输入一个标的代码开始。")
    st.stop()

with st.spinner(f"拉取 {ticker} 可用到期日…"):
    expirations = fetch_expirations_marketdata(ticker)

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

# ── 默认预选落在"9-12月交割日"日历窗口内的到期日（同一年）────────────
_TARGET_MONTHS = (9, 10, 11, 12)


def _in_target_window(exp_str: str) -> bool:
    d = datetime.date.fromisoformat(exp_str)
    return d.year == today.year and d.month in _TARGET_MONTHS


default_selection = [e for e, _ in exp_with_dte if _in_target_window(e)]

st.markdown(
    f"<div style='color:{_MUTED};font-size:11px;margin-top:8px'>"
    "默认预选 9-12月（{}年）内的到期日，可自行增减：</div>".format(today.year),
    unsafe_allow_html=True,
)
exp_labels = {f"{e}  (DTE {d})": e for e, d in exp_with_dte}
default_labels = [lbl for lbl, e in exp_labels.items() if e in default_selection]
selected_labels = st.multiselect("到期日", list(exp_labels.keys()), default=default_labels)
selected_exps = [exp_labels[lbl] for lbl in selected_labels]


def _release_risk(expiration: str) -> list[dict]:
    """同 Bull Put Spread 页——已知宏观发布日中落在[今天, expiration]窗口内的
    那些，不代表"发布结果好坏"，只提示这段窗口里有一次已知会放大已实现波动
    率的日程事件。"""
    return macro_calendar.releases_within(today, datetime.date.fromisoformat(expiration))


def _release_risk_label(expiration: str) -> str:
    hits = _release_risk(expiration)
    if not hits:
        return "—"
    parts = []
    for h in hits:
        mark = "" if h["confirmed"] else "（日期未核实）"
        parts.append(f"{h['label']}{mark}")
    return " · ".join(parts)


if selected_exps:
    st.markdown(
        f"<div style='color:{_MUTED};font-size:11px;margin:4px 0'>"
        "⚠️ 发布日风险（不代表发布结果好坏，只提示该窗口内已实现波动率可能放大）："
        "</div>",
        unsafe_allow_html=True,
    )
    for exp in selected_exps:
        label = _release_risk_label(exp)
        color = _MUTED if label == "—" else _WARN
        st.markdown(
            f"<div style='font-size:11px;color:{color};margin-left:8px'>"
            f"{exp}：{label}</div>",
            unsafe_allow_html=True,
        )

col_w, col_mny = st.columns(2)
with col_w:
    widths = st.multiselect("价差宽度 Width（$）", [2.5, 5.0, 7.5, 10.0, 15.0, 20.0], default=[5.0, 10.0])
with col_mny:
    mny_lo, mny_hi = st.slider(
        "长腿虚实值幅度 Moneyness%（相对现价，负=实值ITM）", min_value=-20, max_value=20, value=(-5, 10),
        help="只在这个区间内的行权价上生成长腿（买入腿）候选。这是你实际选择的入场点——"
             "负值=比现价低的实值(ITM)行权价，正值=比现价高的虚值(OTM)行权价。"
             "短腿（卖出腿）由长腿+价差宽度机械决定，不用单独选。",
    )

st.caption(
    "Net Debit 假设：长腿按 ask 买入、短腿按 bid 卖出（保守的可成交估计，"
    "不是用 mid 价这种理论上更好看但不一定能成交的价格）。"
)

if not selected_exps or not widths:
    st.info("至少选一个到期日和一个价差宽度。")
    st.stop()

if st.button("🚀 生成 & 评分", type="primary"):
    all_candidates: list[BullCallCandidate] = []
    fetch_errors: list[str] = []

    with st.spinner("拉取期权链并生成候选价差…"):
        for exp in selected_exps:
            chain = fetch_chain_marketdata(ticker, exp)
            if chain.empty:
                fetch_errors.append(f"{exp}: 期权链为空")
                continue
            calls = chain[chain["side"].astype(str).str.lower() == "call"].copy()
            if calls.empty:
                fetch_errors.append(f"{exp}: 没有 call 数据")
                continue
            if pd.to_numeric(calls["und_px"], errors="coerce").dropna().empty:
                fetch_errors.append(f"{exp}: 缺少现价 (underlyingPrice)")
                continue
            dte = int(calls["dte"].iloc[0]) if "dte" in calls.columns else _dte(exp)

            all_candidates.extend(generate_call_candidates_from_chain(
                ticker, calls, exp, dte, widths, mny_lo, mny_hi,
            ))

    if fetch_errors:
        with st.expander(f"⚠️ {len(fetch_errors)} 个到期日拉取时有问题", expanded=False):
            for e in fetch_errors:
                st.text(e)

    if not all_candidates:
        st.error("没有生成出任何有效候选价差——检查一下 Moneyness 区间/宽度设置是否离谱，"
                  "或者该标的这几个到期日的期权链数据本身就很薄。")
        st.stop()

    ranked_all = rank_candidates(all_candidates, top_n=None)
    st.session_state["bcs_ranked"] = ranked_all
    st.session_state["bcs_ticker_scored"] = ticker

if "bcs_ranked" in st.session_state and st.session_state.get("bcs_ticker_scored") == ticker:
    ranked = st.session_state["bcs_ranked"]

    def _row(s):
        c = s.candidate
        return {
            "到期日": c.expiration, "DTE": c.dte,
            "Long/Short": f"{c.long_strike:g}/{c.short_strike:g}",
            "Width": s.width, "Net Debit": round(c.net_debit, 2),
            "Max Profit": s.max_profit, "Max Loss": s.max_loss,
            "Breakeven": s.breakeven,
            "ROM": f"{s.rom*100:.1f}%", "ADR": f"{s.adr*100:.0f}%",
            "所需涨幅%": f"{s.move_needed_pct*100:+.1f}%",
            "ADR得分": s.score_adr, "所需涨幅得分": s.score_move,
            "ROM得分": s.score_rom, "DTE得分": s.score_dte,
            "总分": s.total_score,
            "发布日风险": _release_risk_label(c.expiration),
        }

    st.markdown(f"#### 排名前十 · {ticker}（共 {len(ranked)} 个候选价差参与评分）")
    top10_df = pd.DataFrame([_row(s) for s in ranked[:10]])
    top10_df.insert(0, "排名", range(1, len(top10_df) + 1))
    st.dataframe(top10_df, use_container_width=True, hide_index=True)

    if ranked:
        best_adr = max(ranked, key=lambda s: s.adr)
        best_move = max(ranked[:10] if len(ranked) >= 10 else ranked, key=lambda s: -s.move_needed_pct)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(
                f"<div style='background:{_SURF};border:1px solid {_BORDER};border-radius:6px;"
                f"padding:10px;font-size:12px;color:{_TEXT}'>"
                f"<b>追求高资金周转率</b>（短DTE、高ADR）<br>"
                f"<span style='color:{_MUTED}'>{best_adr.candidate.expiration} · "
                f"{best_adr.candidate.long_strike:g}/{best_adr.candidate.short_strike:g} · "
                f"ADR {best_adr.adr*100:.0f}% · 总分 {best_adr.total_score}</span></div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"<div style='background:{_SURF};border:1px solid {_BORDER};border-radius:6px;"
                f"padding:10px;font-size:12px;color:{_TEXT}'>"
                f"<b>已过盈亏平衡点/所需涨幅最小</b>（更保守的入场点）<br>"
                f"<span style='color:{_MUTED}'>{best_move.candidate.expiration} · "
                f"{best_move.candidate.long_strike:g}/{best_move.candidate.short_strike:g} · "
                f"所需涨幅 {best_move.move_needed_pct*100:+.1f}% · 总分 {best_move.total_score}</span></div>",
                unsafe_allow_html=True,
            )

    st.markdown(
        f"<div style='color:{_MUTED};font-size:11px;margin-top:12px'>"
        "风险控制（沿用 Bull Put Spread 页同一套通用纪律参考，不是这个页面自动执行的规则）："
        "亏损达到已付权利金的 100%（即价差归零）前考虑止损；"
        "获利达到最大收益的 50%–70% 时可考虑平仓锁定利润。</div>",
        unsafe_allow_html=True,
    )

    st.markdown("#### 全部候选（按总分排序）")
    full_df = pd.DataFrame([_row(s) for s in ranked])
    full_df.insert(0, "排名", range(1, len(full_df) + 1))
    st.dataframe(full_df, use_container_width=True, hide_index=True, height=400)

    st.download_button(
        "⬇️ 导出 CSV",
        full_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{ticker}_bull_call_spread_{datetime.date.today().isoformat()}.csv",
        mime="text/csv",
    )

with st.expander("📐 评分算法说明（Bull Put Spread 模板的镜像构造，非模板原生覆盖）"):
    st.markdown(
        """
**核心公式**
- ROM（资金回报率）= Max Profit / Max Loss = (Width − Net Debit) / Net Debit
  （跟 put 模板结构一致：两种价差本质都是 max_profit/max_loss 的赔率）
- ADR（日均年化回报率）= (ROM / DTE) × 365 （公式跟 put 模板一致）
- 所需涨幅% = (Breakeven − 现价) / 现价 —— **不是**模板原生指标，是对 put 模板
  Buffer%（下跌安全垫）的镜像替代：Call 价差要靠上涨获利，所以换成"离盈亏
  平衡点还差多少涨幅"，负值=现价已经过盈亏平衡点。5%阈值只是跟 Buffer% 的
  5%基准做量级对称，不是独立验证过的数字。
- 盈亏平衡胜率：put 模板的"1 − Net Credit/Width"公式对借记价差不成立，
  **没有**造一个新公式凑数，直接砍掉这一项。

**4因子评分权重（100分制）**
| 评分项 | 权重 | 基准值 | 满分条件 |
|---|---|---|---|
| ADR | 35% | 350%（沿用模板） | `min(35, ADR/3.5 × 35)` |
| 所需涨幅 | 30% | ≤0%满分，≥5%得0分（镜像构造） | `min(30, max(0, (0.05-需求)/0.05 × 30))` |
| ROM | 25% | 40%（沿用模板） | `min(25, ROM/0.40 × 25)` |
| DTE | 10% | 固定10分 | 到期日窗口在页面上游筛选（9-12月），走到评分这步已经过筛，没有部分及格 |

实现见 `scoring/bull_call_spread.py`，单元测试见 `tests/test_bull_call_spread.py`。
        """
    )
