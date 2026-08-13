# -*- coding: utf-8 -*-
"""R14-A4 计划层验收测试（测试先行，硬门）：plan.json 落盘 / 同日覆盖无残留 / 去重键 / 字段校验。

契约（唯一真源：docs/requirements-r14.md 第四节.1 数据字典）：
- plan.json 信封：{date, mode:"close", generated_at, plans:[...]}，
  落盘 {STOCK_REVIEW_DATA_DIR}/reviews/{date}/plan.json（env = 数据根目录，与 replay/quotes 一致）；
- plans[] 可为空数组（空 = 该日无推荐）；qty=None 序列化为 JSON null（不写 0/空串）；
- 去重键：(rec_date, skill_id, code, side, target_price)；同日重跑（reset→add→finalize）整文件覆盖，
  无残留旧计划；
- 校验（accounts/plan.add 返回 {ok, reason, trade}，ok=False 即非法）：
  skill_id 排除 host/review_assistant；side 仅 buy/sell；target_price>0；
  position_cheng 0-10（0 仅在配 qty>0 时合法，见「工具传 0 时用 qty」）；
  qty 可选正整数（None=按成数折算）；source 枚举 {tool, regex_qty, regex_cheng}；
  code 6 位数字（短数字 zfill(6)）；name 缺失落盘时用 code；
- registry：plan.add 校验 + 按去重键去重（重复返回 ok=True 但 reason 含「跳过重复记录」）；
  plan.finalize(date) 单点落盘 registry；plan.reset(date) 清空该日 registry（同日重跑入口）。
模块未实现时整文件 skip；集成阶段启用必须全绿。
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
    from stock_review_crew.accounts import plan
except ImportError:  # pragma: no cover - A1 未实现
    plan = None

pytestmark = pytest.mark.skipif(
    plan is None, reason="A1 accounts/plan.py 未实现（R14 开发中，集成阶段启用）"
)


@pytest.fixture()
def data_dir():
    """仓库内 .tmp 隔离目录（沙箱 pytest tmp_path 有 ACL 问题，见 PLAYBOOK），自建自清。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / "a4_plan"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    for _ in range(3):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            break


def _rec(**over):
    base = {
        "rec_date": "2026-08-11",
        "skill_id": "alang",
        "skill_name": "阿郎",
        "code": "000636",
        "name": "风华高科",
        "side": "buy",
        "target_price": 10.00,
        "position_cheng": 3,
        "qty": None,
        "source": "tool",
    }
    base.update(over)
    return base


def _load(data_dir, d="2026-08-11"):
    p = data_dir / "reviews" / d / "plan.json"
    assert p.exists(), f"plan.json 未落盘: {p}"
    return json.loads(p.read_text("utf-8")), p


# ---------- 落盘与信封 ----------

