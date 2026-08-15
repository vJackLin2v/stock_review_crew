# -*- coding: utf-8 -*-
"""个股止盈止损条件单引擎专项测试（requirements-trading-v1 §二/§三/§五）。

覆盖：三字段默认/显式绑定、正常止损（盘中低点触发=止损价）、跳空低开（=开盘价）、
跳空高开止盈（=开盘价）、一字跌停顺延（当日不卖、次日开板卖）、13 日超时（收盘价卖）、
优先级（同日止损>止盈）、open 缺失降级（不跳空，按止损/止盈价成交）。

- 引擎级（check_conditional_orders）用确定性 fixture 精确断言成交价/费用/realized/持仓；
- 集成级（settle_plans / run_replay）验证条件单卖出在快照与后续撮合可见、确定性一致。
- 数据根 STOCK_REVIEW_DATA_DIR → .tmp 隔离，绝不写真实 data/。
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

from stock_review_crew.accounts import engine
from stock_review_crew.accounts import replay

try:
    from stock_review_crew.accounts.plan import PlanRec, save_plan
    from stock_review_crew.accounts.plan_extractor import extract_plans
except ImportError:  # pragma: no cover
    PlanRec = None
    save_plan = None
    extract_plans = None

PYTESTMARK_SKIP = PlanRec is None or save_plan is None or extract_plans is None


@pytest.fixture()
def data_dir():
    """仓库内 .tmp 隔离目录（沙箱 pytest tmp_path 有 ACL 问题，见 PLAYBOOK），自建自清。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / "cond_orders"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    for _ in range(3):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            break


BUY_DAY = "2026-08-03"


def _buy_pos(acct, *, code="600519", target=10.00, qty=2900, conditional=None,
             day=None):
    """用 place_order 建仓（显式 qty），返回成交记录；day 缺省用 BUY_DAY 常规行情。"""
    day = day or {"date": BUY_DAY, "pre_close": 10.00, "open": 10.00,
                  "high": 10.20, "low": 9.95, "close": 10.10}
    return engine.place_order(
        acct, code, "贵州茅台", "buy", target, 0, day, qty=qty,
        conditional=conditional)


def _new_acct(code="600519", conditional=None, *, target=10.00, qty=2900):
    acct = engine.open_account("alang")
    engine._settle_day(acct, BUY_DAY)
    r = _buy_pos(acct, code=code, target=target, qty=qty, conditional=conditional)
    assert r.ok, f"建仓失败：{r.reason}"
    return acct


# ---------------------------------------------------------------------------
# ① 三字段绑定（显式 / 默认）
# ---------------------------------------------------------------------------

def test_buy_attaches_conditional_fields():
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    pos = acct["positions"]["600519"]
    assert pos["qty"] == 2900
    assert pos["buy_date"] == BUY_DAY
    assert pos["stop_loss"] == pytest.approx(9.50)
    assert pos["take_profit"] == pytest.approx(11.00)
    assert pos["max_hold_days"] == 13


def test_conditional_defaults_applied_in_engine():
    """None 字段按买入价×0.95 / ×1.10 / 13 补齐（apply_conditional_defaults）；
    trailing_drawdown 不补默认（保持 None → 固定止盈）。"""
    d = engine.apply_conditional_defaults({"stop_loss": None, "take_profit": None,
                                           "max_hold_days": None}, 10.00)
    assert d["stop_loss"] == pytest.approx(9.50)
    assert d["take_profit"] == pytest.approx(11.00)
    assert d["max_hold_days"] == 13
    assert d["trailing_drawdown"] is None
    # 部分显式：显式字段保留，缺失字段走默认
    d2 = engine.apply_conditional_defaults({"stop_loss": 9.0, "take_profit": None,
                                            "max_hold_days": None}, 10.00)
    assert d2["stop_loss"] == pytest.approx(9.0)
    assert d2["take_profit"] == pytest.approx(11.0)
    assert d2["max_hold_days"] == 13
    assert d2["trailing_drawdown"] is None


def test_buy_without_conditional_no_mecanical_sell():
    """老持仓（无条件单字段）→ check_conditional_orders 不机械卖出。"""
    acct = _new_acct()  # conditional=None → position 无条件单字段
    trades = engine.check_conditional_orders(acct, {"date": "2026-08-04"}, {})
    assert trades == []
    assert "600519" in acct["positions"]


