"""
refresh_scores.py — ENERGREX 评分刷新工具
==========================================
更新策略（分层合并）：
  Layer 0: results_validated.csv raw_* 列 ← 保留所有手动字段（AI暴露/NRR/valuation_risk…）
  Layer 1: QUANT_META                     ← sector_tag / capex_rev
  Layer 2: QUANT_AI_EXPOSURE              ← AI 暴露字段补全（只填 None）
  Layer 3: mock_data（原始 10 只）         ← 完整基础数据（有则优先）
  Layer 4: yfinance 实时                  ← price/PE/margin/beta（并行拉取）
  Layer 5: 价格历史计算                    ← RSI14 / vs200DMA / maxDD_1y

可自动更新的字段（yfinance）：
  peg_ratio, ev_sales, forward_pe, fcf_yield, fcf_margin,
  revenue_growth_yoy, eps_growth_yoy, gross_margin, beta

需手动维护的字段（不自动覆盖）：
  ai_revenue_exposure_pct, datacenter_exposure_pct, net_revenue_retention,
  debt_to_equity, valuation_risk, concentration_risk,
  actual_revenue_vs_consensus, guidance_vs_consensus, …

命令行用法：
  python refresh_scores.py               # 刷新全部 84 只
  python refresh_scores.py NVDA TSLA     # 只刷新指定股票
  python refresh_scores.py --no-momentum # 跳过价格历史（快速模式）
"""

import sys, os, pathlib, datetime, logging, argparse
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# Windows: force UTF-8 stdout so Chinese chars don't crash cp1252 terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 路径设置 ──────────────────────────────────────────────
_ROOT    = pathlib.Path(__file__).parent
_SCORE   = _ROOT / "scoring"
_CSV_OUT = _ROOT / "results_validated.csv"

sys.path.insert(0, str(_SCORE))

from yfinance_fetcher import (
    fetch_portfolio_live_parallel, merge_live_into_mock,
    KNOWN_BAD_FIELDS, fetch_prices_only)
from quant_engine import score_ticker
from quant_data import QUANT_META, QUANT_AI_EXPOSURE, QUANT_STANDALONE

try:
    from mock_data import MOCK_STOCKS
except ImportError:
    MOCK_STOCKS: dict = {}

# ── 日志 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
_log = logging.getLogger("refresh")

# ════════════════════════════════════════════════════════
# CSV 列名映射（前缀匹配，容纳中文后缀）
# ════════════════════════════════════════════════════════

# raw_* 字段前缀 → 评分引擎字段名
_RAW_FIELD_MAP: list[tuple[str, str]] = [
    ("raw_peg",          "peg_ratio"),
    ("raw_ev_sales",     "ev_sales"),
    ("raw_forward_pe",   "forward_pe"),
    ("raw_fcf_yield",    "fcf_yield"),
    ("raw_rev_growth",   "revenue_growth_yoy"),
    ("raw_eps_growth",   "eps_growth_yoy"),
    ("raw_fwd_rev",      "next_year_revenue_growth_est"),
    ("raw_gross_margin", "gross_margin"),
    ("raw_fcf_margin",   "fcf_margin"),
    ("raw_roic",         "roic"),
    ("raw_de_ratio",     "debt_to_equity"),
    ("raw_nrr",          "net_revenue_retention"),
    ("raw_rsi14",        "rsi_14"),
    ("raw_vs200ma",      "price_vs_200dma"),
    ("raw_beta",         "beta"),
    ("raw_max_dd",       "max_drawdown_1y"),
    # ── 基本面分母（季度更新，价格驱动比率重算用）─────────────
    ("raw_current_price",      "current_price"),
    ("raw_shares_out",         "shares_outstanding"),
    ("raw_fwd_eps",            "forward_eps"),
    ("raw_ev_snap",            "enterprise_value_snap"),
    ("raw_price_at_ev_snap",   "price_at_ev_snapshot"),
    ("raw_net_debt",           "net_debt"),
    ("raw_rev_ttm",            "revenue_ttm"),
    ("raw_fcf_ttm",            "fcf_ttm"),
    ("raw_ebitda_ttm",         "ebitda_ttm"),
    # ── Damodaran 再投资率原材料（cashflow + financials）──────────
    ("raw_capex",              "capex_ttm"),
    ("raw_depr",               "da_ttm"),
    ("raw_rd",                 "rd_ttm"),
    ("raw_op_inc",             "operating_income_ttm"),
]

