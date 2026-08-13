"""实时数据快照测试：结构契约 / 降级链 / 端点（全离线，monkeypatch）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import backend.main as bm  # noqa: E402
from stock_review_crew.tools import realtime  # noqa: E402

client = TestClient(bm.app)


def test_to_float_rejects_nan_inf():
    """NaN/Inf 字符串与浮点 → None（避免 NaN 上屏，数据字典 None=「—」）。"""
    assert realtime._to_float("nan") is None
    assert realtime._to_float("inf") is None
    assert realtime._to_float(float("nan")) is None
    assert realtime._to_float("10.5") == 10.5
    assert realtime._to_float("-") is None


def test_get_falls_back_to_direct_on_proxy_error(monkeypatch):
    """代理客户端未启动（ProxyError）→ 自动直连重试（国内行情源直连可用）。"""
    import requests

    calls: list[dict] = []

    def fake_get(url, **kw):
        calls.append(kw)
        if len(calls) == 1:
            raise requests.exceptions.ProxyError("代理拒绝连接")
        resp = type("R", (), {"json": lambda self: {"ok": 1}, "raise_for_status": lambda self: None})()
        return resp

    monkeypatch.setattr(requests, "get", fake_get)
    assert realtime._get("http://example.com/x", params={"a": 1}).json() == {"ok": 1}
    assert len(calls) == 2
    assert calls[1].get("proxies") == {"http": None, "https": None}


def _fake_indices():
    return {
        "indices": [
            {"name": "上证指数", "price": 3806.79, "pct_change": -0.0008,
             "open": 3816.37, "high": 3818.27, "low": 3799.52, "source": "东财实时"}
        ],
        "source": "东财实时", "degraded": False, "degraded_reason": [],
    }


def _fake_zt(*a, **k):
    return {
        "limit_up_count": 67, "limit_down_count": 0, "zhaban_count": 11,
        "touched_count": 78, "zhaban_rate": 0.141, "seal_rate": 0.859,
        "yesterday_zt_count": 75, "tier": {"首板": 42, "2板及以上": 25},
        "source": "同花顺实时", "degraded": False, "degraded_reason": [], "units": {},
    }


def _fake_sectors():
    return {
        "top": [{"name": "电子", "pct_change": 0.0309, "net_inflow": 1e10}],
        "bottom": [], "flow_in": [], "flow_out": [],
        "source": "东财实时", "degraded": False, "degraded_reason": [], "units": {},
    }


def _fake_news():
    return {"news": [{"time": "2026-08-04 11:00", "text": "测试快讯", "source": "新浪7x24"}],
            "source": "新浪7x24", "degraded": False, "degraded_reason": []}


def test_snapshot_structure(monkeypatch):
    monkeypatch.setattr(realtime, "fetch_indices", _fake_indices)
    monkeypatch.setattr(realtime, "fetch_zt_stats", _fake_zt)
    monkeypatch.setattr(realtime, "fetch_sector_flow", _fake_sectors)
    monkeypatch.setattr(realtime, "fetch_news", _fake_news)
    snap = realtime.fetch_realtime_snapshot()
    assert set(snap) >= {"status", "indices", "zt", "sectors", "news", "auction", "sources", "degraded"}
    assert snap["indices"]["indices"][0]["pct_change"] == pytest.approx(-0.0008)
    assert snap["zt"]["zhaban_rate"] == pytest.approx(0.141)
    assert snap["sectors"]["top"][0]["name"] == "电子"
    assert snap["news"]["news"][0]["text"] == "测试快讯"
    assert snap["sources"] == ["东财实时", "同花顺实时", "东财实时", "新浪7x24"]
    assert snap["degraded"] == []
    assert "updated_at" in snap


def test_snapshot_degrade_all(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("模拟离线")

    monkeypatch.setattr(realtime, "fetch_indices", _boom)
    monkeypatch.setattr(realtime, "fetch_indices_tx", _boom)
    monkeypatch.setattr(realtime, "fetch_zt_stats", _boom)
    monkeypatch.setattr(realtime, "fetch_sector_flow", _boom)
    monkeypatch.setattr(realtime, "fetch_news", _boom)
    snap = realtime.fetch_realtime_snapshot()
    assert snap["indices"] is None
    assert snap["zt"] is None
    assert snap["sectors"] is None
    assert snap["news"] is None
    assert len(snap["degraded"]) >= 4
    assert snap["auction"]["window"] in (True, False)


def test_realtime_endpoint(monkeypatch):
    monkeypatch.setattr(realtime, "fetch_realtime_snapshot", lambda: {"status": {"phase": "测试"}, "ok": True})
    r = client.get("/api/data/realtime")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"status": {"phase": "测试"}, "ok": True}


def test_stock_quote(monkeypatch):
    fake = {
        "data": {
            "f43": 3806.79, "f44": 3818.27, "f45": 3799.52, "f46": 3816.37,
            "f47": 100, "f48": 1e8, "f57": "600519", "f58": "贵州茅台",
            "f60": 3809.66, "f169": 0.55, "f170": -0.08,
        }
    }
    monkeypatch.setattr(realtime, "_http_json", lambda *a, **k: fake)
    q = realtime.fetch_stock_quote("600519")
    assert q["code"] == "600519"
    assert q["name"] == "贵州茅台"
    assert q["pct_change"] == pytest.approx(-0.0008)
    assert q["turnover_rate"] == pytest.approx(0.0055)


def test_quote_endpoint(monkeypatch):
    monkeypatch.setattr(
        realtime, "fetch_stock_quote",
        lambda code: {"code": code, "name": "测试股", "price": 10.0, "pct_change": 0.01},
    )
    r = client.get("/api/data/quote", params={"code": "600519"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "测试股"
    # 参数校验
    assert client.get("/api/data/quote", params={"code": "abc"}).status_code == 400
    assert client.get("/api/data/quote").status_code == 400
    # 未查到 → 404 中文
    def _boom(code):
        raise realtime.RealtimeError("未查询到该股票")

    monkeypatch.setattr(realtime, "fetch_stock_quote", _boom)
    assert client.get("/api/data/quote", params={"code": "999999"}).status_code == 404


def test_sector_flow_tx_fallback(monkeypatch):
    """东财失败 → 腾讯行业排行兜底（来源标注）。"""
    fake_rank = {
        "data": {
            "rank_list": [
                {"name": "食品饮料", "zdf": "-1.45", "zljlr": "-211.70",
                 "lzg": {"name": "莲花控股", "zdf": "10.05"}},
                {"name": "电子", "zdf": "3.09", "zljlr": "1417736.0",
                 "lzg": {"name": "沪电股份", "zdf": "6.5"}},
            ]
        }
    }

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return fake_rank

    monkeypatch.setattr(realtime, "_http_json", lambda *a, **k: (_ for _ in ()).throw(RealtimeError("东财 502")))
    monkeypatch.setattr(realtime.requests, "get", lambda *a, **k: _Resp())
    b = realtime.fetch_sector_flow()
    assert b["source"] == "腾讯行业"
    assert b["top"][0]["name"] == "电子"
    assert b["top"][0]["pct_change"] == pytest.approx(0.0309)
    assert b["bottom"][0]["name"] == "食品饮料"
    assert b["flow_in"][0]["net_inflow"] == pytest.approx(1417736.0 * 10000)


def test_search_stocks(monkeypatch):
    fake = {
        "QuotationCodeTable": {
            "Data": [
                {"Code": "600519", "Name": "贵州茅台", "MktNum": "1", "SecurityTypeName": "沪A"},
                {"Code": "000858", "Name": "五粮液", "MktNum": "0", "SecurityTypeName": "深A"},
            ],
            "TotalCount": 2,
        }
    }
    monkeypatch.setattr(realtime, "_http_json", lambda *a, **k: fake)
    out = realtime.search_stocks("茅台")
    assert out[0] == {"code": "600519", "name": "贵州茅台", "market": "sh", "type": "沪A"}
    assert out[1]["market"] == "sz"
    # 6 位代码直接返回
    out2 = realtime.search_stocks("600519")
    assert out2[0]["code"] == "600519"


def test_search_endpoint(monkeypatch):
    monkeypatch.setattr(
        realtime, "search_stocks",
        lambda q: [{"code": "600519", "name": "贵州茅台", "market": "sh", "type": "GP"}],
    )
    r = client.get("/api/data/search", params={"q": "茅台"})
    assert r.status_code == 200, r.text
    assert r.json()[0]["code"] == "600519"
    assert client.get("/api/data/search", params={"q": ""}).status_code == 400
    assert client.get("/api/data/search").status_code == 400


# ══════════════════════════════════════════════════════════════════
# R8（2026-08-08）：快讯 A 股相关关键词白名单过滤
# ══════════════════════════════════════════════════════════════════


def test_filter_a_share_news_keeps_market_related():
    """R8：含白名单关键词的快讯保留，无关快讯剔除。"""
    items = [
        {"time": "11:00", "text": "沪指午后翻红，白酒板块拉升", "source": "新浪7x24"},
        {"time": "11:01", "text": "某明星演唱会门票售罄", "source": "新浪7x24"},
        {"time": "11:02", "text": "央行开展逆回购操作", "source": "新浪7x24"},
        {"time": "11:03", "text": "世界杯预选赛今晚开打", "source": "新浪7x24"},
    ]
    out = realtime.filter_a_share_news(items)
    texts = [x["text"] for x in out]
    assert "沪指午后翻红，白酒板块拉升" in texts
    assert "央行开展逆回购操作" in texts
    assert "某明星演唱会门票售罄" not in texts
    assert "世界杯预选赛今晚开打" not in texts


def test_filter_a_share_news_accepts_dict_and_strings():
    """R8：兼容 fetch_news 字典 / 字符串列表两种输入形态。"""
    wrapped = {"news": [{"text": "证监会就分红新规答记者问"}, {"text": "宠物猫走失求转发"}]}
    out = realtime.filter_a_share_news(wrapped)
    assert len(out) == 1
    assert out[0]["text"].startswith("证监会")
    str_out = realtime.filter_a_share_news(["板块轮动加快", "纯娱乐八卦"])
    assert str_out[0]["text"] == "板块轮动加快"  # 字符串输入统一包装为 dict
    assert len(str_out) == 1


def test_fetch_news_filters_non_ashare(monkeypatch):
    """R8：fetch_news 返回前过滤——全部无关 → 空列表 + degraded 标注。"""
    fake_data = {
        "result": {"data": {"feed": {"list": [
            {"rich_text": "国际足球友谊赛结束"},
            {"rich_text": "某电影票房破纪录"},
        ]}}}
    }
    monkeypatch.setattr(realtime, "_http_json", lambda *a, **k: fake_data)
    out = realtime.fetch_news()
    assert out["news"] == []
    assert out["degraded"] is True
    assert any("白名单过滤" in r for r in out["degraded_reason"])

    # 混合场景：只保留 A 股相关
    fake_data["result"]["data"]["feed"]["list"] = [
        {"rich_text": "A股三大指数集体高开，券商板块领涨"},
        {"rich_text": "英超联赛战报"},
    ]
    out2 = realtime.fetch_news()
    assert out2["degraded"] is False
    assert len(out2["news"]) == 1
    assert "A股三大指数" in out2["news"][0]["text"]


# ══════════════════════════════════════════════════════════════════
# R11 R9：tools/search.py 垂直多源聚合（全 mock，不真实请求）
# ══════════════════════════════════════════════════════════════════


class _FakeResp:
    """mock urllib 响应：json 字符串 + 上下文管理器。"""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


ANNOUNCE_SAMPLE = {
    "data": {
        "list": [
            {"title": "贵州茅台：关于中期分红的公告",
             "notice_date": "2026-08-08 09:30:00",
             "url": "//np-cnotice-stock.eastmoney.com/api/content/ann?art_code=1"},
            {"title": "贵州茅台：关于签订重大合同的公告",
             "notice_date": "2026-08-06 16:00:00",
             "url": "//np-cnotice-stock.eastmoney.com/api/content/ann?art_code=2"},
            {"title": "", "notice_date": "", "url": ""},  # 空标题应跳过
        ]
    }
}


def test_search_stock_news_parses_announcements(monkeypatch):
    """R9a：东财公告接口解析 title/notice_date/url；相对 url 补全 https:；空标题跳过。"""
    from stock_review_crew.tools import search as search_tools

    def fake_urlopen(req, timeout=None):
        assert "np-anotice-stock.eastmoney.com" in req.full_url
        assert "stock_list=600519" in req.full_url
        assert timeout == search_tools.SEARCH_TIMEOUT
        return _FakeResp(ANNOUNCE_SAMPLE)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = search_tools.search_stock_news("600519")
    assert len(out) == 2
    assert out[0]["source"] == "东财公告"
    assert out[0]["title"] == "贵州茅台：关于中期分红的公告"
    assert out[0]["date"] == "2026-08-08"
    assert out[0]["url"].startswith("https://")
    assert out[1]["date"] == "2026-08-06"


def test_search_uses_announcements_when_code_present(monkeypatch):
    """R9b：query 含 6 位代码 → 优先公告；快讯链路失败不影响公告结果。"""
    from stock_review_crew.tools import search as search_tools

    calls = {"urlopen": 0}

    def fake_urlopen(req, timeout=None):
        calls["urlopen"] += 1
        return _FakeResp(ANNOUNCE_SAMPLE)

    def boom_fetch_news():
        raise RuntimeError("新浪快讯不可用（mock）")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("stock_review_crew.tools.realtime.fetch_news", boom_fetch_news)
    result = search_tools.search("贵州茅台 600519 最近有什么公告")
    assert result["ok"] is True
    assert result["summary"] is None
    assert calls["urlopen"] == 1
    assert all(r["source"] == "东财公告" for r in result["results"])


def test_search_all_fail_returns_ok_false(monkeypatch):
    """R9c：公告 + 快讯全失败 → ok=False，绝不抛异常。"""
    from stock_review_crew.tools import search as search_tools

    def boom_urlopen(req, timeout=None):
        raise OSError("网络不可用（mock）")

    def boom_fetch_news():
        raise RuntimeError("新浪快讯不可用（mock）")

    monkeypatch.setattr("urllib.request.urlopen", boom_urlopen)
    monkeypatch.setattr("stock_review_crew.tools.realtime.fetch_news", boom_fetch_news)
    result = search_tools.search("600519 怎么看")
    assert result == {"ok": False, "results": [], "summary": None}


def test_filter_news_by_keywords_matches(monkeypatch):
    """R9d：对已有快讯按关键词过滤（复用 realtime 链路）。"""
    from stock_review_crew.tools import search as search_tools

    raw = {"news": [
        {"text": "贵州茅台中期分红落地", "source": "新浪7x24"},
        {"text": "白酒板块午后拉升", "source": "新浪7x24"},
        {"text": "某球星转会消息", "source": "新浪7x24"},
    ]}
    out = search_tools.filter_news_by_keywords(raw, ["茅台", "白酒"])
    assert len(out) == 2
    assert "贵州茅台中期分红落地" in out[0]["text"]
    assert "某球星转会消息" not in [x["text"] for x in out]
    # 无关键词 → 全保留
    assert len(search_tools.filter_news_by_keywords(raw, [])) == 3


def test_extract_stock_code_helper():
    """R9e：从查询文本提取 6 位代码；无则 None。"""
    from stock_review_crew.tools import search as search_tools

    assert search_tools.extract_stock_code("贵州茅台 600519 怎么样") == "600519"
    assert search_tools.extract_stock_code("怎么看大盘") is None


def test_bocha_disabled_by_default(monkeypatch):
    """R9f：即使 BOCHA_API_KEY 存在，BOCHA_SEARCH_ENABLED 默认关 → 不调用全网补充。"""
    from stock_review_crew import config
    from stock_review_crew.tools import search as search_tools

    # 直接打补丁 config 模块属性（模块级常量在 import 时已求值，setenv 无效）
    monkeypatch.setattr(config, "BOCHA_API_KEY", "fake-key")
    monkeypatch.setattr(config, "BOCHA_SEARCH_ENABLED", False)
    assert search_tools._bocha_enabled() is False

    # 开启后才允许调用
    monkeypatch.setattr(config, "BOCHA_SEARCH_ENABLED", True)
    assert search_tools._bocha_enabled() is True


def test_config_bocha_empty_key_no_error(monkeypatch):
    """R9g：BOCHA_API_KEY 空值不报错，搜索仍可降级（全失败 → ok=False）。"""
    from stock_review_crew import config
    from stock_review_crew.tools import search as search_tools

    monkeypatch.setattr(config, "BOCHA_API_KEY", None)
    monkeypatch.setattr(config, "BOCHA_SEARCH_ENABLED", False)
    assert search_tools._bocha_enabled() is False
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    result = search_tools.search("600519")
    assert result["ok"] is False
