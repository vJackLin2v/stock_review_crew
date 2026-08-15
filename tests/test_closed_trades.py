"""历史持仓（已平仓）· 按「清仓周期」手算测试：engine.closed_trades 纯函数。

字段契约（requirements-trades-fix §三.3，与后端 closed_positions 对齐）：
{code, name, buy_date(=周期首买日), sell_date(=周期末卖日), buy_qty, sell_qty,
 avg_buy_price, avg_sell_price, buy_amount, realized_pnl,
 holding_days(=末卖日−首买日+1), buy_reason, sell_reason}

覆盖：不按 code 聚合（同一股票多次买卖 = 多笔独立闭环）、部分卖出 + 加仓属同一周期
（清仓才闭合）、持仓天数含首末两天（+1）、reason 字段透传、旧数据无字段容错、
排序与异常容错。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew.accounts.engine import closed_trades


def _t(date, code, side, qty, amount, status="成交", realized=None, name="宝塔实业",
       buy_reason=None, sell_reason=None):
    t = {
        "date": date, "code": code, "name": name, "side": side,
        "qty": qty, "amount": amount, "price": round(amount / qty, 6),
        "status": status,
        "buy_reason": buy_reason,
        "sell_reason": sell_reason,
    }
    if realized is not None:
        t["realized"] = realized
    return t


def test_full_close_manual():
    """买 1000 股 @6.15（2026-07-06）→ 卖 1000 股 @6.26（2026-07-08）。
    realized 手算（引擎费用口径）：卖出金额 6260，
    佣金 max(6260×0.00025, 5)=5.00、印花税 6260×0.0005=3.13、过户费 6260×0.00001=0.0626
    → realized = (6.26−6.15)×1000 − 5 − 3.13 − 0.0626 = 110 − 8.1926 = 101.8074。
    持仓天数含买入当天：07-08 − 07-06 = 2 + 1 = 3。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026-07-06", "000595", "buy", 1000, 6150.0, buy_reason="低位企稳"),
            _t("2026-07-08", "000595", "sell", 1000, 6260.0, realized=101.8074, sell_reason="止盈"),
        ],
    }
    closed = closed_trades(acct)
    assert len(closed) == 1
    row = closed[0]
    assert row["code"] == "000595"
    assert row["name"] == "宝塔实业"
    assert row["buy_qty"] == 1000
    assert row["sell_qty"] == 1000
    assert row["avg_buy_price"] == pytest.approx(6.15)
    assert row["avg_sell_price"] == pytest.approx(6.26)
    assert row["buy_amount"] == pytest.approx(6150.0)
    assert row["realized_pnl"] == pytest.approx(101.8074)
    assert row["buy_date"] == "2026-07-06"
    assert row["sell_date"] == "2026-07-08"
    assert row["holding_days"] == 3  # 含买入当天：07-08 − 07-06 + 1
    assert row["buy_reason"] == "低位企稳"
    assert row["sell_reason"] == "止盈"


def test_same_stock_two_cycles_not_aggregated():
    """同一股票 7 月买 2 天后卖、8 月再买 2 天后卖 → 两条独立闭环记录，不聚合。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026-07-01", "000595", "buy", 1000, 6150.0, buy_reason="原因A"),
            _t("2026-07-03", "000595", "sell", 1000, 6260.0, realized=101.0, sell_reason="止盈"),
            _t("2026-08-01", "000595", "buy", 1000, 8000.0, buy_reason="原因B"),
            _t("2026-08-03", "000595", "sell", 1000, 8500.0, realized=485.0, sell_reason="移动止盈"),
        ],
    }
    closed = closed_trades(acct)
    assert len(closed) == 2
    # 按 sell_date 倒序：8 月周期在前
    assert [r["buy_date"] for r in closed] == ["2026-08-01", "2026-07-01"]
    july, aug = closed[1], closed[0]
    assert july["holding_days"] == 3 and aug["holding_days"] == 3
    assert july["buy_reason"] == "原因A" and aug["buy_reason"] == "原因B"
    assert july["sell_reason"] == "止盈" and aug["sell_reason"] == "移动止盈"


def test_partial_close_same_cycle_single_record():
    """用户例子：7-1 买 500 → 7-5 卖 200（未清仓）→ 8-1 卖 300（清仓）
    = 同一周期的**一条**记录，持仓天数 = 7-1 到 8-1（32 天）。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026-07-01", "000595", "buy", 500, 3000.0, buy_reason="周期理由"),
            _t("2026-07-05", "000595", "sell", 200, 1300.0, realized=80.0, sell_reason="止损"),
            _t("2026-08-01", "000595", "sell", 300, 2100.0, realized=180.0, sell_reason="止盈"),
        ],
    }
    closed = closed_trades(acct)
    assert len(closed) == 1
    row = closed[0]
    assert row["buy_qty"] == 500 and row["sell_qty"] == 500
    assert row["buy_date"] == "2026-07-01"
    assert row["sell_date"] == "2026-08-01"
    assert row["holding_days"] == 32  # (8-1 − 7-1).days + 1 = 31 + 1
    # 均价 = 周期总量
    assert row["avg_buy_price"] == pytest.approx(3000.0 / 500)
    assert row["avg_sell_price"] == pytest.approx((1300.0 + 2100.0) / 500)
    assert row["buy_amount"] == pytest.approx(3000.0)
    assert row["realized_pnl"] == pytest.approx(80.0 + 180.0)
    assert row["buy_reason"] == "周期理由"
    assert row["sell_reason"] == "止盈"  # 末卖（清仓卖）的理由