# ---------------------------------------------------------------------------
# ② 正常止损（盘中低点触发，成交=止损价）
# ---------------------------------------------------------------------------

def test_stop_loss_normal_touched_at_stop_price():
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    # 盘中最低 9.40 < 止损 9.50，开盘 9.80 ≥ 止损 → 成交价 = 止损价 9.50
    day = {"date": "2026-08-04", "pre_close": 10.00, "open": 9.80,
           "high": 9.90, "low": 9.40, "close": 9.50}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    t = trades[0]
    assert t["side"] == "sell" and t["status"] == "成交"
    assert t["price"] == pytest.approx(9.50)
    assert t["qty"] == 2900
    assert t["condition"] == "stop_loss"
    assert "600519" not in acct["positions"], "止损后应清仓"
    # 费用口径（R12）：佣金 万2.5 最低5、印花税卖出 0.05%、过户费 0.001%
    amount = 9.50 * 2900
    assert t["fee"] == pytest.approx(round(max(amount * 0.00025, 5), 6))
    assert t["stamp_tax"] == pytest.approx(round(amount * 0.0005, 6))
    assert t["transfer_fee"] == pytest.approx(round(amount * 0.00001, 6))
    realized = (9.50 - 10.00) * 2900 - t["fee"] - t["stamp_tax"] - t["transfer_fee"]
    assert t["realized"] == pytest.approx(realized)
    assert acct["realized_pnl"] == pytest.approx(realized)
    # 现金自洽：买入后现金 + 卖出净收入
    cash_after_sell = acct["cash"]


# ---------------------------------------------------------------------------
# ③ 跳空低开止损（成交=开盘价）
# ---------------------------------------------------------------------------

def test_stop_loss_gap_open_at_open_price():
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    # 跳空低开：开盘 9.20 < 止损 9.50 且 盘中最低 9.10 → 成交价 = min(open, stop) = 9.20
    day = {"date": "2026-08-04", "pre_close": 10.00, "open": 9.20,
           "high": 9.45, "low": 9.10, "close": 9.30}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    assert trades[0]["price"] == pytest.approx(9.20)
    assert trades[0]["condition"] == "stop_loss"


# ---------------------------------------------------------------------------
# ④ 跳空高开止盈（成交=开盘价）
# ---------------------------------------------------------------------------

def test_take_profit_gap_open_at_open_price():
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    # 跳空高开：开盘 11.50 ≥ 止盈 11.00 且 盘中最高 11.60 → 成交价 = max(open, tp) = 11.50
    day = {"date": "2026-08-04", "pre_close": 10.50, "open": 11.50,
           "high": 11.60, "low": 10.90, "close": 11.40}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    assert trades[0]["price"] == pytest.approx(11.50)
    assert trades[0]["condition"] == "take_profit"


def test_take_profit_normal_reaches_tp_price():
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    # 盘中最高 11.20 ≥ 止盈 11.00，开盘 10.60 ≤ 止盈 → 成交价 = 止盈价 11.00
    day = {"date": "2026-08-04", "pre_close": 10.50, "open": 10.60,
           "high": 11.20, "low": 10.50, "close": 10.90}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    assert trades[0]["price"] == pytest.approx(11.00)
    assert trades[0]["condition"] == "take_profit"


# ---------------------------------------------------------------------------
# ⑤ 一字跌停顺延（当日不卖、次日开板才卖）
# ---------------------------------------------------------------------------

def test_one_word_limit_down_postpones_to_next_day():
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    # 一字跌停：开盘=最低=最高=收盘=跌停价（pre_close 10.00，主板跌停 9.00）
    day1 = {"date": "2026-08-04", "pre_close": 10.00, "open": 9.00,
            "high": 9.00, "low": 9.00, "close": 9.00}
    trades1 = engine.check_conditional_orders(acct, day1, {"600519": day1})
    assert trades1 == [], "一字跌停当日不卖（顺延）"
    assert "600519" in acct["positions"], "一字跌停顺延，持仓仍在"
    # 次日开板：最低 8.90 < 止损 9.50 → 止损卖出
    day2 = {"date": "2026-08-05", "pre_close": 9.00, "open": 9.30,
            "high": 9.40, "low": 8.90, "close": 9.10}
    trades2 = engine.check_conditional_orders(acct, day2, {"600519": day2})
    assert len(trades2) == 1
    assert trades2[0]["price"] == pytest.approx(9.30), "开板后按 min(open, stop) 成交"
    assert "600519" not in acct["positions"]


