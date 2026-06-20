"""
yfinance 实时数据接入模块
=========================
只刷新 H/M 置信度的市场/财务字段，
保留 mock_data 中全部 L 置信度手动字段（AI暴露、valuation_risk、NRR 等）

字段来源标注：
  🟢 H-Live  : yfinance info 直接读取（价格/市值/PE/PEG/EV指标）
  🟡 M-Live  : yfinance 计算推导（FCF yield = freeCashflow/marketCap）
  🔴 Manual  : mock_data 保留，无法自动获取（AI暴露/NRR/风险评估等）

⚠️ 已知口径差异（不覆盖这些字段）：
  - debt_to_equity  : yfinance 不含可转债，会严重低估高杠杆公司（如 AVGO）
  - operating_margin: yfinance 返回 GAAP口径，SaaS/AI公司 Non-GAAP 会高 20-40pp
  - fcf_margin      : yfinance freeCashflow 是 TTM，margin 需要除以 TTM revenue
"""

import yfinance as yf
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# 配置常量
# ─────────────────────────────────────────────
AUTO_REFRESH_HOURS = 4   # 数据超过此时间后侧边栏提示刷新

# yfinance 对特定 ticker 返回错误的字段，自动剔除（保留 mock_data 手动值）
# 原因见 SCORING_FORMULA.md § 数据注意事项
KNOWN_BAD_FIELDS: dict[str, set[str]] = {
    "NVDA": {"market_cap"},            # 拆股后股数计算错误，yfinance≈$485B vs 实际$4.91T
    "CRWD": {"ev_ebitda"},             # GAAP EBITDA≈0（高SBC），yfinance=2717x；保留Non-GAAP 105
    "SNOW": {"ev_ebitda"},             # GAAP EBITDA为负，yfinance=-73.6x；保留Non-GAAP 185
    "PLTR": {"revenue_growth_yoy"},    # yfinance误返84.7%；实际Q1 FY25 YoY +39%
    "MRVL": {"eps_growth_yoy"},        # yfinance=-80.4%(GAAP商誉摊销)；保留Non-GAAP 75%
}

# ─────────────────────────────────────────────
# yfinance info 字段 → 内部字段名 映射表
# ─────────────────────────────────────────────
_INFO_MAP: dict[str, str] = {
    "currentPrice":                 "current_price",
    "marketCap":                    "market_cap",
    "forwardPE":                    "forward_pe",
    "pegRatio":                     "peg_ratio",
    "enterpriseToEbitda":           "ev_ebitda",
    "enterpriseToRevenue":          "ev_sales",
    "beta":                         "beta",
    "grossMargins":                 "gross_margin",
    "revenueGrowth":                "revenue_growth_yoy",   # TTM YoY（季度数据足够近似）
    "earningsGrowth":               "eps_growth_yoy",
    "priceToSalesTrailing12Months": "ps_ratio",
    # ── 基本面分母（用于价格驱动的比率重算）────────────────────
    "sharesOutstanding":            "shares_outstanding",   # 流通股数
    "forwardEps":                   "forward_eps",          # NTM EPS 预测值
    "enterpriseValue":              "enterprise_value_snap", # EV快照（含债务）
}

# 不允许被 yfinance 覆盖的手动/专有字段
_NO_OVERRIDE: set[str] = {
    # D/E：yfinance 不含可转债，保留 mock 的人工修正值
    "debt_to_equity",
    # operating_margin：yfinance = GAAP，我们使用 Non-GAAP 口径
    "operating_margin",
    # 以下均为 L 置信度手动字段
    "ai_revenue_exposure_pct", "ai_profit_exposure_pct",
    "ai_growth_contribution_pct", "advanced_packaging_exposure_pct",
    "ai_order_backlog_exposure", "cybersecurity_ai_exposure_pct",
    "software_ai_platform_exposure_pct", "market_expectation_score",
    "valuation_risk", "concentration_risk", "liquidity_risk",
    "revenue_predictability_score", "net_revenue_retention",
    "arr_growth_yoy", "actual_revenue_vs_consensus",
    "actual_eps_vs_consensus", "guidance_vs_consensus",
    "company_name", "ticker",
}


