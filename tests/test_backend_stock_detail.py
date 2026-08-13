"""R9 后端契约测试：/api/data/stock/{code} 校验与结构；realtime 指数涨跌家数 enrichment。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import backend.main as bm
import stock_review_crew.tools.realtime as rt
import stock_review_crew.tools.stock_data as sd

client = TestClient(bm.app)


def _fake_detail(code, date=None, days=60):
    return {"code": code, "date": date or "2026-08-04",
            "quote": {"code": code, "price": 10.5, "pct_change": 0.05,
                      "bids": [{"price": 10.4, "volume": 5}], "asks": []},
            "daily": [{"date": "2026-08-04", "close": 10.5}],
            "minute": [], "kline_5m": [], "kline_1m": [],
            "ma": {"ma5": 10.0, "ma10": None, "ma20": None},
            "sources": {"quote": "mock"}, "degraded": [], "updated_at": "2026-08-04 18:00:00"}


def test_stock_detail_ok(monkeypatch):
    monkeypatch.setattr(sd, "fetch_stock_detail", _fake_detail)
    r = client.get("/api/data/stock/600519")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "600519"
    assert body["quote"]["price"] == 10.5
    assert body["ma"]["ma5"] == 10.0 and body["ma"]["ma10"] is None
    assert isinstance(body["degraded"], list)
    assert "sources" in body


def test_stock_detail_invalid_code_400():
    assert client.get("/api/data/stock/abc").status_code == 400
    assert client.get("/api/data/stock/60051").status_code == 400


def test_stock_detail_days_range_400(monkeypatch):
    monkeypatch.setattr(sd, "fetch_stock_detail", _fake_detail)
    assert client.get("/api/data/stock/600519?days=3").status_code == 400
    assert client.get("/api/data/stock/600519?days=999").status_code == 400
    assert client.get("/api/data/stock/600519?days=60").status_code == 200


def test_realtime_indices_enriched_with_updown(monkeypatch):
    base = {"status": {}, "indices": {"indices": [{"code": "000001", "price": 3822.0}]},
            "zt": None, "sectors": None, "news": None, "auction": None,
            "sources": ["mock"], "degraded": []}

    def _enrich(snapshot):
        for row in snapshot["indices"]["indices"]:
            row["up_count"] = 1395
            row["down_count"] = 892
            row["updown_source"] = "通达信协议"
        return snapshot

    monkeypatch.setattr(rt, "fetch_realtime_snapshot", lambda: dict(base))
    monkeypatch.setattr(rt, "enrich_indices_updown", _enrich)
    r = client.get("/api/data/realtime")
    assert r.status_code == 200
    row = r.json()["indices"]["indices"][0]
    assert row["up_count"] == 1395 and row["down_count"] == 892


def test_realtime_survives_enrich_failure(monkeypatch):
    base = {"status": {}, "indices": {"indices": [{"code": "000001", "price": 3822.0}]},
            "zt": None, "sectors": None, "news": None, "auction": None,
            "sources": ["mock"], "degraded": []}
    monkeypatch.setattr(rt, "fetch_realtime_snapshot", lambda: dict(base))

    def _boom(snapshot):
        raise RuntimeError("enrich 挂了")

    monkeypatch.setattr(rt, "enrich_indices_updown", _boom)
    r = client.get("/api/data/realtime")
    assert r.status_code == 200
    assert "up_count" not in r.json()["indices"]["indices"][0]
