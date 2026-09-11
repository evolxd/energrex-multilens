"""
ENERGREX — 门④ 下场前验证

2026-09-10：用户指出"下场前验证"其实已经有内容——`account/risk.py::
build_recommendations()` 第3段"新开仓候选"（AI评分≥70 + IV regime匹配 +
风险余量允许）一直混在作战室"今日操作简报"里，跟持仓管理建议堆在一起，
没人特意去看。这段逻辑抽成了独立的 `new_opportunity_candidates()`，这个
页面直接调它单独展示——不是新写的东西，是把已经在跑的逻辑挪到它真正该
在的门。

只是候选池的初筛，不是可以直接下单的建议：没看硬约束余量（仓位管理页
门③才有）、没看 Kelly 建议仓位（同样在门③）。开仓前建议接着去门③过一遍。
"""
import pathlib

import pandas as pd
import streamlit as st

_ROOT = pathlib.Path(__file__).parent

st.set_page_config(page_title="ENERGREX · 下单前检查", page_icon="✅", layout="wide")

import _sidebar as _sb
_sb.render()

_G = "#00D4AA"; _A = "#FFB347"; _SURF = "#0F1923"; _BDR = "#1E2D3D"
_MUT = "#8B9BB4"; _TXT = "#E2E8F0"

_ACCT = "account_1"

st.markdown(
    f"<div style='background:{_SURF};border:1px solid {_BDR};border-radius:10px;"
    f"padding:11px 20px;display:flex;justify-content:space-between;align-items:center;'>"
    f"<span style='font-size:17px;font-weight:800;letter-spacing:1px;color:{_G}'>"
    f"✅ 下单前检查</span>"
    f"<span style='font-size:12px;color:{_MUT}'>门④ 下场前验证</span>"
    f"</div>",
    unsafe_allow_html=True,
)

try:
    import _cascade
    _am = _cascade._get_am()
    from account.risk import new_opportunity_candidates

    _snap = _am["_compute_risk_snapshot"](_ACCT)
    _iv   = _am["_compute_iv_regime"](_ACCT)
    _ai   = _am["_load_ai_scores"]()
    _held = {p["underlying"] for p in _am["_build_spread_portfolios"](_ACCT)}

    _candidates = new_opportunity_candidates(
        risk_snapshot=_snap, iv_regime=_iv, ai_scores=_ai, held_underlyings=_held,
    )
    _error = None
except Exception as _exc:
    _candidates, _snap, _iv = [], {}, {}
    _error = str(_exc)

if _error:
    st.warning(f"读取候选数据失败：{_error}")
elif _snap.get("error"):
    st.info("尚未读到可用的账户净值——先到「账户监控」页同步 Firstrade。")
else:
    _leverage = _snap.get("leverage_delta") or _snap.get("leverage") or 0.0
    st.caption(
        f"当前 Delta 杠杆 {_leverage:.2f}x · IV Regime {_iv.get('status', 'NO_DATA')} · "
        f"已持仓 {len(_held)} 个标的——杠杆超过硬上限 75% 时不产生新候选（风险余量不够，"
        f"先去仓位管理页处理，不是来加新仓的时候）。"
    )

    if not _candidates:
        st.markdown(
            f"<div style='color:{_MUT};padding:12px;background:{_SURF};"
            f"border:1px dashed {_BDR};border-radius:6px;font-size:13px'>"
            f"当前没有候选——杠杆余量不够，或者没有 AI 评分 ≥70 且还没持仓的标的。"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div style='font-size:13px;font-weight:700;color:{_TXT};margin:12px 0 8px'>"
            f"新开仓候选（AI评分 ≥70，按分数排序，最多3个）</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(pd.DataFrame([
            {"标的": c["标的"], "AI评分": c["AI评分"], "建议方向": c["行动建议"],
             "触发原因": c["触发原因"]}
            for c in _candidates
        ]), use_container_width=True, hide_index=True)

        st.markdown(
            f"<div style='background:{_SURF};border:1px dashed {_BDR};border-radius:8px;"
            f"padding:14px 16px;margin-top:12px;'>"
            f"<div style='color:{_A};font-weight:700;font-size:13px;margin-bottom:6px'>"
            f"这只是初筛，不是仓位建议</div>"
            f"<div style='color:{_MUT};font-size:12.5px;line-height:1.7'>"
            f"没看硬约束余量（单票/产业链集中度还有多少空间）、没看 Kelly 建议张数——"
            f"这两个都在 <b style='color:{_TXT}'>⚖️ 仓位管理</b>（门③）。选中一个标的后"
            f"先去那页看能不能下、下多大，再去 <b style='color:{_TXT}'>🎯 期权价差工具</b>"
            f"（门②）算具体价差。"
            f"</div></div>",
            unsafe_allow_html=True,
        )

st.divider()

