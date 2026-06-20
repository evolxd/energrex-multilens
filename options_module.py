"""
ENERGREX — 期权量化分析模块
============================
数据源：MarketData.app API v1
功能：
  1. 期权链（Call / Put，Strike / IV / Greeks / Volume / OI）
  2. 隐含波动率微笑曲线（IV Smile）
  3. IV 曲面热图（Moneyness × DTE）
  4. Put/Call Ratio 情绪仪表盘
  5. 最活跃期权合约排名
"""

import os, pathlib, datetime
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests

# ── .env 加载 ─────────────────────────────────────────────
_ROOT = pathlib.Path(__file__).parent
_env  = _ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_MD_BASE = "https://api.marketdata.app/v1"
_MD_KEY  = os.environ.get("MARKETDATA_API_KEY", "")

# ── 配色（与 app.py 统一的深色终端风格） ─────────────────
_BG     = "#0A1628"
_SURF   = "#0F1923"
_BORDER = "#1E2D3D"
_TEXT   = "#E2E8F0"
_MUTED  = "#8B9BB4"
_CALL   = "#00D4AA"
_PUT    = "#FF4B6E"
_AMB    = "#FFB347"
_BLUE   = "#4FC3F7"
_WARN   = "#FF8C42"

def _rgba(hex6: str, alpha_hex: str) -> str:
    """Convert '#RRGGBB' + 2-char alpha hex → 'rgba(r,g,b,a)' for Plotly."""
    h = hex6.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = round(int(alpha_hex, 16) / 255, 3)
    return f"rgba({r},{g},{b},{a})"

# ════════════════════════════════════════════════════════
# API 工具层
# ════════════════════════════════════════════════════════

def _md_get(path: str, params: dict | None = None, timeout: int = 20) -> dict | None:
    """向 MarketData.app 发起 GET 请求，正确处理 203/404/402。"""
    if not _MD_KEY:
        return {"s": "error", "errmsg": "MARKETDATA_API_KEY 未配置"}
    p = {"token": _MD_KEY}
    if params:
        p.update(params)
    try:
        r = requests.get(f"{_MD_BASE}{path}", params=p, timeout=timeout)
        # 402 = 超出计划配额
        if r.status_code == 402:
            return {"s": "no_data", "errmsg": "402 — 此接口超出当前 API 计划配额"}
        # 404 = API 用来表示"该查询无数据"（合法响应，返回 JSON）
        if r.status_code == 404:
            return r.json()
        # 203 = 正常成功响应（与 200 语义相同）
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        return {"s": "error", "errmsg": "请求超时（>20s）"}
    except requests.exceptions.RequestException as e:
        return {"s": "error", "errmsg": str(e)}


def _ts_to_date(ts) -> str:
    """将 Unix timestamp（int）转为 YYYY-MM-DD 字符串。"""
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


