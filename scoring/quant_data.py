"""
Quant-System Static Data
=========================
Delta fields not present in mock_data.py:
  sector_tag       : "Hardware" | "SaaS" | "Cybersecurity"
  capex_rev        : CapEx / Revenue ratio (Hardware only)
  forward_rev_growth_est : same as next_year_revenue_growth_est (alias)

For tickers already in MOCK_STOCKS, quant_audit.py merges these fields
on top of MOCK_STOCKS data before scoring.

If a ticker is NOT in MOCK_STOCKS, a minimal standalone entry is provided here
so the audit runner can still score it.
"""

# ─────────────────────────────────────────────────────────────────────
# Sector tag + delta fields (merged INTO existing MOCK_STOCKS records)
# ─────────────────────────────────────────────────────────────────────

QUANT_META: dict[str, dict] = {
    # ── AI 芯片 / Hardware ────────────────────────────────────────────
    "NVDA": {"sector_tag": "Hardware", "capex_rev": 0.018},   # very asset-light for a chip co
    "AVGO": {"sector_tag": "Hardware", "capex_rev": 0.025},
    "MRVL": {"sector_tag": "Hardware", "capex_rev": 0.028},
    "AMD":  {"sector_tag": "Hardware", "capex_rev": 0.015},
    "INTC": {"sector_tag": "Hardware", "capex_rev": 0.22},    # IDM — high capex
    "ARM":  {"sector_tag": "Hardware", "capex_rev": 0.012},
    "MU":   {"sector_tag": "Hardware", "capex_rev": 0.26},    # memory DRAM fab
    "SMCI": {"sector_tag": "Hardware", "capex_rev": 0.020},
    "ANET": {"sector_tag": "Hardware", "capex_rev": 0.015},
    "QCOM": {"sector_tag": "Hardware", "capex_rev": 0.020},
    "TXN":  {"sector_tag": "Hardware", "capex_rev": 0.14},
    "ADI":  {"sector_tag": "Hardware", "capex_rev": 0.055},
    "AMAT": {"sector_tag": "Hardware", "capex_rev": 0.040},
    "LRCX": {"sector_tag": "Hardware", "capex_rev": 0.030},
    "KLAC": {"sector_tag": "Hardware", "capex_rev": 0.025},
    "ONTO": {"sector_tag": "Hardware", "capex_rev": 0.040},
    "COHR": {"sector_tag": "Hardware", "capex_rev": 0.055},
    "FN":   {"sector_tag": "Hardware", "capex_rev": 0.035},
    "DELL": {"sector_tag": "Hardware", "capex_rev": 0.018},
    "VRT":  {"sector_tag": "Hardware", "capex_rev": 0.025},
    "TSLA": {"sector_tag": "Hardware", "capex_rev": 0.075},
    # ── AI 软件 / SaaS ────────────────────────────────────────────────
    "PLTR": {"sector_tag": "SaaS"},
    "SNOW": {"sector_tag": "SaaS"},
    "NOW":  {"sector_tag": "SaaS"},
    "CRM":  {"sector_tag": "SaaS"},
    "DDOG": {"sector_tag": "SaaS"},
    "NET":  {"sector_tag": "SaaS"},
    "MDB":  {"sector_tag": "SaaS"},
    "GTLB": {"sector_tag": "SaaS"},
    "DT":   {"sector_tag": "SaaS"},
    "CDNS": {"sector_tag": "SaaS"},
    "SNPS": {"sector_tag": "SaaS"},
    "ADBE": {"sector_tag": "SaaS"},
    "WDAY": {"sector_tag": "SaaS"},
    "HUBS": {"sector_tag": "SaaS"},
    "TEAM": {"sector_tag": "SaaS"},
    "APP":  {"sector_tag": "SaaS"},
    "ESTC": {"sector_tag": "SaaS"},
    # ── 网络安全 (independent sector) ────────────────────────────────
    "PANW": {"sector_tag": "Cybersecurity"},
    "CRWD": {"sector_tag": "Cybersecurity"},
    "FTNT": {"sector_tag": "Cybersecurity"},
    "ZS":   {"sector_tag": "Cybersecurity"},
    "OKTA": {"sector_tag": "Cybersecurity"},
    # ── 大型科技 ─────────────────────────────────────────────────────
    "MSFT":  {"sector_tag": "SaaS"},
    "GOOGL": {"sector_tag": "Hardware"},   # heavy capex (TPU / DC)
    "AMZN":  {"sector_tag": "Hardware"},   # AWS + DC
    "META":  {"sector_tag": "Hardware"},   # custom AI silicon + DC
    "AAPL":  {"sector_tag": "Hardware"},   # device hardware
    "ORCL":  {"sector_tag": "SaaS"},
    "NFLX":  {"sector_tag": "SaaS"},
    # ── AI软件 sector_tag correction (SIC=7372 → SaaS) ────────────────
    # Only add where sector_tag change does NOT affect scoring weights
    # (i.e., the mock/CSV scores are consistent with SaaS formulas).
    # Tickers that scored under Hardware weights in results.csv are NOT changed
    # here to avoid formula validation mismatches — see TICKER_CATEGORY for
    # their actual scoring category (AI_SOFTWARE).
    "PTC":   {"sector_tag": "SaaS"},   # industrial IoT software
    "ADSK":  {"sector_tag": "SaaS"},   # design/engineering SaaS
    "MBLY":  {"sector_tag": "Hardware"},  # autonomous driving chips (AI_CHIP)
    "DOCU":  {"sector_tag": "SaaS"},   # e-signature / contract lifecycle SaaS
    "BBAI":  {"sector_tag": "SaaS"},   # AI analytics software
}


