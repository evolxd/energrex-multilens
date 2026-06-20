"""
ENERGREX — Daily War Room (主页作战室)
布局: 状态栏 → 简报+账户 → 快捷入口
"""
import os, pathlib, datetime, sqlite3, json, socket
import streamlit as st
import pytz

_ROOT = pathlib.Path(__file__).parent
_DB   = _ROOT / "data" / "energrex.db"

# ── .env 加载 ────────────────────────────────────────────
_env = _ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

st.set_page_config(
    page_title="ENERGREX 作战室",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 全局样式 ──────────────────────────────────────────────
_G = "#00D4AA"; _R = "#FF4B6E"; _A = "#FFB347"; _B = "#4FC3F7"
_SURF = "#0F1923"; _BDR = "#1E2D3D"; _MUT = "#8B9BB4"; _TXT = "#E2E8F0"

st.markdown(f"""<style>
footer {{visibility:hidden;}} #MainMenu {{visibility:hidden;}}
.stButton>button {{
    background:{_SURF}; border:1px solid {_BDR}; color:{_TXT};
    border-radius:8px; font-size:14px; font-weight:600;
    padding:14px 8px; transition:all 0.15s;
}}
.stButton>button:hover {{
    border-color:{_G}; color:{_G}; background:{_G}11;
}}
</style>""", unsafe_allow_html=True)


# ── 共享侧边栏 ───────────────────────────────────────
import _sidebar as _sb
_sb.render()

# ═══════════════════════════════════════════════════
# 数据读取
# ═══════════════════════════════════════════════════
def _db_conn():
    if not _DB.exists():
        return None
    conn = sqlite3.connect(str(_DB))
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data(ttl=60)
def _load_war_data():
    """读取简报 + 账户余额。TTL=60s。"""
    result = {"briefing": None, "accounts": [], "has_db": False}
    conn = _db_conn()
    if conn is None:
        return result
    result["has_db"] = True

    today = datetime.date.today().isoformat()

    # Daily briefing (account_1 优先)
    try:
        row = conn.execute(
            "SELECT gen_time, snap_json, recs_json FROM daily_briefing "
            "WHERE acct_id='account_1' AND date=? ORDER BY id DESC LIMIT 1",
            (today,)).fetchone()
        if row:
            result["briefing"] = {
                "gen_time": (row["gen_time"] or "")[:16].replace("T", " "),
                "snap":     json.loads(row["snap_json"] or "{}"),
                "recs":     json.loads(row["recs_json"] or "[]"),
            }
    except Exception:
        pass

    # Account balances
    try:
        rows = conn.execute(
            "SELECT account_id, total_equity, cash_balance, day_pnl, sync_time "
            "FROM account_balance WHERE sync_time >= ? "
            "GROUP BY account_id HAVING MAX(sync_time) "
            "ORDER BY account_id",
            (today + "T00:00:00",)).fetchall()
        for r in rows:
            result["accounts"].append({
                "id":     r["account_id"],
                "equity": r["total_equity"],
                "cash":   r["cash_balance"],
                "pnl":    r["day_pnl"],
                "time":   (r["sync_time"] or "")[:16].replace("T", " "),
            })
    except Exception:
        pass

    conn.close()
    return result


@st.cache_data(ttl=30)
def _chrome_ok() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 9222), timeout=0.5)
        s.close()
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════
_data  = _load_war_data()
_brief = _data.get("briefing")
_snap  = _brief["snap"] if _brief else {}
_accts = _data.get("accounts", [])
_chrome = _chrome_ok()

_now_et = datetime.datetime.now(
    pytz.timezone("America/New_York"))
_date_str = _now_et.strftime("%Y-%m-%d  %H:%M ET")

