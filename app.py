"""
AI成长股估值评分系统 — Streamlit Dashboard
==========================================
页面：排行榜 / 单股详情 / 对比 / 评分审计
数据源：results_validated.csv（84只美股，验证通过）
"""

import sys, os, pathlib, datetime, io, subprocess
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scoring"))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from scoring_engine import get_category, WEIGHT_CONFIG, calc_damodaran_report, safe_val
from kelly_position import suggested_position_pct, band_detail, kelly_meta
from investor_lenses import all_investor_lenses

_ROOT          = pathlib.Path(__file__).parent
_CSV_VALIDATED = _ROOT / "results_validated.csv"
_CSV_RAW       = _ROOT / "results.csv"
OVERRIDES_PATH = _ROOT / "scoring" / "user_overrides.json"

# ── 从 results_validated.csv 加载评分数据 ─────────────────

def _parse_raw_cell(cell) -> float | None:
    if not cell or str(cell).strip().startswith("n/a"):
        return None
    try:
        return float(str(cell).split()[0].replace("%", ""))
    except Exception:
        return None


@st.cache_data(ttl=300)
def load_from_csv() -> pd.DataFrame:
    path = _CSV_VALIDATED if _CSV_VALIDATED.exists() else _CSV_RAW
    df = pd.read_csv(path, encoding="utf-8-sig")

    # first-wins: each target name is claimed by the first column whose prefix matches
    _taken: set[str] = set()
    _SCORE_PREFIXES = [
        ("val_",      "valuation_score"),
        ("grw_",      "growth_score"),
        ("qlt_",      "quality_score"),
        ("ai_",       "ai_exposure_score"),
        ("exp_",      "expectation_gap_score"),
        ("mom_",      "momentum_score"),
        ("risk_",     "risk_penalty"),
        ("final_",    "final_score"),
        ("rating_",   "rating"),
        ("circuit_",  "circuit"),
        ("sector_",   "category"),
        ("company_",  "company"),
    ]
    renames: dict[str, str] = {}
    for col in df.columns:
        for pfx, target in _SCORE_PREFIXES:
            if col.startswith(pfx) and target not in _taken:
                renames[col] = target
                _taken.add(target)
                break
    df = df.rename(columns=renames)

    for col in ["valuation_score", "growth_score", "quality_score",
                "ai_exposure_score", "expectation_gap_score",
                "momentum_score", "risk_penalty", "final_score"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # validation confidence → grade
    if "validation_confidence" in df.columns:
        df["validation_confidence"] = pd.to_numeric(df["validation_confidence"], errors="coerce").fillna(0)
        df["confidence_grade"] = df["validation_confidence"].apply(
            lambda c: "A" if c >= 0.95 else ("B" if c >= 0.85 else ("C" if c >= 0.65 else "?")))
    else:
        df["confidence_grade"] = "B"

    # base score (before risk) and estimated fields count
    df["raw_score"] = df["final_score"] + df["risk_penalty"]
    lf_col = next((c for c in df.columns if "live_fields" in c), None)
    df["estimated_fields"] = (22 - pd.to_numeric(df[lf_col], errors="coerce").fillna(0)).clip(0).astype(int) \
        if lf_col else 0

    # Always recompute rating from final_score — CSV column may be stale
    def _score_to_rating(s: float) -> str:
        if   s >= 65: return "⭐ Strong Buy"
        elif s >= 55: return "✅ Buy"
        elif s >= 45: return "👀 Watch"
        elif s >= 35: return "⚠️ Expensive"
        else:         return "🚫 Avoid"

    df["rating"] = df["final_score"].apply(_score_to_rating)
    df["kelly_position_pct"] = df["final_score"].apply(suggested_position_pct)

    df = df.sort_values("final_score", ascending=False).reset_index(drop=True)
    df.index = df.index + 1   # 1-based ranking
    return df


@st.cache_data(ttl=300)
def build_csv_data(df_json: str) -> dict[str, dict]:
    """Extract per-ticker raw data dict for audit/detail pages."""
    df = pd.read_json(io.StringIO(df_json), orient="split")
    RAW_MAP = [
        ("peg",         "peg_ratio",                   False),
        ("ev_sales",    "ev_sales",                    False),
        ("forward_pe",  "forward_pe",                  False),
        ("fcf_yield",   "fcf_yield",                   True),
        ("rev_growth",  "revenue_growth_yoy",          True),
        ("eps_growth",  "eps_growth_yoy",              True),
        ("fwd_rev",     "next_year_revenue_growth_est", True),
        ("gross_margin","gross_margin",                True),
        ("fcf_margin",  "fcf_margin",                  True),
        ("roic",        "roic",                        True),
        ("de_ratio",    "de_ratio",                    False),
        ("nrr",         "net_revenue_retention",       False),
        ("rsi14",       "rsi_14",                      False),
        ("vs200ma",     "price_vs_200dma",             True),
        ("beta",        "beta",                        False),
        ("max_dd",      "max_drawdown_1y",             True),
    ]
    col_map: dict[str, tuple[str, bool]] = {}
    for col in df.columns:
        if not str(col).startswith("raw_"):
            continue
        cl = col.lower()
        for frag, field, pct in RAW_MAP:
            if frag in cl:
                col_map[col] = (field, pct)
                break

    result: dict[str, dict] = {}
    for _, row in df.iterrows():
        ticker = row["ticker"]
        d: dict = {"company_name": row.get("company", ticker)}
        for col, (field, pct) in col_map.items():
            v = _parse_raw_cell(row.get(col))
            if v is not None:
                d[field] = v / 100.0 if (pct and abs(v) > 2) else v
        if "de_ratio" in d:
            d["debt_to_equity"] = d["de_ratio"]
        result[ticker] = d
    return result


def load_overrides() -> dict:
    try:
        return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_overrides(data: dict):
    OVERRIDES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ── 加载数据 ──────────────────────────────────────────────
df = load_from_csv()
_CSV_MTIME = (_CSV_VALIDATED if _CSV_VALIDATED.exists() else _CSV_RAW).stat().st_mtime
_csv_data: dict[str, dict] = build_csv_data(df.reset_index().to_json(orient="split"))
TICKERS = df["ticker"].tolist()

def get_active_stocks() -> dict[str, dict]:
    """Backward-compat accessor: returns per-ticker raw data dict."""
    return _csv_data

# ── 页面配置 ────────────────────────────────────────────
st.set_page_config(
    page_title="AI估值评分系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 全局样式 ────────────────────────────────────────────
st.markdown("""
<style>
/* 主色调：深色金融终端风格 */
:root {
    --accent: #00D4AA;
    --accent2: #FFB347;
    --danger: #FF4B6E;
    --text-muted: #8B9BB4;
}

/* 隐藏默认页脚 */
footer {visibility: hidden;}
#MainMenu {visibility: hidden;}

/* 指标卡片 */
.metric-card {
    background: #0F1923;
    border: 1px solid #1E2D3D;
    border-radius: 8px;
    padding: 16px 18px;
    margin-bottom: 12px;
}

/* 评分条 */
.score-bar-wrap {
    background: #1E2D3D;
    border-radius: 4px;
    height: 8px;
    width: 100%;
    overflow: hidden;
}

/* 评级徽章 */
.rating-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.5px;
}

/* 数据标签 */
.data-label {
    color: #8B9BB4;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 2px;
}

/* 大数字 */
.big-number {
    font-size: 28px;
    font-weight: 700;
    line-height: 1.1;
}

/* 分隔线 */
.divider {
    border-top: 1px solid #1E2D3D;
    margin: 16px 0;
}

/* Streamlit 默认元素微调 */
div[data-testid="stMetricValue"] {
    font-size: 1.6rem !important;
}
div[data-testid="stMetricLabel"] {
    font-size: 0.75rem !important;
    color: #8B9BB4 !important;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* 统计卡片按钮 */
div[data-testid="stButton"][data-key="flt_all"] > button,
div[data-testid="stButton"][data-key="flt_buy"] > button,
div[data-testid="stButton"][data-key="flt_watch"] > button,
div[data-testid="stButton"][data-key="flt_exp"] > button,
div[data-testid="stButton"][data-key="flt_avoid"] > button {
    height: 82px !important;
    white-space: pre-line !important;
    font-size: 22px !important;
    font-weight: 700 !important;
    line-height: 1.25 !important;
    border-radius: 10px !important;
    border: 2px solid #1E2D3D !important;
    background: #0F1923 !important;
    color: #E2E8F0 !important;
    text-align: center !important;
    transition: all 0.15s !important;
}
div[data-testid="stButton"][data-key="flt_all"] > button:hover,
div[data-testid="stButton"][data-key="flt_buy"] > button:hover,
div[data-testid="stButton"][data-key="flt_watch"] > button:hover,
div[data-testid="stButton"][data-key="flt_exp"] > button:hover,
div[data-testid="stButton"][data-key="flt_avoid"] > button:hover {
    border-color: #4FC3F7 !important;
    color: #4FC3F7 !important;
}
</style>
""", unsafe_allow_html=True)

# ── Session State 初始化 ────────────────────────────────────
if "user_overrides" not in st.session_state:
    st.session_state.user_overrides = load_overrides()  # {ticker: {field: value}}
if "filter_rating" not in st.session_state:
    st.session_state.filter_rating = None  # None = 全部；否则是 rating 组名

# ── 辅助函数 ─────────────────────────────────────────────
RATING_COLORS = {
    "⭐ Strong Buy": "#00D4AA",
    "✅ Buy":        "#4CAF50",
    "👀 Watch":      "#FFB347",
    "⚠️ Expensive":  "#FF8C42",
    "🚫 Avoid":      "#FF4B6E",
}

SCORE_COLS = [
    ("valuation_score",       "估值",   "#4FC3F7"),
    ("growth_score",          "成长",   "#00D4AA"),
    ("quality_score",         "质量",   "#A78BFA"),
    ("ai_exposure_score",     "AI暴露", "#FFB347"),
    ("expectation_gap_score", "预期差", "#F472B6"),
]

def rating_color(rating: str) -> str:
    for k, v in RATING_COLORS.items():
        if k in rating:
            return v
    return "#8B9BB4"

def score_color(score: float) -> str:
    if score >= 65: return "#00D4AA"
    if score >= 55: return "#4CAF50"
    if score >= 45: return "#FFB347"
    if score >= 35: return "#FF8C42"
    return "#FF4B6E"

def fmt_pct(v):
    if v is None: return "N/A"
    return f"{v*100:.1f}%"

def fmt_x(v):
    if v is None: return "N/A"
    return f"{v:.1f}x"

def make_radar(row: pd.Series, title: str = "") -> go.Figure:
    cats  = ["估值", "成长", "质量", "AI暴露", "预期差"]
    vals  = [
        row["valuation_score"],
        row["growth_score"],
        row["quality_score"],
        row["ai_exposure_score"],
        row["expectation_gap_score"],
    ]
    fig = go.Figure(go.Scatterpolar(
        r=vals + [vals[0]],
        theta=cats + [cats[0]],
        fill="toself",
        fillcolor="rgba(0,212,170,0.15)",
        line=dict(color="#00D4AA", width=2),
        marker=dict(size=5, color="#00D4AA"),
        hovertemplate="%{theta}: %{r:.1f}<extra></extra>",
    ))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100],
                            gridcolor="#1E2D3D", tickcolor="#1E2D3D",
                            tickfont=dict(color="#8B9BB4", size=10)),
            angularaxis=dict(gridcolor="#1E2D3D",
                             tickfont=dict(color="#E2E8F0", size=12)),
            bgcolor="#0A1628",
        ),
        paper_bgcolor="#0A1628",
        plot_bgcolor="#0A1628",
        showlegend=False,
        margin=dict(l=40, r=40, t=30, b=30),
        height=280,
    )
    return fig

def make_score_bar_chart(row: pd.Series) -> go.Figure:
    labels = ["估值", "成长", "质量", "AI暴露", "预期差"]
    values = [
        row["valuation_score"], row["growth_score"],
        row["quality_score"],   row["ai_exposure_score"],
        row["expectation_gap_score"],
    ]
    colors = ["#4FC3F7","#00D4AA","#A78BFA","#FFB347","#F472B6"]

    fig = go.Figure(go.Bar(
        x=values, y=labels,
        orientation="h",
        marker=dict(color=colors, line=dict(width=0)),
        text=[f"{v:.0f}" for v in values],
        textposition="outside",
        textfont=dict(color="#E2E8F0", size=12),
        hovertemplate="%{y}: %{x:.1f}<extra></extra>",
    ))
    fig.add_vline(x=50, line=dict(color="#2D3F55", width=1, dash="dot"))
    fig.update_layout(
        xaxis=dict(range=[0, 115], showgrid=False,
                   zeroline=False, tickfont=dict(color="#8B9BB4")),
        yaxis=dict(showgrid=False, tickfont=dict(color="#E2E8F0", size=13)),
        paper_bgcolor="#0A1628",
        plot_bgcolor="#0A1628",
        margin=dict(l=10, r=60, t=10, b=10),
        height=200,
        bargap=0.35,
    )
    return fig


# ══════════════════════════════════════════════════════════
# 侧边栏导航
# ══════════════════════════════════════════════════════════
def _age_str(ts: datetime.datetime | None) -> str:
    """将 datetime 转为人类可读的时效字符串"""
    if ts is None:
        return ""
    delta = datetime.datetime.now() - ts
    m = int(delta.total_seconds() / 60)
    if m < 1:
        return "刚刚"
    if m < 60:
        return f"{m}分钟前"
    h, rm = divmod(m, 60)
    return f"{h}小时{rm}分钟前" if rm else f"{h}小时前"

import _sidebar as _sb
_sb.render()

