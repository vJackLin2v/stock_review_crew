"""分时/分钟线与龙虎榜测试 + 超时包装测试（全离线）。"""

import struct
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stock_review_crew.tools.stock_data as sd  # noqa: E402

from test_data_common import clear_module_caches, fresh_tmp_dir  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    clear_module_caches(sd)
    monkeypatch.delenv("STOCK_DATA_CACHE_ENABLED", raising=False)
    # 本地缓存（降级链第三环）默认读仓库 data_cache/；不隔离时，测试日期的缓存块
    # （如 data_cache/2026-07-31/block_lhb.json）命中会让「全源失败→数据缺失」
    # 断言错得「本地缓存」——重定向到空临时缓存目录令该环节确定性命中失败
    # （与 PLAYBOOK 降级断言环境耦合→隔离 一致；缓存命中用例自行 setenv 种子目录）。
    import test_data_common as _tdc
    monkeypatch.setenv("STOCK_DATA_CACHE_DIR", str(_tdc.fresh_tmp_dir("lhb_iso")))


# ── 分时/分钟线 ──

def _minute_df(rows):
    return pd.DataFrame([dict(zip(("时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额"), r))
                         for r in rows])


def test_minute_em_primary(monkeypatch):
    """东财主源：bar 规范化 + end_time 过滤。"""
    called: list[str] = []

    def fake_index_minute(symbol, period, start, end):
        called.append(("index", symbol, period))
        return _minute_df([
            ("09:31", 3000.0, 3001.0, 3002.0, 2999.0, 1000, 1e8),
            ("09:32", 3001.0, 3002.0, 3003.0, 3000.0, 1200, 1.2e8),
            ("10:00", 3002.0, 3010.0, 3012.0, 3001.0, 3000, 3e8),
        ])

    monkeypatch.setattr(sd, "_em_index_minute", fake_index_minute)
    out = sd.fetch_minute_data("sh000001", "2026-07-31", period=1, end_time="09:32")
    assert out["source"] == "东财"
    assert out["bar_count"] == 2
    assert [b["time"] for b in out["bars"]] == ["09:31", "09:32"]
    assert called == [("index", "000001", "1")]
    assert out["bars"][0]["amount"] == pytest.approx(1e8)


def test_minute_stock_uses_stock_api(monkeypatch):
    """个股分时走 stock 接口（非指数）。"""
    called: list[str] = []

    def fake_stock_minute(symbol, period, start, end):
        called.append(symbol)
        return _minute_df([("09:31", 10.0, 10.1, 10.2, 9.9, 500, 5e5)])

    monkeypatch.setattr(sd, "_em_stock_minute", fake_stock_minute)
    out = sd.fetch_minute_data("600519", "2026-07-31", period=5)
    assert out["source"] == "东财"
    assert called == ["600519"]
    assert out["period"] == 5


def test_minute_tdx_fallback(monkeypatch):
    """东财失败 → 通达信本地 minline（合成夹具），degraded 可见。"""
    root = fresh_tmp_dir("min_tdx")
    d = root / "vipdoc" / "sh" / "minline"
    d.mkdir(parents=True)
    buf = b"".join(struct.pack("<HHfffffII", (2026 - 2004) * 2048 + 7 * 100 + 31,
                               hm, 3000.0, 3010.0, 2990.0, 3005.0, 1e8, 1000, 0)
                   for hm in (9 * 60 + 31, 9 * 60 + 32))
    (d / "sh000001.lc1").write_bytes(buf)
    monkeypatch.setattr(sd, "_em_index_minute",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("东财失败")))
    out = sd.fetch_minute_data("sh000001", "2026-07-31", period=1, tdx_path=str(root))
    assert out["source"] == "通达信本地"
    assert out["degraded"] is True
    assert out["bar_count"] == 2
    assert out["bars"][0]["time"] == "09:31"


def test_minute_all_fail_missing(monkeypatch):
    """全失败 → 数据缺失，不抛异常。"""
    monkeypatch.setattr(sd, "_em_index_minute",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("东财失败")))
    out = sd.fetch_minute_data("sh000001", "2026-07-31", tdx_path=r"Z:\no_such_tdx")
    assert out["source"] == "数据缺失"
    assert out["degraded"] is True
    assert out["bars"] == []
    assert any("通达信" in r for r in out["degraded_reason"])


def test_minute_invalid_period():
    with pytest.raises(sd.DataSourceError, match="周期"):
        sd.fetch_minute_data("sh000001", "2026-07-31", period=15)


# ── 龙虎榜 ──