def test_one_word_limit_down_no_open_fallback():
    """open 缺失（源拿不到）时一字跌停退化为 低=高=收盘=跌停价 仍识别（顺延）。"""
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    day = {"date": "2026-08-04", "pre_close": 10.00, "high": 9.00, "low": 9.00, "close": 9.00}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert trades == [], "open 缺失但封死签名成立 → 顺延不卖"
    assert "600519" in acct["positions"]


# ---------------------------------------------------------------------------
# ⑥ 13 日超时（收盘价卖）—— 引擎级：max_hold_days=2 模拟快速超时
# ---------------------------------------------------------------------------

def test_timeout_sells_at_close():
    acct = _new_acct(conditional={"stop_loss": 5.00, "take_profit": 99.0, "max_hold_days": 2})
    # 第 2 个结算日（持有第 2 天）：未触止损止盈 → 收盘价卖出
    day = {"date": "2026-08-04", "pre_close": 10.00, "open": 10.10,
           "high": 10.30, "low": 9.90, "close": 10.25}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    assert trades[0]["condition"] == "timeout"
    assert trades[0]["price"] == pytest.approx(10.25), "超时按收盘价卖出"


def test_timeout_not_before_max_hold_days():
    """未到最大持有天数且未触止损止盈 → 不卖。"""
    acct = _new_acct(conditional={"stop_loss": 5.00, "take_profit": 99.0, "max_hold_days": 13})
    day = {"date": "2026-08-04", "pre_close": 10.00, "open": 10.10,
           "high": 10.30, "low": 9.90, "close": 10.25}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert trades == [], "持有第 2 天 < 13，不超时"
    assert "600519" in acct["positions"]


# ---------------------------------------------------------------------------
# ⑦ 优先级：同日既触止损又触止盈 → 按止损
# ---------------------------------------------------------------------------

def test_stop_loss_takes_priority_over_take_profit():
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    # 同日：盘中最低 9.30 < 止损，且盘中最高 11.30 ≥ 止盈 → 按止损卖
    day = {"date": "2026-08-04", "pre_close": 10.00, "open": 10.00,
           "high": 11.30, "low": 9.30, "close": 10.50}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    assert trades[0]["condition"] == "stop_loss"
    assert trades[0]["price"] == pytest.approx(9.50), "止损优先，按止损价成交"
    assert "600519" not in acct["positions"]


# ---------------------------------------------------------------------------
# ⑧ open 缺失降级：不跳空，按止损/止盈价成交
# ---------------------------------------------------------------------------

def test_open_missing_stop_loss_uses_stop_price():
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    day = {"date": "2026-08-04", "pre_close": 10.00, "high": 9.40, "low": 9.20, "close": 9.30}
    # 无 open → 跳空判断退化为不跳空：成交价 = 止损价 9.50
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    assert trades[0]["condition"] == "stop_loss"
    assert trades[0]["price"] == pytest.approx(9.50)


def test_open_missing_take_profit_uses_tp_price():
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    day = {"date": "2026-08-04", "pre_close": 10.00, "high": 11.20, "low": 10.50, "close": 10.90}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    assert trades[0]["condition"] == "take_profit"
    assert trades[0]["price"] == pytest.approx(11.00)


# ---------------------------------------------------------------------------
# ⑧b 移动止盈（trailing take-profit，requirements-trading-v1 §六）
# ---------------------------------------------------------------------------
# 基准：买入价 10.00（avg_cost≈10），take_profit=11.00（激活阈值），trailing_drawdown=0.05
# → 激活/跟踪用 stop = peak_high − 0.05×10 = peak_high − 0.5。

def _trail_acct(code="600519", *, target=10.00, tdd=0.05,
                stop_loss=5.00, max_hold_days=13, qty=2900):
    """建仓（启用移动止盈）。"""
    return _new_acct(code=code, target=target, qty=qty, conditional={
        "stop_loss": stop_loss, "take_profit": 11.00,
        "max_hold_days": max_hold_days, "trailing_drawdown": tdd,
    })


def test_buy_attaches_trailing_and_peak_high():
    acct = _trail_acct()
    pos = acct["positions"]["600519"]
    assert pos["trailing_drawdown"] == pytest.approx(0.05)
    assert pos["peak_high"] is None, "买入时 peak_high 初始化为 None"


