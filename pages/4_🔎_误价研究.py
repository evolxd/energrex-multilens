"""ENERGREX 误价发现与特殊机会研究页。"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import streamlit as st


ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from _sidebar import render  # noqa: E402
from scoring.mispricing_adapters import (  # noqa: E402
    build_valuation_request,
    route_portfolio_expression,
    validate_valuation_output,
)
from scoring.mispricing_engine import as_dict, evaluate_mispricing_case  # noqa: E402
from scoring.mispricing_monitor import evaluate_rules, route_case_state  # noqa: E402
from scoring.mispricing_store import append_snapshot  # noqa: E402
from scoring.unified_valuation import run_unified_valuation  # noqa: E402
from scoring.valuation_evidence_pipeline import (  # noqa: E402
    build_live_valuation_request,
)


st.set_page_config(page_title="ENERGREX 误价研究", page_icon="🔎", layout="wide")
render()

EXAMPLE_PATH = ROOT / "scoring" / "examples" / "futu_engineering_case.json"
STORE_PATH = ROOT / "data" / "mispricing_cases.jsonl"

st.title("🔎 误价发现与特殊机会 V2.4")
st.caption("先证明市场错在哪里，再交给五维、估值和 IDI。这里不产生第二总分，也不下单。")
st.warning("当前 FUTU 内容是工程样例，证据故意标记为待验证，不能作为投资结论。")

default_case = EXAMPLE_PATH.read_text(encoding="utf-8")
case_text = st.text_area(
    "研究案例 JSON",
    value=default_case,
    height=360,
    help="正式案例必须符合 scoring/mispricing_contract.schema.json。",
)

if "mispricing_result" not in st.session_state:
    st.session_state["mispricing_result"] = None
if "mispricing_case" not in st.session_state:
    st.session_state["mispricing_case"] = None
if "formal_valuation_result" not in st.session_state:
    st.session_state["formal_valuation_result"] = None

col1, col2 = st.columns([1, 1])
with col1:
    evaluate_clicked = st.button("运行快速拒绝与门控评估", type="primary", use_container_width=True)
with col2:
    save_clicked = st.button("保存不可变快照", use_container_width=True)

if evaluate_clicked or save_clicked:
    try:
        case = json.loads(case_text)
        decision = evaluate_mispricing_case(case, today=dt.date.today())
        result = as_dict(decision)
        result["evaluated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        st.session_state["mispricing_result"] = result
        st.session_state["mispricing_case"] = case
        if save_clicked:
            record = append_snapshot(STORE_PATH, result)
            st.success(f"快照已保存：{record['record_hash'][:12]}…")
    except Exception as exc:
        st.session_state["mispricing_result"] = None
        st.session_state["mispricing_case"] = None
        st.error(f"案例未通过：{exc}")

result = st.session_state.get("mispricing_result")
if result:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("研究路由", result["decision"])
    c2.metric("证据置信度", result["confidence"])
    c3.metric("证据有效率", f"{result['evidence_validity_rate']:.0%}")
    c4.metric("强来源占比", f"{result['strong_source_rate']:.0%}")
    c5.metric("Quick Reject", result["quick_reject"]["status"])

    st.caption(
        f"主路径：{result['primary_path']} ｜ "
        f"风格谱系：{result['style_lineage']} ｜ "
        f"引擎版本：{result['engine_version']}"
    )

    st.subheader("一句话变体认知")
    thesis = result["thesis"]
    st.write(f"**市场认为：** {thesis['market_believes']}")
    st.write(f"**我们的不同判断：** {thesis['variant_view']}")
    st.write(f"**证据逻辑：** {thesis['because']}")
    st.write(f"**证伪条件：** {thesis['falsifiable_by']}")

    st.subheader(f"{len(result['gates'])}道门")
    st.dataframe(result["gates"], use_container_width=True, hide_index=True)
    if result["blocking_reasons"]:
        st.error("；".join(result["blocking_reasons"]))

    st.subheader("付款方经济学")
    payer = result.get("payer_economics") or {}
    if payer:
        st.json(payer, expanded=False)
    else:
        st.warning("付款方经济学尚未录入；不得据此推导稳定回款或合理杠杆。")

    liquidation = result.get("liquidation_value")
    if liquidation:
        st.subheader("清算价值与普通股回收")
        lc1, lc2, lc3, lc4 = st.columns(4)
        lc1.metric("普通股回收值", f"{liquidation['estimated_common_equity_recovery']:,.2f}")
        lc2.metric("当前市值", f"{liquidation['current_market_cap']:,.2f}")
        lc3.metric("相对市值折价", f"{liquidation['discount_to_market_cap']:.1%}")
        lc4.metric("预计年化回报", f"{liquidation['estimated_annualized_return']:.1%}")
        st.caption(
            f"清算 Gate：{liquidation['gate_status']} ｜ "
            f"预计兑现：{liquidation['liquidation_timeline_months']} 个月"
        )
        if liquidation["gate_reasons"]:
            st.error("；".join(liquidation["gate_reasons"]))

    st.subheader("可执行 Kill Thesis")
    st.dataframe(result["monitor_rules"], use_container_width=True, hide_index=True)
    metric_text = st.text_area(
        "监控指标历史 JSON",
        value=json.dumps(
            {
                "funded_accounts_qoq": [0.02, -0.01, -0.03],
                "diluted_share_change_yoy": [-0.02],
            },
            ensure_ascii=False,
            indent=2,
        ),
        height=150,
    )
    sc1, sc2, sc3 = st.columns(3)
    price_only = sc1.checkbox("仅价格反对，核心事实未变")
    add_pre_authorized = sc2.checkbox("事前已授权满足条件时加仓")
    delayed = sc3.checkbox("兑现窗口已经延迟")
    if st.button("检查触发条件"):
        try:
            trigger_results = evaluate_rules(
                result["monitor_rules"],
                json.loads(metric_text),
            )
            st.dataframe(
                [
                    {
                        "rule_id": item.rule_id,
                        "triggered": item.triggered,
                        "observed_values": list(item.observed_values),
                        "action": item.action,
                        "reason": item.reason,
                    }
                    for item in trigger_results
                ],
                use_container_width=True,
                hide_index=True,
            )
            case_state = route_case_state(
                trigger_results,
                price_only_opposition=price_only,
                add_pre_authorized=add_pre_authorized,
                realization_delayed=delayed,
            )
            st.info(f"当前论文状态：{case_state.value}")
        except Exception as exc:
            st.error(f"监控数据无效：{exc}")

    st.subheader("统一估值与组合表达接口")
    st.caption(
        "这里不计算第二套目标价。估值结果必须来自统一估值引擎，"
        "并通过数据质量、双方法和 Reverse Valuation 校验。"
    )
    stored_case = st.session_state.get("mispricing_case") or {}
    try:
        valuation_request = build_valuation_request(stored_case)
        with st.expander("查看发送给统一估值引擎的请求"):
            st.json(valuation_request, expanded=False)
    except Exception as exc:
        valuation_request = None
        st.error(f"无法生成估值请求：{exc}")

    st.markdown("#### 正式估值证据流水线")
    st.caption(
        "先输入逐字段原始观察。只有双独立来源、时间有效、单位一致且数值在容差内，"
        "系统才生成 LIVE 统一估值请求。Yahoo 的两个包装器仍只算一个来源。"
    )
    evidence_input_text = st.text_area(
        "证据流水线输入 JSON",
        value="{}",
        height=180,
        help=(
            "需要 valuation_case_id、ticker、profile、as_of、observations、"
            "scenarios、realization_months。观察结构遵循 "
            "scoring/valuation_observation.schema.json。"
        ),
    )
    if st.button("审计证据并生成正式估值请求"):
        try:
            evidence_payload = json.loads(evidence_input_text)
            envelope = build_live_valuation_request(
                valuation_case_id=evidence_payload["valuation_case_id"],
                ticker=evidence_payload["ticker"],
                profile=evidence_payload["profile"],
                as_of=evidence_payload["as_of"],
                observations=evidence_payload["observations"],
                scenarios=evidence_payload["scenarios"],
                realization_months=evidence_payload["realization_months"],
                scenario_probabilities=evidence_payload.get("scenario_probabilities"),
                dispersion_reconciliation=evidence_payload.get(
                    "dispersion_reconciliation",
                    "",
                ),
            )
            if envelope["status"] == "READY":
                st.success("证据门通过；已生成带内容哈希快照的 LIVE 正式估值请求。")
                st.json(envelope["request"], expanded=False)
            else:
                st.error("证据门未通过，不能生成 LIVE 正式估值请求。")
                st.json(envelope["evidence"], expanded=False)
        except Exception as exc:
            st.error(f"证据流水线输入无效：{exc}")

    valuation_input_text = st.text_area(
        "统一估值服务完整输入 JSON",
        value="{}",
        height=220,
        help=(
            "遵循 scoring/unified_valuation_request.schema.json；"
            "字段必须带来源、时点、单位和交叉验证。"
        ),
    )
    if st.button("运行统一估值服务"):
        try:
            unified_request = json.loads(valuation_input_text)
            formal_result = run_unified_valuation(unified_request)
            st.session_state["formal_valuation_result"] = formal_result
            if formal_result["data_quality_status"] == "PASS":
                st.success("正式估值数据门与方法门通过")
            else:
                st.warning(
                    "估值仅供复核："
                    + "；".join(formal_result.get("review_reasons", []))
                )
        except Exception as exc:
            st.session_state["formal_valuation_result"] = None
            st.error(f"统一估值失败：{exc}")

    formal_result = st.session_state.get("formal_valuation_result")
    if formal_result:
        fv1, fv2, fv3, fv4 = st.columns(4)
        fv1.metric("熊市价值", f"{formal_result['bear_value']:,.2f}")
        fv2.metric("基准价值", f"{formal_result['base_value']:,.2f}")
        fv3.metric("牛市价值", f"{formal_result['bull_value']:,.2f}")
        fv4.metric("价格门", formal_result["price_gate"])
        st.caption(
            f"数据状态：{formal_result['data_quality_status']} ｜ "
            f"有效率：{formal_result['data_validity_rate']:.0%} ｜ "
            f"方法：{', '.join(formal_result['methods'])}"
        )
        with st.expander("查看正式估值结果与 Reverse Valuation"):
            st.json(formal_result, expanded=False)

    option_text = st.text_area(
        "有限风险期权表达 JSON（可选）",
        value="",
        height=120,
        help="只在正式股票价格门未通过时评估；留空即继续等待。",
    )
    if st.button(
        "验证估值并路由表达",
        disabled=valuation_request is None or formal_result is None,
    ):
        try:
            formal_valuation = validate_valuation_output(
                formal_result,
                expected_ticker=result["ticker"],
            )
            option_payload = json.loads(option_text) if option_text.strip() else None
            expression = route_portfolio_expression(
                mispricing_decision=result["decision"],
                valuation=formal_valuation,
                option=option_payload,
            )
            st.success(f"表达路由：{expression.route.value}")
            st.write("；".join(expression.reasons))
        except Exception as exc:
            st.error(f"估值或表达未通过：{exc}")
