"""R10（M2）后端 API 验收测试：五条行情路由 + 观察列表路由（契约 requirements-r10.md §三）。

全部离线：monkeypatch market_extra 函数与 watchlist 存储根，禁止真实网络。
"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import backend.main as bm


@pytest.fixture()
def data_dir(monkeypatch):
    """隔离数据目录（0o777 自建并清理，避开沙箱 tmp_path ACL 坑，见 PLAYBOOK）。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / "r10_test_data"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(root))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _weather_sample():
    return {
        "date": "2026-08-05", "weather": "晴", "emotion_score": 100,
        "emotion_label": "火热",
        "reasons": ["涨跌家数比 0.67：+15"], "source": "规则引擎", "degraded": False,
    }


def _nasdaq_sample():
    return {
        "items": [{"name": "纳斯达克综合", "symbol": ".IXIC", "close": 26363.44,
                   "pct_change": 0.014, "pre_close": 26000.0, "open": None,
                   "high": None, "low": None, "points": [], "is_today": False}],
        "source": "新浪", "degraded": False, "degraded_reason": [], "note": None,
    }


def _sector_sample():
    return {"hot": [{"name": "半导体", "pct_change": 0.023, "leader": "示例科技"}],
            "cold": [], "source": "东财行业", "degraded": False}


def _extra_sample():
    return {"items": [{"name": "平均股价", "code": "880003", "close": 27.08,
                       "pct_change": 0.008, "source": "通达信协议"}],
            "source": "AKShare", "degraded": False}


def _style_sample():
    return {"large_vs_small": {"score": 62, "label": "大盘占优", "desc": "沪深300 +0.8%，中证1000 -0.3%"},
            "value_vs_growth": {"score": 45, "label": "均衡", "desc": "说明"},
            "source": "指数代理", "degraded": False}


@pytest.fixture(autouse=True)
def fake_market_extra(monkeypatch):
    """全部行情路由注入可控样本（实现函数签名见契约 §五）。"""
    import stock_review_crew.tools.market_extra as me

    # 清 60s 缓存（后端 _market_extra_call 成功缓存，测试间防串）
    bm._MARKET_EXTRA_CACHE.clear()
    monkeypatch.setattr(me, "fetch_market_weather", lambda date: _weather_sample())
    monkeypatch.setattr(me, "fetch_us_nasdaq", _nasdaq_sample)
    monkeypatch.setattr(me, "fetch_sector_heat", _sector_sample)
    monkeypatch.setattr(me, "fetch_extra_indices", _extra_sample)
    monkeypatch.setattr(me, "fetch_style_quad", _style_sample)


def test_health_still_ok():
    r = TestClient(bm.app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_market_weather_route():
    client = TestClient(bm.app)
    r = client.get("/api/data/market-weather")
    assert r.status_code == 200
    data = r.json()
    assert data["weather"] == "晴"
    assert data["emotion_score"] == 100
    assert isinstance(data["reasons"], list)


def test_market_weather_bad_date():
    client = TestClient(bm.app)
    r = client.get("/api/data/market-weather", params={"date": "2026-13-99"})
    assert r.status_code == 400
    assert "日期" in r.json()["detail"]


def test_us_nasdaq_route():
    r = TestClient(bm.app).get("/api/data/us-nasdaq")
    assert r.status_code == 200
    data = r.json()
    assert data["items"][0]["symbol"] == ".IXIC"
    assert data["items"][0]["pct_change"] == pytest.approx(0.014)


def test_sector_heat_route():
    r = TestClient(bm.app).get("/api/data/sector-heat")
    assert r.status_code == 200
    data = r.json()
    assert data["hot"][0]["name"] == "半导体"
    assert data["hot"][0]["pct_change"] == pytest.approx(0.023)


def test_extra_indices_route():
    r = TestClient(bm.app).get("/api/data/extra-indices")
    assert r.status_code == 200
    assert r.json()["items"][0]["code"] == "880003"


def test_market_style_route():
    r = TestClient(bm.app).get("/api/data/market-style")
    assert r.status_code == 200
    data = r.json()
    assert data["large_vs_small"]["label"] == "大盘占优"
    assert data["value_vs_growth"]["score"] == 45


def test_market_extra_module_missing_503(monkeypatch):
    """模块导入失败 → 503 中文（M1 未就绪时的契约）。"""
    import sys

    # 移除包属性 + sys.modules 置 None：from-import 回退子模块导入时触发 ImportError
    monkeypatch.delattr("stock_review_crew.tools.market_extra", raising=False)
    monkeypatch.setitem(sys.modules, "stock_review_crew.tools.market_extra", None)
    r = TestClient(bm.app).get("/api/data/market-weather")
    assert r.status_code == 503
    assert "暂不可用" in r.json()["detail"]


def test_market_extra_call_exception_500(monkeypatch):
    import stock_review_crew.tools.market_extra as me

    def boom(date):
        raise RuntimeError("内部错误")

    monkeypatch.setattr(me, "fetch_market_weather", boom)
    r = TestClient(bm.app).get("/api/data/market-weather")
    assert r.status_code == 500
    assert "获取失败" in r.json()["detail"]


# ── 观察列表路由（契约 §三）──

def test_watchlist_full_flow(data_dir):
    client = TestClient(bm.app)
    # 空列表
    assert client.get("/api/watchlist").json() == []
    # 添加
    r = client.post("/api/watchlist", json={"code": "600519", "note": "观察"})
    assert r.status_code == 200
    rec = r.json()
    assert rec["code"] == "600519"
    assert rec["note"] == "观察"
    # 重复 409
    r2 = client.post("/api/watchlist", json={"code": "600519"})
    assert r2.status_code == 409
    assert "已存在" in r2.json()["detail"]
    # 非法代码 400
    r3 = client.post("/api/watchlist", json={"code": "abc"})
    assert r3.status_code == 400
    # 列表
    assert len(client.get("/api/watchlist").json()) == 1
    # 重排
    client.post("/api/watchlist", json={"code": "000001"})
    r4 = client.put("/api/watchlist/order", json={"codes": ["000001", "600519"]})
    assert r4.status_code == 200
    assert [x["code"] for x in r4.json()] == ["000001", "600519"]
    # 删除
    r5 = client.delete("/api/watchlist/600519")
    assert r5.status_code == 204
    r6 = client.delete("/api/watchlist/600519")
    assert r6.status_code == 404
    assert "不在观察列表中" in r6.json()["detail"]


def test_watchlist_order_bad_body(data_dir):
    client = TestClient(bm.app)
    r = client.put("/api/watchlist/order", json={"codes": "600519"})
    assert r.status_code == 400


def test_watchlist_data_dir_isolated(data_dir):
    """STOCK_REVIEW_DATA_DIR 隔离：不污染仓库 data/。"""
    import stock_review_crew.storage.watchlist as wl

    assert wl.watchlist_file().parent == data_dir
    # 2026-08-06：服务运行会在仓库 data/ 生成真实 watchlist.json（用户数据），
    # 该断言只验证测试隔离目录生效，不再要求仓库目录无文件。
    assert wl.list_watchlist() == []  # 隔离目录内为空