def test_trailing_not_activated_does_not_sell():
    """未达激活阈值（high < 11）→ 不卖、峰值未激活（peak_high 保持 None）。"""
    acct = _trail_acct()
    day = {"date": "2026-08-04", "pre_close": 10.00, "open": 10.40,
           "high": 10.90, "low": 10.20, "close": 10.60}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert trades == []
    assert "600519" in acct["positions"]
    assert acct["positions"]["600519"].get("peak_high") is None


def test_trailing_activated_not_enough_drawdown_no_sell():
    """激活但未回落 5pp → 不卖；峰值跟踪正常。"""
    acct = _trail_acct()
    # Day1 激活：high 11.70 → peak 11.70，stop 11.20；low 11.30 ≥ stop → 不卖
    day1 = {"date": "2026-08-04", "pre_close": 10.00, "open": 11.20,
            "high": 11.70, "low": 11.30, "close": 11.50}
    trades = engine.check_conditional_orders(acct, day1, {"600519": day1})
    assert trades == []
    pos = acct["positions"]["600519"]
    assert pos["peak_high"] == pytest.approx(11.70)
    # Day2：high 11.60（peak 仍 11.70），low 11.35 ≥ stop 11.20 → 未回落 5pp → 不卖
    day2 = {"date": "2026-08-05", "pre_close": 11.50, "open": 11.50,
            "high": 11.60, "low": 11.35, "close": 11.45}
    trades = engine.check_conditional_orders(acct, day2, {"600519": day2})
    assert trades == []
    assert "600519" in acct["positions"]
    assert acct["positions"]["600519"]["peak_high"] == pytest.approx(11.70)


def test_trailing_peak_tracking_updates_to_new_high():
    """激活后峰值随新高抬升：peak 11.50 → 11.70。"""
    acct = _trail_acct()
    day1 = {"date": "2026-08-04", "pre_close": 10.00, "open": 10.50,
            "high": 11.50, "low": 11.10, "close": 11.30}
    engine.check_conditional_orders(acct, day1, {"600519": day1})
    assert acct["positions"]["600519"]["peak_high"] == pytest.approx(11.50)
    day2 = {"date": "2026-08-05", "pre_close": 11.30, "open": 11.40,
            "high": 11.70, "low": 11.25, "close": 11.40}
    trades = engine.check_conditional_orders(acct, day2, {"600519": day2})
    assert trades == [], "low 11.25 ≥ 新 stop 11.20 → 未触发"
    assert acct["positions"]["600519"]["peak_high"] == pytest.approx(11.70)


def test_trailing_drawdown_triggers_sell_at_stop():
    """回落触发：成交价 = min(open, stop)（open ≥ stop → 按 stop，跳空低开按开盘价）。"""
    acct = _trail_acct()
    # Day1 激活：peak 11.70，stop 11.20，不卖
    day1 = {"date": "2026-08-04", "pre_close": 10.00, "open": 11.20,
            "high": 11.70, "low": 11.30, "close": 11.50}
    engine.check_conditional_orders(acct, day1, {"600519": day1})
    assert "600519" in acct["positions"]
    # Day2 触发（非跳空）：open 11.30 ≥ stop 11.20，low 11.10 < 11.20 → 成交价 = stop 11.20
    day2 = {"date": "2026-08-05", "pre_close": 11.50, "open": 11.30,
            "high": 11.40, "low": 11.10, "close": 11.18}
    trades = engine.check_conditional_orders(acct, day2, {"600519": day2})
    assert len(trades) == 1
    assert trades[0]["condition"] == "trailing_take_profit"
    assert trades[0]["price"] == pytest.approx(11.20)
    assert "600519" not in acct["positions"]


def test_trailing_gap_open_sells_at_open_price():
    """跳空低开触发：open < stop → 成交价 = 开盘价。"""
    acct = _trail_acct()
    day1 = {"date": "2026-08-04", "pre_close": 10.00, "open": 11.20,
            "high": 11.70, "low": 11.30, "close": 11.50}
    engine.check_conditional_orders(acct, day1, {"600519": day1})
    # Day2 跳空低开：open 10.80 < stop 11.20，low 10.75 → 成交价 = open 10.80
    day2 = {"date": "2026-08-05", "pre_close": 11.50, "open": 10.80,
            "high": 10.95, "low": 10.75, "close": 10.85}
    trades = engine.check_conditional_orders(acct, day2, {"600519": day2})
    assert len(trades) == 1
    assert trades[0]["condition"] == "trailing_take_profit"
    assert trades[0]["price"] == pytest.approx(10.80)


