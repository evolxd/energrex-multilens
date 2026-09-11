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


def _gather_v2_risk_signals(snap: dict | None) -> list[dict]:
    """纪律架构 v2 的其余信号（account/risk_signals.py），每个子块自己
    try/except——一块挂了不影响其它块。返回 extra_signals 列表喂
    discipline.record_and_resolve_signals。设计见
    docs/DISCIPLINE_AND_REVIEW_ARCHITECTURE.md。
    """
    sys.path.insert(0, str(_ROOT))
    from account import risk_signals as _rs
    out: list[dict] = []

    # ── 风险快照类（BD/杠杆/压力测试/强制去风险）──
    # 2026-09-10 审计 F-16：max_bd/max_leverage/stress_redline 以前不传，
    # 静默用 risk_snapshot_signals() 自己的默认参数——跟 account_monitor.py
    # 的 _RISK_LIMITS（现在读受控注册表）完全脱节，改一处不会同步到另一处。
    # 这里跟 account_monitor.py 读同一份 _RISK_LIMITS，不再各自维护一份。
    try:
        _am_rl = _get_am()["_RISK_LIMITS"]
        out += _rs.risk_snapshot_signals(
            snap,
            max_bd=_am_rl["max_beta_delta_ratio"],
            max_leverage=_am_rl["max_leverage"],
            # stress_redline 检查的是 stress_20_ratio。2026-09-11 之前这里
            # 借用了 stress_hard_stop（-10%情景那条线）当 -20%情景的红线，
            # 是历史遗留的巧合，不是两者本该共用一条线。用户确认要给-20%
            # 情景一条独立的线后，改成读专门的 stress_20_hard_stop。
            stress_redline=_am_rl["stress_20_hard_stop"],
        )
    except Exception as e:
        _log.warning(f"risk_snapshot_signals: {e}")

    # ── 硬约束类 breach ──
    try:
        conn = sqlite3.connect(str(_DB)); conn.row_factory = sqlite3.Row
        pos = [dict(r) for r in conn.execute(
            "SELECT symbol, market_value FROM positions p1 WHERE p1.sync_time = "
            "(SELECT MAX(p2.sync_time) FROM positions p2 WHERE p2.symbol = p1.symbol)")]
        opts = [dict(r) for r in conn.execute(
            "SELECT symbol, market_value FROM options_positions")]
        bal = conn.execute("SELECT total_equity, cash_balance FROM account_balance "
                           "ORDER BY sync_time DESC LIMIT 1").fetchone()
        conn.close()
        equity = float(bal[0]) if bal and bal[0] else None
        cash   = float(bal[1]) if bal and bal[1] is not None else None

        from scoring.position_exposure import compute_exposures
        from scoring.position_limits import LIMIT_SPECS, effective_limit
        from scoring.mispricing_store import read_chain
        def _chain_of(sym):
            try:
                from scoring.scoring_engine import TICKER_CATEGORY
                c = TICKER_CATEGORY.get(sym)
                return c.value if c else None
            except Exception:
                return None
        exposures = compute_exposures(pos, equity, cash, _chain_of, opts)
        if exposures is not None:
            recs = read_chain(_ROOT / "data" / "position_limits.jsonl")
            now = datetime.datetime.now()
            limits = {s.key: effective_limit(recs, s.key, now) for s in LIMIT_SPECS}
            from scoring.position_exposure import breaches
            out += _rs.hard_constraint_signals(breaches(exposures, limits))
    except Exception as e:
        _log.warning(f"hard_constraint_signals: {e}")

    # ── 门④：事后从 transactions 检测 ──
    try:
        conn = sqlite3.connect(str(_DB)); conn.row_factory = sqlite3.Row
        _cut = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        # is_closing：Firstrade 自己的 description 里就标了 "OPEN CONTRACT"/
        # "CLOSING CONTRACT"（股票交易没有这个标记，NOT LIKE 两边都不中，
        # 自然是 False，即按"开仓"处理，跟原来对股票的行为一致）——比在这
        # 里重新用 FIFO 猜哪笔买入平了哪笔卖出可靠得多，也不用管同一天里
        # 数量、方向都一样的多笔交易怎么消歧。见 F-10。
        new_trades = [
            {"symbol": r["symbol"], "type": (r["type"] or "").upper(),
             "quantity": r["quantity"],
             "trade_date": _safe_date(r["trade_date"]),
             "is_closing": "CLOSING CONTRACT" in (r["description"] or "").upper()}
            for r in conn.execute(
                "SELECT symbol, type, quantity, trade_date, description FROM transactions "
                "WHERE account_id='account_1' AND trade_date >= ? AND type IN ('BUY','SELL')",
                (_cut,))
        ]
        # 硬约束在超限的日期集合（从 discipline_signals 的 open 区间算）
        hard_dims = ("单票超限", "集中度超限", "现金底线", "流动性天数")
        breach_dates: set = set()
        for r in conn.execute(
            "SELECT dimension, first_seen_date, resolved_date, status FROM discipline_signals "
            "WHERE account_id='account_1' AND dimension IN ({})".format(
                ",".join("?" * len(hard_dims))), hard_dims):
            d0 = _safe_date(r["first_seen_date"])
            d1 = _safe_date(r["resolved_date"]) if r["status"] != "open" else datetime.date.today()
            if d0 and d1:
                d = d0
                while d <= d1:
                    breach_dates.add(d); d += datetime.timedelta(days=1)
        conn.close()

        cases = set()
        _cases_f = _ROOT / "data" / "mispricing_cases.jsonl"
        if _cases_f.exists():
            import json as _json
            for line in _cases_f.read_text(encoding="utf-8").splitlines():
                try:
                    j = _json.loads(line)
                    t = (j.get("ticker") or j.get("symbol") or "").strip().upper()
                    if t:
                        cases.add(t)
                except Exception:
                    pass

        circuit = set()
        try:
            import csv as _csv
            _rv = _ROOT / "results_validated.csv"
            if _rv.exists():
                with open(_rv, encoding="utf-8-sig", newline="") as f:
                    for row in _csv.DictReader(f):
                        tk = (row.get("ticker") or "").strip().upper()
                        cv = str(row.get("circuit_triggered") or row.get("熔断") or "").strip().lower()
                        if tk and cv in ("true", "1", "yes", "是"):
                            circuit.add(tk)
        except Exception:
            pass

        neg_kelly = set()
        try:
            from account.performance import compute_performance_stats
            st = compute_performance_stats("account_1")
            for cs, v in (st or {}).get("by_combo", {}).items():
                k = v.get("kelly_f_shrunk")
                if k is not None and k <= 0:
                    neg_kelly.add(cs)
        except Exception:
            pass

        traded = _rs.traded_signals(
            new_trades, cases_on_file=cases, circuit_symbols=circuit,
            hard_breach_dates=breach_dates, negative_kelly_strategies=neg_kelly,
            strategy_of=None,  # v1 不做 symbol→strategy 映射，见 risk_signals 顶部
        )
        # 门④违规按交易日记 first_seen，不是 today——必须specifically找那笔
        # "买入开仓"的交易，不能随便拿这个标的名下随便一笔交易的日期填
        # 上去。同一标的窗口内常常既有平仓又有开仓（比如09-01平旧仓、
        # 09-03开新仓），F-10 修复前反正所有 BUY 都算违规、拿哪笔日期垫
        # 都一样；现在平仓不算违规了，如果还是"随便找第一笔"，一个09-03
        # 才真正开始的违规会被错误地标成09-01（那笔其实是平仓）就已经
        # 存在——response_days算的窗口就全错了。
        for s in traded:
            opens = [
                t for t in new_trades
                if _rs.underlying_of(t["symbol"]) == _rs.underlying_of(s["symbol"])
                and str(t.get("type") or "").upper() == "BUY"
                and not t.get("is_closing")
                and t.get("trade_date")
            ]
            if opens:
                s["first_seen"] = min(t["trade_date"] for t in opens).isoformat()
        out += traded
    except Exception as e:
        _log.warning(f"traded_signals: {e}")

    # ── 回调模块 + 止盈复合 ──
    try:
        conn = sqlite3.connect(str(_DB)); conn.row_factory = sqlite3.Row
        unds = {_rs.underlying_of(r["symbol"]) for r in conn.execute(
            "SELECT symbol FROM options_positions WHERE account_id='account_1'")}
        unds |= {r["symbol"].strip().upper() for r in conn.execute(
            "SELECT DISTINCT symbol FROM positions") if r["symbol"]}
        conn.close()
        unds = {u for u in unds if u and u.isascii()}
        bars = {}
        try:
            import yfinance as _yf
            for u in unds:
                try:
                    h = _yf.Ticker(u).history(period="6mo")
                    if not h.empty:
                        h = h.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
                        bars[u] = h.reset_index(drop=True)
                except Exception:
                    continue
        except Exception as e:
            _log.warning(f"pullback yfinance: {e}")
        if bars:
            pb = _rs.pullback_signals(bars)
            # 单独的 MAJOR 是 B 类（只展示，不进纪律分）——这里不记进台账；
            # 只有跟止盈复合的才记
            from account.discipline import scan_pnl_dte_signals
            pnl_dte_all = scan_pnl_dte_signals("account_1")
            out += _rs.compound_zhiying_pullback_signals(pnl_dte_all, pb)
    except Exception as e:
        _log.warning(f"pullback: {e}")

    return out


