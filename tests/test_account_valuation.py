# -*- coding: utf-8 -*-
"""模拟账户估值口径验收（2026-08-13 核心 Bug 修复）：
- 持仓「现价/市值/浮动盈亏」一律取**最近结算日快照的历史收盘价**（snapshots[-1].positions），
  与 total_assets 同源一致，不再调用实时行情（fetch_stock_quote 断言不被调用）；
- 旧快照（无 positions 字段）或无常快照 → 回退成本价并标注 price_estimated / degraded；
- 兼容用例：旧账户回退成本价不报错。
手算样例：600519 收盘 10.5 × 2900 股 = 市值 30,450、浮盈 1,450；
000975 收盘 19.6 × 700 股 = 市值 13,720、浮盈 0；total_assets = 100,000 + 44,170 = 144,170。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from stock_review_crew.accounts.engine import INITIAL_CASH, open_account, snapshot  # noqa: E402

from backend.accounts_routes import (  # noqa: E402
    _historical_close_prices,
    _position_rows,
    _summary,
)


def _make_acct():
    """含历史快照（带 positions）的账户：600519 收盘 10.5；000975 收盘 19.6（全量命中）。"""
    acct = open_account("alang")
    acct["trades"] = []
    acct["positions"] = {
        "600519": {"name": "贵州茅台", "qty": 2900, "frozen_qty": 0, "avg_cost": 10.0},
        "000975": {"name": "000975", "qty": 700, "frozen_qty": 700, "avg_cost": 19.6},
    }
    acct["snapshots"] = [
        snapshot(acct, "2026-07-06", {"600519": 10.5, "000975": 19.6}),
    ]
    return acct


def test_historical_close_prices_from_latest_snapshot():
    acct = _make_acct()
    prices = _historical_close_prices(acct)
    assert prices["600519"] == pytest.approx(10.5)
    assert prices["000975"] == pytest.approx(19.6)


def test_snapshot_estimated_close_flagged_in_degraded():
    """快照生成时某持仓收盘缺失（estimated_prices 非空）→ summary 如实给出降级标注。"""
    acct = _make_acct()
    acct["snapshots"] = [
        snapshot(acct, "2026-07-06", {"600519": 10.5}),  # 000975 收盘缺失 → 按成本兜底
    ]
    s = _summary("alang", acct, {"alang": "阿郎"})
    assert any("收盘价缺失" in d for d in (s.get("degraded") or []))
    rows = _position_rows(acct)
    assert {r["code"]: r["price"] for r in rows}["000975"] == pytest.approx(19.6)


def test_position_rows_use_historical_close_not_realtime(monkeypatch):
    """持仓行价格/市值/浮盈取历史收盘价；fetch_stock_quote 不被调用。"""
    import stock_review_crew.tools.realtime as realtime

    calls: list[str] = []
    monkeypatch.setattr(
        realtime, "fetch_stock_quote",
        lambda code: calls.append(code) or {"price": 999.0},
    )
    rows = _position_rows(_make_acct())
    assert calls == [], "估值口径不得调用实时接口"
    by_code = {r["code"]: r for r in rows}
    r1 = by_code["600519"]
    assert r1["price"] == pytest.approx(10.5)
    assert r1["market_value"] == pytest.approx(2900 * 10.5)        # 30,450
    assert r1["pnl"] == pytest.approx(2900 * 10.5 - 2900 * 10.0)  # 1,450
    assert r1["pnl_rate"] == pytest.approx(1450.0 / 29_000.0)
    assert r1["price_estimated"] is False
    r2 = by_code["000975"]
    assert r2["price"] == pytest.approx(19.6)
    assert r2["market_value"] == pytest.approx(700 * 19.6)         # 13,720
    assert r2["pnl"] == pytest.approx(0.0)
    assert r2["price_estimated"] is False


def test_summary_uses_historical_valuation_not_realtime(monkeypatch):
    """_summary 的浮盈/总资产/收益率全部基于历史快照，且不调用实时接口。"""
    import stock_review_crew.tools.realtime as realtime

    calls: list[str] = []
    monkeypatch.setattr(
        realtime, "fetch_stock_quote",
        lambda code: calls.append(code) or {"price": 999.0},
    )
    acct = _make_acct()
    s = _summary("alang", acct, {"alang": "阿郎"})
    assert calls == [], "summary 不得调用实时接口"
    total = INITIAL_CASH + 2900 * 10.5 + 700 * 19.6  # 144,170
    assert s["total_assets"] == pytest.approx(total)
    assert s["total_return_rate"] == pytest.approx((total - INITIAL_CASH) / INITIAL_CASH)
    assert s["unrealized_pnl"] == pytest.approx(2900 * (10.5 - 10.0))  # 1,450，与 total_assets 同源
    assert s["updated_at"] == "2026-07-06"
    assert s["price_estimated"] is False
    assert s.get("degraded") == []


def test_account_detail_endpoint_uses_historical_price(monkeypatch):
    """API 端点 /api/accounts/{skill_id} 的持仓与摘要同样走历史价，实时接口零调用。"""
    import backend.accounts_routes as routes
    import backend.main as bm
    import stock_review_crew.tools.realtime as realtime
    from fastapi.testclient import TestClient

    acct = _make_acct()
    monkeypatch.setattr(routes.acct_storage, "load_account", lambda sid: dict(acct))
    monkeypatch.setattr(routes.acct_storage, "load_trades", lambda sid: [])
    monkeypatch.setattr(routes.acct_storage, "load_snapshots", lambda sid: acct["snapshots"])
    monkeypatch.setattr(routes.acct_storage, "save_account", lambda sid, a: None)
    calls: list[str] = []
    monkeypatch.setattr(
        realtime, "fetch_stock_quote",
        lambda code: calls.append(code) or {"price": 999.0},
    )
    resp = TestClient(bm.app).get("/api/accounts/alang")
    assert resp.status_code == 200
    data = resp.json()
    assert calls == [], "account_detail 不得调用实时接口"
    by_code = {r["code"]: r for r in data["positions"]}
    assert by_code["600519"]["price"] == pytest.approx(10.5)
    assert by_code["600519"]["market_value"] == pytest.approx(2900 * 10.5)
    assert by_code["600519"]["pnl"] == pytest.approx(1450.0)
    assert data["unrealized_pnl"] == pytest.approx(1450.0)
    assert data["total_assets"] == pytest.approx(144_170.0)
    assert data["updated_at"] == "2026-07-06"


def test_old_snapshot_without_positions_falls_back_to_cost():
    """兼容用例：旧快照（无 positions 字段，历史账户落盘格式）→ 回退成本价并标注，不报错。"""
    acct = _make_acct()
    acct["snapshots"] = [{
        "date": "2026-07-06",
        "cash": 59_021.59037,
        "position_mv": 42_269.0,
        "total_assets": 101_290.59037,
        "realized_pnl": 0.0,
        "estimated_prices": [],
    }]
    rows = _position_rows(acct)
    assert rows, "旧快照账户应正常返回持仓行"
    for r in rows:
        assert r["price"] == r["avg_cost"]
        assert r["market_value"] == pytest.approx(r["avg_cost"] * r["qty"])
        assert r["pnl"] == pytest.approx(0.0)
        assert r["price_estimated"] is True
    s = _summary("bingchuan", acct, {"bingchuan": "冰川"})
    assert s["price_estimated"] is True
    assert any("历史价缺失" in d for d in (s.get("degraded") or []))
    assert s["unrealized_pnl"] == pytest.approx(0.0)
    assert s["total_assets"] == pytest.approx(101_290.59037)


def test_no_snapshot_falls_back_to_cost_with_note():
    """兼容用例：账户有持仓但无常快照 → 回退成本价并给出标注，不报错。"""
    acct = open_account("bingchuan")
    acct["trades"] = []
    acct["positions"] = {
        "000975": {"name": "000975", "qty": 700, "frozen_qty": 700, "avg_cost": 19.6},
    }
    acct["snapshots"] = []
    rows = _position_rows(acct)
    assert rows[0]["price"] == pytest.approx(19.6)
    assert rows[0]["price_estimated"] is True
    s = _summary("bingchuan", acct, {})
    assert s["total_assets"] == pytest.approx(acct["cash"])
    assert any("历史快照" in d for d in (s.get("degraded") or []))
    assert s["price_estimated"] is True