def test_trailing_activate_and_trigger_same_day():
    """同日激活 + 回落触发：high 触发激活、low 又跌穿 stop → 当日卖出。"""
    acct = _trail_acct()
    # high 11.80 激活（stop 11.30），low 11.00 < 11.30 → 同日触发；open 11.40 ≥ stop → 成交 stop 11.30
    day = {"date": "2026-08-04", "pre_close": 10.00, "open": 11.40,
           "high": 11.80, "low": 11.00, "close": 11.20}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    assert trades[0]["condition"] == "trailing_take_profit"
    assert trades[0]["price"] == pytest.approx(11.30)
    assert "600519" not in acct["positions"]


def test_trailing_no_open_uses_stop_price():
    """open 缺失 → 移动止盈成交价 = stop。"""
    acct = _trail_acct()
    day1 = {"date": "2026-08-04", "pre_close": 10.00, "open": 11.20,
            "high": 11.70, "low": 11.30, "close": 11.50}
    engine.check_conditional_orders(acct, day1, {"600519": day1})
    day2 = {"date": "2026-08-05", "pre_close": 11.50,
            "high": 11.30, "low": 11.10, "close": 11.15}
    trades = engine.check_conditional_orders(acct, day2, {"600519": day2})
    assert len(trades) == 1
    assert trades[0]["condition"] == "trailing_take_profit"
    assert trades[0]["price"] == pytest.approx(11.20)


