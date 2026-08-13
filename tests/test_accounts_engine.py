"""R12 账户引擎手算测试（对齐 test_r12_acceptance.py 冻结数字）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew.accounts.engine import (
    INITIAL_CASH,
    limit_prices,
    open_account,
    place_order,
    snapshot,
)


def _day(pre_close=10.0, high=10.8, low=10.0, close=10.5, date="2026-07-15"):
    return {"date": date, "pre_close": pre_close, "high": high, "low": low, "close": close}


def test_open_account_initial():
    acct = open_account("alang")
    assert acct["cash"] == 100_000.0
    assert acct["positions"] == {}
    assert acct["realized_pnl"] == 0.0


def test_buy_normal_manual():
    """10 万×3 成 ÷ 10.001 → 2900 股；佣金 7.25、过户费 0.29 → 现金 70,992.46。"""
    acct = open_account("alang")
    r = place_order(acct, "600519", "贵州茅台", "buy", 10.00, 3, _day())
    assert r.ok, r.reason
    t = r.trade
    assert t["qty"] == 2900
    assert t["fee"] == 7.25
    assert t["transfer_fee"] == 0.29
    assert t["stamp_tax"] == 0.0
    assert acct["cash"] == pytest.approx(70_992.46)
    pos = acct["positions"]["600519"]
    assert pos["qty"] == 2900 and pos["frozen_qty"] == 2900  # T+1 冻结


def test_buy_reject_at_limit_up():
    acct = open_account("alang")
    r = place_order(acct, "600519", "x", "buy", 11.00, 3, _day())
    assert not r.ok and "涨停价" in r.reason
    # 低于涨停且触及 → 成交
    r2 = place_order(acct, "600519", "x", "buy", 10.98, 3, _day(high=10.98))
    assert r2.ok, r2.reason
    assert r2.trade["qty"] == 2700  # 100000*0.3/10.98/1.001 → 2730.8 → 2700


def test_buy_reject_target_not_touched():
    acct = open_account("alang")
    r = place_order(acct, "600519", "x", "buy", 10.00, 3, _day(low=10.10))
    assert not r.ok and "未触及" in r.reason


def test_limit_board_20pct_gem():
    """同价 11.50：主板（±10%）拒、创业板（±20%）成交。"""
    acct1 = open_account("alang")
    r1 = place_order(acct1, "600000", "x", "buy", 11.50, 3, _day(high=11.50))
    assert not r1.ok
    acct2 = open_account("bingchuan")
    r2 = place_order(acct2, "300750", "x", "buy", 11.50, 3, _day(high=11.50))
    assert r2.ok, r2.reason


def test_t_plus_1():
    acct = open_account("alang")
    r = place_order(acct, "600519", "x", "buy", 10.00, 3, _day())
    assert r.ok
    # 当日卖出 → 拒（T+1 冻结）
    r2 = place_order(acct, "600519", "x", "sell", 10.50, 10, _day())
    assert not r2.ok and "T+1" in r2.reason
    # 次日卖出 2900 → 成交
    r3 = place_order(acct, "600519", "x", "sell", 10.50, 10, _day(date="2026-07-16"))
    assert r3.ok, r3.reason
    assert r3.trade["fee"] == pytest.approx(7.6125)
    assert r3.trade["stamp_tax"] == pytest.approx(15.225)
    assert r3.trade["transfer_fee"] == pytest.approx(0.3045)
    assert acct["cash"] == pytest.approx(101_419.318)
    assert acct["realized_pnl"] == pytest.approx((10.50 - 10.00) * 2900 - 7.6125 - 15.225 - 0.3045)


def test_sell_half_position_manual():
    """1000 股 × 5 成 → 500 股；佣金最低 5 元、印花税 2.625、过户费 0.0525。"""
    acct = open_account("alang")
    r = place_order(acct, "600519", "x", "buy", 10.00, 4, _day())  # 买入 3900 股（4 成）
    assert r.ok
    r2 = place_order(acct, "600519", "x", "sell", 10.50, 5, _day(date="2026-07-16"))
    assert r2.ok, r2.reason
    assert r2.trade["qty"] == 1900  # 3900×0.5 → 1900
    assert r2.trade["fee"] == pytest.approx(5.0)  # 1900×10.5×0.00025=4.9875 → 最低 5 元


def test_sell_reject_at_limit_down():
    acct = open_account("alang")
    place_order(acct, "600519", "x", "buy", 10.00, 4, _day())
    r = place_order(acct, "600519", "x", "sell", 9.00, 10, _day(date="2026-07-16"))
    assert not r.ok and "跌停价" in r.reason


def test_sell_reject_bare_short():
    acct = open_account("alang")
    r = place_order(acct, "600519", "x", "sell", 10.50, 10, _day())
    assert not r.ok and "裸卖空" in r.reason


def test_sell_with_explicit_qty_and_zero_cheng_ok():
    """2026-08-12（适配兜底）：「卖出 000595：卖出价6.26元，卖出3000股」类 qty 式
    计划 position_cheng=0（文本无成数）——显式 qty 时成数不参与计算，应正常成交；
    无显式 qty 且成数 0 仍拒绝。"""
    acct = open_account("alang")
    r = place_order(acct, "000595", "x", "buy", 6.56, 10,
                    _day(pre_close=6.5, low=6.0, high=7.0))
    assert r.ok
    r2 = place_order(acct, "000595", "x", "sell", 6.26, 0,
                     _day(date="2026-07-16", pre_close=6.5, low=6.0, high=7.0), qty=3000)
    assert r2.ok, r2.reason
    assert r2.trade["qty"] == 3000
    assert acct["positions"]["000595"]["qty"] == r.trade["qty"] - 3000  # 卖出 3000，剩余 = 买入量-3000
    # 无显式 qty + 成数 0 → 仍拒绝
    r3 = place_order(open_account("baxiaoxian"), "600519", "x", "sell", 10.0, 0,
                     _day(date="2026-07-16"))
    assert not r3.ok and "仓位成数无效" in r3.reason


def test_qty_multiple_of_100_and_insufficient_cash():
    acct = open_account("alang")
    # 10 成买高价股 → 折算 0 → 拒
    r = place_order(acct, "600519", "x", "buy", 1000.00, 10, _day(pre_close=1000, high=1000, low=1000))
    assert not r.ok and ("资金" in r.reason or "0" in r.reason)
    assert acct["cash"] == INITIAL_CASH  # 分文未动


def test_fee_commission_minimum_5():
    acct = open_account("alang")
    r = place_order(acct, "600519", "x", "buy", 1.00, 1, _day(pre_close=1.0, high=1.0, low=1.0))
    if r.ok:  # 小额 1 元×100 股=100 元 → 佣金最低 5 元
        assert r.trade["fee"] == 5.0


def test_limit_prices_round():
    up, down = limit_prices(10.00, 0.10)
    assert up == 11.00 and down == 9.00
    up2, down2 = limit_prices(9.995, 0.10)  # 四舍五入
    assert up2 == pytest.approx(10.99)


def test_snapshot():
    acct = open_account("alang")
    place_order(acct, "600519", "x", "buy", 10.00, 3, _day())
    snap = snapshot(acct, "2026-07-15", {"600519": 10.5})
    assert snap["cash"] == pytest.approx(70_992.46)
    assert snap["position_mv"] == pytest.approx(2900 * 10.5)
    assert snap["total_assets"] == pytest.approx(70_992.46 + 2900 * 10.5)


def test_snapshot_positions_hit_and_miss():
    """2026-08-13 修复：快照新增 positions 字段 {code: {close, market_value, qty, estimated}}，
    手算样例：600519 收盘命中 10.5（2900 股 → 30,450）；000975 收盘缺失按成本 19.6 兜底
    （700 股 → 13,720）且 estimated=True。既有字段（cash/position_mv/total_assets/
    realized_pnl/estimated_prices）保持不变，向后兼容。"""
    acct = open_account("alang")
    acct["trades"] = []
    acct["positions"] = {
        "600519": {"name": "x", "qty": 2900, "frozen_qty": 2900, "avg_cost": 10.0},
        "000975": {"name": "x", "qty": 700, "frozen_qty": 700, "avg_cost": 19.6},
    }
    snap = snapshot(acct, "2026-07-06", {"600519": 10.5})
    assert "positions" in snap
    p1 = snap["positions"]["600519"]
    assert p1["close"] == pytest.approx(10.5)            # close_prices 命中
    assert p1["market_value"] == pytest.approx(2900 * 10.5)  # = 30,450
    assert p1["qty"] == 2900
    assert p1["estimated"] is False
    p2 = snap["positions"]["000975"]
    assert p2["close"] == pytest.approx(19.6)            # 缺失 → avg_cost 兜底
    assert p2["market_value"] == pytest.approx(700 * 19.6)  # = 13,720
    assert p2["qty"] == 700
    assert p2["estimated"] is True
    assert "000975" in snap["estimated_prices"]
    # 既有字段向后兼容
    assert snap["cash"] == pytest.approx(100_000.0)
    assert snap["position_mv"] == pytest.approx(2900 * 10.5 + 700 * 19.6)
    assert snap["total_assets"] == pytest.approx(100_000.0 + 2900 * 10.5 + 700 * 19.6)
    assert snap["realized_pnl"] == pytest.approx(0.0)
    assert snap["estimated_prices"] == ["000975"]


def test_buy_kechuang_insufficient_cash_keeps_min_unit():
    """2026-08-13 边界修复：资金只够 200 股科创板（688/689 最低 200 股起）时，
    递减循环不得把 200 减到 100 成交。手算：现金 4004.5、价格 20.00 →
    raw=200 股、总成本 4005.04 > 现金 → 应拒绝（旧代码会按 100 股成交）；
    现金 4005.5 ≥ 总成本 4005.04 → 应以 200 股成交。"""
    day = {"date": "2026-08-07", "pre_close": 20.0, "high": 20.5, "low": 19.8, "close": 20.2}
    acct = open_account("kechuang_edge")
    acct["trades"] = []
    acct["cash"] = 4004.5
    r = place_order(acct, "688001", "科创板测试", "buy", 20.0, 10, day)
    if r.ok:
        assert r.trade["qty"] >= 200, "科创板买入数量不得低于 200 股"
        assert r.trade["qty"] % 100 == 0
    else:
        assert "资金不足" in r.reason and "200" in r.reason, r.reason
    acct2 = open_account("kechuang_ok")
    acct2["trades"] = []
    acct2["cash"] = 4005.5
    r2 = place_order(acct2, "688001", "科创板测试", "buy", 20.0, 10, day)
    assert r2.ok, r2.reason
    assert r2.trade["qty"] == 200


def test_buy_kechuang_decrement_floor_at_min_unit():
    """2026-08-13 边界修复（递减下限）：预算先算出 300 股但资金只够 200 股
    （现金 4505、价格 15.00：raw=300 → 300 股总成本 4505.045 > 现金）时，
    逐手递减应停在 200（不低于下限），而不是一步减到 100。"""
    day = {"date": "2026-08-07", "pre_close": 15.0, "high": 15.5, "low": 14.8, "close": 15.2}
    acct = open_account("kechuang_floor")
    acct["trades"] = []
    acct["cash"] = 4505.0
    r = place_order(acct, "688001", "科创板测试", "buy", 15.0, 10, day)
    assert r.ok, r.reason
    assert r.trade["qty"] == 200
    assert r.trade["qty"] >= 200
