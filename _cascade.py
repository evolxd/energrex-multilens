"""
_cascade.py — ENERGREX 级联更新引擎
同步/行情更新后自动重算衍生数据：BD、Kelly、出场信号、操作简报。
在 Streamlit 进程内执行（无需 mock），可实时回调进度。
"""
import ast, datetime, logging, pathlib, sqlite3, sys

_ROOT = pathlib.Path(__file__).parent
_DB   = _ROOT / "data" / "energrex.db"
_log  = logging.getLogger("energrex.cascade")

_am: dict | None = None        # account_monitor 函数命名空间缓存
_am_mtime: float  = 0.0        # 上次加载时 account_monitor.py 的 mtime

_AM_SRC = _ROOT / "account_monitor.py"


def _get_am() -> dict:
    """
    AST 过滤 account_monitor.py：截止到 st.set_page_config 所在行之前，
    只加载函数定义，不执行 Streamlit 页面渲染代码。

    自动检测 mtime：account_monitor.py 有修改时热重载，
    无需重启 Streamlit 进程即可使代码变更生效。
    """
    global _am, _am_mtime
    current_mtime = _AM_SRC.stat().st_mtime
    if _am is not None and current_mtime == _am_mtime:
        return _am

    src   = _AM_SRC.read_text(encoding="utf-8")
    lines = src.splitlines()
    # 动态找 st.set_page_config 的行号（1-based）作为截止
    cutoff = next(
        (i + 1 for i, ln in enumerate(lines) if "st.set_page_config" in ln),
        len(lines) + 1,
    )
    tree = ast.parse(src, filename="account_monitor.py")
    filtered = ast.Module(
        body=[n for n in tree.body if getattr(n, "lineno", 0) < cutoff],
        type_ignores=[],
    )
    ast.fix_missing_locations(filtered)
    ns = {
        "__file__": str(_AM_SRC),
        "__name__": "account_monitor",
    }
    exec(compile(filtered, str(_AM_SRC), "exec"), ns)
    _am       = ns
    _am_mtime = current_mtime
    _log.info(f"[cascade] account_monitor 已加载（截止行 {cutoff}，mtime={current_mtime:.0f}）")
    return _am