def test_add_valid_then_finalize_envelope(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan.reset("2026-08-11")
    r = plan.add(_rec(qty=2900))
    assert r["ok"] is True, r["reason"]
    p = plan.finalize("2026-08-11")
    assert p == data_dir / "reviews" / "2026-08-11" / "plan.json"
    raw, _ = _load(data_dir)
    assert raw["date"] == "2026-08-11"
    assert raw["mode"] == "close"  # 批跑仅收盘模式（固定）
    assert isinstance(raw["generated_at"], str) and raw["generated_at"]
    assert len(raw["plans"]) == 1
    d = raw["plans"][0]
    assert d["rec_date"] == "2026-08-11" and d["skill_id"] == "alang"
    assert d["skill_name"] == "阿郎"
    assert d["code"] == "000636" and d["name"] == "风华高科"
    assert d["side"] == "buy" and d["target_price"] == 10.00
    assert d["position_cheng"] == 3 and d["qty"] == 2900
    assert d["source"] == "tool"


def test_qty_none_keeps_json_null(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan.reset("2026-08-11")
    plan.add(_rec())  # qty=None
    plan.finalize("2026-08-11")
    raw, _ = _load(data_dir)
    assert raw["plans"][0]["qty"] is None, "qty 未给时应为 JSON null（不是 0 或空串）"


def test_name_missing_uses_code_and_zfill(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan.reset("2026-08-11")
    plan.add(_rec(name="", code="636"))
    plan.finalize("2026-08-11")
    raw, _ = _load(data_dir)
    assert raw["plans"][0]["name"] == "000636", "name 缺失时落盘用 code"
    assert raw["plans"][0]["code"] == "000636", "code 按 zfill(6) 归一"


def test_empty_plans_means_no_recommendation(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan.reset("2026-08-11")
    plan.finalize("2026-08-11")
    raw, _ = _load(data_dir)
    assert raw["plans"] == [], "plans 可为空数组（空 = 该日无推荐）"


def test_load_missing_returns_empty_envelope(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    assert plan.load("2099-01-01")["plans"] == []


def test_same_day_rewrite_no_residue(monkeypatch, data_dir):
    """同日重跑（reset→add→finalize 两轮）→ 整文件覆盖，无残留旧计划（验收标准 七.1）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan.reset("2026-08-11")
    plan.add(_rec())
    plan.add(_rec(code="600010", target_price=12.50))
    plan.finalize("2026-08-11")
    plan.reset("2026-08-11")
    plan.add(_rec(code="600010", target_price=12.50))
    plan.finalize("2026-08-11")
    raw, _ = _load(data_dir)
    assert len(raw["plans"]) == 1
    assert raw["plans"][0]["code"] == "600010"
    assert raw["plans"][0]["target_price"] == 12.50
    assert all(x["code"] != "000636" for x in raw["plans"]), "旧计划不得残留"


# ---------- 去重键 ----------

def test_add_dedup_same_key_keeps_one(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan.reset("2026-08-11")
    results = [plan.add(_rec()) for _ in range(4)]
    assert results[0]["ok"] is True and "已记录" in results[0]["reason"]
    for r in results[1:]:
        assert r["ok"] is True and "跳过重复" in r["reason"], "重复计划应跳过并明确提示"
    plan.finalize("2026-08-11")
    raw, _ = _load(data_dir)
    assert len(raw["plans"]) == 1


def test_add_keeps_diff_price_side_code_skill_date(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan.reset("2026-08-11")
    plan.reset("2026-08-12")
    for r in (_rec(), _rec(target_price=10.50), _rec(side="sell"),
              _rec(code="600010"), _rec(skill_id="tiechui"), _rec(rec_date="2026-08-12")):
        assert plan.add(r)["ok"] is True
    plan.finalize("2026-08-11")
    raw, _ = _load(data_dir)
    assert len(raw["plans"]) == 5, "价格/方向/代码/分析师/日期不同互不去重"


def test_dedup_structured_and_regex_same_key(monkeypatch, data_dir):
    """同一去重键下 tool 与 regex_cheng 两来源 → 去重为 1 条（来源不同不算新计划）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan.reset("2026-08-11")
    plan.add(_rec(source="tool"))
    plan.add(_rec(source="regex_cheng"))
    plan.finalize("2026-08-11")
    raw, _ = _load(data_dir)
    assert len(raw["plans"]) == 1


# ---------- 校验 ----------

@pytest.mark.parametrize("skill_id", ["host", "review_assistant"])
def test_rejects_host_and_review_assistant(monkeypatch, data_dir, skill_id):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    r = plan.add(_rec(skill_id=skill_id))
    assert r["ok"] is False, f"{skill_id} 必须排除"
    assert skill_id in r["reason"]


@pytest.mark.parametrize("side", ["hold", "watch", "", "BUY"])
def test_rejects_side_not_buy_sell(side):
    r = plan.add(_rec(side=side))
    assert r["ok"] is False and "side" in r["reason"], f"side={side!r} 应拒绝"


@pytest.mark.parametrize("price", [0.0, -1.0, -0.01])
def test_rejects_target_price_not_positive(price):
    r = plan.add(_rec(target_price=price))
    assert r["ok"] is False and "target_price" in r["reason"]


@pytest.mark.parametrize("pos", [11, -1])
def test_rejects_position_cheng_out_of_range(pos):
    r = plan.add(_rec(position_cheng=pos))
    assert r["ok"] is False and "position_cheng" in r["reason"]


def test_position_cheng_zero_requires_qty():
    r = plan.add(_rec(position_cheng=0))
    assert r["ok"] is False, "0 成数未配 qty 应拒绝（工具传 0 时用 qty）"


def test_position_cheng_zero_with_qty_ok():
    r = plan.add(_rec(position_cheng=0, qty=2900))
    assert r["ok"] is True, r["reason"]


@pytest.mark.parametrize("qty", [0, -5, 100.5])
def test_rejects_qty_not_positive_int(qty):
    r = plan.add(_rec(qty=qty))
    assert r["ok"] is False and "qty" in r["reason"]


@pytest.mark.parametrize("source", ["manual", "", "llm"])
def test_rejects_source_not_enum(source):
    r = plan.add(_rec(source=source))
    assert r["ok"] is False and "source" in r["reason"]


@pytest.mark.parametrize("code", ["1234567", "abc", ""])
def test_rejects_code_not_6digit(code):
    r = plan.add(_rec(code=code))
    assert r["ok"] is False and "code" in r["reason"]


def test_invalid_plan_not_in_registry(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    plan.reset("2026-08-11")
    plan.add(_rec(side="hold"))
    plan.finalize("2026-08-11")
    raw, _ = _load(data_dir)
    assert raw["plans"] == [], "非法计划不得进入 registry"
