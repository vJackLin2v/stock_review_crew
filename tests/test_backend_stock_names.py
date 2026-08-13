"""M5 股票代码批量转名称接口测试（2026-08-12 新增）。

契约（任务单 + docs/requirements.md §六）：
- POST /api/data/stock-names {codes: [...]} -> {names: {code: name|null}}；
- 空数组 -> {}；非 6 位代码忽略；重复去重；单代码失败 -> null 不抛错；
- 数据源降级链：腾讯批量行情（_stock_names_via_tx_batch）-> 单代码 fetch_stock_quote 兜底；
- 5 秒名称缓存去重（防限流，失败结果同样缓存）；
- 防误伤：随机 6 位数字（非真实代码）查不到名称返回 null，前端据此保持原样展示。

离线确定性：全程 monkeypatch 网络层，禁止真实请求。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import backend.main as bm
import stock_review_crew.tools.realtime as realtime

client = TestClient(bm.app)


@pytest.fixture(autouse=True)
def _clean_name_cache():
    """模块级名称缓存跨测试共享，逐个清理避免顺序耦合。"""
    bm._name_cache.clear()
    yield
    bm._name_cache.clear()


def test_happy_path_contract(monkeypatch):
    """核心契约：{codes: [...]} -> {names: {code: name}}。"""
    monkeypatch.setattr(
        bm, "_stock_names_via_tx_batch",
        lambda codes: {"600519": "贵州茅台", "000001": "平安银行"},
    )
    resp = client.post("/api/data/stock-names", json={"codes": ["600519", "000001"]})
    assert resp.status_code == 200
    assert resp.json() == {"names": {"600519": "贵州茅台", "000001": "平安银行"}}


def test_empty_array_returns_empty(monkeypatch):
    """空数组 -> {}，且不发任何数据源请求。"""
    calls = []
    monkeypatch.setattr(bm, "_stock_names_via_tx_batch", lambda codes: calls.append(codes) or {})
    resp = client.post("/api/data/stock-names", json={"codes": []})
    assert resp.status_code == 200
    assert resp.json() == {"names": {}}
    assert calls == []


def test_invalid_codes_ignored_and_dedup(monkeypatch):
    """非 6 位代码忽略（含 8 位日期串 20260812）、重复去重、单次批量请求。"""
    seen = []

    def fake(codes):
        seen.append(list(codes))
        return {c: f"名称{c}" for c in codes}

    monkeypatch.setattr(bm, "_stock_names_via_tx_batch", fake)
    resp = client.post(
        "/api/data/stock-names",
        json={"codes": ["600519", "600519", "abc", "12345", "1234567", "", None, "20260812", 600519]},
    )
    assert resp.status_code == 200
    assert set(resp.json()["names"]) == {"600519"}
    assert seen == [["600519"]]  # 去重后仅一次请求、仅唯一代码


def test_random_six_digit_no_name_is_null(monkeypatch):
    """防误伤：随机 6 位数字（非真实代码）查不到名称 -> null，前端保持原样。"""
    monkeypatch.setattr(bm, "_stock_names_via_tx_batch", lambda codes: {})
    monkeypatch.setattr(realtime, "fetch_stock_quote", lambda code: (_ for _ in ()).throw(RuntimeError("无此代码")))
    resp = client.post("/api/data/stock-names", json={"codes": ["493702"]})
    assert resp.status_code == 200
    assert resp.json() == {"names": {"493702": None}}


def test_single_failure_returns_null_no_error(monkeypatch):
    """批量与单代码兜底全挂：返回 null 且 200，绝不 500。"""
    monkeypatch.setattr(bm, "_stock_names_via_tx_batch", lambda codes: (_ for _ in ()).throw(RuntimeError("network down")))
    monkeypatch.setattr(realtime, "fetch_stock_quote", lambda code: (_ for _ in ()).throw(RuntimeError("network down")))
    resp = client.post("/api/data/stock-names", json={"codes": ["600519", "000001"]})
    assert resp.status_code == 200
    assert resp.json() == {"names": {"600519": None, "000001": None}}


def test_quote_fallback_for_missing(monkeypatch):
    """批量只命中部分代码，其余走单代码实时行情兜底。"""
    monkeypatch.setattr(bm, "_stock_names_via_tx_batch", lambda codes: {"000001": "平安银行"})
    monkeypatch.setattr(realtime, "fetch_stock_quote", lambda code: {"code": code, "name": "贵州茅台"})
    resp = client.post("/api/data/stock-names", json={"codes": ["600519", "000001"]})
    assert resp.status_code == 200
    assert resp.json() == {"names": {"600519": "贵州茅台", "000001": "平安银行"}}


def test_batch_returns_non_dict_guarded(monkeypatch):
    """批量接口返回非 dict 脏数据时兜底单代码，不抛错。"""
    monkeypatch.setattr(bm, "_stock_names_via_tx_batch", lambda codes: None)
    monkeypatch.setattr(realtime, "fetch_stock_quote", lambda code: {"name": "贵州茅台"})
    resp = client.post("/api/data/stock-names", json={"codes": ["600519"]})
    assert resp.status_code == 200
    assert resp.json() == {"names": {"600519": "贵州茅台"}}


def test_cache_ttl_dedup(monkeypatch):
    """5 秒缓存：同批代码重复请求不重复打数据源。"""
    calls = []
    monkeypatch.setattr(
        bm, "_stock_names_via_tx_batch",
        lambda codes: calls.append(list(codes)) or {"600519": "贵州茅台"},
    )
    for _ in range(2):
        resp = client.post("/api/data/stock-names", json={"codes": ["600519"]})
        assert resp.status_code == 200
        assert resp.json()["names"] == {"600519": "贵州茅台"}
    assert len(calls) == 1


def test_null_cached_within_ttl(monkeypatch):
    """失败结果同样缓存：TTL 内重复请求不反复打单代码兜底（防限流）。"""
    quote_calls = []
    monkeypatch.setattr(bm, "_stock_names_via_tx_batch", lambda codes: {})
    monkeypatch.setattr(realtime, "fetch_stock_quote", lambda code: quote_calls.append(code) or {"name": None})
    for _ in range(2):
        resp = client.post("/api/data/stock-names", json={"codes": ["600519"]})
        assert resp.status_code == 200
        assert resp.json()["names"] == {"600519": None}
    assert len(quote_calls) == 1


def test_cache_expiry_refetch(monkeypatch):
    """TTL 归零后重新打数据源。"""
    monkeypatch.setattr(bm, "_NAME_CACHE_TTL_SECONDS", 0)
    calls = []
    monkeypatch.setattr(
        bm, "_stock_names_via_tx_batch",
        lambda codes: calls.append(1) or {"600519": "贵州茅台"},
    )
    client.post("/api/data/stock-names", json={"codes": ["600519"]})
    client.post("/api/data/stock-names", json={"codes": ["600519"]})
    assert len(calls) == 2


def test_400_validation():
    """请求体校验：缺 codes / codes 非数组 / 非对象均 400 中文。"""
    assert client.post("/api/data/stock-names", json={}).status_code == 400
    assert client.post("/api/data/stock-names", json={"codes": "600519"}).status_code == 400
    assert client.post("/api/data/stock-names", json=[1, 2]).status_code == 400


def test_tx_batch_parser(monkeypatch):
    """腾讯批量行情响应解析：v_sh600519="1~贵州茅台~..." 取第 2 段为名称。"""

    class FakeResp:
        text = (
            'v_sh600519="1~贵州茅台~600519~1690.00~1692.00~1688.00~100~200~300";\n'
            'v_sz000001="1~平安银行~000001~10.50~10.60~10.40~50~60~70";'
        )

        def raise_for_status(self):
            return None

    monkeypatch.setattr("requests.get", lambda url, **kw: FakeResp())
    out = bm._stock_names_via_tx_batch(["600519", "000001"])
    assert out == {"600519": "贵州茅台", "000001": "平安银行"}


def test_tx_symbol_mapping():
    """腾讯代码前缀映射与 tools.realtime 同口径：6/9/5→sh、4/8/92→bj、其余→sz。"""
    assert bm._stock_name_tx_symbol("600519") == "sh600519"
    assert bm._stock_name_tx_symbol("688111") == "sh688111"
    assert bm._stock_name_tx_symbol("588170") == "sh588170"
    assert bm._stock_name_tx_symbol("000001") == "sz000001"
    assert bm._stock_name_tx_symbol("300750") == "sz300750"
    assert bm._stock_name_tx_symbol("830799") == "bj830799"
