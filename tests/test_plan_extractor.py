# -*- coding: utf-8 -*-
"""R15/P2-8 plan_extractor 统一正则解析测试（手算用例）。

graph._llm_analyst 内三段正则（qty式 / 成数式 / 结构化成数式）归一为
accounts/plan_extractor.extract_plans。每条用例手算断言
code/side/target_price/position_cheng|qty/source 与 tool_log 条目。

契约（与 accounts/plan.py 一致）：plan registry 为内存态——reset(date) 清空该日，
finalize(date) 落盘 {STOCK_REVIEW_DATA_DIR}/reviews/{date}/plan.json 后读回校验
（数据根尊重 STOCK_REVIEW_DATA_DIR，隔离目录见 data_dir fixture）。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

try:
    from stock_review_crew.accounts import plan as plan_store
    from stock_review_crew.accounts.plan_extractor import extract_plans
except ImportError:  # pragma: no cover - 实现未就绪时整文件跳过
    plan_store = None
    extract_plans = None

pytestmark = pytest.mark.skipif(
    plan_store is None or extract_plans is None,
    reason="accounts/plan.py 或 accounts/plan_extractor.py 未实现",
)

DATE = "2026-08-12"
SKILL_ID = "alang"
SKILL_NAME = "阿郎"


@pytest.fixture()
def data_dir():
    """仓库内 .tmp 隔离目录（沙箱 pytest tmp_path 有 ACL 问题，见 PLAYBOOK），自建自清。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / "plan_extractor"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    for _ in range(3):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            break


