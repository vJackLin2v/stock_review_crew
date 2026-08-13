# -*- coding: utf-8 -*-
"""LLM 输入脱敏测试（2026-08-12 R1）：发给 LLM 的文本不含股票名称、其他字段不丢。

手算用例（业务逻辑优先）：
1. 涨停池渲染：含代码不含名称、涨跌幅等字段保留；
2. 板块行：板块名保留、领涨股名删除；
3. 模拟账户持仓：只给代码；
4. 聊天上下文：stock_info compact 无 name、zt_pool dict 无名称；
5. 幂等：重复 sanitize 无副作用。
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew import chat
from stock_review_crew import graph
from stock_review_crew import prompts
from stock_review_crew.llm_sanitize import sanitize_for_llm, sanitize_json_text


def test_zt_pool_render_keeps_code_drops_name():
    """手算：涨停池 stocks 渲染含 600519 不含「贵州茅台」，涨跌幅字段保留。"""
    blocks = {
        "zt_pool": {
            "limit_up": {
                "count": 1,
                "stocks": [{"代码": "600519", "名称": "贵州茅台", "涨跌幅": 0.1, "最新价": 1345.0}],
            },
            "source": "东财",
        }
    }
    md = graph._render_market_markdown(blocks, "close", [])
    assert "600519" in md
    assert "贵州茅台" not in md
    assert "1345.0" in md  # 其他字段不丢
    assert "东财" in md


def test_sector_name_kept_leading_stock_dropped():
    """手算：板块行 name=板块名保留；leading_stock=领涨股名删除。"""
    blocks = {
        "sectors": {
            "top5": [
                {"name": "半导体", "pct_change": 0.02, "amount": 1.2e10,
                 "leading_stock": "北方华创", "leading_pct_change": 0.05},
            ],
            "source": "同花顺",
        }
    }
    md = graph._render_market_markdown(blocks, "close", [])
    assert "半导体" in md
    assert "北方华创" not in md
    assert "0.05" in md  # 领涨股涨跌幅保留


def test_account_block_keeps_code_drops_name():
    """手算：模拟账户持仓只给代码，不给名称。"""
    acct = {
        "cash": 100000.0,
        "positions": {"600519": {"name": "贵州茅台", "qty": 100, "frozen_qty": 0, "avg_cost": 1300.0}},
    }
    text = prompts.build_account_block(acct, "2026-08-12")
    assert "600519" in text
    assert "贵州茅台" not in text
    assert "100 股" in text or "100股" in text


def test_chat_context_stock_info_no_name():
    """手算：聊天上下文 stock_info compact 不含 name；zt_pool dict 无名称。"""
    engine = chat.ChatEngine(
        llm=None, history_enabled=False,
        review_searcher=lambda q: {"available": False, "note": "测试关闭", "data": None},
    )
    ctx, _ = engine._build_context(
        meta={"target_type": "stock", "target": "600519", "target_raw": "600519"},
        parse={"intent": "trend", "target": "600519", "time_range": "近5日"},
        needs=[{"data_key": "stock_info", "reason": "走势"}, {"data_key": "zt_pool", "reason": "梯队"}],
        fetched={
            "stock_info": {"available": True, "data": json.dumps({
                "code": "600519", "name": "贵州茅台", "close": 1345.0, "ma5": 1300.0,
                "recent5": [{"date": "2026-08-12", "close": 1345.0}], "source": "东财"}),
                "source": None, "note": None},
            "zt_pool": {"available": True, "data": {
                "limit_up": {"stocks": [{"代码": "600519", "名称": "贵州茅台"}]}, "source": "东财"},
                "source": None, "note": None},
        },
        user_content="600519 近期走势如何？",
    )
    assert "贵州茅台" not in ctx
    assert "600519" in ctx


def test_sanitize_idempotent_and_safe():
    """幂等：重复脱敏无副作用；异常输入原样返回。"""
    obj = {"代码": "000001", "名称": "平安银行", "价格": 10.5}
    once = sanitize_for_llm(obj)
    twice = sanitize_for_llm(once)
    assert once == twice
    assert "名称" not in once and once["代码"] == "000001" and once["价格"] == 10.5
    assert sanitize_for_llm(None) is None
    assert sanitize_for_llm("纯文本") == "纯文本"
    assert sanitize_json_text('{"code": "600519", "name": "贵州茅台"}') == '{"code": "600519"}'
    assert sanitize_json_text("不是 JSON") == "不是 JSON"
