"""Human-friendly source directions for ENERGREX input verification.

This module deliberately separates *where to look* from the verification
decision.  A search result is not evidence by itself: the saved override must
still name the document and reporting period that supports the value.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class SourceGuide:
    source: str
    location: str
    search_query: str
    source_url: str
    action: str


_FIELD_RULES: dict[str, tuple[str, str, str, str]] = {
    "forward_pe": (
        "Yahoo Finance / 券商一致预期",
        "Statistics → Valuation Measures → Forward P/E",
        'forward PE valuation measures',
        "statistics",
    ),
    "peg_ratio": (
        "Yahoo Finance / 券商一致预期",
        "Statistics → PEG Ratio；同时记录采用 1 年还是 5 年口径",
        'PEG ratio 1 year 5 year expected growth',
        "statistics",
    ),
    "ev_ebitda": (
        "Yahoo Finance / 券商一致预期",
        "Statistics → Enterprise Value/EBITDA",
        'enterprise value EBITDA valuation measures',
        "statistics",
    ),
    "ev_sales": (
        "Yahoo Finance / 券商一致预期",
        "Statistics → Enterprise Value/Revenue",
        'enterprise value revenue valuation measures',
        "statistics",
    ),
    "revenue_growth_yoy": (
        "公司最新财报 / SEC 10-Q",
        "Income Statement 或 Earnings Release 的季度收入表",
        'investor relations quarterly results revenue year over year',
        "filing",
    ),
    "eps_growth_yoy": (
        "公司最新 Earnings Release",
        "Non-GAAP diluted EPS 表；本期与上年同期使用相同口径",
        'investor relations quarterly results non-GAAP diluted EPS',
        "earnings",
    ),
    "fcf_growth_yoy": (
        "公司最新 Earnings Release / Cash Flow Statement",
        "Free cash flow reconciliation；本期与上年同期",
        'investor relations quarterly results free cash flow reconciliation',
        "earnings",
    ),
    "next_year_revenue_growth_est": (
        "券商一致预期 / Yahoo Analysis",
        "Analysis → Growth Estimates 或 Revenue Estimate；记录 NTM/FY 口径",
        'analysis revenue estimate next year growth',
        "market",
    ),
    "analyst_revision_30d": (
        "券商一致预期平台",
        "Estimate revisions / upward revisions，查看最近 30 天",
        'analyst estimate revisions last 30 days',
        "market",
    ),
    "gross_margin": (
        "公司最新财报 / SEC 10-Q",
        "Income Statement；优先 GAAP gross margin",
        'investor relations quarterly results GAAP gross margin',
        "filing",
    ),
    "operating_margin": (
        "公司最新 Earnings Release",
        "Non-GAAP results 或 reconciliation table 的 operating margin",
        'investor relations quarterly results non-GAAP operating margin',
        "earnings",
    ),
    "fcf_margin": (
        "公司最新财报",
        "Free cash flow ÷ revenue；两者必须采用相同期间",
        'investor relations quarterly results free cash flow revenue',
        "earnings",
    ),
    "roic": (
        "SEC 10-K / 10-Q 与公司财报",
        "用 NOPAT、债务、现金和股东权益计算；通常不是直接披露值",
        '10-K operating income debt cash shareholders equity',
        "filing",
    ),
    "debt_to_equity": (
        "SEC 10-Q / 10-K",
        "Balance Sheet 及 Debt footnote；检查 convertible notes",
        '10-Q convertible notes total debt shareholders equity',
        "filing",
    ),
    "net_revenue_retention": (
        "公司股东信 / Earnings Release / 电话会",
        "Key metrics；搜索 NRR、DBNRR 或 net dollar retention",
        'investor relations net revenue retention NRR DBNRR',
        "earnings",
    ),
    "arr_growth_yoy": (
        "公司股东信 / Earnings Release",
        "Key metrics；搜索 ARR 或 annual recurring revenue",
        'investor relations annual recurring revenue ARR year over year',
        "earnings",
    ),
    "actual_revenue_vs_consensus": (
        "公司实际财报 + 券商一致预期",
        "实际 revenue 与发布前 consensus 使用同一季度和单位",
        'quarterly revenue consensus estimate actual',
        "market",
    ),
    "actual_eps_vs_consensus": (
        "公司实际财报 + 券商一致预期",
        "实际 EPS 与发布前 consensus 必须同为 GAAP 或 Non-GAAP",
        'quarterly EPS consensus estimate actual non-GAAP',
        "market",
    ),
    "guidance_vs_consensus": (
        "公司 Earnings Release + 券商一致预期",
        "Outlook/Guidance 中值，对比发布前 consensus",
        'investor relations outlook guidance revenue consensus',
        "earnings",
    ),
    "ai_revenue_exposure_pct": (
        "公司业绩简报 / Earnings Call",
        "搜索 AI revenue、AI ARR、Data Center revenue；没有明确拆分就标记未披露",
        'investor relations AI revenue AI ARR data center revenue',
        "earnings",
    ),
    "ai_profit_exposure_pct": (
        "公司业绩简报 / 分部利润披露",
        "搜索 AI operating income/profit；未明确披露时不要自行推算为已验证",
        'investor relations AI operating income profit contribution',
        "earnings",
    ),
    "ai_growth_contribution_pct": (
        "公司业绩简报 / Earnings Call",
        "搜索 AI contribution to growth；管理层定性描述不能当作精确比例",
        'investor relations AI contribution to growth',
        "earnings",
    ),
}


def _source_url(ticker: str, link_type: str, query: str) -> str:
    if link_type == "statistics":
        return f"https://finance.yahoo.com/quote/{ticker}/key-statistics"
    if link_type == "market":
        return f"https://finance.yahoo.com/quote/{ticker}/analysis"
    if link_type == "filing":
        return f"https://www.sec.gov/edgar/browse/?CIK={ticker}&owner=exclude"
    # Search wording strongly biases toward the issuer's investor-relations
    # material while avoiding a guessed IR domain that could be wrong.
    return "https://www.google.com/search?q=" + quote_plus(
        f'{ticker} {query}'
    )


def guide_for_field(field: str, ticker: str, label: str = "") -> SourceGuide:
    """Return concise instructions for finding a field's authoritative value."""
    source, location, terms, link_type = _FIELD_RULES.get(
        field,
        (
            "公司 Investor Relations / 最新财报",
            "先查 Earnings Release、股东信和 10-Q；没有明确披露就标记未披露",
            f'investor relations {label or field}',
            "earnings",
        ),
    )
    query = f"{ticker} {terms}"
    action = (
        "找到数字后，来源说明请写：文件名 + 财报季度 + 表格/章节。"
        "若公司没有给出精确数字，保留为未披露，不要用新闻推测值替代。"
    )
    return SourceGuide(
        source=source,
        location=location,
        search_query=query,
        source_url=_source_url(ticker, link_type, terms),
        action=action,
    )


def should_show_field(status: str, view: str) -> bool:
    """Keep the default queue focused without removing the full editor."""
    if view == "全部字段":
        return True
    if view == "待核验 + 可选":
        return status in {"pending", "legacy_verified", "optional"}
    # 历史核对但缺少来源的项目必须留在默认工作队列中：
    # 用户应看到日期并只补证据，而不是把同一数字重新核一遍。
    return status in {"pending", "legacy_verified"}
