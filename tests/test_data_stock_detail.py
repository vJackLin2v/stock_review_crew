"""R9 个股详情聚合测试（全离线；手算用例：均线/日期过滤/独立降级）。"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from stock_review_crew.tools import realtime
from stock_review_crew.tools import stock_data as sd
from stock_review_crew.tools import tdx_remote


def _mk_daily(n, start_close=1.0):
    """最近 n 根日K，close = start_close..start_close+n-1（便于均线手算）。"""
    base = datetime.date(2026, 8, 4)
    bars = []
    for i in range(n):
        d = (base - datetime.timedelta(days=n - i)).isoformat()
        c = start_close + i
        bars.append({"date": d, "open": c - 0.1, "high": c + 0.2, "low": c - 0.2,
                     "close": c, "volume": 100.0, "amount": 10000.0,
                     "time": None, "datetime": d + " 15:00"})
    return bars


def _mk_minute_kline(n, category):
    """分钟K：一半在今天（2026-08-04），一半在昨天（用于日期过滤断言）。"""
    bars = []
    for i in range(n):
        d = "2026-08-04" if i % 2 == 0 else "2026-08-03"
        bars.append({"date": d, "time": "10:%02d" % (i % 60), "datetime": d + " 10:%02d" % (i % 60),
                     "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05,
                     "volume": 10.0, "amount": 1000.0})
    return bars


def _fake_quote(code):
    return {"code": code, "name": "测试股", "price": 10.5, "pct_change": 0.05,
            "open": 10.1, "high": 10.8, "low": 10.0, "pre_close": 10.0,
            "volume": 1000, "amount": 1e7, "turnover_rate": 0.01,
            "bids": [{"price": 10.4, "volume": 5}], "asks": [{"price": 10.6, "volume": 3}],
            "source": "mock"}


def _mock_all(monkeypatch, daily_n=5, kline_n=40):
    monkeypatch.setattr(realtime, "fetch_stock_quote", _fake_quote)
    monkeypatch.setattr(tdx_remote, "fetch_daily",
                        lambda code, count=250, is_index=False, market=None: _mk_daily(daily_n))
    monkeypatch.setattr(tdx_remote, "fetch_minute_kline",
                        lambda code, count=800, category=8: _mk_minute_kline(kline_n, category))
    monkeypatch.setattr(sd, "fetch_minute_data",
                        lambda symbol, date=None, period=1, end_time=None, tdx_path=None: {
                            "bars": [{"time": "09:31", "close": 10.0, "volume": 1, "amount": 100}],
                            "source": "mock"})


def test_detail_structure_and_ma_hand_calc(monkeypatch):
    _mock_all(monkeypatch, daily_n=5)
    out = sd.fetch_stock_detail("600519", date="2026-08-04", days=60)
    assert out["code"] == "600519"
    assert out["quote"]["price"] == 10.5
    assert out["quote"]["bids"][0]["price"] == 10.4
    # 手算：close 1..5 → ma5 = 3.0；不足 10/20 根 → None
    assert out["ma"]["ma5"] == pytest.approx(3.0)
    assert out["ma"]["ma10"] is None
    assert out["ma"]["ma20"] is None
    assert len(out["daily"]) == 5
    assert out["daily"][-1]["close"] == 5.0
    assert out["sources"]["daily"] == "通达信协议"
    assert out["degraded"] == []


def test_detail_ma10_hand_calc(monkeypatch):
    _mock_all(monkeypatch, daily_n=12)
    out = sd.fetch_stock_detail("600519", date="2026-08-04")
    # close 1..12 → 最近10根 3..12 均 = 7.5
    assert out["ma"]["ma10"] == pytest.approx(7.5)
    assert out["ma"]["ma20"] is None


def test_detail_quote_failure_degrades_independently(monkeypatch):
    _mock_all(monkeypatch)

    def _boom(code):
        raise RuntimeError("mock 行情挂了")

    monkeypatch.setattr(realtime, "fetch_stock_quote", _boom)
    out = sd.fetch_stock_detail("600519", date="2026-08-04")
    assert out["quote"] is None
    assert any("实时快照" in d for d in out["degraded"])
    # 其余块不受影响
    assert len(out["daily"]) == 5
    assert len(out["minute"]) == 1


def test_detail_daily_fallback_to_tx(monkeypatch):
    _mock_all(monkeypatch)

    def _boom(code, count=250, is_index=False, market=None):
        raise RuntimeError("协议源挂了")

    monkeypatch.setattr(tdx_remote, "fetch_daily", _boom)
    monkeypatch.setattr(sd, "_records_from_tx_kline",
                        lambda symbol: _mk_daily(5))
    out = sd.fetch_stock_detail("600519", date="2026-08-04")
    assert out["sources"]["daily"] == "腾讯日K"
    assert len(out["daily"]) == 5
    assert any("日K" in d for d in out["degraded"])


def test_detail_minute_kline_date_filter_and_cap(monkeypatch):
    _mock_all(monkeypatch, kline_n=1000)
    out = sd.fetch_stock_detail("600519", date="2026-08-04")
    # 保留 ≤ 请求日期的最近 N 根（分钟K图展示最近 5 个交易日），并按容量截断
    assert all(b["date"] <= "2026-08-04" for b in out["kline_1m"])
    assert len(out["kline_1m"]) <= 5 * 240
    assert len({b["date"] for b in out["kline_1m"]}) >= 2  # 多日保留（近5交易日）
    assert all(b["date"] <= "2026-08-04" for b in out["kline_5m"])
    assert len(out["kline_5m"]) <= 5 * 48


def test_invalid_code_and_days_raise():
    with pytest.raises(sd.DataSourceError, match="6 位数字"):
        sd.fetch_stock_detail("abc", date="2026-08-04")
    with pytest.raises(sd.DataSourceError, match="days"):
        sd.fetch_stock_detail("600519", date="2026-08-04", days=3)
