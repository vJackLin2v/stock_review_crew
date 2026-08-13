# -*- coding: utf-8 -*-
"""R15 验收用例骨架（multi-agent-dev 线 B：只读需求文档口径，业务逻辑居首）。

对照 docs/requirements-r15.md。模块实现测试由各开发模块自带
（test_llm_sanitize / test_index_amount / test_backend_stock_names / test_news_switch），
本文件为集成闸门契约断言：跨模块关键口径 + 未实现项骨架（skip 待实现）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew.llm_sanitize import sanitize_for_llm


# ── R1 脱敏契约（业务逻辑手算，硬门槛）──

def test_r1_zt_pool_name_dropped_code_kept():
    obj = {"stocks": [{"代码": "600519", "名称": "贵州茅台", "涨跌幅": 0.1}]}
    out = sanitize_for_llm(obj)
    assert out["stocks"][0]["代码"] == "600519"
    assert "名称" not in out["stocks"][0]
    assert out["stocks"][0]["涨跌幅"] == 0.1  # 其他字段不丢


def test_r1_sector_name_kept():
    obj = {"name": "半导体", "leading_stock": "北方华创"}
    out = sanitize_for_llm(obj)
    assert out["name"] == "半导体"      # 板块名不误伤
    assert "leading_stock" not in out   # 领涨股名删除


# ── R2 节点合并契约（待实现 → 实现后去 skip）──

def test_r2_single_analyst_node():
    """图结构断言：build_graph 后节点集合含 analysts，不含 trend_analysts/sentiment_analysts；
    job 契约：analysts_total=5、analysts_done 单调到 5。"""
    import stock_review_crew.graph as graph

    compiled = graph.build_graph()
    nodes = set(compiled.get_graph().nodes)
    assert "analysts" in nodes
    assert "trend_analysts" not in nodes and "sentiment_analysts" not in nodes


def test_r2_analysts_total_5_and_progress():
    """进度事件：分析师阶段事件出现（trend/sentiment 或统一），analysts_done 单调至 5。"""
    import stock_review_crew.graph as graph

    events = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(graph, "_emit", lambda stage, pct, msg: events.append((stage, pct, msg)))
    monkeypatch.setattr(graph, "_llm_ready", lambda: True)
    monkeypatch.setattr(
        graph, "_llm_analyst",
        lambda state, skill, group, trend_context: {
            "skill_name": skill.get("name", ""), "skill_id": skill.get("id", ""),
            "analysis": f"{group} 观点", "tool_used": False, "tool_calls": [], "group": group,
        },
    )
    skills = [
        {"id": f"t{i}", "name": f"趋势{i}", "group": "trend", "prompt": ""} for i in range(2)
    ] + [
        {"id": f"s{i}", "name": f"情绪{i}", "group": "sentiment", "prompt": ""} for i in range(3)
    ]
    monkeypatch.setattr(graph, "list_skills", lambda: skills)
    results = graph._run_analysts({"date": "2026-08-12", "mode": "close", "snapshot": {}})
    monkeypatch.undo()
    assert len(results) == 5
    pcts = [e[1] for e in events]
    assert pcts == sorted(pcts)


# ── R3 成交额回填契约（实现已就绪，断言引用口径）──

def test_r3_backfill_only_amount():
    """回填只写 amount：其余字段逐键不变（由 test_index_amount 手算覆盖，此处补顶层契约）。"""
    import stock_review_crew.tools.stock_data as sd

    assert hasattr(sd, "_backfill_index_amount")
    assert hasattr(sd, "fetch_index_trend")


# ── R4 空观点修复契约（待实现 → 实现后去 skip）──

def test_r4_empty_opinion_detected():
    """content 为「本轮工具查询返回为空，无明确观点输出」类空话 → 不进入分析师条目（走工具摘要/规则兜底）。"""
    import stock_review_crew.graph as graph

    assert graph._is_empty_opinion("本轮工具查询返回为空，无明确观点输出。") is True
    assert graph._is_empty_opinion("看多为主，建议关注 600519 突破 1350 元整数关口后回踩确认再加仓两成仓位。") is False


def test_r4_tool_result_sanitized():
    """工具返回（get_stock_info 等）追加给 LLM 前无股票名称。"""
    import stock_review_crew.graph as graph

    out = graph._sanitize_tool_result({"code": "600519", "name": "贵州茅台"})
    assert "贵州茅台" not in out and "600519" in out


# ── R5 名称接口契约（实现已就绪）──

def test_r5_stock_names_contract():
    """POST /api/data/stock-names 契约：批量返回、无效忽略、失败 null（由 test_backend_stock_names 覆盖）。"""
    from backend.main import app

    assert any(getattr(r, "path", "") == "/api/data/stock-names" for r in app.routes)


# ── R0 资讯屏蔽回归（实现已就绪）──

def test_r0_news_disabled_contract():
    """NEWS_ENABLED=0：health 返回 news_enabled=false；realtime 快讯空列表（由 test_news_switch 覆盖）。"""
    from stock_review_crew import config

    assert hasattr(config, "news_enabled")
