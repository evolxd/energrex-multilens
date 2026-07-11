"""
AI Growth Stock Valuation Scoring Engine
==========================================
每个子分都有三个机构锚点（满分/中性/零分），
使用 sigmoid + linear_clamp 连续函数，无阶梯跳变。

子分范围：0–100
最终分：weighted sum - risk_penalty，仍在 0–100
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# ─────────────────────────────────────────────
# 公司类型枚举
# ─────────────────────────────────────────────
class CompanyCategory(str, Enum):
    AI_CHIP       = "AI芯片"          # NVDA, AVGO, MRVL, AMD, TSM, MU ...
    AI_SOFTWARE   = "AI软件/SaaS"     # PLTR, SNOW, NOW, DDOG, NET, APP ...
    CYBERSECURITY = "网络安全"         # PANW, FTNT, CRWD, ZS, OKTA
    SEMI_EQUIP    = "半导体设备"        # ONTO, ASML, AMAT, LRCX, KLAC, ROK ...
    MEGA_TECH     = "大型科技"          # MSFT, GOOGL, AMZN, META, AAPL, ORCL ...


# ─────────────────────────────────────────────
# Ticker → Category 静态映射表
# ─────────────────────────────────────────────
TICKER_CATEGORY: dict[str, CompanyCategory] = {
    # ── AI芯片：GPU/ASIC/FPGA + 内存 + 服务器硬件 + 光互联 + 边缘芯片
    "NVDA": CompanyCategory.AI_CHIP,
    "AVGO": CompanyCategory.AI_CHIP,
    "MRVL": CompanyCategory.AI_CHIP,
    "AMD":  CompanyCategory.AI_CHIP,
    "INTC": CompanyCategory.AI_CHIP,
    "ARM":  CompanyCategory.AI_CHIP,
    "MU":   CompanyCategory.AI_CHIP,
    "SMCI": CompanyCategory.AI_CHIP,
    "DELL": CompanyCategory.AI_CHIP,
    "VRT":  CompanyCategory.AI_CHIP,
    "HPE":  CompanyCategory.AI_CHIP,
    "ANET": CompanyCategory.AI_CHIP,
    "CSCO": CompanyCategory.AI_CHIP,
    "CLS":  CompanyCategory.AI_CHIP,
    "JBL":  CompanyCategory.AI_CHIP,
    "FN":   CompanyCategory.AI_CHIP,
    "COHR": CompanyCategory.AI_CHIP,
    "LITE": CompanyCategory.AI_CHIP,
    "PSTG": CompanyCategory.AI_CHIP,
    "QCOM": CompanyCategory.AI_CHIP,
    "NXPI": CompanyCategory.AI_CHIP,
    "TXN":  CompanyCategory.AI_CHIP,
    "ADI":  CompanyCategory.AI_CHIP,
    "MCHP": CompanyCategory.AI_CHIP,
    "CRUS": CompanyCategory.AI_CHIP,
    "TSLA": CompanyCategory.AI_CHIP,
    "MBLY": CompanyCategory.AI_CHIP,
    "AOSL": CompanyCategory.AI_CHIP,
    # ── AI软件/SaaS：平台/云/DevOps/垂直AI/高风险AI
    "PLTR": CompanyCategory.AI_SOFTWARE,
    "SNOW": CompanyCategory.AI_SOFTWARE,
    "MDB":  CompanyCategory.AI_SOFTWARE,
    "NOW":  CompanyCategory.AI_SOFTWARE,
    "CRM":  CompanyCategory.AI_SOFTWARE,
    "DDOG": CompanyCategory.AI_SOFTWARE,
    "NET":  CompanyCategory.AI_SOFTWARE,
    "GTLB": CompanyCategory.AI_SOFTWARE,
    "DT":   CompanyCategory.AI_SOFTWARE,
    "PATH": CompanyCategory.AI_SOFTWARE,
    "CDNS": CompanyCategory.AI_SOFTWARE,
    "SNPS": CompanyCategory.AI_SOFTWARE,
    "PTC":  CompanyCategory.AI_SOFTWARE,
    "ADSK": CompanyCategory.AI_SOFTWARE,
    "TYL":  CompanyCategory.AI_SOFTWARE,
    "ADBE": CompanyCategory.AI_SOFTWARE,
    "WDAY": CompanyCategory.AI_SOFTWARE,
    "HUBS": CompanyCategory.AI_SOFTWARE,
    "TEAM": CompanyCategory.AI_SOFTWARE,
    "APP":  CompanyCategory.AI_SOFTWARE,
    "ZM":   CompanyCategory.AI_SOFTWARE,
    "DOCU": CompanyCategory.AI_SOFTWARE,
    "NTNX": CompanyCategory.AI_SOFTWARE,
    "AFRM": CompanyCategory.AI_SOFTWARE,
    "ACN":  CompanyCategory.AI_SOFTWARE,
    "EXLS": CompanyCategory.AI_SOFTWARE,
    "SOUN": CompanyCategory.AI_SOFTWARE,
    "BBAI": CompanyCategory.AI_SOFTWARE,
    "AI":   CompanyCategory.AI_SOFTWARE,
    "RXRX": CompanyCategory.AI_SOFTWARE,
    "SDGR": CompanyCategory.AI_SOFTWARE,
    "TEM":  CompanyCategory.AI_SOFTWARE,
    "U":    CompanyCategory.AI_SOFTWARE,
    "LUNR": CompanyCategory.AI_SOFTWARE,
    "ESTC": CompanyCategory.AI_SOFTWARE,
    "VEEV": CompanyCategory.AI_SOFTWARE,
    "TTD":  CompanyCategory.AI_SOFTWARE,
    "CFLT": CompanyCategory.AI_SOFTWARE,
    # ── AI芯片（追加）
    "AMBA": CompanyCategory.AI_CHIP,
    # ── 网络安全
    "PANW": CompanyCategory.CYBERSECURITY,
    "FTNT": CompanyCategory.CYBERSECURITY,
    "CRWD": CompanyCategory.CYBERSECURITY,
    "ZS":   CompanyCategory.CYBERSECURITY,
    "OKTA": CompanyCategory.CYBERSECURITY,
    "S":    CompanyCategory.CYBERSECURITY,
    "CYBR": CompanyCategory.CYBERSECURITY,
    # ── 半导体设备 + 工业AI
    "ONTO": CompanyCategory.SEMI_EQUIP,
    "AMAT": CompanyCategory.SEMI_EQUIP,
    "LRCX": CompanyCategory.SEMI_EQUIP,
    "KLAC": CompanyCategory.SEMI_EQUIP,
    "ISRG": CompanyCategory.SEMI_EQUIP,
    "ROK":  CompanyCategory.SEMI_EQUIP,
    "GE":   CompanyCategory.SEMI_EQUIP,
    "HON":  CompanyCategory.SEMI_EQUIP,
    "GRMN": CompanyCategory.SEMI_EQUIP,
    "ACLS": CompanyCategory.SEMI_EQUIP,
    "ASML": CompanyCategory.SEMI_EQUIP,   # 光刻设备，此前缺失映射，误落入默认AI_SOFTWARE
    "GEV":  CompanyCategory.SEMI_EQUIP,   # 电力/电气化设备，同GE/HON/ROK归入工业设备簇
    # ── 大型科技平台（超大市值，ERG不适用）
    "MSFT":  CompanyCategory.MEGA_TECH,
    "GOOGL": CompanyCategory.MEGA_TECH,
    "AMZN":  CompanyCategory.MEGA_TECH,
    "META":  CompanyCategory.MEGA_TECH,
    "AAPL":  CompanyCategory.MEGA_TECH,
    "ORCL":  CompanyCategory.MEGA_TECH,
    "IBM":   CompanyCategory.MEGA_TECH,
    "NFLX":  CompanyCategory.MEGA_TECH,
}

def get_category(ticker: str) -> CompanyCategory:
    """自动识别公司类型，未知 ticker 默认 AI_SOFTWARE"""
    return TICKER_CATEGORY.get(ticker.upper(), CompanyCategory.AI_SOFTWARE)


# ─────────────────────────────────────────────
# 分类型权重配置（所有权重之和 = 1.00）
# ─────────────────────────────────────────────
@dataclass
class WeightConfig:
    valuation:       float
    growth:          float
    quality:         float
    ai_exposure:     float
    expectation_gap: float

    def validate(self):
        total = (self.valuation + self.growth + self.quality
                 + self.ai_exposure + self.expectation_gap)
        assert abs(total - 1.0) < 1e-6, f"权重之和={total:.4f}，必须=1.00"
        return self


WEIGHT_CONFIG: dict[CompanyCategory, WeightConfig] = {
    # AI芯片：最看AI暴露 + EV/EBITDA + FCF
    CompanyCategory.AI_CHIP: WeightConfig(
        valuation=0.25, growth=0.25, quality=0.20,
        ai_exposure=0.20, expectation_gap=0.10
    ).validate(),

    # AI软件：Rule of 40 + NRR + PS最重要
    CompanyCategory.AI_SOFTWARE: WeightConfig(
        valuation=0.20, growth=0.30, quality=0.25,
        ai_exposure=0.15, expectation_gap=0.10
    ).validate(),

    # 网络安全：ARR增长 + FCF + 平台化
    CompanyCategory.CYBERSECURITY: WeightConfig(
        valuation=0.20, growth=0.25, quality=0.30,
        ai_exposure=0.10, expectation_gap=0.15
    ).validate(),

    # 半导体设备 + 工业AI：周期 + 先进封装暴露 + EV/EBITDA
    CompanyCategory.SEMI_EQUIP: WeightConfig(
        valuation=0.30, growth=0.20, quality=0.25,
        ai_exposure=0.15, expectation_gap=0.10
    ).validate(),

    # 大型科技平台：FCF质量最重要；AI是加速器而非全部业务
    CompanyCategory.MEGA_TECH: WeightConfig(
        valuation=0.20, growth=0.25, quality=0.30,
        ai_exposure=0.15, expectation_gap=0.10
    ).validate(),
}


# ─────────────────────────────────────────────
# 基础评分工具函数
# ─────────────────────────────────────────────
def linear_clamp(value: float, worst: float, best: float) -> float:
    """
    线性映射：[worst, best] → [0, 100]
    超出范围自动 clamp
    例：best=50%, worst=0% → 25% → 50分
    """
    if best == worst:
        return 50.0
    score = (value - worst) / (best - worst) * 100
    return float(np.clip(score, 0, 100))


def inverse_clamp(value: float, best: float, worst: float) -> float:
    """
    反向线性映射（越低越好）：[best, worst] → [100, 0]
    例：PEG best=0.5, worst=2.5 → PEG=1.0 → 75分
    """
    return linear_clamp(value, worst=worst, best=best)


def sigmoid_score(value: float, center: float, steepness: float = 5.0,
                  higher_is_better: bool = True) -> float:
    """
    Sigmoid 连续评分（用于非线性收益递减场景）
    center：中性点对应 50 分
    steepness：越大曲线越陡
    """
    x = (value - center) / (abs(center) + 1e-9) * steepness
    if not higher_is_better:
        x = -x
    score = 100 / (1 + np.exp(-x))
    return float(np.clip(score, 0, 100))


def percentile_score(value: float, peer_values: list[float],
                     higher_is_better: bool = True) -> float:
    """
    同类百分位评分（动态基准核心）
    peer_values：同行业 peer 的同一指标值列表
    """
    if not peer_values or np.isnan(value):
        return 50.0
    from scipy import stats
    pct = stats.percentileofscore(peer_values, value, kind='rank')
    return float(pct if higher_is_better else 100 - pct)


def safe_val(value, default=0.0) -> float:
    """处理 None / NaN"""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return default
    return float(value)


# ─────────────────────────────────────────────
# Damodaran 估值纪律框架 — 常量与辅助函数
# ─────────────────────────────────────────────
_RISK_FREE_RATE      = 0.043   # 10Y US Treasury，2026年6月
_EQUITY_RISK_PREMIUM = 0.048   # Damodaran 隐含 ERP，2026年1月

_MATURE_EV_SALES: dict[str, float] = {
    "AI芯片":      5.0,
    "AI软件/SaaS":  8.0,
    "网络安全":      6.0,
    "半导体设备":     4.0,
    "大型科技":      4.0,
}


def calc_wacc(data: dict, category: CompanyCategory) -> float:
    """
    简化 WACC = Ke × We + Kd_税后 × Wd
    绝大多数 AI 公司净现金，WACC ≈ Ke = Rf + β × ERP
    """
    beta           = safe_val(data.get("beta"), 1.2)
    cost_of_equity = _RISK_FREE_RATE + beta * _EQUITY_RISK_PREMIUM

    net_debt = safe_val(data.get("net_debt"), 0.0)
    mkt_cap  = safe_val(data.get("market_cap"))

    if not mkt_cap or mkt_cap <= 0 or net_debt <= 0:
        return round(float(cost_of_equity), 4)

    total_capital    = mkt_cap + net_debt
    w_eq             = mkt_cap / total_capital
    w_dt             = net_debt / total_capital
    cost_of_debt_at  = (_RISK_FREE_RATE + 0.015) * (1 - 0.21)

    return round(float(w_eq * cost_of_equity + w_dt * cost_of_debt_at), 4)


def calc_implied_rev_cagr(data: dict, category: CompanyCategory, years: int = 5) -> Optional[float]:
    """
    反向 DCF：当前 EV/Sales 隐含的未来 N 年营收 CAGR
    假设 N 年后以 _MATURE_EV_SALES 倍数交易
    """
    ev_sales = safe_val(data.get("ev_sales"))
    rev_ttm  = safe_val(data.get("revenue_ttm"))

    if not ev_sales or ev_sales <= 0 or not rev_ttm or rev_ttm <= 0:
        return None

    target = _MATURE_EV_SALES.get(category.value, 5.0)
    if ev_sales <= target:
        return 0.0

    future_rev = (ev_sales * rev_ttm) / target
    return round(float((future_rev / rev_ttm) ** (1.0 / years) - 1), 4)


def calc_damodaran_report(ticker: str, data: dict, category: CompanyCategory) -> dict:
    """
    Damodaran 维度报告：WACC / 超额回报 / 市场隐含叙事 / 再投资效率
    可在 app.py 的单股详情页直接调用，无需重新评分
    """
    wacc           = safe_val(data.get("_wacc")) or calc_wacc(data, category)
    roic           = safe_val(data.get("roic"), 0.15)
    beta           = safe_val(data.get("beta"), 1.2)
    cost_of_equity = round(_RISK_FREE_RATE + beta * _EQUITY_RISK_PREMIUM, 4)
    excess_return  = round(roic - wacc, 4)

    implied_cagr = calc_implied_rev_cagr(data, category)
    fwd_growth   = safe_val(
        data.get("next_year_revenue_growth_est"),
        safe_val(data.get("revenue_growth_yoy"), 0.20),
    )

    if implied_cagr is not None and implied_cagr > 0:
        ratio = fwd_growth / implied_cagr
        if   ratio >= 1.2:  narrative = "✅ 叙事领先定价"
        elif ratio >= 0.85: narrative = "⚖️ 叙事匹配定价"
        elif ratio >= 0.60: narrative = "⚠️ 叙事略低于定价"
        else:               narrative = "🔴 叙事远低于定价"
    elif implied_cagr == 0.0:
        narrative = "📉 市场隐含零增长"
    else:
        narrative = "— 数据不足"

    if   excess_return >= 0.15: quality_verdict = "✅ 大幅创造价值"
    elif excess_return >= 0.05: quality_verdict = "✅ 温和创造价值"
    elif excess_return >= -0.02: quality_verdict = "⚖️ 勉强覆盖资本成本"
    else:                        quality_verdict = "🔴 低于资本成本"

    # ── 再投资率（Damodaran tech 公式：|CapEx| + R&D − D&A）────────
    capex  = safe_val(data.get("capex_ttm"))             # 通常为负数
    da     = safe_val(data.get("da_ttm"))
    rd     = safe_val(data.get("rd_ttm"))
    op_inc = safe_val(data.get("operating_income_ttm"))
    TAX    = 0.21

    reinvestment_method = "estimate"
    if capex is not None and da is not None and op_inc is not None and op_inc > 0:
        capex_abs           = abs(capex)
        rd_val              = rd or 0.0
        growth_reinvest     = capex_abs + rd_val - da      # 可为负（轻资产公司 D&A 超 CapEx）
        nopat               = op_inc * (1 - TAX)
        if nopat > 0:
            reinvestment_rate = round(max(growth_reinvest, 0) / nopat, 4)
            sustainable_growth = round(roic * reinvestment_rate, 4)
            reinvestment_method = "proper" if rd_val > 0 else "capex_only"
        else:
            reinvestment_rate = sustainable_growth = None
    else:
        # 降级：用营收增速/ROIC 恒等式估算
        rev_g = safe_val(data.get("revenue_growth_yoy"), 0.20)
        reinvestment_rate  = round(rev_g / roic, 4) if roic > 0 else None
        sustainable_growth = round(roic * reinvestment_rate, 4) if reinvestment_rate else None

    return {
        "wacc":                  wacc,
        "cost_of_equity":        cost_of_equity,
        "beta":                  beta,
        "excess_return":         excess_return,
        "quality_verdict":       quality_verdict,
        "implied_rev_cagr_5y":  implied_cagr,
        "analyst_fwd_growth":    round(fwd_growth, 4),
        "narrative_consistency": narrative,
        "reinvestment_rate":     reinvestment_rate,
        "reinvestment_method":   reinvestment_method,   # "proper"|"capex_only"|"estimate"
        "sustainable_growth":    sustainable_growth,
        "target_ev_sales":       _MATURE_EV_SALES.get(category.value, 5.0),
        "ev_sales_current":      safe_val(data.get("ev_sales")),
    }


# ─────────────────────────────────────────────
# 各子评分函数
# ─────────────────────────────────────────────

def calc_valuation_score(data: dict, category: CompanyCategory,
                         peer_vals: Optional[dict] = None) -> float:
    """
    估值分 (0–100)：越便宜越高分
    机构锚点：MS/GS 2026、Meritech Capital 2026、Peter Lynch PEG

    指标选择逻辑：
    ┌──────────────┬────────────────────────────────────────────────────────┐
    │ PEG          │ 所有类型均用。PE/增长率的复合指标，Lynch基准1.0=中性     │
    │ EV/EBITDA    │ 芯片/设备首要；SaaS公司EBITDA因高SBC失真权重低           │
    │ ERG          │ EV/Rev ÷ 收入增长(%)，Meritech SaaS/AI芯片核心指标      │
    │ Forward PE   │ 机构报告最常引用的绝对估值锚点；PEG只隐含PE，不能替代    │
    │ FCF Yield    │ 现金回报率；比E/P更准确反映真实盈利能力                  │
    └──────────────┴────────────────────────────────────────────────────────┘

    ── 为什么 Forward PE 之前没有权重？──────────────────────────────────────
    PEG = PE / 增长率，理论上已包含PE。但实践中：
      1. PEG 的增长率假设内嵌在第三方数据里，口径不透明
      2. 机构报告（GS/MS/JPM）均单独引用 Forward PE 作为绝对锚点
      3. 部分机构授权书要求 PE < N 的硬性筛选，PEG 无法替代
    结论：Forward PE 作为 10-15% 权重的独立验证项，不构成与 PEG 的双重计算。
    ─────────────────────────────────────────────────────────────────────────

    ── ERG 单位修正（历史 bug）────────────────────────────────────────────
    原始代码：erg = ev_sales / rev_growth (0.33)  → ERG=31.8
    阈值设定：best=0.10, worst=0.60  （按增长率为%形式：33 设计）
    结果：所有AI软件公司 ERG >> 0.60，evrev_score = 0（最重要的50%权重归零）
    修正：rev_growth 统一转为 % 形式再除，erg = ev_sales / (rev_growth*100)
    ─────────────────────────────────────────────────────────────────────────
    """
    # ── PEG Ratio（Lynch基准：1.0=50分，0.5=满分，2.5=零分）
    peg      = safe_val(data.get("peg_ratio"),  default=2.0)
    peg_score = inverse_clamp(peg, best=0.5, worst=2.5)

    # ── EV/EBITDA（芯片/设备核心；SaaS公司高SBC致EBITDA失真，权重低）
    ev_ebitda = safe_val(data.get("ev_ebitda"), default=30.0)
    if category in (CompanyCategory.AI_CHIP, CompanyCategory.SEMI_EQUIP):
        evebitda_score = inverse_clamp(ev_ebitda, best=15.0, worst=55.0)
    else:
        evebitda_score = inverse_clamp(ev_ebitda, best=20.0, worst=80.0)

    # ── ERG / EV/Revenue（按类型选择公式）
    ev_sales   = safe_val(data.get("ev_sales"), default=15.0)
    rev_growth = safe_val(data.get("revenue_growth_yoy"), default=0.20)
    rev_g_pct  = max(rev_growth * 100, 1.0)  # 转为 % 形式（修正历史 bug）

    if category == CompanyCategory.AI_SOFTWARE:
        # ERG = EV/Rev ÷ 增长率(%)，Meritech Capital 2026 基准
        # best=0.10（EV/Rev=5x at 50%增长），worst=0.60（EV/Rev=30x at 50%）
        erg = ev_sales / rev_g_pct
        evrev_score = inverse_clamp(erg, best=0.10, worst=0.60)

    elif category == CompanyCategory.AI_CHIP:
        # 高增长AI芯片同样适用ERG；阈值比软件宽松（硬件毛利率更低）
        # best=0.15（NVDA级：EV/Rev=13x at 85%增长），worst=0.80
        erg = ev_sales / rev_g_pct
        evrev_score = inverse_clamp(erg, best=0.15, worst=0.80)

    elif category == CompanyCategory.CYBERSECURITY:
        # 安全公司增长较慢(14-25%)但订阅收入质量高，用原始 EV/Revenue
        # best=5x（成熟估值），worst=25x（泡沫区间）
        evrev_score = inverse_clamp(ev_sales, best=5.0, worst=25.0)

    elif category == CompanyCategory.MEGA_TECH:
        # 超大平台：ERG不适用（增长率偏低但护城河宽）；用直接 EV/Sales
        # best=3x（便宜），worst=14x（贵）
        evrev_score = inverse_clamp(ev_sales, best=3.0, worst=14.0)

    else:  # SEMI_EQUIP：周期性强，增长率波动大，用原始 EV/Revenue
        evrev_score = inverse_clamp(ev_sales, best=6.0, worst=20.0)

    # ── Forward PE（机构报告核心锚点，独立于PEG的绝对估值验证）
    # best=20x（成长股低估），worst=80x（泡沫区间）
    fpe       = safe_val(data.get("forward_pe"), default=50.0)
    fpe_score = inverse_clamp(fpe, best=20.0, worst=80.0)

    # ── FCF Yield（现金回报率；best=5% = 高质量成长股的合理上限）
    fcf_yield     = safe_val(data.get("fcf_yield"), default=0.01)
    fcfyield_score = linear_clamp(fcf_yield, worst=0.0, best=0.05)

    # ── 按类型加权合并（权重之和 = 1.00）
    # Forward PE 占 10-15%，从 PEG 和 EV/EBITDA 中扣出
    if category == CompanyCategory.AI_CHIP:
        # 芯片：EV/EBITDA最重要；ERG其次；Forward PE作为绝对锚
        score = (peg_score    * 0.25 + evebitda_score * 0.35
               + evrev_score  * 0.15 + fcfyield_score * 0.10
               + fpe_score    * 0.15)

    elif category == CompanyCategory.AI_SOFTWARE:
        # SaaS：ERG最重要(50%)；EV/EBITDA权重低（SaaS失真）
        score = (peg_score    * 0.15 + evebitda_score * 0.10
               + evrev_score  * 0.50 + fcfyield_score * 0.15
               + fpe_score    * 0.10)

    elif category == CompanyCategory.CYBERSECURITY:
        # 安全：EV/Revenue最重要；Forward PE验证绝对估值
        score = (peg_score    * 0.20 + evebitda_score * 0.15
               + evrev_score  * 0.40 + fcfyield_score * 0.15
               + fpe_score    * 0.10)

    elif category == CompanyCategory.MEGA_TECH:
        # 超大平台：FCF yield最重要（现金牛）；EV/EBITDA次之；不用ERG
        score = (peg_score    * 0.15 + evebitda_score * 0.30
               + evrev_score  * 0.20 + fcfyield_score * 0.25
               + fpe_score    * 0.10)

    else:  # SEMI_EQUIP：EV/EBITDA主导；PE作辅助
        score = (peg_score    * 0.25 + evebitda_score * 0.40
               + evrev_score  * 0.15 + fcfyield_score * 0.10
               + fpe_score    * 0.10)

    return round(float(np.clip(score, 0, 100)), 1)


def calc_growth_score(data: dict, category: CompanyCategory) -> float:
    """
    成长分 (0–100)
    机构锚点：MS/SaaS Capital/BVP 2025–2026
    """
    # ── 收入增长 YoY
    rev_g = safe_val(data.get("revenue_growth_yoy"), 0.10)
    if category == CompanyCategory.AI_CHIP:
        rev_score = linear_clamp(rev_g, worst=0.10, best=0.70)
    elif category == CompanyCategory.AI_SOFTWARE:
        rev_score = linear_clamp(rev_g, worst=0.10, best=0.50)
    elif category == CompanyCategory.CYBERSECURITY:
        # 网络安全用 ARR 增长优先
        arr_g = safe_val(data.get("arr_growth_yoy"), rev_g)
        rev_score = linear_clamp(arr_g, worst=0.10, best=0.35)
    elif category == CompanyCategory.MEGA_TECH:
        # 超大市值：25%增速已是顶级，3%视为停滞
        rev_score = linear_clamp(rev_g, worst=0.03, best=0.25)
    else:  # SEMI_EQUIP
        rev_score = linear_clamp(rev_g, worst=-0.10, best=0.30)

    # ── EPS 增长
    eps_g = safe_val(data.get("eps_growth_yoy"), 0.10)
    eps_score = linear_clamp(eps_g, worst=0.0, best=0.60)

    # ── FCF 增长
    fcf_g = safe_val(data.get("fcf_growth_yoy"), 0.10)
    fcf_g_score = linear_clamp(fcf_g, worst=0.0, best=0.50)

    # ── 前瞻性：分析师预期增长（NTM）
    fwd_rev_g = safe_val(data.get("next_year_revenue_growth_est"), rev_g)
    fwd_score = linear_clamp(fwd_rev_g, worst=0.05, best=0.50)

    # ── 分析师上调动量（30日）
    revision_30d = safe_val(data.get("analyst_revision_30d"), 0.0)
    # revision: -1(大幅下调) ~ +1(大幅上调) → 0~100
    revision_score = linear_clamp(revision_30d, worst=-0.50, best=0.50)

    # 合并
    score = (rev_score    * 0.35
           + eps_score    * 0.20
           + fcf_g_score  * 0.15
           + fwd_score    * 0.20
           + revision_score * 0.10)

    return round(float(np.clip(score, 0, 100)), 1)


def calc_quality_score(data: dict, category: CompanyCategory) -> float:
    """
    质量分 (0–100)
    Rule of 40 口径统一：revenue_growth_yoy + fcf_margin
    机构锚点：Meritech / SaaS Capital / BVP 2026
    """
    gross_margin   = safe_val(data.get("gross_margin"),    0.60)
    fcf_margin     = safe_val(data.get("fcf_margin"),      0.10)
    op_margin      = safe_val(data.get("operating_margin"),0.10)
    roic           = safe_val(data.get("roic"),            0.15)
    debt_eq        = safe_val(data.get("debt_to_equity"),  0.50)
    rev_pred       = safe_val(data.get("revenue_predictability_score"), 0.5)
    nrr            = safe_val(data.get("net_revenue_retention"), 1.10)  # SaaS专用

    # Rule of 40（统一 FCF 口径）
    rev_g          = safe_val(data.get("revenue_growth_yoy"), 0.20)
    rule_of_40     = (rev_g + fcf_margin) * 100  # 转换为百分点

    # ── 毛利率（按类型调阈值）
    if category == CompanyCategory.AI_SOFTWARE:
        gm_score   = linear_clamp(gross_margin, worst=0.60, best=0.85)
    elif category == CompanyCategory.CYBERSECURITY:
        gm_score   = linear_clamp(gross_margin, worst=0.65, best=0.85)
    elif category == CompanyCategory.AI_CHIP:
        gm_score   = linear_clamp(gross_margin, worst=0.40, best=0.70)
    elif category == CompanyCategory.MEGA_TECH:
        # 跨度大（AMZN 48% ~ META 82%）；best=75%，worst=35%
        gm_score   = linear_clamp(gross_margin, worst=0.35, best=0.75)
    else:  # SEMI_EQUIP
        gm_score   = linear_clamp(gross_margin, worst=0.35, best=0.58)

    # ── FCF margin
    fcf_score      = linear_clamp(fcf_margin, worst=0.0,  best=0.40)

    # ── Non-GAAP 营业利润率（统一口径排除SBC）
    # worst=-0.10（亏损），best=0.35（成熟盈利）；中性点~0.12=50分
    op_score       = linear_clamp(op_margin,  worst=-0.10, best=0.35)

    # ── Rule of 40
    if category in (CompanyCategory.AI_SOFTWARE, CompanyCategory.CYBERSECURITY):
        r40_score  = linear_clamp(rule_of_40,  worst=20,  best=70)
    elif category == CompanyCategory.MEGA_TECH:
        r40_score  = linear_clamp(rule_of_40,  worst=15,  best=55)
    else:
        r40_score  = linear_clamp(rule_of_40,  worst=10,  best=50)

    # ── ROIC
    roic_score     = linear_clamp(roic,        worst=0.05, best=0.45)

    # ── 负债风险（越低越好；含可转债）
    debt_score     = inverse_clamp(debt_eq,    best=0.0,  worst=2.0)

    # ── 收入可预测性（订阅/ARR占比）
    pred_score     = linear_clamp(rev_pred,    worst=0.0,  best=1.0)

    # ── NRR（SaaS/安全核心，其他类型给默认中性分）
    if category in (CompanyCategory.AI_SOFTWARE, CompanyCategory.CYBERSECURITY):
        nrr_score  = linear_clamp(nrr,         worst=0.90, best=1.35)
    elif category == CompanyCategory.MEGA_TECH:
        nrr_score  = linear_clamp(nrr,         worst=1.00, best=1.20)
    else:
        nrr_score  = 50.0  # 非订阅类给中性分

    # 合并权重（op_score 占 5%，从 gm 权重扣出以保持总和 = 1.00）
    if category == CompanyCategory.AI_SOFTWARE:
        score = (gm_score  * 0.15 + fcf_score  * 0.20 + op_score  * 0.05
               + r40_score * 0.25 + roic_score * 0.10 + debt_score * 0.05
               + pred_score * 0.05 + nrr_score  * 0.15)
    elif category == CompanyCategory.CYBERSECURITY:
        score = (gm_score  * 0.15 + fcf_score  * 0.25 + op_score  * 0.05
               + r40_score * 0.20 + roic_score * 0.10 + debt_score * 0.05
               + pred_score * 0.05 + nrr_score  * 0.15)
    elif category == CompanyCategory.AI_CHIP:
        score = (gm_score  * 0.20 + fcf_score  * 0.30 + op_score  * 0.05
               + r40_score * 0.20 + roic_score * 0.15 + debt_score * 0.10
               + pred_score * 0.00 + nrr_score  * 0.00)
    elif category == CompanyCategory.MEGA_TECH:
        # FCF和ROIC主导；NRR作轻量补充；无SaaS式的R40权重
        score = (gm_score  * 0.20 + fcf_score  * 0.28 + op_score  * 0.05
               + r40_score * 0.15 + roic_score * 0.18 + debt_score * 0.07
               + pred_score * 0.02 + nrr_score  * 0.05)
    else:  # SEMI_EQUIP
        score = (gm_score  * 0.25 + fcf_score  * 0.25 + op_score  * 0.05
               + r40_score * 0.15 + roic_score * 0.20 + debt_score * 0.10
               + pred_score * 0.00 + nrr_score  * 0.00)

    return round(float(np.clip(score, 0, 100)), 1)


def calc_ai_exposure_score(data: dict, category: CompanyCategory) -> float:
    """
    AI暴露分 (0–100)
    大部分来自手动整理，MVP阶段用 mock 静态值
    机构锚点：MS 2026半导体报告 / BVP Nasdaq Cloud Index
    """
    ai_rev_pct     = safe_val(data.get("ai_revenue_exposure_pct"),  0.30)
    ai_growth_pct  = safe_val(data.get("ai_growth_contribution_pct"),0.30)
    ai_profit_pct  = safe_val(data.get("ai_profit_exposure_pct"),   0.30)

    # 通用 AI收入占比分
    ai_rev_score   = linear_clamp(ai_rev_pct,    worst=0.10, best=0.85)
    ai_growth_score= linear_clamp(ai_growth_pct, worst=0.10, best=0.80)
    ai_profit_score= linear_clamp(ai_profit_pct, worst=0.10, best=0.85)

    # 类型专属指标
    if category == CompanyCategory.AI_CHIP:
        dc_pct     = safe_val(data.get("datacenter_exposure_pct"), 0.50)
        pkg_pct    = safe_val(data.get("advanced_packaging_exposure_pct"), 0.20)
        dc_score   = linear_clamp(dc_pct,  worst=0.25, best=0.85)
        pkg_score  = linear_clamp(pkg_pct, worst=0.05, best=0.60)
        score = (ai_rev_score * 0.30 + ai_growth_score * 0.20
               + dc_score    * 0.35 + pkg_score        * 0.15)

    elif category == CompanyCategory.AI_SOFTWARE:
        platform_pct = safe_val(data.get("software_ai_platform_exposure_pct"), 0.25)
        backlog_pct  = safe_val(data.get("ai_order_backlog_exposure"),          0.20)
        plat_score   = linear_clamp(platform_pct, worst=0.10, best=0.70)
        backlog_score= linear_clamp(backlog_pct,  worst=0.05, best=0.60)
        score = (ai_rev_score  * 0.30 + ai_growth_score * 0.25
               + plat_score   * 0.30 + backlog_score    * 0.15)

    elif category == CompanyCategory.CYBERSECURITY:
        cyber_ai_pct = safe_val(data.get("cybersecurity_ai_exposure_pct"), 0.20)
        cyber_score  = linear_clamp(cyber_ai_pct, worst=0.05, best=0.60)
        score = (ai_rev_score  * 0.25 + ai_growth_score * 0.25
               + ai_profit_score * 0.20 + cyber_score   * 0.30)

    elif category == CompanyCategory.MEGA_TECH:
        # 超大平台：AI平台化 + AI收入占比（AI只是业务的一部分）
        platform_pct = safe_val(data.get("software_ai_platform_exposure_pct"), 0.25)
        plat_score   = linear_clamp(platform_pct, worst=0.10, best=0.60)
        score = (ai_rev_score  * 0.35 + ai_growth_score * 0.30
               + plat_score   * 0.35)

    else:  # SEMI_EQUIP
        pkg_pct    = safe_val(data.get("advanced_packaging_exposure_pct"), 0.30)
        pkg_score  = linear_clamp(pkg_pct, worst=0.10, best=0.70)
        score = (ai_rev_score  * 0.25 + ai_growth_score * 0.30
               + ai_profit_score * 0.15 + pkg_score     * 0.30)

    return round(float(np.clip(score, 0, 100)), 1)


def calc_expectation_gap_score(data: dict) -> float:
    """
    预期差分 (0–100)：实际表现 vs 市场预期的差距
    Beat & Raise = 高分 / Miss & Lower = 低分
    """
    # 实际 vs 一致预期（beat rate，单位：%，5% = 超预期5%）
    rev_beat     = safe_val(data.get("actual_revenue_vs_consensus"),  0.0)
    eps_beat     = safe_val(data.get("actual_eps_vs_consensus"),      0.0)
    guide_beat   = safe_val(data.get("guidance_vs_consensus"),        0.0)

    # 分析师上调得分（已在成长分里，此处用90日动量版本）
    rev_score    = linear_clamp(rev_beat,   worst=-0.08, best=0.10)
    eps_score    = linear_clamp(eps_beat,   worst=-0.10, best=0.15)
    guide_score  = linear_clamp(guide_beat, worst=-0.10, best=0.12)

    # 财报后市场反应（次日涨跌幅）
    reaction     = safe_val(data.get("earnings_reaction_score"), 0.0)
    react_score  = linear_clamp(reaction,   worst=-0.15, best=0.20)

    # 市场预期 z-score（预期过高是负面）
    mkt_expect   = safe_val(data.get("market_expectation_score"), 0.5)
    # market_expectation_score: 0=预期极低(好), 1=预期极高(坏)
    mkt_score    = linear_clamp(1 - mkt_expect, worst=0.0, best=1.0)

    score = (rev_score   * 0.30
           + eps_score   * 0.25
           + guide_score * 0.25
           + react_score * 0.10
           + mkt_score   * 0.10)

    return round(float(np.clip(score, 0, 100)), 1)


def calc_risk_penalty(data: dict, category: CompanyCategory) -> float:
    """
    风险扣分 (0–20)：从最终分中扣除
    ─────────────────────────────────────
    risk_penalty = (beta_component * 0.30
                  + volatility_component * 0.25
                  + valuation_risk * 0.25
                  + concentration_risk * 0.20) * 20

    每个 component 范围 0–1（0=无风险，1=最高风险）
    最终 penalty 上限 = 20 分
    """
    # ── Beta风险（1.0=中性，>2.0=高风险）
    beta         = safe_val(data.get("beta"), 1.2)
    beta_comp    = linear_clamp(beta,       worst=0.5, best=2.5) / 100

    # ── 波动率风险（30日年化波动率）
    vol_30d      = safe_val(data.get("volatility_30d"), 0.40)
    vol_comp     = linear_clamp(vol_30d,    worst=0.20, best=0.90) / 100

    # ── 估值风险（估值分的倒数：越贵 penalty 越高）
    # 直接用 final valuation_score 的反向（需外部传入或估算）
    val_risk     = safe_val(data.get("valuation_risk"), 0.5)
    val_comp     = float(np.clip(val_risk, 0, 1))

    # ── 集中度/流动性风险
    conc_risk    = safe_val(data.get("concentration_risk"), 0.3)
    liq_risk     = safe_val(data.get("liquidity_risk"),     0.2)
    conc_comp    = float(np.clip((conc_risk + liq_risk) / 2, 0, 1))

    # ── 最大回撤（1年）
    max_dd       = safe_val(data.get("max_drawdown_1y"), 0.30)
    dd_comp      = linear_clamp(max_dd,     worst=0.10, best=0.70) / 100

    # 合并
    raw_penalty = (beta_comp   * 0.25
                 + vol_comp    * 0.20
                 + val_comp    * 0.25
                 + conc_comp   * 0.15
                 + dd_comp     * 0.15)

    penalty = raw_penalty * 20  # 映射到 0–20
    return round(float(np.clip(penalty, 0, 20)), 2)


# ─────────────────────────────────────────────
# 主评分函数
# ─────────────────────────────────────────────
# Genuinely uncertain fields — hand-estimated, no public data source
# (AI-exposure fields for non-EDGAR tickers, subjective risk scores)
_LOW_CONFIDENCE_FIELDS = [
    "ai_revenue_exposure_pct", "ai_profit_exposure_pct", "ai_growth_contribution_pct",
    "advanced_packaging_exposure_pct", "ai_order_backlog_exposure",
    "cybersecurity_ai_exposure_pct", "software_ai_platform_exposure_pct",
    "market_expectation_score", "valuation_risk", "concentration_risk",
    "liquidity_risk", "revenue_predictability_score",
]

# Fields still estimated when real-time APIs are unavailable
# (Polygon provides these now; only count when still mock)
_MED_CONFIDENCE_FIELDS = [
    "ev_ebitda", "fcf_yield", "next_year_revenue_growth_est",
    "actual_revenue_vs_consensus", "guidance_vs_consensus",
]

# Fields covered by Polygon — no longer count toward uncertainty
_REAL_DATA_FIELDS = {
    "peg_ratio", "forward_pe", "ev_sales", "ps_ratio",
    "actual_eps_vs_consensus", "analyst_revision_30d",
    "gross_margin", "operating_margin", "net_income_margin", "fcf_margin",
    "revenue_growth_yoy", "debt_to_equity", "market_cap", "beta",
    "earnings_reaction_score",
}


def calc_score_uncertainty(data: dict) -> dict:
    """
    Data quality grade based on which real-time sources contributed.

    A — Polygon + EDGAR AI-exposure (all financial fields real + AI fields extracted)
    B — Polygon only (financial fields real; AI exposure still estimated)
    D — mock data only (no real-time API available)

    Injected flags set by scheduler / app.py before scoring:
      _has_polygon      : bool — Polygon returned >=5 fields
      _edgar_confidence : dict — EDGAR upgraded some AI-exposure fields
    """
    has_polygon = bool(data.get("_has_polygon"))
    has_edgar   = bool(data.get("_edgar_confidence"))

    if has_polygon and has_edgar:
        grade = "A"
    elif has_polygon:
        grade = "B"
    else:
        grade = "D"

    edgar_conf = data.get("_edgar_confidence", {})
    l_count = sum(
        1 for f in _LOW_CONFIDENCE_FIELDS
        if data.get(f) is not None
        and f not in _REAL_DATA_FIELDS
        and edgar_conf.get(f, "L") == "L"
    )
    return {
        "confidence_grade":  grade,
        "estimated_fields":  l_count,
        "l_field_count":     l_count,
    }


def score_stock(ticker: str, data: dict,
                peer_vals: Optional[dict] = None) -> dict:
    """
    计算单只股票的完整评分
    返回所有子分 + final_score + rating + 评分不确定性区间
    """
    category = get_category(ticker)
    weights  = WEIGHT_CONFIG[category]

    v_score  = calc_valuation_score(data, category, peer_vals)
    g_score  = calc_growth_score(data, category)
    q_score  = calc_quality_score(data, category)
    ai_score = calc_ai_exposure_score(data, category)
    eg_score = calc_expectation_gap_score(data)
    risk_pen = calc_risk_penalty(data, category)

    # 加权合计
    raw_score = (v_score  * weights.valuation
               + g_score  * weights.growth
               + q_score  * weights.quality
               + ai_score * weights.ai_exposure
               + eg_score * weights.expectation_gap)

    final_score = float(np.clip(raw_score - risk_pen, 0, 100))

    # Rating 规则（阈值基于84只股票实际分布校准：范围21-78，中位数47）
    if   final_score >= 65: rating = "⭐ Strong Buy"
    elif final_score >= 55: rating = "✅ Buy"
    elif final_score >= 45: rating = "👀 Watch"
    elif final_score >= 35: rating = "⚠️ Expensive"
    else:                   rating = "🚫 Avoid"

    # 不确定性
    unc = calc_score_uncertainty(data)

    return {
        "ticker":            ticker,
        "category":          category.value,
        "valuation_score":   v_score,
        "growth_score":      g_score,
        "quality_score":     q_score,
        "ai_exposure_score": ai_score,
        "expectation_gap_score": eg_score,
        "risk_penalty":      risk_pen,
        "raw_score":         round(raw_score, 1),
        "final_score":       round(final_score, 1),
        "rating":            rating,
        "confidence_grade":  unc["confidence_grade"],
        "estimated_fields":  unc["estimated_fields"],
        # 权重快照
        "_weights": {
            "valuation": weights.valuation,
            "growth":    weights.growth,
            "quality":   weights.quality,
            "ai":        weights.ai_exposure,
            "eg":        weights.expectation_gap,
        }
    }


def score_portfolio(stocks: dict[str, dict]) -> pd.DataFrame:
    """
    批量评分，返回排序后的 DataFrame
    stocks = {"NVDA": {...data...}, "PANW": {...data...}, ...}
    """
    results = [score_stock(ticker, data) for ticker, data in stocks.items()]
    df = pd.DataFrame(results)
    df = df.sort_values("final_score", ascending=False).reset_index(drop=True)
    df.index += 1  # 从1开始排名
    return df