def _scan_exit_signals() -> list[dict]:
    """扫描 DB 中的出场信号：止盈 / 止损 / 临近到期（≤7 DTE）。"""
    signals: list[dict] = []
    try:
        conn = sqlite3.connect(str(_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT symbol, quantity, unit_cost, current_price, total_pnl, expiry "
            "FROM options_positions WHERE account_id='account_1'"
        ).fetchall()
        today = datetime.date.today()
        for o in rows:
            sym   = str(o["symbol"]        or "")
            q     = float(o["quantity"]     or 0)
            cost  = float(o["unit_cost"]    or 0)
            pnl   = float(o["total_pnl"]   or 0)

            if cost != 0 and q != 0:
                basis   = abs(cost) * abs(q) * 100
                pnl_pct = pnl / basis if basis else 0
                if q < 0 and pnl_pct >= 0.50:
                    signals.append({"symbol": sym, "type": "止盈",
                                    "reason": f"空期权盈利 {pnl_pct*100:.0f}%，建议回补"})
                elif q > 0 and pnl_pct >= 1.0:
                    signals.append({"symbol": sym, "type": "止盈",
                                    "reason": f"多期权盈利 {pnl_pct*100:.0f}%，建议部分兑现"})
                elif q > 0 and pnl_pct <= -0.50:
                    signals.append({"symbol": sym, "type": "止损",
                                    "reason": f"多期权亏损 {pnl_pct*100:.0f}%，建议止损"})

            try:
                exp = datetime.date.fromisoformat(str(o["expiry"] or ""))
                dte = (exp - today).days
                if 0 <= dte <= 7:
                    signals.append({"symbol": sym, "type": "到期",
                                    "reason": f"距到期 {dte} 天，建议处理"})
            except Exception:
                pass

        conn.close()
    except Exception as e:
        _log.warning(f"_scan_exit_signals: {e}")

    seen: set[str] = set()
    unique: list[dict] = []
    for s in signals:
        if s["symbol"] not in seen:
            seen.add(s["symbol"]); unique.append(s)
    return unique


def run_sync_cascade(step=None) -> dict:
    """
    账户同步后的级联计算：
      sync → 重算BD → 重算Kelly → 扫描出场信号 → 更新操作简报

    step(msg: str): 每完成一步回调，用于 UI 进度显示。
    返回 summary dict: equity, bd_pct, pnl, exit_signals, kelly_strategies.
    """
    def _s(msg: str) -> None:
        _log.info(msg)
        if step:
            step(msg)

    summary: dict = {"equity": 0.0, "bd_pct": 0.0, "pnl": 0.0,
                     "exit_signals": 0, "kelly_strategies": 0}
    am = _get_am()

    # 0 ── 确保 Chrome 就绪（自动启动 + 登录检测）
    chrome_status = am["_ensure_chrome"](_s)
    if chrome_status == "needs_login":
        _s("⏸  同步暂停：请登录后再次点击「⚡ 同步账户」")
        return summary
    if chrome_status == "no_chrome":
        _s("❌ 无法启动 Chrome，同步取消")
        return summary

    # 1 ── 账户同步（含步骤 1.5 持仓对比）
    _s("⚡ 同步 Firstrade 账户数据...")
    try:
        am["_auto_sync"]("account_1")
        _s("✅ 账户余额同步完成")

        # 1.5 ── 展示持仓对比结果
        ss   = am["_sync_state"]()
        diff = ss.get("positions_diff") or {}
        summ = diff.get("summary", "持仓对比未执行")
        _s(f"📋 持仓对比: {summ}")
        for line in (diff.get("changes") or []):
            _s(f"   {line}")
        if not diff.get("ok") and diff.get("raw_url"):
            _s(f"   实际导航URL: {diff['raw_url']}")
    except Exception as e:
        _s(f"⚠️ 同步失败: {e}")

    # 1.6 ── 刷新期权现价 / Greeks（BD 计算依赖 delta，必须在重算前执行）
    _s("📈 刷新期权现价 / Greeks...")
    try:
        upd, dele = am["_refresh_options_prices"]("account_1")
        _s(f"✅ 期权 Greeks 更新 {upd} 条，到期删除 {dele} 条")
    except Exception as e:
        _s(f"⚠️ 期权 Greeks 刷新失败: {e}")

    # 2 ── 重算 BD
    _s("🔄 重算 BD（Beta-Delta 比率）...")
    snap: dict = {}
    try:
        snap = am["_compute_risk_snapshot"]("account_1")
        bd  = (snap.get("beta_delta_ratio") or 0) * 100
        eq  = float(snap.get("equity", 0) or 0)
        pnl = float(snap.get("day_pnl",  0) or 0)
        summary.update(equity=eq, bd_pct=bd, pnl=pnl)
        _s(f"✅ BD = {bd:.1f}%  净值 = ${eq:,.0f}")
        # 持久化给侧边栏读取（避免 daily_briefing 快照过期）
        import json as _json
        _snap_s = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in snap.items()}
        (_ROOT / "data" / "risk_cache.json").write_text(_json.dumps(_snap_s))
    except Exception as e:
        _s(f"⚠️ BD 重算失败: {e}")

    # 3 ── 重算 Kelly
    _s("🔄 重算 Kelly 仓位张数...")
    try:
        sys.path.insert(0, str(_ROOT))
        from exit_strategy_engine import weighted_kelly_by_strategy, save_kelly_to_db
        today = datetime.date.today()
        conn  = sqlite3.connect(str(_DB))
        conn.row_factory = sqlite3.Row
        stats = weighted_kelly_by_strategy(_DB, "account_1", today)
        if stats:
            n = save_kelly_to_db(conn, stats, "account_1", today)
            conn.commit()
            summary["kelly_strategies"] = n
            _s(f"✅ Kelly 更新 {n} 个策略")
        else:
            _s("⚠️ 无 combo_strategy 数据，Kelly 跳过")
        conn.close()
    except Exception as e:
        _s(f"⚠️ Kelly 计算失败: {e}")

    # 4 ── 出场信号扫描
    _s("🔄 扫描出场信号...")
    try:
        sigs = _scan_exit_signals()
        summary["exit_signals"] = len(sigs)
        _s(f"✅ 出场信号 {len(sigs)} 个")
    except Exception as e:
        _s(f"⚠️ 出场信号扫描失败: {e}")

    # 5 ── 更新操作简报
    _s("🔄 更新今日操作简报...")
    try:
        am["_generate_and_save_daily_briefing"]("account_1")
        _s("✅ 操作简报已更新")
    except Exception as e:
        _s(f"⚠️ 操作简报更新失败: {e}")

    return summary


