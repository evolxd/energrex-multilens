"""ENERGREX 仓位管理：硬约束看板、突破预警、限额变更审核。

第一版不做任何评分驱动的仓位建议 —— 见 POSITION_MODULE_DESIGN.md 第一节。
这里的每一条约束都不依赖预测力，它们回答的是「万一我错了会不会死」。
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

import streamlit as st

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from _sidebar import render  # noqa: E402
from scoring.mispricing_monitor import (  # noqa: E402
    SEVERE_STATES,
    WATCH_STATES,
    thesis_state_for_ticker,
)
from scoring.mispricing_store import append_snapshot, read_chain, verify_chain  # noqa: E402
from scoring.position_exposure import (  # noqa: E402
    UNCLASSIFIED,
    approaching,
    breaches,
    compute_exposures,
    underlying_of,
)
from scoring.position_limits import (  # noqa: E402
    COOLING_OFF_DAYS,
    CORRECTION_WINDOW_HOURS,
    LIMIT_BY_KEY,
    LIMIT_SPECS,
    LOOSEN_COOLDOWN_DAYS,
    MAX_LOOSEN_STEP_PCT,
    MIN_REASON_CHARS,
    build_record,
    effective_limit,
    evaluate_change,
    pending_change,
)

st.set_page_config(page_title="ENERGREX 仓位管理", page_icon="⚖️", layout="wide")
render()

LIMITS_LOG = ROOT / "data" / "position_limits.jsonl"
MISPRICING_LOG = ROOT / "data" / "mispricing_cases.jsonl"

# route_case_state's non-neutral outcomes, split into the same two severities
# the hard-constraint alerts above already use. HOLD and ADD_IF_PREAUTHORIZED
# are deliberately absent: both mean the thesis is intact under price-only
# opposition, which is not something to alert on.
st.title("⚖️ 仓位管理")
st.caption(
    "硬约束看板与预警。这一版不产生任何基于评分的买卖建议 —— "
    "评分的预测力尚未通过验证门槛，用它放大敞口等于给噪声加杠杆。"
)


# ── 数据读取 ────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def _load_portfolio():
    """Latest stock positions, option positions and balance.

    Options must be included: this account holds the large majority of its
    equity in spreads, so a stock-only reading would report near-zero
    concentration while one underlying sits at roughly half the book.

    Returns empty rather than raising so the limit-setting half of the page
    still works before any account sync.
    """
    try:
        from account.db import db

        conn = db()
        latest = conn.execute(
            "SELECT MAX(sync_time) FROM positions"
        ).fetchone()
        sync_time = latest[0] if latest else None
        # Latest row per symbol, not "every row sharing one exact global
        # sync_time" -- _refresh_stock_prices() used to stamp each stock
        # with its own datetime.now() one at a time, so the old exact-match
        # query silently returned only whichever stock happened to update
        # last (2026-08-27 bug fix; see account/repository.py::load_positions
        # for the full story).
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
        st.warning(f"读取账户数据失败：{exc}")
        return [], [], None, None, None


def _chain_of(symbol: str) -> str | None:
    try:
        from scoring_engine import TICKER_CATEGORY

        cat = TICKER_CATEGORY.get(symbol)
        return cat.value if cat else None
    except Exception:
        return None


@st.cache_data(ttl=3600)
def _load_avg_dollar_volume(tickers: tuple[str, ...]) -> dict[str, float]:
    """20-trading-day average daily dollar volume per underlying.

    Cached for an hour, not 60s like the portfolio itself -- this is a
    liquidity characteristic of the stock, not something that meaningfully
    changes minute to minute, and it's one yfinance call per ticker.
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


positions, option_positions, total_equity, cash_balance, sync_time = _load_portfolio()
_underlyings = tuple(sorted({
    p["symbol"].strip().upper() for p in positions if p.get("symbol")
} | {
    underlying_of(p["symbol"]) for p in (option_positions or []) if p.get("symbol")
}))
avg_dollar_volume = _load_avg_dollar_volume(_underlyings)
exposures = compute_exposures(
    positions, total_equity, cash_balance, _chain_of, option_positions,
    avg_dollar_volume=avg_dollar_volume,
)

records = read_chain(LIMITS_LOG)
chain_ok, chain_issue = verify_chain(records)
if not chain_ok:
    st.error(
        f"限额变更日志的哈希链校验失败：{chain_issue}。"
        "在修复之前不会接受任何新的变更 —— 历史被改动过的日志没有约束力。"
    )

now = dt.datetime.now()
current_limits = {
    spec.key: effective_limit(records, spec.key, now) for spec in LIMIT_SPECS
}


