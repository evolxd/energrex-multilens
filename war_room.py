"""
ENERGREX — Daily War Room (作战室仪表盘)
布局: 状态栏 → 简报+账户 → 快捷入口

2026-09-08 从 home.py 拆出来——home.py 现在只是 st.navigation() 的导航壳
（六重门重建），实际的仪表盘内容搬到这个文件，跟其它每个页面一样自己管
自己的 st.set_page_config()。
"""
import datetime, sqlite3, json, socket
import streamlit as st
import pytz

from account.accounts import list_accounts as _list_accounts
from scoring.mispricing_monitor import SEVERE_STATES, WATCH_STATES, thesis_state_for_ticker
from scoring.position_exposure import compute_exposures

import pathlib
_ROOT = pathlib.Path(__file__).parent
_DB   = _ROOT / "data" / "energrex.db"
_MISPRICING_LOG = _ROOT / "data" / "mispricing_cases.jsonl"

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


# ── 共享侧边栏（数据更新 + 风险状态；导航本身在 home.py 的 st.navigation()）
import _sidebar as _sb
_sb.render()

# ── 账户选择器（跟账户监控页侧边栏共用同一个 session_state key："sb_acct"，
# 两个页面切账户是同一件事，不是各切各的）───────────────────────────
ACCT_CFG = _list_accounts()
_acct_opts = {c["label"]: c for c in ACCT_CFG}
with st.sidebar:
    st.divider()
    _sel_display = st.selectbox("账户", list(_acct_opts.keys()),
                                key="sb_acct", label_visibility="collapsed")
_sel_cfg = _acct_opts[_sel_display]
_ACCT_ID = _sel_cfg["id"]

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
def _load_war_data(acct_id: str):
    """读取简报 + 账户余额。TTL=60s。acct_id 进了函数签名，Streamlit
    按参数分别缓存——切账户不会读到上一个账户缓存的简报。"""
    result = {"briefing": None, "accounts": [], "has_db": False}
    conn = _db_conn()
    if conn is None:
        return result
    result["has_db"] = True

    today = datetime.date.today().isoformat()

    # Daily briefing（选中的账户）
    try:
        row = conn.execute(
            "SELECT gen_time, snap_json, recs_json FROM daily_briefing "
            "WHERE acct_id=? AND date=? ORDER BY id DESC LIMIT 1",
            (acct_id, today)).fetchone()
        if row:
            result["briefing"] = {
                "gen_time": (row["gen_time"] or "")[:16].replace("T", " "),
                "snap":     json.loads(row["snap_json"] or "{}"),
                "recs":     json.loads(row["recs_json"] or "[]"),
            }
    except Exception:
        pass

    # Account balance（选中账户的最近一次同步，不限今天——以前只认今天同步过的，
    # 当天没同步就整块显示"暂无账户数据"；而且没按账户过滤。同步日期显示在卡片上）
    try:
        rows = conn.execute(
            "SELECT account_id, total_equity, cash_balance, day_pnl, sync_time "
            "FROM account_balance WHERE account_id=? "
            "ORDER BY sync_time DESC LIMIT 1",
            (acct_id,)).fetchall()
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


