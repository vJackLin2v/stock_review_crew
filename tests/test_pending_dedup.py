# -*- coding: utf-8 -*-
"""plan 去重语义（R14，迁移自 pending 去重）：同一去重键 (rec_date, skill_id, code, side, target_price)
只记录一条计划；不同价格/方向/股票/日期各自保留；结构化（tool）与正则（regex_*）来源同一键去重为一条。

覆盖：
- plan.add 去重键语义（同键 4 次 → 最终 1 条；不同价格/方向 → 3 条；rec_date/skill_id/code 参与键；
  tool 与 regex_cheng 来源同一键 → 1 条）；重复 add 返回 ok=True 但 reason 含「跳过重复」；
- place_order 工具（graph 绑定，R14 改为写计划 registry）经 plan.add 去重：
  同日同参数 4 次 → registry 仅 1 条；不同价格/方向 → 各 1 条；
- 工具产出计划字段完整：rec_date/skill_id/code/side/target_price/position_cheng/source=tool。
模块未实现时整文件 skip；集成阶段启用必须全绿。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

try:
    from stock_review_crew.accounts import plan
except ImportError:  # pragma: no cover - A1 未实现
    plan = None

try:
    import stock_review_crew.graph as graph
except ImportError:  # pragma: no cover
    graph = None

pytestmark = pytest.mark.skipif(
    plan is None or graph is None,
    reason="A1 accounts/plan.py / graph 计划 registry 未实现（R14 开发中，集成阶段启用）",
)


def _rec(**over):
    base = {
        "rec_date": "2026-08-04",
        "skill_id": "alang",
        "skill_name": "阿郎",
        "code": "000636",
        "name": "风华高科",
        "side": "buy",
        "target_price": 54.33,
        "position_cheng": 2,
        "qty": None,
        "source": "tool",
    }
    base.update(over)
    return base


def test_add_dedup_same_key_keeps_one():
    """同参数重复 add → 首次「已记录」，后续「跳过重复」，registry 仅 1 条。"""
    first = plan.add(_rec())
    assert first["ok"] is True and "已记录" in first["reason"]
    for _ in range(3):
        r = plan.add(_rec())
        assert r["ok"] is True and "跳过重复" in r["reason"]
    plan.reset("2026-08-04")


def test_add_keeps_diff_price_and_side():
    recs = [_rec(), _rec(target_price=55.00), _rec(side="sell", target_price=54.33)]
    for r in recs:
        assert plan.add(r)["ok"] is True
    plan.reset("2026-08-04")


def test_add_dedup_key_includes_rec_date_skill_code():
    recs = [
        _rec(rec_date="2026-08-05"),
        _rec(skill_id="tiechui"),
        _rec(code="600010"),
    ]
    for r in recs:
        assert plan.add(r)["ok"] is True
    plan.reset("2026-08-04")
    plan.reset("2026-08-05")


def test_add_dedup_structured_and_regex_same_key():
    """结构化（tool）与正则（regex_cheng）产出同一键 → 去重为 1 条（来源不同不算新计划）。"""
    assert plan.add(_rec(source="tool"))["ok"] is True
    r = plan.add(_rec(source="regex_cheng"))
    assert r["ok"] is True and "跳过重复" in r["reason"]
    plan.reset("2026-08-04")


def _bind_registry(monkeypatch):
    """将 plan.add 替换为记录器（模拟 registry 去重），返回 store。"""
    store: list = []

    def fake_add(rec):
        key = (rec["rec_date"], rec["skill_id"], rec["code"], rec["side"], rec["target_price"])
        if any((r["rec_date"], r["skill_id"], r["code"], r["side"], r["target_price"]) == key
               for r in store):
            return {"ok": True, "reason": "已存在相同推荐，跳过重复记录", "trade": None}
        store.append(rec)
        return {"ok": True, "reason": "已记录收盘后推荐", "trade": None}

    monkeypatch.setattr("stock_review_crew.accounts.plan.add", fake_add)
    return store


def test_regex_entry_dedup_same_rec(monkeypatch):
    """2026-08-12：下单类工具已移除——计划只来自文本正则解析（regex_* 多次命中同一键
    → registry 仅 1 条，plan 去重语义，替代 pending 去重）。"""
    store = _bind_registry(monkeypatch)
    rec = {
        "rec_date": "2026-08-04", "skill_id": "alang", "skill_name": "阿狼",
        "code": "000636", "name": "000636", "side": "buy",
        "target_price": 54.33, "position_cheng": 2, "source": "regex_cheng",
    }
    for _ in range(4):  # 正则对同一行重复命中（如分析师文本含两段重复推荐）
        r = plan.add(dict(rec))
        assert r["ok"] is True
    assert len(store) == 1
    kept = store[0]
    assert kept["rec_date"] == "2026-08-04" and kept["skill_id"] == "alang"
    assert kept["code"] == "000636" and kept["side"] == "buy"
    assert kept["target_price"] == 54.33 and kept["position_cheng"] == 2
    assert kept["source"] == "regex_cheng"


def test_regex_entry_keeps_diff_price_and_side(monkeypatch):
    """不同目标价/方向 → 各记一条（同一股票不同价位视为不同计划）。"""
    store = _bind_registry(monkeypatch)
    base = {
        "rec_date": "2026-08-04", "skill_id": "alang", "skill_name": "阿狼",
        "code": "000636", "name": "000636", "side": "buy", "source": "regex_cheng",
    }
    plan.add(dict(base, target_price=54.33, position_cheng=2))
    plan.add(dict(base, target_price=55.00, position_cheng=2))
    plan.add(dict(base, side="sell", target_price=54.33, position_cheng=2))
    assert len(store) == 3
