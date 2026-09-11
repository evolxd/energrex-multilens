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

# ── 维度定义 ────────────────────────────────────────────────
# DIMENSIONS：所有会被记录进 discipline_signals 的维度（含"止盈纪律"——
#   v2 里它只做展示 + 喂复合信号，不算纪律分，但历史还记）。
# SCORED_DIMENSIONS：进百分制纪律分的 A 类维度（设计见
#   docs/DISCIPLINE_AND_REVIEW_ARCHITECTURE.md §1）。v1 只有 4 个里的 3 个
#   （止盈踢出去了）；其余 12 个的扫描源在 account/risk_signals.py，
#   随 stage 2/3 接进来，这里先把维度名和权重定死。
DIMENSIONS = (
    "止盈纪律", "止损纪律", "到期处理", "对冲纪律",
    "单票超限", "集中度超限", "现金底线", "流动性天数",
    "杠杆超限", "压力测试超红线", "强制去风险",
    "熔断票交易", "开仓恶化breach", "偏离Kelly", "无case交易",
    "止盈回调复合",
)

SCORED_DIMENSIONS = tuple(d for d in DIMENSIONS if d != "止盈纪律")

# 三档权重：按"忽视它的最大伤害"分（设计文档 §2.3）
DIMENSION_WEIGHT: dict[str, int] = {
    # 高 (3)：忽视 = 灾难性 / 不可逆损失
    "止损纪律": 3, "杠杆超限": 3, "压力测试超红线": 3,
    "强制去风险": 3, "止盈回调复合": 3,
    # 中 (2)：忽视 = 风险画像恶化，但不至于当场爆
    "到期处理": 2, "对冲纪律": 2, "单票超限": 2, "集中度超限": 2,
    "熔断票交易": 2, "开仓恶化breach": 2,
    # 低 (1)：忽视 = 次优但可恢复
    "现金底线": 1, "流动性天数": 1, "偏离Kelly": 1, "无case交易": 1,
}

# 门④违规类——下单即违规、是你造成的，事件分上限压到 0.75（其余上限 1.0）
MEN4_VIOLATION_DIMENSIONS = {"开仓恶化breach", "偏离Kelly", "无case交易", "熔断票交易"}

# 响应窗口（设计文档 §5，v1 默认，跑真实数据再调）。"到期处理"没有固定
# 天数窗口——规则是 DTE=0 前处理完，由 record_and_resolve_signals 单独判，
# 所以是 None。
RESPONSE_WINDOW_DAYS: dict[str, int | None] = {
    "止盈纪律": 2,
    "止损纪律": 2,
    "到期处理": None,
    "对冲纪律": 1,
    "单票超限": 2, "集中度超限": 2, "现金底线": 2, "流动性天数": 2,
    "杠杆超限": 2, "压力测试超红线": 2, "强制去风险": 2,
    "止盈回调复合": 3,
    "开仓恶化breach": 3, "偏离Kelly": 3,
    "无case交易": 5, "熔断票交易": 5,
}

# 事件分时间衰减：迟响应从上限起，每迟一个自然日扣 DECAY_RATE，扣满归 0
DECAY_RATE = 0.15  # → 迟满约 7 天（1.0/0.15≈6.7）事件分归 0

# 纪律分分档（百分制，90% 及格）
GRADE_TIERS = (
    (95.0, "优秀"),
    (90.0, "及格"),
    (80.0, "警示"),
    (0.0,  "严重失守"),
)


def _cap_for(dimension: str) -> float:
    """门④违规类事件分上限 0.75，其余 1.0。"""
    return 0.75 if dimension in MEN4_VIOLATION_DIMENSIONS else 1.0