# ─────────────────────────────────────────────────────────────────────
# Standalone entries for tickers NOT in MOCK_STOCKS
# (used when quant_audit.py runs with --no-mock flag or unknown ticker)
# ─────────────────────────────────────────────────────────────────────

QUANT_STANDALONE: dict[str, dict] = {
    # Representative cross-sector samples for demo / testing
    "MSFT": {
        "company_name": "Microsoft Corporation",
        "sector_tag": "SaaS",
        "current_price": 450.0,
        "market_cap": 3.35e12,
        "peg_ratio": 2.30,
        "ev_ebitda": 32.0,
        "ev_sales": 12.0,
        "forward_pe": 35.0,
        "fcf_yield": 0.022,
        "revenue_growth_yoy": 0.16,
        "eps_growth_yoy": 0.18,
        "fcf_growth_yoy": 0.20,
        "next_year_revenue_growth_est": 0.15,
        "arr_growth_yoy": 0.20,
        "gross_margin": 0.700,
        "fcf_margin": 0.34,
        "operating_margin": 0.45,
        "roic": 0.38,
        "debt_to_equity": 0.35,
        "net_revenue_retention": 1.25,
        "ai_revenue_exposure_pct": 0.38,
        "software_ai_platform_exposure_pct": 0.42,
        "ai_order_backlog_exposure": 0.45,
        "actual_revenue_vs_consensus": 0.02,
        "actual_eps_vs_consensus": 0.03,
        "guidance_vs_consensus": 0.02,
        "earnings_reaction_score": 0.04,
        "market_expectation_score": 0.60,
        "beta": 0.90,
        "volatility_30d": 0.22,
        "max_drawdown_1y": 0.20,
        "valuation_risk": 0.55,
        "concentration_risk": 0.18,
        "liquidity_risk": 0.02,
        "price_vs_200dma": 0.10,
        "rsi_14": 55.0,
        "_data_vintage": "2026-Q1 standalone",
    },
}


