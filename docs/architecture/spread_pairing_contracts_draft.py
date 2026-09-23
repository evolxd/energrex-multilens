"""草案 —— account/spread_pairing.py 的顶层契约（未接入真实代码，供阶段一 Gate 审查）

按 arch.md 第三节「显式契约 (Protocol-First)」起草：跨模块调用必须用
typing.Protocol / TypedDict / 标准 Type Hints 显式声明输入字段、输出类型、
异常类型，杜绝 account_monitor.py 现在这种 `_get_am()["fn_name"]` 字典键反射。

对照 docs/architecture/account_monitor.md 第 4 节「目标状态」：
    外围层 (account_monitor.py 等) --正常 import--> 中间层 (服务/适配)
                                    --正常 import + Protocol--> 内核层 (本文件)
    底层 (account/options_repository.py) --依赖注入(DataFrame 参数)--> 内核层

设计上的关键决定（阶段一提交审查，阶段三据此实施，不要跳过审查直接写代码）：
1. `build_spread_portfolios` 的公开签名改为接收已加载好的 `pd.DataFrame` +
   `today: date`，不接收 `acct_id`、不自己调用 `_load_options_positions()`。
   现在 `_build_spread_portfolios(acct_id)` 混着"读数据库"和"配对计算"两件事，
   违反 arch 第二节"无副作用纯内存"红线。数据库读取应留在中间层（阶段三时
   可以是 account_monitor.py 里一个 3 行的薄包装函数，也可以是新的
   account/spread_pairing_service.py，视阶段三实际拆分情况决定，本次不预判）。
2. 三种组合（垂直价差 / 日历对角价差 / 裸仓）字段并不完全相同（如
   low_strike/high_strike 只有垂直价差有，near_expiry/far_expiry 只有对角价差
   有），用三个独立 TypedDict 而不是一个到处 `total=False` 的大字典，
   这样字段访问才能被静态类型检查真正覆盖，而不是"看起来查过、其实全是
   Optional"。
3. pandas 不属于 arch 红线里禁止的"UI 框架"或"网络/数据库直接驱动"，
   `pd.DataFrame` 作为输入参数类型是被允许的——但内核层只准"消费"这个
   DataFrame，不准反过来查询/写回数据库。
"""

from __future__ import annotations

import datetime
from typing import Literal, Protocol, TypedDict, Union

import pandas as pd

# ────────────────────────────────────────────────────────────────
# 基础类型别名
# ────────────────────────────────────────────────────────────────

OptionType = Literal["call", "put"]
RiskLevel = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]


# ────────────────────────────────────────────────────────────────
# 输入契约：从 DataFrame 解析出的单条腿
# ────────────────────────────────────────────────────────────────

class SpreadLeg(TypedDict):
    """_parse_spread_legs 的输出元素，对应现有实现里 legs.append({...}) 的字段。"""

    symbol: str
    underlying: str
    direction: OptionType
    strike: float
    expiry: str            # ISO 日期字符串 "YYYY-MM-DD"
    qty: int                # 带符号：正=多头，负=空头；配对过程中会被消耗、改写
    unit_cost: float
    cur_price: float | None      # 缺失时不得是 NaN，必须是 None（阶段二黄金快照已验证的口径）
    total_pnl: float | None      # 同上
    delta: float | None
    iv: float | None
    dte: int                # 已知问题：非法到期日解析失败时退回 9999，见下方"已知行为"


class PortfolioLegRef(SpreadLeg):
    """组合结果里 legs 字段的元素：SpreadLeg 加上它在原始腿列表里的下标。"""

    li: int


# ────────────────────────────────────────────────────────────────
# 输出契约：三种组合类型（字段有真实差异，不用一个大 TypedDict 糊弄过去）
# ────────────────────────────────────────────────────────────────

class SpreadPortfolioBase(TypedDict):
    id: str
    type: str                       # 如 "Bull Call Spread" / "Calendar Spread" / "Naked Long Call"
    underlying: str
    direction: OptionType
    legs: list[PortfolioLegRef]
    spread_qty: int
    expiry: str
    dte: int
    net_per_share: float
    net_total: float
    max_profit: float | None
    max_loss: float | None
    breakeven: float | None
    current_pnl: float
    pnl_pct: float | None
    risk_level: RiskLevel
    recommendation: str             # 中文建议文案；文案本身是业务标准，重构中不得改字