with st.sidebar:
    st.markdown("### 📊 AI估值评分系统")

    # CSV source info
    _csv_file = _CSV_VALIDATED if _CSV_VALIDATED.exists() else _CSV_RAW
    _csv_dt   = datetime.datetime.fromtimestamp(_CSV_MTIME)
    _csv_age  = _age_str(_csv_dt)
    st.markdown(
        f"<span style='color:#4FC3F7;font-size:12px'>📄 {_csv_file.name} · {_csv_age}</span>",
        unsafe_allow_html=True)
    st.markdown(
        f"<span style='color:#8B9BB4;font-size:11px'>{len(df)} 只美股 · 84/84 验证通过</span>",
        unsafe_allow_html=True)
    st.divider()

    page = st.radio(
        "导航",
        ["🏆 排行榜", "🔍 单股详情", "⚖️ 对比分析", "🔬 评分审计", "📝 数据编辑"],
        label_visibility="collapsed",
    )
    st.divider()

    # 快速筛选
    st.markdown("<span style='color:#8B9BB4;font-size:11px;text-transform:uppercase;letter-spacing:1px'>筛选</span>",
                unsafe_allow_html=True)
    search_q = st.text_input("搜索股票代码", placeholder="NVDA, AMD …",
                              label_visibility="collapsed", key="search_q")
    cats = ["全部"] + sorted(df["category"].dropna().unique().tolist())
    sel_cat = st.selectbox("公司类型", cats, label_visibility="collapsed")
    min_score = st.slider("最低 Final Score", 0, 100, 0)
    top_n_opts = [20, 50, len(df)]
    top_n = st.select_slider("显示前 N 只", options=top_n_opts,
                              value=top_n_opts[1], key="top_n")

    st.divider()

    # 极速价格刷新
    if st.button("⚡ 极速价格刷新（~5秒）", use_container_width=True,
                 help="只拉实时股价，从存储基本面重算 PE/EV/PEG，约5秒完成84只"):
        import refresh_scores as _rs_sb
        with st.spinner("⚡ 拉取实时价格并重算评分…"):
            try:
                _rs_sb.refresh_prices_only(verbose=False)
                st.cache_data.clear()
                st.success("价格已更新，评分已重算")
                st.rerun()
            except Exception as _e:
                st.error(f"极速刷新失败：{_e}")

    if st.button("🔄 全量刷新（~2分钟）", use_container_width=True,
                 help="重新拉取所有财务数据 + 动量指标，同时更新基本面分母"):
        import refresh_scores as _rs_sb2
        with st.spinner("🔄 全量刷新中（拉取财务数据+动量）…"):
            try:
                _rs_sb2.refresh_all(verbose=False)
                st.cache_data.clear()
                st.success("全量刷新完成")
                st.rerun()
            except Exception as _e:
                st.error(f"全量刷新失败：{_e}")

    st.markdown("""
<div style='color:#8B9BB4;font-size:11px;line-height:1.8'>
数据来源<br>
📄 results_validated.csv<br>
84只美股 · 全部验证通过<br>
含验证置信度 &amp; 人工核查标记
</div>
""", unsafe_allow_html=True)


# ── 应用筛选 ─────────────────────────────────────────────
df_view = df.copy()
if search_q.strip():
    q = search_q.strip().upper()
    df_view = df_view[df_view["ticker"].str.contains(q, case=False, na=False)]
if sel_cat != "全部":
    df_view = df_view[df_view["category"] == sel_cat]
df_view = df_view[df_view["final_score"] >= min_score]


# ══════════════════════════════════════════════════════════
# 页面 1：排行榜
# ══════════════════════════════════════════════════════════
if page == "🏆 排行榜":
    # ── 顶部汇总指标（可点击筛选）────────────────────────
    total      = len(df_view)
    strong_buy = (df_view["rating"] == "⭐ Strong Buy").sum()
    buy        = (df_view["rating"] == "✅ Buy").sum()
    watch      = (df_view["rating"] == "👀 Watch").sum()
    expensive  = (df_view["rating"] == "⚠️ Expensive").sum()
    avoid      = (df_view["rating"] == "🚫 Avoid").sum()

    # filter_rating 可取值：None / "buy_plus" / "watch" / "expensive" / "avoid"
    _fr = st.session_state.filter_rating

    def _set_filter(val):
        st.session_state.filter_rating = None if st.session_state.filter_rating == val else val

    _sc1, _sc2, _sc3, _sc4, _sc5 = st.columns(5)

    with _sc1:
        _lbl = f"{'✓ ' if _fr is None else ''}{total}\n全 部"
        st.button(_lbl, key="flt_all", help="显示全部股票",
                  on_click=_set_filter, args=[None],
                  use_container_width=True)

    with _sc2:
        _lbl = f"{'✓ ' if _fr == 'buy_plus' else ''}{strong_buy + buy}\nBuy 以上"
        st.button(_lbl, key="flt_buy", help="筛选 Buy 以上（Strong Buy + Buy）",
                  on_click=_set_filter, args=["buy_plus"],
                  use_container_width=True)

    with _sc3:
        _lbl = f"{'✓ ' if _fr == 'watch' else ''}{watch}\n观  察"
        st.button(_lbl, key="flt_watch", help="筛选 👀 Watch（观察，≥45分）",
                  on_click=_set_filter, args=["watch"],
                  use_container_width=True)

    with _sc4:
        _lbl = f"{'✓ ' if _fr == 'expensive' else ''}{expensive}\n偏  贵"
        st.button(_lbl, key="flt_exp", help="筛选 ⚠️ Expensive（偏贵）",
                  on_click=_set_filter, args=["expensive"],
                  use_container_width=True)

    with _sc5:
        _lbl = f"{'✓ ' if _fr == 'avoid' else ''}{avoid}\n回  避"
        st.button(_lbl, key="flt_avoid", help="筛选 🚫 Avoid（回避）",
                  on_click=_set_filter, args=["avoid"],
                  use_container_width=True)

    # 动态注入激活状态样式
    _FILT_STYLE = {
        None:        ("#4FC3F7", "flt_all"),
        "buy_plus":  ("#00D4AA", "flt_buy"),
        "watch":     ("#FFB347", "flt_watch"),
        "expensive": ("#FF8C42", "flt_exp"),
        "avoid":     ("#FF4B6E", "flt_avoid"),
    }
    _ac, _ak = _FILT_STYLE.get(_fr, ("#4FC3F7", "flt_all"))
    st.markdown(
        f"<style>"
        f"div[data-testid='stButton'][data-key='{_ak}'] > button {{"
        f"border-color:{_ac} !important;"
        f"color:{_ac} !important;"
        f"background:{_ac}18 !important;"
        f"}}</style>",
        unsafe_allow_html=True,
    )

    # ── 激活筛选横幅 ──────────────────────────────────────
    _FR_META = {
        "buy_plus":  ("Buy 以上", "#00D4AA",
                      ["⭐ Strong Buy", "✅ Buy"]),
        "watch":     ("👀 Watch（观察）", "#FFB347",
                      ["👀 Watch"]),
        "expensive": ("⚠️ Expensive（偏贵）", "#FF8C42",
                      ["⚠️ Expensive"]),
        "avoid":     ("🚫 Avoid（回避）", "#FF4B6E",
                      ["🚫 Avoid"]),
    }
    if _fr and _fr in _FR_META:
        _fr_label, _fr_color, _fr_ratings = _FR_META[_fr]
        df_view = df_view[df_view["rating"].isin(_fr_ratings)]
        _banner_col, _clear_col = st.columns([5, 1])
        _banner_col.markdown(
            f"<div style='background:{_fr_color}18;border:1px solid {_fr_color}44;"
            f"border-radius:6px;padding:7px 14px;font-size:13px;color:{_fr_color};"
            f"margin:8px 0'>当前筛选：<b>{_fr_label}</b>（{len(df_view)} 只）</div>",
            unsafe_allow_html=True,
        )
        if _clear_col.button("× 清除", key="flt_clear"):
            st.session_state.filter_rating = None
            st.rerun()

    # ── 主排行榜表格 ─────────────────────────────────────
    _total_filtered = len(df_view)
    _show_n = top_n if top_n < _total_filtered else _total_filtered
    _suffix = f"（前 {_show_n} / 共 {_total_filtered} 只）" if _show_n < _total_filtered else f"（共 {_total_filtered} 只）"
    st.markdown(f"#### 综合评分排名 {_suffix}")

    for _, row in df_view.head(_show_n).iterrows():
        rank    = int(row.name)
        r_color = rating_color(row["rating"])
        fs      = row["final_score"]
        fs_col  = score_color(fs)
        rp      = row["risk_penalty"]
        hr      = str(row.get("human_review_required", "FALSE")).upper()
        # 用户已在数据编辑页核对过 → 不再显示核查 badge（覆盖 CSV 旧标记）
        _tk_ov  = st.session_state.user_overrides.get(row["ticker"], {})
        _user_verified = any(
            isinstance(v, dict) and v.get("status") == "verified"
            for v in _tk_ov.values()
        )
        review_badge = (
            "<span style='background:#FFB34722;color:#FFB347;font-size:8px;"
            "padding:1px 4px;border-radius:2px;border:1px solid #FFB34744;"
            "margin-left:4px'>核查</span>"
            if hr == "TRUE" and not _user_verified else ""
        )

        col_left, col_bars, col_score = st.columns([3, 5, 2])

        with col_left:
            _kp = row.get("kelly_position_pct")
            _kp_html = (
                f"<span style='color:#8B9BB4;font-size:9px;margin-left:6px'>"
                f"建议仓位(半凯利) {_kp*100:.1f}%</span>"
                if _kp is not None else ""
            )
            st.markdown(
                f"<div style='padding:6px 0;line-height:1.3'>"
                f"<span style='font-size:11px;color:#2D3F55;font-weight:700'>#{rank}</span>"
                f"<span style='font-size:17px;font-weight:800;color:#E2E8F0;"
                f"margin-left:7px'>{row['ticker']}</span>{review_badge}<br>"
                f"<span style='font-size:10px;color:#8B9BB4'>{row.get('category','')}</span>"
                f"<span style='background:{r_color}22;color:{r_color};"
                f"font-size:9px;padding:1px 6px;border-radius:3px;"
                f"border:1px solid {r_color}44;margin-left:6px'>{row['rating']}</span>"
                f"{_kp_html}"
                f"</div>",
                unsafe_allow_html=True,
            )

        with col_bars:
            bar_html = "<div style='padding:7px 0'>"
            for bcol, blabel, bcolor in SCORE_COLS:
                v = row[bcol]
                bar_html += (
                    f"<div style='display:flex;align-items:center;gap:6px;margin-bottom:2px'>"
                    f"<span style='width:34px;font-size:9px;color:#8B9BB4;text-align:right'>{blabel}</span>"
                    f"<div style='flex:1;background:#1E2D3D;border-radius:3px;height:10px;overflow:hidden'>"
                    f"<div style='width:{v}%;height:100%;background:{bcolor};border-radius:3px'></div></div>"
                    f"<span style='width:24px;font-size:10px;color:{bcolor};text-align:right'>{v:.0f}</span>"
                    f"</div>"
                )
            bar_html += "</div>"
            st.markdown(bar_html, unsafe_allow_html=True)

        with col_score:
            st.markdown(
                f"<div style='text-align:center;padding:6px 0'>"
                f"<div style='font-size:34px;font-weight:900;color:{fs_col};line-height:1'>{fs:.0f}</div>"
                f"<div style='font-size:9px;color:#8B9BB4'>/100</div>"
                f"<div style='font-size:9px;color:#FF4B6E;margin-top:1px'>-{rp:.1f}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown(
            "<div style='height:1px;background:#0D1822;margin:0'></div>",
            unsafe_allow_html=True,
        )

    # ── 分布图 ─────────────────────────────────────────
    st.divider()
    st.markdown("#### 分类型评分分布")

    _unique_cats = df_view["category"].unique().tolist()
    if not _unique_cats:
        st.info("无匹配股票")
    else:
        cat_cols = st.columns(len(_unique_cats))
        for i, (cat, grp) in enumerate(df_view.groupby("category")):
            with cat_cols[i % len(cat_cols)]:
                st.markdown(f"<div style='color:#8B9BB4;font-size:11px;"
                            f"text-transform:uppercase;letter-spacing:1px;"
                            f"margin-bottom:8px'>{cat}</div>",
                            unsafe_allow_html=True)
                for _, r in grp.iterrows():
                    c = score_color(r["final_score"])
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;"
                        f"margin-bottom:4px;font-size:13px'>"
                        f"<span style='color:#E2E8F0'>{r['ticker']}</span>"
                        f"<span style='color:{c};font-weight:600'>"
                        f"{r['final_score']:.1f}</span></div>",
                        unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 页面 2：单股详情