def event_score(dimension: str, status: str, response_days: int | None) -> float:
    """一个信号"了结"那刻的事件分（设计文档 §2.2）。

    - 窗口内你真的调了仓 → 上限（1.0 或门④违规的 0.75）
    - 超窗口后才调（迟响应）→ max(0, 上限 − DECAY_RATE × 迟了几天)
    - 自动漂回 / 从没解决 / 到期被动了结 → 0（不响应就是不响应，运气不洗白）

    still-open 的行不调这个（事件分留 NULL，汇总时当临时 0）。
    """
    if status != "acted":
        return 0.0
    window = RESPONSE_WINDOW_DAYS.get(dimension) or 0
    days_late = max(0, (response_days or 0) - window)
    if days_late == 0:
        return _cap_for(dimension)
    return max(0.0, _cap_for(dimension) - DECAY_RATE * days_late)


def _grade(score: float) -> str:
    for threshold, label in GRADE_TIERS:
        if score >= threshold:
            return label
    return GRADE_TIERS[-1][1]


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


_NON_TICKER_SYMBOLS = {"PORTFOLIO", "QQQ_HEDGE_MISSING"}


def _find_acted_evidence(conn, acct_id: str, row) -> tuple[str | None, str | None]:
    """一个信号不再触发了，找"你真的做了动作"的证据（设计文档 §3）。
    返回 (动作日期, 说明) 或 (None, None)。

    顺序：
      1. option_realized_trades 里同 symbol、first_seen 之后的平仓（v1 老逻辑）
      2. transactions 里的减仓交易——能归标的的看那个标的的 SELL；
         PORTFOLIO / 产业链名 / 合成 symbol 看窗口内任意一笔 SELL
         （组合层面 = 有没有做任何降风险的事）
    """
    sym = row["symbol"]
    fs  = str(row["first_seen_date"])

    t = conn.execute(
        "SELECT symbol, close_date FROM option_realized_trades "
        "WHERE account_id=? AND symbol=? AND close_date > ? "
        "ORDER BY close_date ASC LIMIT 1",
        (acct_id, sym, fs),
    ).fetchone()
    if t:
        return str(t["close_date"]), f"{t['symbol']} 平仓于 {t['close_date']}"

    from account.risk_signals import underlying_of
    import re as _re
    # 能归标的的 symbol：纯股票代码，或 OCC 期权代码。其余（PORTFOLIO、
    # 产业链名"AI芯片"、合成 symbol）都当组合层面。
    is_ticker_like = bool(_re.match(r"^[A-Z]{1,6}(\d{6}[CP]\d{8})?$", sym or ""))
    if not is_ticker_like or sym in _NON_TICKER_SYMBOLS:
        tx = conn.execute(
            "SELECT trade_date, symbol FROM transactions "
            "WHERE account_id=? AND type='SELL' AND trade_date > ? "
            "ORDER BY trade_date ASC LIMIT 1",
            (acct_id, fs),
        ).fetchone()
        if tx:
            return str(tx["trade_date"]), f"窗口内减仓 {tx['symbol']} 于 {tx['trade_date']}（组合层面降风险）"
        return None, None

    und = underlying_of(sym)
    tx = conn.execute(
        "SELECT trade_date, symbol FROM transactions "
        "WHERE account_id=? AND type='SELL' AND trade_date > ? "
        "AND (symbol=? OR symbol LIKE ?) ORDER BY trade_date ASC LIMIT 1",
        (acct_id, fs, und, f"{und}%"),
    ).fetchone()
    if tx:
        return str(tx["trade_date"]), f"减仓 {tx['symbol']} 于 {tx['trade_date']}"
    return None, None