def run_price_cascade(step=None) -> dict:
    """
    行情更新后的级联计算：
      期权现价 → 股票现价 → 重算BD → 扫描出场信号 → 更新操作简报

    step(msg: str): 每完成一步回调，用于 UI 进度显示。
    返回 summary dict: equity, bd_pct, pnl, exit_signals.
    """
    def _s(msg: str) -> None:
        _log.info(msg)
        if step:
            step(msg)

    summary: dict = {"equity": 0.0, "bd_pct": 0.0, "pnl": 0.0, "exit_signals": 0}
    am = _get_am()

    # 1 ── 期权现价
    _s("📈 拉取期权最新现价 / Greeks（MarketData）...")
    try:
        upd, dele = am["_refresh_options_prices"]("account_1")
        _s(f"✅ 期权更新 {upd} 条，到期删除 {dele} 条")
    except Exception as e:
        _s(f"⚠️ 期权现价失败: {e}")

    # 2 ── 股票现价
    _s("📈 拉取股票最新现价（yfinance）...")
    try:
        r = am["_refresh_stock_prices"]("account_1")
        n = r.get("updated", 0) if isinstance(r, dict) else 0
        _s(f"✅ 股票更新 {n} 只")
    except Exception as e:
        _s(f"⚠️ 股票现价失败: {e}")

    # 3 ── 重算 BD
    _s("🔄 重算 BD + 压力测试...")
    try:
        snap = am["_compute_risk_snapshot"]("account_1")
        bd  = (snap.get("beta_delta_ratio") or 0) * 100
        eq  = float(snap.get("equity", 0) or 0)
        pnl = float(snap.get("day_pnl",  0) or 0)
        summary.update(equity=eq, bd_pct=bd, pnl=pnl)
        _s(f"✅ BD = {bd:.1f}%  净值 = ${eq:,.0f}")
        import json as _json
        _snap_s = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in snap.items()}
        (_ROOT / "data" / "risk_cache.json").write_text(_json.dumps(_snap_s))
    except Exception as e:
        _s(f"⚠️ BD 重算失败: {e}")

    # 4 ── 出场信号
    _s("🔄 重跑出场信号（止盈/止损/临近到期）...")
    try:
        sigs = _scan_exit_signals()
        summary["exit_signals"] = len(sigs)
        _s(f"✅ 出场信号 {len(sigs)} 个")
    except Exception as e:
        _s(f"⚠️ 出场信号扫描失败: {e}")

    # 5 ── 更新操作简报
    _s("🔄 更新今日操作简报（含最新行情）...")
    try:
        am["_generate_and_save_daily_briefing"]("account_1")
        _s("✅ 操作简报已更新")
    except Exception as e:
        _s(f"⚠️ 操作简报更新失败: {e}")

    return summary