# ── 看板 ────────────────────────────────────────────────────────────
st.subheader("硬约束现状")

if exposures is None:
    st.info(
        "尚未读到可用的账户净值。先到「账户监控」页同步 Firstrade，"
        "下方仍可设定限额。"
    )
else:
    st.caption(
        f"持仓快照：{sync_time or '未知'} ｜ 总净值 ${exposures.total_equity:,.0f} ｜ "
        f"股票 {len(positions)} 笔 + 期权 {len(option_positions)} 笔（期权按标的净额并入，"
        "计价口径为市值而非 Delta 名义敞口）"
    )
    cols = st.columns(len(LIMIT_SPECS))
    for col, spec in zip(cols, LIMIT_SPECS):
        limit_value = current_limits.get(spec.key)
        reading = exposures.for_limit(spec.key)
        with col:
            st.metric(spec.label, f"{reading:.1f}{spec.unit}" if reading is not None else "—")
            if limit_value is None:
                st.caption("尚未设定上限" if spec.kind == "max" else "尚未设定下限")
            else:
                if spec.kind == "max":
                    room = limit_value - (reading or 0.0)
                    st.caption(f"上限 {limit_value:.1f}{spec.unit} ｜ 余量 {room:+.1f}{spec.unit}")
                else:
                    room = (reading or 0.0) - limit_value
                    st.caption(f"下限 {limit_value:.1f}{spec.unit} ｜ 余量 {room:+.1f}{spec.unit}")

    if exposures.unclassified_tickers:
        st.warning(
            "以下持仓不在 TICKER_CATEGORY 里，已单独计入「"
            + UNCLASSIFIED
            + "」产业链，请补映射，否则集中度会被低估："
            + "、".join(exposures.unclassified_tickers)
        )

    with st.expander("按标的 / 按产业链拆解"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**按标的**")
            for sym, pct in sorted(
                exposures.by_ticker_pct.items(), key=lambda x: -x[1]
            ):
                st.write(f"{sym} — {pct:.1f}%")
        with c2:
            st.markdown("**按产业链**")
            for chain, pct in sorted(
                exposures.by_chain_pct.items(), key=lambda x: -x[1]
            ):
                st.write(f"{chain} — {pct:.1f}%")


# ── 预警 ────────────────────────────────────────────────────────────
st.subheader("预警")

if exposures is None:
    st.caption("无持仓数据，无法评估。")
elif not any(v is not None for v in current_limits.values()):
    st.caption("尚未设定任何限额，无可评估的约束。")
else:
    breached = breaches(exposures, current_limits)
    near = approaching(exposures, current_limits)
    if not breached and not near:
        st.success("全部约束均在线内。")
    for item in breached:
        detail = f"（{item['detail']}）" if item["detail"] else ""
        unit = LIMIT_BY_KEY[item["key"]].unit
        st.error(
            f"**突破 · {item['label']}**{detail}：当前 {item['reading']:.1f}{unit}，"
            f"限额 {item['limit']:.1f}{unit}，超出 {item['overshoot']:.1f}{unit}。"
            "超限期间无法调高该限额，只能调整持仓。"
        )
    for item in near:
        detail = f"（{item['detail']}）" if item["detail"] else ""
        unit = LIMIT_BY_KEY[item["key"]].unit
        st.warning(
            f"**逼近 · {item['label']}**{detail}：当前 {item['reading']:.1f}{unit}，"
            f"限额 {item['limit']:.1f}{unit}。"
        )


# ── 持仓论点监控 ─────────────────────────────────────────────────────
# Only tickers that already have a mispricing case on file (authored via the
# 误价研究 page) show up here -- most held tickers will not, and that is
# expected, not an error. A thesis breaching a hard limit above is passed in
# so route_case_state's RISK_REDUCTION_REQUIRED override applies even when
# the metric-level monitor_rules alone would read as clean.
st.subheader("持仓论点监控")

if exposures is None or not exposures.by_ticker_pct:
    st.caption("无持仓数据，无法评估。")
else:
    breached_tickers = {
        item["detail"]
        for item in breaches(exposures, current_limits)
        if item["key"] == "single_stock_max" and item["detail"]
    }
    thesis_rows = []
    for ticker in sorted(exposures.by_ticker_pct):
        state = thesis_state_for_ticker(
            MISPRICING_LOG,
            ticker,
            portfolio_or_option_limit_breached=ticker in breached_tickers,
        )
        if state is not None:
            thesis_rows.append(state)

    if not thesis_rows:
        st.caption("持仓里没有票在「误价研究」页建过案例，无可监控的论点。")
    else:
        for state in thesis_rows:
            case_state = state["case_state"]
            label = f"**{state['ticker']}** · {case_state}"
            rule_text = "；".join(
                f"{r.rule_id}: {r.reason}" for r in state["triggered_rules"]
            )
            if case_state in SEVERE_STATES:
                st.error(f"{label}｜{rule_text}" if rule_text else label)
            elif case_state in WATCH_STATES:
                st.warning(f"{label}｜{rule_text}" if rule_text else label)
            else:
                st.success(label)
            if state["pending_observation_count"]:
                pending_text = "、".join(
                    f"{metric}×{count}"
                    for metric, count in state["pending_by_metric"].items()
                )
                st.info(
                    f"　↳ {state['ticker']} 有 {state['pending_observation_count']} "
                    f"项观测值待核对（{pending_text}），核对前不计入触发判断。"
                )


# ── 变更审核 ────────────────────────────────────────────────────────
st.subheader("限额设定与变更")
st.caption(
    f"收紧立即生效。放宽需通过：超限锁 · 不少于 {MIN_REASON_CHARS} 字的书面理由 · "
    f"单次不超过 {MAX_LOOSEN_STEP_PCT:.0f} 个百分点 · {LOOSEN_COOLDOWN_DAYS} 天内仅一次 · "
    f"{COOLING_OFF_DAYS} 天冷静期。首次设定后 {CORRECTION_WINDOW_HOURS} 小时内可自由修正。"
)

for spec in LIMIT_SPECS:
    pending = pending_change(records, spec.key, now)
    current = current_limits.get(spec.key)
    with st.expander(
        f"{spec.label}"
        + (f" — 当前 {current:.1f}{spec.unit}" if current is not None else " — 未设定")
        + ("　⏳ 有变更待生效" if pending else "")
    ):
        st.caption(spec.help_text)

        if pending:
            st.info(
                f"已记录变更：{pending['old_value']:.1f}{spec.unit} → "
                f"{pending['new_value']:.1f}{spec.unit}，"
                f"于 {pending['effective_at']} 生效。在此之前仍按 "
                f"{current:.1f}{spec.unit} 执行。"
            )

        with st.form(f"limit_form_{spec.key}"):
            new_value = st.number_input(
                f"新的限额（{spec.unit}）",
                min_value=0.0,
                max_value=100.0 if spec.unit == "%" else 250.0,
                value=float(current) if current is not None else 15.0,
                step=0.5,
                key=f"val_{spec.key}",
            )
            reason = st.text_area(
                "变更理由（放宽时必填，将永久写入哈希链日志）",
                key=f"reason_{spec.key}",
                height=80,
            )
            submitted = st.form_submit_button("提交变更")

        if submitted:
            if not chain_ok:
                st.error("日志哈希链校验未通过，拒绝写入。")
            else:
                exposure = exposures.for_limit(spec.key) if exposures else None
                verdict = evaluate_change(
                    spec.key,
                    float(new_value),
                    reason=reason,
                    now=now,
                    history=records,
                    exposure=exposure,
                )
                if not verdict.allowed:
                    for blocker in verdict.blockers:
                        st.error(blocker)
                else:
                    payload = build_record(
                        spec.key,
                        float(new_value),
                        verdict,
                        reason=reason,
                        now=now,
                        exposure=exposure,
                        old_value=current,
                    )
                    append_snapshot(LIMITS_LOG, payload)
                    for note in verdict.notes:
                        st.success(note)
                    st.cache_data.clear()
                    st.rerun()


# ── 变更历史 ────────────────────────────────────────────────────────
with st.expander("变更历史（哈希链，不可篡改）"):
    if not records:
        st.caption("尚无记录。")
    else:
        st.caption(
            "链校验："
            + ("通过 ✅" if chain_ok else f"失败 ❌ {chain_issue}")
            + f" ｜ 共 {len(records)} 条"
        )
        for record in reversed(records):
            p = record["payload"]
            spec = LIMIT_BY_KEY.get(p["key"])
            unit = spec.unit if spec else "%"
            old = f"{p['old_value']:.1f}{unit}" if p.get("old_value") is not None else "—"
            st.write(
                f"`{p['timestamp']}` **{p['key']}** {old} → {p['new_value']:.1f}{unit}"
                f" · {p['direction']}"
                + (f" · 生效 {p['effective_at']}" if p.get("effective_at") else "")
            )
            if p.get("reason"):
                st.caption(f"　理由：{p['reason']}")