# 评分列前缀 → 内部字段名（app.py rename 逻辑保持一致）
_SCORE_PREFIX_MAP: list[tuple[str, str]] = [
    ("val_",     "valuation_score"),
    ("grw_",     "growth_score"),
    ("qlt_",     "quality_score"),
    ("ai_",      "ai_exposure_score"),
    ("exp_",     "expectation_gap_score"),
    ("mom_",     "momentum_score"),
    ("risk_",    "risk_penalty"),
    ("final_",   "final_score"),
    ("rating_",  "rating"),
    ("circuit_", "circuit"),
    ("company_", "company"),
    ("sector_",  "category"),
]

# 只允许 yfinance 覆盖的字段（手动字段不被更新）
_YFINANCE_UPDATABLE = {
    "peg_ratio", "ev_sales", "forward_pe", "fcf_yield", "fcf_margin",
    "revenue_growth_yoy", "eps_growth_yoy", "gross_margin", "beta",
    "rsi_14", "price_vs_200dma", "max_drawdown_1y",
    # 基本面分母（季度更新）
    "current_price", "shares_outstanding", "forward_eps",
    "enterprise_value_snap", "price_at_ev_snapshot",
    "net_debt", "revenue_ttm", "fcf_ttm", "ebitda_ttm",
    "capex_ttm", "da_ttm", "rd_ttm", "operating_income_ttm",
}

# 价格驱动重算结果列（比率，非基本面）— 写回 CSV 时用
_PRICE_DERIVED_FIELDS = {
    "forward_pe", "peg_ratio", "ev_ebitda", "ev_sales", "fcf_yield", "fcf_margin",
}

# 价格极速刷新不需要从 CSV 存储的列（动态计算，不写回）
_FUNDAMENTAL_CSV_COLS: dict[str, str] = {
    "raw_current_price_yf":    "n/a [--]",
    "raw_shares_out_yf":       "n/a [--]",
    "raw_fwd_eps_yf":          "n/a [--]",
    "raw_ev_snap_yf":          "n/a [--]",
    "raw_price_at_ev_snap_yf": "n/a [--]",
    "raw_net_debt_yf":         "n/a [--]",
    "raw_rev_ttm_yf":          "n/a [--]",
    "raw_fcf_ttm_yf":          "n/a [--]",
    "raw_ebitda_ttm_yf":       "n/a [--]",
    # Damodaran 再投资率原材料（cashflow + financials）
    "raw_capex_yf":            "n/a [--]",
    "raw_depr_yf":             "n/a [--]",
    "raw_rd_yf":               "n/a [--]",
    "raw_op_inc_yf":           "n/a [--]",
}


def _build_col_maps(df: pd.DataFrame) -> tuple[dict, dict]:
    """
    返回两个映射：
      raw_col_map:   field_name → CSV 列名（用于写回 raw_* 列）
      score_col_map: field_name → CSV 列名（用于写回 score 列）
    """
    raw_col_map: dict[str, str]   = {}
    score_col_map: dict[str, str] = {}
    taken_raw: set[str]   = set()
    taken_score: set[str] = set()

    for col in df.columns:
        # raw 前缀匹配
        for pfx, field in _RAW_FIELD_MAP:
            if col.startswith(pfx) and field not in taken_raw:
                raw_col_map[field] = col
                taken_raw.add(field)
                break
        # score 前缀匹配
        for pfx, field in _SCORE_PREFIX_MAP:
            if col.startswith(pfx) and field not in taken_score:
                score_col_map[field] = col
                taken_score.add(field)
                break

    return raw_col_map, score_col_map


