# -*- coding: utf-8 -*-
"""监管异动单测（离线性：monkeypatch 日K序列）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew.tools import anomaly
from stock_review_crew.tools import stock_data
from stock_review_crew.tools import tdx_remote


def _series(days=35, start_close=10.0, daily_pct=0.03):
    """生成单调上涨的个股日K序列（按交易日粗略 daily）。"""
    import datetime

    out = []
    d = datetime.date(2026, 5, 1)
    close = start_close
    while len(out) < days:
        if d.weekday() < 5:
            out.append({"date": d.strftime("%Y-%m-%d"), "close": close})
            close *= (1 + daily_pct)
        d += datetime.timedelta(days=1)
    return out


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def fake_stock(code):
        return _series(35, 10.0, 0.05), "测试"

    def fake_index(sym, c, m):
        return _series(35, 3000.0, 0.01), "测试"

    monkeypatch.setattr(anomaly, "_stock_series", fake_stock)
    monkeypatch.setattr(anomaly, "_index_series", fake_index)
    monkeypatch.setattr(anomaly, "_apply_realtime_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(tdx_remote, "available", lambda: False)
    monkeypatch.setattr(stock_data, "_records_from_tx_kline", lambda s: [])
    monkeypatch.setattr(stock_data, "_records_from_em_kline", lambda s: [])


def test_board_of():
    assert anomaly.board_of("600664")["board"] == "沪主板"
    assert anomaly.board_of("300750")["board"] == "创业板"
    assert anomaly.board_of("688001")["board"] == "科创板"
    assert anomaly.board_of("830001") is None  # 北交所


def test_compute_anomaly_ok():
    out = anomaly.compute_anomaly("600664", days=2)
    assert out["ok"] is True
    assert out["board"] == "沪主板"
    assert out["windows"]["3d"]["current"] is not None
    assert len(out["simulation"]) == 2
    assert len(out["daily_30"]) <= 30
    assert out["disclaimer"] == "仅供参考，不构成投资建议"


def test_compute_anomaly_asof_date():
    out = anomaly.compute_anomaly("600664", days=2, date="2026-06-01")
    assert out["ok"] is True
    assert out["date"] == "2026-06-01"
    assert out["windows"]["30d"]["current"] is not None



def test_compute_anomaly_supported_only():
    out = anomaly.compute_anomaly("830001", days=2)
    assert out["ok"] is False