def test_trailing_absent_fixed_take_profit_unchanged():
    """无 trailing_drawdown → 固定止盈行为不变（成交 max(open, tp)）。"""
    acct = _new_acct(conditional={"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13})
    assert acct["positions"]["600519"].get("trailing_drawdown") is None
    assert acct["positions"]["600519"].get("peak_high") is None
    day = {"date": "2026-08-04", "pre_close": 10.00, "open": 11.50,
           "high": 11.60, "low": 10.90, "close": 11.40}
    trades = engine.check_conditional_orders(acct, day, {"600519": day})
    assert len(trades) == 1
    assert trades[0]["condition"] == "take_profit"
    assert trades[0]["price"] == pytest.approx(11.50)


def test_apply_conditional_defaults_preserves_trailing():
    d = engine.apply_conditional_defaults(
        {"stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13,
         "trailing_drawdown": 0.05}, 10.00)
    assert d["trailing_drawdown"] == pytest.approx(0.05)
    assert d["take_profit"] == pytest.approx(11.00)
    d2 = engine.apply_conditional_defaults(
        {"stop_loss": None, "take_profit": None, "max_hold_days": None,
         "trailing_drawdown": None}, 10.00)
    assert d2["trailing_drawdown"] is None
    assert d2["take_profit"] == pytest.approx(11.00)


# ---------------------------------------------------------------------------
# ⑨ 计划三字段校验 / 解析
# ---------------------------------------------------------------------------

@pytest.mark.skipif(PYTESTMARK_SKIP, reason="plan/plan_extractor 未实现")
def test_plan_validation_conditional_fields():
    """stop_loss 必须 < target_price < take_profit；max_hold_days 为正整数。"""
    ok = PlanRec.from_dict({
        "rec_date": "2026-08-03", "skill_id": "alang", "code": "600519", "side": "buy",
        "target_price": 10.00, "position_cheng": 3, "qty": None, "source": "tool",
        "stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13,
    })
    assert ok.stop_loss == 9.50 and ok.take_profit == 11.0 and ok.max_hold_days == 13
    # 实际校验走 validate_plan / from_dict 抛 ValueError
    with pytest.raises(ValueError):
        PlanRec.from_dict({
            "rec_date": "2026-08-03", "skill_id": "alang", "code": "600519", "side": "buy",
            "target_price": 10.00, "position_cheng": 3, "source": "tool",
            "stop_loss": 10.50,  # ≥ target_price
        })
    with pytest.raises(ValueError):
        PlanRec.from_dict({
            "rec_date": "2026-08-03", "skill_id": "alang", "code": "600519", "side": "buy",
            "target_price": 10.00, "position_cheng": 3, "source": "tool",
            "take_profit": 9.00,  # ≤ target_price
        })
    with pytest.raises(ValueError):
        PlanRec.from_dict({
            "rec_date": "2026-08-03", "skill_id": "alang", "code": "600519", "side": "buy",
            "target_price": 10.00, "position_cheng": 3, "source": "tool",
            "max_hold_days": 0,
        })


@pytest.mark.skipif(PYTESTMARK_SKIP, reason="plan/plan_extractor 未实现")
def test_plan_extractor_parses_conditional_fields(monkeypatch, data_dir):
    """买入行解析止损/止盈/持有天数；缺省保持 None；卖出行不新增字段。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    from stock_review_crew.accounts import plan as plan_store
    plan_store.reset("2026-08-03")
    tool_log = extract_plans(
        "买入 600519：买入价10.00元，仓位3成，止损9.50元，止盈11.00元，持有13天", "2026-08-03", "alang", "阿郎", set())
    plan_store.finalize("2026-08-03")
    plans = json.loads((data_dir / "reviews" / "2026-08-03" / "plan.json")
                       .read_text("utf-8"))["plans"]
    assert len(plans) == 1
    d = plans[0]
    assert d["stop_loss"] == pytest.approx(9.50)
    assert d["take_profit"] == pytest.approx(11.00)
    assert d["max_hold_days"] == 13
    # 缺省 → None
    plan_store.reset("2026-08-04")
    extract_plans("买入 002384：买入价10.00元，仓位2成", "2026-08-04", "alang", "阿郎", set())
    plan_store.finalize("2026-08-04")
    d2 = json.loads((data_dir / "reviews" / "2026-08-04" / "plan.json")
                    .read_text("utf-8"))["plans"][0]
    assert d2["stop_loss"] is None and d2["take_profit"] is None and d2["max_hold_days"] is None


@pytest.mark.skipif(PYTESTMARK_SKIP, reason="plan/plan_extractor 未实现")
def test_plan_validation_trailing_drawdown():
    """trailing_drawdown 可选：0<x<1；None → JSON null；越界抛 ValueError。"""
    ok = PlanRec.from_dict({
        "rec_date": "2026-08-03", "skill_id": "alang", "code": "600519", "side": "buy",
        "target_price": 10.00, "position_cheng": 3, "qty": None, "source": "tool",
        "stop_loss": 9.50, "take_profit": 11.00, "max_hold_days": 13,
        "trailing_drawdown": 0.05,
    })
    assert ok.trailing_drawdown == pytest.approx(0.05)
    assert ok.to_dict()["trailing_drawdown"] == pytest.approx(0.05)
    for bad in (0.0, 1.0, -0.1, 1.5, 5):
        with pytest.raises(ValueError):
            PlanRec.from_dict({
                "rec_date": "2026-08-03", "skill_id": "alang", "code": "600519",
                "side": "buy", "target_price": 10.00, "position_cheng": 3,
                "source": "tool", "trailing_drawdown": bad,
            })
    r = PlanRec.from_dict({
        "rec_date": "2026-08-03", "skill_id": "alang", "code": "600519", "side": "buy",
        "target_price": 10.00, "position_cheng": 3, "source": "tool",
    })
    assert r.trailing_drawdown is None
    assert r.to_dict()["trailing_drawdown"] is None


@pytest.mark.skipif(PYTESTMARK_SKIP, reason="plan/plan_extractor 未实现")
def test_plan_extractor_parses_trailing_drawdown(monkeypatch, data_dir):
    """买入行解析 回落N% / 回落N个百分点 → trailing_drawdown=N/100；未写 → None。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    from stock_review_crew.accounts import plan as plan_store
    plan_store.reset("2026-08-03")
    extract_plans(
        "买入 600519：买入价10.00元，仓位3成，止损9.50元，止盈11.00元，回落5%，持有5天",
        "2026-08-03", "alang", "阿郎", set())
    plan_store.finalize("2026-08-03")
    plans = json.loads((data_dir / "reviews" / "2026-08-03" / "plan.json")
                       .read_text("utf-8"))["plans"]
    assert len(plans) == 1
    assert plans[0]["trailing_drawdown"] == pytest.approx(0.05)
    # 回落N个百分点 写法
    plan_store.reset("2026-08-04")
    extract_plans("买入 002384：买入价10.00元，仓位2成，回落3个百分点",
                  "2026-08-04", "alang", "阿郎", set())
    plan_store.finalize("2026-08-04")
    d2 = json.loads((data_dir / "reviews" / "2026-08-04" / "plan.json")
                    .read_text("utf-8"))["plans"][0]
    assert d2["trailing_drawdown"] == pytest.approx(0.03)
    # 未写回落 → None（固定止盈）
    plan_store.reset("2026-08-05")
    extract_plans("买入 600000：买入价10.00元，仓位2成", "2026-08-05", "alang", "阿郎", set())
    plan_store.finalize("2026-08-05")
    d3 = json.loads((data_dir / "reviews" / "2026-08-05" / "plan.json")
                    .read_text("utf-8"))["plans"][0]
    assert d3["trailing_drawdown"] is None


# ---------------------------------------------------------------------------
# ⑩ 集成：settle_plans / run_replay 条件单接入后成交可见、确定性一致
# ---------------------------------------------------------------------------

def _write_plan(data_dir, d, plans):
    p = data_dir / "reviews" / d / "plan.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"date": d, "mode": "close", "generated_at": "2026-08-11T00:00:00", "plans": plans},
        ensure_ascii=False), "utf-8")