class VerticalPortfolio(SpreadPortfolioBase):
    """同到期日、同类型 → 垂直价差。"""

    low_strike: float
    high_strike: float
    strike_width: float
    is_debit: bool


class DiagonalPortfolio(SpreadPortfolioBase):
    """跨到期日、同类型 → 日历价差 / 对角价差（含反向对角）。"""

    near_expiry: str
    far_expiry: str
    near_strike: float
    far_strike: float
    is_proper: bool          # False = 买腿早于卖腿到期（反向对角，风险性质不同）


class NakedPortfolio(SpreadPortfolioBase):
    """未配对的裸仓，字段和 SpreadPortfolioBase 完全一致，单独命名是为了调用方
    能用 isinstance-like 的 type 字段判断（"Naked Long/Short Call/Put"）分支处理。"""


SpreadPortfolio = Union[VerticalPortfolio, DiagonalPortfolio, NakedPortfolio]


# ────────────────────────────────────────────────────────────────
# 异常契约
# ────────────────────────────────────────────────────────────────

class SpreadPairingError(Exception):
    """本模块所有主动抛出异常的基类，调用方可以只捕获这一个类型。"""


class InvalidPositionsFrameError(SpreadPairingError, ValueError):
    """df 缺少必需列（symbol/quantity/direction/unit_cost/current_price/total_pnl/
    delta/iv 之一）时抛出。

    现有实现（重构前）对这种情况没有校验，是静默假设列存在、缺失时 pandas
    直接 KeyError——这不算"契约"，只是没写会崩。阶段三实施时是否要新增这道
    校验、以及新增后是否需要给调用方一个"降级为空列表"的兼容期，需要用户
    在阶段三启动前明确决定：这是一次行为变更，不能在"零回归"的重构框架下
    悄悄夹带，必须单独走一次特征测试更新流程。本契约先把类型和触发条件定义
    出来，是否启用留待阶段三讨论。
    """


# ────────────────────────────────────────────────────────────────
# 主入口：调用方（account_monitor.py 等）应当依赖这个 Protocol，
# 而不是直接 import 具体实现——测试可以用符合此 Protocol 的假实现替换。
# ────────────────────────────────────────────────────────────────

class SpreadPairingEngine(Protocol):
    def __call__(
        self,
        df: pd.DataFrame,
        today: datetime.date,
    ) -> list[SpreadPortfolio]:
        """把某账户的期权持仓（options_positions 表的查询结果）识别成组合。

        Args:
            df: 至少包含 symbol/quantity/direction/unit_cost/current_price/
                total_pnl/delta/iv 列的 DataFrame。不做数据库查询——数据获取
                是调用方（中间层）的职责，本函数只消费已经拿到的数据。
            today: 计算 DTE 的基准日期，由调用方显式传入（不在函数内部调用
                datetime.date.today()），这样测试才能用 freezegun 之外的方式
                —— 直接传参数——冻结日期，两种方式都要支持。

        Returns:
            按风险等级从高到低排序（CRITICAL → HIGH → MEDIUM → LOW，同风险等级
            按 underlying 字母序）的组合列表。空持仓返回空列表，不返回 None。

        Raises:
            InvalidPositionsFrameError: 见上方类定义（阶段三前需确认是否启用）。
        """
        ...


def build_spread_portfolios(
    df: pd.DataFrame,
    today: datetime.date,
) -> list[SpreadPortfolio]:
    """SpreadPairingEngine 的默认实现（阶段三时把现有 13 个私有辅助函数的逻辑
    原样迁移到这里；本草案只声明签名，不包含实现，避免在契约审查阶段引入
    还没跑过黄金快照的代码）。"""
    raise NotImplementedError("阶段一契约草案：签名已定，实现留待阶段三")


__all__ = [
    "OptionType",
    "RiskLevel",
    "SpreadLeg",
    "PortfolioLegRef",
    "SpreadPortfolioBase",
    "VerticalPortfolio",
    "DiagonalPortfolio",
    "NakedPortfolio",
    "SpreadPortfolio",
    "SpreadPairingError",
    "InvalidPositionsFrameError",
    "SpreadPairingEngine",
    "build_spread_portfolios",
]
