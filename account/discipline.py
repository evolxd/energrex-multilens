"""
account/discipline.py — 门⑤纪律：信号响应记录 + 分维度打分

设计见 docs/DISCIPLINE_GATE_DESIGN.md。核心判断：一个信号不再触发了，只有
找到对应的真实交易记录（option_realized_trades 里同 symbol、close_date
在 first_seen_date 之后）才算"已响应"（acted）；信号自己消失（比如止损
信号因为股价自己反弹而不再触发）算"自然消失"（self_resolved）——两者是
不同的结果，不能混为一谈，self_resolved 不计入纪律执行率的分子。

本轮做 4 个维度：止盈纪律/止损纪律/到期处理/对冲纪律。论点纪律留到二期
（见设计文档 §2）。

已知局限（设计文档没展开、写代码时才发现的）：对冲纪律里 MISSING_HEDGE
（该开新对冲但没开）这种情况，正确的"响应"是开一笔新仓位，不是平仓——
option_realized_trades 只记录已平仓的交易，检测不到"开了一笔新对冲"这个
动作。这类信号目前只能靠 self_resolved（触发条件自己消失，比如BD降回
限额内）来关闭，不会被标成 acted。以后如果要处理这个，得改成额外看
positions 表有没有新增匹配的行。
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

from account.db import db as _db

# ── 维度定义（设计文档 §2） ──────────────────────────────────
DIMENSIONS = ("止盈纪律", "止损纪律", "到期处理", "对冲纪律")

# 响应窗口——用户已确认按这几个数字实现。"到期处理"没有固定天数窗口，
# 规则是"必须在 DTE=0 前处理完"，由 record_and_resolve_signals 单独判断，
# 不走这个字典的"超过N天算超期"逻辑，所以这里是 None。
RESPONSE_WINDOW_DAYS: dict[str, int | None] = {
    "止盈纪律": 2,
    "止损纪律": 2,
    "到期处理": None,
    "对冲纪律": 1,
}


def scan_pnl_dte_signals(acct_id: str, today: _dt.date | None = None) -> list[dict]:
    """止盈/止损/到期三个维度扫描，阈值跟 _cascade.py::_scan_exit_signals
    完全一样，但不去重——那边是给UI摘要用的，按symbol折叠成一条；这里要保留
    同一个symbol身上的每一种信号类型（比如同时是止盈信号又快到期）。
    """
    today = today or _dt.date.today()
    conn = _db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT symbol, quantity, unit_cost, current_price, total_pnl, expiry "
        "FROM options_positions WHERE account_id=?",
        (acct_id,),
    ).fetchall()
    conn.close()

    out: list[dict] = []
    for o in rows:
        sym  = str(o["symbol"] or "")
        q    = float(o["quantity"]  or 0)
        cost = float(o["unit_cost"] or 0)
        pnl  = float(o["total_pnl"] or 0)

        if cost != 0 and q != 0:
            basis   = abs(cost) * abs(q) * 100
            pnl_pct = pnl / basis if basis else 0
            if q < 0 and pnl_pct >= 0.50:
                out.append({"symbol": sym, "dimension": "止盈纪律",
                            "detail": f"空期权盈利 {pnl_pct*100:.0f}%，建议回补"})
            elif q > 0 and pnl_pct >= 1.0:
                out.append({"symbol": sym, "dimension": "止盈纪律",
                            "detail": f"多期权盈利 {pnl_pct*100:.0f}%，建议部分兑现"})
            elif q > 0 and pnl_pct <= -0.50:
                out.append({"symbol": sym, "dimension": "止损纪律",
                            "detail": f"多期权亏损 {pnl_pct*100:.0f}%，建议止损"})

        try:
            exp = _dt.date.fromisoformat(str(o["expiry"] or ""))
            dte = (exp - today).days
            if 0 <= dte <= 7:
                out.append({"symbol": sym, "dimension": "到期处理",
                            "detail": f"距到期 {dte} 天，建议处理"})
        except Exception:
            pass
    return out


def hedge_governance_signals(hedge_governance: dict | None) -> list[dict]:
    """把 account.hedge_governance.evaluate_protective_put_hedges() 的返回值
    转成信号列表。调用方（_cascade.py）已经算好这个 dict 传进来——这个函数
    自己不拉取任何数据，跟 account.risk.compute_qqq_hedge_plan 一样保持"纯"。
    """
    if not hedge_governance:
        return []
    status = hedge_governance.get("status", "")
    if status in ("NO_HEDGE_NEEDED", "VALID_HEDGE"):
        return []

    out: list[dict] = []
    rows = hedge_governance.get("rows") or []
    for row in rows:
        if row.get("status") == "VALID_HEDGE":
            continue
        codes = ", ".join(i["code"] for i in row.get("issues", []))
        out.append({
            "symbol": row["symbol"], "dimension": "对冲纪律",
            "detail": f"{row.get('status')}: {codes}",
        })
    if not rows:
        # MISSING_HEDGE：该有保护性 put 但一张都没开，没有具体 symbol 可挂，
        # 用固定的合成 symbol——见模块顶部 docstring"已知局限"。
        out.append({
            "symbol": "QQQ_HEDGE_MISSING", "dimension": "对冲纪律",
            "detail": f"{status}: {hedge_governance.get('summary', '')}",
        })
    return out


def record_and_resolve_signals(
    acct_id: str,
    pnl_dte_signals: list[dict],
    hedge_governance: dict | None,
    today: _dt.date | None = None,
) -> dict:
    """设计文档 §4 的记录/核对算法，每次账户同步调一次。

    pnl_dte_signals: scan_pnl_dte_signals() 的结果。
    hedge_governance: account.risk.compute_qqq_hedge_plan(...) 返回值里的
        "hedge_governance" 键——调用方已经算好，这个函数不自己拉取。

    返回本次同步的变更摘要：
    {"new": N, "acted": N, "self_resolved": N, "expired_unhandled": N, "still_open": N}
    """
    today   = today or _dt.date.today()
    today_s = today.isoformat()
    current = list(pnl_dte_signals) + hedge_governance_signals(hedge_governance)

    conn = _db()
    conn.row_factory = sqlite3.Row
    summary = {"new": 0, "acted": 0, "self_resolved": 0, "expired_unhandled": 0, "still_open": 0}

    current_keys: set[tuple[str, str]] = set()
    for sig in current:
        dim, sym, detail = sig["dimension"], sig["symbol"], sig.get("detail", "")
        current_keys.add((dim, sym))
        row = conn.execute(
            "SELECT id FROM discipline_signals WHERE account_id=? AND dimension=? "
            "AND symbol=? AND status='open'",
            (acct_id, dim, sym),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE discipline_signals SET last_seen_date=?, detail=? WHERE id=?",
                (today_s, detail, row["id"]),
            )
        else:
            try:
                conn.execute(
                    "INSERT INTO discipline_signals "
                    "(account_id, dimension, symbol, first_seen_date, last_seen_date, "
                    " detail, status) VALUES (?,?,?,?,?,?,'open')",
                    (acct_id, dim, sym, today_s, today_s, detail),
                )
                summary["new"] += 1
            except sqlite3.IntegrityError:
                # 同一天同一个信号已经插过一条（UNIQUE约束）——同一天多次
                # 同步（同步账户+更新行情都会跑这个）不会重复插入。
                pass

    # 核对：之前 open、这次同步没再出现的
    open_rows = conn.execute(
        "SELECT id, dimension, symbol, first_seen_date FROM discipline_signals "
        "WHERE account_id=? AND status='open'",
        (acct_id,),
    ).fetchall()
    for row in open_rows:
        if (row["dimension"], row["symbol"]) in current_keys:
            continue
        trade = conn.execute(
            "SELECT symbol, close_date FROM option_realized_trades "
            "WHERE account_id=? AND symbol=? AND close_date > ? "
            "ORDER BY close_date ASC LIMIT 1",
            (acct_id, row["symbol"], row["first_seen_date"]),
        ).fetchone()
        if trade:
            first_seen = _dt.date.fromisoformat(str(row["first_seen_date"])[:10])
            closed_s   = str(trade["close_date"])
            try:
                closed = _dt.date.fromisoformat(closed_s[:10])
                resp_days = (closed - first_seen).days
            except Exception:
                resp_days = None
            conn.execute(
                "UPDATE discipline_signals SET status='acted', resolved_date=?, "
                "resolved_via=?, response_days=? WHERE id=?",
                (closed_s, f"{trade['symbol']} 平仓于 {closed_s}", resp_days, row["id"]),
            )
            summary["acted"] += 1
        else:
            conn.execute(
                "UPDATE discipline_signals SET status='self_resolved', resolved_date=? "
                "WHERE id=?",
                (today_s, row["id"]),
            )
            summary["self_resolved"] += 1

    # 到期处理维度额外规则：DTE 到 0 那天如果还是 open，直接标
    # expired_unhandled，不用等下一轮同步判断有没有匹配交易——到期就是到期了。
    expiring = conn.execute(
        "SELECT id, symbol FROM discipline_signals "
        "WHERE account_id=? AND dimension='到期处理' AND status='open'",
        (acct_id,),
    ).fetchall()
    for row in expiring:
        exp_row = conn.execute(
            "SELECT expiry FROM options_positions WHERE account_id=? AND symbol=?",
            (acct_id, row["symbol"]),
        ).fetchone()
        if exp_row and exp_row["expiry"]:
            try:
                exp = _dt.date.fromisoformat(str(exp_row["expiry"])[:10])
            except Exception:
                continue
            if exp <= today:
                conn.execute(
                    "UPDATE discipline_signals SET status='expired_unhandled', "
                    "resolved_date=? WHERE id=?",
                    (today_s, row["id"]),
                )
                summary["expired_unhandled"] += 1

    conn.commit()
    summary["still_open"] = conn.execute(
        "SELECT COUNT(*) FROM discipline_signals WHERE account_id=? AND status='open'",
        (acct_id,),
    ).fetchone()[0]
    conn.close()
    return summary


def get_overdue_signals(acct_id: str, today: _dt.date | None = None) -> list[dict]:
    """还是 open、且已经超过对应维度响应窗口的信号——同步后弹提示用（方案A，
    见设计文档 §9）。到期处理维度没有"超过N天算超期"这个概念（规则是DTE=0
    前处理完，DTE还没到0之前都不算超期，到了0就直接是expired_unhandled、
    不再是open状态），所以这里跳过它，不会重复统计。
    """
    today = today or _dt.date.today()
    conn = _db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT dimension, symbol, first_seen_date FROM discipline_signals "
        "WHERE account_id=? AND status='open'",
        (acct_id,),
    ).fetchall()
    conn.close()

    out: list[dict] = []
    for r in rows:
        dim    = r["dimension"]
        window = RESPONSE_WINDOW_DAYS.get(dim)
        if window is None:
            continue
        first_seen = _dt.date.fromisoformat(str(r["first_seen_date"])[:10])
        days_open  = (today - first_seen).days
        if days_open > window:
            out.append({"dimension": dim, "symbol": r["symbol"],
                        "days_open": days_open, "window": window})
    return out


def compute_discipline_scores(
    acct_id: str,
    since: _dt.date | None = None,
    until: _dt.date | None = None,
) -> dict[str, dict]:
    """设计文档 §5：分维度返回响应率/平均响应天数/未处理最久一条，不合成
    一个总分——止损纪律95%但对冲纪律40%，平均成一个数只会把真正的短板
    藏起来，跟这周做Kelly分桶时学到的教训是同一件事。
    """
    until = until or _dt.date.today()
    conn = _db()
    conn.row_factory = sqlite3.Row

    out: dict[str, dict] = {}
    for dim in DIMENSIONS:
        where  = "account_id=? AND dimension=?"
        params: list = [acct_id, dim]
        if since:
            where += " AND first_seen_date >= ?"
            params.append(since.isoformat())
        where += " AND first_seen_date <= ?"
        params.append(until.isoformat())

        resolved = conn.execute(
            f"SELECT status, response_days FROM discipline_signals WHERE {where} "
            "AND status IN ('acted','self_resolved','expired_unhandled')",
            params,
        ).fetchall()
        n     = len(resolved)
        acted = [r for r in resolved if r["status"] == "acted"]
        response_rate = (len(acted) / n) if n else None
        acted_days = [r["response_days"] for r in acted if r["response_days"] is not None]
        avg_days   = (sum(acted_days) / len(acted_days)) if acted_days else None

        oldest_open = conn.execute(
            "SELECT symbol, first_seen_date FROM discipline_signals "
            "WHERE account_id=? AND dimension=? AND status='open' "
            "ORDER BY first_seen_date ASC LIMIT 1",
            (acct_id, dim),
        ).fetchone()
        oldest_days   = None
        oldest_symbol = None
        if oldest_open:
            fs = _dt.date.fromisoformat(str(oldest_open["first_seen_date"])[:10])
            oldest_days   = (until - fs).days
            oldest_symbol = oldest_open["symbol"]

        out[dim] = {
            "n": n,
            "since": since.isoformat() if since else None,
            "until": until.isoformat(),
            "response_rate": response_rate,
            "avg_response_days": avg_days,
            "oldest_open_symbol": oldest_symbol,
            "oldest_open_days": oldest_days,
        }
    conn.close()
    return out
