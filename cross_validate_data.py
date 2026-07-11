"""
数据交叉验证脚本 — cross_validate_data.py
对 mock_data.py + user_overrides.json 中的核心字段与 yfinance 实时数据对比
输出: data/data_validation_report.json
"""
import sys, os, json, pathlib, datetime, time
sys.path.insert(0, str(pathlib.Path(__file__).parent / "scoring"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yfinance as yf
from mock_data import MOCK_STOCKS

# ── 配置 ──────────────────────────────────────────────────────────────
ROOT        = pathlib.Path(__file__).parent
OVERRIDES_P = ROOT / "scoring" / "user_overrides.json"
OUT_DIR     = ROOT / "data"
OUT_FILE    = OUT_DIR / "data_validation_report.json"
OUT_DIR.mkdir(exist_ok=True)

# 全量股票（不再局限于最早的10只 mock 股票，覆盖 mock_data.py 里的所有 ticker）
TICKERS_CORE = sorted(MOCK_STOCKS.keys())

# 偏差阈值
THR_OK   = 0.05   # <5%  → 一致
THR_WARN = 0.20   # 5-20% → 需确认

# ── yfinance 字段映射 ─────────────────────────────────────────────────
# (mock字段名, yf_info字段, 缩放因子, 说明)
YF_AUTO_FIELDS = [
    ("current_price",       "currentPrice",           1.0,  "实时股价"),
    ("market_cap",          "marketCap",              1.0,  "市值（美元）"),
    ("beta",                "beta",                   1.0,  "5年月度Beta"),
    ("forward_pe",          "forwardPE",              1.0,  "Forward P/E"),
    ("ev_sales",            "enterpriseToRevenue",    1.0,  "EV/Sales (TTM)"),
    ("ev_ebitda",           "enterpriseToEbitda",     1.0,  "EV/EBITDA (TTM)"),
    ("gross_margin",        "grossMargins",           1.0,  "毛利率 GAAP TTM"),
    ("revenue_growth_yoy",  "revenueGrowth",          1.0,  "营收增速 YoY TTM"),
    ("eps_growth_yoy",      "earningsGrowth",         1.0,  "EPS增速 YoY TTM (GAAP)"),
    # fcf_margin 需计算：freeCashflow / totalRevenue
    ("fcf_margin",          "_CALC_FCF_MARGIN",       1.0,  "FCF利润率 (TTM FCF/Revenue)"),
]

# 财报手动核对字段
MANUAL_FIELDS = [
    ("operating_margin",              "Non-GAAP 营业利润率（排除SBC/摊销）"),
    ("debt_to_equity",                "总金融债务/股东权益（⚠️ 须含可转债）"),
    ("net_revenue_retention",         "NRR — SaaS/安全公司财报直接披露"),
    ("arr_growth_yoy",                "ARR增速 — SaaS/安全公司财报直接披露"),
    ("roic",                          "Non-GAAP ROIC — 需自行计算NOPAT/IC"),
    ("next_year_revenue_growth_est",  "NTM增速估算 — 需卖方共识(Bloomberg/FactSet)"),
    ("peg_ratio",                     "PEG（⚠️ 1yr/5yr口径可差3倍，建议统一用5yr）"),
    ("fcf_growth_yoy",                "FCF增速YoY — 同期季度/年度对比，需财报核实"),
]

# ── 数值合理性边界（与来源是否一致无关，纯粹的"这个数字看起来对不对"检查）──
# 命中区间外 → 无论 status 是不是 verified，一律标红，防止明显的小数点/单位错误蒙混过关
MAGNITUDE_BOUNDS = {
    "peg_ratio":                    (0.1,  8.0),
    "debt_to_equity":               (0.0,  5.0),
    "net_revenue_retention":        (0.70, 1.60),
    "roic":                         (-0.30, 0.60),
    "operating_margin":             (-1.0, 0.80),
    "next_year_revenue_growth_est": (-0.30, 1.00),
    "ev_ebitda":                    (0.0,  200.0),
}

def bounds_check(field: str, value) -> str | None:
    """数值是否超出合理范围。返回 None 表示没问题或没有边界定义。"""
    if value is None or not isinstance(value, (int, float)):
        return None
    bounds = MAGNITUDE_BOUNDS.get(field)
    if not bounds:
        return None
    lo, hi = bounds
    if value < lo or value > hi:
        return f"🔴 超出合理范围[{lo}, {hi}]，疑似单位/小数点错误，需重新核实"
    return None

def find_template_duplicates(overrides: dict, fields: list, min_count: int = 2) -> dict:
    """检测"已核对(verified)"的字段里，同一数值在≥min_count只股票间完全相同
    → 疑似复制粘贴的模板默认值蒙混过了"已核对"状态，而不是逐只股票真正查证过的数字。

    注意：只扫描 status=="verified" 的 override 条目，不扫描 mock_data.py 的原始mock基线——
    mock基线本来就是按行业分桶给的近似占位值，大量重复是预期行为；
    但一旦标成"verified"却和别的股票数值一模一样，就是今天在 GEV/ASML/MU 上发现的那类问题
    （fcf_growth_yoy=9.5、debt_to_equity=15.0、net_revenue_retention=1.25 在多只股票间重复且都标verified）。
    """
    dup = {}
    for field in fields:
        counts: dict = {}
        for ticker, tfields in overrides.items():
            entry = tfields.get(field)
            if not isinstance(entry, dict) or entry.get("status") != "verified":
                continue
            v = entry.get("value")
            if v is None:
                continue
            counts.setdefault(v, []).append(ticker)
        for v, tickers in counts.items():
            if len(tickers) >= min_count:
                dup.setdefault(field, []).append({"value": v, "tickers": tickers})
    return dup

# ── 估值失真预警：字段核实时点的股价 vs 现价 ──────────────────────────
# 这几个字段的比率里，价格是分子/分母的一部分——股价大幅变动后，
# 用锁定时的财报数据算出来的比率(PE/EV/PEG)可能已经过时，需要重新核实
PRICE_SENSITIVE_FIELDS = {"forward_pe", "peg_ratio", "ev_ebitda", "ev_sales"}

STALE_THR_WARN  = 0.15   # 15%  → 提醒
STALE_THR_ERROR = 0.30   # 30%  → 强提醒，比率类字段大概率已过时

def price_at_date(ticker_obj, date_str: str):
    """取 date_str 起 7 天内第一个交易日的收盘价。"""
    try:
        start = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        end   = start + datetime.timedelta(days=7)
        hist  = ticker_obj.history(start=start.strftime("%Y-%m-%d"),
                                    end=end.strftime("%Y-%m-%d"))
        if hist.empty:
            return None
        return float(hist["Close"].iloc[0])
    except Exception:
        return None

def check_price_staleness(ticker_obj, ticker_overrides: dict, current_price) -> dict:
    """按 verified_at 日期分组，只看价格敏感字段(PE/EV/PEG等)，
    检查基本面锁定当时的股价 vs 现价变动幅度，判断这些比率是否可能已经过时。
    返回 {verified_at日期: {price_then, price_now, pct_change, status, fields}}
    """
    if current_price is None:
        return {}
    by_date: dict = {}
    for field, entry in ticker_overrides.items():
        if field not in PRICE_SENSITIVE_FIELDS:
            continue
        if not isinstance(entry, dict) or entry.get("status") != "verified":
            continue
        d = entry.get("verified_at")
        if not d:
            continue
        by_date.setdefault(d, []).append(field)

    result = {}
    for d, fields in by_date.items():
        p_then = price_at_date(ticker_obj, d)
        if not p_then:
            continue
        pct = (current_price - p_then) / p_then
        if abs(pct) >= STALE_THR_ERROR:
            status = (f"🔴 股价较核实时({d})变动{pct*100:+.1f}%，"
                      f"{'/'.join(fields)} 大概率已过时，需重新核实")
        elif abs(pct) >= STALE_THR_WARN:
            status = (f"🟡 股价较核实时({d})变动{pct*100:+.1f}%，"
                      f"建议重新核实 {'/'.join(fields)}")
        else:
            status = f"✅ 股价较核实时({d})变动{pct*100:+.1f}%，在容忍范围内"
        result[d] = {
            "price_then": round(p_then, 2),
            "price_now":  round(current_price, 2),
            "pct_change": round(pct, 4),
            "status":     status,
            "fields":     fields,
        }
    return result

# 主观估算字段
SUBJECTIVE_FIELDS = [
    "ai_revenue_exposure_pct",
    "ai_profit_exposure_pct",
    "ai_growth_contribution_pct",
    "advanced_packaging_exposure_pct",
    "ai_order_backlog_exposure",
    "cybersecurity_ai_exposure_pct",
    "software_ai_platform_exposure_pct",
    "valuation_risk",
    "concentration_risk",
    "liquidity_risk",
    "market_expectation_score",
    "revenue_predictability_score",
    "actual_revenue_vs_consensus",
    "actual_eps_vs_consensus",
    "guidance_vs_consensus",
]

# SEC EDGAR 链接模板
def edgar_link(ticker: str) -> str:
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=10-Q&dateb=&owner=include&count=5"

# ── 辅助函数 ──────────────────────────────────────────────────────────
def pct_diff(our_val, yf_val) -> float | None:
    """计算相对偏差（基于 yf_val）。"""
    if our_val is None or yf_val is None:
        return None
    if yf_val == 0:
        return None
    return abs(our_val - yf_val) / abs(yf_val)

def status_label(diff: float | None) -> str:
    if diff is None:
        return "⚪ 无法对比（缺值）"
    if diff < THR_OK:
        return "✅ 一致"
    if diff < THR_WARN:
        return "🟡 需确认（5-20%偏差，可能口径不同）"
    return "🔴 数据异常（>20%偏差）"

def signed_pct(our, yf_v) -> str:
    if our is None or yf_v is None:
        return "N/A"
    d = (our - yf_v) / abs(yf_v) * 100 if yf_v != 0 else None
    if d is None:
        return "N/A"
    return f"{d:+.1f}%"

def fmt(v) -> str:
    if v is None:
        return "None"
    if isinstance(v, float):
        if abs(v) > 1e9:
            return f"{v/1e12:.3f}T" if abs(v) >= 1e12 else f"{v/1e9:.1f}B"
        if abs(v) < 10:
            return f"{v:.4f}"
        return f"{v:.2f}"
    return str(v)

# ── 加载 overrides ────────────────────────────────────────────────────
def load_overrides() -> dict:
    try:
        return json.loads(OVERRIDES_P.read_text(encoding="utf-8"))
    except Exception:
        return {}

def merged_data(ticker: str, overrides: dict) -> dict:
    """合并 mock_data + overrides（overrides 优先）。
    兼容新格式 {"field": {"value":..., "status":...}} 和旧格式 {"field": value}。
    """
    base = dict(MOCK_STOCKS.get(ticker, {}))
    for field, v in overrides.get(ticker, {}).items():
        base[field] = v["value"] if (isinstance(v, dict) and "value" in v) else v
    return base

# ── 主流程 ───────────────────────────────────────────────────────────
def run_validation():
    overrides = load_overrides()
    now_str   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 只在"已核对(verified)"的 override 条目里找跨股票重复值（不依赖yfinance，秒级完成）
    dup_fields    = [f for f, _ in MANUAL_FIELDS]
    template_dups = find_template_duplicates(overrides, dup_fields, min_count=2)

    report = {
        "generated_at": now_str,
        "tickers": TICKERS_CORE,
        "thresholds": {"ok": THR_OK, "warn": THR_WARN},
        "summary": {"auto_ok": 0, "auto_warn": 0, "auto_error": 0, "auto_na": 0,
                    "manual_total": 0, "manual_flagged": 0, "subjective_total": 0,
                    "price_stale_warn": 0, "price_stale_error": 0},
        "template_duplicates": template_dups,
        "results": {},
    }

    if template_dups:
        print(f"\n{'='*56}\n  ⚠️ 疑似模板默认值（同一数值在≥3只股票间重复）\n{'='*56}")
        for field, groups in template_dups.items():
            for g in groups:
                print(f"  🔴 {field} = {g['value']}  → {', '.join(g['tickers'])}")

    for ticker in TICKERS_CORE:
        print(f"\n{'='*56}")
        print(f"  {ticker}")
        print(f"{'='*56}")

        data = merged_data(ticker, overrides)

        # ─── 拉取 yfinance ────────────────────────────────────────
        print(f"  拉取 yfinance...", end=" ", flush=True)
        try:
            t    = yf.Ticker(ticker)
            info = t.info
            time.sleep(0.5)   # 礼貌性延迟
            print("OK")
        except Exception as e:
            print(f"失败: {e}")
            info = {}

        yf_fcf_margin = None
        fcf  = info.get("freeCashflow")
        rev  = info.get("totalRevenue")
        if fcf and rev and rev > 0:
            yf_fcf_margin = fcf / rev

        # ─── 估值失真预警（核实时股价 vs 现价）───────────────────
        staleness = check_price_staleness(t, overrides.get(ticker, {}), info.get("currentPrice"))
        if staleness:
            print(f"\n  [估值失真检查]")
            for d, s in staleness.items():
                print(f"  {s['status']}")
                if "🔴" in s["status"]:
                    report["summary"]["price_stale_error"] += 1
                elif "🟡" in s["status"]:
                    report["summary"]["price_stale_warn"] += 1

        # ─── 自动可验证字段 ─────────────────────────────────────
        auto_results = []
        for field, yf_key, scale, desc in YF_AUTO_FIELDS:
            our_val = data.get(field)
            if yf_key == "_CALC_FCF_MARGIN":
                yf_val = yf_fcf_margin
            else:
                raw = info.get(yf_key)
                yf_val = raw * scale if raw is not None else None

            diff   = pct_diff(our_val, yf_val)
            status = status_label(diff)

            # 特殊注释：已知口径差异
            note = ""
            if field == "eps_growth_yoy" and ticker in ("PLTR", "MRVL"):
                note = "⚠️ yfinance=GAAP口径；mock=Non-GAAP（已知差异，以mock为准）"
            elif field == "revenue_growth_yoy" and ticker == "PLTR":
                note = "⚠️ yfinance=TTM YoY；mock=最近单季度QoQ（已知差异）"
            elif field == "fcf_margin":
                note = "yfinance=TTM FCF/Revenue；mock可能为单季度口径"
            elif field == "market_cap" and ticker == "NVDA":
                note = "yfinance市值随实时价格变动，偏差属正常"

            entry = {
                "field":   field,
                "desc":    desc,
                "our_val": fmt(our_val),
                "yf_val":  fmt(yf_val),
                "diff_pct": f"{diff*100:.1f}%" if diff is not None else "N/A",
                "signed_diff": signed_pct(our_val, yf_val),
                "status":  status,
            }
            if note:
                entry["note"] = note

            auto_results.append(entry)

            # 打印
            s_icon = status.split(" ")[0]
            print(f"  {s_icon}  {field:32s} 我们:{fmt(our_val):>12}  YF:{fmt(yf_val):>12}  偏差:{entry['diff_pct']:>7}  {note}")

            # 汇总计数
            if "✅" in status:
                report["summary"]["auto_ok"] += 1
            elif "🟡" in status:
                report["summary"]["auto_warn"] += 1
            elif "🔴" in status:
                report["summary"]["auto_error"] += 1
            else:
                report["summary"]["auto_na"] += 1

        # ─── 财报手动核对字段 ──────────────────────────────────
        manual_results = []
        print(f"\n  [财报核对字段]")
        # 尝试获取最近季报日期（yfinance quarterly_financials）
        try:
            qfin = t.quarterly_financials
            last_report = str(qfin.columns[0].date()) if not qfin.empty else "未知"
        except Exception:
            last_report = "未知"

        for field, hint in MANUAL_FIELDS:
            our_val = data.get(field)

            # 自动数值合理性检查（不依赖yfinance，独立于"是否已核对"状态）
            b_warn = bounds_check(field, our_val)
            is_dup = any(ticker in g["tickers"]
                         for g in template_dups.get(field, []))

            if b_warn:
                status = b_warn
            elif is_dup:
                status = "🔴 疑似模板默认值（与其他股票数值完全相同，需逐个核实）"
            else:
                status = "📋 待人工核对"

            entry = {
                "field":       field,
                "hint":        hint,
                "our_val":     fmt(our_val),
                "last_report": last_report,
                "edgar_link":  edgar_link(ticker),
                "status":      status,
            }
            manual_results.append(entry)
            report["summary"]["manual_total"] += 1
            if b_warn or is_dup:
                report["summary"]["manual_flagged"] += 1
            icon = status.split(" ")[0]
            print(f"  {icon} {field:32s} 当前值:{fmt(our_val):>12}  最近财报:{last_report}  {status if (b_warn or is_dup) else ''}")

        # ─── 主观估算字段 ──────────────────────────────────────
        subj_results = []
        print(f"\n  [主观估算字段]")
        for field in SUBJECTIVE_FIELDS:
            our_val = data.get(field)
            if our_val is None:
                continue
            entry = {
                "field":  field,
                "our_val": fmt(our_val),
                "status": "🔵 估算值，无法自动验证",
            }
            subj_results.append(entry)
            report["summary"]["subjective_total"] += 1
            print(f"  🔵 {field:32s} {fmt(our_val)}")

        report["results"][ticker] = {
            "company":         data.get("company_name", ticker),
            "data_vintage":    data.get("_data_vintage", "未注明"),
            "yf_fetch_time":   now_str,
            "auto_validate":   auto_results,
            "manual_review":   manual_results,
            "subjective":      subj_results,
            "price_staleness": staleness,
        }

    # ─── 汇总打印 ─────────────────────────────────────────────────
    s = report["summary"]
    auto_total = s["auto_ok"] + s["auto_warn"] + s["auto_error"] + s["auto_na"]
    print(f"\n{'='*56}")
    print(f"  汇总")
    print(f"{'='*56}")
    print(f"  自动验证字段合计: {auto_total}")
    print(f"    ✅ 一致:       {s['auto_ok']}")
    print(f"    🟡 需确认:     {s['auto_warn']}")
    print(f"    🔴 数据异常:   {s['auto_error']}")
    print(f"    ⚪ 无法对比:   {s['auto_na']}")
    print(f"  📋 待人工核对（含数值合理性检查）: {s['manual_total']}  其中 🔴 自动标红: {s['manual_flagged']}")
    print(f"  🔵 主观估算:     {s['subjective_total']}")
    print(f"  ⚠️ 估值失真预警: 🔴 {s['price_stale_error']}  🟡 {s['price_stale_warn']}（股价较核实时大幅变动，PE/EV/PEG等比率需重查）")
    if template_dups:
        n_dup_fields = len(template_dups)
        n_dup_tickers = len({tk for groups in template_dups.values() for g in groups for tk in g["tickers"]})
        print(f"  ⚠️ 疑似模板默认值: {n_dup_fields} 个字段涉及 {n_dup_tickers} 只股票（见上方明细）")

    # ─── 写 JSON ────────────────────────────────────────────────
    OUT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  报告已保存: {OUT_FILE}")
    return report

if __name__ == "__main__":
    run_validation()