def _run(monkeypatch, data_dir, text, held_codes):
    """执行 extract_plans → finalize 落盘 → 读回 (tool_log, plans[])。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan_store.reset(DATE)
    tool_log = extract_plans(text, DATE, SKILL_ID, SKILL_NAME, held_codes)
    plan_store.finalize(DATE)
    p = data_dir / "reviews" / DATE / "plan.json"
    assert p.exists(), f"plan.json 未落盘: {p}"
    return tool_log, json.loads(p.read_text("utf-8"))["plans"]


# ---------- 手算用例 ----------


def test_qty_cheng_structured_with_prefix(monkeypatch, data_dir):
    """买入 002384：买入价189.00元，仓位2成 → buy / 189.00 / 成数2 / regex_cheng。"""
    tool_log, plans = _run(monkeypatch, data_dir, "买入 002384：买入价189.00元，仓位2成", set())
    assert len(plans) == 1, plans
    d = plans[0]
    assert d["code"] == "002384" and d["side"] == "buy"
    assert abs(d["target_price"] - 189.00) < 1e-9
    assert d["position_cheng"] == 2 and d["qty"] is None
    assert d["source"] == "regex_cheng"
    assert d["name"] == "002384", "名称缺失落盘用 code"
    assert len(tool_log) == 1 and tool_log[0]["type"] == "place_order_auto"
    assert tool_log[0]["code"] == "002384" and tool_log[0]["side"] == "buy"


def test_cheng_no_prefix_price_kw_side(monkeypatch, data_dir):
    """002384：买入价189.00元，仓位2成（无前缀）→ buy（价格关键字判定）/ 成数2。"""
    tool_log, plans = _run(monkeypatch, data_dir, "002384：买入价189.00元，仓位2成", set())
    assert len(plans) == 1, plans
    d = plans[0]
    assert d["code"] == "002384" and d["side"] == "buy"
    assert abs(d["target_price"] - 189.00) < 1e-9
    assert d["position_cheng"] == 2 and d["qty"] is None
    assert d["source"] == "regex_cheng"
    assert len(tool_log) == 1 and tool_log[0]["type"] == "place_order_auto"


def test_qty_structured_with_name(monkeypatch, data_dir):
    """买入 002384 东山精密：买入价189.00元，买入100股 → buy / 东山精密 / qty100 / regex_qty。"""
    tool_log, plans = _run(
        monkeypatch, data_dir, "买入 002384 东山精密：买入价189.00元，买入100股", set()
    )
    assert len(plans) == 1, plans
    d = plans[0]
    assert d["code"] == "002384" and d["side"] == "buy" and d["name"] == "东山精密"
    assert abs(d["target_price"] - 189.00) < 1e-9
    assert d["qty"] == 100 and d["position_cheng"] == 0
    assert d["source"] == "regex_qty"
    assert len(tool_log) == 1 and tool_log[0]["type"] == "place_order_auto_qty"
    assert tool_log[0]["code"] == "002384" and tool_log[0]["side"] == "buy"


def test_sell_qty_structured_with_name(monkeypatch, data_dir):
    """卖出 000967 盈峰环境：卖出价12.50元，卖出200股 → sell / 盈峰环境 / qty200（有持仓）。"""
    tool_log, plans = _run(
        monkeypatch, data_dir, "卖出 000967 盈峰环境：卖出价12.50元，卖出200股", {"000967"}
    )
    assert len(plans) == 1, plans
    d = plans[0]
    assert d["code"] == "000967" and d["side"] == "sell" and d["name"] == "盈峰环境"
    assert abs(d["target_price"] - 12.50) < 1e-9
    assert d["qty"] == 200 and d["position_cheng"] == 0
    assert d["source"] == "regex_qty"
    assert len(tool_log) == 1 and tool_log[0]["type"] == "place_order_auto_qty"
    assert tool_log[0]["side"] == "sell"


def test_sell_cheng_with_prefix_held(monkeypatch, data_dir):
    """卖出 000967：卖出价12.50元，仓位3成 → sell / 成数3（有持仓）。"""
    tool_log, plans = _run(monkeypatch, data_dir, "卖出 000967：卖出价12.50元，仓位3成", {"000967"})
    assert len(plans) == 1, plans
    d = plans[0]
    assert d["code"] == "000967" and d["side"] == "sell"
    assert abs(d["target_price"] - 12.50) < 1e-9
    assert d["position_cheng"] == 3 and d["qty"] is None
    assert d["source"] == "regex_cheng"
    assert len(tool_log) == 1 and tool_log[0]["type"] == "place_order_auto"
    assert tool_log[0]["side"] == "sell"


def test_sell_without_position_skipped(monkeypatch, data_dir):
    """卖出无持仓：held_codes 不含该 code → sell_skipped_no_position 且不落 plan。"""
    tool_log, plans = _run(
        monkeypatch, data_dir, "卖出 000967：卖出价12.50元，仓位3成", {"600519"}
    )
    assert len(plans) == 0, f"无持仓卖出不应落 plan: {plans}"
    assert len(tool_log) == 1
    assert tool_log[0]["type"] == "sell_skipped_no_position"
    assert tool_log[0]["code"] == "000967"
    assert "无持仓" in tool_log[0]["reason"]


def test_structured_cheng_no_duplicate(monkeypatch, data_dir):
    """结构化成数式只匹配一次（2026-08-10 曾单日单股 4 条 pending 的回归防线）。"""
    _, plans = _run(monkeypatch, data_dir, "买入 002384 东山精密：买入价189.00元，仓位2成", set())
    assert len(plans) == 1, f"统一正则重复匹配: {plans}"


def test_buy_with_reason_tail(monkeypatch, data_dir):
    """R16：买入行带「，原因是XXX」尾部（无前缀）→ 仍正确解析 code/buy/3.90/cheng=1；
    原因文本不被误解析成代码/价格/成数。"""
    tool_log, plans = _run(
        monkeypatch, data_dir,
        "002172：买入价3.90元，仓位1成，原因是医疗服务2板龙头、医药主线净流入43亿", set()
    )
    assert len(plans) == 1, plans
    d = plans[0]
    assert d["code"] == "002172" and d["side"] == "buy"
    assert abs(d["target_price"] - 3.90) < 1e-9
    assert d["position_cheng"] == 1 and d["qty"] is None
    assert d["source"] == "regex_cheng"
    assert d["name"] == "002172", "名称缺失落盘用 code"
    assert len(tool_log) == 1 and tool_log[0]["type"] == "place_order_auto"
    assert tool_log[0]["code"] == "002172" and tool_log[0]["side"] == "buy"


def test_buy_with_reason_tail_and_prefix(monkeypatch, data_dir):
    """R16：带「买入」前缀 + 原因尾部 → 解析一致（side 由前缀判定，成数不因尾部改变）。"""
    tool_log, plans = _run(
        monkeypatch, data_dir,
        "买入 002172：买入价3.90元，仓位1成，原因是医疗服务2板龙头", set()
    )
    assert len(plans) == 1, plans
    d = plans[0]
    assert d["code"] == "002172" and d["side"] == "buy"
    assert abs(d["target_price"] - 3.90) < 1e-9
    assert d["position_cheng"] == 1 and d["qty"] is None
    assert d["source"] == "regex_cheng"
    assert len(tool_log) == 1 and tool_log[0]["type"] == "place_order_auto"
    assert tool_log[0]["code"] == "002172" and tool_log[0]["side"] == "buy"


def test_sell_with_reason_tail_still_parses(monkeypatch, data_dir):
    """R16：卖出行即使带原因尾部（不应出现，但防御）也能解析且卖出语义不变。"""
    tool_log, plans = _run(
        monkeypatch, data_dir,
        "卖出 000967：卖出价12.50元，仓位3成，原因是冲高兑现", {"000967"}
    )
    assert len(plans) == 1, plans
    d = plans[0]
    assert d["code"] == "000967" and d["side"] == "sell"
    assert abs(d["target_price"] - 12.50) < 1e-9
    assert d["position_cheng"] == 3 and d["qty"] is None
    assert d["source"] == "regex_cheng"
    assert len(tool_log) == 1 and tool_log[0]["type"] == "place_order_auto"
    assert tool_log[0]["side"] == "sell"
