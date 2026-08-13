"""tdx_remote（通达信协议远程源）单元测试：归一化/市场判定/全挂降级（全离线，注入假 tdxpy）。"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

import stock_review_crew.tools.tdx_remote as tr


class FakeAPI:
    """假通达信协议客户端：返回固定行情/K线，记录连接。"""

    def __init__(self):
        self.connected: list[tuple[str, int]] = []

    def connect(self, host, port, time_out=5):
        self.connected.append((host, port))
        return True

    def setup(self):
        return None

    def disconnect(self):
        return None

    def get_security_quotes(self, pairs):
        return [{
            "code": pairs[0][1], "price": 10.5, "last_close": 10.0, "open": 10.1,
            "high": 10.8, "low": 10.0, "vol": 1000, "amount": 1e7,
            "servertime": "15:00:00", "bid1": 10.4, "ask1": 10.6,
            "bid_vol1": 5, "ask_vol1": 6,
        }]

    def get_security_bars(self, category, market, code, start, count):
        return [{
            "datetime": "2026-08-04 14:51", "open": 1, "high": 2, "low": 0.5,
            "close": 1.5, "vol": 100, "amount": 10000,
        }] * min(count, 3)

    def get_index_bars(self, category, market, code, start, count):
        return [{
            "datetime": "2026-08-04 15:00", "open": 1, "high": 2, "low": 0.5,
            "close": 1.5, "vol": 100, "amount": 10000,
            "up_count": 100, "down_count": 20,
        }] * min(count, 3)


def _inject(monkeypatch, api_cls=FakeAPI, servers=None):
    fake = types.ModuleType("tdxpy")
    fake_hq = types.ModuleType("tdxpy.hq")
    fake_hq.TdxHq_API = api_cls
    fake.hq = fake_hq
    monkeypatch.setitem(sys.modules, "tdxpy", fake)
    monkeypatch.setitem(sys.modules, "tdxpy.hq", fake_hq)
    monkeypatch.setattr(tr, "_SERVERS", servers or [("127.0.0.1", 7709)])


@pytest.fixture(autouse=True)
def fake_tdxpy(monkeypatch):
    _inject(monkeypatch)


def test_available_true_when_tdxpy_injected():
    assert tr.available() is True


def test_market_of():
    assert tr._market_of("600519") == 1
    assert tr._market_of("000001") == 0
    assert tr._market_of("002466") == 0
    assert tr._market_of("830799") == 2
    assert tr._market_of("688001") == 1


def test_to_float_rejects_nan():
    assert tr._to_float("nan") is None
    assert tr._to_float(float("nan")) is None
    assert tr._to_float("8.65") == 8.65


def test_fetch_quote_normalized():
    q = tr.fetch_quote("600519")
    assert q["code"] == "600519"
    assert q["price"] == 10.5
    assert q["pct_change"] == pytest.approx(0.05)  # (10.5-10.0)/10.0，小数
    assert q["pre_close"] == 10.0
    assert q["source"] == "通达信协议"
    assert q["units"]["pct_change"].startswith("小数")


def test_etf_price_scale_normalized(monkeypatch):
    """通达信协议对 5xx 基金/ETF 价格按 ×10 返回，须归一为元（实测 0.865→8.65）。"""

    class ETFAPI(FakeAPI):
        def get_security_quotes(self, pairs):
            return [{"code": pairs[0][1], "price": 8.65, "last_close": 8.29,
                     "open": 8.36, "high": 8.82, "low": 8.24, "vol": 1000, "amount": 1e7,
                     "servertime": "15:00:00", "bid1": 8.64, "ask1": 8.66,
                     "bid_vol1": 5, "ask_vol1": 6}]

        def get_security_bars(self, category, market, code, start, count):
            return [{"datetime": "2026-08-04 14:51", "open": 8.36, "high": 8.82,
                     "low": 8.24, "close": 8.65, "vol": 100, "amount": 10000}] * min(count, 3)

    _inject(monkeypatch, ETFAPI)
    q = tr.fetch_quote("588170")
    assert q["price"] == pytest.approx(0.865)
    assert q["pre_close"] == pytest.approx(0.829)
    assert q["open"] == pytest.approx(0.836)
    assert q["bid1"] == pytest.approx(0.864)
    assert q["ask1"] == pytest.approx(0.866)
    bar = tr.fetch_minute_kline("588170", count=2, category=8)[0]
    assert bar["close"] == pytest.approx(8.65)  # K线价格已是真实元值，不缩放
    # 非 ETF 价格不受 scale 影响
    _inject(monkeypatch, FakeAPI)
    q2 = tr.fetch_quote("600519")
    assert q2["price"] == 10.5


def test_fetch_daily_records():
    bars = tr.fetch_daily("600519", count=3)
    assert len(bars) == 3
    b = bars[0]
    assert b["date"] == "2026-08-04" and b["time"] == "14:51"
    assert b["close"] == 1.5 and b["amount"] == 10000


def test_fetch_index_daily_keeps_counts(monkeypatch):
    seen: dict = {}

    class RecFakeAPI(FakeAPI):
        def get_index_bars(self, category, market, code, start, count):
            seen["market"] = market
            return super().get_index_bars(category, market, code, start, count)

    _inject(monkeypatch, RecFakeAPI)
    bars = tr.fetch_daily("000001", count=2, is_index=True)  # 上证指数须走沪市 market=1
    assert bars[0]["up_count"] == 100 and bars[0]["down_count"] == 20
    assert seen["market"] == 1
    seen.clear()
    bars = tr.fetch_daily("399006", count=2, is_index=True, market=0)  # 创业板指深市
    assert seen["market"] == 0


def test_fetch_minute_kline():
    bars = tr.fetch_minute_kline("002466", count=5, category=8)
    assert len(bars) == 3  # 假客户端最多返回 3 根
    assert bars[0]["datetime"] == "2026-08-04 14:51"


def test_all_servers_fail_raises_chinese(monkeypatch):
    class BadAPI:
        def connect(self, host, port, time_out=5):
            return False

        def disconnect(self):
            return None

    _inject(monkeypatch, BadAPI, servers=[("1.2.3.4", 7709), ("5.6.7.8", 7709)])
    with pytest.raises(tr.TdxRemoteError, match="全部不可用"):
        tr.fetch_quote("600519")


def test_available_false_without_tdxpy(monkeypatch):
    monkeypatch.delitem(sys.modules, "tdxpy", raising=False)
    monkeypatch.delitem(sys.modules, "tdxpy.hq", raising=False)
    assert tr.available() is False
    with pytest.raises(tr.TdxRemoteError, match="未安装"):
        tr.fetch_quote("600519")
