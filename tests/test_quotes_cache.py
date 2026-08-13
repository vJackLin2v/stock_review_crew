# -*- coding: utf-8 -*-
"""R14-A4 行情缓存验收测试（测试先行，硬门）：命中缓存不联网 / 未命中抓取并落盘 / 失败 degraded / 离线全命中。

契约（唯一真源：docs/requirements-r14.md 第四节.2）：
- 缓存文件：{STOCK_REVIEW_DATA_DIR}/quotes_cache/{date}.json，
  内容 {date, fetched_at, source, quotes, degraded}；
- quotes[code] = {name, pre_close, high, low, close}；抓取失败的代码【不在】quotes 里（而非 None）；
- degraded：该日取数降级/缺失标注（铁律 7，绝不静默错报）；
- ensure_quotes(date, codes, fixture=False)：缓存优先（命中不联网、不改写文件）；
  未命中逐 code 抓取并落盘（真实降级链：历史主源→腾讯→...）；失败代码 degraded 标注且不在结果；
- save_quotes(date, payload) 手工落盘 / load_quotes(date) 读取（缺失返回 None）。
模块未实现时整文件 skip；集成阶段启用必须全绿。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

try:
    from stock_review_crew.accounts import quotes
except ImportError:  # pragma: no cover - A2 未实现
    quotes = None

pytestmark = pytest.mark.skipif(
    quotes is None, reason="A2 accounts/quotes.py 未实现（R14 开发中，集成阶段启用）"
)


@pytest.fixture()
def data_dir():
    """仓库内 .tmp 隔离目录（沙箱 pytest tmp_path 有 ACL 问题，见 PLAYBOOK），自建自清。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / "a4_quotes"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    for _ in range(3):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            break


def _quote(code="600010", close=8.5, pre_close=8.3, high=8.6, low=8.2, name="包钢股份"):
    return {"name": name, "pre_close": pre_close, "high": high, "low": low, "close": close}


def _save_cache(data_dir, date_str, quotes_map, degraded=None, source="test"):
    p = quotes.save_quotes(date_str, {
        "date": date_str, "quotes": quotes_map,
        "degraded": degraded or [], "source": source,
    })
    return p


