"""读账户持仓/限额、算敞口——门③仓位管理和门④下场前验证共用的那一层。

2026-09-10：这些函数原来是 pages/5_⚖️_仓位管理.py 里的页面内私有函数
（_load_portfolio / _chain_of / _load_avg_dollar_volume），门④下场前验证
要问的是同一套硬约束"加了这笔之后会不会破线"，照抄一份等于把同一份读数
逻辑维护两遍。抽到这里，两个页面 import 同一份。

exposures_after_trade() 是新的：在现有持仓之上加一笔假设的新仓重算敞口，
这样门④的检查用的完全是门③同一套 compute_exposures/breaches，不另写一套
约束判断——两个页面对"超没超限"永远不会给出互相矛盾的答案。
"""
from __future__ import annotations

import datetime as dt

from scoring.position_exposure import Exposures, compute_exposures, underlying_of


def load_portfolio(db_factory=None):
    """最新一笔股票持仓、期权持仓、净值、现金、同步时间。

    期权必须算进来：这个账户绝大部分净值在价差里，只读股票表会报出
    "最大单票 5%，一切正常"，而实际上某一个标的占了半个账户。

    读不到数据返回空值而不是抛异常——限额设定那半个页面在首次同步之前
    也要能用。
    """
    try:
        if db_factory is None:
            from account.db import db as db_factory

        conn = db_factory()
        latest = conn.execute("SELECT MAX(sync_time) FROM positions").fetchone()
        sync_time = latest[0] if latest else None
        # 每个 symbol 取自己最新的那行，而不是"全局同一个 sync_time"——
        # _refresh_stock_prices() 是一只一只打上各自的 datetime.now()，
        # 老的精确匹配写法只会捞到最后更新的那一只（2026-08-27 修复，
        # 完整来龙去脉见 account/repository.py::load_positions）。
        rows = (
            [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT symbol, market_value FROM positions p1
                    WHERE p1.sync_time = (
                        SELECT MAX(p2.sync_time) FROM positions p2
                        WHERE p2.symbol = p1.symbol
                    )
                    """
                )
            ]
            if sync_time
            else []
        )
        options = [
            dict(r)
            for r in conn.execute(
                "SELECT symbol, market_value FROM options_positions"
            )
        ]
        bal = conn.execute(
            "SELECT total_equity, cash_balance FROM account_balance "
            "ORDER BY sync_time DESC LIMIT 1"
        ).fetchone()
        conn.close()
        equity = float(bal[0]) if bal and bal[0] else None
        cash = float(bal[1]) if bal and bal[1] is not None else None
        return rows, options, equity, cash, sync_time
    except Exception as exc:  # pragma: no cover - defensive UI path
        return [], [], None, None, f"读取失败：{exc}"


def chain_of(symbol: str) -> str | None:
    """标的 → 产业链名。不在 TICKER_CATEGORY 里返回 None（调用方会计入"未分类"）。

    2026-08-27 修过一个静默 bug：这里曾经 `from scoring_engine import ...`
    （扁平写法），而文件实际在 scoring/scoring_engine.py，异常被裸 except
    吞掉，导致每一只标的——包括 NVDA 这种分类明确的——永远落进"未分类"，
    产业链集中度限额从写下那天起量的就不是产业链。
    """
    try:
        from scoring.scoring_engine import TICKER_CATEGORY

        cat = TICKER_CATEGORY.get(symbol)
        return cat.value if cat else None
    except Exception:
        return None


def load_avg_dollar_volume(tickers: tuple[str, ...]) -> dict[str, float]:
    """每个标的近 20 个交易日的日均成交额。

    调用方负责加缓存（页面上用 st.cache_data(ttl=3600)）——这是股票的流动性
    特征，不是分钟级会变的东西，但每个标的一次 yfinance 调用，不缓存很慢。
    """
    import yfinance as yf

    result: dict[str, float] = {}
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period="1mo")
            if hist.empty:
                continue
            dollar_vol = (hist["Close"] * hist["Volume"]).dropna()
            if len(dollar_vol):
                result[ticker] = float(dollar_vol.tail(20).mean())
        except Exception:
            continue
    return result


def underlyings_in(positions: list[dict], option_positions: list[dict] | None) -> tuple[str, ...]:
    """持仓里出现过的所有标的（期权按 OCC 解析出标的），排序去重。"""
    names = {
        (p.get("symbol") or "").strip().upper()
        for p in (positions or [])
        if p.get("symbol")
    } | {
        underlying_of(p["symbol"])
        for p in (option_positions or [])
        if p.get("symbol")
    }
    return tuple(sorted(n for n in names if n))


def load_limits(limits_log, now: dt.datetime | None = None):
    """限额日志 → (records, {limit_key: 生效值}, 哈希链是否完好, 问题描述)。"""
    from scoring.position_limits import LIMIT_SPECS, effective_limit
    from scoring.mispricing_store import read_chain, verify_chain

    records = read_chain(limits_log)
    chain_ok, chain_issue = verify_chain(records)
    now = now or dt.datetime.now()
    limits = {spec.key: effective_limit(records, spec.key, now) for spec in LIMIT_SPECS}
    return records, limits, chain_ok, chain_issue


def exposures_after_trade(
    positions: list[dict],
    option_positions: list[dict] | None,
    total_equity: float | None,
    cash_balance: float | None,
    category_of,
    *,
    symbol: str,
    capital_committed: float,
    avg_dollar_volume: dict[str, float] | None = None,
) -> Exposures | None:
    """在现有持仓之上加一笔假设的新仓，返回加仓后的敞口。

    capital_committed 是这笔占用的资金/保证金，按正值并进该标的的净敞口，
    同时从现金里扣掉同样的数——对 debit 价差就是付出的净权利金，对 credit
    价差是被占住的保证金（钱没花掉但也不能再用了）。两种都按"这笔钱不再
    可用"处理，是刻意取保守的一边：宁可把集中度和现金占比算得难看一点，
    也不要让一笔实际会占住保证金的交易在检查里显示成不占资金。
    """
    added = list(option_positions or [])
    added.append({"symbol": symbol.strip().upper(), "market_value": abs(capital_committed)})
    new_cash = (cash_balance or 0.0) - abs(capital_committed)
    return compute_exposures(
        positions,
        total_equity,
        new_cash,
        category_of,
        added,
        avg_dollar_volume=avg_dollar_volume,
    )
