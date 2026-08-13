# -*- coding: utf-8 -*-
"""资讯开关测试（2026-08-12）：NEWS_ENABLED 默认关闭=屏蔽资讯。

覆盖：
- config.news_enabled()：默认 off、1/true/on/yes 开启；
- 复盘引擎：关闭时取数剥离资讯块（snapshot/market_data 无资讯）、资讯分析节点跳过；
- 聊天：news/news_headlines/realtime_news 三键关闭时返回「资讯已关闭」；
- 实时快讯：关闭时不抓新浪7x24，快照返回空列表（前端自动隐藏）。

注意：tests/conftest.py 显式 NEWS_ENABLED=1（保持既有资讯链路用例语义），
本文件用例自行置 0/1。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew import config
import stock_review_crew.chat as chat
import stock_review_crew.graph as graph
import stock_review_crew.tools.market_extra as market_extra
import stock_review_crew.tools.realtime as realtime


def _i1_blocks() -> dict:
    return {
        "date": "2026-08-12",
        "mode": "close",
        "mode_label": "收盘复盘",
        "blocks": {
            "index": {
                "indices": {"shanghai": {"close": 3764.0, "pct_change": 0.0052}},
                "source": "测试源", "degraded": False, "degraded_reason": [],
            },
            "news": {"caixin": [{"summary": "政策利好"}], "cctv": ["要闻一"], "source": "财新"},
        },
        "degraded": [],
        "degraded_flag": False,
    }


def _patch_fetch(monkeypatch) -> None:
    """把取数与天气全部 mock（避免网络）。"""
    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: (lambda mode, date: _i1_blocks()))
    monkeypatch.setattr(
        market_extra, "compute_market_weather",
        lambda data: {"weather": "晴", "emotion_label": "偏暖", "emotion_score": 66},
    )
    monkeypatch.setattr(
        market_extra, "fetch_market_weather",
        lambda date: {"weather": "晴", "emotion_label": "偏暖", "emotion_score": 66},
    )


# ── 开关本身 ──

def test_news_enabled_default_off(monkeypatch):
    monkeypatch.delenv("NEWS_ENABLED", raising=False)
    assert config.news_enabled() is False
    for v in ("1", "true", "on", "yes", "TRUE"):
        monkeypatch.setenv("NEWS_ENABLED", v)
        assert config.news_enabled() is True, v
    for v in ("0", "false", "off", "no", ""):
        monkeypatch.setenv("NEWS_ENABLED", v)
        assert config.news_enabled() is False, v


# ── 复盘引擎 ──

def test_fetch_data_node_strips_news_when_disabled(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "0")
    _patch_fetch(monkeypatch)
    out = graph.fetch_data_node({"mode": "close", "date": "2026-08-12"})
    assert out["raw_news"] == ""
    assert "news" not in out["snapshot"]["data"]
    assert "今日财经资讯" not in out["market_data"]
    assert "政策利好" not in out["market_data"]


def test_fetch_data_node_keeps_news_when_enabled(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "1")
    _patch_fetch(monkeypatch)
    out = graph.fetch_data_node({"mode": "close", "date": "2026-08-12"})
    assert out["raw_news"] != ""
    assert "今日财经资讯" in out["market_data"]
    assert "政策利好" in out["market_data"]


def test_news_analyst_node_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "0")
    emitted: list[tuple] = []
    monkeypatch.setattr(graph, "_emit", lambda stage, pct, msg: emitted.append((stage, pct)))
    out = graph.news_analyst_node({"raw_news": "有资讯内容", "market_data": "md 正文"})
    assert out["market_data"] == "md 正文"          # 不追加「资讯分析」章节
    assert "资讯分析" not in out["market_data"]
    assert "degraded" not in out
    assert not any(stage == "news" for stage, _ in emitted), "关闭时不应 emit news 阶段"


def test_news_analyst_node_emits_when_enabled(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "1")
    emitted: list[tuple] = []
    monkeypatch.setattr(graph, "_emit", lambda stage, pct, msg: emitted.append((stage, pct)))
    out = graph.news_analyst_node({"raw_news": "{}", "market_data": "md 正文"})
    assert any(stage == "news" for stage, _ in emitted), "开启时应 emit news 阶段"
    assert "资讯分析" in out["market_data"]


# ── 聊天 ──

def test_chat_news_tools_disabled(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "0")
    calls: list[str] = []
    monkeypatch.setattr(chat, "_call_tools_func", lambda *a, **k: calls.append(a[1]) or {"news": []})
    for key in ("news", "news_headlines", "realtime_news"):
        res = chat._fetch_data(key, {"target": ""}, {"target": ""})
        assert res["available"] is False, key
        assert "资讯已关闭" in res["note"], key
    assert calls == [], "关闭时不应调用资讯工具"


def test_chat_news_tools_enabled(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "1")
    monkeypatch.setattr(
        chat, "_call_tools_func",
        lambda *a, **k: {"caixin": [{"summary": "政策利好"}], "source": "财新"},
    )
    res = chat._fetch_data("news", {"target": ""}, {"target": ""})
    assert res["available"] is True
    assert "政策利好" in str(res["data"])


# ── 实时快讯 ──

def test_realtime_snapshot_no_news_when_disabled(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "0")
    news_calls: list[str] = []
    monkeypatch.setattr(realtime, "market_status", lambda now=None: {"is_trading_day": False})
    monkeypatch.setattr(realtime, "fetch_indices", lambda: {"indices": []})
    monkeypatch.setattr(realtime, "fetch_zt_stats", lambda **k: {"total": 0})
    monkeypatch.setattr(realtime, "fetch_sector_flow", lambda: {"sectors": []})
    monkeypatch.setattr(realtime, "fetch_news", lambda: news_calls.append("fetch_news") or {"news": [1]})
    snap = realtime.fetch_realtime_snapshot()
    assert news_calls == [], "关闭时不应抓新浪7x24 快讯"
    assert snap["news"] == {"news": [], "source": "closed"}


def test_realtime_snapshot_keeps_news_when_enabled(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "1")
    monkeypatch.setattr(realtime, "market_status", lambda now=None: {"is_trading_day": False})
    monkeypatch.setattr(realtime, "fetch_indices", lambda: {"indices": []})
    monkeypatch.setattr(realtime, "fetch_zt_stats", lambda **k: {"total": 0})
    monkeypatch.setattr(realtime, "fetch_sector_flow", lambda: {"sectors": []})
    monkeypatch.setattr(realtime, "fetch_news", lambda: {"news": [{"text": "快讯一"}]})
    snap = realtime.fetch_realtime_snapshot()
    assert snap["news"]["news"][0]["text"] == "快讯一"
    assert "新浪7x24" in snap["sources"]