def _safe_date(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


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

    # 4.5 ── 门④+门⑤纪律：记录全部风控信号 + 核对上次的有没有真的响应
    # （docs/DISCIPLINE_AND_REVIEW_ARCHITECTURE.md）。
    _s("🔄 记录纪律信号...")
    try:
        from account import discipline as _disc
        try:
            hedge_plan = am["_compute_qqq_hedge_plan"]("account_1")
            hedge_gov  = hedge_plan.get("hedge_governance") if isinstance(hedge_plan, dict) else None
        except Exception as _e_hedge:
            hedge_gov = None
            _s(f"⚠️ 对冲纪律检查失败（跳过这一维度，不影响其它维度）: {_e_hedge}")
        extra = _gather_v2_risk_signals(snap)
        disc_summ = _disc.record_and_resolve_signals(
            "account_1", pnl_dte, hedge_gov, extra_signals=extra)
        summary["discipline"] = disc_summ
        _s(
            f"✅ 纪律信号（含硬约束/杠杆/门④/回调 {len(extra)} 条）：新增{disc_summ['new']} · "
            f"已响应{disc_summ['acted']} · 自然消失{disc_summ['self_resolved']} · "
            f"到期未处理{disc_summ['expired_unhandled']} · 仍未处理{disc_summ['still_open']}"
        )
        try:
            _score = _disc.compute_discipline_score(
                "account_1",
                since=datetime.date.today() - datetime.timedelta(days=30))
            if _score["score"] is not None:
                summary["discipline_score"] = _score["score"]
                _s(f"   纪律分（近30天）：{_score['score']}% · {_score['grade']}"
                   + (f" · {_score['pending_review']} 条待复核" if _score['pending_review'] else ""))
        except Exception:
            pass
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