def _parse_chain(data: dict) -> pd.DataFrame:
    """将 MarketData.app 期权链响应（并行数组）解析为 DataFrame。"""
    n = len(data.get("strike", []))
    if n == 0:
        return pd.DataFrame()

    def arr(key, default=None):
        v = data.get(key, [default] * n)
        return v if len(v) == n else [default] * n

    df = pd.DataFrame({
        "symbol":  arr("optionSymbol"),
        "exp_ts":  arr("expiration"),          # Unix timestamp (int)
        "dte":     arr("dte"),
        "side":    arr("side"),
        "strike":  arr("strike"),
        "bid":     arr("bid"),
        "ask":     arr("ask"),
        "mid":     arr("mid"),
        "last":    arr("last"),
        "volume":  arr("volume"),
        "oi":      arr("openInterest"),
        "iv":      arr("iv"),
        "delta":   arr("delta"),
        "gamma":   arr("gamma"),
        "theta":   arr("theta"),
        "vega":    arr("vega"),
        "itm":     arr("inTheMoney"),
        "und_px":  arr("underlyingPrice"),
    })

    # 数值列转换
    for c in ["strike", "bid", "ask", "mid", "last", "iv",
              "delta", "gamma", "theta", "vega", "und_px"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["volume", "oi", "dte"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

    # Unix timestamp → 日期字符串
    df["exp"] = df["exp_ts"].apply(
        lambda v: _ts_to_date(v) if pd.notna(v) and v else "—")
    df["iv_pct"] = (df["iv"] * 100).round(2)

    return df.drop(columns=["exp_ts"])


@st.cache_data(ttl=600, show_spinner=False)
def fetch_expirations(ticker: str) -> list[str]:
    """拉取可用到期日列表（字符串格式 YYYY-MM-DD）。"""
    data = _md_get(f"/options/expirations/{ticker.upper()}/")
    if not data or data.get("s") != "ok":
        return []
    return sorted(data.get("expirations", []))


@st.cache_data(ttl=60, show_spinner=False)
def fetch_chain(ticker: str, expiration: str, strike_limit: int = 40) -> pd.DataFrame:
    """拉取指定到期日的完整期权链（Call + Put）。"""
    data = _md_get(
        f"/options/chain/{ticker.upper()}/",
        {"expiration": expiration, "strikeLimit": strike_limit},
    )
    if not data or data.get("s") != "ok":
        return pd.DataFrame()
    return _parse_chain(data)


@st.cache_data(ttl=120, show_spinner=False)
def fetch_surface(ticker: str, days: int = 90) -> pd.DataFrame:
    """
    拉取 NTM Call 期权（多到期日）用于 IV 曲面图。
    使用 from/to 日期范围参数覆盖多个到期日。
    """
    today    = datetime.date.today()
    to_date  = (today + datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    from_str = today.strftime("%Y-%m-%d")
    data = _md_get(
        f"/options/chain/{ticker.upper()}/",
        {
            "range":       "NTM",
            "side":        "call",
            "strikeLimit": 5,          # 每个到期日±5档
            "from":        from_str,
            "to":          to_date,
        },
    )
    if not data or data.get("s") != "ok":
        return pd.DataFrame()
    return _parse_chain(data)


# ════════════════════════════════════════════════════════
# 页面配置
# ════════════════════════════════════════════════════════
st.set_page_config(
    page_title="ENERGREX · 期权分析",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(f"""
<style>
footer {{visibility:hidden;}} #MainMenu {{visibility:hidden;}}
.chain-wrap {{
    background:{_BG};border:1px solid {_BORDER};
    border-radius:8px;overflow:hidden;max-height:520px;overflow-y:auto;
}}
.chain-hdr {{
    display:grid;gap:3px;padding:7px 10px;
    background:{_SURF};border-bottom:1px solid {_BORDER};
    font-size:10px;text-transform:uppercase;
    letter-spacing:0.8px;font-weight:600;position:sticky;top:0;z-index:1;
}}
.chain-row {{
    display:grid;gap:3px;padding:5px 10px;
    border-bottom:1px solid {_BORDER}55;font-size:12px;align-items:center;
}}
.chain-row:hover {{background:{_SURF};}}
.atm-row {{background:{_AMB}09;}}
.sk-cell {{
    text-align:center;font-weight:700;color:{_AMB};
    background:{_BORDER};border-radius:3px;padding:2px 4px;
    font-size:12px;
}}
.atm-sk {{color:{_AMB};background:{_AMB}25;}}
.stat-box {{
    background:{_SURF};border:1px solid {_BORDER};
    border-radius:8px;padding:12px;text-align:center;
}}
.stat-val {{font-size:20px;font-weight:800;line-height:1.2;}}
.stat-lbl {{color:{_MUTED};font-size:10px;text-transform:uppercase;
           letter-spacing:0.7px;margin-top:2px;}}
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════
# Session state 初始化
# ════════════════════════════════════════════════════════
if "opt_ticker" not in st.session_state: st.session_state.opt_ticker = ""
if "opt_exps"   not in st.session_state: st.session_state.opt_exps   = []

# ════════════════════════════════════════════════════════
# 侧边栏
# ════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(
        f"<div style='font-size:20px;font-weight:800;letter-spacing:2px;"
        f"color:{_CALL}'>⚡ ENERGREX</div>"
        f"<div style='font-size:11px;color:{_MUTED};margin-bottom:8px'>"
        f"期权量化分析</div>",
        unsafe_allow_html=True)
    st.divider()

    ticker_input = st.text_input(
        "股票代码", value="NVDA",
        placeholder="NVDA · AAPL · SPY …").strip().upper()

    load_btn = st.button("🔍 加载期权数据", type="primary", use_container_width=True)
    st.divider()

    # ── 到期日选择（使用已加载的列表） ──────────────────
    exps = st.session_state.opt_exps
    if exps:
        def _fmt_exp(d: str) -> str:
            try:
                dt = datetime.datetime.strptime(d, "%Y-%m-%d")
                dte = (dt.date() - datetime.date.today()).days
                return f"{d}  ({dte}天)"
            except Exception:
                return d

        selected_exp = st.selectbox("到期日", exps,
                                    format_func=_fmt_exp, key="exp_sel")
        st.markdown(
            f"<div style='color:{_MUTED};font-size:11px'>"
            f"共 {len(exps)} 个到期日可选</div>",
            unsafe_allow_html=True)
    else:
        st.selectbox("到期日", ["— 先加载数据 —"],
                     disabled=True, key="exp_sel_empty")
        selected_exp = None

    st.divider()
    strike_limit = st.slider("每侧 Strike 数量", 10, 60, 30, step=5)
    side_filter  = st.radio("合约方向筛选",
                            ["Call + Put", "仅 Call", "仅 Put"],
                            label_visibility="collapsed")
    st.divider()

    surf_days = st.slider("IV 曲面范围（天）", 30, 180, 90, step=30)
    st.divider()

    st.markdown(
        f"<div style='color:{_MUTED};font-size:11px;line-height:1.9'>"
        f"{'🟢' if _MD_KEY else '🔴'} MarketData.app API<br>"
        f"期权链缓存 60s · 曲面 120s"
        f"</div>", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════
# API Key 检查
# ════════════════════════════════════════════════════════
if not _MD_KEY:
    st.error("⛔ 未配置 `MARKETDATA_API_KEY`。请在 `.env` 文件中设置后重启。")
    st.stop()

# ════════════════════════════════════════════════════════
# 数据加载触发
# ════════════════════════════════════════════════════════
ticker = ticker_input   # 始终以侧边栏输入为准

if load_btn or (ticker and ticker != st.session_state.opt_ticker):
    with st.spinner(f"⏳ 正在拉取 {ticker} 期权到期日列表…"):
        new_exps = fetch_expirations(ticker)
    if not new_exps:
        st.error(f"❌ 未找到 **{ticker}** 的期权数据。请确认 Ticker 正确且该股票有活跃期权。")
        st.stop()
    st.session_state.opt_exps   = new_exps
    st.session_state.opt_ticker = ticker
    st.rerun()

# ── 等待用户第一次加载 ────────────────────────────────────
if not exps:
    st.markdown(f"## 📈 期权量化分析")
    st.info(
        "👈 在左侧输入股票代码并点击 **加载期权数据** 开始分析。\n\n"
        "支持所有美股期权：`NVDA` · `AAPL` · `TSLA` · `SPY` · `QQQ` · `AMZN` …")
    st.stop()

if selected_exp is None:
    st.warning("请选择一个到期日。")
    st.stop()

# ════════════════════════════════════════════════════════
# 加载期权链
# ════════════════════════════════════════════════════════
st.markdown(f"## 📈 期权分析 — `{ticker}`  ·  {selected_exp}")

with st.spinner(f"加载 {ticker} {selected_exp} 期权链…"):
    chain_raw = fetch_chain(ticker, selected_exp, strike_limit)

if chain_raw.empty:
    st.warning(f"⚠️ {ticker} {selected_exp} 期权链数据为空，请换一个到期日。")
    st.stop()

# 方向筛选
if side_filter == "仅 Call":
    chain = chain_raw[chain_raw["side"] == "call"].copy()
elif side_filter == "仅 Put":
    chain = chain_raw[chain_raw["side"] == "put"].copy()
else:
    chain = chain_raw.copy()

calls = chain_raw[chain_raw["side"] == "call"].sort_values("strike").reset_index(drop=True)
puts  = chain_raw[chain_raw["side"] == "put" ].sort_values("strike").reset_index(drop=True)

und_px  = chain_raw["und_px"].dropna().median()   # 用中位数更稳健
dte_val = int(chain_raw["dte"].dropna().iloc[0]) if not chain_raw["dte"].dropna().empty else 0

# ════════════════════════════════════════════════════════
# 快速摘要指标行
# ════════════════════════════════════════════════════════
total_c_vol = int(calls["volume"].sum())
total_p_vol = int(puts["volume"].sum())
total_c_oi  = int(calls["oi"].sum())
total_p_oi  = int(puts["oi"].sum())
pcr_vol = round(total_p_vol / total_c_vol, 2) if total_c_vol else 0.0
pcr_oi  = round(total_p_oi  / total_c_oi,  2) if total_c_oi  else 0.0

# ATM IV = nearest-to-ATM 行的 IV
if not chain_raw.empty and und_px:
    atm_idx = (chain_raw["strike"] - und_px).abs().idxmin()
    atm_iv  = float(chain_raw.loc[atm_idx, "iv_pct"])
else:
    atm_iv = 0.0

pcr_vol_c = _PUT if pcr_vol > 1.2 else (_AMB if pcr_vol > 0.8 else _CALL)
pcr_oi_c  = _PUT if pcr_oi  > 1.2 else (_AMB if pcr_oi  > 0.8 else _CALL)

st.divider()
s1, s2, s3, s4, s5, s6 = st.columns(6)
for col, lbl, val, clr in [
    (s1, "标的价格",   f"${und_px:.2f}",         _TEXT),
    (s2, "ATM IV",     f"{atm_iv:.1f}%",         _AMB),
    (s3, "PCR Vol",    f"{pcr_vol:.2f}",          pcr_vol_c),
    (s4, "PCR OI",     f"{pcr_oi:.2f}",           pcr_oi_c),
    (s5, "Call 成交量", f"{total_c_vol:,}",        _CALL),
    (s6, "Put 成交量",  f"{total_p_vol:,}",        _PUT),
]:
    col.markdown(
        f"<div class='stat-box'>"
        f"<div class='stat-val' style='color:{clr}'>{val}</div>"
        f"<div class='stat-lbl'>{lbl}</div></div>",
        unsafe_allow_html=True)

st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════
# 期权链表格（HTML Grid）
# ════════════════════════════════════════════════════════
st.markdown(f"#### 期权链  —  DTE {dte_val} 天")

CALL_COLS = ["iv_pct", "delta", "bid", "ask", "volume", "oi"]
PUT_COLS  = ["iv_pct", "delta", "bid", "ask", "volume", "oi"]
HDR_MAP   = {"iv_pct": "IV %", "delta": "Δ Delta",
             "bid": "Bid", "ask": "Ask", "volume": "Vol", "oi": "OI"}


def _fmt(v, col: str) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if col == "iv_pct":
        return f"{v:.1f}%"
    if col == "delta":
        return f"{v:+.3f}"
    if col in ("bid", "ask", "mid", "last"):
        return f"${v:.2f}" if v else "—"
    if col in ("volume", "oi"):
        return f"{int(v):,}"
    return str(v)


# 合并 strikes 并限制显示数量
all_strikes = sorted(set(calls["strike"]).union(set(puts["strike"])))
if und_px and len(all_strikes) > 50:
    all_strikes = sorted(sorted(all_strikes, key=lambda s: abs(s - und_px))[:50])

call_idx = calls.set_index("strike").to_dict("index")
put_idx  = puts.set_index("strike").to_dict("index")

nc = len(CALL_COLS)
np_ = len(PUT_COLS)
# Grid columns: [call cols] [strike] [put cols]
col_widths = (
    " ".join(["1fr"] * nc)
    + " 0.65fr "
    + " ".join(["1fr"] * np_)
)

# Header
hdr_calls = "".join(
    f"<div style='text-align:right;color:{_CALL}'>{HDR_MAP[c]}</div>"
    for c in CALL_COLS)
hdr_puts = "".join(
    f"<div style='text-align:right;color:{_PUT}'>{HDR_MAP[c]}</div>"
    for c in PUT_COLS)
header_html = (
    f"<div class='chain-hdr' style='grid-template-columns:{col_widths}'>"
    + hdr_calls
    + f"<div style='text-align:center'>Strike</div>"
    + hdr_puts
    + "</div>"
)

rows_html = ""
for k in all_strikes:
    crow = call_idx.get(k, {})
    prow = put_idx.get(k,  {})

    is_atm = bool(und_px and abs(k - und_px) < und_px * 0.006)
    row_cls = "chain-row atm-row" if is_atm else "chain-row"
    sk_cls  = "sk-cell atm-sk"   if is_atm else "sk-cell"

    iv_c = crow.get("iv_pct")
    iv_p = prow.get("iv_pct")
    c_iv_color = _WARN if (iv_c and iv_c > 80) else _CALL
    p_iv_color = _WARN if (iv_p and iv_p > 80) else _PUT

    def _cell(d: dict, col: str, base_color: str, iv_color: str) -> str:
        color   = iv_color if col == "iv_pct" else base_color
        weight  = "600"    if col == "iv_pct" else "400"
        opacity = "1"      if d else "0.35"
        return (f"<div style='text-align:right;color:{color};"
                f"font-weight:{weight};opacity:{opacity}'>"
                f"{_fmt(d.get(col), col)}</div>")

    call_cells = "".join(_cell(crow, c, _TEXT, c_iv_color) for c in CALL_COLS)
    put_cells  = "".join(_cell(prow, c, _TEXT, p_iv_color) for c in PUT_COLS)

    rows_html += (
        f"<div class='{row_cls}' style='grid-template-columns:{col_widths}'>"
        + call_cells
        + f"<div class='{sk_cls}'>${k:.0f}</div>"
        + put_cells
        + "</div>"
    )

st.markdown(
    f"<div class='chain-wrap'>{header_html}{rows_html}</div>",
    unsafe_allow_html=True)

# ════════════════════════════════════════════════════════
# 图表区：IV 微笑 + PCR 仪表盘
# ════════════════════════════════════════════════════════
st.divider()
ch1, ch2 = st.columns([3, 2])

# ── IV 微笑曲线 ───────────────────────────────────────────
with ch1:
    st.markdown("#### IV 微笑曲线")
    fig_smile = go.Figure()

    if not calls.empty:
        calls_valid = calls.dropna(subset=["iv_pct", "strike"])
        fig_smile.add_trace(go.Scatter(
            x=calls_valid["strike"], y=calls_valid["iv_pct"],
            mode="lines+markers", name="Call IV",
            line=dict(color=_CALL, width=2.5),
            marker=dict(size=6, color=_CALL, symbol="circle"),
            hovertemplate="Strike $%{x:.0f}  IV %{y:.1f}%<extra>Call</extra>",
        ))
    if not puts.empty:
        puts_valid = puts.dropna(subset=["iv_pct", "strike"])
        fig_smile.add_trace(go.Scatter(
            x=puts_valid["strike"], y=puts_valid["iv_pct"],
            mode="lines+markers", name="Put IV",
            line=dict(color=_PUT, width=2.5, dash="dot"),
            marker=dict(size=6, color=_PUT, symbol="circle-open"),
            hovertemplate="Strike $%{x:.0f}  IV %{y:.1f}%<extra>Put</extra>",
        ))
    if und_px:
        fig_smile.add_vline(
            x=und_px,
            line=dict(color=_AMB, width=1.5, dash="dash"),
            annotation_text=f"  ATM ${und_px:.0f}",
            annotation_font=dict(color=_AMB, size=11))

    fig_smile.update_layout(
        xaxis=dict(title="Strike 价格", showgrid=False,
                   tickfont=dict(color=_MUTED), title_font=dict(color=_MUTED)),
        yaxis=dict(title="IV %", showgrid=True, gridcolor=_BORDER,
                   tickfont=dict(color=_MUTED), title_font=dict(color=_MUTED)),
        paper_bgcolor=_BG, plot_bgcolor=_BG,
        legend=dict(font=dict(color=_TEXT), bgcolor=_BG, bordercolor=_BORDER,
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=30, b=10), height=320,
    )
    st.plotly_chart(fig_smile, use_container_width=True)

# ── PCR 仪表盘 + 量对比柱状图 ────────────────────────────
with ch2:
    st.markdown("#### Put/Call 情绪")

    # PCR 仪表盘
    pcr_color = _PUT if pcr_vol > 1.2 else (_AMB if pcr_vol > 0.8 else _CALL)
    fig_gauge = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=pcr_vol,
        delta={"reference": 1.0, "relative": False,
               "valueformat": ".2f",
               "font": {"color": _MUTED, "size": 12}},
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": "Put/Call Ratio（成交量）",
               "font": {"color": _TEXT, "size": 12}},
        number={"font": {"color": pcr_color, "size": 32},
                "valueformat": ".2f"},
        gauge={
            "axis": {"range": [0, 2.5],
                     "tickfont": {"color": _MUTED, "size": 9},
                     "tickvals": [0, 0.7, 1.0, 1.3, 2.0, 2.5]},
            "bar":  {"color": pcr_color, "thickness": 0.25},
            "bgcolor": _SURF,
            "bordercolor": _BORDER,
            "borderwidth": 1,
            "steps": [
                {"range": [0,   0.7],  "color": _rgba(_CALL, "18")},
                {"range": [0.7, 1.3],  "color": _rgba(_AMB,  "18")},
                {"range": [1.3, 2.5],  "color": _rgba(_PUT,  "18")},
            ],
            "threshold": {
                "line": {"color": _AMB, "width": 2},
                "thickness": 0.8, "value": 1.0,
            },
        },
    ))
    fig_gauge.update_layout(
        paper_bgcolor=_BG,
        font=dict(color=_TEXT),
        margin=dict(l=20, r=20, t=40, b=10),
        height=220,
    )
    st.plotly_chart(fig_gauge, use_container_width=True)

    # 解读标签
    if pcr_vol > 1.3:
        interp = ("🐻 **偏空**", "Put 成交量显著高于 Call，市场情绪偏悲观或有对冲需求。")
    elif pcr_vol < 0.7:
        interp = ("🐂 **偏多**", "Call 成交量显著高于 Put，市场情绪偏乐观。")
    else:
        interp = ("⚖️ **中性**", "Put/Call 比接近 1，市场情绪中性。")
    st.markdown(
        f"<div style='background:{_SURF};border:1px solid {_BORDER};"
        f"border-radius:6px;padding:10px 12px;font-size:12px'>"
        f"{interp[0]}<br>"
        f"<span style='color:{_MUTED}'>{interp[1]}</span></div>",
        unsafe_allow_html=True)

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    # Vol/OI 双柱
    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        name="成交量", x=["Call", "Put"],
        y=[total_c_vol, total_p_vol],
        marker=dict(color=[_CALL, _PUT]),
        text=[f"{total_c_vol:,}", f"{total_p_vol:,}"],
        textfont=dict(color=_TEXT, size=10),
        textposition="outside",
    ))
    fig_bar.add_trace(go.Bar(
        name="持仓量 OI", x=["Call", "Put"],
        y=[total_c_oi, total_p_oi],
        marker=dict(color=[_rgba(_CALL, "77"), _rgba(_PUT, "77")]),
        text=[f"{total_c_oi:,}", f"{total_p_oi:,}"],
        textfont=dict(color=_TEXT, size=10),
        textposition="outside",
    ))
    fig_bar.update_layout(
        barmode="group",
        xaxis=dict(showgrid=False, tickfont=dict(color=_MUTED)),
        yaxis=dict(showgrid=False, visible=False),
        paper_bgcolor=_BG, plot_bgcolor=_BG,
        legend=dict(font=dict(color=_TEXT, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", y=1.1),
        margin=dict(l=0, r=0, t=24, b=0),
        height=130,
    )
    st.plotly_chart(fig_bar, use_container_width=True)

# ════════════════════════════════════════════════════════
# IV 曲面热图
# ════════════════════════════════════════════════════════
st.divider()
st.markdown(f"#### IV 曲面热图  —  NTM Call · {surf_days}天内")

with st.spinner(f"加载 {ticker} IV 曲面数据（{surf_days}天 · NTM）…"):
    sdf_raw = fetch_surface(ticker, surf_days)

if sdf_raw.empty:
    st.info("曲面数据为空。可能是 API 配额限制，或该 Ticker 在指定范围内无 NTM 数据。")
else:
    sdf = sdf_raw.dropna(subset=["iv", "strike", "dte", "und_px"]).copy()
    sdf = sdf[sdf["und_px"] > 0].copy()

    if sdf.empty:
        st.info("曲面数据清洗后为空（iv/und_px 字段均缺失）。")
    else:
        # Moneyness = strike/und_px - 1（%）
        sdf["moneyness"] = ((sdf["strike"] / sdf["und_px"] - 1) * 100).round(1)
        sdf["iv_pct"]    = (sdf["iv"] * 100).round(2)

        surf_tab = sdf.pivot_table(
            index="dte", columns="moneyness",
            values="iv_pct", aggfunc="mean",
        ).sort_index()

        if surf_tab.empty:
            st.info("曲面透视表为空，数据不足以绘制曲面。")
        else:
            # 限制 moneyness 范围 ±15%
            valid_cols = [c for c in surf_tab.columns if -15 <= c <= 15]
            surf_tab   = surf_tab[valid_cols]

            z_min = float(surf_tab.min().min())
            z_max = float(surf_tab.max().max())

            fig_surf = go.Figure(go.Heatmap(
                z=surf_tab.values,
                x=[f"{c:+.1f}%" for c in surf_tab.columns],
                y=[f"DTE {d}" for d in surf_tab.index],
                colorscale=[
                    [0.00, "#0D2137"],
                    [0.30, _CALL],
                    [0.55, _AMB],
                    [0.75, _WARN],
                    [1.00, _PUT],
                ],
                zmin=z_min, zmax=z_max,
                colorbar=dict(
                    title=dict(text="IV %", font=dict(color=_MUTED)),
                    tickfont=dict(color=_MUTED),
                    bgcolor=_SURF, bordercolor=_BORDER, borderwidth=1,
                    len=0.9,
                ),
                hovertemplate=(
                    "Moneyness: %{x}<br>"
                    "DTE: %{y}<br>"
                    "IV: %{z:.1f}%<extra></extra>"
                ),
            ))
            fig_surf.update_layout(
                xaxis=dict(
                    title="Moneyness（相对 ATM %）",
                    tickfont=dict(color=_MUTED),
                    title_font=dict(color=_MUTED),
                    showgrid=False,
                ),
                yaxis=dict(
                    title="到期天数 DTE",
                    tickfont=dict(color=_MUTED),
                    title_font=dict(color=_MUTED),
                    showgrid=False,
                    autorange="reversed",
                ),
                paper_bgcolor=_BG, plot_bgcolor=_BG,
                margin=dict(l=10, r=10, t=10, b=10),
                height=380,
            )
            st.plotly_chart(fig_surf, use_container_width=True)
            n_exp_surf = surf_tab.shape[0]
            st.markdown(
                f"<div style='color:{_MUTED};font-size:11px;text-align:center'>"
                f"Call IV · {n_exp_surf} 个到期日 · "
                f"颜色从深蓝（低IV）到红（高IV）· ATM IV ≈ {atm_iv:.1f}%"
                f"</div>",
                unsafe_allow_html=True)

# ════════════════════════════════════════════════════════
# 最活跃期权合约
# ════════════════════════════════════════════════════════
st.divider()
st.markdown("#### 最活跃期权合约")
ta1, ta2 = st.columns(2)


def _render_top(container, df_src: pd.DataFrame, by: str, label: str, color: str, n: int = 8):
    with container:
        st.markdown(
            f"<div style='color:{color};font-size:12px;font-weight:600;"
            f"margin-bottom:4px'>▲ 按 {label} 排序</div>",
            unsafe_allow_html=True)
        if df_src.empty:
            st.info("无数据")
            return
        top = df_src.nlargest(n, by)[
            ["symbol", "side", "strike", "exp", "iv_pct", "delta", by]
        ].reset_index(drop=True)
        top.index = top.index + 1
        top.columns = ["合约", "方向", "Strike", "到期日", "IV %", "Δ", label]
        top["方向"]  = top["方向"].map({"call": "🟢 Call", "put": "🔴 Put"}).fillna("—")
        top["Strike"] = top["Strike"].apply(lambda v: f"${v:.0f}")
        top["IV %"]   = top["IV %"].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "—")
        top["Δ"]      = top["Δ"].apply(lambda v: f"{v:+.3f}" if pd.notna(v) else "—")
        top[label]    = top[label].apply(lambda v: f"{int(v):,}" if pd.notna(v) else "—")
        st.dataframe(top, use_container_width=True, height=260,
                     hide_index=False)


_render_top(ta1, chain_raw, "volume", "成交量", _CALL)
_render_top(ta2, chain_raw, "oi",     "持仓量", _AMB)

# ── 页脚 ─────────────────────────────────────────────────
st.divider()
st.markdown(
    f"<div style='text-align:center;color:{_MUTED};font-size:11px;padding:4px 0'>"
    f"ENERGREX · MarketData.app API · "
    f"期权链缓存 60s · IV 曲面缓存 120s · {ticker} {selected_exp}"
    f"</div>",
    unsafe_allow_html=True)
