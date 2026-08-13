"""R12 分析师模拟账户系统。

每位分析师（skill_id 动态开户）独立账户，初始资金 100,000 元；
严格按 A 股规则撮合：T+1、涨跌停（主板 ±10% / 创业板·科创板 ±20%）、
100 股整数倍、费用（佣金 万2.5 最低 5 元 / 印花税 卖出 0.05% / 过户费 0.001% 双向）。
成交口径（用户确认）：按分析师提供的目标价成交——当日价格区间覆盖目标价
（最低 ≤ 目标价 ≤ 最高）即按目标价成交；目标价 ≥ 涨停价拒买、≤ 跌停价拒卖。
"""

from .engine import (
    INITIAL_CASH,
    TradeResult,
    limit_prices,
    open_account,
    place_order,
    snapshot,
)
from .storage import load_account, save_account

__all__ = [
    "INITIAL_CASH",
    "TradeResult",
    "limit_prices",
    "open_account",
    "place_order",
    "snapshot",
    "load_account",
    "save_account",
]
