"""交割单指标引擎 v2 测试（T4，自 synalysis_crew tests/test_metrics.py 移植）。

覆盖（口径冻结，与 synalysis 逐字一致）：
- 完整历史 / 中途开始（is_partial）两种场景；
- FIFO 闭环（费用入成本/扣净额）、红股只摊薄不记买入、期初持仓卖出只记
  unmatched_sell_amount、胜率/盈亏比/平均持仓周期按完整交易统计；
- 逐日 TWR（出入金不影响收益率、期初持仓合成 1:1、return_curve 月度补齐）；
- 账户级翻倍（R ≥ +100% 独立事件）/ 腰斩（v2.2 递进式 floor）与事件明细；
- 流水模式（无余额文件按发生金额累加）、M6 首行证转银、亏损榜升序；
- akshare 估值成功/失败按成本兜底（monkeypatch，不发网络请求）；
- 固定 JSON Schema（snake_case、严格 JSON 序列化）。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace

import pytest

_SRC = __import__("pathlib").Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stock_review_crew.trades.metrics import (  # noqa: E402
    MetricsResult,
    compute_metrics,
)
import stock_review_crew.trades.metrics as metrics_module  # noqa: E402
from stock_review_crew.trades.parser import OpType, TradeRecord  # noqa: E402


def T(
    code: str,
    name: str,
    op: str,
    qty: float,
    price: float,
    amount: float,
    balance: float,
    trade_date: object,
    fee: float = 0.0,
    stamp_tax: float = 0.0,
    commission: float = 0.0,
    transfer_fee: float = 0.0,
    cash_amount: object = None,
) -> TradeRecord:
    """构造一条交割记录（amount=成交金额，balance=交易后资金余额）。"""
    rec = TradeRecord(
        code=code,
        name=name,
        op_type=getattr(OpType, op),
        qty=qty,
        price=price,
        amount=amount,
        balance=balance,
        fee=fee,
        stamp_tax=stamp_tax,
        commission=commission,
        transfer_fee=transfer_fee,
        trade_date=trade_date,
    )
    if cash_amount is not None:
        rec.cash_amount = cash_amount
    return rec


@pytest.fixture()
def no_price_fetch(monkeypatch):
    """封死行情拉取：任何指标计算都按成本兜底，保证确定性与离线。"""
    monkeypatch.setattr(
        metrics_module,
        "_fetch_latest_prices",
        lambda codes, timeout=15.0: None,
    )
    return None


def _full_history_trades() -> list[TradeRecord]:
    """完整历史手算场景（费用为 0，数字均为精确值）。"""
    return [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 100000, date(2024, 1, 1)),
        T("A", "A股", "BUY", 100, 10, 1000, 99000, date(2024, 1, 2)),
        T("A", "A股", "SELL", 100, 25, 2500, 101500, date(2024, 1, 3)),
        T("B", "B股", "BUY", 100, 10, 1000, 100500, date(2024, 1, 4)),
        T("C", "C股", "BUY", 100, 10, 1000, 99500, date(2024, 1, 5)),
        T("B", "B股", "SELL", 100, 4, 400, 99900, date(2024, 1, 6)),
        T("C", "C股", "SELL", 100, 14, 1400, 101300, date(2024, 1, 12)),
        T("D", "D股", "BUY", 100, 10, 1000, 100300, date(2024, 2, 1)),
        T("D", "D股", "SELL", 100, 12, 1200, 101500, date(2024, 2, 15)),
        T("E", "E股", "BUY", 100, 20, 2000, 99500, date(2024, 3, 1)),
        T("E", "E股", "SELL", 100, 10, 1000, 100500, date(2024, 3, 20)),
        T("F", "F股", "BUY", 100, 5, 500, 100000, date(2024, 3, 21)),
        T("F", "F股", "SELL", 100, 6, 600, 100600, date(2024, 4, 25)),
    ]


def _deposit_basis_double_halved_trades() -> list[TradeRecord]:
    """A0=0（累计入金基准）场景：v 序列 [1.5, 0.7333, 1.7333, 1.3333]。"""
    return [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("A", "A股", "BUY", 100, 50, 5000, 5000, date(2024, 1, 3)),
        T("A", "A股", "SELL", 100, 100, 10000, 15000, date(2024, 1, 5)),
        T("", "", "BANK_TO_SEC", 0, 0, 0, 20000, date(2024, 2, 1)),
        T("B", "B股", "BUY", 100, 100, 10000, 10000, date(2024, 2, 5)),
        T("B", "B股", "SELL", 100, 10, 1000, 11000, date(2024, 2, 10)),
        T("C", "C股", "BUY", 100, 50, 5000, 6000, date(2024, 3, 2)),
        T("C", "C股", "SELL", 100, 200, 20000, 26000, date(2024, 3, 10)),
        T("D", "D股", "BUY", 100, 100, 10000, 16000, date(2024, 4, 2)),
        T("D", "D股", "SELL", 100, 40, 4000, 20000, date(2024, 4, 10)),
    ]


def _opening_asset_basis_trades() -> list[TradeRecord]:
    """A0>0（期初资产基准）场景：期初持仓变现 4000，2 月入金 10000，3 月盈利 5000。"""
    return [
        T("OLD", "老股", "SELL", 200, 20, 4000, 4000, date(2025, 1, 5)),
        T("", "", "BANK_TO_SEC", 0, 0, 0, 14000, date(2025, 2, 3)),
        T("N", "N股", "BUY", 100, 50, 5000, 9000, date(2025, 3, 2)),
        T("N", "N股", "SELL", 100, 100, 10000, 19000, date(2025, 3, 5)),
    ]


def _open_position_trades() -> list[TradeRecord]:
    return [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("ZZZ", "ZZZ股", "BUY", 100, 10, 1000, 9000, date(2024, 1, 2)),
        T("WWW", "WWW股", "BUY", 100, 20, 2000, 7000, date(2024, 1, 3)),
    ]


def _flow_mode_trades() -> list[TradeRecord]:
    """无余额手算场景（balance=None + cash_amount 带符号发生金额）。"""
    return [
        T("", "", "BANK_TO_SEC", 0, 0, 0, None, date(2024, 1, 2), cash_amount=10000),
        T("A", "A股", "BUY", 100, 10, 1000, None, date(2024, 1, 3),
          fee=10, commission=10, cash_amount=-1010),
        T("A", "A股", "SELL", 100, 15, 1500, None, date(2024, 1, 5),
          fee=5, cash_amount=1495),
        T("", "", "DIVIDEND", 0, 0, 0, None, date(2024, 1, 8), cash_amount=100),
        T("", "", "SEC_TO_BANK", 0, 0, 0, None, date(2024, 1, 10),
          cash_amount=-2000),
    ]


# ---------------------------------------------------------------------------
# 完整历史手算抽查（账户 / 交易统计 / 盈亏 / 行为）
# ---------------------------------------------------------------------------


def test_full_history_hand_check_account_and_trading(no_price_fetch):
    m = compute_metrics(_full_history_trades())

    assert m["meta"]["is_partial"] is False
    assert m["meta"]["start_date"] == "2024-01-01"
    assert m["meta"]["end_date"] == "2024-04-25"
    assert m["meta"]["calendar_days"] == 116
    assert m["meta"]["active_trading_days"] == 12
    assert m["meta"]["balance_source"] == "balance"

    a = m["account"]
    assert a["initial_balance"] == 0.0
    assert a["ending_balance"] == pytest.approx(100600.0, abs=0.01)
    assert a["net_transfer_in"] == pytest.approx(100000.0, abs=0.01)
    assert a["total_return_rate"] == pytest.approx(0.006, abs=1e-4)
    assert a["annualized_return_rate"] == pytest.approx(
        (1.006) ** (365 / 115) - 1, abs=1e-4
    )
    assert a["realized_pnl"] == pytest.approx(600.0, abs=0.01)
    assert a["total_cost"] == 0.0
    assert a["holding_market_value"] == 0.0
    assert a["market_value_source"] == "cost"

    t = m["trading"]
    assert t["total_amount"] == pytest.approx(13600.0, abs=0.01)
    assert t["total_count"] == 12
    assert t["buy_count"] == 6
    assert t["buy_amount"] == pytest.approx(6500.0, abs=0.01)
    assert t["sell_count"] == 6
    assert t["sell_amount"] == pytest.approx(7100.0, abs=0.01)
    assert t["distinct_stock_count"] == 6
    assert t["current_holding_count"] == 0
    assert t["avg_trade_amount"] == pytest.approx(13600 / 12, abs=0.01)
    avg_balance = sum(
        [100000, 99000, 101500, 100500, 99500, 99900, 101300,
         100300, 101500, 99500, 100500, 100000, 100600]
    ) / 13
    assert t["capital_turnover_rate"] == pytest.approx(13600 / avg_balance, abs=0.01)
    # 平均持仓周期按完整交易：A 2 / B 3 / C 8 / D 15 / E 20 / F 36 天 → 14 天
    assert t["avg_holding_period_days"] == pytest.approx(14.0, abs=0.01)


def test_full_history_hand_check_pnl_behavior_and_trades(no_price_fetch):
    m = compute_metrics(_full_history_trades())

    p = m["pnl"]
    assert p["realized_pnl"] == pytest.approx(600.0, abs=0.01)
    assert p["win_count"] == 4
    assert p["loss_count"] == 2
    assert p["win_rate"] == pytest.approx(4 / 6, abs=1e-4)
    assert p["total_profit"] == pytest.approx(2200.0, abs=0.01)
    assert p["total_loss"] == pytest.approx(1600.0, abs=0.01)
    assert p["profit_loss_ratio"] == pytest.approx(2200 / 1600, abs=1e-4)
    assert p["max_single_profit"] == pytest.approx(1500.0, abs=0.01)
    assert p["max_single_loss"] == pytest.approx(-1000.0, abs=0.01)
    assert p["double_count"] == 0
    assert p["halved_count"] == 0
    assert p["unmatched_sell_amount"] == 0.0
    assert p["monthly_pnl"] == [
        {"month": "2024-01", "pnl": 1300.0},
        {"month": "2024-02", "pnl": 200.0},
        {"month": "2024-03", "pnl": -1000.0},
        {"month": "2024-04", "pnl": 100.0},
    ]
    assert p["return_curve"] == [
        {"month": "2024-01", "date": "2024-01-12", "return_rate": 0.013},
        {"month": "2024-02", "date": "2024-02-15", "return_rate": 0.015},
        {"month": "2024-03", "date": "2024-03-21", "return_rate": 0.005},
        {"month": "2024-04", "date": "2024-04-25", "return_rate": 0.006},
    ]
    assert p["max_drawdown"] == pytest.approx(1000 / 101500, abs=1e-4)
    assert p["stock_leaderboard"]["top_profit"][0]["code"] == "A"
    assert p["stock_leaderboard"]["top_loss"][0]["code"] == "E"
    assert p["stock_leaderboard"]["top_loss"][0]["total_pnl"] == pytest.approx(
        -1000.0, abs=0.01
    )

    trades = m["trades"]
    assert len(trades) == 6
    assert trades[0] == {
        "code": "A",
        "name": "A股",
        "buy_qty": 100.0,
        "buy_amount": 1000.0,
        "sell_qty": 100.0,
        "sell_amount": 2500.0,
        "pnl": 1500.0,
        "holding_days": 2,
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
        "status": "closed",
    }
    assert [(t["code"], t["pnl"], t["holding_days"]) for t in trades] == [
        ("A", 1500.0, 2),
        ("B", -600.0, 3),
        ("C", 400.0, 8),
        ("D", 200.0, 15),
        ("E", -1000.0, 20),
        ("F", 100.0, 36),
    ]

    b = m["behavior"]
    assert b["holding_period_distribution"] == {
        "d1": 0,
        "d2_3": 2,
        "d4_5": 0,
        "d6_10": 1,
        "d11_20": 2,
        "d21_30": 0,
        "d31_60": 1,
        "gt60": 0,
    }
    assert b["monthly_activity"] == [
        {"month": "2024-01", "total_count": 6, "buy_count": 3, "sell_count": 3},
        {"month": "2024-02", "total_count": 2, "buy_count": 1, "sell_count": 1},
        {"month": "2024-03", "total_count": 3, "buy_count": 2, "sell_count": 1},
        {"month": "2024-04", "total_count": 1, "buy_count": 0, "sell_count": 1},
    ]
    assert b["max_position"]["ratio"] == pytest.approx(2000 / 101500, abs=1e-4)
    assert b["max_position"]["code"] == "E"
    assert b["max_position"]["date"] == "2024-03-01"
    assert b["top5_concentration"] == pytest.approx(12500 / 13600, abs=1e-4)
    assert b["favorite_stocks_top10"][0]["code"] == "A"
    assert b["favorite_stocks_top10"][0]["count"] == 2
    assert b["style"]["label"] == "波段·集中·均衡"


# ---------------------------------------------------------------------------
# FIFO 与费用口径
# ---------------------------------------------------------------------------


def test_fifo_fees_in_cost_and_net_proceeds(no_price_fetch):
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("X", "X股", "BUY", 100, 10, 1000, 8990, date(2024, 1, 2), fee=10, commission=10),
        T("X", "X股", "BUY", 100, 12, 1200, 7780, date(2024, 1, 4), fee=10, commission=10),
        T("X", "X股", "SELL", 50, 15, 750, 8525, date(2024, 1, 5), fee=5),
        T("X", "X股", "SELL", 150, 15, 2250, 10765, date(2024, 1, 10), fee=10),
    ]
    m = compute_metrics(trades)

    assert m["pnl"]["realized_pnl"] == pytest.approx(765.0, abs=0.01)
    assert m["pnl"]["win_count"] == 1
    assert m["pnl"]["loss_count"] == 0
    assert m["pnl"]["win_rate"] == pytest.approx(1.0, abs=1e-4)
    assert m["pnl"]["total_profit"] == pytest.approx(765.0, abs=0.01)
    assert m["pnl"]["profit_loss_ratio"] is None
    assert m["pnl"]["max_single_profit"] == pytest.approx(765.0, abs=0.01)
    assert m["account"]["total_cost"] == pytest.approx(35.0, abs=0.01)
    assert m["account"]["total_return_rate"] == pytest.approx(10765 / 9990 - 1, abs=1e-4)
    assert m["trading"]["total_amount"] == pytest.approx(5200.0, abs=0.01)
    assert m["trades"] == [
        {
            "code": "X",
            "name": "X股",
            "buy_qty": 200.0,
            "buy_amount": 2220.0,
            "sell_qty": 200.0,
            "sell_amount": 2985.0,
            "pnl": 765.0,
            "holding_days": 9,
            "start_date": "2024-01-02",
            "end_date": "2024-01-10",
            "status": "closed",
        }
    ]
    assert m["trading"]["avg_holding_period_days"] == pytest.approx(9.0, abs=0.01)


def test_fifo_partial_sell_no_completed_trade(no_price_fetch):
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("Y", "Y股", "BUY", 100, 10, 1000, 9000, date(2024, 1, 2)),
        T("Y", "Y股", "BUY", 100, 20, 2000, 7000, date(2024, 1, 4)),
        T("Y", "Y股", "SELL", 150, 15, 2250, 9250, date(2024, 1, 10)),
    ]
    m = compute_metrics(trades)
    assert m["pnl"]["realized_pnl"] == pytest.approx(250.0, abs=0.01)
    assert m["account"]["holding_cost_value"] == pytest.approx(1000.0, abs=0.01)
    assert m["trading"]["current_holding_count"] == 1
    assert m["trades"] == []
    assert m["pnl"]["win_count"] == 0
    assert m["pnl"]["win_rate"] is None
    assert m["trading"]["avg_holding_period_days"] is None


# ---------------------------------------------------------------------------
# 中途开始（is_partial）两种场景
# ---------------------------------------------------------------------------


def test_mid_history_with_initial_cash_is_partial(no_price_fetch):
    trades = [
        T("A", "A股", "BUY", 100, 10, 1000, 5000, date(2024, 1, 2), fee=10, commission=10),
        T("", "", "BANK_TO_SEC", 0, 0, 0, 25000, date(2024, 1, 5)),
        T("A", "A股", "SELL", 100, 15, 1500, 26495, date(2024, 1, 8), fee=5),
    ]
    m = compute_metrics(trades)
    assert m["meta"]["is_partial"] is True
    assert m["account"]["initial_balance"] == pytest.approx(6010.0, abs=0.01)
    assert m["account"]["net_transfer_in"] == pytest.approx(20000.0, abs=0.01)
    assert m["account"]["opening_asset_value"] == pytest.approx(6010.0, abs=0.01)
    assert m["account"]["ending_balance"] == pytest.approx(26495.0, abs=0.01)
    # v2.1 主口径（TWR）：(6000/6010) × (26495/26000) − 1 ≈ 0.0173
    assert m["account"]["total_return_rate"] == pytest.approx(
        (6000 / 6010) * (26495 / 26000) - 1, abs=1e-4
    )
    assert m["account"]["total_return_rate_net"] == pytest.approx(485 / 6010, abs=1e-4)
    assert m["account"]["realized_pnl"] == pytest.approx(485.0, abs=0.01)
    assert m["pnl"]["win_rate"] == pytest.approx(1.0, abs=1e-4)
    assert m["account"]["total_cost"] == pytest.approx(15.0, abs=0.01)
    assert m["trades"] == [
        {
            "code": "A",
            "name": "A股",
            "buy_qty": 100.0,
            "buy_amount": 1010.0,
            "sell_qty": 100.0,
            "sell_amount": 1495.0,
            "pnl": 485.0,
            "holding_days": 7,
            "start_date": "2024-01-02",
            "end_date": "2024-01-08",
            "status": "closed",
        }
    ]
    assert m["pnl"]["return_curve"] == [
        {"month": "2024-01", "date": "2024-01-08", "return_rate": 0.0173},
    ]


def test_mid_history_with_opening_position_is_partial(no_price_fetch):
    """首笔是期初持仓的卖出（无法配对）：pnl 中性，只记 unmatched_sell_amount。"""
    trades = [
        T("OLD", "老股", "SELL", 200, 20, 4000, 4000, date(2025, 1, 5)),
        T("OLD", "老股", "BUY", 100, 10, 1000, 3000, date(2025, 1, 6)),
        T("OLD", "老股", "SELL", 100, 12, 1200, 4200, date(2025, 1, 8)),
    ]
    m = compute_metrics(trades)
    assert m["meta"]["is_partial"] is True
    assert m["account"]["initial_balance"] == 0.0
    assert m["pnl"]["unmatched_sell_amount"] == pytest.approx(4000.0, abs=0.01)
    assert m["pnl"]["realized_pnl"] == pytest.approx(200.0, abs=0.01)
    assert m["pnl"]["win_count"] == 1
    assert m["trades"] == [
        {
            "code": "OLD",
            "name": "老股",
            "buy_qty": 100.0,
            "buy_amount": 1000.0,
            "sell_qty": 100.0,
            "sell_amount": 1200.0,
            "pnl": 200.0,
            "holding_days": 3,
            "start_date": "2025-01-06",
            "end_date": "2025-01-08",
            "status": "closed",
        }
    ]
    assert m["account"]["opening_asset_value"] == pytest.approx(4000.0, abs=0.01)
    assert m["account"]["total_return_rate"] == pytest.approx(0.05, abs=1e-4)
    assert m["account"]["total_return_rate_net"] == pytest.approx(0.05, abs=1e-4)
    assert m["pnl"]["return_curve"] == [
        {"month": "2025-01", "date": "2025-01-08", "return_rate": 0.05},
    ]


def test_sell_exceeding_position_marks_partial(no_price_fetch):
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("K", "K股", "BUY", 100, 10, 1000, 9000, date(2024, 1, 2)),
        T("K", "K股", "SELL", 150, 12, 1800, 10800, date(2024, 1, 3)),
    ]
    m = compute_metrics(trades)
    assert m["meta"]["is_partial"] is True
    assert m["pnl"]["realized_pnl"] == pytest.approx(200.0, abs=0.01)
    assert m["pnl"]["unmatched_sell_amount"] == pytest.approx(600.0, abs=0.01)
    assert m["trading"]["current_holding_count"] == 0
    assert m["account"]["opening_asset_value"] == pytest.approx(600.0, abs=0.01)
    assert m["account"]["total_return_rate"] == pytest.approx(
        round(200 / 10600, 4), abs=1e-6
    )
    assert m["pnl"]["return_curve"] == [
        {"month": "2024-01", "date": "2024-01-03", "return_rate": 0.0189},
    ]
    assert m["trades"] == [
        {
            "code": "K",
            "name": "K股",
            "buy_qty": 100.0,
            "buy_amount": 1000.0,
            "sell_qty": 100.0,
            "sell_amount": 1200.0,
            "pnl": 200.0,
            "holding_days": 2,
            "start_date": "2024-01-02",
            "end_date": "2024-01-03",
            "status": "closed",
        }
    ]


# ---------------------------------------------------------------------------
# 红股 / 多周期 / 特殊操作
# ---------------------------------------------------------------------------


def test_bonus_share_dilutes_cost_and_trade_uses_buy_qty_only(no_price_fetch):
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("G", "G股", "BUY", 100, 10, 1000, 9000, date(2024, 1, 2)),
        T("G", "G股", "BONUS_SHARE", 50, 0, 0, 9000, date(2024, 3, 1)),
        T("G", "G股", "SELL", 150, 15, 2250, 11250, date(2024, 5, 1)),
    ]
    m = compute_metrics(trades)
    assert m["pnl"]["realized_pnl"] == pytest.approx(1250.0, abs=0.01)
    assert m["account"]["holding_cost_value"] == 0.0
    assert m["trading"]["current_holding_count"] == 0
    assert m["pnl"]["win_count"] == 1
    assert m["trades"] == [
        {
            "code": "G",
            "name": "G股",
            "buy_qty": 100.0,
            "buy_amount": 1000.0,
            "sell_qty": 150.0,
            "sell_amount": 2250.0,
            "pnl": 1250.0,
            "holding_days": 121,
            "start_date": "2024-01-02",
            "end_date": "2024-05-01",
            "status": "closed",
        }
    ]
    assert m["behavior"]["holding_period_distribution"]["gt60"] == 1
    assert m["behavior"]["special_operations"]["bonus_share"] == {
        "count": 1, "qty": 50.0,
    }


def test_multi_cycle_closed_trades(no_price_fetch):
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("M", "M股", "BUY", 100, 10, 1000, 9000, date(2024, 1, 2)),
        T("M", "M股", "SELL", 100, 25, 2500, 11500, date(2024, 1, 3)),
        T("M", "M股", "BUY", 100, 10, 1000, 10500, date(2024, 1, 4)),
        T("M", "M股", "SELL", 100, 26, 2600, 13100, date(2024, 1, 5)),
    ]
    m = compute_metrics(trades)
    assert len(m["trades"]) == 2
    assert [(t["pnl"], t["holding_days"]) for t in m["trades"]] == [
        (1500.0, 2), (1600.0, 2),
    ]
    assert m["pnl"]["realized_pnl"] == pytest.approx(3100.0, abs=0.01)
    assert m["pnl"]["win_count"] == 2
    assert m["pnl"]["win_rate"] == pytest.approx(1.0, abs=1e-4)
    assert m["behavior"]["holding_period_distribution"]["d2_3"] == 2


def test_special_operations_excluded_from_trades_and_stats(no_price_fetch):
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("131810", "R-001", "REPO", 40, 2.525, 4000, 5999.9, date(2024, 1, 3), fee=0.1),
        T("", "", "DIVIDEND", 0, 0, 0, 6049.9, date(2024, 1, 4)),
        T("", "", "INTEREST", 0, 0, 0, 6051.1, date(2024, 1, 5)),
        T("", "", "DESIGNATED_TRADE", 0, 0, 0, 6051.1, date(2024, 1, 6)),
        T("H", "H股", "BUY", 100, 10, 1000, 5051.1, date(2024, 1, 7)),
        T("", "", "DIVIDEND_DIFF", 0, 0, 0, 5050.5, date(2024, 1, 8)),
        T("H", "H股", "SELL", 100, 12, 1200, 6250.5, date(2024, 1, 9)),
    ]
    m = compute_metrics(trades)
    sp = m["behavior"]["special_operations"]
    assert sp["reverse_repo"] == {"count": 1, "amount": pytest.approx(4000.1, abs=0.01)}
    assert sp["dividend"]["count"] == 2
    assert sp["dividend"]["amount"] == pytest.approx(49.4, abs=0.01)
    assert sp["interest"] == {"count": 1, "amount": pytest.approx(1.2, abs=0.01)}
    assert sp["other"]["count"] == 1
    assert m["trading"]["total_count"] == 2
    assert m["trading"]["total_amount"] == pytest.approx(2200.0, abs=0.01)
    assert m["trading"]["distinct_stock_count"] == 1
    assert m["pnl"]["realized_pnl"] == pytest.approx(200.0, abs=0.01)
    assert len(m["trades"]) == 1
    assert m["trades"][0]["code"] == "H"
    assert m["pnl"]["return_curve"] == [
        {"month": "2024-01", "date": "2024-01-09", "return_rate": 0.0251},
    ]


# ---------------------------------------------------------------------------
# 收益率 / 翻倍腰斩（v2.1 逐日 TWR / v2.2 递进式）
# ---------------------------------------------------------------------------


def test_return_curve_twr_with_transfer_day(no_price_fetch):
    """A0=0 完整历史 + 中途转账日：2/1 转账日 r=0，最终 R=0.5，无翻倍/腰斩。"""
    m = compute_metrics(_deposit_basis_double_halved_trades())
    assert m["meta"]["is_partial"] is False
    assert m["account"]["opening_asset_value"] == 0.0
    assert m["pnl"]["return_curve"] == [
        {"month": "2024-01", "date": "2024-01-05", "return_rate": 0.5},
        {"month": "2024-02", "date": "2024-02-10", "return_rate": -0.175},
        {"month": "2024-03", "date": "2024-03-10", "return_rate": 0.95},
        {"month": "2024-04", "date": "2024-04-10", "return_rate": 0.5},
    ]
    assert m["pnl"]["double_count"] == 0
    assert m["pnl"]["halved_count"] == 0
    assert m["pnl"]["max_drawdown"] == pytest.approx((1.5 - 0.825) / 1.5, abs=1e-4)
    assert m["account"]["total_return_rate"] == pytest.approx(0.5, abs=1e-4)
    assert m["account"]["total_return_rate_net"] == pytest.approx(1 / 3, abs=1e-4)
    assert [(t["code"], t["pnl"], t["holding_days"]) for t in m["trades"]] == [
        ("A", 5000.0, 3),
        ("B", -9000.0, 6),
        ("C", 15000.0, 9),
        ("D", -6000.0, 9),
    ]
    assert m["pnl"]["win_count"] == 2
    assert m["pnl"]["loss_count"] == 2
    assert m["pnl"]["profit_loss_ratio"] == pytest.approx(4 / 3, abs=1e-4)
    assert m["trading"]["avg_holding_period_days"] == pytest.approx(6.75, abs=0.01)


def test_return_curve_twr_opening_position_synthetic(no_price_fetch):
    """A0>0 期初持仓合成：首日卖出期初持仓 r=0，合成持仓 1:1 不产生虚假收益。"""
    m = compute_metrics(_opening_asset_basis_trades())
    assert m["meta"]["is_partial"] is True
    assert m["account"]["opening_asset_value"] == pytest.approx(4000.0, abs=0.01)
    assert m["pnl"]["unmatched_sell_amount"] == pytest.approx(4000.0, abs=0.01)
    assert m["pnl"]["return_curve"] == [
        {"month": "2025-01", "date": "2025-01-05", "return_rate": 0.0},
        {"month": "2025-02", "date": "2025-02-03", "return_rate": 0.0},
        {"month": "2025-03", "date": "2025-03-05", "return_rate": 0.3571},
    ]
    assert m["pnl"]["max_drawdown"] == 0.0
    assert m["account"]["total_return_rate"] == pytest.approx(5 / 14, abs=1e-4)
    assert m["account"]["total_return_rate_net"] == pytest.approx(1.25, abs=1e-4)
    assert m["trades"] == [
        {
            "code": "N",
            "name": "N股",
            "buy_qty": 100.0,
            "buy_amount": 5000.0,
            "sell_qty": 100.0,
            "sell_amount": 10000.0,
            "pnl": 5000.0,
            "holding_days": 4,
            "start_date": "2025-03-02",
            "end_date": "2025-03-05",
            "status": "closed",
        }
    ]


def test_return_curve_fills_months_without_records(no_price_fetch):
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("A", "A股", "BUY", 100, 10, 1000, 9000, date(2024, 1, 2)),
        T("A", "A股", "SELL", 100, 12, 1200, 10200, date(2024, 1, 3)),
        T("B", "B股", "BUY", 100, 10, 1000, 9200, date(2024, 4, 1)),
    ]
    m = compute_metrics(trades)
    assert [pt["month"] for pt in m["pnl"]["return_curve"]] == [
        "2024-01", "2024-02", "2024-03", "2024-04",
    ]
    assert m["pnl"]["return_curve"][1] == {
        "month": "2024-02", "date": "2024-02-29", "return_rate": 0.02,
    }
    assert m["pnl"]["return_curve"][2] == {
        "month": "2024-03", "date": "2024-03-31", "return_rate": 0.02,
    }


def test_twr_transfers_do_not_affect_return(no_price_fetch):
    base = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("A", "A股", "BUY", 100, 10, 1000, 9000, date(2024, 1, 3)),
        T("A", "A股", "SELL", 100, 12, 1200, 10200, date(2024, 1, 4)),
    ]
    with_roundtrip = base + [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 110200, date(2024, 1, 5)),
        T("", "", "SEC_TO_BANK", 0, 0, 0, 10200, date(2024, 1, 5)),
    ]
    m1 = compute_metrics(base)
    m2 = compute_metrics(with_roundtrip)
    assert m1["account"]["total_return_rate"] == pytest.approx(0.02, abs=1e-6)
    assert m2["account"]["total_return_rate"] == pytest.approx(0.02, abs=1e-6)
    assert m2["pnl"]["return_curve"] == [
        {"month": "2024-01", "date": "2024-01-05", "return_rate": 0.02},
    ]
    assert m2["account"]["net_transfer_in"] == pytest.approx(10000.0, abs=0.01)
    assert m2["account"]["gross_deposit"] == pytest.approx(110000.0, abs=0.01)
    assert m2["account"]["gross_withdraw"] == pytest.approx(100000.0, abs=0.01)


def test_twr_double_and_halved_events(no_price_fetch):
    """v2.1 翻倍独立事件 + v2.2 递进式腰斩（floor 新高重置）。"""
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("A", "A股", "BUY", 100, 10, 1000, 9000, date(2024, 1, 3)),
        T("A", "A股", "SELL", 100, 110, 11000, 20000, date(2024, 1, 4)),
        T("B", "B股", "BUY", 100, 50, 5000, 15000, date(2024, 1, 5)),
        T("B", "B股", "SELL", 100, 25, 2500, 17500, date(2024, 1, 6)),
        T("C", "C股", "BUY", 100, 80, 8000, 9500, date(2024, 1, 7)),
        T("C", "C股", "SELL", 100, 160, 16000, 25500, date(2024, 1, 8)),
        T("D", "D股", "BUY", 100, 200, 20000, 5500, date(2024, 1, 9)),
        T("D", "D股", "SELL", 100, 50, 5000, 10500, date(2024, 1, 10)),
        T("E", "E股", "BUY", 100, 30, 3000, 7500, date(2024, 1, 11)),
        T("E", "E股", "SELL", 100, 55, 5500, 13000, date(2024, 1, 12)),
        T("G", "G股", "BUY", 100, 60, 6000, 7000, date(2024, 1, 15)),
        T("G", "G股", "SELL", 100, 80, 8000, 15000, date(2024, 1, 16)),
        T("H", "H股", "BUY", 100, 80, 8000, 7000, date(2024, 1, 17)),
        T("H", "H股", "SELL", 100, 40, 4000, 11000, date(2024, 1, 18)),
    ]
    m = compute_metrics(trades)
    assert m["pnl"]["double_count"] == 2
    assert m["pnl"]["halved_count"] == 1
    assert m["pnl"]["double_events"] == [
        {"date": "2024-01-04", "return_rate": 1.0},
        {"date": "2024-01-08", "return_rate": 1.55},
    ]
    assert m["pnl"]["halved_events"] == [
        {"date": "2024-01-10", "return_rate": 0.05},
    ]
    assert m["account"]["total_return_rate"] == pytest.approx(0.1, abs=1e-6)
    assert m["pnl"]["max_drawdown"] == pytest.approx((2.55 - 1.05) / 2.55, abs=1e-4)


def test_twr_halved_progressive_consecutive_decline_v22(no_price_fetch):
    """v2.2 端到端：连续下跌逐级计数 + 回升新高重置 + 再腰斩。"""
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("A", "A股", "BUY", 1000, 10, 10000, 0, date(2024, 1, 3)),
        T("A", "A股", "SELL", 1000, 5, 5000, 5000, date(2024, 1, 4)),
        T("A", "A股", "BUY", 1000, 5, 5000, 0, date(2024, 1, 5)),
        T("A", "A股", "SELL", 1000, 2.5, 2500, 2500, date(2024, 1, 8)),
        T("A", "A股", "BUY", 1000, 2.5, 2500, 0, date(2024, 1, 9)),
        T("A", "A股", "SELL", 1000, 1.25, 1250, 1250, date(2024, 1, 10)),
        T("A", "A股", "BUY", 1000, 1.25, 1250, 0, date(2024, 1, 11)),
        T("A", "A股", "SELL", 1000, 30, 30000, 30000, date(2024, 1, 12)),
        T("A", "A股", "BUY", 1000, 30, 30000, 0, date(2024, 1, 15)),
        T("A", "A股", "SELL", 1000, 15, 15000, 15000, date(2024, 1, 16)),
        T("A", "A股", "BUY", 1000, 15, 15000, 0, date(2024, 1, 17)),
        T("A", "A股", "SELL", 1000, 12, 12000, 12000, date(2024, 1, 18)),
        T("A", "A股", "BUY", 1000, 12, 12000, 0, date(2024, 1, 19)),
        T("A", "A股", "SELL", 1000, 7.2, 7200, 7200, date(2024, 1, 22)),
    ]
    m = compute_metrics(trades)
    assert m["pnl"]["double_count"] == 1
    assert m["pnl"]["halved_count"] == 5
    assert m["pnl"]["double_events"] == [
        {"date": "2024-01-12", "return_rate": 2.0},
    ]
    assert m["pnl"]["halved_events"] == [
        {"date": "2024-01-04", "return_rate": -0.5},
        {"date": "2024-01-08", "return_rate": -0.75},
        {"date": "2024-01-10", "return_rate": -0.875},
        {"date": "2024-01-16", "return_rate": 0.5},
        {"date": "2024-01-22", "return_rate": -0.28},
    ]
    assert m["account"]["total_return_rate"] == pytest.approx(0.72 - 1, abs=1e-6)
    assert m["pnl"]["max_drawdown"] == pytest.approx((1.0 - 0.125) / 1.0, abs=1e-6)


def test_count_doublings_hand_sequences():
    assert metrics_module._count_doublings([1.0, 1.9, 2.0, 2.1, 1.9, 2.0]) == 2
    assert metrics_module._count_doublings([1.0, 2.0, 1.5, 2.0]) == 2
    assert metrics_module._count_doublings([1.5, 1.9]) == 0
    assert metrics_module._count_doublings([]) == 0


def test_count_halvings_progressive_consecutive_decline():
    assert metrics_module._count_halvings([1.0, 0.5, 0.25, 0.125]) == 3
    assert metrics_module._count_halvings([1.0, 0.5, 0.25, 0.125, 0.0625]) == 4


def test_count_halvings_rebound_resets_baseline():
    assert metrics_module._count_halvings([1.0, 0.5, 0.25, 0.125, 2.0, 1.0]) == 4
    assert metrics_module._count_halvings([1.0, 0.5, 0.6, 0.25]) == 2


def test_count_halvings_mixed_series():
    assert metrics_module._count_halvings([1.0, 1.6, 1.2, 0.7, 0.9, 2.0, 1.4, 0.68]) == 2


def test_count_halvings_flat_and_equal_boundaries():
    assert metrics_module._count_halvings([1.0, 0.5, 0.5, 0.4]) == 1
    assert metrics_module._count_halvings([1.0, 1.0, 1.0]) == 0
    assert metrics_module._count_halvings([]) == 0


def test_double_halved_events_hand_sequences():
    dates = [date(2024, 1, d) for d in (2, 3, 4, 5, 6, 7)]
    vs = [1.0, 0.5, 0.25, 0.125, 2.0, 1.0]
    assert metrics_module._halved_events(dates, vs) == [
        {"date": "2024-01-03", "return_rate": -0.5},
        {"date": "2024-01-04", "return_rate": -0.75},
        {"date": "2024-01-05", "return_rate": -0.875},
        {"date": "2024-01-07", "return_rate": 0.0},
    ]
    assert metrics_module._double_events(dates, vs) == [
        {"date": "2024-01-06", "return_rate": 1.0},
    ]
    assert metrics_module._count_halvings(vs) == len(
        metrics_module._halved_events(dates, vs)
    )
    assert metrics_module._count_doublings(vs) == len(
        metrics_module._double_events(dates, vs)
    )


# ---------------------------------------------------------------------------
# M6 首行证转银 / 榜单排序
# ---------------------------------------------------------------------------


def test_first_row_sec_to_bank_counts_withdrawal(no_price_fetch):
    trades = [
        T("", "", "SEC_TO_BANK", 0, 0, 2000, 8000, date(2024, 1, 2)),
        T("A", "A股", "BUY", 100, 10, 1000, 7000, date(2024, 1, 3)),
        T("A", "A股", "SELL", 100, 12, 1200, 8200, date(2024, 1, 5)),
    ]
    m = compute_metrics(trades)
    assert m["account"]["initial_balance"] == pytest.approx(10000.0, abs=0.01)
    assert m["account"]["net_transfer_in"] == pytest.approx(-2000.0, abs=0.01)
    assert m["account"]["gross_deposit"] == pytest.approx(0.0, abs=0.01)
    assert m["account"]["gross_withdraw"] == pytest.approx(2000.0, abs=0.01)
    assert m["account"]["opening_asset_value"] == pytest.approx(10000.0, abs=0.01)
    assert m["account"]["total_return_rate"] == pytest.approx(0.025, abs=1e-6)


def test_top_loss_ascending_top_profit_descending(no_price_fetch):
    trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 100000, date(2024, 1, 2)),
        T("P", "P股", "BUY", 100, 10, 1000, 99000, date(2024, 1, 3)),
        T("P", "P股", "SELL", 100, 5, 500, 99500, date(2024, 1, 4)),
        T("Q", "Q股", "BUY", 100, 20, 2000, 97500, date(2024, 1, 5)),
        T("Q", "Q股", "SELL", 100, 10, 1000, 98500, date(2024, 1, 6)),
        T("R", "R股", "BUY", 100, 30, 3000, 95500, date(2024, 1, 7)),
        T("R", "R股", "SELL", 100, 28, 2800, 98300, date(2024, 1, 8)),
        T("S", "S股", "BUY", 100, 10, 1000, 97300, date(2024, 1, 9)),
        T("S", "S股", "SELL", 100, 13, 1300, 98600, date(2024, 1, 10)),
        T("T", "T股", "BUY", 100, 10, 1000, 97600, date(2024, 1, 11)),
        T("T", "T股", "SELL", 100, 11, 1100, 98700, date(2024, 1, 12)),
    ]
    m = compute_metrics(trades)
    assert [(x["code"], x["total_pnl"]) for x in m["pnl"]["stock_leaderboard"]["top_loss"]] == [
        ("Q", -1000.0), ("P", -500.0), ("R", -200.0),
    ]
    assert [(x["code"], x["total_pnl"]) for x in m["pnl"]["stock_leaderboard"]["top_profit"]] == [
        ("S", 300.0), ("T", 100.0),
    ]


# ---------------------------------------------------------------------------
# akshare 估值：成功 / 失败按成本兜底
# ---------------------------------------------------------------------------


def test_akshare_live_price_success(monkeypatch):
    monkeypatch.setattr(
        metrics_module,
        "_fetch_latest_prices",
        lambda codes, timeout=15.0: {"ZZZ": 25.0, "WWW": 8.0},
    )
    m = compute_metrics(_open_position_trades())
    a = m["account"]
    assert a["market_value_source"] == "akshare"
    assert a["valuation_date"] is not None
    assert a["holding_market_value"] == pytest.approx(3300.0, abs=0.01)
    assert a["holding_cost_value"] == pytest.approx(3000.0, abs=0.01)
    assert a["unrealized_pnl"] == pytest.approx(300.0, abs=0.01)
    assert m["pnl"]["win_count"] == 0
    assert m["trades"] == []


def test_akshare_failure_falls_back_to_cost(monkeypatch):
    monkeypatch.setattr(
        metrics_module, "_fetch_latest_prices", lambda codes, timeout=15.0: None
    )
    m = compute_metrics(_open_position_trades())
    a = m["account"]
    assert a["market_value_source"] == "cost"
    assert a["valuation_date"] is None
    assert a["holding_market_value"] == pytest.approx(3000.0, abs=0.01)
    assert a["unrealized_pnl"] == 0.0


def test_price_fetch_fallback_chain(monkeypatch):
    """akshare 全市场表失败 + realtime 链不可用 → 逐只直连兜底仍应拿到实时价。"""
    import stock_review_crew.tools.realtime as rt_module

    monkeypatch.setattr(
        metrics_module, "_fetch_spot_em_table", lambda ak_module: None
    )
    monkeypatch.setattr(
        rt_module, "fetch_stock_quote",
        lambda code: (_ for _ in ()).throw(RuntimeError("net down")),
    )
    monkeypatch.setattr(
        metrics_module,
        "_fetch_single_em_price",
        lambda code: {"ZZZ": 25.0, "WWW": 8.0}.get(code),
    )
    m = compute_metrics(_open_position_trades(), price_timeout=5)
    assert m["account"]["market_value_source"] == "akshare"
    assert m["account"]["holding_market_value"] == pytest.approx(3300.0, abs=0.01)


# ---------------------------------------------------------------------------
# Schema / 序列化 / 边界
# ---------------------------------------------------------------------------


def test_json_schema_snake_case_and_serializable(no_price_fetch):
    m = compute_metrics(_full_history_trades())
    pattern = re.compile(r"^[a-z0-9_]+$")

    def check_keys(obj, path=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                assert pattern.match(key), f"非 snake_case 字段: {path}{key}"
                check_keys(value, f"{path}{key}.")
        elif isinstance(obj, list):
            for item in obj:
                check_keys(item, path)

    check_keys(m)
    payload = json.dumps(m, ensure_ascii=False, allow_nan=False)
    assert json.loads(payload) == m.to_dict()
    assert isinstance(m, MetricsResult)
    assert len(m["trades"]) == 6
    assert len(m["pnl"]["return_curve"]) == 4
    assert isinstance(m["stocks"], list) and len(m["stocks"]) == 6


def test_empty_result_has_v2_fields():
    m = compute_metrics([])
    assert m["meta"]["is_partial"] is False
    assert m["meta"]["start_date"] is None
    assert m["meta"]["balance_source"] == "balance"
    assert m["account"]["initial_balance"] == 0.0
    assert m["account"]["total_return_rate"] is None
    assert m["pnl"]["win_rate"] is None
    assert m["trading"]["total_count"] == 0
    assert m["trading"]["avg_holding_period_days"] is None
    assert m["pnl"]["monthly_pnl"] == []
    assert m["pnl"]["equity_curve"] == []
    assert m["pnl"]["return_curve"] == []
    assert m["pnl"]["double_events"] == []
    assert m["pnl"]["halved_events"] == []
    assert m["trades"] == []
    assert m["stocks"] == []
    json.dumps(m, allow_nan=False)


def test_date_format_tolerance_and_stable_order(no_price_fetch):
    trades = [
        T("D1", "D1股", "BUY", 100, 10, 1000, 9000, "20240102"),
        T("D2", "D2股", "BUY", 100, 10, 1000, 8000, 20240102),
        T("D3", "D3股", "BUY", 100, 10, 1000, 7000, datetime(2024, 1, 2, 10, 30)),
    ]
    m = compute_metrics(trades)
    assert m["meta"]["start_date"] == "2024-01-02"
    assert m["meta"]["end_date"] == "2024-01-02"
    assert m["trading"]["total_count"] == 3
    assert m["trading"]["current_holding_count"] == 3
    assert m["trades"] == []


def test_duck_typed_records_without_balance():
    trades = [
        SimpleNamespace(
            code="Q", name="Q股", op_type="BUY", qty=100, price=10,
            amount=1000, trade_date="2024-02-01",
        ),
        SimpleNamespace(
            code="Q", name="Q股", op_type="SELL", qty=100, price=12,
            amount=1200, trade_date="2024-02-03",
        ),
    ]
    m = compute_metrics(trades)
    assert m["pnl"]["realized_pnl"] == pytest.approx(200.0, abs=0.01)
    assert m["pnl"]["win_rate"] == pytest.approx(1.0, abs=1e-4)
    assert len(m["trades"]) == 1
    assert m["trades"][0]["pnl"] == pytest.approx(200.0, abs=0.01)
    assert m["pnl"]["return_curve"][0]["return_rate"] is None
    assert m["account"]["total_return_rate"] is None
    assert m["meta"]["is_partial"] is True
    assert m["meta"]["balance_source"] == "flow"
    assert m["account"]["initial_balance"] == 0.0


# ---------------------------------------------------------------------------
# 流水模式（无余额文件，涨乐「发生金额」口径）
# ---------------------------------------------------------------------------


def test_flow_mode_hand_check(no_price_fetch):
    m = compute_metrics(_flow_mode_trades())
    assert m["meta"]["is_partial"] is True
    assert m["meta"]["balance_source"] == "flow"
    assert m["meta"]["start_date"] == "2024-01-02"
    assert m["meta"]["end_date"] == "2024-01-10"

    a = m["account"]
    assert a["initial_balance"] == 0.0
    assert a["ending_balance"] == pytest.approx(8585.0, abs=0.01)
    assert a["net_transfer_in"] == pytest.approx(8000.0, abs=0.01)
    assert a["gross_deposit"] == pytest.approx(10000.0, abs=0.01)
    assert a["gross_withdraw"] == pytest.approx(2000.0, abs=0.01)
    assert a["opening_asset_value"] == 0.0
    assert a["realized_pnl"] == pytest.approx(485.0, abs=0.01)
    assert a["total_cost"] == pytest.approx(15.0, abs=0.01)
    assert a["total_return_rate"] == pytest.approx(0.0585, abs=1e-6)
    assert a["total_return_rate_net"] == pytest.approx(-0.1415, abs=1e-6)

    p = m["pnl"]
    assert p["realized_pnl"] == pytest.approx(485.0, abs=0.01)
    assert p["win_count"] == 1
    assert p["win_rate"] == pytest.approx(1.0, abs=1e-4)
    assert p["monthly_pnl"] == [{"month": "2024-01", "pnl": 485.0}]
    assert p["return_curve"] == [
        {"month": "2024-01", "date": "2024-01-10", "return_rate": 0.0585},
    ]
    assert m["trades"] == [
        {
            "code": "A",
            "name": "A股",
            "buy_qty": 100.0,
            "buy_amount": 1010.0,
            "sell_qty": 100.0,
            "sell_amount": 1495.0,
            "pnl": 485.0,
            "holding_days": 3,
            "start_date": "2024-01-03",
            "end_date": "2024-01-05",
            "status": "closed",
        }
    ]
    assert m["behavior"]["special_operations"]["dividend"] == {
        "count": 1,
        "amount": pytest.approx(100.0),
    }


def test_flow_mode_equivalent_to_balance_mode(no_price_fetch):
    """同一组交易分别用「发生金额流水」与「资金余额」表达 → 关键指标完全一致。"""
    balance_trades = [
        T("", "", "BANK_TO_SEC", 0, 0, 0, 10000, date(2024, 1, 2)),
        T("A", "A股", "BUY", 100, 10, 1000, 8990, date(2024, 1, 3),
          fee=10, commission=10),
        T("A", "A股", "SELL", 100, 15, 1500, 10485, date(2024, 1, 5), fee=5),
        T("", "", "DIVIDEND", 0, 0, 100, 10585, date(2024, 1, 8)),
        T("", "", "SEC_TO_BANK", 0, 0, 0, 8585, date(2024, 1, 10)),
    ]
    m_bal = compute_metrics(balance_trades)
    m_flow = compute_metrics(_flow_mode_trades())

    assert m_bal["meta"]["balance_source"] == "balance"
    for key in ("realized_pnl", "net_transfer_in", "gross_deposit", "gross_withdraw",
                "total_return_rate", "total_return_rate_net", "ending_balance"):
        assert m_flow["account"][key] == pytest.approx(
            m_bal["account"][key], abs=1e-9
        ), key
    assert m_flow["trades"] == m_bal["trades"]
    assert m_flow["pnl"]["monthly_pnl"] == m_bal["pnl"]["monthly_pnl"]
    assert m_flow["pnl"]["return_curve"] == m_bal["pnl"]["return_curve"]


def test_flow_mode_parser_contract_zero_balance_filled_cash_amount(no_price_fetch):
    """parser 契约：涨乐 CSV 无余额列 → balance=0.0、cash_amount=发生金额，
    应识别为流水模式（而非把 0.0 余额当权威）。"""
    trades = [
        T("A", "A股", "BUY", 100, 10, 1000, 0.0, date(2024, 1, 2),
          fee=10, commission=10, cash_amount=-1010),
        T("A", "A股", "SELL", 100, 12, 1200, 0.0, date(2024, 1, 4),
          fee=5, cash_amount=1195),
    ]
    m = compute_metrics(trades)
    assert m["meta"]["is_partial"] is True
    assert m["meta"]["balance_source"] == "flow"
    assert m["account"]["initial_balance"] == 0.0
    assert m["account"]["ending_balance"] == pytest.approx(185.0, abs=0.01)
    assert m["pnl"]["realized_pnl"] == pytest.approx(185.0, abs=0.01)
    assert m["trades"][0]["buy_amount"] == pytest.approx(1010.0, abs=0.01)
    assert m["trades"][0]["sell_amount"] == pytest.approx(1195.0, abs=0.01)


def test_estimate_cash_delta_prefers_cash_amount():
    """发生金额优先：买入负、卖出正、转账±、红利+；0.0 视为未提供回退估算。"""
    def delta(op, qty, price, amount, cash_amount=None, fee=0.0, commission=0.0):
        return metrics_module._estimate_cash_delta(
            metrics_module._Rec(
                op=op, code="", name="", qty=qty, price=price, amount=amount,
                balance=None, fee=fee, stamp_tax=0.0, commission=commission,
                transfer_fee=0.0, date=date(2024, 1, 2), idx=0,
                cash_amount=cash_amount,
            )
        )

    assert delta("buy", 100, 10, 1000, cash_amount=-1010, fee=10, commission=10) == -1010.0
    assert delta("sell", 100, 15, 1500, cash_amount=1495, fee=5) == 1495.0
    assert delta("transfer_in", 0, 0, 5000, cash_amount=5000) == 5000.0
    assert delta("transfer_out", 0, 0, 2000, cash_amount=-2000) == -2000.0
    # 0.0 视为未提供（同花顺版式 parser 留 0.0）→ 按操作类型估算
    assert delta("buy", 100, 10, 1000, cash_amount=0.0, fee=10, commission=10) == -1010.0
    assert delta("sell", 100, 10, 1000, cash_amount=0.0, fee=10) == 990.0
    # 未提供现金（None）→ 估算
    assert delta("buy", 100, 10, 1000) == -1000.0