def _parse_raw_str(val) -> float | None:
    """Parse "0.62 [yf]" / "85.2% [yf]" / "n/a [--]" → float or None."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    s = str(val).strip()
    if not s or s.startswith("n/a"):
        return None
    # strip source tag like " [yf]", " [mk]", " [--]"
    if "[" in s:
        s = s[:s.index("[")].strip()
    s = s.replace("%", "").strip()
    try:
        fval = float(s)
        return fval if np.isfinite(fval) else None
    except (ValueError, TypeError):
        return None


def _fmt_yf(val, pct: bool = False) -> str:
    """Format float value as display string with [yf] source tag."""
    if val is None:
        return "n/a [--]"
    if pct:
        return f"{val * 100:.1f}% [yf]"
    return f"{val:.4g} [yf]"


# Fields that should be displayed as percentages in raw_ columns
_PCT_FIELDS = {
    "fcf_yield", "revenue_growth_yoy", "eps_growth_yoy",
    "gross_margin", "fcf_margin", "roic",
    "price_vs_200dma", "max_drawdown_1y",
}


def _csv_baseline(row: pd.Series, raw_col_map: dict[str, str]) -> dict:
    """从 CSV 的一行提取所有原始字段值，作为手动字段的基础。"""
    data: dict = {}
    for field, col in raw_col_map.items():
        fval = _parse_raw_str(row.get(col))
        if fval is not None:
            # pct fields are stored as "85.2% [yf]" → divide back to 0-1 range
            if field in _PCT_FIELDS:
                fval = fval / 100.0
            data[field] = fval
    return data


# ════════════════════════════════════════════════════════
# 价格驱动的估值比率重算
# ════════════════════════════════════════════════════════

def _recompute_valuation_ratios(data: dict, ticker: str = "") -> dict:
    """
    用实时价格重算估值比率。
    所有比率 = f(live_price, 季度基本面)，只要价格变就立刻反映。

    优先用法：shares_outstanding 算市值/EV（精确）
    回退用法：enterprise_value_snap × (price/price_at_snap) 外插（近似）
    两者均无则跳过重算，保留 yfinance 快照值。
    """
    price = data.get("current_price")
    if not price or price <= 0:
        return data

    shares = data.get("shares_outstanding")
    fwd_eps = data.get("forward_eps")
    eps_g   = data.get("eps_growth_yoy")
    revenue = data.get("revenue_ttm")
    ebitda  = data.get("ebitda_ttm")
    fcf     = data.get("fcf_ttm")

    # ── 市值 & EV ─────────────────────────────────────────
    # 优先路径：精确算法（shares × price）
    bad_fields = KNOWN_BAD_FIELDS.get(ticker, set())
    if shares and shares > 0 and "shares_outstanding" not in bad_fields:
        net_debt = data.get("net_debt", 0.0) or 0.0
        mkt_cap  = price * shares
        ev       = mkt_cap + net_debt
    else:
        # 回退路径：EV快照按价格比例外插
        ev_snap   = data.get("enterprise_value_snap")
        price_snap = data.get("price_at_ev_snapshot")
        if ev_snap and price_snap and price_snap > 0:
            price_ratio = price / price_snap
            ev      = ev_snap * price_ratio
            net_debt = data.get("net_debt", 0.0) or 0.0
            mkt_cap  = ev - net_debt
        else:
            ev = mkt_cap = None  # 无法重算，跳过 EV 类

    # ── Forward PE & PEG（只需要 forward_eps，不需要 EV）──
    if fwd_eps and fwd_eps > 0:
        fpe = price / fwd_eps
        data["forward_pe"] = round(fpe, 2)
        if eps_g and eps_g > 0:
            # PEG = Forward PE / (eps growth %)；避免除以0
            data["peg_ratio"] = round(fpe / (eps_g * 100), 3)

    # ── EV 类比率 ─────────────────────────────────────────
    if ev and ev > 0:
        if ebitda and ebitda > 0:
            data["ev_ebitda"] = round(ev / ebitda, 2)
        if revenue and revenue > 0:
            data["ev_sales"]  = round(ev / revenue, 2)

    if mkt_cap and mkt_cap > 0:
        if fcf is not None:
            data["fcf_yield"] = round(fcf / mkt_cap, 5)
        if revenue and revenue > 0 and fcf is not None:
            data["fcf_margin"] = round(fcf / revenue, 4)

    return data


# ════════════════════════════════════════════════════════
# 动量指标（yfinance 价格历史）
# ════════════════════════════════════════════════════════

def _compute_momentum(ticker: str) -> dict:
    """从 1 年价格历史计算 RSI14 / vs200DMA / maxDD_1y。"""
    try:
        import yfinance as yf
        close = yf.Ticker(ticker).history(period="1y")["Close"]
        if close.empty or len(close) < 15:
            return {}

        # RSI-14
        delta  = close.diff()
        gain   = delta.clip(lower=0).rolling(14).mean()
        loss   = (-delta.clip(upper=0)).rolling(14).mean()
        rs     = gain / loss.replace(0, np.nan)
        rsi14  = float((100 - 100 / (1 + rs)).iloc[-1])

        # vs 200DMA
        periods = min(200, len(close))
        ma200   = float(close.rolling(periods).mean().iloc[-1])
        vs200   = float(close.iloc[-1]) / ma200 - 1 if ma200 else None

        # Max drawdown 1y
        peak  = close.cummax()
        maxdd = float(((close - peak) / peak).min())

        return {
            "rsi_14":          round(rsi14, 1),
            "price_vs_200dma": round(vs200, 4)  if vs200  is not None else None,
            "max_drawdown_1y": round(maxdd, 4),
        }
    except Exception as e:
        _log.warning(f"{ticker} momentum: {e}")
        return {}


def _fetch_momentum_bulk(
    tickers: list[str], workers: int = 5
) -> dict[str, dict]:
    result: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut_map = {ex.submit(_compute_momentum, t): t for t in tickers}
        for fut in as_completed(fut_map):
            result[fut_map[fut]] = fut.result()
    return result


def _safe_set(df: pd.DataFrame, mask, col: str, val) -> None:
    """Write val to df[col] at mask, coercing to the column's actual dtype."""
    try:
        df.loc[mask, col] = val
    except TypeError:
        # Arrow string columns reject non-str; float64 columns reject str
        try:
            col_dtype_str = str(df[col].dtype).lower()
            if "string" in col_dtype_str or "object" in col_dtype_str:
                df.loc[mask, col] = str(val) if val is not None else pd.NA
            else:
                df.loc[mask, col] = float(val)
        except Exception:
            pass  # leave column unchanged rather than crash


