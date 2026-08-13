"""历史持仓（已平仓）手算测试：engine.closed_trades 纯函数。

字段契约（与后端 closed_positions 对齐）：
{code, name, buy_qty, sell_qty, avg_buy_price, avg_sell_price, realized_pnl,
 first_buy_date, last_sell_date, holding_days}
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew.accounts.engine import closed_trades


def _t(date, code, side, qty, amount, status="成交", realized=None, name="宝塔实业"):
    t = {
        "date": date, "code": code, "name": name, "side": side,
        "qty": qty, "amount": amount, "price": round(amount / qty, 6),
        "status": status,
    }
    if realized is not None:
        t["realized"] = realized
    return t


def test_full_close_manual():
    """买 1000 股 @6.15（2026-07-06）→ 卖 1000 股 @6.26（2026-07-08）。
    realized 手算（引擎费用口径）：卖出金额 6260，
    佣金 max(6260×0.00025, 5)=5.00、印花税 6260×0.0005=3.13、过户费 6260×0.00001=0.0626
    → realized = (6.26−6.15)×1000 − 5 − 3.13 − 0.0626 = 110 − 8.1926 = 101.8074。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026-07-06", "000595", "buy", 1000, 6150.0),
            _t("2026-07-08", "000595", "sell", 1000, 6260.0, realized=101.8074),
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
    assert row["realized_pnl"] == pytest.approx(101.8074)
    assert row["first_buy_date"] == "2026-07-06"
    assert row["last_sell_date"] == "2026-07-08"
    assert row["holding_days"] == 2  # 自然日：07-08 − 07-06


def test_partial_close_not_counted():
    """买 1000 卖 400 仍持 600 → 不算历史持仓（buy_qty != sell_qty）。"""
    acct = {
        "positions": {"000595": {"name": "宝塔实业", "qty": 600, "frozen_qty": 0, "avg_cost": 6.15}},
        "trades": [
            _t("2026-07-06", "000595", "buy", 1000, 6150.0),
            _t("2026-07-08", "000595", "sell", 400, 2504.0, realized=30.0),
        ],
    }
    assert closed_trades(acct) == []


def test_partial_close_position_removed_still_not_counted():
    """买 1000 卖 400 且 positions 已无该 code → buy_qty != sell_qty，仍不算。"""
    acct = {
        "positions": {},
        "trades": [
            _t("2026-07-06", "000595", "buy", 1000, 6150.0),
            _t("2026-07-08", "000595", "sell", 400, 2504.0, realized=30.0),
        ],
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
    """status=拒绝 的记录不计入：拒绝的卖出不构成平仓；拒绝的买入也不累加买入量。"""
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
    """sell 记录缺 realized → 按 0 累加，不报错。"""
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


def test_multiple_closed_sorted_by_last_sell_desc():
    """多只已平仓按末卖日倒序（最近卖出在前）。"""
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
    assert closed[0]["first_buy_date"] == "2026/07/06"
    assert closed[0]["last_sell_date"] == "2026-07-08"


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


def test_position_qty_zero_still_closed():
    """positions 中该 code qty == 0 → 视为已平仓（与 positions 无该 code 等价）。"""
    acct = {
        "positions": {"000595": {"name": "宝塔实业", "qty": 0, "frozen_qty": 0, "avg_cost": 6.15}},
        "trades": [
            _t("2026-07-06", "000595", "buy", 1000, 6150.0),
            _t("2026-07-08", "000595", "sell", 1000, 6260.0, realized=101.8074),
        ],
    }
    closed = closed_trades(acct)
    assert len(closed) == 1
    assert closed[0]["code"] == "000595"
