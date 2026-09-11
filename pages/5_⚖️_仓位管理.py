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
from scoring.exposure_context import (  # noqa: E402
    chain_of as _chain_of,
    load_avg_dollar_volume,
    load_limits,
    load_portfolio,
    underlyings_in,
)
from scoring.mispricing_store import append_snapshot  # noqa: E402
from scoring.position_exposure import (  # noqa: E402
    UNCLASSIFIED,
    approaching,
    breaches,
    compute_exposures,
)
from scoring.position_limits import (  # noqa: E402
    COOLING_OFF_DAYS,
    CORRECTION_WINDOW_HOURS,
    LIMIT_BY_KEY,
    LIMIT_SPECS,
    LOOSEN_COOLDOWN_DAYS,
    MAX_LOOSEN_STEP_PCT,
    MIN_REASON_CHARS,
    RISK_SNAPSHOT_LIMIT_SPECS,
    build_record,
    effective_risk_limits,
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
# 读数逻辑住在 scoring/exposure_context.py，门④下场前验证用的是同一份——
# 两个页面对"现在的敞口是多少、超没超限"必须给一样的答案。这里只是套一层
# Streamlit 缓存：持仓 60 秒，日均成交额 1 小时（每个标的一次 yfinance 调用，
# 而流动性不是分钟级会变的东西）。
_load_portfolio = st.cache_data(ttl=60)(load_portfolio)
_load_avg_dollar_volume = st.cache_data(ttl=3600)(load_avg_dollar_volume)


positions, option_positions, total_equity, cash_balance, sync_time = _load_portfolio()
_underlyings = underlyings_in(positions, option_positions)
avg_dollar_volume = _load_avg_dollar_volume(_underlyings)
exposures = compute_exposures(
    positions, total_equity, cash_balance, _chain_of, option_positions,
    avg_dollar_volume=avg_dollar_volume,
)

now = dt.datetime.now()
records, current_limits, chain_ok, chain_issue = load_limits(LIMITS_LOG, now)
if not chain_ok:
    st.error(
        f"限额变更日志的哈希链校验失败：{chain_issue}。"
        "在修复之前不会接受任何新的变更 —— 历史被改动过的日志没有约束力。"
    )

# 风险快照类限额（杠杆/Beta-Delta/压力测试/回撤）——2026-09-10 审计 F-08：
# 这7条以前是 account_monitor.py 里的硬编码字典，跟这里的仓位限额走的是
# 完全不同的治理（没有变更记录、没有放宽关卡）。现在跟仓位限额共用同一份
# position_limits.jsonl、同一套 evaluate_change 治理，只是"现状"读数来自
# account_monitor._compute_risk_snapshot()，不是这页已经算好的 exposures
# ——两者是不同的数据源，所以是独立的一组卡片+表单，不跟上面四条混在
# 同一个循环里。
risk_snapshot_limits = effective_risk_limits(records, now)
try:
    import _cascade as _casc
    _risk_snap = _casc._get_am()["_compute_risk_snapshot"]("account_1")
    if _risk_snap.get("error"):
        _risk_snap = None
except Exception as _rs_exc:
    _risk_snap = None
    st.session_state["_risk_snapshot_error"] = str(_rs_exc)


def _risk_snapshot_reading(key: str, snap: dict | None) -> float | None:
    """按注册表的存储单位（stress/drawdown 是百分点，leverage/BD 是原始
    倍数）返回当前读数——跟 account_monitor.py._load_risk_limits() 反方向
    的换算，两处的换算规则必须对应，任何一处改了单位都要看另一处。"""
    if not snap:
        return None
    if key == "max_leverage":
        v = snap.get("leverage_delta") if snap.get("leverage_delta") is not None else snap.get("leverage")
        return abs(v) if v is not None else None
    if key == "max_beta_delta_ratio":
        v = snap.get("beta_delta_ratio")
        return abs(v) if v is not None else None
    if key in ("stress_warning", "stress_de_risk", "stress_hard_stop"):
        v = snap.get("stress_10_ratio")
        return abs(v) * 100.0 if v is not None else None
    if key == "stress_20_hard_stop":
        v = snap.get("stress_20_ratio")
        return abs(v) * 100.0 if v is not None else None
    if key in ("drawdown_freeze", "drawdown_de_risk"):
        v = snap.get("drawdown")
        return abs(v) * 100.0 if v is not None else None
    return None


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


# ── 风险快照现状 ────────────────────────────────────────────────────
st.subheader("风险快照限额（杠杆 / 压力测试 / 回撤）")

if _risk_snap is None:
    st.info(
        "尚未读到可用的风险快照——先到「账户监控」页同步 Firstrade，"
        "下方仍可设定限额。"
    )
else:
    _rcols = st.columns(len(RISK_SNAPSHOT_LIMIT_SPECS))
    for _col, _spec in zip(_rcols, RISK_SNAPSHOT_LIMIT_SPECS):
        _limit_v = risk_snapshot_limits.get(_spec.key)
        _reading = _risk_snapshot_reading(_spec.key, _risk_snap)
        with _col:
            st.metric(_spec.label, f"{_reading:.1f}{_spec.unit}" if _reading is not None else "—")
            if _limit_v is not None:
                _room = _limit_v - (_reading or 0.0)
                st.caption(f"上限 {_limit_v:.1f}{_spec.unit} ｜ 余量 {_room:+.1f}{_spec.unit}")


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


# ── 仓位建议（历史胜率/赔率，按策略类型）────────────────────────────
# 这个不是上面说的"评分驱动的仓位建议"（那是预测力没验证过的AI估值分），
# 是纯粹从已实现交易历史算出来的经验赔率——回答"过去这类价差我的真实
# 胜率/赔率是多少，所以下一笔该押多大"，跟股票评分预测力无关。
# 2026-09-09 用户拆分决定：绩效事实留在门⑥账户监控的"交易绩效"tab（原始
# 胜率/均赢均亏/赔率b/原始Kelly），收缩估计/置信区间这层"仓位建议"数字
# 放这里——两处读的是同一个 account/performance.py，不是两份口径。
st.subheader("仓位建议（历史胜率/赔率，按策略类型）")
try:
    from account.performance import compute_performance_stats as _perf_stats
    _perf = _perf_stats("account_1")
except Exception as _exc:
    _perf = None
    st.warning(f"读取交易历史失败：{_exc}")

if not _perf or not _perf.get("by_combo"):
    st.caption("暂无已实现交易历史，无法给出仓位建议——先在账户监控页跑一次「运行 FIFO 分析」。")
else:
    st.caption(
        "收缩胜率/收缩Kelly：往全账户总体拉一把（K=20，笔数越多越信自己的数据），"
        "缓解小样本噪音。胜率区间(95%) 是 Wilson 区间，算在原始胜率上，回答"
        "\"这一桶自己的笔数够不够撑起这个胜率数字\"——区间很宽就是笔数太少，不是 bug。"
        "都仍是 docs/SIX_GATES_AND_EXPOSURE_DESIGN.md §2 敞口设计的候选输入，"
        "还没打小样本折扣、没过硬约束封顶，不是可以直接拿去下单的仓位建议——"
        "封顶要在上面「硬约束现状」的限额之下再走一遍。目前只统计账户一。"
    )
    import pandas as _pd
    _sizing_rows = _pd.DataFrame([
        {"组合策略": k, "次数": v["count"],
         "统计区间": f"{v['close_date_min']} → {v['close_date_max']}",
         "收缩胜率": f"{v['win_rate_shrunk']*100:.0f}%",
         "胜率区间(95%)": f"{v['win_rate_ci_lower']*100:.0f}%–{v['win_rate_ci_upper']*100:.0f}%",
         "收缩Kelly": f"{v['kelly_f_shrunk']*100:+.1f}%" if v["kelly_f_shrunk"] is not None else "—"}
        for k, v in sorted(_perf["by_combo"].items(), key=lambda x: -x[1]["count"])
    ])
    st.dataframe(_sizing_rows, use_container_width=True, hide_index=True,
                 height=min(60 + len(_sizing_rows) * 38, 400))


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


st.caption(
    "以下 7 条走同一套治理（同一份哈希链日志、同一套超限锁/书面理由/步长/"
    "频率/冷静期），现状读数来自「账户监控」的风险快照，不是上面的持仓敞口。"
)
for spec in RISK_SNAPSHOT_LIMIT_SPECS:
    pending = pending_change(records, spec.key, now)
    current = risk_snapshot_limits.get(spec.key)
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

        with st.form(f"risk_limit_form_{spec.key}"):
            new_value = st.number_input(
                f"新的限额（{spec.unit}）",
                min_value=0.0,
                max_value=100.0 if spec.unit == "%" else 20.0,
                value=float(current) if current is not None else 15.0,
                step=0.5,
                key=f"risk_val_{spec.key}",
            )
            reason = st.text_area(
                "变更理由（放宽时必填，将永久写入哈希链日志）",
                key=f"risk_reason_{spec.key}",
                height=80,
            )
            submitted = st.form_submit_button("提交变更")

        if submitted:
            if not chain_ok:
                st.error("日志哈希链校验未通过，拒绝写入。")
            else:
                reading = _risk_snapshot_reading(spec.key, _risk_snap)
                verdict = evaluate_change(
                    spec.key,
                    float(new_value),
                    reason=reason,
                    now=now,
                    history=records,
                    exposure=reading,
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
                        exposure=reading,
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