# ════════════════════════════════════════════════════════
# 主刷新函数
# ════════════════════════════════════════════════════════

def refresh_all(
    tickers: list[str] | None = None,
    skip_momentum: bool = False,
    workers: int = 5,
    csv_path: pathlib.Path = _CSV_OUT,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    刷新评分并写回 CSV，返回更新后的 DataFrame。

    Parameters
    ----------
    tickers       : 指定刷新的 Ticker 列表；None = 刷新全部
    skip_momentum : True = 跳过价格历史计算（节省约 60%时间）
    workers       : 并发线程数
    csv_path      : 读写的 CSV 路径
    verbose       : 控制台进度输出
    """

    def log(msg: str):
        if verbose:
            print(msg, flush=True)

    # ── 读 CSV ────────────────────────────────────────────
    if not csv_path.exists():
        raise FileNotFoundError(f"找不到 {csv_path}")
    df = pd.read_csv(csv_path, encoding="utf-8-sig")

    # 首次运行时自动添加基本面分母列
    _added_cols = []
    for col, default in _FUNDAMENTAL_CSV_COLS.items():
        if col not in df.columns:
            df[col] = default
            _added_cols.append(col)
    if _added_cols:
        log(f"  新增 CSV 列：{_added_cols}")

    raw_col_map, score_col_map = _build_col_maps(df)
    log(f"CSV 已加载：{len(df)} 只股票，{len(raw_col_map)} 个 raw 字段")

    # ── 读 user_overrides（最高优先级，人工核对值）──────────
    _ov_path = _ROOT / "scoring" / "user_overrides.json"
    try:
        import json as _json
        _user_overrides: dict = _json.loads(_ov_path.read_text(encoding="utf-8"))
        _ov_tickers = [t for t in _user_overrides if _user_overrides[t]]
        if _ov_tickers:
            log(f"  user_overrides.json 已加载：{len(_ov_tickers)} 只有核对数据 → {_ov_tickers}")
    except Exception:
        _user_overrides = {}

    all_tickers = df["ticker"].tolist()
    targets     = [t for t in (tickers or all_tickers) if t in all_tickers]
    log(f"刷新目标：{len(targets)} 只 → {targets[:8]}{'…' if len(targets)>8 else ''}")

    # ── Layer 4：并行拉取 yfinance ─────────────────────────
    log("⏳ yfinance 拉取中（并行）…")
    live_bulk = fetch_portfolio_live_parallel(targets, max_workers=workers)
    yf_ok = sum(1 for v in live_bulk.values() if not v.get("_errors"))
    log(f"  yfinance 成功 {yf_ok}/{len(targets)}")

    # ── Layer 5：动量指标 ─────────────────────────────────
    if skip_momentum:
        momentum_bulk: dict[str, dict] = {t: {} for t in targets}
        log("  跳过动量计算（--no-momentum）")
    else:
        log("⏳ 计算动量指标（RSI14 / vs200DMA / maxDD）…")
        momentum_bulk = _fetch_momentum_bulk(targets, workers)
        mom_ok = sum(1 for v in momentum_bulk.values() if v)
        log(f"  动量计算成功 {mom_ok}/{len(targets)}")

    # ── 逐 ticker 评分 ─────────────────────────────────────
    log("⏳ 评分中…")
    ok_cnt, err_cnt = 0, 0

    for ticker in targets:
        row_mask = df["ticker"] == ticker
        row      = df[row_mask].iloc[0]

        # Layer 0: CSV raw_ 字段（手动字段基础）
        data = _csv_baseline(row, raw_col_map)

        # Layer 1: QUANT_META（sector_tag / capex_rev）
        if ticker in QUANT_META:
            data.update(QUANT_META[ticker])

        # Layer 2: QUANT_AI_EXPOSURE（只填 None）
        if ticker in QUANT_AI_EXPOSURE:
            for k, v in QUANT_AI_EXPOSURE[ticker].items():
                if k not in data:
                    data[k] = v

        # Layer 3: mock_data（覆盖所有字段，原始 10 只）
        if ticker in MOCK_STOCKS:
            mock_base = dict(MOCK_STOCKS[ticker])
            mock_base.update(data)     # 已有的字段不被 mock 覆盖
            data = {**MOCK_STOCKS[ticker], **{k: v for k, v in data.items()
                                              if k not in _YFINANCE_UPDATABLE}}

        # Layer 4: yfinance（只更新 auto 字段）
        live = live_bulk.get(ticker, {})
        data = merge_live_into_mock(data, live)

        # Layer 4.5: 用实时价格重算估值比率（yfinance 已提供当前价格）
        data = _recompute_valuation_ratios(data, ticker)

        # Layer 5: 动量（直接覆盖，计算值更新）
        mom = momentum_bulk.get(ticker, {})
        for k, v in mom.items():
            if v is not None:
                data[k] = v

        # Layer 6: user_overrides（最高优先级：人工核对/修正值）
        tk_ov = _user_overrides.get(ticker, {})
        if tk_ov:
            applied = []
            for field, entry in tk_ov.items():
                val = entry.get("value") if isinstance(entry, dict) else entry
                if val is not None:
                    try:
                        data[field] = float(val)
                        applied.append(field)
                    except (TypeError, ValueError):
                        pass
            if applied and verbose:
                log(f"  {ticker} override 覆盖 {len(applied)} 个字段: {applied}")

        # 评分
        try:
            result = score_ticker(ticker, data)
            ok_cnt += 1
        except Exception as e:
            _log.error(f"{ticker} score_ticker error: {e}")
            err_cnt += 1
            continue

        # ── 写回 score 列 ──────────────────────────────────
        score_updates: dict[str, object] = {
            "valuation_score":      result.dim_scores.get("valuation",     0),
            "growth_score":         result.dim_scores.get("growth",        0),
            "quality_score":        result.dim_scores.get("quality",       0),
            "ai_exposure_score":    result.dim_scores.get("ai_exposure",   0),
            "expectation_gap_score":result.dim_scores.get("expectation_gap",0),
            "momentum_score":       result.dim_scores.get("momentum",      0),
            "risk_penalty":         result.risk_penalty,
            "final_score":          result.final_score,
            "rating":               str(result.rating),
            "circuit":              1.0 if result.circuit_triggered else 0.0,
        }
        for field, val in score_updates.items():
            col = score_col_map.get(field)
            if col and col in df.columns:
                _safe_set(df, row_mask, col, val)

        # ── 写回 raw_ 列（只更新 auto 字段，格式: "value [yf]"）──
        _r = data  # alias
        raw_updates: dict[str, str] = {}
        for field, is_pct in [
            ("peg_ratio",             False),
            ("ev_sales",              False),
            ("forward_pe",            False),
            ("fcf_yield",             True),
            ("revenue_growth_yoy",    True),
            ("eps_growth_yoy",        True),
            ("gross_margin",          True),
            ("fcf_margin",            True),
            ("beta",                  False),
            ("rsi_14",                False),
            ("price_vs_200dma",       True),
            ("max_drawdown_1y",       True),
            # 基本面分母（季度更新，存入 CSV 供极速刷新用）
            ("current_price",         False),
            ("shares_outstanding",    False),
            ("forward_eps",           False),
            ("enterprise_value_snap", False),
            ("price_at_ev_snapshot",  False),
            ("net_debt",              False),
            ("revenue_ttm",           False),
            ("fcf_ttm",               False),
            ("ebitda_ttm",            False),
            # Damodaran 再投资率原材料
            ("capex_ttm",             False),
            ("da_ttm",                False),
            ("rd_ttm",                False),
            ("operating_income_ttm",  False),
            # 重算后的估值比率（也写回，保持 CSV 最新）
            ("ev_ebitda",             False),
        ]:
            v = _r.get(field)
            if v is not None:
                raw_updates[field] = _fmt_yf(v, pct=is_pct)

        for field, fmt_val in raw_updates.items():
            col = raw_col_map.get(field)
            if col and col in df.columns:
                _safe_set(df, row_mask, col, fmt_val)

        # ── 清除 human_review_required（若用户已核对至少一个字段）──
        if "human_review_required" in df.columns and tk_ov:
            _has_verified = any(
                isinstance(entry, dict) and entry.get("status") == "verified"
                for entry in tk_ov.values()
            )
            if _has_verified:
                _safe_set(df, row_mask, "human_review_required", "FALSE")
                if verbose:
                    log(f"  {ticker} human_review_required → FALSE（用户已核对）")

        if verbose and ok_cnt % 10 == 0:
            log(f"  … {ok_cnt}/{len(targets)}")

    # ── 重新排序 ───────────────────────────────────────────
    final_col = score_col_map.get("final_score")
    if final_col:
        df = df.sort_values(final_col, ascending=False).reset_index(drop=True)

    # ── 更新刷新时间 ───────────────────────────────────────
    df["last_refreshed"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 写回 CSV ───────────────────────────────────────────
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log(f"\n✅ 完成：{ok_cnt} 只成功，{err_cnt} 只失败 → {csv_path.name}")

    return df


# ════════════════════════════════════════════════════════
# 极速价格刷新（只拉价格，基本面从 CSV 读，比全量快 ~10x）
# ════════════════════════════════════════════════════════

def refresh_prices_only(
    tickers: list[str] | None = None,
    csv_path: pathlib.Path = _CSV_OUT,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    极速刷新：只拉当前股价 → 重算估值比率 → 重新评分 → 写回 CSV。
    约 5–10 秒完成 84 只（对比全量刷新约 2–3 分钟）。
    前提：CSV 已有 raw_shares_out_yf / raw_fwd_eps_yf 等列（首次全量刷新后自动具备）。
    """
    def log(msg: str):
        if verbose:
            print(msg, flush=True)

    if not csv_path.exists():
        raise FileNotFoundError(f"找不到 {csv_path}")
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    raw_col_map, score_col_map = _build_col_maps(df)

    all_tickers = df["ticker"].tolist()
    targets = [t for t in (tickers or all_tickers) if t in all_tickers]
    log(f"⚡ 价格极速刷新：{len(targets)} 只")

    prices = fetch_prices_only(targets)
    log(f"  价格获取 {len(prices)}/{len(targets)}")

    _ov_path = _ROOT / "scoring" / "user_overrides.json"
    try:
        import json as _json
        _user_overrides: dict = _json.loads(_ov_path.read_text(encoding="utf-8"))
    except Exception:
        _user_overrides = {}

    ok_cnt = 0
    for ticker in targets:
        price = prices.get(ticker)
        if not price:
            continue

        row_mask = df["ticker"] == ticker
        row = df[row_mask].iloc[0]

        # CSV 基础数据（含存储的基本面分母）
        data = _csv_baseline(row, raw_col_map)

        # QUANT 补充
        if ticker in QUANT_META:
            data.update(QUANT_META[ticker])
        if ticker in QUANT_AI_EXPOSURE:
            for k, v in QUANT_AI_EXPOSURE[ticker].items():
                if k not in data:
                    data[k] = v

        # 注入实时价格
        data["current_price"] = price

        # 重算估值比率
        data = _recompute_valuation_ratios(data, ticker)

        # user_overrides 最高优先级
        tk_ov = _user_overrides.get(ticker, {})
        for field, entry in tk_ov.items():
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val is not None:
                try:
                    data[field] = float(val)
                except (TypeError, ValueError):
                    pass

        try:
            result = score_ticker(ticker, data)
            ok_cnt += 1
        except Exception as e:
            _log.error(f"{ticker} price_refresh score error: {e}")
            continue

        score_updates = {
            "valuation_score":      result.dim_scores.get("valuation",      0),
            "growth_score":         result.dim_scores.get("growth",         0),
            "quality_score":        result.dim_scores.get("quality",        0),
            "ai_exposure_score":    result.dim_scores.get("ai_exposure",    0),
            "expectation_gap_score":result.dim_scores.get("expectation_gap",0),
            "momentum_score":       result.dim_scores.get("momentum",       0),
            "risk_penalty":         result.risk_penalty,
            "final_score":          result.final_score,
            "rating":               str(result.rating),
            "circuit":              1.0 if result.circuit_triggered else 0.0,
        }
        for field, val in score_updates.items():
            col = score_col_map.get(field)
            if col and col in df.columns:
                _safe_set(df, row_mask, col, val)

        # 写回实时价格及重算比率
        for field, is_pct in [
            ("current_price", False),
            ("forward_pe",    False),
            ("peg_ratio",     False),
            ("ev_ebitda",     False),
            ("ev_sales",      False),
            ("fcf_yield",     True),
            ("fcf_margin",    True),
        ]:
            v = data.get(field)
            if v is not None:
                col = raw_col_map.get(field)
                if col and col in df.columns:
                    _safe_set(df, row_mask, col, _fmt_yf(v, pct=is_pct))

    final_col = score_col_map.get("final_score")
    if final_col:
        df = df.sort_values(final_col, ascending=False).reset_index(drop=True)

    df["last_refreshed"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log(f"\n⚡ 极速刷新完成：{ok_cnt} 只 → {csv_path.name}")
    return df


# ════════════════════════════════════════════════════════
# 命令行入口
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ENERGREX 评分刷新工具")
    parser.add_argument("tickers", nargs="*", help="指定刷新的 Ticker（留空=全部）")
    parser.add_argument("--no-momentum", action="store_true",
                        help="跳过价格历史计算（节省时间）")
    parser.add_argument("--price-only", action="store_true",
                        help="极速模式：只拉价格，重算比率评分（约5秒）")
    parser.add_argument("--workers", type=int, default=5,
                        help="并行线程数（默认 5）")
    args = parser.parse_args()

    if args.price_only:
        refresh_prices_only(tickers=args.tickers or None, verbose=True)
    else:
        refresh_all(
            tickers      = args.tickers or None,
            skip_momentum= args.no_momentum,
            workers      = args.workers,
            verbose      = True,
        )
