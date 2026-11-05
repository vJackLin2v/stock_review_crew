"""6 模式取数节点 + @tool 包装测试（全离线、全 mock）。"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stock_review_crew.tools.stock_data as sd  # noqa: E402

from test_data_common import clear_module_caches, make_ts_daily_df, make_zt_pool_df  # noqa: E402


TODAY = sd._now().strftime("%Y-%m-%d")


def _tx_daily(symbol):
    rows = []
    for i in range(70):
        d = (sd._now() - __import__("datetime").timedelta(days=70 - i)).strftime("%Y-%m-%d")
        rows.append({"date": d, "open": 3000 + i, "high": 3010 + i, "low": 2990 + i,
                     "close": 3005 + i, "amount": 5e11 + i * 1e8, "volume": 5e8 + i})
    return pd.DataFrame(rows)


def _em_zt(date):
    return make_zt_pool_df([
        {"代码": "600001", "名称": "涨停王", "涨跌幅": 10.0, "最新价": 11.0, "涨停价": 11.0,
         "换手率": 5.0, "连板数": 1, "所属行业": "软件", "成交额": 1e8, "封板资金": 5e7},
        {"代码": "300001", "名称": "创业牛", "涨跌幅": 20.0, "最新价": 12.0, "涨停价": 12.0,
         "换手率": 8.0, "连板数": 2, "所属行业": "半导体", "成交额": 2e8, "封板资金": 1e8},
    ])


def _em_zbgc(date):
    return make_zt_pool_df([
        {"代码": "600004", "名称": "炸板股", "涨跌幅": 6.0, "最新价": 10.6, "涨停价": 11.0,
         "换手率": 10.0, "连板数": 0, "所属行业": "传媒", "成交额": 9e7, "炸板次数": 2},
    ])


def _em_dtgc(date):
    return make_zt_pool_df([
        {"代码": "600006", "名称": "跌停股", "涨跌幅": -10.0, "最新价": 9.0, "跌停价": 9.0,
         "换手率": 3.0, "成交额": 1e7},
    ])


def _ts_daily_ok(trade_date):
    if trade_date == TODAY.replace("-", ""):
        return make_ts_daily_df([
            {"ts_code": "600001", "pre_close": 10.0, "open": 10.5, "high": 11.0,
             "low": 10.4, "close": 11.0, "pct_chg": 10.0, "vol": 1000, "amount": 1e7},
            {"ts_code": "300001", "pre_close": 10.0, "open": 10.5, "high": 11.0,
             "low": 10.4, "close": 10.5, "pct_chg": 5.0, "vol": 1000, "amount": 1e7},
        ])
    if trade_date == "20260731":
        return make_ts_daily_df([
            {"ts_code": "600001", "pre_close": 9.0, "open": 9.5, "high": 10.0,
             "low": 9.4, "close": 10.0, "pct_chg": 11.1, "vol": 1000, "amount": 1e7},
        ])
    return pd.DataFrame()


def _ts_basic():
    return pd.DataFrame({"ts_code": ["600001", "300001"], "name": ["涨停王", "创业牛"],
                         "list_date": ["20200101", "20200101"]})


def _ths_summary():
    return pd.DataFrame([
        {"板块": "软件", "涨跌幅": 3.2, "总成交额": 1e10, "净流入": 5e8,
         "上涨家数": 50, "下跌家数": 5, "领涨股": "软件A", "领涨股-涨跌幅": 10.0},
        {"板块": "半导体", "涨跌幅": 2.5, "总成交额": 2e10, "净流入": 3e8,
         "上涨家数": 40, "下跌家数": 8, "领涨股": "芯片B", "领涨股-涨跌幅": 8.0},
        {"板块": "银行", "涨跌幅": -0.5, "总成交额": 5e9, "净流入": -1e8,
         "上涨家数": 10, "下跌家数": 30, "领涨股": "银行C", "领涨股-涨跌幅": 1.0},
        {"板块": "煤炭", "涨跌幅": -1.2, "总成交额": 3e9, "净流入": -2e8,
         "上涨家数": 3, "下跌家数": 33, "领涨股": "煤D", "领涨股-涨跌幅": 0.5},
    ])


def _em_index_minute(symbol, period, start, end):
    rows = []
    for hm in ("09:31", "09:32", "10:00", "11:30", "13:01", "14:00", "15:00"):
        rows.append({"时间": hm, "开盘": 3000.0, "收盘": 3001.0, "最高": 3002.0,
                     "最低": 2999.0, "成交量": 1000, "成交额": 1e8})
    return pd.DataFrame(rows)


def _em_lhb(start, end):
    return pd.DataFrame([
        {"代码": "600001", "名称": "涨停王", "收盘价": 11.0, "涨跌幅": 10.0,
         "龙虎榜买入额": 1e8, "龙虎榜卖出额": 5e7, "龙虎榜净买额": 5e7,
         "龙虎榜成交额": 1.5e8, "上榜原因": "日涨幅偏离值达7%"},
    ])


def _cctv(date):
    return pd.DataFrame({"title": ["央视要闻一", "央视要闻二"]})


def _eco(date):
    return pd.DataFrame({"时间": ["10:00"], "地区": ["中国"], "事件": ["CPI"],
                         "重要性": ["3"]})


def _caixin():
    return pd.DataFrame({"tag": ["宏观"], "summary": ["财新头条摘要"]})


def _us_index(symbol):
    return pd.DataFrame({"date": ["2026-08-01", "2026-08-02"], "close": [18000.0, 18100.0]})


def _foreign(symbols):
    return pd.DataFrame({"最新价": [13000.0], "涨跌幅": [0.5]})


def _futures(symbol):
    return pd.DataFrame({"close": [4000.0], "volume": [1000], "hold": [5000]})


def _mock_all_success(monkeypatch):
    monkeypatch.setattr(sd.config, "KAIPANLA_COOKIE", "test=1")
    # 2026-08-14：固定 _now 到当日 14:00（盘中），消除 test_mode_blocks_all_success 的
    # 时间依赖——凌晨跑时 _mode_end_time 会把分时 end_time 落到开盘前，导致分钟块降级、测试失败。
    from datetime import datetime, time as _time

    _fixed_now = datetime.combine(datetime.strptime(TODAY, "%Y-%m-%d").date(), _time(14, 0))
    monkeypatch.setattr(sd, "_now", lambda: _fixed_now)
    import stock_review_crew.tools.realtime as rt_mod

    monkeypatch.setattr(rt_mod, "fetch_indices", lambda: {"indices": []})
    monkeypatch.setattr(rt_mod, "fetch_breadth_realtime",
                        lambda: {"total": 4600, "up": 2800, "down": 1800, "flat": 0})

    def _sectors_block():
        return {
            "top5": [{
                "name": "半导体", "pct_change": 0.032, "amount": 1e9,
                "leading_stock": "示例科技",
                "raw": {"板块": "半导体", "涨跌幅": 3.2, "成交额": 1e9, "领涨股": "示例科技"},
            }],
            "bottom5": [{
                "name": "保险", "pct_change": -0.03, "amount": 1e8,
                "leading_stock": "示例保险",
                "raw": {"板块": "保险", "涨跌幅": -3.0, "成交额": 1e8, "领涨股": "示例保险"},
            }],
            "bottom5_flow": [],
            "units": sd._SECTOR_UNITS,
            "source": "同花顺", "degraded": False, "degraded_reason": [],
        }

    for _n in ("_sectors_from_ths_today", "_sectors_from_tx_today",
               "_sectors_from_browser_today", "_sectors_from_em_today"):
        monkeypatch.setattr(sd, _n, _sectors_block)
    monkeypatch.setattr(sd, "_futures_from_ak", lambda: {"IH": None, "IM": None})
    monkeypatch.setattr(sd, "_tx_index_daily", _tx_daily)
    monkeypatch.setattr(sd, "_em_zt_pool", _em_zt)
    monkeypatch.setattr(sd, "_em_zt_pool_zbgc", _em_zbgc)
    monkeypatch.setattr(sd, "_em_zt_pool_dtgc", _em_dtgc)
    monkeypatch.setattr(sd, "_ts_daily", _ts_daily_ok)
    monkeypatch.setattr(sd, "_ts_stock_basic", _ts_basic)
    monkeypatch.setattr(sd, "_ths_sector_summary", _ths_summary)
    monkeypatch.setattr(sd, "_em_index_minute", _em_index_minute)
    monkeypatch.setattr(sd, "_em_lhb", _em_lhb)
    monkeypatch.setattr(sd, "_news_cctv", _cctv)
    monkeypatch.setattr(sd, "_news_economic_baidu", _eco)
    monkeypatch.setattr(sd, "_news_caixin", _caixin)
    monkeypatch.setattr(sd, "_us_index_sina", _us_index)
    monkeypatch.setattr(sd, "_foreign_realtime", _foreign)
    monkeypatch.setattr(sd, "_futures_main_contract", _futures)
    monkeypatch.setattr(sd, "_http_get_json",
                        lambda url, params=None, headers=None, timeout=15:
                        [{"代码": "600001", "名称": "涨停王", "竞价涨幅": 1.0, "竞价金额": 1e8}])
    # 2026-08-10 改动：fetch_sentiment 降级链 Tushare 优先（_sentiment_from_tushare 为首入口），
    # 必须 mock，否则走真实网络（假代理下挂起）
    monkeypatch.setattr(sd, "_sentiment_from_tushare", lambda date, prev_date=None: {
        "yesterday": "2026-08-02", "today": date,
        "yesterday_zt_count": 60, "matched_today": 50,
        "avg_return": 0.10, "median_return": 0.08, "red_rate": 0.55,
        "lianban_count": 5, "lianban_rate": 0.1,
        "hean_count": 2, "hean_rate": 0.04,
        "best3": [], "worst3": [],
        "zhaban": 3, "touched": 10, "zhaban_rate": 0.3,
        "up_count": 2800, "down_count": 1800, "up_down_ratio": 1.5556,
        "raw": {"avg_return_pct": 10.0, "red_rate_pct": 55.0,
                "lianban_rate_pct": 10.0, "hean_rate_pct": 4.0, "单位": "%"},
        "source": "Tushare计算", "degraded": False, "degraded_reason": [],
    })


def _mock_all_fail(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("模拟离线失败")
    # 通达信本地兜底也禁用：与 .env TDX_PATH / F:\tdx 实际存在无关
    import stock_review_crew.config as cfg
    import stock_review_crew.tools.realtime as rt_mod

    monkeypatch.setattr(cfg, "TDX_PATH", None)
    monkeypatch.setattr("stock_review_crew.tools.realtime.fetch_news", _raise)
    monkeypatch.setattr(rt_mod, "fetch_indices", _raise)
    monkeypatch.setattr(rt_mod, "fetch_breadth_realtime", _raise)
    for name in ("_tx_index_daily", "_em_zt_pool", "_em_zt_pool_zbgc", "_em_zt_pool_dtgc",
                 "_ts_daily", "_ts_stock_basic", "_ths_sector_summary", "_em_index_minute",
                 "_em_lhb", "_news_cctv", "_news_economic_baidu", "_news_caixin",
                 "_us_index_sina", "_foreign_realtime", "_futures_main_contract",
                 "_futures_from_ak", "_legu_activity", "_http_get_json",
                 "_em_zt_pool_previous", "_em_spot",
                 "_em_pre_min", "_em_stock_daily", "_em_sector_names", "_ths_sector_names",
                 "_em_sector_hist", "_ths_sector_hist", "_zt_pool_from_ths",
                 "_sentiment_from_tushare", "_sentiment_from_ths",
                 "_sentiment_from_realtime", "_sentiment_from_em",
                 "_sectors_from_ths_today", "_sectors_from_tx_today",
                 "_sectors_from_browser_today", "_sectors_from_em_today",
                 "_records_from_em_kline",
                 "_records_from_tx_kline", "_minute_bars_from_tx", "_sectors_from_tx_today"):
        monkeypatch.setattr(sd, name, _raise, raising=False)
    monkeypatch.setattr(sd, "get_cache_dir", lambda: Path(__file__).parent / "no_such_cache")


EXPECTED_BLOCKS = {
    "pre_market": {"index_trend", "macro", "news"},
    "auction": {"auction"},
    "intraday_am": {"index_trend", "minute", "sectors", "zt_pool", "breadth", "sentiment"},
    "noon": {"index_trend", "minute", "sectors", "zt_pool", "breadth", "sentiment"},
    "intraday_pm": {"index_trend", "minute", "sectors", "zt_pool", "breadth", "sentiment"},
    "close": {"index_trend", "minute", "zt_pool", "sectors", "breadth", "sentiment", "lhb", "news"},
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    clear_module_caches(sd)
    monkeypatch.delenv("STOCK_DATA_CACHE_ENABLED", raising=False)
    monkeypatch.delenv("STOCK_DATA_CACHE_DIR", raising=False)


@pytest.mark.parametrize("mode", sorted(EXPECTED_BLOCKS))
def test_mode_blocks_all_success(monkeypatch, mode):
    _mock_all_success(monkeypatch)
    out = sd.fetch_mode_data(mode, TODAY)
    assert out["mode"] == mode
    assert out["mode_label"] in sd.MODE_LABELS.values()
    assert set(out["blocks"]) == EXPECTED_BLOCKS[mode]
    assert out["degraded_flag"] is False
    for name, blk in out["blocks"].items():
        assert "source" in blk, name
        assert "degraded" in blk, name
        assert blk["degraded"] is False, f"{name}: {blk.get('degraded_reason')}"


@pytest.mark.parametrize("mode", sorted(EXPECTED_BLOCKS))
def test_mode_all_fail_degrades_visibly(monkeypatch, mode):
    """全部源失败：每个块标注降级/缺失，流程不抛异常。"""
    _mock_all_fail(monkeypatch)
    out = sd.fetch_mode_data(mode, TODAY)
    assert out["mode"] == mode
    assert out["degraded_flag"] is True
    assert out["degraded"], "降级原因必须可见"
    for name, blk in out["blocks"].items():
        assert blk["degraded"] is True, name
        assert blk["degraded_reason"], name
        assert blk["source"] in ("数据缺失", "本地缓存", "新浪/东财", "财新/央视/经济日历"), \
            f"{name}: {blk['source']}"


def test_invalid_mode_raises_chinese():
    with pytest.raises(ValueError, match="无效模式"):
        sd.fetch_mode_data("bad_mode", TODAY)


def test_invalid_date_raises_chinese():
    with pytest.raises(sd.DataSourceError, match="无效日期"):
        sd.fetch_index_trend("2026/07/31")


def test_index_partial_shenzhen_missing_ok(monkeypatch):
    """2026-08-13：深证成指加回取数（INDEX_CODES 含 shenzhen=sz399001）。
    深证成指（非必需）全链失败时保留键并置 None（「无数据」，绝不静默）；
    必需指数（上证/创业板/科创50）必须保障，走备用链并降级标注。"""

    def _tx_daily_partial(symbol):
        if symbol == "sz399001":  # 深证成指（非必需）失败
            raise RuntimeError("深证源不可用（mock）")
        return _tx_daily(symbol)

    monkeypatch.setattr(sd, "_tx_index_daily", _tx_daily_partial)
    monkeypatch.setattr(sd, "_records_from_em_kline", lambda symbol: None)
    monkeypatch.setattr(sd, "_records_from_tx_kline", lambda symbol: None)
    monkeypatch.setattr(sd, "_records_from_tdx_remote",
                        lambda symbol: (_ for _ in ()).throw(RuntimeError("协议源离线（mock）")))
    monkeypatch.setattr(sd, "get_cache_dir", lambda: Path(__file__).parent / "no_such_cache")
    import stock_review_crew.config as cfg

    monkeypatch.setattr(cfg, "TDX_PATH", None)

    out = sd.fetch_index_trend("2026-07-31")
    assert out["source"] != "数据缺失"
    ind = out["indices"]
    assert ind["shanghai"] is not None
    assert ind["chuangye"] is not None
    assert ind["kechuang"] is not None
    assert "shenzhen" in ind
    assert ind["shenzhen"] is None  # 深证缺失 → 键保留、值为 None（「无数据」）
    assert out["degraded"] is True  # 深证主源失败 → 走备用链并降级标注


def test_index_required_missing_still_data_missing(monkeypatch):
    """创业板（必需）缺失 → 指数块仍判「数据缺失」（复盘 R7 终止口径不变）。"""

    def _tx_daily_partial2(symbol):
        if symbol == "sz399006":  # 创业板指（必需）失败
            raise RuntimeError("创业板源不可用（mock）")
        return _tx_daily(symbol)

    monkeypatch.setattr(sd, "_tx_index_daily", _tx_daily_partial2)
    monkeypatch.setattr(sd, "_records_from_em_kline", lambda symbol: None)
    monkeypatch.setattr(sd, "_records_from_tx_kline", lambda symbol: None)
    monkeypatch.setattr(sd, "get_cache_dir", lambda: Path(__file__).parent / "no_such_cache")
    import stock_review_crew.config as cfg

    monkeypatch.setattr(cfg, "TDX_PATH", None)

    out = sd.fetch_index_trend("2026-07-31")
    assert out["source"] == "数据缺失"
    assert out["indices"]["chuangye"] is None


def test_zt_pool_ratios_decimal(monkeypatch):
    _mock_all_success(monkeypatch)
    out = sd.fetch_zt_pool(TODAY)
    assert out["zhaban_rate"] == pytest.approx(1 / 3)  # 1 炸板 ÷ (2 涨停 + 1 炸板)
    assert out["limit_up"]["tier"] == {"首板": 1, "2": 1}
    assert out["limit_up"]["count"] == 2
    assert out["limit_down"]["count"] == 1
    # raw 行保留上游百分数，块级标注单位
    assert out["limit_up"]["units"]["涨跌幅"] == "%"
    assert out["limit_up"]["stocks"][0]["涨跌幅"] == 10.0


def test_sectors_contract_fields(monkeypatch):
    _mock_all_success(monkeypatch)
    out = sd.fetch_sectors(TODAY)
    assert out["source"] == "同花顺"
    top = out["top5"][0]
    assert top["pct_change"] == pytest.approx(0.032)   # 小数
    assert top["raw"]["涨跌幅"] == pytest.approx(3.2)  # raw 原值
    assert out["units"]["涨跌幅"] == "%"


def test_breadth_decimal(monkeypatch):
    _mock_all_success(monkeypatch)
    out = sd.fetch_market_breadth(TODAY)
    assert out["source"] == "Tushare计算"
    assert out["up"] == 2 and out["down"] == 0
    assert out["up_down_ratio"] is None  # 无下跌 → None 而非 0
    assert out["limit_up"] == 1
    # P0-2：分布键名必须为含 % 规范键；R17 细分「跌5%-7%」「跌7%以上」两档（深度扣分用）
    assert set(out["distribution"]) == {
        "涨停", "跌停", "涨0%-3%", "涨3%-5%", "涨5%-7%", "涨7%-10%", "涨10%以上",
        "跌0%-3%", "跌3%-5%", "跌5%-7%", "跌7%-10%", "跌10%以上",
        "涨5%-10%", "涨2%-5%", "涨0%-2%",
        "平盘", "跌0%-2%", "跌2%-5%", "跌7%以上",
    }


# ── @tool 包装 ──

def test_tool_wrappers_return_json(monkeypatch):
    _mock_all_success(monkeypatch)
    micro = json.loads(sd.get_market_micro.invoke({"date": TODAY}))
    assert set(micro) >= {"index", "zhangting", "dieting", "market_breadth", "sectors"}
    assert micro["zhangting"]["total"] == 2
    trend = json.loads(sd.get_index_trend.invoke({"date": TODAY, "days": 30}))
    assert "shanghai" in trend["indices"]
    assert trend["indices"]["shanghai"]["pct_change"] <= 1.0  # 小数
    sent = json.loads(sd.get_sentiment.invoke({"date": TODAY}))
    assert sent["avg_return"] == pytest.approx(0.10)
    mode = json.loads(sd.get_mode_data.invoke({"mode": "close", "date": TODAY}))
    assert set(mode["blocks"]) == EXPECTED_BLOCKS["close"]
    bad = json.loads(sd.get_mode_data.invoke({"mode": "nope", "date": TODAY}))
    assert "无效模式" in bad["error"]


def test_search_history_wrapper(monkeypatch):
    monkeypatch.setattr(sd, "kb_search", lambda q: "[]")
    assert sd.search_history.invoke({"query": "涨停"}) == "[]"


# ── 个股查询降级链 ──

def test_stock_info_tushare(monkeypatch):
    df = make_ts_daily_df([
        {"ts_code": "600519.SH", "trade_date": "20260728", "pre_close": 1400.0,
         "open": 1400.0, "high": 1420.0, "low": 1390.0, "close": 1410.0,
         "pct_chg": 0.7, "vol": 1000, "amount": 1e8},
        {"ts_code": "600519.SH", "trade_date": "20260731", "pre_close": 1410.0,
         "open": 1420.0, "high": 1450.0, "low": 1415.0, "close": 1440.0,
         "pct_chg": 2.1, "vol": 1200, "amount": 1.2e8},
    ])
    monkeypatch.setattr(sd, "_ts_daily_range", lambda code, start, end: df)
    monkeypatch.setattr(sd, "_ts_stock_basic", lambda: pd.DataFrame(
        {"ts_code": ["600519.SH"], "name": ["贵州茅台"], "list_date": ["20010101"]}))
    out = sd.fetch_stock_info("600519", "2026-07-31")
    assert out["source"] == "Tushare"
    assert out["name"] == "贵州茅台"
    assert out["close"] == pytest.approx(1440.0)
    assert out["pct_change"] == pytest.approx(0.021)  # 小数
    assert out["recent5"][-1]["pct_change"] == pytest.approx(0.021)


def test_stock_info_em_fallback(monkeypatch):
    monkeypatch.setattr(sd, "_ts_daily_range", lambda *a, **k: (_ for _ in ()).throw(
        sd.DataSourceError("未配置 TUSHARE_TOKEN")))
    monkeypatch.setattr(sd, "_em_stock_daily", lambda symbol, s, e: pd.DataFrame([
        {"日期": "2026-07-31", "开盘": 10.0, "收盘": 11.0, "最高": 11.2, "最低": 9.9,
         "成交量": 1000, "成交额": 1e7, "涨跌幅": 10.0},
    ]))
    out = sd.fetch_stock_info("600001", "2026-07-31")
    assert out["source"] == "东财"
    assert out["pct_change"] == pytest.approx(0.10)