# ─── 行 1：状态栏 ───────────────────────────────────
_bdr_pct  = (_snap.get("beta_delta_ratio") or 0) * 100
_s10_pct  = (_snap.get("stress_10_ratio")  or 0) * 100
_s10_amt  = _snap.get("stress_10", 0) or 0
_s20_pct  = (_snap.get("stress_20_ratio")  or 0) * 100
_s20_amt  = _snap.get("stress_20", 0) or 0
_theta    = _snap.get("theta_per_day", 0) or 0
_nexp_s   = _snap.get("nearest_expiry_sym", "")
_nexp_d   = _snap.get("nearest_expiry_date")
_risk_st  = _snap.get("risk_status", "")
_draw_st  = _snap.get("drawdown_status", "")

_RISK_DOT = {
    "RED_HARD_STOP":  ("🔴", _R),
    "ORANGE_DE_RISK": ("🟠", _A),
    "YELLOW_WARNING": ("🟡", "#FFD700"),
    "GREEN":          ("🟢", _G),
}
_rdot, _rcol = _RISK_DOT.get(_risk_st, ("⚪", _MUT))

_DRAW_DOT = {
    "RED_MANDATORY_DE_RISK": ("🔴", _R,         "强制去风险"),
    "ORANGE_CAUTION":        ("🟠", _A,         "回撤警戒"),
    "YELLOW_WARNING":        ("🟡", "#FFD700",  "回撤注意"),
    "GREEN":                 ("🟢", _G,         ""),
}
_ddot, _dcol, _dlabel = _DRAW_DOT.get(_draw_st, ("⚪", _MUT, ""))

if _nexp_d:
    try:
        _nexp_date = datetime.date.fromisoformat(str(_nexp_d))
        _dte_near  = (_nexp_date - datetime.date.today()).days
        _nexp_str  = f"最近到期:{_nexp_s} {_dte_near}天"
    except Exception:
        _nexp_str = ""
else:
    _nexp_str = ""

_chrome_str = (
    f"<span style='color:{_G}'>Chrome:🟢</span>"
    if _chrome else
    f"<span style='color:{_R}'>Chrome:🔴未连接</span>"
)

if _snap:
    _bd_str  = f"BD:{_bdr_pct:.0f}%"
    _th_str  = f"Θ:{_theta:+,.0f}/天"
    _sc10    = _R if abs(_s10_pct) > 20 else "#FFD700" if abs(_s10_pct) > 10 else _G
    _sc20    = _R if abs(_s20_pct) > 20 else "#FFD700" if abs(_s20_pct) > 10 else _G
    _stress_block = (
        f"<span style='display:inline-flex;flex-direction:column;"
        f"line-height:1.5;vertical-align:middle;gap:0'>"
        f"<span style='color:{_sc10}'>压力 -10%&nbsp;&nbsp;"
        f"${_s10_amt:+,.0f}&nbsp;({_s10_pct:+.1f}% 净值)</span>"
        f"<span style='color:{_sc20}'>压力 -20%&nbsp;&nbsp;"
        f"${_s20_amt:+,.0f}&nbsp;({_s20_pct:+.1f}% 净值)</span>"
        f"</span>"
    )
    _snap_inline = (
        f"<span style='color:{_rcol}'>{_rdot} {_bd_str}</span>"
        f"<span style='color:{_MUT}'> &nbsp;|&nbsp; </span>"
        f"<span style='color:{_TXT}'>{_th_str}</span>"
        f"<span style='color:{_MUT}'> &nbsp;|&nbsp; </span>"
        + _stress_block
    )
    if _draw_st and _draw_st != "GREEN":
        _snap_inline += (
            f"<span style='color:{_MUT}'> &nbsp;|&nbsp; </span>"
            f"<span style='color:{_dcol};font-weight:700'>{_ddot} DD:{_dlabel}</span>"
        )
    if _nexp_str:
        _snap_inline += (
            f"<span style='color:{_MUT}'> &nbsp;|&nbsp; </span>"
            f"<span style='color:{_TXT}'>{_nexp_str}</span>"
        )
    _snap_inline += (
        f"<span style='color:{_MUT}'> &nbsp;|&nbsp; </span>"
        + _chrome_str
    )
else:
    _snap_inline = (
        f"<span style='color:{_MUT}'>⚪ BD:-- &nbsp;|&nbsp; Θ:-- &nbsp;|&nbsp; </span>"
        + _chrome_str
        + f"<span style='color:{_A};margin-left:8px'>简报待生成</span>"
    )