def test_lhb_em_primary(monkeypatch):
    """东财龙虎榜：字段规范化，涨跌幅小数。"""
    monkeypatch.setattr(sd, "_em_lhb", lambda s, e: pd.DataFrame([
        {"代码": "600001", "名称": "涨停王", "收盘价": 11.0, "涨跌幅": 10.0,
         "龙虎榜买入额": 1e8, "龙虎榜卖出额": 5e7, "龙虎榜净买额": 5e7,
         "龙虎榜成交额": 1.5e8, "上榜原因": "日涨幅偏离值达7%"},
    ]))
    out = sd.fetch_lhb("2026-07-31")
    assert out["source"] == "东财"
    assert out["degraded"] is False
    assert out["count"] == 1
    row = out["stocks"][0]
    assert row["code"] == "600001"
    assert row["pct_change"] == pytest.approx(0.10)
    assert row["net_amount"] == pytest.approx(5e7)
    assert out["units"]["pct_change"].startswith("小数")


def test_lhb_tushare_fallback(monkeypatch):
    """东财失败 → Tushare top_list（万元→元换算）。"""
    monkeypatch.setattr(sd, "_em_lhb", lambda s, e: (_ for _ in ()).throw(RuntimeError("东财失败")))
    monkeypatch.setattr(sd, "_ts_top_list", lambda d: pd.DataFrame([
        {"ts_code": "600001.SH", "name": "涨停王", "close": 11.0, "pct_change": 10.0,
         "l_buy": 10000.0, "l_sell": 5000.0, "net_amount": 5000.0, "l_amount": 15000.0,
         "reason": "日涨幅偏离"},
    ]))
    monkeypatch.setattr(sd, "_ts_stock_basic", lambda: pd.DataFrame(
        {"ts_code": ["600001.SH"], "name": ["涨停王"], "list_date": ["20200101"]}))
    out = sd.fetch_lhb("2026-07-31")
    assert out["source"] == "Tushare"
    assert out["degraded"] is True
    row = out["stocks"][0]
    assert row["net_amount"] == pytest.approx(5000.0 * 10000)  # 万元 → 元
    assert row["pct_change"] == pytest.approx(0.10)
    assert row["name"] == "涨停王"


def test_lhb_all_fail_missing(monkeypatch):
    monkeypatch.setattr(sd, "_em_lhb", lambda s, e: (_ for _ in ()).throw(RuntimeError("东财失败")))
    monkeypatch.setattr(sd, "_ts_top_list", lambda d: (_ for _ in ()).throw(
        sd.DataSourceError("未配置 TUSHARE_TOKEN")))
    out = sd.fetch_lhb("2026-07-31")
    assert out["source"] == "数据缺失"
    assert out["count"] is None
    assert out["stocks"] == []


# ── 超时包装 ──

def test_source_timeout_raises_chinese():
    """单源超时 → 中文错误，且不阻塞进程退出。"""
    def _slow():
        time.sleep(60)
        return 1
    start = time.monotonic()
    with pytest.raises(sd.DataSourceError, match="超时"):
        sd._call_with_timeout(_slow, timeout=0.3)
    assert time.monotonic() - start < 5


def test_source_timeout_degrades_chain(monkeypatch):
    """超时源计入降级链（reason 含「超时」），最终走缺失标注。"""
    monkeypatch.setattr(sd, "SOURCE_TIMEOUT_SECONDS", 1)

    def _slow_zt(date):
        time.sleep(60)
        return pd.DataFrame()

    monkeypatch.setattr(sd, "_em_zt_pool", _slow_zt)
    monkeypatch.setattr(sd, "_em_zt_pool_zbgc",
                        lambda d: (_ for _ in ()).throw(RuntimeError("备用失败")))
    monkeypatch.setattr(sd, "_em_zt_pool_dtgc",
                        lambda d: (_ for _ in ()).throw(RuntimeError("备用失败")))
    monkeypatch.setattr(sd, "_zt_pool_from_ths",
                        lambda d: (_ for _ in ()).throw(RuntimeError("同花顺失败（离线）")))
    monkeypatch.setattr(sd, "_ts_daily", lambda d: (_ for _ in ()).throw(
        sd.DataSourceError("未配置 TUSHARE_TOKEN")))
    start = time.monotonic()
    out = sd.fetch_zt_pool("2026-07-31")
    assert out["source"] == "数据缺失"
    assert time.monotonic() - start < 10, "整体耗时不应等待慢源完成"
    assert any("超时" in r for r in out["degraded_reason"])