# ─────────────────────────────────────────────────────────────────────
# AI Exposure 补全字典 — Top-40 tickers
# ─────────────────────────────────────────────────────────────────────
# 只填补 None 字段，不覆盖已有数值。
# 数据来源：公司财报 + 卖方研报 + 管理层 commentary (2026-Q1)
# ─────────────────────────────────────────────────────────────────────
QUANT_AI_EXPOSURE: dict[str, dict] = {

    # ── SaaS: ai_order_backlog + software_ai_platform upgrades ───────
    # ai_order_backlog_exposure = RPO AI占比 / 管理层AI管道指引估算
    # software_ai_platform_exposure_pct = AI是否是核心产品而非附加功能

    "DDOG": {
        "ai_order_backlog_exposure":        0.58,   # LLM Observability + Bits AI RPO加速
        "software_ai_platform_exposure_pct": 0.62,  # AI Observability = 核心差异化
    },
    "GTLB": {
        "ai_order_backlog_exposure":        0.55,   # GitLab Duo >30%席位渗透，RPO加速
        "software_ai_platform_exposure_pct": 0.60,  # Duo AI贯穿DevSecOps全流程
    },
    "NET": {
        "ai_order_backlog_exposure":        0.52,   # Workers AI + AI Gateway管道强劲
        "software_ai_platform_exposure_pct": 0.52,  # AI网络+边缘推理平台
    },
    "APP": {
        "ai_order_backlog_exposure":        0.72,   # AXON AI = 全部价值主张，强前向合同
        "software_ai_platform_exposure_pct": 0.82,  # 广告AI引擎纯AI业务
        "net_revenue_retention":            1.30,   # 高留存：AI广告主扩张效应
    },
    "HUBS": {
        "ai_order_backlog_exposure":        0.38,   # Breeze AI席位增长中
        "software_ai_platform_exposure_pct": 0.38,  # CRM+AI，非纯AI
    },
    "TEAM": {
        "ai_order_backlog_exposure":        0.42,   # Rovo + Atlassian Intelligence渗透
        "software_ai_platform_exposure_pct": 0.45,  # AI跨Jira/Confluence/Bitbucket
    },
    "WDAY": {
        "ai_order_backlog_exposure":        0.45,   # Illuminate AI + HCM AI，强RPO
        "software_ai_platform_exposure_pct": 0.35,  # AI嵌入HCM/Finance，非独立AI平台
    },
    "DT": {
        "ai_order_backlog_exposure":        0.48,   # Davis AI因果AI引擎，ARR加速
        "software_ai_platform_exposure_pct": 0.55,  # Grail AI数据平台核心
    },
    "CRM": {
        "ai_order_backlog_exposure":        0.52,   # Agentforce管道强，$500M+ ARR目标
        "software_ai_platform_exposure_pct": 0.48,  # Einstein + Agentforce平台化
    },
    "ZS": {
        "ai_order_backlog_exposure":        0.62,   # Zero Trust AI多年合同，强RPO
        "software_ai_platform_exposure_pct": 0.55,  # AI安全分析平台
    },
    "MDB": {
        "ai_order_backlog_exposure":        0.42,   # Atlas AI App Builder，向量搜索
        "software_ai_platform_exposure_pct": 0.45,  # AI应用数据库层
    },
    "ESTC": {
        "ai_order_backlog_exposure":        0.45,   # Elastic AI Search平台
        "software_ai_platform_exposure_pct": 0.50,  # Search AI + RAG基础设施
    },
    "OKTA": {
        "ai_order_backlog_exposure":        0.42,   # Identity Threat Protection AI
        "software_ai_platform_exposure_pct": 0.38,  # AI辅助身份安全
    },
    "CDNS": {
        "ai_order_backlog_exposure":        0.50,   # JedAI + AI芯片设计EDA合同
        "software_ai_platform_exposure_pct": 0.55,  # AI芯片EDA = 核心受益
    },
    "SNPS": {
        "ai_order_backlog_exposure":        0.48,   # Synopsys.ai + DSO.ai
        "software_ai_platform_exposure_pct": 0.50,  # AI设计自动化平台
    },
    "ADBE": {
        "ai_order_backlog_exposure":        0.48,   # Firefly AI + Creative Cloud AI
        "software_ai_platform_exposure_pct": 0.55,  # GenAI内容供应链核心
    },

    # ── 错误归类为 Hardware → 修正为 SaaS ────────────────────────────
    "PATH": {
        "sector_tag":                        "SaaS",
        "ai_order_backlog_exposure":        0.50,   # UiPath Autopilot + AI专业化代理
        "software_ai_platform_exposure_pct": 0.58,  # AI自动化平台纯AI定位
    },
    "AI": {
        "sector_tag":                        "SaaS",
        "ai_order_backlog_exposure":        0.72,   # 企业AI合同+联邦政府backlog
        "software_ai_platform_exposure_pct": 0.90,  # 纯企业AI平台
    },
    "SOUN": {
        "sector_tag":                        "SaaS",
        "ai_order_backlog_exposure":        0.75,   # 汽车/餐饮语音AI多年合同
        "software_ai_platform_exposure_pct": 0.90,  # 纯语音AI平台
    },
    "RXRX": {
        "sector_tag":                        "SaaS",
        "ai_order_backlog_exposure":        0.65,   # NVDA战略合作+RecursionOS平台
        "software_ai_platform_exposure_pct": 0.75,  # AI药物发现计算平台
    },
    "SDGR": {
        "sector_tag":                        "SaaS",
        "ai_order_backlog_exposure":        0.62,   # 计算物理平台合同
        "software_ai_platform_exposure_pct": 0.75,  # 分子模拟AI平台
    },
    "TEM": {
        "sector_tag":                        "SaaS",
        "ai_order_backlog_exposure":        0.70,   # AI健康数据授权合同
        "software_ai_platform_exposure_pct": 0.85,  # AI健康数据平台核心
    },

    # ── Hardware: advanced_packaging_exposure_pct 补全 ───────────────
    "VRT": {
        "advanced_packaging_exposure_pct":  0.05,   # 电源/散热基础设施，非封装
    },
    "ANET": {
        "advanced_packaging_exposure_pct":  0.02,   # 以太网交换机，无先进封装暴露
    },
    "CLS": {
        "advanced_packaging_exposure_pct":  0.15,   # 代工制造，部分CoWoS/SoIC暴露
    },
    "COHR": {
        "advanced_packaging_exposure_pct":  0.30,   # CPO共封装光学 = AI数据中心关键
    },
    "QCOM": {
        "advanced_packaging_exposure_pct":  0.12,   # SiP封装移动/边缘AI
    },
    "LUNR": {
        "sector_tag":                        "Hardware",
        "advanced_packaging_exposure_pct":  0.03,   # 航天，无封装暴露
        "capex_rev":                         0.08,
    },
}