def _safe(val) -> Optional[float]:
    """过滤 NaN/None/Infinity，返回干净的 float 或 None"""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f or abs(f) > 1e15:   # NaN or overflow
            return None
        return f
    except (TypeError, ValueError):
        return None


def fetch_live(ticker: str) -> dict:
    """
    获取单只股票实时数据。
    返回 {field: value, ..., '_errors': [...]}
    失败字段不包含在结果中（不影响 mock_data 原值）
    """
    result: dict = {"_ticker": ticker, "_errors": []}
    try:
        t    = yf.Ticker(ticker)
        info = t.info

        # ── 基础 info 字段 ──────────────────────────────
        for yf_key, our_key in _INFO_MAP.items():
            val = _safe(info.get(yf_key))
            if val is not None:
                result[our_key] = val

        # ── 基本面分母（原始绝对值，不受股价影响，季报后才变）──
        fcf  = _safe(info.get("freeCashflow"))
        mcap = _safe(info.get("marketCap"))
        rev  = _safe(info.get("totalRevenue"))
        ebitda_abs = _safe(info.get("ebitda"))

        if fcf  is not None: result["fcf_ttm"]      = fcf
        if rev  is not None: result["revenue_ttm"]  = rev
        if ebitda_abs is not None: result["ebitda_ttm"] = ebitda_abs

        # net_debt = totalDebt - totalCash（可为负，即净现金）
        total_debt = _safe(info.get("totalDebt")) or 0.0
        total_cash = _safe(info.get("totalCash")) or 0.0
        result["net_debt"] = total_debt - total_cash

        # 记录 EV 快照时对应的价格，用于比率外插
        cp = result.get("current_price")
        if cp:
            result["price_at_ev_snapshot"] = cp

        # ── FCF Yield = TTM freeCashflow / marketCap ──
        if fcf and mcap and mcap > 0:
            result["fcf_yield"] = round(fcf / mcap, 5)

        # ── FCF Margin = TTM freeCashflow / TTM revenue ──
        if fcf and rev and rev > 0:
            result["fcf_margin"] = round(fcf / rev, 4)

        # ── EV/Sales 备用（部分 ticker enterpriseToRevenue 返回 None）──
        if "ev_sales" not in result:
            ev  = _safe(info.get("enterpriseValue"))
            if ev and rev and rev > 0:
                result["ev_sales"] = round(ev / rev, 2)

        # ── 再投资率原材料（cashflow + financials，非 info 字段）──────
        # 用于 Damodaran tech 公式：增长再投资 = |CapEx| + R&D − D&A
        try:
            cf = t.cashflow
            if not cf.empty:
                _cf_col = cf.columns[0]
                _capex = cf.loc["Capital Expenditure", _cf_col] \
                    if "Capital Expenditure" in cf.index else None
                _da = cf.loc["Depreciation And Amortization", _cf_col] \
                    if "Depreciation And Amortization" in cf.index else None
                v = _safe(_capex)
                if v is not None: result["capex_ttm"] = v   # 为负数（现金流出）
                v = _safe(_da)
                if v is not None: result["da_ttm"]    = v   # 为正数
            fin = t.financials
            if not fin.empty:
                _fin_col = fin.columns[0]
                _rd = fin.loc["Research And Development", _fin_col] \
                    if "Research And Development" in fin.index else None
                _oi = fin.loc["Operating Income", _fin_col] \
                    if "Operating Income" in fin.index else None
                v = _safe(_rd)
                if v is not None: result["rd_ttm"]               = v
                v = _safe(_oi)
                if v is not None: result["operating_income_ttm"] = v
        except Exception:
            pass   # 非致命：calc_damodaran_report 会自动降级到估算

        # ── yfinance D/E 仅记录参考值，不覆盖 mock（含可转债口径）──
        de_raw = _safe(info.get("debtToEquity"))
        if de_raw is not None:
            # yfinance 返回 %形式（6.5 = 6.5% = 0.065x）
            result["_debt_to_equity_yf_ref"] = round(de_raw / 100, 3)

        # ── 剔除已知 yfinance 数据错误字段 ──────────────────
        for bad_field in KNOWN_BAD_FIELDS.get(ticker, set()):
            if bad_field in result:
                del result[bad_field]
                result.setdefault("_skipped_bad", []).append(bad_field)

    except Exception as e:
        result["_errors"].append(f"general: {e}")

    return result


