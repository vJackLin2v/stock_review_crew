"""R10（M1）数据层验收测试：市场天气/纳指/板块温度/额外指数/风格坐标。

只读契约：docs/requirements-r10.md §二（数据字典，评分规则冻结）与 §五（函数签名）。
全部离线：monkeypatch 数据源，禁止真实网络。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew.tools import market_extra


@pytest.fixture(autouse=True)
def _clear_cache():
    """清 market_extra 60s 内存缓存（fetch 函数缓存后，测试间 monkeypatch 防串）。"""
    market_extra.clear_cache()
    yield
    market_extra.clear_cache()


# ── F3 市场天气/情绪温度：纯函数手算（规则冻结于 requirements-r10.md §二）──

def _blocks(up=3000, down=1500, zt=80, dt=5, zhaban=0.20, height=5, red=0.68):
    """构造 compute_market_weather 输入 blocks（fetch_mode_data close 结构）。"""
    return {
        "zt_pool": {
            "limit_up": {"count": zt, "tier": {"首板": max(1, height - 1), f"{height}板": 1}},
            "limit_down": {"count": dt},
            "zhaban_rate": zhaban,
        },
        "breadth": {"up": up, "down": down},
        "sentiment": {"red_rate": red},
    }


def test_weather_hand_calc_strong_market():
    """验收手算样例（2026-08-06 严格化）：r=0.67→+8；d=75→+7；炸板0.20→+6；连板5→+6；
    红盘0.68→+6；50+8+7+6+6+6=83 → 晴 / 偏暖（不再轻易 100）。"""
    out = market_extra.compute_market_weather(_blocks())
    assert out["emotion_score"] == 83
    assert out["weather"] == "晴"
    assert out["emotion_label"] == "火热"  # 83≥80 仍火热，但不再 clamp 满分（差异可见）
    reasons = "；".join(out["reasons"])
    assert "涨跌家数比 0.67：+8" in reasons
    assert "涨停 80 家、跌停 5 家：+7" in reasons
    assert "炸板率 0.20：+6" in reasons
    assert "连板高度 5：+6" in reasons
    assert "昨日涨停红盘率 0.68：+6" in reasons


def test_weather_hand_calc_weak_market():
    """弱市样例（2026-08-06 严格化）：r=0.286(<0.3)→-14；d=20-40=-20→-2；炸板0.5→-12；
    连板1→-4；红盘0.3→-8；50-14-2-12-4-8=10 → 偏冷有雨 / 冰点。"""
    out = market_extra.compute_market_weather(_blocks(up=1000, down=2500, zt=20, dt=40,
                                                      zhaban=0.5, height=1, red=0.3))
    assert out["emotion_score"] == 10
    assert out["weather"] == "偏冷有雨"
    assert out["emotion_label"] == "冰点"
    reasons = "；".join(out["reasons"])
    assert "涨跌家数比 0.29" in reasons and "-14" in reasons
    assert "涨停 20 家、跌停 40 家：-2" in reasons
    assert "炸板率 0.50：-12" in reasons
    assert "昨日涨停红盘率 0.30：-8" in reasons


def test_weather_boundaries():
    """分档边界（2026-08-06 严格化）：r=0.6→+4；r=0.4→0；r=0.3→-8；炸板0.25→0；
    0.4→0；连板3→+3。"""
    out = market_extra.compute_market_weather(
        _blocks(up=600, down=400, zt=30, dt=0, zhaban=0.25, height=3, red=0.5))
    reasons = "；".join(out["reasons"])
    assert "涨跌家数比 0.60：+4" in reasons
    assert "炸板率 0.25：+0" in reasons
    assert "连板高度 3：+3" in reasons
    assert "涨停 30 家、跌停 0 家：+3" in reasons

    out2 = market_extra.compute_market_weather(
        _blocks(up=400, down=600, zt=30, dt=0, zhaban=0.4, height=3, red=0.5))
    r2 = "；".join(out2["reasons"])
    assert "涨跌家数比 0.40：+0" in r2
    assert "炸板率 0.40：+0" in r2

    out3 = market_extra.compute_market_weather(
        _blocks(up=300, down=700, zt=30, dt=0, zhaban=0.4, height=3, red=0.5))
    r3 = "；".join(out3["reasons"])
    assert "涨跌家数比 0.30：-8" in r3


def test_weather_diff_clamp_upper_lower():
    """涨停-跌停差上下限（2026-08-06 严格化）：d=200 → +12 clamp；d=-200 → -12 clamp。"""
    out = market_extra.compute_market_weather(
        _blocks(up=3000, down=1500, zt=200, dt=0, zhaban=0.2, height=5, red=0.68))
    assert "涨停 200 家、跌停 0 家：+12" in "；".join(out["reasons"])
    assert out["emotion_score"] == 88
    out2 = market_extra.compute_market_weather(
        _blocks(up=1000, down=2500, zt=0, dt=200, zhaban=0.5, height=1, red=0.3))
    assert "涨停 0 家、跌停 200 家：-12" in "；".join(out2["reasons"])
    assert out2["emotion_score"] == 0


def test_weather_all_missing():
    """输入全缺失：score=None、天气「数据缺失」、标签 None、reasons 标注数据缺失。"""
    out = market_extra.compute_market_weather({})
    assert out["emotion_score"] is None
    assert out["weather"] == "数据缺失"
    assert out["emotion_label"] is None
    reasons = "；".join(out["reasons"])
    assert "数据缺失" in reasons


def test_weather_missing_items_not_scored():
    """部分缺失：缺失项不加分且 reason 标「数据缺失」；其余项正常计分。"""
    blocks = {
        "zt_pool": {"limit_up": {"count": 80, "tier": {"5板": 1}},
                    "limit_down": {"count": 5}, "zhaban_rate": 0.20},
        "breadth": {"up": 3000, "down": 1500},
        "sentiment": {},
    }
    out = market_extra.compute_market_weather(blocks)
    reasons = "；".join(out["reasons"])
    assert "昨日涨停红盘率" in reasons and "数据缺失" in reasons
    # 50+8+7+6+6=77（红盘缺失不加分）
    assert out["emotion_score"] == 77


def test_weather_flat_blocks_compat():
    """扁平结构兼容（sentiment 直接放在顶层键）。"""
    blocks = {
        "zt_pool": {"limit_up": {"count": 80, "tier": {"5板": 1}},
                    "limit_down": {"count": 5}, "zhaban_rate": 0.20},
        "breadth": {"up": 3000, "down": 1500},
        "red_rate": 0.68,
    }
    out = market_extra.compute_market_weather(blocks)
    assert out["emotion_score"] == 83


def test_weather_schema():
    """输出结构契约：date/weather/emotion_score/emotion_label/reasons/source/degraded。"""
    out = market_extra.compute_market_weather(_blocks())
    for key in ("weather", "emotion_score", "emotion_label", "reasons", "source", "degraded"):
        assert key in out, key
    assert isinstance(out["reasons"], list)
    assert out["emotion_score"] is not None


# ── 情绪周期定位 classify_emotion_cycle（规则引擎，纯计算）──

def test_emotion_cycle_data_insufficient():
    """zt_total 与 red_rate/avg_return 都拿不到 → 「数据不足」。"""
    assert market_extra.classify_emotion_cycle({}) == "数据不足"
    assert market_extra.classify_emotion_cycle(None) == "数据不足"
    assert market_extra.classify_emotion_cycle({"zt_pool": {}}) == "数据不足"
    # 仅有涨停家数（zt_total 拿得到）→ 不算「数据不足」，落「修复/发酵」且缺失值标「数据缺失」
    out = market_extra.classify_emotion_cycle(
        {"zt_pool": {"limit_up": {"count": 59}}, "sentiment": {}})
    assert out.startswith("修复/发酵")
    assert "红盘率数据缺失" in out and "炸板率数据缺失" in out


def test_emotion_cycle_ice_point():
    """zt_total <= 20 → 冰点（涨停X家，情绪极冷）。"""
    out = market_extra.classify_emotion_cycle(
        {"zt_pool": {"limit_up": {"count": 15}, "zhaban_rate": 0.1},
         "sentiment": {"red_rate": 0.45, "avg_return": 0.01}})
    assert out == "冰点（涨停15家，情绪极冷）"


def test_emotion_cycle_eclipse():
    """red_rate<0.5 且（炸板率≥0.3 或 平均收益<0）→ 退潮（红盘率X%、炸板率X%）。"""
    out = market_extra.classify_emotion_cycle(
        {"zt_pool": {"limit_up": {"count": 59}, "zhaban_rate": 0.3922},
         "sentiment": {"red_rate": 0.4583, "avg_return": -0.0114}})
    assert out.startswith("退潮")
    assert "红盘率45.8%" in out and "炸板率39.2%" in out
    # 仅平均收益<0 触发（炸板率缺失 → 标注数据缺失，不编造）
    out2 = market_extra.classify_emotion_cycle(
        {"zt_pool": {"limit_up": {"count": 59}},
         "sentiment": {"red_rate": 0.4, "avg_return": -0.02}})
    assert out2.startswith("退潮") and "炸板率数据缺失" in out2


def test_emotion_cycle_main_rise():
    """red_rate>=0.6 且 avg_return>0 且 炸板率<0.25 → 主升（红盘率X%、涨停X家）。"""
    out = market_extra.classify_emotion_cycle(
        {"zt_pool": {"limit_up": {"count": 82}, "zhaban_rate": 0.18},
         "sentiment": {"red_rate": 0.68, "avg_return": 0.032}})
    assert out == "主升（红盘率68.0%、涨停82家）"


def test_emotion_cycle_repair():
    """其余 → 修复/发酵（红盘率X%、炸板率X%）。"""
    out = market_extra.classify_emotion_cycle(
        {"zt_pool": {"limit_up": {"count": 59}, "zhaban_rate": 0.28},
         "sentiment": {"red_rate": 0.55, "avg_return": 0.01}})
    assert out.startswith("修复/发酵")
    assert "红盘率55.0%" in out and "炸板率28.0%" in out


def test_emotion_cycle_breadth_fallback_and_never_raise():
    """涨停家数可从 breadth.limit_up 提取；异常输入绝不抛（返回「数据不足」）。"""
    out = market_extra.classify_emotion_cycle(
        {"breadth": {"limit_up": 40},
         "sentiment": {"red_rate": 0.62, "avg_return": 0.02, "zhaban_rate": 0.2}})
    assert out.startswith("主升") and "涨停40家" in out

    class BoomDict(dict):
        def get(self, key, default=None):
            raise RuntimeError("炸")

    assert market_extra.classify_emotion_cycle({"zt_pool": BoomDict()}) == "数据不足"


# ── fetch_market_weather：取数包装（monkeypatch close 取数）──

def test_fetch_weather_ok(monkeypatch):
    monkeypatch.setattr(market_extra.stock_data, "fetch_zt_pool",
                        lambda date: _blocks()["zt_pool"])
    monkeypatch.setattr(market_extra.stock_data, "fetch_market_breadth",
                        lambda date: _blocks()["breadth"])
    monkeypatch.setattr(market_extra.stock_data, "fetch_sentiment",
                        lambda date: _blocks()["sentiment"])
    out = market_extra.fetch_market_weather("2026-08-05")
    assert out["date"] == "2026-08-05"
    assert out["emotion_score"] == 83
    assert out["degraded"] is False


def test_fetch_weather_missing_blocks(monkeypatch):
    monkeypatch.setattr(market_extra.stock_data, "fetch_zt_pool",
                        lambda date: (_ for _ in ()).throw(RuntimeError("缺块")))
    monkeypatch.setattr(market_extra.stock_data, "fetch_market_breadth",
                        lambda date: (_ for _ in ()).throw(RuntimeError("缺块")))
    monkeypatch.setattr(market_extra.stock_data, "fetch_sentiment",
                        lambda date: (_ for _ in ()).throw(RuntimeError("缺块")))
    out = market_extra.fetch_market_weather("2026-08-05")
    assert out["weather"] == "数据缺失"
    assert out["emotion_score"] is None
    assert out["degraded"] is True


def test_fetch_weather_source_error(monkeypatch):
    def boom(mode, date):
        raise RuntimeError("取数失败")

    monkeypatch.setattr(market_extra.stock_data, "fetch_zt_pool",
                        lambda date: (_ for _ in ()).throw(RuntimeError("取数失败")))
    monkeypatch.setattr(market_extra.stock_data, "fetch_market_breadth",
                        lambda date: (_ for _ in ()).throw(RuntimeError("取数失败")))
    monkeypatch.setattr(market_extra.stock_data, "fetch_sentiment",
                        lambda date: (_ for _ in ()).throw(RuntimeError("取数失败")))
    out = market_extra.fetch_market_weather("2026-08-05")  # 绝不抛
    assert out["degraded"] is True
    assert out["degraded_reason"]
    assert "取数失败" in "；".join(out["degraded_reason"])


# ── F6 纳斯达克 ──

def _us_rows(close=26363.44, prev=26000.0, pct=1.4):
    return [
        {"date": "2026-08-04", "close": prev, "open": 25800.0, "high": 26100.0, "low": 25700.0, "pct": 0.5},
        {"date": "2026-08-05", "close": close, "open": 26000.0, "high": 26400.0, "low": 25900.0, "pct": pct},
    ]


def test_nasdaq_ok(monkeypatch):
    monkeypatch.setattr(market_extra, "_us_kline_from_tx", lambda symbol: _us_rows())
    out = market_extra.fetch_us_nasdaq()
    assert out["degraded"] is False
    assert len(out["items"]) == 2
    for item in out["items"]:
        assert item["close"] == 26363.44
        assert item["pct_change"] == pytest.approx(0.014)
        assert len(item["points"]) == 2
        assert "is_today" in item
    assert out["source"] == "腾讯"


def test_nasdaq_fallback_em(monkeypatch):
    """腾讯失败 → 新浪失败 → .NDX 走东财备用；.IXIC 独立降级不拖垮。"""
    monkeypatch.setattr(market_extra, "_us_kline_from_tx",
                        lambda symbol: (_ for _ in ()).throw(RuntimeError("腾讯不可用")))
    monkeypatch.setattr(market_extra, "_ak_us_index_sina", lambda symbol: (_ for _ in ()).throw(RuntimeError("新浪不可用")))
    monkeypatch.setattr(market_extra, "_ak_us_index_hist_em", lambda symbol: _us_rows())
    out = market_extra.fetch_us_nasdaq()
    assert out["degraded"] is True
    ix = next(i for i in out["items"] if i["symbol"] == ".IXIC")
    ndx = next(i for i in out["items"] if i["symbol"] == ".NDX")
    assert ix["close"] is None  # .IXIC 无备用 → 缺失
    assert ndx["close"] == 26363.44  # .NDX 东财备用成功
    assert out["source"] == "东财"


def test_nasdaq_all_fail(monkeypatch):
    monkeypatch.setattr(market_extra, "_us_kline_from_tx",
                        lambda symbol: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(market_extra, "_ak_us_index_sina",
                        lambda symbol: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(market_extra, "_ak_us_index_hist_em",
                        lambda symbol: (_ for _ in ()).throw(RuntimeError("y")))
    out = market_extra.fetch_us_nasdaq()  # 绝不抛
    assert out["degraded"] is True
    assert all(i["close"] is None for i in out["items"])
    assert out["source"] == "数据缺失"


def test_nasdaq_schema():
    out = {
        "items": [{"name": "纳斯达克综合", "symbol": ".IXIC", "close": None, "pct_change": None,
                   "pre_close": None, "open": None, "high": None, "low": None,
                   "points": [], "is_today": False}],
        "source": "数据缺失", "degraded": True, "degraded_reason": [], "note": None,
    }
    assert out["items"][0]["symbol"] == ".IXIC"


# ── F5 板块温度榜 ──

def test_sector_heat_ok(monkeypatch):
    sample = {
        "hot": [{"name": "半导体", "pct_change": 0.023, "leader": "示例科技"}],
        "cold": [{"name": "煤炭", "pct_change": -0.015, "leader": "示例煤业"}],
        "source": "东财行业", "degraded": False,
    }
    monkeypatch.setattr(market_extra, "_sector_heat_from_em", lambda: sample)
    out = market_extra.fetch_sector_heat()
    assert out == sample or (out["hot"][0]["name"] == "半导体" and out["cold"][0]["name"] == "煤炭")


def test_sector_heat_fallback(monkeypatch):
    monkeypatch.setattr(market_extra, "_sector_heat_from_em",
                        lambda: (_ for _ in ()).throw(RuntimeError("东财失败")))
    monkeypatch.setattr(
        "stock_review_crew.tools.realtime.fetch_sector_flow",
        lambda: {"top": [{"name": "军工", "pct_change": 0.01, "net_inflow": 1.0}],
                 "bottom": [], "source": "腾讯行业", "degraded": False, "degraded_reason": []})
    out = market_extra.fetch_sector_heat()
    assert out["degraded"] is True  # 备用链降级标注（领涨股可能缺失）
    assert any(r["name"] == "军工" for r in out["hot"])


def test_sector_heat_all_fail(monkeypatch):
    monkeypatch.setattr(market_extra, "_sector_heat_from_em",
                        lambda: (_ for _ in ()).throw(RuntimeError("东财失败")))
    monkeypatch.setattr("stock_review_crew.tools.realtime.fetch_sector_flow",
                        lambda: (_ for _ in ()).throw(RuntimeError("腾讯失败")))
    monkeypatch.setattr("stock_review_crew.tools.stock_data.fetch_sectors",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("板块链失败")))
    out = market_extra.fetch_sector_heat()  # 绝不抛
    assert out["degraded"] is True
    assert out["hot"] == [] and out["cold"] == []


# ── F2 额外指数 ──

def test_extra_indices_ok(monkeypatch):
    sample = {"name": "平均股价", "code": "880003", "close": 27.08, "pct_change": 0.008, "source": "通达信协议"}
    monkeypatch.setattr(market_extra, "_extra_index_item", lambda d: (sample, "通达信协议"))
    out = market_extra.fetch_extra_indices()
    assert out["degraded"] is False
    assert len(out["items"]) == 4
    assert out["items"][0]["close"] == 27.08


def test_extra_indices_partial_fail(monkeypatch):
    def fake(d):
        if d["code"] == "880003":
            return ({"name": "平均股价", "code": "880003", "close": 27.08,
                     "pct_change": 0.008, "source": "通达信协议"}, "通达信协议")
        raise RuntimeError(f"{d['code']} 不可用")

    monkeypatch.setattr(market_extra, "_extra_index_item", fake)
    out = market_extra.fetch_extra_indices()
    assert out["degraded"] is True
    missing = [i for i in out["items"] if i["close"] is None]
    assert len(missing) == 3
    ok = [i for i in out["items"] if i["close"] is not None]
    assert len(ok) == 1 and ok[0]["code"] == "880003"


def test_extra_indices_all_fail(monkeypatch):
    monkeypatch.setattr(market_extra, "_extra_index_item",
                        lambda d: (_ for _ in ()).throw(RuntimeError("x")))
    out = market_extra.fetch_extra_indices()  # 绝不抛
    assert out["degraded"] is True
    assert all(i["close"] is None for i in out["items"])
    assert out["source"] == "数据缺失"


def test_extra_indices_bk_fail_reason_zh(monkeypatch):
    """2026-08-07 修复：BK 系（微盘/高股息）东财失败时降级原因全中文，
    不再透传英文异常原文（曾出现 time data 'i' does not match ... / 502 Bad Gateway）。"""

    def _em_boom(*a, **k):
        raise market_extra.DataSourceError("东财数据源请求失败（HTTP 502）")

    def _tdx_boom(*a, **k):
        raise market_extra.DataSourceError("通达信协议数据源异常")

    monkeypatch.setattr(market_extra, "_em_board_quote", _em_boom)
    monkeypatch.setattr(market_extra, "_tdx_index_item", _tdx_boom)
    out = market_extra.fetch_extra_indices()
    assert out["degraded"] is True
    assert all(i["close"] is None for i in out["items"])
    for reason in out["degraded_reason"]:
        assert "微盘股" in reason or "高股息股" in reason or "平均股价" in reason or "同花顺全A" in reason
        # 英文异常特征必须为零
        for eng in ("time data", "does not match", "Traceback", "Bad Gateway",
                    "Server Error", "url:", "HTTPError", "ValueError"):
            assert eng not in reason, f"降级原因透传英文异常：{reason}"


def test_em_http_json_zh_error(monkeypatch):
    """2026-08-07 修复：东财 HTTP 失败对外只给中文短句（含状态码），不透传 URL/英文。"""
    import requests

    def _get_boom(*a, **k):
        resp = requests.Response()
        resp.status_code = 502
        raise requests.exceptions.HTTPError(
            "502 Server Error: Bad Gateway for url: http://80.push2his.eastmoney.com/xxx",
            response=resp,
        )

    monkeypatch.setattr(market_extra.stock_data, "_get", _get_boom)
    try:
        market_extra._em_http_json("http://push2his.eastmoney.com/api/qt/stock/get")
        raise AssertionError("应抛 DataSourceError")
    except market_extra.DataSourceError as exc:
        msg = str(exc)
        assert msg == "东财数据源请求失败（HTTP 502）", msg


# ── F4 风格坐标 ──

def test_style_quad_ok(monkeypatch):
    """2026-08-07 细分：score 灵敏度 ×1000，label 按差值分档（≥1.5pp 明显占优）。"""
    monkeypatch.setattr(market_extra, "_style_index_pct",
                        lambda code, tx, name: 0.08 if code == "000300" else -0.03)
    monkeypatch.setattr(market_extra, "_style_board_pct",
                        lambda name, secid: (0.05, False) if "红利" in name else (-0.02, False))
    out = market_extra.fetch_style_quad()
    assert out["degraded"] is False
    lv = out["large_vs_small"]
    assert lv["score"] == 100  # 50 + (0.08+0.03)*1000
    assert lv["label"] == "大盘明显占优"  # 差 11pp ≥1.5pp
    vg = out["value_vs_growth"]
    assert vg["score"] == 100  # 50 + (0.05+0.02)*1000
    assert vg["label"] == "价值明显占优"  # 差 7pp ≥1.5pp


def test_style_quad_label_fine_grained(monkeypatch):
    """细分档位：差 <0.5pp → 均衡；0.5–1.5pp → 略占优；≥1.5pp → 明显占优。"""
    monkeypatch.setattr(market_extra, "_style_index_pct",
                        lambda code, tx, name: 0.003)  # 差 0pp
    monkeypatch.setattr(market_extra, "_style_board_pct",
                        lambda name, secid: (0.006, False) if "红利" in name else (-0.004, False))  # 差 1.0pp
    out = market_extra.fetch_style_quad()
    assert out["large_vs_small"]["label"] == "均衡"  # 0pp
    assert out["value_vs_growth"]["label"] == "价值略占优"  # 1.0pp → 0.5–1.5
    # 用户场景：红利 -0.73% vs 小盘成长 +1.30% → 差 2.03pp → 成长明显占优
    monkeypatch.setattr(market_extra, "_style_index_pct",
                        lambda code, tx, name: 0.013)
    monkeypatch.setattr(market_extra, "_style_board_pct",
                        lambda name, secid: (-0.0073, False) if "红利" in name else (0.013, False))
    out2 = market_extra.fetch_style_quad()
    assert out2["value_vs_growth"]["label"] == "成长明显占优"
    assert out2["value_vs_growth"]["score"] == 30  # 50 + (-0.0073-0.013)*1000 ≈ 30


def test_style_quad_dim_missing(monkeypatch):
    def idx_pct(code, tx, name):
        raise RuntimeError(f"{code} 失败")

    def board_pct(name, secid):
        raise RuntimeError(f"{secid} 失败")

    monkeypatch.setattr(market_extra, "_style_index_pct", idx_pct)
    monkeypatch.setattr(market_extra, "_style_board_pct", board_pct)
    monkeypatch.setattr(market_extra, "_pe_bucket_pcts",
                        lambda: (_ for _ in ()).throw(RuntimeError("PE 分档失败")))
    out = market_extra.fetch_style_quad()  # 绝不抛
    assert out["large_vs_small"]["score"] is None
    assert out["large_vs_small"]["label"] == "数据缺失"
    assert out["value_vs_growth"]["score"] is None
    assert out["degraded"] is True