st.markdown(
    f"<div style='background:{_SURF};border:1px solid {_BDR};border-radius:10px;"
    f"padding:10px 20px;display:flex;justify-content:space-between;"
    f"align-items:center;margin-bottom:12px'>"
    f"<span style='font-size:18px;font-weight:800;letter-spacing:2px;color:{_G}'>"
    f"⚡ ENERGREX 作战室</span>"
    f"<span style='font-size:12px'>{_snap_inline}</span>"
    f"<span style='font-size:12px;color:{_MUT}'>{_date_str}</span>"
    f"</div>",
    unsafe_allow_html=True,
)

# ─── 强制去风险横幅 ─────────────────────────────────
if _draw_st == "RED_MANDATORY_DE_RISK":
    st.markdown(
        f"<div style='background:{_R}22;border:2px solid {_R};"
        f"border-radius:8px;padding:12px 20px;margin-bottom:10px;"
        f"display:flex;align-items:center;gap:12px'>"
        f"<span style='font-size:22px'>⛔</span>"
        f"<div>"
        f"<span style='color:{_R};font-size:15px;font-weight:800;letter-spacing:1px'>"
        f"强制去风险信号触发</span>"
        f"<span style='color:{_TXT};font-size:12px;margin-left:12px'>"
        f"账户回撤已超阈值 — 请立即查看持仓并执行减仓计划</span>"
        f"</div>"
        f"<span style='margin-left:auto;color:{_R};font-size:11px;opacity:.7'>"
        f"drawdown_status: RED_MANDATORY_DE_RISK</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
elif _draw_st and _draw_st != "GREEN":
    st.markdown(
        f"<div style='background:{_dcol}15;border:1px solid {_dcol}80;"
        f"border-radius:8px;padding:10px 20px;margin-bottom:10px'>"
        f"<span style='color:{_dcol};font-weight:700'>{_ddot} 回撤预警：{_dlabel}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

# Chrome 小提示（不连接时）
if not _chrome:
    st.caption("🔴 Chrome CDP 未连接 — 运行 `start_chrome.bat` 并登录 Firstrade 后自动同步")

# ─── 行 2：简报 + 账户 ──────────────────────────────
_left, _right = st.columns([6, 4], gap="medium")

with _left:
    _gen_tag = (f"<span style='font-size:11px;color:{_MUT}'>"
                f"生成于 {_brief['gen_time']} ET</span>"
                if _brief else
                f"<span style='font-size:11px;color:{_A}'>尚未生成，今日 09:35 ET 自动生成</span>")
    st.markdown(
        f"<div style='font-size:14px;font-weight:700;color:{_TXT};"
        f"margin-bottom:6px'>⚡ 今日操作简报 &nbsp; {_gen_tag}</div>",
        unsafe_allow_html=True,
    )

    if _brief and _brief["recs"]:
        _recs = _brief["recs"]
        _urgent = [r for r in _recs if str(r.get("优先级","")).startswith(("🔴","🟠"))]
        _others = [r for r in _recs if not str(r.get("优先级","")).startswith(("🔴","🟠"))]
        _show   = (_urgent + _others)[:5]

        for _r in _show:
            _pri = str(_r.get("优先级", ""))
            _lc  = (_R if _pri.startswith("🔴") else
                    _A if _pri.startswith("🟠") else
                    _G if _pri.startswith("🟢") else _MUT)
            _act = str(_r.get("行动建议", ""))[:80]
            st.markdown(
                f"<div style='border-left:3px solid {_lc};padding:5px 10px;"
                f"margin:3px 0;background:{_lc}0d;border-radius:0 5px 5px 0'>"
                f"<span style='color:{_lc};font-weight:700;font-size:12px'>"
                f"{_pri}</span>"
                f"<span style='color:{_TXT};font-weight:600;font-size:13px;"
                f"margin:0 6px'>{_r.get('标的','')}</span>"
                f"<span style='color:{_MUT};font-size:11px'>{_r.get('组合','')}"
                f" DTE:{_r.get('DTE','—')}</span><br>"
                f"<span style='color:{_TXT};font-size:12px'>{_act}"
                f"{'…' if len(str(_r.get('行动建议',''))) > 80 else ''}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        if len(_recs) > 5:
            st.caption(f"另有 {len(_recs)-5} 条 → 🏦 持仓详情 › 交易建议")
    elif _brief:
        st.markdown(
            f"<div style='color:{_G};padding:12px;background:{_G}11;"
            f"border-radius:6px;font-size:13px'>✅ 今日无紧急操作建议</div>",
            unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div style='color:{_MUT};padding:12px;background:{_SURF};"
            f"border:1px dashed {_BDR};border-radius:6px;font-size:12px'>"
            f"简报将在 09:35 ET 自动生成，也可在 🏦 持仓详情 页手动触发</div>",
            unsafe_allow_html=True)

with _right:
    st.markdown(
        f"<div style='font-size:14px;font-weight:700;color:{_TXT};"
        f"margin-bottom:6px'>💼 账户快览</div>",
        unsafe_allow_html=True,
    )

    if _accts:
        for _ac in _accts:
            _eq  = _ac.get("equity")
            _ca  = _ac.get("cash")
            _pnl = _ac.get("pnl")
            _tid = _ac.get("id", "")
            _lbl = "账户一" if "1" in _tid else "账户二"
            _pnl_col = (_G if (_pnl or 0) >= 0 else _R)

            _rows_html = ""
            if _eq is not None:
                _rows_html += (f"<div style='display:flex;justify-content:space-between;"
                               f"padding:3px 0'>"
                               f"<span style='color:{_MUT};font-size:12px'>总资产</span>"
                               f"<span style='color:{_TXT};font-weight:700;font-size:14px'>"
                               f"${_eq:,.0f}</span></div>")
            if _ca is not None:
                _rows_html += (f"<div style='display:flex;justify-content:space-between;"
                               f"padding:3px 0'>"
                               f"<span style='color:{_MUT};font-size:12px'>现金</span>"
                               f"<span style='color:{_TXT};font-size:13px'>"
                               f"${_ca:,.0f}</span></div>")
            if _pnl is not None:
                _rows_html += (f"<div style='display:flex;justify-content:space-between;"
                               f"padding:3px 0'>"
                               f"<span style='color:{_MUT};font-size:12px'>当日盈亏</span>"
                               f"<span style='color:{_pnl_col};font-weight:700;font-size:13px'>"
                               f"${_pnl:+,.0f}</span></div>")

            if _rows_html:
                st.markdown(
                    f"<div style='background:{_SURF};border:1px solid {_BDR};"
                    f"border-radius:8px;padding:10px 14px;margin-bottom:8px'>"
                    f"<div style='font-size:11px;color:{_MUT};text-transform:uppercase;"
                    f"letter-spacing:1px;margin-bottom:6px'>{_lbl}</div>"
                    f"{_rows_html}</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.markdown(
            f"<div style='color:{_MUT};font-size:12px;padding:12px;"
            f"background:{_SURF};border:1px dashed {_BDR};border-radius:8px'>"
            f"暂无账户数据 — 请在 🏦 持仓详情 页上传 CSV</div>",
            unsafe_allow_html=True,
        )

# ─── 行 3：快捷入口 ─────────────────────────────────
st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
_n1, _n2, _n3, _n4 = st.columns(4, gap="small")
with _n1:
    st.page_link("pages/1_📊_AI_估值评分.py",
                 label="📊 AI估值排行榜", use_container_width=True)
with _n2:
    st.page_link("pages/2_📈_期权分析.py",
                 label="📈 期权分析", use_container_width=True)
with _n3:
    st.page_link("pages/3_🏦_账户监控.py",
                 label="🏦 持仓详情", use_container_width=True)
with _n4:
    st.page_link("pages/3_🏦_账户监控.py",
                 label="⚙️ 数据管理", use_container_width=True)