def record_and_resolve_signals(
    acct_id: str,
    pnl_dte_signals: list[dict],
    hedge_governance: dict | None,
    today: _dt.date | None = None,
    extra_signals: list[dict] | None = None,
) -> dict:
    """设计文档 §4 的记录/核对算法，每次账户同步调一次。

    pnl_dte_signals: scan_pnl_dte_signals() 的结果。
    hedge_governance: account.risk.compute_qqq_hedge_plan(...) 返回值里的
        "hedge_governance" 键——调用方已经算好，这个函数不自己拉取。
    extra_signals: v2 的其余信号（account/risk_signals.py 的输出——硬约束/
        风险快照/门④/回调复合）。每项 {symbol, dimension, detail}，门④违规类
        可带 "first_seen" 键把首次日期定成交易日而不是 today。

    返回本次同步的变更摘要：
    {"new": N, "acted": N, "self_resolved": N, "expired_unhandled": N, "still_open": N}
    """
    today   = today or _dt.date.today()
    today_s = today.isoformat()
    current = (list(pnl_dte_signals)
               + hedge_governance_signals(hedge_governance)
               + list(extra_signals or []))

    conn = _db()
    conn.row_factory = sqlite3.Row
    summary = {"new": 0, "acted": 0, "self_resolved": 0, "expired_unhandled": 0, "still_open": 0}

    current_keys: set[tuple[str, str]] = set()
    for sig in current:
        dim, sym, detail = sig["dimension"], sig["symbol"], sig.get("detail", "")
        current_keys.add((dim, sym))
        first_seen_s = str(sig.get("first_seen") or today_s)[:10]
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
                    (acct_id, dim, sym, first_seen_s, today_s, detail),
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
        acted_date, acted_via = _find_acted_evidence(conn, acct_id, row)
        if acted_date:
            first_seen = _dt.date.fromisoformat(str(row["first_seen_date"])[:10])
            try:
                resp_days = (_dt.date.fromisoformat(str(acted_date)[:10]) - first_seen).days
            except Exception:
                resp_days = None
            _es = event_score(row["dimension"], "acted", resp_days)
            conn.execute(
                "UPDATE discipline_signals SET status='acted', resolved_date=?, "
                "resolved_via=?, response_days=?, event_score=? WHERE id=?",
                (str(acted_date), acted_via, resp_days, _es, row["id"]),
            )
            summary["acted"] += 1
        else:
            conn.execute(
                "UPDATE discipline_signals SET status='self_resolved', resolved_date=?, "
                "event_score=0.0 WHERE id=?",
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
                    "resolved_date=?, event_score=0.0 WHERE id=?",
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


def compute_discipline_score(
    acct_id: str,
    since: _dt.date | None = None,
    until: _dt.date | None = None,
) -> dict:
    """设计文档 §2：百分制纪律分（合规率），90% 及格。

    纪律分 = Σ(事件分 × 维度权重) / Σ(维度权重) × 100%，下限 0（没有负分，
    严重程度靠三档权重表达）。

    计入的事件：
      - 周期内 first_seen 的、已了结的信号（acted/self_resolved/expired）——
        用存的 event_score（老行为空时现算）
      - 周期内 first_seen 的、还 open 且**已超响应窗口**的信号——算临时 0，
        一直拖累直到了结（窗口内还没超期的 open 信号不算，你还有时间）
      - review_tag 是"有意例外"/"不认同信号"的整条剔除（§6：书面豁免）

    返回 {score, grade, n_events, by_dimension:{dim:{score,n,weight}},
    pending_review}。
    """
    until = until or _dt.date.today()
    conn = _db()
    conn.row_factory = sqlite3.Row

    total_weighted = 0.0
    total_weight   = 0.0
    n_events       = 0
    pending_review = 0
    by_dimension: dict[str, dict] = {}

    for dim in SCORED_DIMENSIONS:
        weight = DIMENSION_WEIGHT.get(dim, 1)
        window = RESPONSE_WINDOW_DAYS.get(dim)

        where  = "account_id=? AND dimension=?"
        params: list = [acct_id, dim]
        if since:
            where += " AND first_seen_date >= ?"
            params.append(since.isoformat())
        where += " AND first_seen_date <= ?"
        params.append(until.isoformat())

        rows = conn.execute(
            f"SELECT status, response_days, event_score, review_tag, first_seen_date "
            f"FROM discipline_signals WHERE {where}",
            params,
        ).fetchall()

        dim_scores: list[float] = []
        for r in rows:
            if r["review_tag"] in ("有意例外", "不认同信号"):
                continue  # §6 书面豁免，整条剔除
            status = r["status"]
            if status in ("acted", "self_resolved", "expired_unhandled"):
                es = r["event_score"]
                if es is None:
                    es = event_score(dim, status, r["response_days"])
                dim_scores.append(float(es))
                if float(es) < _cap_for(dim) and not r["review_tag"]:
                    pending_review += 1
            elif status == "open" and window is not None:
                # 还 open 且已超响应窗口 → 算临时 0，一直拖累直到了结。
                # 窗口内还没超期的不算（你还有时间）；到期处理 window 是
                # None，open 期间不算 0。
                fs = _dt.date.fromisoformat(str(r["first_seen_date"])[:10])
                if (until - fs).days > window:
                    dim_scores.append(0.0)

        if dim_scores:
            dim_avg = sum(dim_scores) / len(dim_scores)
            by_dimension[dim] = {
                "score": round(dim_avg * 100, 1),
                "n": len(dim_scores),
                "weight": weight,
            }
            total_weighted += dim_avg * weight
            total_weight   += weight
            n_events       += len(dim_scores)

    score = (total_weighted / total_weight * 100) if total_weight else None
    score = max(0.0, round(score, 1)) if score is not None else None

    conn.close()
    return {
        "score": score,
        "grade": _grade(score) if score is not None else None,
        "n_events": n_events,
        "since": since.isoformat() if since else None,
        "until": until.isoformat(),
        "by_dimension": by_dimension,
        "pending_review": pending_review,
    }


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


# ── 每周复核（设计文档 §6） ─────────────────────────────────────
REVIEW_TAGS = ("有意例外", "漏了", "不认同信号")


def get_review_queue(
    acct_id: str,
    since: _dt.date | None = None,
    until: _dt.date | None = None,
) -> list[dict]:
    """待复核清单：周期内已了结、事件分没到上限（迟响应/自然消失/到期未
    处理）、且还没标注过的信号。给每周复核页面用。
    """
    until = until or _dt.date.today()
    conn = _db()
    conn.row_factory = sqlite3.Row
    where  = "account_id=? AND review_tag IS NULL AND status IN " \
             "('acted','self_resolved','expired_unhandled')"
    params: list = [acct_id]
    if since:
        where += " AND first_seen_date >= ?"
        params.append(since.isoformat())
    where += " AND first_seen_date <= ?"
    params.append(until.isoformat())
    rows = conn.execute(
        f"SELECT id, dimension, symbol, first_seen_date, resolved_date, status, "
        f"response_days, event_score, detail FROM discipline_signals WHERE {where} "
        f"ORDER BY first_seen_date DESC",
        params,
    ).fetchall()
    conn.close()

    out: list[dict] = []
    for r in rows:
        es = r["event_score"]
        if es is None:
            es = event_score(r["dimension"], r["status"], r["response_days"])
        if float(es) >= _cap_for(r["dimension"]):
            continue  # 满分事件不用复核
        out.append({
            "id": r["id"], "dimension": r["dimension"], "symbol": r["symbol"],
            "first_seen_date": r["first_seen_date"], "resolved_date": r["resolved_date"],
            "status": r["status"], "response_days": r["response_days"],
            "event_score": round(float(es), 2), "detail": r["detail"],
        })
    return out


def set_review_annotation(signal_id: int, tag: str, note: str = "") -> None:
    """给一条信号打复核标注。"有意例外"/"不认同信号"会让它在
    compute_discipline_score 里整条剔除（§6 书面豁免）；"漏了"不改分。
    """
    if tag not in REVIEW_TAGS:
        raise ValueError(f"未知复核标注：{tag}")
    conn = _db()
    conn.execute(
        "UPDATE discipline_signals SET review_tag=?, review_note=? WHERE id=?",
        (tag, note or None, signal_id),
    )
    conn.commit()
    conn.close()
