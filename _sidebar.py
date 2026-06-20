"""
_sidebar.py — ENERGREX 共享侧边栏
所有页面 import 后调用 render() 即可渲染统一侧边栏。
"""
import streamlit as st
import sqlite3, json, pathlib, socket, datetime

_ROOT = pathlib.Path(__file__).parent
_DB   = _ROOT / "data" / "energrex.db"

_G   = "#00D4AA"; _R  = "#FF4B6E"; _A  = "#FFB347"
_MUT = "#8B9BB4"; _TXT = "#E2E8F0"; _SURF = "#0F1923"

_BD_LIMIT = 350.0


def _chrome_ok() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 9222), timeout=1)
        s.close()
        return True
    except Exception:
        return False


def _load_db() -> dict:
    out = {
        "equity": 0.0, "cash": 0.0, "pnl": 0.0,
        "sync_time": None, "bd_pct": 0.0, "theta": 0.0,
        "opt_updated": None,
    }
    try:
        conn = sqlite3.connect(str(_DB))
        conn.row_factory = sqlite3.Row

        bal = conn.execute(
            "SELECT total_equity, cash_balance, day_pnl, sync_time "
            "FROM account_balance ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if bal:
            out["equity"]    = float(bal["total_equity"]  or 0)
            out["cash"]      = float(bal["cash_balance"]  or 0)
            out["pnl"]       = float(bal["day_pnl"]       or 0)
            out["sync_time"] = (str(bal["sync_time"] or "")[:16]
                                .replace("T", " "))

        # 优先读 cascade 实时写入的 risk_cache.json，再降级到 daily_briefing 快照
        _rc_path = _ROOT / "data" / "risk_cache.json"
        if _rc_path.exists():
            try:
                _rc = json.loads(_rc_path.read_text())
                out["bd_pct"] = (_rc.get("beta_delta_ratio") or 0) * 100
                out["theta"]  = _rc.get("theta_per_day", 0) or 0
            except Exception:
                pass
        if out["bd_pct"] == 0.0:
            br = conn.execute(
                "SELECT snap_json FROM daily_briefing "
                "WHERE acct_id='account_1' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if br:
                snap = json.loads(br["snap_json"] or "{}")
                out["bd_pct"] = (snap.get("beta_delta_ratio") or 0) * 100
                out["theta"]  = snap.get("theta_per_day", 0) or 0

        opt = conn.execute(
            "SELECT MAX(last_updated) AS t FROM options_positions "
            "WHERE account_id='account_1'"
        ).fetchone()
        if opt and opt["t"]:
            out["opt_updated"] = str(opt["t"])[:16].replace("T", " ")

        conn.close()
    except Exception:
        pass
    return out


def _hhmm(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        return ts[11:16] + " ET"
    except Exception:
        return ts or "—"


def render() -> None:
    """渲染 ENERGREX 共享侧边栏。每个页面顶部调用一次。"""
    d      = _load_db()
    chrome = _chrome_ok()

    with st.sidebar:
        # ── 标题 ─────────────────────────────────────────
        st.markdown(
            f"<div style='font-size:22px;font-weight:800;"
            f"letter-spacing:2px;color:{_G};padding:4px 0'>⚡ ENERGREX</div>",
            unsafe_allow_html=True,
        )
        st.divider()

        # ── 导航 ─────────────────────────────────────────
        st.markdown(
            f"<div style='font-size:10px;color:{_MUT};text-transform:uppercase;"
            f"letter-spacing:1px;margin-bottom:2px'>📍 导航</div>",
            unsafe_allow_html=True,
        )
        st.page_link("home.py",                        label="作战室（主页）", icon="🏠")
        st.page_link("pages/1_📊_AI_估值评分.py",      label="AI 估值评分",   icon="📊")
        st.page_link("pages/2_📈_期权分析.py",         label="期权分析",       icon="📈")
        st.page_link("pages/3_🏦_账户监控.py",         label="账户监控",       icon="🏦")
        st.divider()

        # ── 数据更新 ─────────────────────────────────────
        st.markdown(
            f"<div style='font-size:10px;color:{_MUT};text-transform:uppercase;"
            f"letter-spacing:1px;margin-bottom:6px'>🔄 数据更新</div>",
            unsafe_allow_html=True,
        )

        # 同步账户
        _c1, _c2 = st.columns([3, 2])
        with _c1:
            _sync = st.button(
                "⚡ 同步账户",
                key="sb_sync",
                use_container_width=True,
                help=("从 Firstrade 同步余额 + 持仓（Chrome 未连接时自动启动）"
                      if not chrome else "从 Firstrade 同步余额 + 持仓"),
            )
        with _c2:
            st.markdown(
                f"<div style='font-size:10px;color:{_MUT};line-height:1.6;"
                f"margin-top:5px'>上次<br><b>{_hhmm(d['sync_time'])}</b></div>",
                unsafe_allow_html=True,
            )
        if _sync:
            import _cascade as _casc
            _summ: dict = {}
            with st.status("⚡ 账户同步中...", expanded=True) as _stat:
                def _sync_step(msg: str, _s=_stat) -> None:
                    st.write(msg)
                    _s.update(label=msg)
                try:
                    _summ = _casc.run_sync_cascade(step=_sync_step)
                    _stat.update(label="✅ 账户同步 + 级联完成", state="complete")
                except Exception as _e:
                    _stat.update(label=f"❌ 错误: {_e}", state="error")
            if _summ:
                _eq  = _summ.get("equity",       0)
                _bd  = _summ.get("bd_pct",        0)
                _pnl = _summ.get("pnl",           0)
                _sig = _summ.get("exit_signals",  0)
                st.success(
                    f"净值 ${_eq:,.0f} · BD {_bd:.0f}%"
                    f" · 盈亏 ${_pnl:+,.0f} · 出场信号 {_sig} 个"
                )
            st.cache_data.clear()
            st.rerun()

        # 更新行情
        _c1, _c2 = st.columns([3, 2])
        with _c1:
            _price = st.button(
                "📈 更新行情",
                key="sb_price",
                use_container_width=True,
                help="yfinance + MarketData 刷新股票和期权现价 / Greeks",
            )
        with _c2:
            st.markdown(
                f"<div style='font-size:10px;color:{_MUT};line-height:1.6;"
                f"margin-top:5px'>上次<br><b>{_hhmm(d['opt_updated'])}</b></div>",
                unsafe_allow_html=True,
            )
        if _price:
            import _cascade as _casc
            _summ2: dict = {}
            with st.status("📈 行情更新中...", expanded=True) as _stat2:
                def _price_step(msg: str, _s=_stat2) -> None:
                    st.write(msg)
                    _s.update(label=msg)
                try:
                    _summ2 = _casc.run_price_cascade(step=_price_step)
                    _stat2.update(label="✅ 行情更新 + 级联完成", state="complete")
                except Exception as _e:
                    _stat2.update(label=f"❌ 错误: {_e}", state="error")
            if _summ2:
                _eq2  = _summ2.get("equity",      0)
                _bd2  = _summ2.get("bd_pct",       0)
                _pnl2 = _summ2.get("pnl",          0)
                _sig2 = _summ2.get("exit_signals", 0)
                st.success(
                    f"净值 ${_eq2:,.0f} · BD {_bd2:.0f}%"
                    f" · 盈亏 ${_pnl2:+,.0f} · 出场信号 {_sig2} 个"
                )
            st.cache_data.clear()
            st.rerun()

        st.divider()

        # ── 风险状态 ─────────────────────────────────────
        st.markdown(
            f"<div style='font-size:10px;color:{_MUT};text-transform:uppercase;"
            f"letter-spacing:1px;margin-bottom:6px'>⚠️ 风险状态</div>",
            unsafe_allow_html=True,
        )
        _bd  = d["bd_pct"]
        _col = _R if _bd > _BD_LIMIT else _A if _bd > _BD_LIMIT * 0.85 else _G
        st.markdown(
            f"<div style='font-size:12px;margin-bottom:3px'>"
            f"<span style='color:{_MUT}'>BD </span>"
            f"<span style='color:{_col};font-weight:700'>{_bd:.0f}%</span>"
            f"<span style='color:{_MUT}'> / {_BD_LIMIT:.0f}%上限</span></div>",
            unsafe_allow_html=True,
        )
        st.progress(min(_bd / _BD_LIMIT, 1.0))
        _cash_col = _G if d["cash"] >= 0 else _R
        st.markdown(
            f"<div style='font-size:11px;color:{_MUT};margin-top:4px'>"
            f"Theta <span style='color:{_TXT}'>${d['theta']:+,.0f}/天</span>"
            f"&nbsp;&nbsp;"
            f"现金 <span style='color:{_cash_col}'>${d['cash']:,.0f}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        # Chrome 连接状态
        _cdp_col = _G if chrome else _R
        _cdp_lbl = "Chrome CDP ✅ 已连接" if chrome else "Chrome CDP ❌ 未连接"
        st.markdown(
            f"<div style='font-size:10px;color:{_cdp_col};margin-top:6px'>"
            f"{_cdp_lbl}</div>",
            unsafe_allow_html=True,
        )
