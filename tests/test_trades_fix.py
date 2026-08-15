# -*- coding: utf-8 -*-
"""模拟账户交易/历史持仓修复验收（requirements-trades-fix §二/§三）。

覆盖 3 个修复点 + 验收标准逐条：
1. holding_days 同口径 +1：buy 今天 sell 明天 = 2；7-01 买 7-03 卖 = 3
   （engine.closed_trades / engine.trade_stats 两处）；
2. trade 加 buy_reason/sell_reason：
   - place_order 直接调用 buy_reason=None；
   - 撮合（settle_plans / run_replay）买入写入 plan.reason；
   - _conditional_sell_record 的 condition → 中文 sell_reason（止损/止盈/移动止盈/持有超时），
     condition 字段保留兼容；
   - trades.json 序列化带这两个字段；
3. closed_trades 按「清仓周期」不聚合（同股票多次买卖 = 多笔独立记录；
   部分卖出/加仓属同一周期，清仓后新周期）；
4. 前端字段契约：normalize 单测。
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


@pytest.fixture()
def data_dir():
    """仓库内 .tmp 隔离目录（沙箱 pytest tmp_path 有 ACL 问题，见 PLAYBOOK），自建自清。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / "trades_fix"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    for _ in range(3):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            break


# ---------------------------------------------------------------------------
# 可复用的撮合输入（plan / quotes 文件格式即对外契约）
# ---------------------------------------------------------------------------

def _plan(rec_date, code="600519", side="buy", target=10.00, position=3, reason=None, **kw):
    return {"rec_date": rec_date, "skill_id": "alang", "skill_name": "阿郎",
            "code": code, "name": "贵州茅台", "side": side, "target_price": target,
            "position_cheng": position, "qty": None, "source": "tool",
            "reason": reason, **kw}


def _write_plan(data_dir, d, plans):
    p = data_dir / "reviews" / d / "plan.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"date": d, "mode": "close", "generated_at": "2026-08-11T00:00:00", "plans": plans},
        ensure_ascii=False), "utf-8")


def _quote(close=10.5, pre_close=10.0, high=10.8, low=10.2, open_=10.3, name="贵州茅台"):
    return {"name": name, "pre_close": pre_close, "open": open_,
            "high": high, "low": low, "close": close}


def _write_quotes(data_dir, d, quotes_map):
    p = data_dir / "quotes_cache" / f"{d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"date": d, "fetched_at": "2026-08-11T00:00:00", "source": "test",
         "quotes": quotes_map, "degraded": []}, ensure_ascii=False), "utf-8")


# ---------------------------------------------------------------------------
# 1. holding_days +1（trade_stats）
# ---------------------------------------------------------------------------

def _mk_trades(buy_date, sell_date, code="600519"):
    return [
        {"date": buy_date, "code": code, "name": code, "side": "buy",
         "qty": 100, "price": 10.0, "amount": 1000.0, "fee": 5.0, "stamp_tax": 0.0,
         "transfer_fee": 0.01, "status": "成交", "reject_reason": ""},
        {"date": sell_date, "code": code, "name": code, "side": "sell",
         "qty": 100, "price": 11.0, "amount": 1100.0, "fee": 5.0, "stamp_tax": 0.55,
         "transfer_fee": 0.011, "status": "成交", "reject_reason": "", "realized": 90.0},
    ]


def test_trade_stats_holding_days_plus_one():
    """trade_stats.avg_holding_days：今天买明天卖 = 2；7-01 买 7-03 卖 = 3（含买入当天）。"""
    # 今天买明天卖 → 2
    acct = {"trades": _mk_trades("2026-07-01", "2026-07-02")}
    assert engine.trade_stats(acct)["avg_holding_days"] == 2.0
    # 7-01 买 7-03 卖 → 3
    acct2 = {"trades": _mk_trades("2026-07-01", "2026-07-03")}
    assert engine.trade_stats(acct2)["avg_holding_days"] == 3.0
    # 无卖出 → None
    assert engine.trade_stats({"trades": [{"side": "buy", "qty": 100, "status": "成交"}]})[
        "avg_holding_days"] is None


# ---------------------------------------------------------------------------
# 2. trade 的 buy_reason / sell_reason
# ---------------------------------------------------------------------------

def test_place_order_direct_call_buy_reason_none():
    """place_order 直接调用（无 plan）：buy_reason / sell_reason 为 None（不报错）。"""
    acct = engine.open_account("alang")
    day = {"date": "2026-07-01", "pre_close": 10.0, "high": 10.2, "low": 9.8, "close": 10.1}
    r = engine.place_order(acct, "600519", "贵州茅台", "buy", 10.00, 3, day, qty=100)
    assert r.ok
    assert r.trade["side"] == "buy"
    assert r.trade["buy_reason"] is None
    assert r.trade["sell_reason"] is None
    engine._settle_day(acct, "2026-07-02")
    day2 = {"date": "2026-07-02", "pre_close": 10.0, "high": 11.0, "low": 9.9, "close": 10.9}
    r2 = engine.place_order(acct, "600519", "贵州茅台", "sell", 11.0, 10, day2, qty=100,
                            sell_reason="主动卖出")
    assert r2.ok
    assert r2.trade["sell_reason"] == "主动卖出"
    assert r2.trade["buy_reason"] is None


