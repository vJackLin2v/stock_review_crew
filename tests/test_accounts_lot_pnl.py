# -*- coding: utf-8 -*-
"""账户引擎：A 股最小买入单位（科创板 200 股起）与持仓盈亏计算测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew.accounts import engine as acct_engine


@pytest.mark.parametrize("code,expected", [
    ("688001", 200),   # 科创板最低 200 股（2 手）
    ("689009", 200),
    ("600000", 100),   # 主板 100 股
    ("000001", 100),   # 深主板 100 股
    ("300001", 100),   # 创业板 100 股
])
def test_min_buy_unit(code, expected):
    assert acct_engine.min_buy_unit(code) == expected


@pytest.mark.parametrize("qty,code,expected", [
    (150, "688001", 0),      # 科创板不足 200 股 → 0（拒绝）
    (250, "688001", 200),    # 科创板 250 → 200（2 手）
    (350, "688001", 300),    # 科创板 350 → 300（3 手）
    (550, "688001", 500),    # 科创板 550 → 500
    (150, "600000", 100),    # 主板 150 → 100
    (250, "600000", 200),    # 主板 250 → 200
    (99, "600000", 0),       # 主板不足 100 → 0
])
def test_round_lot_buy(qty, code, expected):
    assert acct_engine.round_lot(qty, code, side="buy") == expected


@pytest.mark.parametrize("qty,code,expected", [
    (350, "688001", 300),   # 卖出按 100 递增
    (50, "688001", 50),     # 零股允许一次性卖出
    (250, "600000", 200),
    (30, "600000", 30),     # 零股卖出
])
def test_round_lot_sell(qty, code, expected):
    assert acct_engine.round_lot(qty, code, side="sell") == expected


def _mk_acct():
    acct = acct_engine.open_account("test_lot")
    acct["positions"] = {
        "688001": {"name": "科创测试", "qty": 200, "frozen_qty": 0, "avg_cost": 10.0},
        "600000": {"name": "主板测试", "qty": 300, "frozen_qty": 0, "avg_cost": 5.0},
    }
    acct["trades"] = []
    return acct


def test_snapshot_with_close_prices():
    acct = _mk_acct()
    snap = acct_engine.snapshot(acct, "2026-08-07", {"688001": 12.0, "600000": 4.0})
    # 200*12 + 300*4 = 2400 + 1200 = 3600
    assert snap["position_mv"] == 3600.0
    assert snap["total_assets"] == round(100000.0 + 3600.0, 6)


def test_snapshot_missing_price_uses_cost():
    acct = _mk_acct()
    snap = acct_engine.snapshot(acct, "2026-08-07", {"600000": 4.0})
    # 688001 无价 → 按成本 10.0 估值；estimate 标注
    assert snap["position_mv"] == round(200 * 10.0 + 300 * 4.0, 6)
    assert "688001" in snap["estimated_prices"]


def test_buy_kechuang_min_200():
    acct = acct_engine.open_account("test_kechuang")
    acct["trades"] = []
    day = {"date": "2026-08-07", "pre_close": 10.0, "high": 10.5, "low": 9.8, "close": 10.2}
    # 1 成仓位（1 万）买 10 元科创板 → 1000 股 → 取 100 股整数倍 = 1000（≥200）
    r = acct_engine.place_order(acct, "688001", "科创", "buy", 10.0, 1, day)
    assert r.ok, r.reason
    assert r.trade["qty"] >= 200
    assert r.trade["qty"] % 100 == 0


def test_sell_more_than_position_rejected():
    acct = _mk_acct()
    day = {"date": "2026-08-07", "pre_close": 12.0, "high": 12.5, "low": 11.8, "close": 12.2}
    # 5 成卖出 600000（300 股）→ 150 股 → 100 股，可用 300 股足够
    r = acct_engine.place_order(acct, "600000", "主板测试", "sell", 12.0, 5, day)
    assert r.ok, r.reason
    assert r.trade["qty"] == 100
    # 8 成卖出 600000（剩余 200 股）→ 160 → 100
    r2 = acct_engine.place_order(acct, "600000", "主板测试", "sell", 12.0, 8, day)
    assert r2.ok, r2.reason
    assert r2.trade["qty"] == 100
    # 剩余 100 股，5 成=50 → 零股可卖 50
    r3 = acct_engine.place_order(acct, "600000", "主板测试", "sell", 12.0, 5, day)
    assert r3.ok, r3.reason
    assert r3.trade["qty"] == 50


def test_sell_no_position_rejected():
    acct = acct_engine.open_account("test_naked")
    acct["trades"] = []
    day = {"date": "2026-08-07", "pre_close": 12.0, "high": 12.5, "low": 11.8, "close": 12.2}
    r = acct_engine.place_order(acct, "600000", "主板", "sell", 12.0, 3, day)
    assert not r.ok
    assert "无持仓" in r.reason


def test_buy_full_position_never_exceeds_cash():
    """10 成满仓买入：金额+费用必须 ≤ 现金（逐手递减），绝不超资产。"""
    acct = acct_engine.open_account("test_full")
    acct["trades"] = []
    day = {"date": "2026-08-07", "pre_close": 10.0, "high": 10.5, "low": 9.8, "close": 10.2}
    r = acct_engine.place_order(acct, "600000", "主板", "buy", 10.0, 10, day)
    assert r.ok, r.reason
    qty = r.trade["qty"]
    total = r.trade["amount"] + r.trade["fee"] + r.trade["transfer_fee"]
    assert total <= 100000.0 + 1e-6, f"总花费 {total} 超过现金"
    assert acct["cash"] >= 0


def test_buy_qty_high_price_falls_back_within_cash():
    """高价股 10 成：递减后仍在现金内。"""
    acct = acct_engine.open_account("test_high")
    acct["trades"] = []
    day = {"date": "2026-08-07", "pre_close": 500.0, "high": 510.0, "low": 495.0, "close": 505.0}
    r = acct_engine.place_order(acct, "600000", "主板", "buy", 500.0, 10, day)
    assert r.ok, r.reason
    total = r.trade["amount"] + r.trade["fee"] + r.trade["transfer_fee"]
    assert total <= 100000.0 + 1e-6
    assert r.trade["qty"] > 0


def test_sell_cheng_never_exceeds_available():
    """成数卖出：折算股数不得超过可卖持仓。"""
    acct = acct_engine.open_account("test_sell_edge")
    acct["trades"] = []
    acct["positions"] = {"600000": {"name": "主板", "qty": 150, "frozen_qty": 0, "avg_cost": 10.0}}
    day = {"date": "2026-08-07", "pre_close": 12.0, "high": 12.5, "low": 11.8, "close": 12.2}
    # 10 成卖 150 股 → 折算 150 → 取整 100，不超过 available
    r = acct_engine.place_order(acct, "600000", "主板", "sell", 12.0, 10, day)
    assert r.ok, r.reason
    assert r.trade["qty"] <= 150
