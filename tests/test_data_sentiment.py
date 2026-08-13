"""情绪指标测试（契约 §六.5）：昨日涨停今日表现/炸板率/涨跌家数比。

口径：主板 10%、创业/科创 20%，过滤 ST/北证；比率一律小数；raw 保留原值并标注单位。
"""

import struct
import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stock_review_crew.tools.stock_data as sd  # noqa: E402

from test_data_common import clear_module_caches, fresh_tmp_dir, make_ts_daily_df  # noqa: E402


REVIEW_DATE = "2026-07-31"
PREV_DATE = "2026-07-30"


def _basic_df() -> pd.DataFrame:
    names = {
        "600001": "涨停王", "600002": "差一分", "600003": "ST某某", "300001": "创业牛",
        "688001": "科创差", "830001": "北证股", "600004": "炸板股", "600005": "封板股",
        "600006": "下跌股", "600007": "微涨股", "600008": "平盘股", "300002": "创业封",
        "830002": "北证二", "600009": "ST今日",
    }
    return pd.DataFrame({"ts_code": list(names), "name": list(names.values()),
                         "list_date": ["20200101"] * len(names)})


def _prev_daily() -> pd.DataFrame:
    rows = [
        {"ts_code": "600001", "pre_close": 10.0, "open": 10.5, "high": 11.01,
         "low": 10.4, "close": 11.01, "pct_chg": 10.1, "vol": 1000, "amount": 1e7},
        {"ts_code": "600002", "pre_close": 10.0, "open": 10.2, "high": 10.99,
         "low": 10.1, "close": 10.99, "pct_chg": 9.9, "vol": 1000, "amount": 1e7},
        {"ts_code": "600003", "pre_close": 10.0, "open": 10.0, "high": 11.01,
         "low": 10.0, "close": 11.01, "pct_chg": 10.1, "vol": 1000, "amount": 1e7},  # ST
        {"ts_code": "300001", "pre_close": 10.0, "open": 11.0, "high": 12.0,
         "low": 10.9, "close": 12.0, "pct_chg": 20.0, "vol": 1000, "amount": 1e7},
        {"ts_code": "688001", "pre_close": 10.0, "open": 10.5, "high": 11.9,
         "low": 10.4, "close": 11.9, "pct_chg": 19.0, "vol": 1000, "amount": 1e7},  # 未达20%
        {"ts_code": "830001", "pre_close": 10.0, "open": 11.0, "high": 12.0,
         "low": 10.9, "close": 12.0, "pct_chg": 20.0, "vol": 1000, "amount": 1e7},  # 北证
    ]
    return make_ts_daily_df(rows)


def _today_daily() -> pd.DataFrame:
    rows = [
        {"ts_code": "600001", "pre_close": 11.01, "open": 11.5, "high": 12.11,
         "low": 11.4, "close": 12.11, "pct_chg": 10.0, "vol": 2000, "amount": 2e7},
        {"ts_code": "300001", "pre_close": 12.0, "open": 12.2, "high": 12.5,
         "low": 12.1, "close": 12.5, "pct_chg": 4.2, "vol": 2000, "amount": 2e7},
        {"ts_code": "600004", "pre_close": 10.0, "open": 10.8, "high": 11.0,
         "low": 10.2, "close": 10.5, "pct_chg": -2.0, "vol": 2000, "amount": 2e7},  # 摸板未封
        {"ts_code": "600005", "pre_close": 10.0, "open": 10.9, "high": 11.01,
         "low": 10.8, "close": 11.01, "pct_chg": 10.0, "vol": 2000, "amount": 2e7},  # 封板
        {"ts_code": "600006", "pre_close": 10.0, "open": 9.9, "high": 10.0,
         "low": 9.8, "close": 9.9, "pct_chg": -1.0, "vol": 2000, "amount": 2e7},
        {"ts_code": "600007", "pre_close": 10.0, "open": 10.0, "high": 10.1,
         "low": 9.9, "close": 10.05, "pct_chg": 0.5, "vol": 2000, "amount": 2e7},
        {"ts_code": "600008", "pre_close": 10.0, "open": 10.0, "high": 10.0,
         "low": 10.0, "close": 10.0, "pct_chg": 0.0, "vol": 2000, "amount": 2e7},
        {"ts_code": "300002", "pre_close": 10.0, "open": 11.5, "high": 12.01,
         "low": 11.4, "close": 12.01, "pct_chg": 20.0, "vol": 2000, "amount": 2e7},  # 创业封板
        {"ts_code": "830002", "pre_close": 10.0, "open": 11.0, "high": 12.0,
         "low": 10.9, "close": 11.99, "pct_chg": 19.9, "vol": 2000, "amount": 2e7},  # 北证
        {"ts_code": "600009", "pre_close": 10.0, "open": 10.9, "high": 11.01,
         "low": 10.8, "close": 11.01, "pct_chg": 10.0, "vol": 2000, "amount": 2e7},  # ST今日
    ]
    return make_ts_daily_df(rows)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    clear_module_caches(sd)