# ══════════════════════════════════════════════════════════
elif page == "🔍 单股详情":
    st.markdown("## 单股详情")

    ticker = st.selectbox(
        "选择股票",
        df["ticker"].tolist(),
        format_func=lambda t: f"{t}  —  {df[df['ticker']==t]['rating'].values[0]}",
    )

    row  = df[df["ticker"] == ticker].iloc[0]
    # 使用合并后的数据（若已刷新则含实时字段）
    data = get_active_stocks().get(ticker, {})
    cat  = get_category(ticker)
    fs   = row["final_score"]
    r_color = rating_color(row["rating"])
    fs_color = score_color(fs)

    # validation metadata from the row
    _val_conf  = row.get("validation_confidence", 0)
    _hr_needed = str(row.get("human_review_required", "FALSE")).upper() == "TRUE"
    _val_status = row.get("validation_status", "PASS")
    # 用户已在数据编辑页核对过 → 清除核查标记
    _tk_ov_detail = st.session_state.user_overrides.get(ticker, {})
    if any(isinstance(v, dict) and v.get("status") == "verified" for v in _tk_ov_detail.values()):
        _hr_needed = False

    # ── 头部 ───────────────────────────────────────────
    h1, h2, h3 = st.columns([3, 2, 2])

    with h1:
        company = row.get("company", ticker)
        val_badge = ""
        if _hr_needed:
            val_badge = ("<span style='background:#FFB34722;color:#FFB347;"
                         "font-size:10px;padding:2px 6px;border-radius:3px;"
                         "border:1px solid #FFB34744;margin-left:6px'>🔍待核查</span>")
        conf_pct = f"{_val_conf*100:.0f}%" if _val_conf else "—"
        st.markdown(
            f"<div style='padding:16px;background:#0F1923;"
            f"border:1px solid #1E2D3D;border-radius:8px'>"
            f"<div style='font-size:28px;font-weight:800;"
            f"color:#E2E8F0'>{ticker}{val_badge}</div>"
            f"<div style='color:#8B9BB4;font-size:13px;"
            f"margin-top:2px'>{company}</div>"
            f"<div style='margin-top:10px;display:flex;gap:16px'>"
            f"<div><div style='color:#8B9BB4;font-size:10px;"
            f"text-transform:uppercase'>板块</div>"
            f"<div style='font-size:14px;font-weight:600;"
            f"color:#8B9BB4;padding-top:3px'>{row.get('category','—')}</div></div>"
            f"<div><div style='color:#8B9BB4;font-size:10px;"
            f"text-transform:uppercase'>类型</div>"
            f"<div style='font-size:14px;font-weight:600;"
            f"color:#8B9BB4;padding-top:3px'>{cat.value}</div></div>"
            f"</div>"
            f"<div style='color:#4FC3F7;font-size:10px;margin-top:6px'>"
            f"📄 results_validated.csv · 验证置信度 {conf_pct}"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True)

    with h2:
        st.markdown(
            f"<div style='padding:16px;background:#0F1923;"
            f"border:1px solid #1E2D3D;border-radius:8px;"
            f"text-align:center;height:100%'>"
            f"<div style='color:#8B9BB4;font-size:11px;"
            f"text-transform:uppercase;letter-spacing:1px'>综合评分</div>"
            f"<div style='font-size:56px;font-weight:900;"
            f"color:{fs_color};line-height:1.1;margin:4px 0'>{fs:.0f}</div>"
            f"<div style='color:#8B9BB4;font-size:11px'>/ 100</div>"
            f"<div style='margin-top:8px'>"
            f"<span style='background:{r_color}22;color:{r_color};"
            f"font-size:12px;padding:3px 12px;border-radius:4px;"
            f"border:1px solid {r_color}44'>{row['rating']}</span>"
            f"</div></div>",
            unsafe_allow_html=True)

    with h3:
        rp = row["risk_penalty"]
        rs = row["raw_score"]
        st.markdown(
            f"<div style='padding:16px;background:#0F1923;"
            f"border:1px solid #1E2D3D;border-radius:8px'>"
            f"<div style='color:#8B9BB4;font-size:11px;"
            f"text-transform:uppercase;letter-spacing:1px;margin-bottom:10px'>分数构成</div>"
            f"<div style='display:flex;justify-content:space-between;"
            f"margin-bottom:6px'>"
            f"<span style='color:#8B9BB4;font-size:12px'>加权合计</span>"
            f"<span style='color:#E2E8F0;font-weight:600'>{rs:.1f}</span></div>"
            f"<div style='display:flex;justify-content:space-between;"
            f"margin-bottom:6px'>"
            f"<span style='color:#FF4B6E;font-size:12px'>风险扣分</span>"
            f"<span style='color:#FF4B6E;font-weight:600'>-{rp:.2f}</span></div>"
            f"<div style='height:1px;background:#1E2D3D;margin:8px 0'></div>"
            f"<div style='display:flex;justify-content:space-between'>"
            f"<span style='color:#E2E8F0;font-size:13px;font-weight:600'>"
            f"Final Score</span>"
            f"<span style='color:{fs_color};font-size:16px;"
            f"font-weight:800'>{fs:.1f}</span></div>"
            f"<div style='height:1px;background:#1E2D3D;margin:8px 0'></div>"
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;margin-top:3px'>"
            f"<span style='color:#8B9BB4;font-size:11px'>数据质量</span>"
            f"<span style='color:#FF8C42;font-size:12px;font-weight:700'>"
            f"{row.get('confidence_grade','?')} · {row.get('estimated_fields','-')} 项估算</span></div>"
            f"</div>",
            unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # ── 半凯利建议仓位 ───────────────────────────────────
    _kp     = suggested_position_pct(fs)
    _kmeta  = kelly_meta()
    _kdet   = band_detail(fs)
    if _kp is not None and _kdet:
        with st.expander(f"💰 建议仓位（半凯利）：{_kp*100:.1f}%", expanded=False):
            kc1, kc2, kc3, kc4 = st.columns(4)
            kc1.metric("所属分档", _kdet.get("rating_label", "—"))
            kc2.metric("历史胜率(近似)", f"{_kdet.get('win_rate', 0)*100:.1f}%")
            kc3.metric("赔率(avg_win/avg_loss)", f"{_kdet.get('payoff_ratio', '—')}")
            kc4.metric("样本数", _kdet.get("sample_size", "—"))
            st.caption(
                f"回测生成于 {_kmeta.get('generated_at','—')} · {_kmeta.get('method','')}"
            )
            st.warning(_kmeta.get("caveat", ""), icon="⚠️")
    elif _kp is None:
        st.caption("💰 半凯利建议仓位：该分档样本不足或尚无回测数据，暂不显示")

    # ── 雷达 + 子分条形 ─────────────────────────────────
    rc1, rc2 = st.columns([1, 1])
    with rc1:
        st.markdown("#### 能力雷达图")
        st.plotly_chart(make_radar(row), use_container_width=True)
    with rc2:
        st.markdown("#### 子分详情")
        st.plotly_chart(make_score_bar_chart(row), use_container_width=True)

        # 权重说明
        w = WEIGHT_CONFIG[cat]
        weight_html = (
            f"<div style='background:#0A1628;border:1px solid #1E2D3D;"
            f"border-radius:6px;padding:10px 12px;font-size:11px;"
            f"color:#8B9BB4;line-height:2'>"
            f"<span style='color:#4FC3F7'>估值×{w.valuation:.0%}</span> · "
            f"<span style='color:#00D4AA'>成长×{w.growth:.0%}</span> · "
            f"<span style='color:#A78BFA'>质量×{w.quality:.0%}</span> · "
            f"<span style='color:#FFB347'>AI暴露×{w.ai_exposure:.0%}</span> · "
            f"<span style='color:#F472B6'>预期差×{w.expectation_gap:.0%}</span>"
            f"</div>"
        )
        st.markdown(weight_html, unsafe_allow_html=True)

    st.divider()

    # ── 关键指标面板 ─────────────────────────────────────
    st.markdown("#### 关键财务指标")
    m1, m2, m3, m4, m5, m6 = st.columns(6)

    _rev_g  = data.get("revenue_growth_yoy") or data.get("rev_growth")
    _fcfm   = data.get("fcf_margin")
    _r40    = ((_rev_g or 0) + (_fcfm or 0)) * 100
    metrics = [
        (m1, "PEG",        data.get("peg_ratio"),    "{:.2f}x", row["valuation_score"]),
        (m2, "EV/Sales",   data.get("ev_sales"),     "{:.1f}x", row["valuation_score"]),
        (m3, "收入增长YoY",  _rev_g,                  "{:.0%}",  row["growth_score"]),
        (m4, "FCF Margin", _fcfm,                    "{:.0%}",  row["quality_score"]),
        (m5, "毛利率",      data.get("gross_margin"), "{:.0%}",  row["quality_score"]),
        (m6, "Rule of 40", _r40,                     "{:.0f}",  row["quality_score"]),
    ]
    for col, label, val, fmt, score in metrics:
        with col:
            val_str = fmt.format(val) if val is not None else "N/A"
            c = score_color(score)
            st.markdown(
                f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
                f"border-radius:6px;padding:12px;text-align:center'>"
                f"<div style='color:#8B9BB4;font-size:10px;"
                f"text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px'>"
                f"{label}</div>"
                f"<div style='font-size:20px;font-weight:700;"
                f"color:{c}'>{val_str}</div>"
                f"</div>",
                unsafe_allow_html=True)

    st.divider()

    # ── 投资分析文字 ─────────────────────────────────────
    st.markdown("#### 投资分析摘要")

    rev_g  = data.get("revenue_growth_yoy") or data.get("rev_growth") or 0
    fcfm   = data.get("fcf_margin") or 0
    peg    = data.get("peg_ratio") or 99
    r40    = (rev_g + fcfm) * 100
    beta   = data.get("beta") or 1
    ai_score_val = row.get("ai_exposure_score", 0) or 0

    # 估值判断（白话版，讲清楚"为什么"而不只是甩数字）
    if row["valuation_score"] >= 65:
        val_text = (
            f"**估值合理** — PEG {peg:.2f}x，意味着现在的股价相对它的成长速度来说不算贵，"
            f"市场还没有把未来的好消息都提前透支。买在这个位置，就算后面成长只是符合预期、"
            f"没有超预期，估值本身也不至于成为拖累股价的因素——安全边际是靠"
            f"「便宜」换来的，不是靠「猜对未来」换来的。"
        )
    elif row["valuation_score"] >= 45:
        val_text = (
            f"**估值偏贵** — PEG {peg:.2f}x，说明现在这个价格已经把不少乐观预期计入进去了。"
            f"这不代表股票不能再涨，但意味着接下来每个季度的增长都得原样兑现，"
            f"一旦增长稍微放缓、或者一次指引不及预期，估值倍数很容易被重新定价，"
            f"下修的空间比想象中大。"
        )
    else:
        val_text = (
            f"**估值极高** — PEG {peg:.2f}x，远超合理区间，说明当前股价背后隐含的增长假设"
            f"已经相当激进（甚至可能比历史上任何同类公司都要乐观）。这种情况下，"
            f"公司需要连续多年都交出「超预期」的成绩单才能配得上这个价格；"
            f"只要有一次不及预期，市场很可能会用大幅回调来「重新定价」这份乐观。"
        )

    # 成长判断
    if row["growth_score"] >= 75:
        grow_text = (
            f"**成长强劲** — 收入增长 {rev_g:.0%}，明显超出行业平均水平。这种量级的增长"
            f"通常意味着公司要么处在一条渗透率快速提升的曲线上，要么刚拿下了新的大客户/新市场，"
            f"成长动能短期内看不到明显减速的迹象——但也要留意，越快的增长基数效应会越快显现，"
            f"几个季度后同比增速大概率会自然收敛，这不代表基本面变差，而是数学规律。"
        )
    elif row["growth_score"] >= 50:
        grow_text = (
            f"**成长稳健** — 收入增长 {rev_g:.0%}，符合市场预期，没有明显加速也没有明显失速。"
            f"这种「平稳」本身是把双刃剑：好处是可预测性强，业绩意外的概率较低；"
            f"坏处是如果市场已经按「加速」的故事给了估值溢价，稳健增长反而可能被解读为「不及预期」。"
        )
    else:
        grow_text = (
            f"**成长趋缓** — 收入增长 {rev_g:.0%}，低于同类优质公司水平。需要进一步分辨这是"
            f"行业整体降温（大家都慢，不是它的问题），还是这家公司自己开始丢份额/丢定价权"
            f"（结构性问题，需要重新审视护城河）——这两种情况对应的操作完全不同。"
        )

    # 质量判断
    if r40 >= 60:
        qual_text = (
            f"**质量出色** — Rule of 40 = {r40:.0f}（营收增速+FCF利润率，FCF口径），"
            f"属于同类顶级水平。这说明公司不是在「烧钱换增长」，而是增长和赚钱能力可以兼得——"
            f"这种公司即使短期估值偏贵，长期靠内生现金流也能不断消化掉估值压力。"
        )
    elif r40 >= 40:
        qual_text = (
            f"**质量合格** — Rule of 40 = {r40:.0f}，超过40这条经验基准线，"
            f"说明增长和盈利能力之间取得了基本平衡，没有明显偏科，但也谈不上极致优秀，"
            f"是一份「过得去」的成绩单。"
        )
    else:
        qual_text = (
            f"**质量待改善** — Rule of 40 = {r40:.0f}，低于40基准，说明增长和盈利能力"
            f"至少有一项在拖后腿——要么是增长不够快，要么是在增长的同时利润率/现金流"
            f"没有跟上，需要进一步拆解到底是哪一项出了问题。"
        )

    # AI暴露（用评分代理）
    if ai_score_val >= 75:
        ai_text = (
            f"**AI核心资产** — AI暴露评分 {ai_score_val:.0f}，是真正意义上的AI受益者，"
            f"而不是蹭热点的边缘参与者。这类公司的营收/利润里有实打实的一块直接挂钩AI需求，"
            f"AI产业的景气度会直接体现在它的财报里，而不只是体现在股价的「故事溢价」里。"
        )
    elif ai_score_val >= 50:
        ai_text = (
            f"**AI中等暴露** — AI暴露评分 {ai_score_val:.0f}，能够受益于AI浪潮，"
            f"但还称不上核心标的——AI相关业务对总盘子的贡献还不够大，"
            f"股价里如果已经计入了很高的AI预期，需要留意这部分预期是否走在了实际业务进展的前面。"
        )
    else:
        ai_text = (
            f"**AI暴露有限** — AI暴露评分 {ai_score_val:.0f}，AI叙事对这家公司的支撑力度不足。"
            f"如果股价近期因为「蹭上了AI概念」而明显上涨，这部分涨幅缺乏基本面支撑，"
            f"一旦市场情绪降温，这类公司回吐涨幅的风险相对更高。"
        )

    # 风险
    if row["risk_penalty"] >= 10:
        risk_text = (
            f"**高风险** — Beta {beta:.2f}，风险扣分 {row['risk_penalty']:.1f}。"
            f"这个beta水平意味着大盘每跌1%，这只股票历史上平均要跌超过1%，"
            f"波动会明显放大你的账户净值曲线——仓位大小要按这个波动率倒推着控制，"
            f"而不是按「我有多看好它」来决定。"
        )
    elif row["risk_penalty"] >= 6:
        risk_text = (
            f"**中等风险** — Beta {beta:.2f}，风险扣分 {row['risk_penalty']:.1f}，"
            f"是正常成长股该有的风险水平——涨的时候比大盘猛，跌的时候也会比大盘狠，"
            f"属于「愿赌服输」的正常波动区间，不算异常。"
        )
    else:
        risk_text = (
            f"**低风险** — Beta {beta:.2f}，风险扣分仅 {row['risk_penalty']:.1f}，"
            f"防御性相对较强，大盘大跌时这只股票historically抗跌能力更好，"
            f"适合作为组合里相对稳的那一部分仓位。"
        )

    col_a, col_b = st.columns(2)
    with col_a:
        st.info(f"📐 {val_text}")
        if row["growth_score"] >= 75:
            st.success(f"📈 {grow_text}")
        elif row["growth_score"] >= 50:
            st.warning(f"📈 {grow_text}")
        else:
            st.error(f"📈 {grow_text}")
        st.info(f"🏆 {qual_text}")
    with col_b:
        st.info(f"🤖 {ai_text}")
        if row["risk_penalty"] >= 10:
            st.error(f"⚡ {risk_text}")
        elif row["risk_penalty"] >= 6:
            st.warning(f"⚡ {risk_text}")
        else:
            st.success(f"⚡ {risk_text}")

    st.divider()

    # ── Damodaran 估值纪律分析 ────────────────────────────────
    st.markdown("#### 📐 Damodaran 估值纪律分析")

    dam = calc_damodaran_report(ticker, data, cat)

    _excess_pct  = dam["excess_return"] * 100
    _wacc_pct    = dam["wacc"] * 100
    _ke_pct      = dam["cost_of_equity"] * 100
    _roic_disp   = safe_val(data.get("roic"), 0.15) * 100

    # 颜色辅助
    def _excess_color(e):
        return "#00D4AA" if e >= 5 else ("#FFB347" if e >= -2 else "#FF4B6E")

    # 行 1：资本成本模块
    dc1, dc2, dc3, dc4 = st.columns(4)
    with dc1:
        st.markdown(
            f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
            f"border-radius:6px;padding:14px;text-align:center'>"
            f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
            f"letter-spacing:0.8px;margin-bottom:4px'>Beta (β)</div>"
            f"<div style='font-size:22px;font-weight:700;color:#E2E8F0'>{dam['beta']:.2f}</div>"
            f"</div>", unsafe_allow_html=True)
    with dc2:
        st.markdown(
            f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
            f"border-radius:6px;padding:14px;text-align:center'>"
            f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
            f"letter-spacing:0.8px;margin-bottom:4px'>权益成本 ke</div>"
            f"<div style='font-size:22px;font-weight:700;color:#E2E8F0'>{_ke_pct:.1f}%</div>"
            f"<div style='color:#8B9BB4;font-size:10px;margin-top:2px'>"
            f"4.3% + {dam['beta']:.2f}β × 4.8%ERP</div>"
            f"</div>", unsafe_allow_html=True)
    with dc3:
        st.markdown(
            f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
            f"border-radius:6px;padding:14px;text-align:center'>"
            f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
            f"letter-spacing:0.8px;margin-bottom:4px'>WACC</div>"
            f"<div style='font-size:22px;font-weight:700;color:#E2E8F0'>{_wacc_pct:.1f}%</div>"
            f"<div style='color:#8B9BB4;font-size:10px;margin-top:2px'>加权资本成本</div>"
            f"</div>", unsafe_allow_html=True)
    with dc4:
        _ec = _excess_color(_excess_pct)
        st.markdown(
            f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
            f"border-radius:6px;padding:14px;text-align:center'>"
            f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
            f"letter-spacing:0.8px;margin-bottom:4px'>ROIC − WACC 超额回报</div>"
            f"<div style='font-size:22px;font-weight:700;color:{_ec}'>{_excess_pct:+.1f}%</div>"
            f"<div style='color:{_ec};font-size:11px;margin-top:2px'>{dam['quality_verdict']}</div>"
            f"</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # 行 2：市场隐含叙事
    impl = dam.get("implied_rev_cagr_5y")
    ev_c = dam.get("ev_sales_current")
    ev_t = dam.get("target_ev_sales")
    fwd  = dam.get("analyst_fwd_growth", 0)

    if impl is not None:
        dc5, dc6, dc7, dc8 = st.columns(4)
        with dc5:
            st.markdown(
                f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
                f"border-radius:6px;padding:14px;text-align:center'>"
                f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
                f"letter-spacing:0.8px;margin-bottom:4px'>当前 EV/Sales</div>"
                f"<div style='font-size:22px;font-weight:700;color:#E2E8F0'>"
                f"{f'{ev_c:.1f}x' if ev_c else 'N/A'}</div>"
                f"</div>", unsafe_allow_html=True)
        with dc6:
            st.markdown(
                f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
                f"border-radius:6px;padding:14px;text-align:center'>"
                f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
                f"letter-spacing:0.8px;margin-bottom:4px'>成熟期目标 EV/Sales</div>"
                f"<div style='font-size:22px;font-weight:700;color:#8B9BB4'>{ev_t:.1f}x</div>"
                f"<div style='color:#8B9BB4;font-size:10px;margin-top:2px'>"
                f"{cat.value} 成熟基准</div>"
                f"</div>", unsafe_allow_html=True)
        with dc7:
            _ic = "#FF8C42" if impl > 0.5 else ("#FFB347" if impl > 0.25 else "#E2E8F0")
            st.markdown(
                f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
                f"border-radius:6px;padding:14px;text-align:center'>"
                f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
                f"letter-spacing:0.8px;margin-bottom:4px'>市场隐含5年CAGR</div>"
                f"<div style='font-size:22px;font-weight:700;color:{_ic}'>"
                f"{impl*100:.1f}%</div>"
                f"<div style='color:#8B9BB4;font-size:10px;margin-top:2px'>市场要求的年均增速</div>"
                f"</div>", unsafe_allow_html=True)
        with dc8:
            _nc_map = {"✅": "#00D4AA", "⚖️": "#4FC3F7", "⚠️": "#FFB347", "🔴": "#FF4B6E", "📉": "#FF4B6E"}
            _nc_emoji = dam["narrative_consistency"][:2] if dam["narrative_consistency"] else "—"
            _nc_color = next((v for k, v in _nc_map.items() if _nc_emoji.startswith(k)), "#8B9BB4")
            st.markdown(
                f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
                f"border-radius:6px;padding:14px;text-align:center'>"
                f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
                f"letter-spacing:0.8px;margin-bottom:4px'>分析师NTM增长 vs 市场要求</div>"
                f"<div style='font-size:18px;font-weight:700;color:#E2E8F0'>"
                f"{fwd*100:.1f}% vs {impl*100:.1f}%</div>"
                f"<div style='color:{_nc_color};font-size:11px;margin-top:3px'>"
                f"{dam['narrative_consistency']}</div>"
                f"</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # 叙事一致性可视化横条
        _ratio = fwd / impl if impl > 0 else 0
        _bar_pct = min(int(_ratio * 80), 100)
        _bar_color = "#00D4AA" if _ratio >= 1.2 else ("#4FC3F7" if _ratio >= 0.85 else ("#FFB347" if _ratio >= 0.6 else "#FF4B6E"))
        st.markdown(
            f"<div style='background:#0A1628;border:1px solid #1E2D3D;"
            f"border-radius:6px;padding:12px 16px'>"
            f"<div style='font-size:11px;color:#8B9BB4;margin-bottom:6px'>"
            f"叙事覆盖率（分析师预测 / 市场隐含要求）= {_ratio:.2f}x</div>"
            f"<div style='background:#1E2D3D;border-radius:4px;height:8px;overflow:hidden'>"
            f"<div style='background:{_bar_color};height:8px;width:{_bar_pct}%;border-radius:4px'></div>"
            f"</div><div style='display:flex;justify-content:space-between;margin-top:4px;"
            f"font-size:10px;color:#8B9BB4'>"
            f"<span>0×（叙事崩溃）</span><span style='color:#FFB347'>0.85×（匹配）</span>"
            f"<span style='color:#00D4AA'>1.2×（领先）</span></div>"
            f"</div>",
            unsafe_allow_html=True)
    else:
        st.caption("_市场隐含 CAGR 需要 EV/Sales 与 revenue_ttm 数据，当前暂无。_")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # 行 3：再投资效率
    ri  = dam.get("reinvestment_rate")
    sg  = dam.get("sustainable_growth")
    _ri_method = dam.get("reinvestment_method", "estimate")
    if ri is not None:
        dc9, dc10, dc11 = st.columns(3)
        with dc9:
            st.markdown(
                f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
                f"border-radius:6px;padding:14px;text-align:center'>"
                f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
                f"letter-spacing:0.8px;margin-bottom:4px'>ROIC</div>"
                f"<div style='font-size:22px;font-weight:700;color:#A78BFA'>{_roic_disp:.1f}%</div>"
                f"</div>", unsafe_allow_html=True)
        with dc10:
            _ri_label = {"proper": "|CapEx|+R&D−D&A / NOPAT",
                         "capex_only": "|CapEx|−D&A / NOPAT",
                         "estimate": "估算：营收增速 / ROIC"}.get(_ri_method, "—")
            st.markdown(
                f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
                f"border-radius:6px;padding:14px;text-align:center'>"
                f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
                f"letter-spacing:0.8px;margin-bottom:4px'>再投资率</div>"
                f"<div style='font-size:22px;font-weight:700;color:#E2E8F0'>{ri*100:.0f}%</div>"
                f"<div style='color:#8B9BB4;font-size:10px;margin-top:2px'>"
                f"{_ri_label}</div>"
                f"</div>", unsafe_allow_html=True)
        with dc11:
            _sg_c = "#00D4AA" if sg and sg > 0.30 else "#E2E8F0"
            _sg_str = f"{sg*100:.0f}%" if sg else "N/A"
            st.markdown(
                f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
                f"border-radius:6px;padding:14px;text-align:center'>"
                f"<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
                f"letter-spacing:0.8px;margin-bottom:4px'>可持续增长率</div>"
                f"<div style='font-size:22px;font-weight:700;color:{_sg_c}'>{_sg_str}</div>"
                f"<div style='color:#8B9BB4;font-size:10px;margin-top:2px'>"
                f"ROIC × 再投资率</div>"
                f"</div>", unsafe_allow_html=True)

    st.markdown(
        "<div style='background:#0A1628;border:1px solid #1E2D3D;border-left:3px solid #4FC3F7;"
        "border-radius:4px;padding:10px 14px;font-size:11px;color:#8B9BB4;margin-top:8px'>"
        "📌 <b style='color:#4FC3F7'>游戏类型说明</b>：ENERGREX 使用<b>定价游戏</b>（EV倍数比较），"
        "非 DCF 内在价值计算。Damodaran 分析为补充视角，"
        "用于检验市场隐含叙事是否与基本面叙事一致。"
        "市场隐含 CAGR 基于「成熟期 EV/Sales 回归」假设，仅供参考。"
        "</div>",
        unsafe_allow_html=True)

    # ── 白话解读：上面这堆数字到底什么意思 ─────────────────────
    if _excess_pct >= 5:
        _excess_text = (
            f"ROIC 比 WACC 高出 {_excess_pct:.1f} 个百分点——说明这家公司每往生意里"
            f"多投一块钱资本，创造出来的回报明显超过了这块钱本身的资金成本。这是「良性增长」："
            f"公司规模扩张得越多，创造的价值就越多，增长是在给股东「加分」，不是在「占用」资本。"
        )
    elif _excess_pct >= -2:
        _excess_text = (
            f"ROIC 和 WACC 基本打平（超额回报仅 {_excess_pct:+.1f}%），意味着新增投入的资本"
            f"回报率跟资金成本差不多——继续扩张规模对股东来说大致是「不赚不赔」的中性操作。"
            f"这种情况下，估值里如果给了很高的成长溢价，需要打个问号：增长本身不创造额外价值，"
            f"支撑高估值的理由就得来自别的地方（比如护城河变宽、议价权提升），而不是单纯做大规模。"
        )
    else:
        _excess_text = (
            f"ROIC 比 WACC 还低 {abs(_excess_pct):.1f} 个百分点——说明现在这盘生意每多投入一块钱"
            f"资本，实际上创造的回报覆盖不了资金成本，某种程度上是在「价值毁灭」。继续追加投资"
            f"扩大规模，账面上营收/资产可能还在涨，但对股东价值反而是拖累，这种增长「越多越差」。"
        )

    _ri_sg_text = ""
    if ri is not None and sg is not None:
        _ri_sg_text = (
            f"再投资率 {ri*100:.0f}% 意味着公司把赚到的 NOPAT（税后经营利润）里这么大比例"
            f"重新投回业务扩张，而不是分给股东。ROIC × 再投资率 算出来的「可持续增长率」是 "
            f"{sg*100:.0f}%——这是不依赖额外融资、光靠自己造血就能撑住的内生增长速度上限。"
        )
        if fwd:
            if fwd > sg * 1.2:
                _ri_sg_text += (
                    f"而市场/分析师预期的增速是 {fwd*100:.1f}%，比这个可持续增长率高出一大截，"
                    f"说明要实现这个增长预期，公司大概率需要额外融资、并购，或者利润率/资本效率"
                    f"进一步提升——不是靠现在这套资本回报和再投资节奏「自然」就能达到的，"
                    f"这部分差距是需要重点盯的风险点。"
                )
            elif fwd < sg * 0.8:
                _ri_sg_text += (
                    f"而市场/分析师预期的增速是 {fwd*100:.1f}%，反而低于这个可持续增长率，"
                    f"说明预期偏保守——公司现有的资本回报和再投资效率，理论上能支撑比市场"
                    f"预期更快的增长，这种「预期低于实际能力」的情况，往往是超预期的潜在来源。"
                )
            else:
                _ri_sg_text += (
                    f"而市场/分析师预期的增速是 {fwd*100:.1f}%，跟这个可持续增长率大致匹配，"
                    f"说明当前的增长预期靠现有的资本回报效率就能自然达到，不需要额外的"
                    f"融资或效率提升假设，这部分风险相对可控。"
                )

    st.markdown(
        "<div style='background:#0F1923;border:1px solid #1E2D3D;border-radius:6px;"
        "padding:14px 16px;margin-top:8px'>"
        "<div style='color:#8B9BB4;font-size:10px;text-transform:uppercase;"
        "letter-spacing:0.8px;margin-bottom:8px'>🗣️ 解读</div>"
        f"<div style='color:#E2E8F0;font-size:13px;line-height:1.7'>{_excess_text}"
        + (f"<br><br>{_ri_sg_text}" if _ri_sg_text else "") +
        "</div></div>",
        unsafe_allow_html=True)

    st.divider()

    # ── 多维智库视角（8 位投资人框架，页面直接算出的白话判断）────────
    st.markdown("#### 🧠 多维智库视角")
    st.caption(
        "把 8 位投资人的思维框架套在这只股票的数据上，直接算出方向性白话判断——"
        "⚠️ 规则化的框架提示，不是这些投资人的真实观点，供人工复核。"
    )

    _lenses = all_investor_lenses(ticker, data, cat, {
        "valuation":      float(row["valuation_score"]),
        "growth":         float(row["growth_score"]),
        "quality":        float(row["quality_score"]),
        "ai_exposure":    float(row.get("ai_exposure_score") or 0),
        "expectation_gap":float(row.get("expectation_gap_score") or 0),
        "momentum":       float(row.get("momentum_score") or 0),
        "risk_penalty":   float(row["risk_penalty"]),
        "final":          float(row["final_score"]),
    })

    # 两列卡片布局
    _lc = st.columns(2)
    for _i, _L in enumerate(_lenses):
        with _lc[_i % 2]:
            _c = _L["verdict_color"]
            st.markdown(
                f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
                f"border-left:3px solid {_c};border-radius:6px;"
                f"padding:14px 16px;margin-bottom:12px;min-height:220px'>"
                f"<div style='display:flex;justify-content:space-between;"
                f"align-items:baseline;margin-bottom:2px'>"
                f"<span style='font-size:15px;font-weight:700;color:#E2E8F0'>"
                f"{_L['icon']} {_L['name']}</span>"
                f"<span style='font-size:9px;color:#8B9BB4;text-transform:uppercase;"
                f"letter-spacing:0.5px'>{_L['dimension']}</span></div>"
                f"<div style='font-size:10px;color:#8B9BB4;margin-bottom:8px'>"
                f"{_L['framework']}</div>"
                f"<div style='display:inline-block;background:{_c}22;color:{_c};"
                f"font-size:12px;font-weight:600;padding:3px 10px;border-radius:4px;"
                f"border:1px solid {_c}44;margin-bottom:8px'>{_L['verdict']}</div>"
                f"<div style='color:#C7D2E0;font-size:12px;line-height:1.65'>"
                f"{_L['paragraph']}</div>"
                f"</div>",
                unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 页面 3：对比分析
# ══════════════════════════════════════════════════════════
elif page == "⚖️ 对比分析":
    st.markdown("## 对比分析")

    tickers_all = df["ticker"].tolist()
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        t1 = st.selectbox("股票 A", tickers_all, index=0)
    with col_s2:
        t2 = st.selectbox("股票 B", tickers_all, index=4)

    if t1 == t2:
        st.warning("请选择两只不同的股票")
        st.stop()

    r1 = df[df["ticker"] == t1].iloc[0]
    r2 = df[df["ticker"] == t2].iloc[0]
    _as = get_active_stocks()
    d1 = _as[t1]
    d2 = _as[t2]

    # ── 头部对比 ────────────────────────────────────────
    cmp1, cmp_mid, cmp2 = st.columns([5, 1, 5])

    def render_compare_card(row, data, side="left"):
        fs = row["final_score"]
        c  = score_color(fs)
        rc = rating_color(row["rating"])
        return (
            f"<div style='background:#0F1923;border:1px solid #1E2D3D;"
            f"border-radius:8px;padding:16px;text-align:{'right' if side=='right' else 'left'}'>"
            f"<div style='font-size:26px;font-weight:800;color:#E2E8F0'>"
            f"{row['ticker']}</div>"
            f"<div style='color:#8B9BB4;font-size:12px;margin-top:2px'>"
            f"{row['category']}</div>"
            f"<div style='font-size:42px;font-weight:900;color:{c};"
            f"line-height:1.1;margin:8px 0'>{fs:.0f}</div>"
            f"<div style='color:#8B9BB4;font-size:11px'>/ 100</div>"
            f"<div style='margin-top:8px'>"
            f"<span style='background:{rc}22;color:{rc};font-size:11px;"
            f"padding:2px 10px;border-radius:3px;border:1px solid {rc}44'>"
            f"{row['rating']}</span></div></div>"
        )

    with cmp1:
        st.markdown(render_compare_card(r1, d1, "left"), unsafe_allow_html=True)
    with cmp_mid:
        st.markdown(
            "<div style='text-align:center;padding-top:40px;"
            "font-size:20px;color:#2D3F55'>VS</div>",
            unsafe_allow_html=True)
    with cmp2:
        st.markdown(render_compare_card(r2, d2, "right"), unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── 子分雷达叠加 ────────────────────────────────────
    cats_radar = ["估值", "成长", "质量", "AI暴露", "预期差"]
    v1 = [r1["valuation_score"], r1["growth_score"], r1["quality_score"],
          r1["ai_exposure_score"], r1["expectation_gap_score"]]
    v2 = [r2["valuation_score"], r2["growth_score"], r2["quality_score"],
          r2["ai_exposure_score"], r2["expectation_gap_score"]]

    fig_radar = go.Figure()
    fig_radar.add_trace(go.Scatterpolar(
        r=v1+[v1[0]], theta=cats_radar+[cats_radar[0]],
        fill="toself", name=t1,
        fillcolor="rgba(0,212,170,0.12)",
        line=dict(color="#00D4AA", width=2),
        marker=dict(size=6, color="#00D4AA"),
    ))
    fig_radar.add_trace(go.Scatterpolar(
        r=v2+[v2[0]], theta=cats_radar+[cats_radar[0]],
        fill="toself", name=t2,
        fillcolor="rgba(255,179,71,0.12)",
        line=dict(color="#FFB347", width=2),
        marker=dict(size=6, color="#FFB347"),
    ))
    fig_radar.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0,100],
                            gridcolor="#1E2D3D", tickcolor="#1E2D3D",
                            tickfont=dict(color="#8B9BB4", size=10)),
            angularaxis=dict(gridcolor="#1E2D3D",
                             tickfont=dict(color="#E2E8F0", size=13)),
            bgcolor="#0A1628",
        ),
        paper_bgcolor="#0A1628",
        plot_bgcolor="#0A1628",
        legend=dict(font=dict(color="#E2E8F0"), bgcolor="#0A1628"),
        margin=dict(l=50, r=50, t=20, b=20),
        height=320,
    )

    ra, rb = st.columns([3, 2])
    with ra:
        st.markdown("#### 雷达叠加对比")
        st.plotly_chart(fig_radar, use_container_width=True)

    with rb:
        st.markdown("#### 子分胜负")
        score_items = [
            ("估值分",   "valuation_score"),
            ("成长分",   "growth_score"),
            ("质量分",   "quality_score"),
            ("AI暴露分", "ai_exposure_score"),
            ("预期差分", "expectation_gap_score"),
            ("风险扣分", "risk_penalty"),
            ("最终分",   "final_score"),
        ]
        header = (
            f"<div style='display:flex;justify-content:space-between;"
            f"color:#8B9BB4;font-size:10px;text-transform:uppercase;"
            f"letter-spacing:0.8px;margin-bottom:8px;padding:0 4px'>"
            f"<span>指标</span><span>{t1}</span>"
            f"<span style='color:#4B5563'>//</span><span>{t2}</span></div>"
        )
        st.markdown(header, unsafe_allow_html=True)

        for label, col in score_items:
            v_1 = r1[col]
            v_2 = r2[col]
            # 风险扣分：越小越好
            better_1 = (v_1 > v_2) if col != "risk_penalty" else (v_1 < v_2)
            c1_str = "#00D4AA" if better_1 else "#8B9BB4"
            c2_str = "#00D4AA" if not better_1 else "#8B9BB4"
            win = "←" if better_1 else "→"
            win_color = "#00D4AA"
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;"
                f"align-items:center;padding:5px 4px;"
                f"border-bottom:1px solid #1E2D3D;font-size:13px'>"
                f"<span style='color:#8B9BB4;width:70px'>{label}</span>"
                f"<span style='color:{c1_str};font-weight:600;width:40px;"
                f"text-align:right'>{v_1:.1f}</span>"
                f"<span style='color:{win_color};width:20px;text-align:center'>{win}</span>"
                f"<span style='color:{c2_str};font-weight:600;width:40px'>{v_2:.1f}</span>"
                f"</div>",
                unsafe_allow_html=True)

    # ── 关键指标对比表 ───────────────────────────────────
    st.divider()

    compare_metrics = [
        ("PEG",           "peg_ratio",                      "{:.2f}x", False),
        ("EV/EBITDA",     "ev_ebitda",                      "{:.1f}x", False),
        ("EV/Sales",      "ev_sales",                       "{:.1f}x", False),
        ("收入增长YoY",    "revenue_growth_yoy",             "{:.0%}",  True),
        ("EPS增长YoY",    "eps_growth_yoy",                 "{:.0%}",  True),
        ("毛利率",         "gross_margin",                   "{:.1%}",  True),
        ("FCF Margin",    "fcf_margin",                     "{:.1%}",  True),
        ("ROIC",          "roic",                           "{:.1%}",  True),
        ("AI收入占比",     "ai_revenue_exposure_pct",        "{:.0%}",  True),
        ("数据中心暴露",   "datacenter_exposure_pct",        "{:.0%}",  True),
        ("Beta",          "beta",                           "{:.2f}",  False),
        ("最大回撤1年",    "max_drawdown_1y",                "{:.0%}",  False),
    ]

    tbl_html = (
        f"<table style='width:100%;border-collapse:collapse;font-size:13px'>"
        f"<tr style='background:#0F1923'>"
        f"<th style='padding:8px 12px;text-align:left;color:#8B9BB4;"
        f"font-size:11px;text-transform:uppercase;border-bottom:1px solid #1E2D3D'>"
        f"指标</th>"
        f"<th style='padding:8px 12px;text-align:right;color:#00D4AA;"
        f"font-size:11px;text-transform:uppercase;border-bottom:1px solid #1E2D3D'>"
        f"{t1}</th>"
        f"<th style='padding:8px 12px;text-align:right;color:#FFB347;"
        f"font-size:11px;text-transform:uppercase;border-bottom:1px solid #1E2D3D'>"
        f"{t2}</th>"
        f"<th style='padding:8px 12px;text-align:center;color:#8B9BB4;"
        f"font-size:11px;text-transform:uppercase;border-bottom:1px solid #1E2D3D'>"
        f"优势</th></tr>"
    )

    for label, field, fmt, higher_better in compare_metrics:
        val1 = d1.get(field)
        val2 = d2.get(field)
        s1 = fmt.format(val1) if val1 is not None else "N/A"
        s2 = fmt.format(val2) if val2 is not None else "N/A"

        if val1 is not None and val2 is not None:
            t1_wins = (val1 > val2) if higher_better else (val1 < val2)
            c1 = "#00D4AA" if t1_wins else "#E2E8F0"
            c2 = "#FFB347" if not t1_wins else "#E2E8F0"
            winner = f"<span style='color:#00D4AA'>{t1}</span>" if t1_wins \
                     else f"<span style='color:#FFB347'>{t2}</span>"
        else:
            c1 = c2 = "#8B9BB4"
            winner = "—"

        tbl_html += (
            f"<tr style='border-bottom:1px solid #0F1923'>"
            f"<td style='padding:7px 12px;color:#8B9BB4'>{label}</td>"
            f"<td style='padding:7px 12px;text-align:right;"
            f"color:{c1};font-weight:600'>{s1}</td>"
            f"<td style='padding:7px 12px;text-align:right;"
            f"color:{c2};font-weight:600'>{s2}</td>"
            f"<td style='padding:7px 12px;text-align:center;"
            f"font-size:11px'>{winner}</td></tr>"
        )
    tbl_html += "</table>"

    st.markdown(
        f"<style>@media print{{.metric-compare-block{{"
        f"break-inside:avoid;page-break-inside:avoid;}}}}</style>"
        f"<div class='metric-compare-block'>"
        f"<h4 style='margin:0 0 10px 0'>关键指标对照</h4>"
        f"<div style='background:#0A1628;border:1px solid #1E2D3D;"
        f"border-radius:8px;overflow:hidden'>{tbl_html}</div>"
        f"</div>",
        unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 页面 4：评分审计
# ══════════════════════════════════════════════════════════
elif page == "🔬 评分审计":
    st.markdown("## 评分审计 — 逐行核对")
    st.markdown(
        "<div style='color:#8B9BB4;font-size:13px;margin-bottom:16px'>"
        "每一个分数的原始输入值、计算公式、中间结果都在这里，可以逐行核对。"
        "</div>",
        unsafe_allow_html=True)

    audit_ticker = st.selectbox("选择股票", df["ticker"].tolist(), key="audit_sel")
    row  = df[df["ticker"] == audit_ticker].iloc[0]
    data = get_active_stocks()[audit_ticker]
    cat  = get_category(audit_ticker)

    def audit_section(title, color="#4FC3F7"):
        st.markdown(
            f"<div style='background:#0A1628;border-left:3px solid {color};"
            f"padding:10px 14px;border-radius:0 6px 6px 0;margin:12px 0 8px'>"
            f"<span style='color:{color};font-size:13px;font-weight:600'>"
            f"{title}</span></div>",
            unsafe_allow_html=True)

    def audit_row(label, raw, formula, result, note="", is_manual=False):
        manual_tag = (" <span style='background:#FF8C4222;color:#FF8C42;"
                      "font-size:10px;padding:1px 6px;border-radius:3px;"
                      "border:1px solid #FF8C4244'>手动</span>") if is_manual else ""
        note_html = (f"<div style='color:#8B9BB4;font-size:11px;"
                     f"margin-top:2px'>{note}</div>") if note else ""
        result_color = score_color(result) if isinstance(result, float) and result <= 100 else "#E2E8F0"
        st.markdown(
            f"<div style='display:grid;grid-template-columns:180px 90px 1fr 60px;"
            f"gap:12px;align-items:start;padding:6px 0;"
            f"border-bottom:1px solid #1E2D3D;font-size:12px'>"
            f"<div style='color:#E2E8F0'>{label}{manual_tag}"
            f"{note_html}</div>"
            f"<div style='color:#FFB347;font-family:monospace'>{raw}</div>"
            f"<div style='color:#8B9BB4;font-family:monospace;font-size:11px'>"
            f"{formula}</div>"
            f"<div style='color:{result_color};font-weight:700;"
            f"text-align:right'>{result if isinstance(result,str) else f'{result:.1f}'}</div>"
            f"</div>",
            unsafe_allow_html=True)

    def linear_show(v, worst, best, label=""):
        if v is None: return "N/A", 50.0
        numer = v - worst
        denom = best - worst
        raw   = numer / denom * 100 if denom else 50
        clamped = max(0, min(100, raw))
        formula = f"({v:.4f}-{worst})÷({best}-{worst})×100={raw:.1f}→{clamped:.1f}"
        return formula, clamped

    def inverse_show(v, best, worst):
        if v is None: return "N/A", 50.0
        numer = worst - v
        denom = worst - best
        raw   = numer / denom * 100 if denom else 50
        clamped = max(0, min(100, raw))
        formula = f"({worst}-{v:.3f})÷({worst}-{best})×100={raw:.1f}→{clamped:.1f}"
        return formula, clamped

    # ── ① 估值分 ────────────────────────────────────────
    # 权重与 scoring_engine.calc_valuation_score 保持一致（含 ERG 单位修正 + Forward PE）
    audit_section("① 估值分  →  " + str(row["valuation_score"]), "#4FC3F7")
    peg       = data.get("peg_ratio", 2.0) or 2.0
    ev_ebitda = data.get("ev_ebitda", 30.0) or 30.0
    ev_sales  = data.get("ev_sales", 15.0) or 15.0
    rev_g     = data.get("revenue_growth_yoy", 0.20) or 0.20
    rev_g_pct = max(rev_g * 100, 1.0)        # 转为 %形式（ERG 单位修正）
    fpe       = data.get("forward_pe", 50.0) or 50.0
    fcfy      = data.get("fcf_yield", 0.01) or 0.01

    f1, s1 = inverse_show(peg,      best=0.5,  worst=2.5)
    # EV/EBITDA 阈值：芯片/设备用 15~55；软件/安全用 20~80
    if cat.value in ("AI芯片", "半导体设备"):
        f2, s2 = inverse_show(ev_ebitda, best=15.0, worst=55.0)
    else:
        f2, s2 = inverse_show(ev_ebitda, best=20.0, worst=80.0)

    # ERG/EV/Revenue：芯片和软件用 ERG（÷增长率%），安全和设备用原始 EV/Rev
    if cat.value == "AI软件/SaaS":
        erg = ev_sales / rev_g_pct
        f3, s3 = inverse_show(erg, best=0.10, worst=0.60)
        audit_row("ERG (EV/Rev÷RevG%)", f"{erg:.3f}", f3, s3,
                  f"={ev_sales}÷{rev_g_pct:.1f}%  best=0.10, worst=0.60")
    elif cat.value == "AI芯片":
        erg = ev_sales / rev_g_pct
        f3, s3 = inverse_show(erg, best=0.15, worst=0.80)
        audit_row("ERG (EV/Rev÷RevG%)", f"{erg:.3f}", f3, s3,
                  f"={ev_sales}÷{rev_g_pct:.1f}%  best=0.15, worst=0.80")
    elif cat.value == "网络安全":
        f3, s3 = inverse_show(ev_sales, best=5.0, worst=25.0)
        audit_row("EV/Revenue", f"{ev_sales}x", f3, s3, "best=5.0, worst=25.0")
    else:  # SEMI_EQUIP
        f3, s3 = inverse_show(ev_sales, best=6.0, worst=20.0)
        audit_row("EV/Revenue", f"{ev_sales}x", f3, s3, "best=6.0, worst=20.0")

    f5, s5 = inverse_show(fpe,  best=20.0, worst=80.0)
    f4, s4 = linear_show(fcfy,  worst=0.0,  best=0.05)

    audit_row("PEG Ratio",   f"{peg}x",       f1, s1, "best=0.5, worst=2.5")
    audit_row("EV/EBITDA",   f"{ev_ebitda}x", f2, s2)
    audit_row("Forward PE",  f"{fpe:.1f}x",   f5, s5, "best=20, worst=80（机构绝对估值锚）")
    audit_row("FCF Yield",   f"{fcfy:.3f}",   f4, s4)

    # 权重与 scoring_engine 一致（各类 5 项之和 = 1.00）
    weights_v = {
        "AI芯片":      (0.25, 0.35, 0.15, 0.10, 0.15),  # peg evebitda erg fcf fpe
        "AI软件/SaaS": (0.15, 0.10, 0.50, 0.15, 0.10),
        "网络安全":     (0.20, 0.15, 0.40, 0.15, 0.10),
        "半导体设备":   (0.25, 0.40, 0.15, 0.10, 0.10),
    }
    wv = weights_v.get(cat.value, (0.20, 0.20, 0.20, 0.20, 0.20))
    total_v = s1*wv[0] + s2*wv[1] + s3*wv[2] + s4*wv[3] + s5*wv[4]
    st.markdown(
        f"<div style='color:#4FC3F7;font-size:12px;padding:6px 0;"
        f"font-family:monospace'>"
        f"PEG {s1:.1f}×{wv[0]} + EV/EBITDA {s2:.1f}×{wv[1]} + "
        f"ERG/EV/Rev {s3:.1f}×{wv[2]} + FCF {s4:.1f}×{wv[3]} + "
        f"FPE {s5:.1f}×{wv[4]} = <strong>{total_v:.1f}</strong></div>",
        unsafe_allow_html=True)

    # ── ② 成长分 ────────────────────────────────────────
    audit_section("② 成长分  →  " + str(row["growth_score"]), "#00D4AA")
    rg  = data.get("revenue_growth_yoy", 0.10) or 0.10
    eg  = data.get("eps_growth_yoy", 0.10) or 0.10
    fg  = data.get("fcf_growth_yoy", 0.10) or 0.10
    fwd = data.get("next_year_revenue_growth_est", rg) or rg
    r30 = data.get("analyst_revision_30d", 0.0) or 0.0

    worst_r = {"AI芯片":0.10,"AI软件/SaaS":0.10,"网络安全":0.10,"半导体设备":-0.10}
    best_r  = {"AI芯片":0.70,"AI软件/SaaS":0.50,"网络安全":0.35,"半导体设备":0.30}
    fr, sr = linear_show(rg, worst=worst_r[cat.value], best=best_r[cat.value])
    fe, se = linear_show(eg, worst=0.0, best=0.60)
    ff, sf = linear_show(fg, worst=0.0, best=0.50)
    fw, sw = linear_show(fwd,worst=0.05,best=0.50)
    f30,s30= linear_show(r30,worst=-0.50,best=0.50)

    audit_row("收入增长YoY", f"{rg:.0%}", fr, sr, f"worst={worst_r[cat.value]:.0%}, best={best_r[cat.value]:.0%}")
    audit_row("EPS增长YoY",  f"{eg:.0%}", fe, se, "worst=0%, best=60%")
    audit_row("FCF增长YoY",  f"{fg:.0%}", ff, sf, "worst=0%, best=50%")
    audit_row("NTM收入预期",  f"{fwd:.0%}",fw, sw, "worst=5%, best=50%")
    audit_row("分析师上调30d",f"{r30:.0%}",f30,s30,"worst=-50%, best=+50%")
    tg = sr*0.35+se*0.20+sf*0.15+sw*0.20+s30*0.10
    st.markdown(
        f"<div style='color:#00D4AA;font-size:12px;padding:6px 0;font-family:monospace'>"
        f"{sr:.1f}×0.35 + {se:.1f}×0.20 + {sf:.1f}×0.15 + {sw:.1f}×0.20 + {s30:.1f}×0.10"
        f" = <strong>{tg:.1f}</strong></div>",
        unsafe_allow_html=True)

    # ── ③ 质量分 ────────────────────────────────────────
    audit_section("③ 质量分  →  " + str(row["quality_score"]), "#A78BFA")
    gm   = data.get("gross_margin", 0.60) or 0.60
    fcfm = data.get("fcf_margin", 0.10) or 0.10
    roic = data.get("roic", 0.15) or 0.15
    de   = data.get("debt_to_equity", 0.5) or 0.5
    nrr  = data.get("net_revenue_retention", 1.10) or 1.10
    r40  = (rg + fcfm) * 100

    gm_ranges = {"AI芯片":(0.40,0.70),"AI软件/SaaS":(0.60,0.85),
                 "网络安全":(0.65,0.85),"半导体设备":(0.35,0.58)}
    r40_ranges = {"AI芯片":(10,50),"AI软件/SaaS":(20,70),
                  "网络安全":(20,70),"半导体设备":(10,50)}
    gmw, gmb = gm_ranges[cat.value]
    r40w, r40b = r40_ranges[cat.value]

    fgm,  sgm  = linear_show(gm,   worst=gmw,  best=gmb)
    ffcfm,sfcfm= linear_show(fcfm, worst=0.0,  best=0.40)
    fr40, sr40 = linear_show(r40,  worst=r40w, best=r40b)
    froic,sroic= linear_show(roic, worst=0.05, best=0.45)
    fde,  sde  = inverse_show(de,  best=0.0,   worst=2.0)

    st.markdown(f"<div style='color:#8B9BB4;font-size:11px;margin-bottom:4px'>"
                f"Rule of 40 = {rg:.0%} + {fcfm:.0%} = {r40:.1f}</div>",
                unsafe_allow_html=True)
    audit_row("毛利率",      f"{gm:.1%}",  fgm,   sgm,   f"worst={gmw:.0%}, best={gmb:.0%}")
    audit_row("FCF Margin",  f"{fcfm:.1%}",ffcfm, sfcfm)
    audit_row("Rule of 40",  f"{r40:.1f}", fr40,  sr40)
    audit_row("ROIC",        f"{roic:.1%}",froic, sroic)
    audit_row("D/E（越低越好）",f"{de:.2f}", fde,   sde)
    if cat.value in ("AI软件/SaaS", "网络安全"):
        fnrr, snrr = linear_show(nrr, worst=0.90, best=1.35)
        audit_row("NRR净收入留存", f"{nrr:.2f}", fnrr, snrr, "worst=90%, best=135%")

    # ── ④ AI暴露分 ────────────────────────────────────────
    audit_section("④ AI暴露分  →  " + str(row["ai_exposure_score"]) +
                  "  ⚠️ 手动字段", "#FFB347")
    ai_fields = [
        ("AI收入占比",   "ai_revenue_exposure_pct",    0.10, 0.85),
        ("AI增长贡献",   "ai_growth_contribution_pct", 0.10, 0.80),
        ("AI利润占比",   "ai_profit_exposure_pct",     0.10, 0.85),
        ("数据中心",     "datacenter_exposure_pct",    0.25, 0.85),
        ("先进封装",     "advanced_packaging_exposure_pct", 0.05, 0.60),
        ("AI软件平台",   "software_ai_platform_exposure_pct",0.10,0.70),
        ("AI积压",       "ai_order_backlog_exposure",  0.05, 0.60),
        ("网安AI功能",   "cybersecurity_ai_exposure_pct",0.05,0.60),
    ]
    for label, field, worst, best in ai_fields:
        val = data.get(field)
        if val is not None:
            f_, s_ = linear_show(val, worst=worst, best=best)
            audit_row(label, f"{val:.0%}", f_, s_, is_manual=True)

    # ── ⑤ 预期差分 ────────────────────────────────────────
    audit_section("⑤ 预期差分  →  " + str(row["expectation_gap_score"]), "#F472B6")
    rb  = data.get("actual_revenue_vs_consensus", 0.0) or 0.0
    eb  = data.get("actual_eps_vs_consensus",     0.0) or 0.0
    gb  = data.get("guidance_vs_consensus",       0.0) or 0.0
    rct = data.get("earnings_reaction_score",     0.0) or 0.0
    me  = data.get("market_expectation_score",    0.5) or 0.5

    frb, srb = linear_show(rb,  worst=-0.08, best=0.10)
    feb, seb = linear_show(eb,  worst=-0.10, best=0.15)
    fgb, sgb = linear_show(gb,  worst=-0.10, best=0.12)
    frt, srt = linear_show(rct, worst=-0.15, best=0.20)
    fme, sme = linear_show(1-me,worst=0.0,   best=1.0)

    audit_row("收入Beat率",   f"{rb:.1%}",  frb, srb)
    audit_row("EPS Beat率",   f"{eb:.1%}",  feb, seb)
    audit_row("指引Beat率",   f"{gb:.1%}",  fgb, sgb)
    audit_row("财报次日涨跌",  f"{rct:.1%}", frt, srt)
    audit_row("预期高低(反转)",f"1-{me:.2f}={1-me:.2f}", fme, sme,
              "market_expectation_score越高越扣分")

    # ── ⑥ 风险扣分 ────────────────────────────────────────
    audit_section("⑥ 风险扣分  →  -" + str(row["risk_penalty"]) +
                  "  (上限20)", "#FF4B6E")
    beta  = data.get("beta",             1.2) or 1.2
    vol   = data.get("volatility_30d",   0.40) or 0.40
    valr  = data.get("valuation_risk",   0.5) or 0.5
    conc  = data.get("concentration_risk",0.3) or 0.3
    liq   = data.get("liquidity_risk",   0.2) or 0.2
    mdd   = data.get("max_drawdown_1y",  0.30) or 0.30

    fb,  sb  = linear_show(beta, worst=0.5,  best=2.5)
    fv,  sv  = linear_show(vol,  worst=0.20, best=0.90)
    fdd, sdd = linear_show(mdd,  worst=0.10, best=0.70)

    audit_row("Beta → 风险组件", f"{beta:.2f}",
              f"score={sb:.1f}/100={sb/100:.4f}组件", sb/100)
    audit_row("波动率30d → 组件",f"{vol:.0%}",
              f"score={sv:.1f}/100={sv/100:.4f}组件", sv/100)
    audit_row("估值风险(手动0~1)",f"{valr:.2f}",
              "直接使用", valr*100, is_manual=True)
    audit_row("集中度+流动性均值", f"({conc:.2f}+{liq:.2f})/2",
              f"={((conc+liq)/2):.4f}", ((conc+liq)/2)*100, is_manual=True)
    audit_row("最大回撤1年 → 组件",f"{mdd:.0%}",
              f"score={sdd:.1f}/100={sdd/100:.4f}组件", sdd/100)

    raw_p = (sb/100)*0.25 + (sv/100)*0.20 + valr*0.25 + ((conc+liq)/2)*0.15 + (sdd/100)*0.15
    penalty = min(raw_p * 20, 20)
    st.markdown(
        f"<div style='color:#FF4B6E;font-size:12px;padding:8px 0;font-family:monospace'>"
        f"raw = {sb/100:.4f}×0.25 + {sv/100:.4f}×0.20 + {valr:.4f}×0.25 + "
        f"{(conc+liq)/2:.4f}×0.15 + {sdd/100:.4f}×0.15 = {raw_p:.4f}<br>"
        f"penalty = {raw_p:.4f} × 20 = <strong>{penalty:.2f}</strong></div>",
        unsafe_allow_html=True)

    # ── ⑦ 最终汇总 ────────────────────────────────────────
    audit_section("⑦ 最终分汇总", "#E2E8F0")
    w = WEIGHT_CONFIG[cat]
    vs, gs, qs, ai, eg = (row["valuation_score"], row["growth_score"],
                          row["quality_score"], row["ai_exposure_score"],
                          row["expectation_gap_score"])
    rp = row["risk_penalty"]
    raw = vs*w.valuation + gs*w.growth + qs*w.quality + ai*w.ai_exposure + eg*w.expectation_gap
    final = max(0, min(100, raw - rp))

    summary_rows = [
        (f"估值 × {w.valuation:.0%}",   vs, vs*w.valuation),
        (f"成长 × {w.growth:.0%}",      gs, gs*w.growth),
        (f"质量 × {w.quality:.0%}",     qs, qs*w.quality),
        (f"AI暴露 × {w.ai_exposure:.0%}",ai, ai*w.ai_exposure),
        (f"预期差 × {w.expectation_gap:.0%}",eg, eg*w.expectation_gap),
    ]
    for label, score, weighted in summary_rows:
        st.markdown(
            f"<div style='display:flex;justify-content:space-between;"
            f"padding:4px 0;border-bottom:1px solid #1E2D3D;font-size:13px'>"
            f"<span style='color:#8B9BB4'>{label}</span>"
            f"<span style='color:#E2E8F0'>{score:.1f}</span>"
            f"<span style='color:#E2E8F0'>= {weighted:.2f}</span></div>",
            unsafe_allow_html=True)

    st.markdown(
        f"<div style='display:flex;justify-content:space-between;"
        f"padding:6px 0;border-bottom:1px solid #1E2D3D;font-size:13px'>"
        f"<span style='color:#FF4B6E'>风险扣分</span>"
        f"<span style='color:#FF4B6E'>-{rp:.2f}</span></div>",
        unsafe_allow_html=True)

    fs_color2 = score_color(final)
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:center;padding:12px 0;margin-top:4px'>"
        f"<span style='color:#E2E8F0;font-size:15px;font-weight:600'>FINAL SCORE</span>"
        f"<span style='color:{fs_color2};font-size:32px;font-weight:900'>{final:.1f}</span>"
        f"</div>",
        unsafe_allow_html=True)

    rating_map = {"⭐ Strong Buy":65,"✅ Buy":55,"👀 Watch":45,"⚠️ Expensive":35}
    for r_label, threshold in rating_map.items():
        if final >= threshold:
            rc = rating_color(r_label)
            st.markdown(
                f"<div style='background:{rc}22;border:1px solid {rc}44;"
                f"border-radius:6px;padding:10px 14px;color:{rc};"
                f"font-size:14px;font-weight:600;text-align:center'>"
                f"{r_label}</div>",
                unsafe_allow_html=True)
            break


