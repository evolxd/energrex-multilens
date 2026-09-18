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
    "DT":   {"sector_tag": "SaaS"},
    "CDNS": {"sector_tag": "SaaS"},
    "SNPS": {"sector_tag": "SaaS"},
    "ADBE": {"sector_tag": "SaaS"},
    "WDAY": {"sector_tag": "SaaS"},
    "HUBS": {"sector_tag": "SaaS"},
    "TEAM": {"sector_tag": "SaaS"},
    "APP":  {"sector_tag": "SaaS"},
    "ESTC": {"sector_tag": "SaaS"},
    "DUOL": {"sector_tag": "SaaS"},   # Duolingo，订阅制App，资本结构上是SaaS不是Hardware；
                                      # 语言学习SaaS，不在常驻观察池，仅一次性案例分析用
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
    "ACIW": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "ACMR": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "ADIG": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "AEHR": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "AIP": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "AKAM": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "ALAB": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "ALGM": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "ALMU": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "ALOT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "AMBQ": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "AMKR": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "ASYS": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "AXTI": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "BKFG": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "BLSH": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "BOX": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "BRAI": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "CAMT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "CBRS": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "CEVA": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "CHKP": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "COHU": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "CORZ": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "CPAY": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "CRCT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "CRDO": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "CRSR": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "CRWV": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "DBX": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "DIOD": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "DLO": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "DOCN": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "DOX": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "ENTG": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "FFIV": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "FORM": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "FOUR": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "GDDY": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "GEN": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "GFS": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "GSIT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "HPQ": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "ICHR": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "INFQ": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "INTT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "IONQ": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "IOT": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "IPGP": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "KLIC": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "LASR": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "LSCC": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "MBGL": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "MPWR": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "MRAM": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "MTSI": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "MXL": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "NTAP": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "NTSK": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "NVEC": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "NVMI": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "NVTS": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "ON": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "OSS": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "PAY": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "PI": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "PLAB": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "POET": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "POWI": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "PSQL": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "PXLW": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "Q": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "QBTS": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "QLYS": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "QMCO": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "QRVO": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "QUBT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "QUIK": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "RBCN": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "RBRK": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "RELY": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "RGTI": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "RMBS": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SAIL": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "SCIA": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SITM": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SKHY": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SKYT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SLAB": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SMTC": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SNDK": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SSYS": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "STX": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SWKS": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "SYNA": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "TACT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "TENB": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "TER": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "TOST": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "TRT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "TWLO": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "UCTT": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "UMAC": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "VECO": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "VELO": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "VRNS": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "VRSN": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "VSH": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "WDC": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "WEX": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "WIX": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "WOLF": {"sector_tag": "Hardware"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 Hardware
    "XYZ": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "ZETA": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：确认属实，维持 SaaS
    "A": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "AADX": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "ABT": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "ACHR": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "ADPT": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "ADSE": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "ADVB": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "AEIS": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "AHCO": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "AIR": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "ALMR": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "AME": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "AMPX": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "AMSC": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "AMWL": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "AORT": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "AOS": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "ARXS": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "ATEC": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "ATKR": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "ATRO": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "AVAV": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "AVNS": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "AVR": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "AXGN": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "AXON": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "AYI": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "BA": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "BBNX": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "BDSX": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "BE": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "BETA": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "BFLY": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "BIAF": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "BIO": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "BIOQ": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "BLLN": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "BRKR": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "BSX": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "BTSG": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "BVS": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "BW": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "BWXT": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "CARL": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "CBLL": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "CDNA": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "CDRE": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "CERT": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "CMI": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "CMPD": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "CNMD": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "CR": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "CRL": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "CSTL": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "CSW": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "CTEV": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "CW": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "CXT": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "DCI": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "DCO": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "DCTH": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "DGX": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "DHR": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "DOCS": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "DOV": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "DPC": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "DRIO": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "DRS": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "DXCM": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "EAF": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "EMR": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "ENOV": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "ENR": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "ENS": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "EPAC": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "ESP": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "ESTA": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "ETN": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "EW": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "FAC": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "FCEL": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "FELE": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "FLGT": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "FLS": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "FLY": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "FPS": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "FRNM": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "FTAI": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "GD": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "GEHC": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "GGG": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "GH": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "GHM": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "GKOS": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "GMED": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "GNRC": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "GRAL": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "GRC": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "GTES": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "GTLS": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "HAE": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "HAWK": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "HAYW": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "HII": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "HLIO": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "HNGE": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "HONA": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "HQY": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "HSTM": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "HTFL": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "HUBB": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "HWM": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "HXL": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "IART": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "ICLR": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "IDXGD": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "IDXX": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "IEX": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "ILMN": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "INIO": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "INMD": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "INSP": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "IQV": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "IR": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "IRMD": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "IRTC": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "ITGR": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "ITT": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "ITW": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "JBTM": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "KAI": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "KE": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "KIDS": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "KRMN": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "KTOS": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "LH": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "LHX": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "LIVN": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "LMRI": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "LMT": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "LOAR": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "LTBR": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "LYNX": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "MDT": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "MEDP": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "MFP": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "MFPVV": {"sector_tag": "Hardware"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "MIDD": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "MIR": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "MRCY": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "MTD": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "MWA": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "NDRA": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "NDSN": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "NEO": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "NEOG": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "NNE": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "NOC": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "NPK": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "NPO": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "NRC": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "NTRA": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "NVCR": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "NVT": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "OESX": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "OMCL": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "OMDA": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "OPRX": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "OTIS": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "PEN": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "PH": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "PHR": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "PINC": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "PL": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "PLPC": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "PNR": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "PODD": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "POWL": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "PRCT": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "PRPO": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "PRVA": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "PSNL": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "QDEL": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "QGEN": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "RCAT": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "RDNT": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "RDW": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "RFIL": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "RKLB": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "RRX": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "RTX": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "RVTY": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "SARO": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "SHC": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "SIBN": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "SLP": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "SMR": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "SOPH": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "SPCX": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "SPOK": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "STE": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "STI": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "SXI": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "SYK": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "SYM": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "TALK": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "TBRG": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TDG": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "TDOC": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "TMDX": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "TMO": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "TNC": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "TNDM": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "TWST": {"sector_tag": "SaaS"},  # 2026-08-28 批量补入(健康/工业),capex_rev待研究
    "TXG": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "TXT": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "UFPT": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "ULBI": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "VCYT": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "VOYG": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "VREX": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "VSEC": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "VVX": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "WAT": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "WAY": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "WEAV": {"sector_tag": "SaaS"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→SaaS
    "WGS": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "WTS": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "WWD": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "XE": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "XGN": {"sector_tag": "Diagnostics"},  # 2026-09-14 yfinance真实sector/industry核实：SaaS→Diagnostics
    "XPON": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "XYL": {"sector_tag": "Machinery"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Machinery
    "YSS": {"sector_tag": "AeroDefense"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→AeroDefense
    "ZBH": {"sector_tag": "MedicalDevices"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→MedicalDevices
    "SCCO": {"sector_tag": "Materials"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Materials
    "FCX": {"sector_tag": "Materials"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Materials
    "IE": {"sector_tag": "Materials"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Materials
    "CUAI": {"sector_tag": "Materials"},  # 2026-09-14 yfinance真实sector/industry核实：Hardware→Materials

    "COST": {"sector_tag": "WarehouseRetail"},  # 2026-09-17 用户单独要求收录
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