def fetch_portfolio_live(tickers: list[str]) -> dict[str, dict]:
    """串行批量获取（兼容保留，推荐改用 fetch_portfolio_live_parallel）"""
    return {t: fetch_live(t) for t in tickers}


def fetch_portfolio_live_parallel(
    tickers: list[str], max_workers: int = 5
) -> dict[str, dict]:
    """
    并行批量获取，速度比串行快约 3–5x（yfinance 是 I/O 密集型）。
    max_workers=5 在 yfinance 免费 API 下不触发限速。
    """
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(fetch_live, t): t for t in tickers}
        for future in as_completed(future_map):
            ticker = future_map[future]
            try:
                results[ticker] = future.result()
            except Exception as e:
                results[ticker] = {"_ticker": ticker, "_errors": [str(e)]}
    return results


def merge_live_into_mock(mock: dict, live: dict) -> dict:
    """
    将 live 数据合并进 mock_data。
    - 仅覆盖 H/M 置信度字段
    - _NO_OVERRIDE 内的字段永远保留 mock 原值
    - 新增 '_live_fields' 记录哪些字段来自实时数据
    """
    merged = dict(mock)
    live_applied: list[str] = []

    for field, val in live.items():
        if field.startswith("_"):
            continue
        if field in _NO_OVERRIDE:
            continue
        if _safe(val) is None and not isinstance(val, (str, bool)):
            continue
        merged[field] = val
        live_applied.append(field)

    merged["_live_fields"]   = live_applied
    merged["_data_vintage"]  = "live"

    return merged


# ─────────────────────────────────────────────
# 便捷函数：获取并合并单只股票
# ─────────────────────────────────────────────
def get_live_merged(ticker: str, mock_data: dict) -> dict:
    live = fetch_live(ticker)
    return merge_live_into_mock(mock_data, live)


# ─────────────────────────────────────────────
# 极速价格专用批量获取（比全量 info 快 ~10x）
# 只返回 {ticker: price}，用于价格驱动的比率重算
# ─────────────────────────────────────────────
def fetch_prices_only(tickers: list[str]) -> dict[str, float]:
    """
    仅获取当前价格，不拉财务指标。
    单次请求约 1–2 秒（84只），适合高频调用。
    """
    import yfinance as yf
    result: dict[str, float] = {}
    try:
        if len(tickers) == 1:
            fi = yf.Ticker(tickers[0]).fast_info
            p = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
            if p:
                result[tickers[0]] = float(p)
            return result

        # 批量下载（1d 数据量小，速度快）
        import pandas as pd
        raw = yf.download(
            tickers, period="2d", progress=False,
            auto_adjust=True, group_by="ticker",
        )
        if raw.empty:
            return result

        if isinstance(raw.columns, pd.MultiIndex):
            for t in tickers:
                try:
                    s = raw[t]["Close"].dropna()
                    if not s.empty:
                        result[t] = float(s.iloc[-1])
                except (KeyError, TypeError):
                    pass
        else:
            # 单 ticker 时 yf.download 返回扁平列
            try:
                s = raw["Close"].dropna()
                if not s.empty and tickers:
                    result[tickers[0]] = float(s.iloc[-1])
            except (KeyError, TypeError):
                pass
    except Exception as e:
        warnings.warn(f"fetch_prices_only failed: {e}")
    return result