@st.cache_data(ttl=60)
def _load_thesis_alerts(acct_id: str) -> list[dict]:
    """Held tickers whose mispricing thesis has moved to SEVERE/WATCH.

    Deliberately separate from daily_briefing's recs above: those are
    mechanical (stop-loss / take-profit / DTE, from _cascade.py's exit-signal
    scan). This is "the reason you bought it may no longer hold" -- a
    different question with a different response, so it gets its own block
    instead of being folded into the same card list.

    2026-09-11: this used to query positions/options_positions/account_balance
    with no account_id filter at all -- with only one account ever synced that
    was invisible, but it silently blended every account's holdings into one
    thesis-alert list. Now scoped to the selected account, matching every
    other per-account query on this page.
    """
    try:
        from account.db import db

        conn = db()
        latest = conn.execute(
            "SELECT MAX(sync_time) FROM positions WHERE account_id=?",
            (acct_id,)).fetchone()
        sync_time = latest[0] if latest else None
        # Latest row per symbol, not "every row sharing one exact global
        # sync_time" -- see account/repository.py::load_positions for why
        # the old exact-match query used to silently drop most holdings.
        rows = (
            [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT symbol, market_value FROM positions p1
                    WHERE p1.account_id = ? AND p1.sync_time = (
                        SELECT MAX(p2.sync_time) FROM positions p2
                        WHERE p2.symbol = p1.symbol AND p2.account_id = p1.account_id
                    )
                    """,
                    (acct_id,),
                )
            ]
            if sync_time
            else []
        )
        options = [
            dict(r)
            for r in conn.execute(
                "SELECT symbol, market_value FROM options_positions WHERE account_id=?",
                (acct_id,))
        ]
        bal = conn.execute(
            "SELECT total_equity FROM account_balance WHERE account_id=? "
            "ORDER BY sync_time DESC LIMIT 1", (acct_id,)
        ).fetchone()
        conn.close()
        equity = float(bal[0]) if bal and bal[0] else None

        exposures = compute_exposures(rows, equity, None, lambda _s: None, options)
        if exposures is None:
            return []

        alert_states = SEVERE_STATES | WATCH_STATES
        alerts = []
        for ticker in sorted(exposures.by_ticker_pct):
            state = thesis_state_for_ticker(_MISPRICING_LOG, ticker)
            if state and state["case_state"] in alert_states:
                alerts.append(state)
        return alerts
    except Exception:
        return []


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
_data  = _load_war_data(_ACCT_ID)
_brief = _data.get("briefing")
_snap  = _brief["snap"] if _brief else {}
_accts = _data.get("accounts", [])
_chrome = _chrome_ok()
_ACCT_LABEL = {c["id"]: c["label"] for c in ACCT_CFG}

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
_draw_pct = (_snap.get("drawdown") or 0) * 100
_draw_basis = _snap.get("drawdown_basis", "legacy_or_unknown")

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
    # 这一整行数字来自存库的简报快照，不是实时计算。右上角那个时间戳是"现在
    # 几点"，跟数据无关——两者并排显示，读的人自然会把 BD 当成当前值。用户连续
    # 三轮看到 BD 378% 不动、以为修复失效，实际是 09-19 11:50 那次生成冻结下来
    # 的数，中间所有计算侧的改动它根本不经过。所以把快照自己的年龄标出来。
    _brief_age = ""
    if _brief and _brief.get("gen_time"):
        try:
            # gen_time 存的是美东墙钟时间（不带时区），所以"现在"也必须取美东，
            # 不能用本机时间——本机在太平洋时区时会算出"-2.7小时前"。
            _gt = datetime.datetime.fromisoformat(_brief["gen_time"].replace(" ", "T"))
            _now_et = datetime.datetime.now(pytz.timezone("America/New_York")).replace(tzinfo=None)
            _hrs = (_now_et - _gt).total_seconds() / 3600
            _age_txt = (f"{_hrs/24:.1f}天前" if _hrs >= 24 else f"{_hrs:.1f}小时前")
            _age_col = _R if _hrs >= 24 else (_A if _hrs >= 4 else _MUT)
            _brief_age = (f"<span style='color:{_age_col}'>&nbsp;|&nbsp; "
                          f"快照 {_age_txt}</span>")
        except Exception:
            _brief_age = ""
    _snap_inline += _brief_age
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

# ─── 运行来源标记 ───────────────────────────────────
# 一台机器上可以有同一个仓库的多份克隆，每份都有自己的 data/energrex.db。
# 2026-09-20 就同时存在三个目录：应用实际服务的是 VSCODE管理项目\...\
# energrex-multilens，第二份克隆 ~/ai_valuation 停在旧提交，而 git pull 一直
# 敲在 Documents\ENERGREX期权量化系统——那是另一个仓库。连续几天的修复看起来
# 毫无效果，因为界面上没有任何东西能说明"你现在看的是哪一份"。
#
# 另外 Streamlit 只重跑页面脚本，import 进来的 account/*、scoring/* 缓存在
# sys.modules 里，拉了新代码不重启同样不生效——所以这里一并检测模块漂移。
try:
    from account.build_info import stale_modules as _stale_modules
    from account.build_info import summary as _build_summary

    _stale_now = _stale_modules()
    st.markdown(
        f"<div style='font-size:10.5px;color:{_R if _stale_now else _MUT};"
        f"text-align:right;margin:-8px 0 8px'>运行自 {_build_summary()}</div>",
        unsafe_allow_html=True,
    )
    if _stale_now:
        st.warning(
            "⚠️ 以下模块的文件已更新，但当前进程仍在跑旧版本，**必须完全重启 "
            f"Streamlit 才会生效**：{'、'.join(_stale_now)}"
        )
except Exception:
    pass          # 版本标记不该拖垮作战室

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


# ─── 重构流水线看板（只读，见 docs/REFACTORING_WORKFLOW.md）─────
def render_refactor_dashboard() -> None:
    """重构流水线状态看板（只读）：读取 .refactor_status.json 并渲染，从不写入。
    文件不存在或格式损坏时静默/温和降级，不能拖垮作战室主页面。"""
    _status_path = _ROOT / ".refactor_status.json"
    try:
        _raw = _status_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return  # 还没有任何重构在跑，不显示这块面板
    try:
        _data = json.loads(_raw)
    except json.JSONDecodeError as _exc:
        st.warning(f"⚠️ 重构看板：.refactor_status.json 解析失败（{_exc}）")
        return

    _status = _data.get("status", "IDLE")
    _phase = _data.get("current_phase")
    _target = _data.get("target_module")
    _motivation = _data.get("motivation")
    _logs = _data.get("logs") or []

    _PHASE_PCT = {
        "PHASE_1_ARCH_SCOUTING": 0.25,
        "PHASE_2_SAFETY_NET": 0.50,
        "PHASE_3_SANDBOX_REFACTOR": 0.75,
        "PHASE_4_VERIFY_MERGE": 1.00,
        "DONE": 1.00,
    }
    _frac = _PHASE_PCT.get(_phase, 0.0)

    st.markdown("##### 🛠️ 重构流水线看板")
    if _status == "IN_PROGRESS":
        st.warning(f"进行中 · 当前阶段：{_phase or '未知'}")
    elif _status == "COMPLETED":
        st.success(f"已完成 · {_phase or 'DONE'}")
    else:
        st.info("当前待命，暂无进行中的重构。")

    st.progress(_frac, text=f"{_phase or _status} · {int(_frac * 100)}%")

    _c1, _c2 = st.columns(2)
    with _c1:
        st.markdown(f"**目标模块**\n\n{_target or '（未设定）'}")
    with _c2:
        st.markdown(f"**重构动机**\n\n{_motivation or '（未设定）'}")

    with st.expander("📝 最新重构日志", expanded=True):
        if not _logs:
            st.caption("暂无日志记录。")
        else:
            for _entry in reversed(_logs[-5:]):
                st.markdown(
                    f"`{_entry.get('time', '?')}` **{_entry.get('event', '?')}**  \n"
                    f"{_entry.get('message', '')}"
                )
    st.divider()


render_refactor_dashboard()

# ─── 行 2：简报 + 账户 ──────────────────────────────
_left, _right = st.columns([6, 4], gap="medium")

with _left:
    _gen_tag = (f"<span style='font-size:11px;color:{_MUT}'>"
                f"生成于 {_brief['gen_time']} ET</span>"
                if _brief else
                f"<span style='font-size:11px;color:{_A}'>尚未生成，今日 09:35 ET 自动生成</span>")
    _hdr_col, _btn_col = st.columns([5, 1])
    with _hdr_col:
        st.markdown(
            f"<div style='font-size:14px;font-weight:700;color:{_TXT};"
            f"margin-bottom:6px'>⚡ 今日操作简报（{_sel_cfg['label']}） &nbsp; {_gen_tag}</div>",
            unsafe_allow_html=True,
        )
    with _btn_col:
        if st.button("🔄 重新生成", key="war_regen_briefing", use_container_width=True,
                     help="用这个账户当前的实时持仓重新生成今日简报"):
            import _cascade
            with st.spinner("生成中…"):
                _regen = _cascade._get_am()["_generate_and_save_daily_briefing"](_ACCT_ID)
            # 失败必须说出来。顶部 BD/压力测试全部读自这份存库快照，生成失败
            # 时页面会原样显示上一份旧数据，静默吞异常就等于谎报"已刷新"。
            if isinstance(_regen, dict) and not _regen.get("ok"):
                st.error(
                    f"❌ 简报生成失败，顶部风险数字仍是上一份旧快照："
                    f"{_regen.get('error') or '未知错误'}"
                )
            else:
                _load_war_data.clear()
                st.rerun()

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
            st.caption(f"另有 {len(_recs)-5} 条 → 🏦 账户监控 › 交易建议")
    elif _brief:
        st.markdown(
            f"<div style='color:{_G};padding:12px;background:{_G}11;"
            f"border-radius:6px;font-size:13px'>✅ 今日无紧急操作建议</div>",
            unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div style='color:{_MUT};padding:12px;background:{_SURF};"
            f"border:1px dashed {_BDR};border-radius:6px;font-size:12px'>"
            f"简报将在 09:35 ET 自动生成，也可在 🏦 账户监控 页手动触发</div>",
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
            _lbl = _ACCT_LABEL.get(_tid, _tid)
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
                    f"letter-spacing:1px;margin-bottom:6px'>{_lbl}"
                    f"<span style='float:right;text-transform:none;letter-spacing:0'>"
                    f"同步于 {_ac.get('time', '')}</span></div>"
                    f"{_rows_html}</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.markdown(
            f"<div style='color:{_MUT};font-size:12px;padding:12px;"
            f"background:{_SURF};border:1px dashed {_BDR};border-radius:8px'>"
            f"{_ACCT_LABEL.get(_ACCT_ID, _ACCT_ID)} 还没有同步过数据 — "
            f"请在 🏦 账户监控 页同步或上传 CSV</div>",
            unsafe_allow_html=True,
        )

# ─── 行 2.5：持仓论点监控（跟上面的机械层信号是两回事，见函数注释）───
_thesis_alerts = _load_thesis_alerts(_ACCT_ID)
if _thesis_alerts:
    st.markdown(
        f"<div style='font-size:14px;font-weight:700;color:{_TXT};"
        f"margin:14px 0 6px'>🔎 持仓论点监控</div>",
        unsafe_allow_html=True,
    )
    for _t in _thesis_alerts:
        _cs = _t["case_state"]
        _lc = _R if _cs in SEVERE_STATES else _A
        _rule_text = "；".join(
            f"{r.rule_id}: {r.reason}" for r in _t["triggered_rules"]
        )
        st.markdown(
            f"<div style='border-left:3px solid {_lc};padding:5px 10px;"
            f"margin:3px 0;background:{_lc}0d;border-radius:0 5px 5px 0'>"
            f"<span style='color:{_lc};font-weight:700;font-size:12px'>{_cs}</span>"
            f"<span style='color:{_TXT};font-weight:600;font-size:13px;"
            f"margin:0 6px'>{_t['ticker']}</span>"
            + (f"<span style='color:{_MUT};font-size:11px'>{_rule_text}</span>"
               if _rule_text else "")
            + f"<div style='color:{_MUT};font-size:11px;margin-top:2px'>"
            f"详情 → ⚖️ 仓位管理</div></div>",
            unsafe_allow_html=True,
        )

# ─── 行 3：快捷入口 ─────────────────────────────────
st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
_n1, _n2, _n3, _n4, _n5 = st.columns(5, gap="small")
with _n1:
    st.page_link("app.py", label="📊 AI估值排行榜", use_container_width=True)
with _n2:
    st.page_link("options_module.py", label="📈 期权分析", use_container_width=True)
with _n3:
    st.page_link("account_monitor.py", label="🏦 持仓详情", use_container_width=True)
with _n4:
    st.page_link("pages/5_⚖️_仓位管理.py", label="⚖️ 仓位管理", use_container_width=True)
with _n5:
    st.page_link("account_monitor.py", label="⚙️ 数据管理", use_container_width=True)
