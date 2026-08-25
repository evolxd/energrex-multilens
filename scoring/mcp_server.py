"""
ENERGREX MCP Server
====================
把这套评分系统包成对话里能直接调用的工具，不用每次手写脚本去跑
quant_audit.py / refresh_scores.py。

复用已有的数据合并/评分/拆分逻辑（quant_audit.merge_data + clean_data、
quant_engine.score_ticker、score_split.split_scores），这个文件本身
不重新实现任何评分公式——它只是一层薄的"对话可调用"包装。

启动（stdio，Claude Code/Desktop 用这个）：
    python -m scoring.mcp_server
或：
    cd scoring && python mcp_server.py

配置见仓库根目录 .mcp.json。
"""

from __future__ import annotations

import contextlib
import io
import sys
import pathlib

_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from quant_audit import merge_data, clean_data, print_audit_report  # noqa: E402
from quant_engine import score_ticker  # noqa: E402
from score_split import split_scores  # noqa: E402
from scoring_engine import TICKER_CATEGORY  # noqa: E402


server = MCPServer(
    name="energrex-valuation",
    version="0.1.0",
    instructions=(
        "ENERGREX AI成长股估值评分系统的对话工具。用 get_stock_score 拿结构化"
        "评分数据，用 get_valuation_report 拿可读的逐字段审计报告，用 "
        "list_universe 看当前覆盖的股票代码列表。所有数据默认走实时 yfinance"
        "抓取——注意，final_score 是被熔断乘数压过的分，company_score 才是"
        "不受熔断影响的真实五维质量分，两者含义不同，别混着报。"
    ),
)


def _score_result(ticker: str, use_live: bool = True):
    ticker = ticker.upper().strip()
    raw = merge_data(ticker, use_live=use_live)
    data = clean_data(raw)
    if not data:
        return None, None
    result = score_ticker(ticker, data)
    return result, data


@server.tool(
    description=(
        "查询单只股票的完整评分：估值/成长/质量/AI暴露/预期差/动量六维得分、"
        "风险扣分、是否触发熔断、公司真实质量分(不受熔断影响，company_score)、"
        "最终综合分与评级。默认走实时 yfinance 数据。"
    )
)
def get_stock_score(ticker: str) -> dict:
    result, data = _score_result(ticker)
    if result is None:
        return {"error": f"未找到 {ticker.upper()} 的数据，检查代码是否正确或是否在覆盖范围内"}

    split = split_scores(
        result.dim_scores,
        risk_penalty=result.risk_penalty,
        beta=data.get("beta"),
        drawdown_abs=data.get("max_drawdown_1y"),
        de_ratio=data.get("debt_to_equity"),
        blended=result.final_score,
    )

    return {
        "ticker": result.ticker,
        "公司名": result.company_name,
        "板块": result.sector,
        "六维得分": {
            "估值": result.dim_scores.get("valuation"),
            "成长": result.dim_scores.get("growth"),
            "质量": result.dim_scores.get("quality"),
            "AI暴露": result.dim_scores.get("ai_exposure"),
            "预期差": result.dim_scores.get("expectation_gap"),
            "动量": result.dim_scores.get("momentum"),
        },
        "风险扣分": result.risk_penalty,
        "综合得分_final_score": result.final_score,
        "公司质量分_不受熔断影响_company_score": (
            round(split.company, 2) if split.company is not None else None
        ),
        "熔断": {
            "触发": result.circuit_triggered,
            "分项": split.circuit.clauses,
            "详情": result.circuit_reason,
        },
        "评级": result.rating,
        "AI角色": result.ai_profile_label,
        "剔除字段_bad_fields": result.bad_fields,
        "数据来源": "实时 yfinance + mock_data 补全（生产环境有网络时）",
    }


@server.tool(
    description=(
        "生成某只股票的完整逐字段估值审计报告（文本），包含每个评分子项的"
        "原始值、计算公式、得分，以及最终评级——跟命令行 `python quant_audit.py "
        "TICKER` 的输出一致。"
    )
)
def get_valuation_report(ticker: str) -> str:
    result, data = _score_result(ticker)
    if result is None:
        return f"未找到 {ticker.upper()} 的数据，检查代码是否正确或是否在覆盖范围内"

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_audit_report(result, data)
    return buf.getvalue()


@server.tool(
    description="列出当前系统覆盖的全部股票代码及所属类型（AI芯片/AI软件/网络安全/半导体设备/科技巨头）。"
)
def list_universe() -> dict:
    by_category: dict[str, list[str]] = {}
    for ticker, category in TICKER_CATEGORY.items():
        by_category.setdefault(category.value, []).append(ticker)
    for tickers in by_category.values():
        tickers.sort()
    return {
        "总数": len(TICKER_CATEGORY),
        "分类": by_category,
    }


if __name__ == "__main__":
    server.run(transport="stdio")