def test_conditional_sell_reason_cn_mapping():
    """条件单 condition → 中文 sell_reason：止损/止盈/移动止盈/持有超时；condition 保留。"""
    mapping = engine.CONDITION_SELL_REASON
    assert mapping == {
        "stop_loss": "止损",
        "take_profit": "止盈",
        "trailing_take_profit": "移动止盈",
        "timeout": "持有超时",
    }
    acct = engine.open_account("alang")
    engine._settle_day(acct, "2026-07-01")
    day = {"date": "2026-07-01", "pre_close": 10.0, "high": 10.2, "low": 9.8, "close": 10.1}
    r = engine.place_order(acct, "600519", "贵州茅台", "buy", 10.00, 0, day, qty=100,
                           conditional={"stop_loss": 9.5, "take_profit": 11.0,
                                        "max_hold_days": 13})
    assert r.ok
    # 止损卖出 → sell_reason=止损，condition=stop_loss（兼容保留）
    day2 = {"date": "2026-07-02", "pre_close": 10.0, "open": 10.0,
            "high": 10.2, "low": 9.3, "close": 9.5}
    trades = engine.check_conditional_orders(acct, day2, {"600519": day2})
    assert len(trades) == 1
    assert trades[0]["sell_reason"] == "止损"
    assert trades[0]["condition"] == "stop_loss"
    assert trades[0]["sell_reason"] is not None and trades[0]["buy_reason"] is None


def test_settle_plans_writes_buy_reason_from_plan(monkeypatch, data_dir):
    """settle_plans 撮合买入：plan.reason → trade.buy_reason；卖出 sell_reason=主动卖出。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-01", [_plan("2026-07-01", reason="低位放量突破")])
    _write_plan(data_dir, "2026-07-02",
                [_plan("2026-07-02", side="sell", target=10.50, position=10)])
    _write_quotes(data_dir, "2026-07-02", {"600519": _quote(close=10.50, high=10.60, low=10.0)})
    _write_quotes(data_dir, "2026-07-03", {"600519": _quote(close=10.50, high=10.60, low=10.2)})
    r1 = replay.settle_plans("2026-07-01", "2026-07-02")
    assert r1["filled"] == 1
    r2 = replay.settle_plans("2026-07-02", "2026-07-03")
    assert r2["filled"] == 1
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    buys = [t for t in trades if t["side"] == "buy"]
    sells = [t for t in trades if t["side"] == "sell"]
    assert buys[0]["buy_reason"] == "低位放量突破"
    assert buys[0]["sell_reason"] is None
    assert sells[0]["sell_reason"] == "主动卖出"
    assert sells[0]["buy_reason"] is None


# ---------------------------------------------------------------------------
# 常量归一后的 CSV/JSON 序列化：旧数据无字段容错
# ---------------------------------------------------------------------------

def test_old_trade_missing_reason_fields_tolerated():
    """旧 trades 记录无 buy_reason/sell_reason → closed_trades 不报错，字段保持 None。"""
    acct = {"positions": {}, "trades": [
        {"date": "2026-07-01", "code": "C", "name": "C", "side": "buy",
         "qty": 100, "amount": 1000.0, "status": "成交", "price": 10.0},
        {"date": "2026-07-02", "code": "C", "name": "C", "side": "sell",
         "qty": 100, "amount": 1100.0, "status": "成交", "price": 11.0, "realized": 95.0},
    ]}
    closed = engine.closed_trades(acct)
    assert len(closed) == 1
    assert closed[0]["buy_reason"] is None and closed[0]["sell_reason"] is None
    assert closed[0]["holding_days"] == 2


# ---------------------------------------------------------------------------
# 3. 清仓周期：加仓/部分卖属同一周期，清仓后新周期（不聚合，只统计成交）
# ---------------------------------------------------------------------------

def test_closed_trades_add_on_roll_up_into_cycles():
    """多笔买入（加仓）+ 一次清仓卖 → 同一周期一条记录：均价=总量、realized=Σ。"""
    acct = {"positions": {}, "trades": [
        # 买 50（07-01）→ 买 50（07-02 加仓）→ 卖 100（07-04 清仓）= 一个周期
        {"date": "2026-07-01", "code": "M", "name": "M", "side": "buy",
         "qty": 50, "amount": 500.0, "status": "成交", "buy_reason": "R1"},
        {"date": "2026-07-02", "code": "M", "name": "M", "side": "buy",
         "qty": 50, "amount": 550.0, "status": "成交", "buy_reason": "R2"},
        {"date": "2026-07-04", "code": "M", "name": "M", "side": "sell",
         "qty": 100, "amount": 1300.0, "status": "成交", "realized": 240.0, "sell_reason": "止盈"},
    ]}
    closed = engine.closed_trades(acct)
    assert len(closed) == 1, "加仓+清仓卖属同一周期，应一条记录"
    row = closed[0]
    assert row["buy_qty"] == 100 and row["sell_qty"] == 100
    assert row["buy_date"] == "2026-07-01" and row["sell_date"] == "2026-07-04"
    assert row["holding_days"] == 4  # (7-04 − 7-01).days + 1
    # 均价 = 周期总量；buy_amount = 买入总额
    assert row["avg_buy_price"] == pytest.approx(1050.0 / 100)
    assert row["buy_amount"] == pytest.approx(1050.0)
    assert row["realized_pnl"] == pytest.approx(240.0)
    assert row["buy_reason"] == "R1", "buy_reason = 周期首买理由"
    assert row["sell_reason"] == "止盈"