def test_partial_sell_not_cleared_no_record():
    """买 1000 卖 600、剩余 400 仍持 → 未清仓，不产生历史记录。"""
    acct = {
        "positions": {"000595": {"name": "宝塔实业", "qty": 400, "frozen_qty": 0, "avg_cost": 6.15}},
        "trades": [
            _t("2026-07-06", "000595", "buy", 1000, 6150.0, buy_reason="原因"),
            _t("2026-07-08", "000595", "sell", 600, 3756.0, realized=60.0, sell_reason="止盈"),
        ],
    }
    assert closed_trades(acct) == []


def test_reentry_after_clear_starts_new_cycle():
    """清仓后再买入 = 新周期：加入仓、部分卖、再清仓 → 两段各自独立成记录。"""
    acct = {
        "positions": {},
        "trades": [
            # 周期1：买 500 → 卖 200 → 买 300（加仓）→ 卖 600（清仓）
            _t("2026-07-01", "000595", "buy", 500, 3000.0, buy_reason="周期一理由"),
            _t("2026-07-05", "000595", "sell", 200, 1300.0, realized=80.0, sell_reason="止损"),
            _t("2026-07-20", "000595", "buy", 300, 1800.0, buy_reason="加仓理由"),
            _t("2026-07-22", "000595", "sell", 600, 3900.0, realized=190.0, sell_reason="止盈"),
            # 周期2：再买 → 全卖
            _t("2026-08-01", "000595", "buy", 1000, 8000.0, buy_reason="周期二理由"),
            _t("2026-08-03", "000595", "sell", 1000, 8500.0, realized=486.0, sell_reason="移动止盈"),
        ],
    }
    closed = closed_trades(acct)
    assert len(closed) == 2, "清仓后再买入 = 新周期，应两条独立记录"
    # 按 sell_date 倒序：8 月周期在前
    cycle2, cycle1 = closed[0], closed[1]
    # 周期2
    assert cycle2["buy_date"] == "2026-08-01" and cycle2["sell_date"] == "2026-08-03"
    assert cycle2["buy_qty"] == 1000 and cycle2["sell_qty"] == 1000
    assert cycle2["holding_days"] == 3
    assert cycle2["buy_reason"] == "周期二理由" and cycle2["sell_reason"] == "移动止盈"
    # 周期1：首买 07-01，末卖（清仓）07-22；期间加仓属同一周期
    assert cycle1["buy_date"] == "2026-07-01" and cycle1["sell_date"] == "2026-07-22"
    assert cycle1["buy_qty"] == 800 and cycle1["sell_qty"] == 800
    assert cycle1["buy_amount"] == pytest.approx(3000.0 + 1800.0)
    assert cycle1["avg_buy_price"] == pytest.approx(4800.0 / 800)
    assert cycle1["realized_pnl"] == pytest.approx(80.0 + 190.0)
    assert cycle1["holding_days"] == 22  # (7-22 − 7-1).days + 1
    assert cycle1["buy_reason"] == "周期一理由"  # 周期首买理由
    assert cycle1["sell_reason"] == "止盈"  # 末卖（清仓卖）理由


