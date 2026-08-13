"""交割单分析子包（R11-R1，自 synalysis_crew 移植，口径冻结）。

公共契约（唯一真源：H:\\synalysis_crew\\docs\\requirements.md 第 4 章）：
- ``parse_trades(path) -> list[TradeRecord]``：券商无关解析（同花顺/涨乐 xlsx 为主，
  csv/txt 尽力而为），解析失败抛中文 ``ParseError``；
- ``compute_metrics(trades) -> MetricsResult``：v2 指标引擎（FIFO 闭环 / 逐日 TWR /
  胜率盈亏比持仓周期 / 翻倍腰斩递进事件 / is_partial 区间口径 / 流水模式）；
- ``analyze(trades, metrics, max_rounds=2) -> AnalysisResult``：AI 分析报告
  （LLM 复用 stock_review_crew.config，不可用 → 规则引擎降级，免责声明恒在）。
"""

from .analyst import DISCLAIMER, analyze, ensure_disclaimer
from .metrics import MetricsResult, compute_metrics
from .parser import (
    OP_LABELS,
    OpType,
    ParseError,
    TradeRecord,
    parse_trades,
)

__all__ = [
    "DISCLAIMER",
    "MetricsResult",
    "OP_LABELS",
    "OpType",
    "ParseError",
    "TradeRecord",
    "analyze",
    "compute_metrics",
    "ensure_disclaimer",
    "parse_trades",
]