def test_cache_path_under_quotes_cache(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    assert quotes.quotes_cache_root() == data_dir / "quotes_cache"
    assert quotes.quote_path("2026-08-11") == data_dir / "quotes_cache" / "2026-08-11.json"


def test_miss_fetch_and_persist(monkeypatch, data_dir):
    """未命中 → 抓取并落盘；文件 schema 与 load_quotes 往返一致（结构断言，不依赖具体行情数值）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    r = quotes.ensure_quotes("2026-08-11", ["600010"])
    assert "600010" in r["quotes"], "有效代码应抓取成功（依赖行情源可用，见模块文档）"
    assert r["date"] == "2026-08-11"
    assert r["source"] and isinstance(r["degraded"], list)
    p = quotes.quote_path("2026-08-11")
    assert p.exists(), "未命中抓取后必须落盘"
    raw = json.loads(p.read_text("utf-8"))
    assert raw["date"] == "2026-08-11"
    assert {"date", "fetched_at", "source", "quotes", "degraded"} <= set(raw)
    assert set(raw["quotes"]["600010"]) == {"name", "pre_close", "high", "low", "close"}
    assert quotes.load_quotes("2026-08-11")["quotes"]["600010"] == r["quotes"]["600010"]


def test_cache_hit_no_network_and_bytes_stable(monkeypatch, data_dir):
    """命中缓存 → 返回缓存内容（含 degraded/source 原样保留），文件字节不变（不联网）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    payload_quotes = {"600010": _quote(), "600519": _quote(code="600519", name="贵州茅台")}
    degraded = ["600999 2026-08-11 行情获取失败（主源+腾讯兜底均不可用）"]
    _save_cache(data_dir, "2026-08-11", payload_quotes, degraded=degraded)
    p = quotes.quote_path("2026-08-11")
    b1 = p.read_bytes()
    r = quotes.ensure_quotes("2026-08-11", ["600010", "600519"])
    assert r["quotes"] == payload_quotes
    assert r["degraded"] == degraded, "缓存中的降级标注必须保留（不静默恢复）"
    assert r["source"] == "test"
    assert p.read_bytes() == b1, "命中缓存不得改写缓存文件"


def test_fetch_failure_degraded_and_code_absent(monkeypatch, data_dir):
    """抓取失败（无效代码恒失败，确定性）→ degraded 标注且该代码不在结果/文件里（而非 None）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    r = quotes.ensure_quotes("2026-08-11", ["000000"])
    assert "000000" not in r["quotes"], "失败代码不得出现在结果里"
    assert r["quotes"] == {}
    assert any("000000" in d for d in r["degraded"]), "失败代码必须 degraded 标注"
    raw = json.loads(quotes.quote_path("2026-08-11").read_text("utf-8"))
    assert "000000" not in raw["quotes"], "缺失代码不在文件里（而非 None）"
    assert any("000000" in d for d in raw["degraded"])
    # 命中缓存：失败代码仍然缺失且 degraded 保留（不静默补成成功）
    r2 = quotes.ensure_quotes("2026-08-11", ["000000"])
    assert "000000" not in r2["quotes"]
    assert any("000000" in d for d in r2["degraded"])


def test_offline_all_hit_from_cache(monkeypatch, data_dir):
    """离线重放：缓存已就绪时 ensure_quotes 全部命中缓存（不触碰真实抓取链路）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _save_cache(data_dir, "2026-08-11", {"600010": _quote()})
    r = quotes.ensure_quotes("2026-08-11", ["600010"])
    assert r["quotes"] == {"600010": _quote()}
    assert r["degraded"] == []


def test_load_quotes_missing_returns_none(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    assert quotes.load_quotes("2099-01-01") is None


# ---------- 历史日期不采信腾讯实时价（2026-08-13 缺陷修复） ----------

def test_historical_date_no_realtime_fallback(monkeypatch, data_dir):
    """历史日期主源失败 → 返回 (None, "") 不采信腾讯实时价；当天日期仍可腾讯兜底。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    from datetime import datetime
    import stock_review_crew.tools.stock_data as stock_data_mod

    def boom(code, date_str):
        raise RuntimeError("主源不可用（模拟）")

    monkeypatch.setattr(stock_data_mod, "fetch_stock_info", boom)
    # 腾讯实时兜底可正常返回 → 若被历史日期采信将「成功」；断言历史日期必须忽略
    monkeypatch.setattr(quotes, "_fetch_tx_quote", lambda code: _quote())

    # 历史日期：主源失败 → (None, "")，绝不退实时价
    q, src = quotes._fetch_day_quote_ex("600519", "2026-08-01")
    assert q is None and src == "", "历史日期主源失败不得采信腾讯实时价"

    # 当天日期：允许腾讯实时兜底（保留现有行为）
    today = datetime.now().strftime("%Y-%m-%d")
    q, src = quotes._fetch_day_quote_ex("600519", today)
    assert q is not None and src == "腾讯qt.gtimg.cn", "当天日期仍可腾讯实时兜底"


def test_historical_missing_quote_degraded_note(monkeypatch, data_dir):
    """历史日期缺行情：degraded 如实标注「历史行情主源不可用，且不采信实时价」。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    import stock_review_crew.tools.stock_data as stock_data_mod

    def boom(code, date_str):
        raise RuntimeError("主源不可用（模拟）")

    monkeypatch.setattr(stock_data_mod, "fetch_stock_info", boom)
    monkeypatch.setattr(quotes, "_fetch_tx_quote", lambda code: _quote())

    r = quotes.ensure_quotes("2026-08-01", ["600519"])
    assert "600519" not in r["quotes"], "历史日期不得用实时价补成成功"
    assert any("历史行情主源不可用" in d and "不采信实时价" in d
               for d in r["degraded"]), "历史缺行情必须如实标注"