def test_sentiment_tushare_precise(monkeypatch):
    """Tushare 精确口径：涨停规则/ST/北证过滤/比率小数/raw 单位。"""
    monkeypatch.setattr(sd, "_ts_daily", lambda d: _prev_daily() if d == "20260730" else _today_daily())
    monkeypatch.setattr(sd, "_ts_stock_basic", _basic_df)
    out = sd.fetch_sentiment(REVIEW_DATE)
    assert out["source"] == "Tushare计算"
    assert out["degraded"] is False
    assert out["yesterday_zt_count"] == 2          # 600001 + 300001（ST/北证/差一分被过滤）
    assert out["matched_today"] == 2
    assert out["avg_return"] == pytest.approx(0.071)      # (10.0+4.2)/2 %
    assert out["red_rate"] == pytest.approx(1.0)
    assert out["lianban_count"] == 1                       # 600001 继续涨停
    assert out["lianban_rate"] == pytest.approx(0.5)
    assert out["hean_count"] == 0
    assert out["hean_rate"] == pytest.approx(0.0)
    # 炸板率 = 炸板 ÷ 摸板（今日全市场口径）
    assert out["touched"] == 4     # 600001/600004/600005/300002（600001 触及当日涨停价）
    assert out["zhaban"] == 1      # 600004
    assert out["zhaban_rate"] == pytest.approx(0.25)
    # 涨跌家数（ST/北证已过滤）
    assert out["up_count"] == 5
    assert out["down_count"] == 2
    assert out["up_down_ratio"] == pytest.approx(2.5)
    # raw 保留原值并标注单位
    assert out["raw"]["avg_return_pct"] == pytest.approx(7.1)
    assert out["raw"]["单位"] == "%"
    # 明细行名称来自 stock_basic
    codes = {r["code"] for r in out["best3"] + out["worst3"]}
    assert codes <= {"600001", "300001"}
    assert out["best3"][0]["name"] == "涨停王"


def test_sentiment_em_fallback(monkeypatch):
    """Tushare 失败 → 东财（昨日涨停池 + 实时快照），degraded 可见。"""
    monkeypatch.setattr(sd, "_sentiment_from_realtime", lambda date: (_ for _ in ()).throw(
        sd.DataSourceError("离线测试禁用盘中实时")))
    monkeypatch.setattr(sd, "_ts_daily", lambda d: (_ for _ in ()).throw(
        sd.DataSourceError("未配置 TUSHARE_TOKEN，Tushare 数据源不可用（走降级链）")))
    from test_data_common import make_zt_pool_df
    pool = make_zt_pool_df([
        {"代码": "600519", "名称": "贵州茅台", "成交额": 9e9},
        {"代码": "300750", "名称": "宁德时代", "成交额": 8e9},
    ])
    monkeypatch.setattr(sd, "_em_zt_pool_previous", lambda date: pool)
    today = sd._now().strftime("%Y-%m-%d")
    spot = pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "昨收": 1500.0, "最新价": 1575.0, "涨跌幅": 5.0},
        {"代码": "300750", "名称": "宁德时代", "昨收": 200.0, "最新价": 240.0, "涨跌幅": 20.0},
    ])
    monkeypatch.setattr(sd, "_em_spot", lambda: spot)
    out = sd.fetch_sentiment(today)
    assert out["source"] == "东财"
    assert out["degraded"] is True
    assert out["matched_today"] == 2
    assert out["avg_return"] == pytest.approx(0.125)
    assert out["red_rate"] == pytest.approx(1.0)
    assert out["lianban_count"] == 1   # 300750 达到 20% 涨停
    assert out["lianban_rate"] == pytest.approx(0.5)