# ════════════════════════════════════════════════════════════════════
# 下单前检查：硬约束 + Kelly 建议对比
# 上面那段是"选哪个标的"，这段是"下多大"——顺序就是真实下单的顺序。
# 两项检查都不新造规则：硬约束走门③同一套 compute_exposures/breaches
# （scoring/exposure_context.py 共用），Kelly 走门⑥同一套收缩估计
# （account/performance.py），这里只负责把"加上这笔之后"的数算出来。
# ════════════════════════════════════════════════════════════════════
st.markdown(
    f"<div style='font-size:15px;font-weight:800;color:{_G};margin:6px 0 2px'>"
    f"下单前检查</div>"
    f"<div style='color:{_MUT};font-size:12.5px;margin-bottom:10px'>"
    f"填上打算下的这一笔，回答两个问题：加上它会不会破硬约束线，"
    f"以及冒的钱是不是超过历史胜率/赔率撑得起的规模。</div>",
    unsafe_allow_html=True,
)

try:
    import datetime as _dt

    from account.performance import (
        MIN_SAMPLE_FOR_KELLY,
        compute_performance_stats,
        kelly_size_check,
    )
    from scoring.exposure_context import (
        chain_of,
        exposures_after_trade,
        load_avg_dollar_volume,
        load_limits,
        load_portfolio,
        underlyings_in,
    )
    from scoring.position_exposure import breaches, compute_exposures
    from scoring.position_limits import LIMIT_BY_KEY

    # 缓存 1 小时，跟门③一致——日均成交额是流动性特征，不是分钟级会变的
    # 东西，但每个标的一次 yfinance 调用，不缓存会很慢。
    _load_adv = st.cache_data(ttl=3600)(load_avg_dollar_volume)

    _positions, _options, _equity, _cash, _sync_time = load_portfolio()
    _records, _limits, _chain_ok, _chain_issue = load_limits(
        _ROOT / "data" / "position_limits.jsonl", _dt.datetime.now()
    )
    _perf = compute_performance_stats(_ACCT)
    _by_combo = (_perf or {}).get("by_combo", {})
    _check_error = None
except Exception as _exc:
    _positions = _options = _records = None
    _equity = _cash = None
    _limits, _by_combo = {}, {}
    _chain_ok, _chain_issue = True, ""
    _check_error = str(_exc)

if _check_error:
    st.warning(f"读取持仓/限额数据失败：{_check_error}")
elif not _equity:
    st.info("尚未读到可用的账户净值——先到「🏦 账户监控」页同步 Firstrade。")
elif not any(v is not None for v in _limits.values()):
    st.info("还没设定任何硬约束限额——先到「⚖️ 仓位管理」页设限额，这里才有线可对。")
