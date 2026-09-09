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

    # utf-8-sig, not utf-8: a stray BOM at the head of account_monitor.py
    # makes ast.parse below raise, which silently broke the 同步账户 button
    # until the BOM was found by hand. The page loader and the spread tests
    # already read this file BOM-tolerantly, so only this path was exposed.
    src   = _AM_SRC.read_text(encoding="utf-8-sig")
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


_DIM_TO_TYPE = {"止盈纪律": "止盈", "止损纪律": "止损", "到期处理": "到期"}


def _dedupe_signals_by_symbol(raw: list[dict]) -> list[dict]:
    """按symbol去重折叠成摘要用的旧格式——一个symbol同时踩两种信号
    （比如既止盈又快到期）不重复计数，只关心"多少个symbol有问题"。"""
    seen: set[str] = set()
    unique: list[dict] = []
    for s in raw:
        if s["symbol"] not in seen:
            seen.add(s["symbol"])
            unique.append({
                "symbol": s["symbol"],
                "type": _DIM_TO_TYPE.get(s["dimension"], s["dimension"]),
                "reason": s["detail"],
            })
    return unique


def _scan_exit_signals() -> list[dict]:
    """扫描 DB 中的出场信号：止盈 / 止损 / 临近到期（≤7 DTE），按symbol去重。

    2026-09-09：核心扫描（阈值/SQL）搬到了 account/discipline.py::
    scan_pnl_dte_signals——门⑤纪律记录要用同一份数据但**不能**去重折叠
    （一个symbol的止盈信号和到期信号是两个独立维度，各自要记录），这里
    以前是重复实现了一遍同样的阈值逻辑，现在改成调那边、自己只做去重这
    一层，不再有两份重复的阈值判断代码。`run_sync_cascade` 需要同时用到
    去重前/去重后两种视图，直接调 `account.discipline.scan_pnl_dte_signals`
    +`_dedupe_signals_by_symbol` 避免这个函数和门⑤记录各查一次库；这个
    函数留给 `run_price_cascade`（不需要门⑤记录）这种只要摘要计数的场景。
    """
    try:
        sys.path.insert(0, str(_ROOT))
        from account.discipline import scan_pnl_dte_signals
        raw = scan_pnl_dte_signals("account_1")
    except Exception as e:
        _log.warning(f"_scan_exit_signals: {e}")
        raw = []
    return _dedupe_signals_by_symbol(raw)


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

    # 4 ── 出场信号扫描 + 4.5 门⑤纪律记录（同一批 options_positions 数据，
    # 只查一次库：pnl_dte 是未去重的原始信号列表，_scan_exit_signals 摘要
    # 用的"去重计数"和门⑤记录用的"每种信号类型各自留一条"是同一份数据的
    # 两种视图，见 _dedupe_signals_by_symbol 的说明）。
    _s("🔄 扫描出场信号...")
    pnl_dte: list[dict] = []
    try:
        sys.path.insert(0, str(_ROOT))
        from account.discipline import scan_pnl_dte_signals
        pnl_dte = scan_pnl_dte_signals("account_1")
        sigs = _dedupe_signals_by_symbol(pnl_dte)
        summary["exit_signals"] = len(sigs)
        _s(f"✅ 出场信号 {len(sigs)} 个")
    except Exception as e:
        _s(f"⚠️ 出场信号扫描失败: {e}")

    # 4.5 ── 门⑤纪律：记录信号 + 核对上次的有没有真的响应
    # （docs/DISCIPLINE_GATE_DESIGN.md）。
    _s("🔄 记录纪律信号...")
    try:
        from account import discipline as _disc
        try:
            hedge_plan = am["_compute_qqq_hedge_plan"]("account_1")
            hedge_gov  = hedge_plan.get("hedge_governance") if isinstance(hedge_plan, dict) else None
        except Exception as _e_hedge:
            hedge_gov = None
            _s(f"⚠️ 对冲纪律检查失败（跳过这一维度，不影响其它三个）: {_e_hedge}")
        disc_summ = _disc.record_and_resolve_signals("account_1", pnl_dte, hedge_gov)
        summary["discipline"] = disc_summ
        _s(
            f"✅ 纪律信号：新增{disc_summ['new']} · 已响应{disc_summ['acted']} · "
            f"自然消失{disc_summ['self_resolved']} · 到期未处理{disc_summ['expired_unhandled']} · "
            f"仍未处理{disc_summ['still_open']}"
        )
    except Exception as e:
        _s(f"⚠️ 纪律信号记录失败: {e}")

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