def _plan(rec_date, code="600519", side="buy", target=10.00, position=3, **kw):
    return {"rec_date": rec_date, "skill_id": "alang", "skill_name": "阿郎",
            "code": code, "name": "贵州茅台", "side": side, "target_price": target,
            "position_cheng": position, "qty": None, "source": "tool", **kw}


def _quote(close=10.5, pre_close=10.0, high=10.8, low=10.2, open_=10.3, name="贵州茅台"):
    return {"name": name, "pre_close": pre_close, "open": open_,
            "high": high, "low": low, "close": close}


def _write_quotes(data_dir, d, quotes_map):
    p = data_dir / "quotes_cache" / f"{d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"date": d, "fetched_at": "2026-08-11T00:00:00", "source": "test",
         "quotes": quotes_map, "degraded": []}, ensure_ascii=False), "utf-8")


def test_settle_plans_conditional_stop_loss_sell(monkeypatch, data_dir):
    """settle_plans：买入当日正常成交（T+1 冻结）→ 次日 low 触止损 → 条件单卖出入账，快照反映清仓。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-08-03", [
        _plan("2026-08-03", target=10.00, stop_loss=9.50, take_profit=11.00, max_hold_days=13)])
    _write_plan(data_dir, "2026-08-04", [])
    # 撮合日（08-04）：正常行情，target=10.00 触及成交（高 10.20 ≥ 10.00）
    _write_quotes(data_dir, "2026-08-04", {"600519": _quote(close=10.10, pre_close=10.00, high=10.20, low=9.95, open_=10.05)})
    # 检查日（08-05）：盘中最低 9.30 < 止损 9.50，开盘 9.60 ≥ 止损 → 成交价 9.50
    _write_quotes(data_dir, "2026-08-05", {"600519": _quote(close=9.50, pre_close=10.00, high=9.60, low=9.30, open_=9.60)})
    r1 = replay.settle_plans("2026-08-03", "2026-08-04")
    assert r1["filled"] == 1 and r1["rejected"] == 0
    r2 = replay.settle_plans("2026-08-04", "2026-08-05")
    assert "conditional_trades" in r2, "条件单卖出应记录"
    assert any("600519" in n and "条件单卖出" in n for n in r2["conditional_trades"])
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    cond = [t for t in trades if t.get("side") == "sell" and t.get("condition")]
    assert len(cond) == 1
    assert cond[0]["price"] == pytest.approx(9.50)
    acc = json.loads((data_dir / "accounts" / "alang" / "account.json").read_text("utf-8"))
    assert acc["positions"] == {}, "条件单止损后应清仓"
    snaps = json.loads((data_dir / "accounts" / "alang" / "snapshots.json").read_text("utf-8"))
    assert snaps[-1]["date"] == "2026-08-05"
    assert snaps[-1]["position_mv"] == pytest.approx(0.0), "清仓后快照无持仓市值"


def test_run_replay_conditional_timeout_after_13_days(monkeypatch, data_dir):
    """run_replay：13 个结算日持仓未触发止损止盈 → 第 13 个结算日收盘价卖出；
    确定性：两次重放逐字节一致，且条件卖出记录存在。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    # D0:买入计划；D1..D13: 无计划（空 plans 也构成结算日），行情都不触止损止盈
    _write_plan(data_dir, "2026-07-01", [_plan("2026-07-01", target=10.00)])
    dates = ["2026-07-%02d" % d for d in range(2, 15)]  # 02..14 → 13 个结算日（含买入日后的 D1..D13）
    for dt in dates:
        _write_plan(data_dir, dt, [])
    # 买入撮合日 = D0 之后最近结算日 = 07-02；07-03..07-14 为后续持有结算日
    for dt in ["2026-07-02"] + dates:
        # 不触止损(9.5)/止盈(11)：low>9.5、high<11，且 low≤买入价10.00 保证撮合日触及
        _write_quotes(data_dir, dt, {"600519": _quote(close=10.30, pre_close=10.20, high=10.40, low=9.98, open_=10.30)})
    import stock_review_crew.accounts.engine as engine_mod
    out = data_dir / "out"
    rc_first = replay.run_replay(report_dir=str(out))
    assert rc_first == 0

    def snapshot_bytes():
        files = {}
        for p in (data_dir / "accounts" / "alang").glob("*.json"):
            files[p.name] = p.read_bytes()
        return files

    first = snapshot_bytes()
    rc_second = replay.run_replay(report_dir=str(out))
    assert rc_second == 0
    second = snapshot_bytes()
    assert set(first) == set(second)
    for k in first:
        assert first[k] == second[k], f"{k} 条件单重放两次不一致（确定性）"

    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    cond = [t for t in trades if t.get("side") == "sell" and t.get("condition")]
    assert len(cond) == 1, f"应恰好一次条件单超时卖出，实际 {len(cond)}"
    assert cond[0]["condition"] == "timeout"
    assert cond[0]["qty"] == 2900