else:
    if not _chain_ok:
        st.error(f"限额变更日志哈希链校验失败：{_chain_issue}。下面的限额不可信，先去门③修。")

    _f1, _f2, _f3, _f4 = st.columns([1.1, 1.5, 1.2, 1.2])
    _ticker = _f1.text_input("标的", value="", placeholder="NVDA").strip().upper()
    _strategy = _f2.selectbox("策略类型", sorted(_by_combo.keys()) + ["（其他/无历史）"])
    _max_loss = _f3.number_input("本次最大亏损 $", min_value=0.0, step=100.0, value=0.0,
                                 help="这单最坏情况下亏多少——Kelly 比的就是这个数，"
                                      "不是市值也不是名义敞口")
    _capital = _f4.number_input("占用资金/保证金 $", min_value=0.0, step=100.0, value=0.0,
                                help="debit 价差填付出的净权利金，credit 价差填被占住的"
                                     "保证金。留 0 则按最大亏损计")

    if not _ticker or _max_loss <= 0:
        st.caption("填标的和最大亏损后出结果。")
    else:
        _committed = _capital if _capital > 0 else _max_loss
        # 日均成交额要带上这次的新标的，否则"流动性天数"这条限额在新标的上
        # 永远读不出数（compute_exposures 对没有成交额数据的标的是直接不给
        # 读数，而不是默认当成流动性充足）。
        _adv = _load_adv(tuple(sorted(set(underlyings_in(_positions, _options)) | {_ticker})))
        _before = compute_exposures(_positions, _equity, _cash, chain_of, _options,
                                    avg_dollar_volume=_adv)
        _after = exposures_after_trade(
            _positions, _options, _equity, _cash, chain_of,
            symbol=_ticker, capital_committed=_committed, avg_dollar_volume=_adv,
        )

        # ── A. 硬约束 ────────────────────────────────────────────
        st.markdown(
            f"<div style='font-size:13px;font-weight:700;color:{_TXT};margin:14px 0 6px'>"
            f"① 硬约束：加上这笔之后</div>", unsafe_allow_html=True)

        _rows = []
        for _key, _limit in _limits.items():
            _spec = LIMIT_BY_KEY.get(_key)
            if _spec is None or _limit is None or _after is None:
                continue
            _now_v = _before.for_limit(_key) if _before else None
            _new_v = _after.for_limit(_key)
            if _new_v is None:
                continue
            _bad = _spec.is_breached(_new_v, float(_limit))
            _rows.append({
                "约束": _spec.label,
                "现在": f"{_now_v:.1f}{_spec.unit}" if _now_v is not None else "—",
                "下单后": f"{_new_v:.1f}{_spec.unit}",
                "限额": f"{'≤' if _spec.kind == 'max' else '≥'}{float(_limit):.1f}{_spec.unit}",
                "判定": "❌ 破线" if _bad else "✅ 通过",
            })
        _after_breaches = breaches(_after, _limits) if _after else []
        if _rows:
            st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
        if _after_breaches:
            for _b in _after_breaches:
                _unit = LIMIT_BY_KEY[_b["key"]].unit
                st.error(
                    f"**{_b['label']}** 下单后 {_b['reading']:.1f}{_unit}，"
                    f"超出限额 {_b['overshoot']:.1f}{_unit}"
                    + (f"（{_b['detail']}）" if _b.get("detail") else "")
                )
        else:
            st.success("全部硬约束在线内。")

        # ── B. Kelly ─────────────────────────────────────────────
        st.markdown(
            f"<div style='font-size:13px;font-weight:700;color:{_TXT};margin:16px 0 6px'>"
            f"② Kelly：这笔冒的钱撑不撑得起</div>", unsafe_allow_html=True)

        _k = kelly_size_check(_by_combo, _strategy, _equity, _max_loss)
        if _k["status"] == "no_data":
            st.info(f"「{_strategy}」没有历史战绩，给不出建议规模——这一条只能靠你自己判断。")
        elif _k["status"] == "low_sample":
            st.warning(
                f"「{_strategy}」只有 {_k['n']} 笔已平仓记录（少于 {MIN_SAMPLE_FOR_KELLY} 笔）。"
                f"收缩Kelly在这个笔数下基本就是全账户平均值，拿它定规模是在假装知道自己"
                f"不知道的事——这里不给建议张数。"
            )
        elif _k["status"] == "negative":
            st.error(
                f"「{_strategy}」的收缩Kelly ≤ 0（{_k['n']} 笔，胜率 "
                f"{(_k['win_rate'] or 0)*100:.0f}%）——历史上这个策略是亏钱的，"
                f"Kelly 的答案是不下注。要下就得先说清楚这次跟历史哪里不一样。"
            )
        else:
            _c1, _c2, _c3 = st.columns(3)
            _c1.metric("建议上限（半Kelly）", f"${_k['suggested_max_loss']:,.0f}")
            _c2.metric("你打算冒", f"${_max_loss:,.0f}",
                       delta=f"{(_k['ratio']-1)*100:+.0f}%" if _k["ratio"] else None,
                       delta_color="inverse")
            _c3.metric("收缩Kelly f*", f"{_k['kelly_f_shrunk']*100:.1f}%")
            st.caption(
                f"{_k['n']} 笔（{_k['close_date_min']} ~ {_k['close_date_max']}）· "
                f"原始胜率 {(_k['win_rate'] or 0)*100:.0f}%"
                f"（95%区间 {(_k['win_rate_ci_lower'] or 0)*100:.0f}–"
                f"{(_k['win_rate_ci_upper'] or 0)*100:.0f}%）· "
                f"收缩后 {(_k['win_rate_shrunk'] or 0)*100:.0f}% · "
                f"赔率b {(_k['payoff_b_shrunk'] or 0):.2f} · "
                f"建议上限 = f* × 50% × 净值 ${_equity:,.0f}"
            )
            if _k["status"] == "over":
                st.error(
                    f"超了建议上限 {(_k['ratio']-1)*100:.0f}%——按半Kelly应该减到 "
                    f"${_k['suggested_max_loss']:,.0f} 以内。"
                )
            else:
                st.success(f"在建议上限之内（用掉 {_k['ratio']*100:.0f}%）。")

        # ── C. 结论 ──────────────────────────────────────────────
        if _after_breaches:
            _verdict, _color = "不能下：会破硬约束线", "#FF5C7A"
        elif _k["status"] == "negative":
            _verdict, _color = "不建议下：历史上这个策略是亏钱的", "#FF5C7A"
        elif _k["status"] == "over":
            _verdict, _color = (
                f"可以下，但要减到 ${_k['suggested_max_loss']:,.0f} 以内", _A)
        elif _k["status"] in ("low_sample", "no_data"):
            _verdict, _color = "硬约束这关过了；规模没有历史数据可依，自己定", _A
        else:
            _verdict, _color = "两关都过，可以按这个规模下", _G
        st.markdown(
            f"<div style='background:{_SURF};border:1px solid {_color};border-radius:8px;"
            f"padding:12px 16px;margin-top:14px;color:{_color};font-weight:700;"
            f"font-size:14px'>③ 结论：{_verdict}</div>",
            unsafe_allow_html=True,
        )

st.divider()
st.caption(
    "门④和门⑤的分工：门④（这页）往前看——我要开新仓，有没有初步候选、"
    "这笔下多大才不破线；门⑤往回看——过去系统提醒过的事，我有没有"
    "真的照做。门⑤那套「开仓恶化breach/无case交易/偏离Kelly」的事后检测见 "
    "🛡️ 纪律看板，设计见 docs/DISCIPLINE_AND_REVIEW_ARCHITECTURE.md。"
)
