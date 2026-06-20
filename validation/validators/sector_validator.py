"""
Sector validator
================
Cross-checks sector assignment using:
  1. FMP sector/industry strings
  2. Finnhub industry string
  3. SEC SIC code + description
  4. Keyword analysis on company description text

Our taxonomy:  Hardware | SaaS | Cybersecurity
External sources use different labels → we map them.
"""

from __future__ import annotations
import re
import logging

logger = logging.getLogger(__name__)

# ── Keyword lists (lowercase) ──────────────────────────────────────────

KEYWORDS: dict[str, list[str]] = {
    "Cybersecurity": [
        "cybersecurity", "cyber security", "endpoint security", "cloud security",
        "zero trust", "sase", "xdr", "siem", "identity security", "threat intelligence",
        "firewall", "ddos", "waf", "ransomware", "vulnerability management",
        "intrusion detection", "network security", "security operations",
        "privileged access", "identity and access", "data protection",
        "compliance security", "fraud detection", "threat detection",
    ],
    "Hardware": [
        "semiconductor", "gpu", "chip", "silicon", "data center hardware",
        "networking chip", "asic", "memory", "wafer", "foundry", "fab",
        "photolithography", "packaging", "eda", "electronic design",
        "printed circuit", "server hardware", "storage hardware",
        "networking equipment", "fiber optic", "laser", "power management ic",
        "microcontroller", "embedded processor", "fpga",
    ],
    "SaaS": [
        "cloud software", "subscription software", "software as a service",
        "saas", "application software", "workflow automation",
        "crm", "erp", "database", "data warehouse", "data platform",
        "analytics platform", "collaboration software", "devops",
        "cloud platform", "api platform", "software platform",
        "human capital management", "financial management software",
    ],
    "AI": [   # used to flag AI exposure, not a standalone sector
        "artificial intelligence", "generative ai", "machine learning",
        "large language model", "llm", "deep learning", "neural network",
        "inference", "training", "foundation model", "ai model",
        "automation", "intelligent automation", "computer vision",
        "natural language processing", "nlp", "predictive analytics",
    ],
}

# ── SIC code → our sector mapping ─────────────────────────────────────

SIC_MAP: dict[str, str] = {
    "3674": "Hardware",   # Semiconductors
    "3672": "Hardware",   # Printed Circuit Boards
    "3577": "Hardware",   # Computer Peripheral Equipment
    "3571": "Hardware",   # Electronic Computers
    "3575": "Hardware",   # Computer Terminals
    "3669": "Hardware",   # Communications Equipment
    "3825": "Hardware",   # Instruments for Measuring
    "7372": "SaaS",       # Prepackaged Software
    "7371": "SaaS",       # Computer Programming Services
    "7374": "SaaS",       # Computer Processing and Data Preparation
    "7379": "SaaS",       # Services-Computer Rental and Leasing
    "7371": "SaaS",       # Computer Programming, Data Processing
}

# ── FMP / Finnhub label → our sector ─────────────────────────────────

LABEL_MAP: dict[str, str] = {
    # FMP sectors
    "semiconductors": "Hardware",
    "semiconductor": "Hardware",
    "electronic components": "Hardware",
    "computer hardware": "Hardware",
    "technology hardware": "Hardware",
    "networking & communication devices": "Hardware",
    "software—application": "SaaS",
    "software-application": "SaaS",
    "software—infrastructure": "SaaS",
    "software-infrastructure": "SaaS",
    "internet content & information": "SaaS",
    "information technology services": "SaaS",
    "electronic technology": "Hardware",
    # Finnhub industries
    "technology": "SaaS",     # ambiguous; will be refined by keyword
    "semiconductors": "Hardware",
    "software": "SaaS",
    "security software & services": "Cybersecurity",
    "internet software/services": "SaaS",
}


def _keyword_score(text: str) -> dict[str, int]:
    """Count keyword hits for each sector in a block of text."""
    t = text.lower()
    return {sector: sum(1 for kw in kws if kw in t)
            for sector, kws in KEYWORDS.items()}


def _map_label(label: str) -> str | None:
    if not label:
        return None
    return LABEL_MAP.get(label.lower().strip())


