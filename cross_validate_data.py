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

TICKERS_CORE = ["NVDA", "AVGO", "PLTR", "PANW", "CRWD", "FTNT", "NOW", "ONTO", "MRVL", "SNOW"]

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
]

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

    report = {
        "generated_at": now_str,
        "tickers": TICKERS_CORE,
        "thresholds": {"ok": THR_OK, "warn": THR_WARN},
        "summary": {"auto_ok": 0, "auto_warn": 0, "auto_error": 0, "auto_na": 0,
                    "manual_total": 0, "subjective_total": 0},
        "results": {},
    }

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
            entry = {
                "field":       field,
                "hint":        hint,
                "our_val":     fmt(our_val),
                "last_report": last_report,
                "edgar_link":  edgar_link(ticker),
                "status":      "📋 待人工核对",
            }
            manual_results.append(entry)
            report["summary"]["manual_total"] += 1
            print(f"  📋 {field:32s} 当前值:{fmt(our_val):>12}  最近财报:{last_report}")

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
            "company":        data.get("company_name", ticker),
            "data_vintage":   data.get("_data_vintage", "未注明"),
            "yf_fetch_time":  now_str,
            "auto_validate":  auto_results,
            "manual_review":  manual_results,
            "subjective":     subj_results,
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
    print(f"  📋 待人工核对:   {s['manual_total']}")
    print(f"  🔵 主观估算:     {s['subjective_total']}")

    # ─── 写 JSON ────────────────────────────────────────────────
    OUT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  报告已保存: {OUT_FILE}")
    return report

if __name__ == "__main__":
    run_validation()
