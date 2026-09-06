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
    "ASML": {"sector_tag": "Hardware", "capex_rev": 0.030},
    "ONTO": {"sector_tag": "Hardware", "capex_rev": 0.040},
    "COHR": {"sector_tag": "Hardware", "capex_rev": 0.055},
    "FN":   {"sector_tag": "Hardware", "capex_rev": 0.035},
    "DELL": {"sector_tag": "Hardware", "capex_rev": 0.018},
    "VRT":  {"sector_tag": "Hardware", "capex_rev": 0.025},
    "GEV":  {"sector_tag": "Hardware", "capex_rev": 0.025},
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
    "DUOL": {"sector_tag": "SaaS"},  # 语言学习SaaS，不在常驻观察池，仅一次性案例分析用
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
    "ACIW": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "ACMR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "ADIG": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "AEHR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "AIP": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "AKAM": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "ALAB": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "ALGM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "ALMU": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "ALOT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "AMBQ": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "AMKR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "ASYS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "AXTI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "BKFG": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "BLSH": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "BOX": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "BRAI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "CAMT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "CBRS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "CEVA": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "CHKP": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "COHU": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "CORZ": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "CPAY": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "CRCT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "CRDO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "CRSR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "CRWV": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "DBX": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "DIOD": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "DLO": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "DOCN": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "DOX": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "ENTG": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "FFIV": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "FORM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "FOUR": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "GDDY": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "GEN": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "GFS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "GSIT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "HPQ": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "ICHR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "INFQ": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "INTT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "IONQ": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "IOT": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "IPGP": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "KLIC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "LASR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "LSCC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "MBGL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "MPWR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "MRAM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "MTSI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "MXL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "NTAP": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "NTSK": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "NVEC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "NVMI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "NVTS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "ON": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "OSS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "PAY": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "PI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "PLAB": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "POET": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "POWI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "PSQL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "PXLW": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "Q": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "QBTS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "QLYS": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "QMCO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "QRVO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "QUBT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "QUIK": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "RBCN": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "RBRK": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "RELY": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "RGTI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "RMBS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SAIL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "SCIA": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SITM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SKHY": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SKYT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SLAB": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SMTC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SNDK": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SSYS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "STX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SWKS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "SYNA": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "TACT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "TENB": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "TER": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "TOST": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "TRT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "TWLO": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "UCTT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "UMAC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "VECO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "VELO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "VRNS": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "VRSN": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "VSH": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "WDC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "WEX": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "WIX": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "WOLF": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入,capex_rev待研究
    "XYZ": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "ZETA": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入,capex_rev待研究
    "A": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AADX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ABT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ACHR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ADPT": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ADSE": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ADVB": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AEIS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AHCO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AIR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ALMR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AME": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AMPX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AMSC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AMWL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AORT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AOS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ARXS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ATEC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ATKR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ATRO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AVAV": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AVNS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AVR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AXGN": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AXON": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "AYI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BA": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BBNX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BDSX": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BE": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BETA": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BFLY": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BIAF": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BIO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BIOQ": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BLLN": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BRKR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BSX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BTSG": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BVS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BW": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "BWXT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CARL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CBLL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CDNA": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CDRE": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CERT": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CMI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CMPD": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CNMD": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CRL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CSTL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CSW": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CTEV": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CW": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "CXT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DCI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DCO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DCTH": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DGX": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DHR": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DOCS": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DOV": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DPC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DRIO": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DRS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "DXCM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "EAF": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "EMR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ENOV": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ENR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ENS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "EPAC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ESP": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ESTA": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ETN": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "EW": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "FAC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "FCEL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "FELE": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "FLGT": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "FLS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "FLY": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "FPS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "FRNM": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "FTAI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GD": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GEHC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GGG": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GH": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GHM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GKOS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GMED": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GNRC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GRAL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GRC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GTES": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "GTLS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HAE": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HAWK": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HAYW": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HII": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HLIO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HNGE": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HONA": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HQY": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HSTM": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HTFL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HUBB": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HWM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "HXL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "IART": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ICLR": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "IDXGD": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "IDXX": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "IEX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ILMN": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "INIO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "INMD": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "INSP": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "IQV": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "IR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "IRMD": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "IRTC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ITGR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ITT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ITW": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "JBTM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "KAI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "KE": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "KIDS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "KRMN": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "KTOS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "LH": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "LHX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "LIVN": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "LMRI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "LMT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "LOAR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "LTBR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "LYNX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MDT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MEDP": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MFP": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MFPVV": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MIDD": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MIR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MRCY": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MTD": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MWA": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NDRA": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NDSN": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NEO": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NEOG": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NNE": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NOC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NPK": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NPO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NRC": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NTRA": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NVCR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "NVT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "OESX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "OMCL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "OMDA": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "OPRX": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "OTIS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PEN": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PH": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PHR": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PINC": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PLPC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PNR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PODD": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "POWL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PRCT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PRPO": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PRVA": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PSNL": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "QDEL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "QGEN": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "RCAT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "RDNT": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "RDW": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "RFIL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "RKLB": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "RRX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "RTX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "RVTY": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SARO": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SHC": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SIBN": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SLP": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SMR": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SOPH": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SPCX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SPOK": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "STE": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "STI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SXI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SYK": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SYM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TALK": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TBRG": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TDG": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TDOC": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TMDX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TMO": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TNC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TNDM": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TWST": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TXG": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TXT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "UFPT": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ULBI": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "VCYT": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "VOYG": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "VREX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "VSEC": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "VVX": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "WAT": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "WAY": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "WEAV": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "WGS": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "WTS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "WWD": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "XE": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "XGN": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "XPON": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "XYL": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "YSS": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "ZBH": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "SCCO": {"sector_tag": "Hardware"},  # 2026-08-28 铜矿，补FCX盲区时一并加入,capex_rev待研究
    "FCX": {"sector_tag": "Hardware"},  # 2026-08-28 真实持仓里一直有，之前完全没覆盖,capex_rev待研究
    "IE": {"sector_tag": "Hardware"},  # 2026-08-28 铜矿,capex_rev待研究
    "CUAI": {"sector_tag": "Hardware"},  # 2026-08-28 铜矿,capex_rev待研究
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
    # 已知分类错配，未解决（2026-08-19 核实过）：RXRX/SDGR/TEM 是 AI+生物科技
    # 交叉标的，SECTOR_BASELINES（quant_engine.py）只有 Hardware/SaaS/
    # Cybersecurity 三个选项，没有 Biotech，只能"就近取用"选了 SaaS——SaaS
    # 基准的 fcf_margin worst锚点是-10%，但临床期生物科技烧钱到-80%是行业
    # 常态，不代表比SaaS同业差，这会系统性压低这三只票的quality维度分。
    # 尝试过给 SECTOR_BASELINES 新增 Biotech 类目、用 Damodaran NYU Stern
    # 的行业分布数据定 best/worst 锚点（这套系统别处已经在用 Damodaran
    # 框架，方法论上是对的路），但这次会话里 pages.stern.nyu.edu 被网络出口
    # 代理挡住，搜索引擎摘要也只给单家公司数字、给不出真正的行业分布分位数，
    # 没能拿到能交代来源的锚点数字。宁可维持现状（错配但诚实），也不要编数字
    # 包装成"已解决"。而且即便日后拿到数据，RXRX/SDGR/TEM 本身也不是同质
    # 的一组——RXRX 是临床期AI药物发现平台，SDGR 收入大头其实是软件授权
    # （更接近SaaS），TEM 有$14亿 TTM营收的商业化诊断业务——单一 Biotech
    # 分类未必对这三只票都合适，需要更细的拆分，不是简单加一类就能解决。
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

    # ── 2026-08-19 全库 sector_tag 交叉审计确认的错配（不是主观判断，是拿
    # quant_engine.py 实际解析出的 sector_tag 去跟 scoring_engine.py 自己的
    # TICKER_CATEGORY 分类核对，两边打架的地方——这11个原本都因为没有显式
    # 设置 sector_tag，默认落进了 Hardware，但 scoring_engine.py 早就把它们
    # 归类为 AI软件/SaaS 或网络安全）。只补 sector_tag 这一个字段——
    # ai_order_backlog_exposure / software_ai_platform_exposure_pct 这类
    # AI暴露细分字段没有一并编造，缺了就让它按"missing"处理，不假装核实过。
    # LUNR 特意不放进这批：交叉审计标它跟 scoring_engine.py 的 AI_SOFTWARE
    # 分类冲突，但下面第 299 行左右已经有一条更早、更具体的判断——"航天，
    # 无封装暴露"，capex_rev=0.08——明确把它当 Hardware 处理，理由写得清楚，
    # 不是疏漏。这两边谁对，是个真实的判断分歧（Intuitive Machines 造实体
    # 登月器，物理意义上确实是硬件；但 scoring_engine 那边可能是按它的数据/
    # 软件服务收入占比来归类），不是"漏设置默认值"这种可以无脑跟着改的情况，
    # 留给人决定，这里不覆盖。
    "ACN": {"sector_tag": "SaaS"},   # Accenture，IT咨询/专业服务，非硬件资本结构
    "AFRM": {"sector_tag": "SaaS"},  # Affirm，金融科技/BNPL贷款，非硬件
    "EXLS": {"sector_tag": "SaaS"}, # ExlService，BPO/数据分析服务，非硬件
    "NTNX": {"sector_tag": "SaaS"}, # Nutanix，超融合基础设施软件，非物理硬件
    "S":    {"sector_tag": "Cybersecurity"},  # SentinelOne，网络安全，不是Hardware/SaaS
    "TTD":  {"sector_tag": "SaaS"},  # The Trade Desk，程序化广告SaaS平台
    "TYL":  {"sector_tag": "SaaS"}, # Tyler Technologies，政府软件SaaS
    "U":    {"sector_tag": "SaaS"},  # Unity Software，游戏引擎软件平台
    "VEEV": {"sector_tag": "SaaS"}, # Veeva Systems，生命科学云软件CRM
    "ZM":   {"sector_tag": "SaaS"},  # Zoom，视频会议SaaS，典型SaaS却被默认成了硬件

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
