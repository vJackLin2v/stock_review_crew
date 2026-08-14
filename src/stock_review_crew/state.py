"""State Schema — LangGraph 共享状态定义"""
import operator
from typing import Annotated, Optional, TypedDict

class ReviewState(TypedDict):
    # ── 输入（I4 上下文注入 + 后端入参）──
    date: str
    mode: str                              # pre_market/auction/intraday_am/noon/intraday_pm/close
    max_rounds: int                        # 辩论循环上限（≤3）
    yesterday_report: str                  # 昨日收盘复盘摘要（缺失="" → 触发「无昨日报告，跳过验证」）
    earlier_today: str                     # 当日更早时间点复盘摘要（缺失=""）
    skill_names: list[str]

    # ── Node1 产出：行情数据 ──
    market_data: str                       # 渲染后的 Markdown 数据块（供分析师/报告）
    market_weather: str                    # R12：市场天气/情绪温度简报（如「晴（79 分/偏暖）」）
    emotion_cycle: str                     # R16：情绪周期定位（fetch_data_node 注入；数据层未落地时「数据不足」）
    raw_news: str                          # 资讯原始 JSON 字符串（供资讯节点）
    snapshot: dict                         # 结构化数据快照（供 I4 落盘/前端图表/规则引擎）

    # ── Node2 产出：分析 ──
    analyses: Annotated[list[dict], operator.add]
    analyst_accounts: Optional[dict]       # R12：{skill_id: 账户快照}（analysts_node 聚合；无数据为空 dict）

    # ── Node3 产出：主持人判断 ──
    discussion_topic: str
    discussion_done: bool
    round_count: int
    host_analysis: str                     # R16：主持人分歧判断全文（LLM 原文或规则降级文本；报告「分歧点」章节输入）

    # ── Node4 产出：辩论记录（R16：辩论节点已移除，恒空列表，保留供报告契约兼容）──
    debate_history: Annotated[list[dict], operator.add]

    # ── Node5 产出：最终报告（§五 result.report 对齐）──
    final_report: str
    overall_tags: list[str]
    disclaimer: str

    # ── 降级标记（LLM 无 Key/失败 → 规则引擎）──
    degraded: bool                         # 是否发生过降级（可见标注）
    degraded_notes: Annotated[list[str], operator.add]   # 降级明细（meta.degraded[]）