def test_sentiment_tdx_fallback(monkeypatch):
    """通达信本地兜底：日线文件计算，估算标注。"""
    monkeypatch.setattr(sd, "_sentiment_from_realtime", lambda date: (_ for _ in ()).throw(
        sd.DataSourceError("离线测试禁用盘中实时")))
    monkeypatch.setattr(sd, "_sentiment_from_em", lambda date: (_ for _ in ()).throw(
        sd.DataSourceError("离线测试禁用东财推算")))
    monkeypatch.setattr(sd, "_sentiment_from_ths", lambda date: (_ for _ in ()).throw(
        sd.DataSourceError("离线测试禁用同花顺")))
    root = fresh_tmp_dir("sent_tdx")
    for market, code, closes in (
        ("sh", "600001", (10.0, 11.0, 12.1)),   # 昨涨停 → 今 +10%
        ("sz", "300001", (20.0, 21.0, 20.0)),   # 昨未涨停（20% 上限 24）
    ):
        d = root / "vipdoc" / market / "lday"
        d.mkdir(parents=True, exist_ok=True)
        buf = b""
        for ymd, c in ((20260729, closes[0]), (20260730, closes[1]), (20260731, closes[2])):
            buf += struct.pack("<IIIIIfII", ymd, int(c * 100), int(c * 100),
                               int(c * 100), int(c * 100), 1e8, 1000, 0)
        (d / f"{market}{code}.day").write_bytes(buf)
    out = sd.fetch_sentiment(REVIEW_DATE, tdx_path=str(root))
    assert out["source"] == "通达信本地"
    assert out["degraded"] is True
    assert out["yesterday_zt_count"] == 1
    assert out["matched_today"] == 1
    assert out["avg_return"] == pytest.approx(0.10)
    assert out["lianban_count"] == 1
    assert out["lianban_rate"] == pytest.approx(1.0)
    assert out["up_count"] == 1 and out["down_count"] == 1
    assert any("无法过滤ST" in r for r in out["degraded_reason"])


def test_sentiment_all_fail_missing(monkeypatch):
    """全部失败：缺失块，None 占位（禁 0）。"""
    monkeypatch.setattr(sd, "_sentiment_from_realtime", lambda date: (_ for _ in ()).throw(
        sd.DataSourceError("离线测试禁用盘中实时")))
    monkeypatch.setattr(sd, "_sentiment_from_em", lambda date: (_ for _ in ()).throw(
        sd.DataSourceError("离线测试禁用东财推算")))
    monkeypatch.setattr(sd, "_sentiment_from_ths", lambda date: (_ for _ in ()).throw(
        sd.DataSourceError("离线测试禁用同花顺")))
    monkeypatch.setattr(sd, "_ts_daily", lambda d: (_ for _ in ()).throw(sd.DataSourceError("无 token")))
    monkeypatch.setattr(sd, "_em_zt_pool_previous", lambda date: pd.DataFrame())
    monkeypatch.setattr(sd, "_em_spot", lambda: pd.DataFrame())
    monkeypatch.setattr(sd.tdx_local, "iter_day_codes", lambda tdx_path=None: iter(()))
    monkeypatch.setattr(sd, "_cache_load_block", lambda date, block: None)
    out = sd.fetch_sentiment(REVIEW_DATE)
    assert out["source"] == "数据缺失"
    assert out["degraded"] is True
    assert out["avg_return"] is None
    assert out["red_rate"] is None
    assert out["lianban_rate"] is None
    assert out["zhaban_rate"] is None


def test_sentiment_missing_today_data(monkeypatch):
    """今日（盘中）无 Tushare 日线 → 链上东财，而不是错报 0。"""
    monkeypatch.setattr(sd, "_sentiment_from_realtime", lambda date: (_ for _ in ()).throw(
        sd.DataSourceError("离线测试禁用盘中实时")))
    monkeypatch.setattr(sd, "_ts_daily", lambda d: _prev_daily() if d == "20260730" else pd.DataFrame())
    monkeypatch.setattr(sd, "_ts_stock_basic", _basic_df)
    from test_data_common import make_zt_pool_df
    monkeypatch.setattr(sd, "_em_zt_pool_previous", lambda date: make_zt_pool_df(
        [{"代码": "600001", "名称": "涨停王", "成交额": 1e8}]))
    today = sd._now().strftime("%Y-%m-%d")
    monkeypatch.setattr(sd, "_em_spot", lambda: pd.DataFrame(
        [{"代码": "600001", "名称": "涨停王", "昨收": 11.01, "最新价": 12.0, "涨跌幅": 9.0}]))
    out = sd.fetch_sentiment(today)
    assert out["source"] == "东财"
    assert out["avg_return"] == pytest.approx(0.09)