def suggest_sector(description: str, fmp_sector: str = "",
                   fmp_industry: str = "", finnhub_industry: str = "",
                   sic: str = "") -> tuple[str, float]:
    """
    Returns (suggested_sector, confidence 0-1).
    """
    votes: dict[str, int] = {}

    # FMP / Finnhub label votes
    for label in [fmp_sector, fmp_industry, finnhub_industry]:
        s = _map_label(label)
        if s:
            votes[s] = votes.get(s, 0) + 2   # external label = 2 pts each

    # SIC code vote
    s = SIC_MAP.get(str(sic))
    if s:
        votes[s] = votes.get(s, 0) + 2

    # Keyword analysis on description
    if description:
        kw_scores = _keyword_score(description)
        # Cybersecurity overrides if strongly present
        if kw_scores["Cybersecurity"] >= 3:
            votes["Cybersecurity"] = votes.get("Cybersecurity", 0) + 4
        elif kw_scores["Cybersecurity"] >= 1:
            votes["Cybersecurity"] = votes.get("Cybersecurity", 0) + 2

        top_hw = kw_scores["Hardware"]
        top_sw = kw_scores["SaaS"]
        if top_hw > top_sw and top_hw >= 2:
            votes["Hardware"] = votes.get("Hardware", 0) + 3
        elif top_sw > top_hw and top_sw >= 2:
            votes["SaaS"] = votes.get("SaaS", 0) + 3
        elif top_hw >= 2:
            votes["Hardware"] = votes.get("Hardware", 0) + 2
        elif top_sw >= 2:
            votes["SaaS"] = votes.get("SaaS", 0) + 2

    if not votes:
        return "Unknown", 0.0

    best = max(votes, key=lambda k: votes[k])
    total = sum(votes.values())
    conf = votes[best] / total if total else 0.0
    return best, round(conf, 2)


def count_ai_keywords(description: str) -> int:
    if not description:
        return 0
    return sum(1 for kw in KEYWORDS["AI"] if kw in description.lower())


def validate(
    ticker: str,
    csv_sector: str,
    fmp_profile: dict,
    finnhub_profile: dict,
    sec_info: dict,
    description: str = "",
) -> dict:
    """
    Returns:
      sector_suggested    str
      sector_confidence   float 0-1
      source_conflict     bool
      sector_sources      str  (which sources agreed/disagreed)
      ai_keyword_count    int
      notes               list[str]
    """
    notes: list[str] = []

    fmp_sector_raw    = fmp_profile.get("sector", "")
    fmp_industry_raw  = fmp_profile.get("industry", "")
    finnhub_ind_raw   = finnhub_profile.get("sector", "")
    sic               = sec_info.get("sic", "")
    sic_desc          = sec_info.get("sic_desc", "")

    # Combine description sources
    full_desc = " ".join(filter(None, [
        description,
        fmp_profile.get("description", ""),
        sic_desc,
    ]))

    suggested, conf = suggest_sector(
        full_desc, fmp_sector_raw, fmp_industry_raw, finnhub_ind_raw, sic
    )

    # Check name match
    fmp_name     = fmp_profile.get("company_name", "").lower()
    finnhub_name = finnhub_profile.get("company_name", "").lower()
    sec_name     = sec_info.get("company_name", "").lower()

    name_sources: list[str] = []
    if fmp_name:     name_sources.append(f"FMP:{fmp_profile.get('company_name','')}")
    if finnhub_name: name_sources.append(f"Finnhub:{finnhub_profile.get('company_name','')}")
    if sec_name:     name_sources.append(f"SEC:{sec_info.get('company_name','')}")

    # Cybersecurity is our custom sub-sector of SaaS; external sources classify it as SaaS/Software/Hardware.
    # Hardware suggestion often comes from SIC codes like 3577 (Computer Peripherals) which are
    # outdated for modern cybersecurity software companies — not a real conflict.
    _cyber_saas_ok = (csv_sector == "Cybersecurity" and suggested in ("SaaS", "Software", "Hardware"))

    # Source conflict = CSV sector disagrees with suggested by ≥1 confident external source
    source_conflict = (
        bool(suggested and suggested != "Unknown")
        and suggested != csv_sector
        and conf >= 0.50
        and not _cyber_saas_ok
    )

    if source_conflict:
        notes.append(
            f"Sector conflict: CSV={csv_sector} | Suggested={suggested} (conf={conf:.0%}) "
            f"| FMP={fmp_sector_raw}/{fmp_industry_raw} | Finnhub={finnhub_ind_raw} | SIC={sic}"
        )

    ai_kws = count_ai_keywords(full_desc)

    sector_sources = "; ".join([
        f"FMP:{fmp_sector_raw}" if fmp_sector_raw else "",
        f"Finnhub:{finnhub_ind_raw}" if finnhub_ind_raw else "",
        f"SIC:{sic}({sic_desc})" if sic else "",
    ]).strip("; ")

    return {
        "sector_suggested":    suggested,
        "sector_confidence":   conf,
        "source_conflict":     source_conflict,
        "sector_sources":      sector_sources,
        "name_sources":        " | ".join(name_sources),
        "ai_keyword_count":    ai_kws,
        "notes":               notes,
    }