def test_buy_only_not_closed_no_record_from_remaining():
    """只有买入（无任何卖出）→ 无历史记录（整只持仓未平仓）。"""
    acct = {
        "positions": {"000595": {"name": "宝塔实业", "qty": 1000, "frozen_qty": 1000, "avg_cost": 6.15}},
        "trades": [_t("2026-07-06", "000595", "buy", 1000, 6150.0)],
    }
    assert closed_trades(acct) == []


def test_still_held_buy_only_not_counted():
    """只买不卖（仍持有）→ 不算历史持仓。"""
    acct = {
        "positions": {"000595": {"name": "宝塔实业", "qty": 1000, "frozen_qty": 1000, "avg_cost": 6.15}},
        "trades": [_t("2026-07-06", "000595", "buy", 1000, 6150.0)],
    }
    assert closed_trades(acct) == []


def test_rejected_records_not_counted():
    """status=拒绝 的记录不计入：拒绝的卖出不构成平仓；拒绝的买入也不进买入队列。"""
    acct = {
        "positions": {"000595": {"name": "宝塔实业", "qty": 1000, "frozen_qty": 0, "avg_cost": 6.15}},
        "trades": [
            _t("2026-07-06", "000595", "buy", 1000, 6150.0),
            _t("2026-07-07", "000595", "buy", 500, 3100.0, status="拒绝"),
            _t("2026-07-08", "000595", "sell", 1000, 6260.0, status="拒绝", realized=101.8074),
        ],
    }
    assert closed_trades(acct) == []


def test_no_trades_returns_empty():
    acct = {"positions": {}, "trades": []}
    assert closed_trades(acct) == []


def test_missing_realized_treated_as_zero():
    """sell 记录缺 realized → 分摊按 0，不报错。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026-07-06", "000595", "buy", 1000, 6150.0),
            _t("2026-07-08", "000595", "sell", 1000, 6260.0),  # 无 realized
        ],
    }
    closed = closed_trades(acct)
    assert len(closed) == 1
    assert closed[0]["realized_pnl"] == 0.0


def test_multiple_closed_sorted_by_sell_date_desc():
    """多笔闭环按卖出日期倒序（最近卖出在前）。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026-07-01", "000001", "buy", 100, 1000.0, name="A"),
            _t("2026-07-05", "000001", "sell", 100, 1100.0, realized=95.0, name="A"),
            _t("2026-07-02", "000002", "buy", 100, 900.0, name="B"),
            _t("2026-07-09", "000002", "sell", 100, 880.0, realized=-30.0, name="B"),
            _t("2026-07-03", "000003", "buy", 100, 500.0, name="C"),
            _t("2026-07-04", "000003", "sell", 100, 520.0, realized=15.0, name="C"),
        ],
    }
    closed = closed_trades(acct)
    assert [r["code"] for r in closed] == ["000002", "000001", "000003"]


def test_bad_date_skips_holding_days_only():
    """日期解析失败 → 跳过 holding_days 字段（None），记录仍返回、不报错。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026/07/06", "000595", "buy", 1000, 6150.0),  # 非法日期格式
            _t("2026-07-08", "000595", "sell", 1000, 6260.0, realized=101.8074),
        ],
    }
    closed = closed_trades(acct)
    assert len(closed) == 1
    assert closed[0]["holding_days"] is None
    assert closed[0]["buy_date"] == "2026/07/06"
    assert closed[0]["sell_date"] == "2026-07-08"


def test_avg_price_rounds_to_6():
    """均价 round 6 位：buy_amount 6150.666666 / 1000 = 6.150666666… → 6.150667。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026-07-06", "000595", "buy", 1000, 6150.666666),
            _t("2026-07-08", "000595", "sell", 1000, 6260.0, realized=101.8074),
        ],
    }
    closed = closed_trades(acct)
    assert closed[0]["avg_buy_price"] == 6.150667
    assert closed[0]["buy_amount"] == pytest.approx(6150.666666)


def test_holding_days_today_buy_tomorrow_sell_is_2():
    """用户口径：今天买明天卖 = 2 天（买入当天算第 1 天）。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026-07-01", "000595", "buy", 100, 1000.0),
            _t("2026-07-02", "000595", "sell", 100, 1100.0, realized=95.0),
        ],
    }
    closed = closed_trades(acct)
    assert len(closed) == 1
    assert closed[0]["holding_days"] == 2