def test_settle_plans_trailing_take_profit(monkeypatch, data_dir):
    """移动止盈经 settle_plans 全链路：plan 带 trailing_drawdown → 激活后峰值新高回落触发卖出。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    # D0 买入计划：target 10.00，trailing_drawdown=0.05（take_profit 默认 11.00 激活阈值）
    _write_plan(data_dir, "2026-08-03",
                [_plan("2026-08-03", target=10.00, trailing_drawdown=0.05)])
    _write_plan(data_dir, "2026-08-04", [])
    _write_plan(data_dir, "2026-08-05", [])
    # 撮合日（08-04）：单价触及买入
    _write_quotes(data_dir, "2026-08-04",
                  {"600519": _quote(close=10.10, pre_close=10.00, high=10.20, low=9.95, open_=10.05)})
    # 激活日（08-05）：high 11.70（→peak 11.70, stop 11.20），low 11.30 未触发
    _write_quotes(data_dir, "2026-08-05",
                  {"600519": _quote(close=11.50, pre_close=10.00, high=11.70, low=11.30, open_=11.20)})
    # 回落触发日（08-06）：high 11.40（peak 仍 11.70），low 11.10 < stop 11.20 → 卖
    _write_quotes(data_dir, "2026-08-06",
                  {"600519": _quote(close=11.18, pre_close=11.50, high=11.40, low=11.10, open_=11.30)})
    assert replay.settle_plans("2026-08-03", "2026-08-04")["filled"] == 1
    r2 = replay.settle_plans("2026-08-04", "2026-08-05")
    assert "conditional_trades" not in r2 or not r2["conditional_trades"], "激活日未触发不卖"
    # 撮合日也推进无计划日的状态，因此 08-05 结算会把 08-04 撮合的持仓跑条件单（激活日）
    acc = json.loads((data_dir / "accounts" / "alang" / "account.json").read_text("utf-8"))
    assert "600519" in acc["positions"], "激活日未回落 → 持仓未卖"
    r3 = replay.settle_plans("2026-08-05", "2026-08-06")
    assert any("600519" in n and "条件单卖出" in n for n in r3["conditional_trades"])
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    cond = [t for t in trades if t.get("side") == "sell" and t.get("condition")]
    assert len(cond) == 1
    assert cond[0]["condition"] == "trailing_take_profit"
    assert cond[0]["price"] == pytest.approx(11.20), "触发日 open 11.30 ≥ stop → 按 stop 成交"
    acc2 = json.loads((data_dir / "accounts" / "alang" / "account.json").read_text("utf-8"))
    assert acc2["positions"] == {}, "移动止盈触发后应清仓"