# ══════════════════════════════════════════════════════════
# 页面 5：数据管理中心（三档状态标注 + 核对工作流）
# ══════════════════════════════════════════════════════════
elif page == "📝 数据编辑":
    if "_save_success" in st.session_state:
        st.success(st.session_state.pop("_save_success"))

    # ── 加载验证报告 ────────────────────────────────────
    _VAL_RPT: dict = {}
    _val_rpt_path = _ROOT / "data" / "data_validation_report.json"
    if _val_rpt_path.exists():
        try:
            _VAL_RPT = json.loads(_val_rpt_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    _has_rpt = bool(_VAL_RPT)

    # ── 状态样式常量 ─────────────────────────────────────
    # key → (icon, color, label)
    _ST = {
        "verified_auto":   ("✅", "#00D4AA", "yfinance已验证"),
        "verified_user":   ("✅", "#00D4AA", "已核对"),
        "pending":         ("🟡", "#FFB347", "待审核"),
        "estimated":       ("✍️", "#8B9BB4", "估算值"),
        "not_applicable":  ("⬜", "#4A5568", "不适用"),
    }

    # 各公司类型的不适用字段（权重=0，填任何值都不影响评分）
    _NA_FIELDS_BY_CAT: dict[str, set] = {
        "AI芯片":     {"net_revenue_retention", "arr_growth_yoy", "revenue_predictability_score"},
        "半导体设备": {"net_revenue_retention", "arr_growth_yoy", "revenue_predictability_score"},
        "AI软件/SaaS": {"arr_growth_yoy"},
        "大型科技":   {"arr_growth_yoy"},
        "网络安全":   set(),
    }

    # ── override 格式辅助（兼容旧版纯数字格式）───────────
    def _ov_entry(raw) -> dict:
        if isinstance(raw, dict) and "value" in raw:
            return raw
        if raw is not None:
            return {"value": raw, "status": "pending", "verified_at": None, "source": ""}
        return {}

    def _ov_val(ov_dict: dict, field: str):
        e = _ov_entry(ov_dict.get(field))
        return e.get("value")

    def _ov_meta(ov_dict: dict, field: str) -> dict:
        return _ov_entry(ov_dict.get(field))

    # ── yfinance 报告查询 ─────────────────────────────────
    def _auto_yf_status(ticker: str, field: str) -> str | None:
        """返回 'ok'/'warn'/'error'/'na' 或 None（无报告）。"""
        ticker_data = _VAL_RPT.get("results", {}).get(ticker, {})
        for entry in ticker_data.get("auto_validate", []):
            if entry["field"] == field:
                s = entry.get("status", "")
                if "✅" in s:   return "ok"
                if "🟡" in s:   return "warn"
                if "🔴" in s:   return "error"
                return "na"
        return None

    def _field_eff_status(field: str, default_status: str,
                          existing: dict, ticker: str) -> str:
        """决定字段最终显示状态。"""
        if field in _na_fields:
            return "not_applicable"
        meta = _ov_meta(existing, field)
        if meta.get("status") == "verified":
            return "verified_user"
        if default_status == "estimated":
            return "estimated"
        if default_status == "auto":
            astat = _auto_yf_status(ticker, field)
            return "verified_auto" if astat == "ok" else "pending"
        return "pending"

    def _review_url(url_type: str, ticker: str) -> str:
        return {
            "financials": f"https://finance.yahoo.com/quote/{ticker}/financials",
            "sec": (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                    f"&CIK={ticker}&type=10-Q&dateb=&owner=include&count=5"),
            "key-stats": f"https://finance.yahoo.com/quote/{ticker}/key-statistics",
        }.get(url_type, "#")

    # ── 字段定义（含状态元数据）─────────────────────────
    # 每行: (label, field, fmt, vmin, vmax, step, help,
    #        default_status["auto"|"pending"|"estimated"], source_label, url_type)
    _FG = {
        "市场估值": [
            ("Forward PE",        "forward_pe",        "%.2f",  0.0,  300.0, 0.1,  "NTM Forward PE",                    "auto",      "yfinance",  "key-stats"),
            ("PEG Ratio",         "peg_ratio",         "%.3f",  0.0,   10.0, 0.01, "⚠️ 口径敏感：1yr/5yr可差3x",        "pending",   "key-stats", "key-stats"),
            ("EV/EBITDA",         "ev_ebitda",         "%.1f",  0.0,  300.0, 0.1,  "",                                  "auto",      "yfinance",  "key-stats"),
            ("EV/Revenue",        "ev_sales",          "%.2f",  0.0,  200.0, 0.1,  "",                                  "auto",      "yfinance",  "key-stats"),
        ],
        "成长指标": [
            ("收入增长 YoY",       "revenue_growth_yoy",  "%.4f", -0.5,  5.0, 0.001, "最近单季度 YoY",                  "auto",      "yfinance",  "financials"),
            ("EPS 增长 YoY",       "eps_growth_yoy",      "%.4f", -1.0, 20.0, 0.001, "Non-GAAP diluted EPS YoY",       "auto",      "yfinance",  "financials"),
            ("FCF 增长 YoY",       "fcf_growth_yoy",      "%.4f", -1.0, 20.0, 0.001, "同期季度对比",                    "pending",   "财报",      "financials"),
            ("NTM 收入增长预期",   "next_year_revenue_growth_est","%.4f",-0.5,5.0,0.001,"⚠️ 需卖方共识(Bloomberg/FactSet)","pending","卖方共识",   "key-stats"),
            ("分析师上调 30d",     "analyst_revision_30d","%.4f", -1.0,  1.0, 0.001, "正=上调 负=下调",                 "pending",   "卖方共识",  "key-stats"),
        ],
        "质量指标": [
            ("毛利率",             "gross_margin",       "%.4f",  0.0,  1.0, 0.001, "GAAP产品毛利率",                   "auto",      "yfinance",  "financials"),
            ("运营利润率",         "operating_margin",   "%.4f", -0.5,  1.0, 0.001, "⚠️ 需Non-GAAP，yfinance=GAAP",   "pending",   "财报",      "sec"),
            ("FCF Margin",         "fcf_margin",         "%.4f", -0.5,  1.0, 0.001, "FCF/Revenue（TTM）",               "auto",      "yfinance",  "financials"),
            ("ROIC",               "roic",               "%.4f",  0.0,  2.0, 0.001, "Non-GAAP NOPAT / 投入资本",       "pending",   "财报",      "sec"),
            ("D/E（含可转债）",    "debt_to_equity",     "%.3f",  0.0, 30.0, 0.001, "⚠️ yfinance不含converts，必须手动","pending",   "财报",      "sec"),
            ("NRR 净收入留存率",   "net_revenue_retention","%.3f",0.5,  2.0, 0.001, "SaaS/安全公司财报直接披露",        "pending",   "财报",      "sec"),
            ("ARR 增长 YoY",       "arr_growth_yoy",     "%.4f", -0.5,  3.0, 0.001, "订阅收入增长",                    "pending",   "财报",      "financials"),
        ],
        "AI暴露（估算）": [
            ("AI收入暴露 %",       "ai_revenue_exposure_pct",          "%.3f", 0.0, 1.0, 0.001, "芯片用DataCenter代理", "estimated", "估算", None),
            ("AI利润暴露 %",       "ai_profit_exposure_pct",           "%.3f", 0.0, 1.0, 0.001, "卖方模型估算",         "estimated", "估算", None),
            ("AI成长贡献 %",       "ai_growth_contribution_pct",       "%.3f", 0.0, 1.0, 0.001, "",                    "estimated", "估算", None),
            ("AI平台暴露（软件）", "software_ai_platform_exposure_pct","%.3f", 0.0, 1.0, 0.001, "",                    "estimated", "估算", None),
            ("先进封装暴露",       "advanced_packaging_exposure_pct",  "%.3f", 0.0, 1.0, 0.001, "供应链估算，非财报",   "estimated", "估算", None),
            ("网安AI暴露",         "cybersecurity_ai_exposure_pct",    "%.3f", 0.0, 1.0, 0.001, "占ARR比估算",          "estimated", "估算", None),
        ],
        "预期差": [
            ("实际收入 vs 预期",   "actual_revenue_vs_consensus",  "%.4f",-0.3,0.3, 0.001,"正=beat；⚠️ 当前Mock",      "pending",   "卖方共识", "key-stats"),
            ("实际EPS vs 预期",    "actual_eps_vs_consensus",      "%.4f",-0.5,0.5, 0.001,"⚠️ 当前Mock",               "pending",   "卖方共识", "key-stats"),
            ("指引 vs 预期",       "guidance_vs_consensus",        "%.4f",-0.5,0.5, 0.001,"⚠️ 当前Mock",               "pending",   "卖方共识", "key-stats"),
            ("市场预期评分 (0-1)", "market_expectation_score",     "%.3f", 0.0,1.0, 0.001,"主观评估：分析师情绪",       "estimated", "估算",     None),
        ],
        "风险评估（主观）": [
            ("估值风险 (0=便宜,1=极贵)","valuation_risk",          "%.3f", 0.0,1.0, 0.001,"",                           "estimated", "估算", None),
            ("集中度风险",             "concentration_risk",       "%.3f", 0.0,1.0, 0.001,"",                           "estimated", "估算", None),
            ("流动性风险",             "liquidity_risk",           "%.3f", 0.0,1.0, 0.001,"",                           "estimated", "估算", None),
            ("收入可预测性",           "revenue_predictability_score","%.3f",0.0,1.0,0.001,"1=完全ARR",                 "estimated", "估算", None),
        ],
    }

    # ── 股票选择器 ───────────────────────────────────────
    ed_ticker = st.selectbox("选择股票", TICKERS, key="edit_ticker")
    active    = get_active_stocks().get(ed_ticker, {})
    existing  = st.session_state.user_overrides.get(ed_ticker, {})

    _ed_cat    = get_category(ed_ticker).value          # e.g. "AI芯片"
    _na_fields = _NA_FIELDS_BY_CAT.get(_ed_cat, set())  # fields irrelevant for this category

    # ── 顶部状态仪表盘 ───────────────────────────────────
    _n_verified_user = sum(
        1 for v in existing.values()
        if _ov_entry(v).get("status") == "verified"
    )
    _n_verified_auto = sum(
        1 for grp in _FG.values()
        for _, fld, *_, ds, _, _ in grp
        if _field_eff_status(fld, ds, existing, ed_ticker) == "verified_auto"
    )
    _n_pending = sum(
        1 for grp in _FG.values()
        for _, fld, *_, ds, _, _ in grp
        if _field_eff_status(fld, ds, existing, ed_ticker) == "pending"
    )
    _last_verified = max(
        (_ov_entry(v).get("verified_at") or "" for v in existing.values()),
        default="—",
    ) or "—"

    _db1, _db2, _db3, _db4 = st.columns(4)
    _db1.metric("✅ 用户已核对", _n_verified_user)
    _db2.metric("✅ yfinance验证", _n_verified_auto,
                help="上次运行 cross_validate_data.py 结果" if _has_rpt else "尚无验证报告，请运行 cross_validate_data.py")
    _db3.metric("🟡 待审核", _n_pending)
    _db4.metric("📅 最近核对", _last_verified)

    if not _has_rpt:
        st.info("提示：运行 `python cross_validate_data.py` 生成验证报告，可自动标绿 yfinance 已验证字段。")
    st.divider()

    # ── 全局来源说明 ─────────────────────────────────────
    _source_note = st.text_input(
        "来源说明（勾选「已核对」并保存后附加到所有确认字段）",
        placeholder="例：Q1FY2027财报 Non-GAAP  /  Yahoo Finance 2026-06-15",
        key="edit_source_note",
    )

    # ── 字段编辑区 ───────────────────────────────────────
    new_values: dict    = {}   # field → new numeric value
    confirm_chk: dict   = {}   # field → bool

    for _grp_name, _grp_fields in _FG.items():
        # 统计组内 pending 数
        _g_pend = sum(
            1 for _, fld, *_, ds, _, _ in _grp_fields
            if _field_eff_status(fld, ds, existing, ed_ticker) == "pending"
        )
        _exp_title = (
            f"{_grp_name}  —  🟡 {_g_pend} 个待审核"
            if _g_pend else
            f"{_grp_name}  —  ✅ 全部已验证"
        )

        with st.expander(_exp_title, expanded=(_g_pend > 0)):
            # 表头
            _hh = st.columns([0.4, 2.8, 1.0, 1.4, 1.4, 1.0])
            for _ht in ["", "字段 / 来源", "当前值", "填入值", "核对链接", "已核对"]:
                _hh[["", "字段 / 来源", "当前值", "填入值", "核对链接", "已核对"].index(_ht)].markdown(
                    f"<span style='color:#4A5568;font-size:10px;text-transform:uppercase;"
                    f"letter-spacing:1px'>{_ht}</span>", unsafe_allow_html=True)
            st.markdown(
                "<div style='height:1px;background:#1E2D3D;margin:2px 0 6px'></div>",
                unsafe_allow_html=True)

            for _row in _grp_fields:
                label, field, fmt, vmin, vmax, step, helptext, dstat, src_label, url_type = _row
                fstat  = _field_eff_status(field, dstat, existing, ed_ticker)
                icon, color, _ = _ST[fstat]

                # ── 不适用字段：灰色单行展示，不渲染输入框 ──────
                if fstat == "not_applicable":
                    _na_rc = st.columns([0.4, 5.6, 2.0])
                    _na_rc[0].markdown(
                        f"<div style='text-align:center;padding-top:8px;"
                        f"font-size:13px;opacity:.4'>{icon}</div>",
                        unsafe_allow_html=True)
                    _na_rc[1].markdown(
                        f"<div style='padding:7px 0;font-size:11px;color:#4A5568'>"
                        f"{label}</div>",
                        unsafe_allow_html=True)
                    _na_rc[2].markdown(
                        f"<div style='padding:7px 0;font-size:10px;color:#4A5568'>"
                        f"⬜ 不适用（{_ed_cat}不参与评分）</div>",
                        unsafe_allow_html=True)
                    continue   # 跳过后续所有输入/复选框

                ov_val  = _ov_val(existing, field)
                cur_val = active.get(field)
                meta    = _ov_meta(existing, field)
                def_v   = float(ov_val) if ov_val is not None else (
                          float(cur_val) if cur_val is not None else float((vmin + vmax) / 2))

                _rc = st.columns([0.4, 2.8, 1.0, 1.4, 1.4, 1.0])

                # [0] 状态图标
                _rc[0].markdown(
                    f"<div style='text-align:center;padding-top:10px;"
                    f"font-size:15px;line-height:1'>{icon}</div>",
                    unsafe_allow_html=True)

                # [1] 字段名 + 来源徽章 + 核对时间
                _badge = (f"<span style='background:{color}1a;color:{color};"
                          f"font-size:8px;padding:1px 5px;border-radius:3px;"
                          f"border:1px solid {color}44;vertical-align:middle'>"
                          f"{src_label}</span>")
                _help  = (f"<div style='font-size:9px;color:#4A5568'>{helptext}</div>"
                          if helptext else "")
                if fstat == "verified_user" and meta.get("verified_at"):
                    _extra = (f"<div style='font-size:9px;color:#00D4AA'>"
                              f"核对于 {meta['verified_at']}"
                              f"{' · '+meta['source'] if meta.get('source') else ''}</div>")
                elif fstat == "verified_auto":
                    _extra = "<div style='font-size:9px;color:#00D4AA'>yfinance 自动验证</div>"
                else:
                    _extra = ""
                _rc[1].markdown(
                    f"<div style='padding:3px 0'>"
                    f"<span style='font-size:12px;color:#E2E8F0'>{label}</span> {_badge}"
                    f"{_help}{_extra}</div>",
                    unsafe_allow_html=True)

                # [2] 当前值
                _disp = ov_val if ov_val is not None else cur_val
                if _disp is not None:
                    try:   _ds = fmt % float(_disp)
                    except Exception: _ds = str(_disp)
                    _dc = "#4FC3F7" if ov_val is not None else "#8B9BB4"
                    _rc[2].markdown(
                        f"<div style='padding:9px 0;font-size:12px;color:{_dc}'>{_ds}</div>",
                        unsafe_allow_html=True)
                else:
                    _rc[2].markdown(
                        "<div style='padding:9px 0;font-size:12px;color:#2D3F55'>N/A</div>",
                        unsafe_allow_html=True)

                # [3] 数字输入框
                with _rc[3]:
                    val = st.number_input(
                        label, label_visibility="collapsed",
                        value=def_v,
                        min_value=float(vmin), max_value=float(vmax), step=float(step),
                        key=f"edit_{ed_ticker}_{field}",
                        format=fmt,
                    )
                    new_values[field] = val

                # [4] 核对链接（仅 pending 字段显示）
                if fstat == "pending" and url_type:
                    _link = _review_url(url_type, ed_ticker)
                    _rc[4].markdown(
                        f"<div style='padding:8px 0'>"
                        f"<a href='{_link}' target='_blank' "
                        f"style='color:#FFB347;font-size:11px;font-weight:600;"
                        f"text-decoration:none;background:#FFB34715;"
                        f"border:1px solid #FFB34744;border-radius:4px;"
                        f"padding:3px 10px;white-space:nowrap'>核对 →</a></div>",
                        unsafe_allow_html=True)
                elif fstat in ("verified_auto", "verified_user"):
                    _rc[4].markdown(
                        f"<div style='padding:9px 0;font-size:10px;color:#00D4AA'>核对完毕</div>",
                        unsafe_allow_html=True)

                # [5] 已核对复选框（pending 字段）或 状态注释
                with _rc[5]:
                    if fstat == "pending":
                        _already = meta.get("status") == "verified"
                        confirm_chk[field] = st.checkbox(
                            "chk", value=_already,
                            key=f"chk_{ed_ticker}_{field}",
                            label_visibility="collapsed",
                        )
                    elif fstat == "estimated":
                        st.markdown(
                            "<div style='font-size:9px;color:#4A5568;padding:10px 0'>估算</div>",
                            unsafe_allow_html=True)

    st.divider()

    # ── 操作按钮 ─────────────────────────────────────────
    _bc1, _bc2, _bc3 = st.columns([3.0, 2.2, 2.6])

    with _bc1:
        if st.button("✅ 确认核对并保存（自动重算评分）", type="primary", use_container_width=True):
            _today = datetime.date.today().isoformat()
            all_ov  = dict(st.session_state.user_overrides)
            tk_data = dict(all_ov.get(ed_ticker, {}))
            changed = 0

            for field, val in new_values.items():
                old_meta    = _ov_meta(tk_data, field)
                is_checked  = confirm_chk.get(field, False)
                in_overrides = field in tk_data

                ref_val = old_meta.get("value") if in_overrides else active.get(field)
                try:
                    val_changed = (ref_val is None) or (abs(float(ref_val) - val) > 1e-8)
                except Exception:
                    val_changed = True

                was_verified   = old_meta.get("status") == "verified"
                status_changed = is_checked != was_verified

                if not val_changed and not status_changed:
                    continue
                if not in_overrides and not val_changed and not is_checked:
                    continue

                tk_data[field] = {
                    "value":       val,
                    "status":      "verified" if is_checked else old_meta.get("status", "pending"),
                    "verified_at": _today if is_checked else old_meta.get("verified_at"),
                    "source":      (_source_note.strip() or old_meta.get("source", ""))
                                   if is_checked else old_meta.get("source", ""),
                }
                changed += 1

            if changed:
                all_ov[ed_ticker] = tk_data
                save_overrides(all_ov)
                st.session_state.user_overrides = all_ov
                _checked_n = sum(1 for f in confirm_chk if confirm_chk[f] and f in new_values)

                # 保存后立即重算评分
                import refresh_scores as _rs
                with st.spinner(f"⏳ 已保存 {changed} 个字段，正在重算 {ed_ticker} 评分…"):
                    try:
                        _rs.refresh_all(
                            tickers=[ed_ticker],
                            skip_momentum=True,
                            verbose=False,
                        )
                        st.cache_data.clear()
                        st.session_state._save_success = (
                            f"✅ 已保存 {ed_ticker} {changed} 个字段（{_checked_n} 个已核对）并重算评分"
                            + (f"  来源：{_source_note.strip()}" if _source_note.strip() else "")
                        )
                    except Exception as _re:
                        st.session_state._save_success = (
                            f"✅ 已保存 {ed_ticker} {changed} 个字段"
                            f"  ⚠️ 评分重算失败：{_re}"
                        )
                st.rerun()
            else:
                st.info("没有检测到修改，无需保存。")

    with _bc2:
        if existing and st.button("🗑 清除所有核对值", use_container_width=True):
            all_ov = dict(st.session_state.user_overrides)
            all_ov.pop(ed_ticker, None)
            save_overrides(all_ov)
            st.session_state.user_overrides = all_ov
            st.warning(f"已清除 {ed_ticker} 的所有核对值。")
            st.rerun()

    with _bc3:
        if existing:
            _sum_lines = []
            for k, v in existing.items():
                e = _ov_entry(v)
                try:
                    vstr = f"{float(e['value']):.4g}"
                except Exception:
                    vstr = str(e.get("value", ""))
                _ico = "✅" if e.get("status") == "verified" else "🟡"
                _sum_lines.append(
                    f"{_ico} <span style='color:#4FC3F7'>{k}</span>"
                    f"=<span style='color:#E2E8F0'>{vstr}</span>")
            st.markdown(
                f"<div style='background:#0A1628;border:1px solid #1E2D3D;"
                f"border-radius:6px;padding:8px 12px;font-size:11px;color:#8B9BB4'>"
                f"已保存字段（{ed_ticker}）：<br>"
                + " &nbsp; ".join(_sum_lines)
                + "</div>",
                unsafe_allow_html=True)
