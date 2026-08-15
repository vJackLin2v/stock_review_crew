# -*- coding: utf-8 -*-
"""离线回放：完整行情快照落盘 + 重跑历史复盘离线复用（2026-08-15）。

验收覆盖：
1. save_market_data / load_market_data / has_market_data 落盘与读取（隔离目录）；
2. 已有 market_data.json 的日期重跑：无任何实时取数（fetch 抛异常仍完成），
   market_data 来自缓存、降级说明含「离线回放」；
3. 无 market_data.json 的新日期：仍正常实时取数（向后兼容）；
4. REVIEW_FORCE_FETCH=1 时即使有缓存也强制实时取数并刷新缓存；
5. 离线回放产出的 market_data/market_weather/emotion_cycle 与实时取数一致（确定性）；
6. 模式不符的缓存不误回放（回落实时取数）。

隔离：conftest 函数级 autouse 已把 STOCK_REVIEW_DATA_DIR 指到独立临时目录，
本文件测试绝不写仓库 data/。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import stock_review_crew.graph as graph
from stock_review_crew.storage import reviews


def _state(date="2026-07-15", mode="close", **extra):
    s = {"date": date, "mode": mode}
    s.update(extra)
    return s


def _close_blocks():
    return {
        "index_trend": {
            "indices": {"shanghai": {"close": 3764.0, "pct_change": 0.0052}},
            "source": "测试",
            "degraded": False,
        },
        "zt_pool": {
            "limit_up": {"count": 47, "stocks": []},
            "limit_down": {"count": 3, "stocks": []},
            "source": "测试",
            "degraded": False,
        },
        "sectors": {"top5": [{"板块": "半导体", "涨跌幅": 0.02}]},
        "breadth": {"up": 2800, "down": 1800},
        "sentiment": {"up_count": 2800, "down_count": 1800},
        "news": {"caixin": [{"summary": "政策利好"}], "cctv": [], "source": "财新"},
    }


def _make_payload(date="2026-07-15", mode="close", market_data="缓存文本", weather="晴",
                  emotion="主升", source="fetch_mode_data"):
    return {
        "date": date,
        "mode": mode,
        "mode_label": "收盘复盘",
        "blocks": _close_blocks(),
        "market_data": market_data,
        "market_weather": weather,
        "emotion_cycle": emotion,
        "raw_news": "",
        "degraded_notes": [],
        "source": source,
        "saved_at": "2026-07-15T15:00:00",
    }


# ── 1. 存储 helper ──

def test_save_load_roundtrip():
    d = "2026-07-15"
    payload = _make_payload(date=d)
    assert not reviews.has_market_data(d)
    assert reviews.save_market_data(d, payload) == d
    assert reviews.has_market_data(d)
    loaded = reviews.load_market_data(d)
    assert loaded["mode"] == "close"
    assert loaded["blocks"]["zt_pool"]["limit_up"]["count"] == 47
    assert loaded["market_weather"] == "晴"
    # 落盘到 {date}/market_data.json（与 meta/report/snapshot 同目录）
    path = reviews.market_data_path(d)
    assert path.name == "market_data.json"
    assert path.exists()


def test_save_market_data_invalid_date():
    with pytest.raises(ValueError):
        reviews.save_market_data("2026/07/15", _make_payload())
    with pytest.raises(ValueError):
        reviews.save_market_data("", _make_payload())


def test_load_market_data_missing_returns_empty():
    assert reviews.load_market_data("2026-01-02") == {}
    assert reviews.has_market_data("2026-01-02") is False


def test_load_market_data_corrupt_is_empty():
    d = "2026-07-15"
    reviews.save_market_data(d, _make_payload(date=d))
    path = reviews.market_data_path(d)
    path.write_text("{ 坏了", encoding="utf-8")
    assert reviews.load_market_data(d) == {}


# ── 2. 离线回放：有缓存时不再实时取数 ──

def test_offline_replay_skips_real_fetch(monkeypatch):
    d = "2026-07-15"
    reviews.save_market_data(d, _make_payload(date=d))
    called = []

    def boom(mode, date):
        called.append((mode, date))
        raise RuntimeError("实时取数不应被调用")

    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: boom)
    out = graph.fetch_data_node(_state(date=d))
    assert called == [], "离线回放不应触发任何实时取数"
    assert out["market_data"] == "缓存文本"
    assert out["market_weather"] == "晴"
    assert out["emotion_cycle"] == "主升"
    assert out["snapshot"]["source"] == "离线回放"
    assert any("离线回放" in n for n in out["degraded_notes"])
    assert out["degraded"] is True


def test_offline_replay_full_graph_completes(monkeypatch):
    """验收 2：重跑复盘（整图）仍完成，market_data 来自缓存、降级含「离线回放」。"""
    from stock_review_crew.graph import build_graph, default_initial_state

    d = "2026-07-15"
    reviews.save_market_data(d, _make_payload(date=d, market_data="# 缓存报告文本"))

    class _Resp:
        def __init__(self, content):
            self.content = content

    class _FakeLLM:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, config=None):
            return _Resp(_llm_reply(messages))

    def _llm_reply(messages):
        user = ""
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "user":
                user = m.get("content", "")
        if "报告要求" in user:
            return "# 2026-07-15 A股收盘复盘报告\n## 一、市场概况\n略\n\n> 仅供参考，不构成投资建议"
        if "实质性分歧" in user:
            return "分歧判断：无\n总结：观点一致。"
        return "## 1、昨日复盘验证\n跳过\n## 2、今日判断\n看多\n## 3、操作计划\n进攻"

    monkeypatch.setattr(graph, "_llm_ready", lambda: True)
    llm = _FakeLLM()
    monkeypatch.setattr(graph, "_llm_pair", lambda: (llm, _FakeLLM()))
    monkeypatch.setattr(graph, "_callbacks", lambda: [])

    def boom(mode, date):
        raise RuntimeError("不应实时取数")

    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: boom)
    monkeypatch.setattr(graph, "_load_analyst_tools", lambda: (None, None))

    app = build_graph()
    state = app.invoke(default_initial_state(d, mode="close"))
    # 下游 news 节点会向 market_data 追加「资讯分析」段，故用前缀断言缓存文本来源
    assert state["market_data"].startswith("# 缓存报告文本")
    assert any("离线回放" in n for n in state["degraded_notes"])
    assert "不构成投资建议" in state["final_report"]


# ── 3. 无缓存的新日期：正常实时取数（向后兼容） ──

def test_no_cache_falls_through_to_real_fetch(monkeypatch):
    d = "2026-07-16"
    calls = []

    def fetcher(mode, date):
        calls.append((mode, date))
        return {"date": date, "mode": mode, "mode_label": "收盘复盘",
                "blocks": _close_blocks(), "degraded": [], "degraded_flag": False}

    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: fetcher)
    out = graph.fetch_data_node(_state(date=d))
    assert calls == [("close", d)]
    assert out["snapshot"]["source"] == "fetch_mode_data"
    assert "行情数据快照" in out["market_data"]
    # 实时取数后应自动落盘，供后续离线回放
    assert reviews.has_market_data(d)
    assert reviews.load_market_data(d)["mode"] == "close"


# ── 4. REVIEW_FORCE_FETCH=1 强制实时取数并刷新缓存 ──

def test_force_fetch_refreshes_cache(monkeypatch):
    d = "2026-07-17"
    reviews.save_market_data(d, _make_payload(date=d, market_data="旧缓存"))
    monkeypatch.setenv("REVIEW_FORCE_FETCH", "1")
    calls = []

    def fetcher(mode, date):
        calls.append((mode, date))
        return {"date": date, "mode": mode, "mode_label": "收盘复盘",
                "blocks": _close_blocks(), "degraded": [], "degraded_flag": False}

    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: fetcher)
    out = graph.fetch_data_node(_state(date=d))
    assert calls == [("close", d)], "强制实时取数必须真正调用 fetch_mode_data"
    assert out["snapshot"]["source"] == "fetch_mode_data"
    assert out["market_data"] != "旧缓存"
    # 缓存被刷新为新取数结果
    assert reviews.load_market_data(d)["market_data"] == out["market_data"]


def test_force_fetch_by_state_flag(monkeypatch):
    """state.force_fetch 真值同样强制实时取数。"""
    d = "2026-07-18"
    reviews.save_market_data(d, _make_payload(date=d, market_data="旧缓存"))
    calls = []

    def fetcher(mode, date):
        calls.append((mode, date))
        return {"date": date, "mode": mode, "mode_label": "收盘复盘",
                "blocks": _close_blocks(), "degraded": [], "degraded_flag": False}

    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: fetcher)
    out = graph.fetch_data_node(_state(date=d, force_fetch=True))
    assert calls == [("close", d)]
    assert out["market_data"] != "旧缓存"


# ── 5. 确定性：离线回放与实时取数（同 blocks）命中同值 ──

def test_replay_deterministic_with_real_fetch(monkeypatch):
    d = "2026-07-19"
    blocks = _close_blocks()
    calls = []

    def fetcher(mode, date):
        calls.append((mode, date))
        return {"date": date, "mode": mode, "mode_label": "收盘复盘",
                "blocks": blocks, "degraded": [], "degraded_flag": False}

    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: fetcher)
    live = graph.fetch_data_node(_state(date=d))  # 实时取数并落盘
    # 清掉 fetcher，改成离线回放路径（同 blocks）
    live2 = graph.fetch_data_node(_state(date=d))
    assert calls == [("close", d)], "第二次应命中缓存不再取数"
    assert live2["market_data"] == live["market_data"]
    assert live2["market_weather"] == live["market_weather"]
    assert live2["emotion_cycle"] == live["emotion_cycle"]


# ── 6. 模式不符的缓存不误回放 ──

def test_mode_mismatch_cache_skips_replay(monkeypatch):
    d = "2026-07-20"
    # 缓存是 close，本次请求 auction → 不应回放
    reviews.save_market_data(d, _make_payload(date=d, mode="close"))
    calls = []

    def fetcher(mode, date):
        calls.append((mode, date))
        return {"date": date, "mode": mode, "mode_label": "竞价复盘",
                "blocks": {"auction": {"count": 5}}, "degraded": [], "degraded_flag": False}

    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: fetcher)
    out = graph.fetch_data_node(_state(date=d, mode="auction"))
    assert calls == [("auction", d)], "模式不符应实时取数"
    assert out["snapshot"]["mode"] == "auction"
    assert out["snapshot"]["source"] == "fetch_mode_data"


# ── 7. 预热脚本：日期发现 + 落盘（mock 取数，避免真实网络） ──

# ── 8. 追加需求（2026-08-16）：取数校验错误消息区分「实时失败」vs「离线回放快照缺失」 ──

def _bad_index_trend_blocks():
    """一次性快照：index_trend 被降级为 source=数据缺失（历史取数超时遗留的坏快照）。"""
    blocks = _close_blocks()
    blocks["index_trend"] = {"source": "数据缺失", "degraded": True}
    return blocks


def _bad_payload(date="2026-08-16", mode="close"):
    """坏快照的落盘 payload（blocks 里 index_trend 数据缺失，mode=close）。"""
    return {
        "date": date,
        "mode": mode,
        "mode_label": "收盘复盘",
        "blocks": _bad_index_trend_blocks(),
        "market_data": "坏快照缓存文本",
        "market_weather": "晴",
        "emotion_cycle": "主升",
        "raw_news": "",
        "degraded_notes": [graph._OFFLINE_NOTE],
        "source": "fetch_mode_data",
        "saved_at": "2026-08-16T15:00:00",
    }


def test_replay_bad_snapshot_validation_msg_distinguishes_offline(monkeypatch):
    """B1：回放命中坏快照（index_trend source=数据缺失）→ validate_data_node 抛
    DataValidationError，且消息区分来源——含「离线回放快照」与「market_data.json」，
    明确提示是历史遗留、可删快照强制实时重取，而非误导向「查网络/代理」。"""
    d = "2026-08-16"
    reviews.save_market_data(d, _bad_payload(date=d, mode="close"))
    # 无 REVIEW_FORCE_FETCH、state 无 force_fetch → 走离线回放路径（不实时取数）
    out = graph.fetch_data_node(_state(date=d, mode="close"))
    assert out["snapshot"]["source"] == "离线回放", "坏快照应命中离线回放路径"

    state = _state(date=d, mode="close", snapshot=out["snapshot"])
    with pytest.raises(graph.DataValidationError) as ei:
        graph.validate_data_node(state)
    msg = str(ei.value)
    assert "离线回放快照" in msg, f"回放缺失消息应含『离线回放快照』，实际：{msg}"
    assert "market_data.json" in msg, f"回放缺失消息应含 market_data.json 指引删除重取，实际：{msg}"
    assert "全部数据源不可用" not in msg, f"回放缺失不应误指实时所有源不可用：{msg}"


def test_real_fetch_missing_validation_msg_keeps_live_wording(monkeypatch):
    """B2：实时取数路径缺失核心块 → validate_data_node 抛错但消息不变——
    仍含「全部数据源不可用」，且**不含**「离线回放快照」（原实时消息不被污染）。"""
    d = "2026-08-17"
    # 无快照（该日期无 market_data.json）→ 走实时取数路径；注入 fetcher 返回坏 blocks
    def fetcher(mode, date):
        return {"date": date, "mode": mode, "mode_label": "收盘复盘",
                "blocks": _bad_index_trend_blocks(), "degraded": [], "degraded_flag": False}

    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: fetcher)
    out = graph.fetch_data_node(_state(date=d, mode="close"))
    assert out["snapshot"]["source"] == "fetch_mode_data", "无快照应命中实时取数路径"

    state = _state(date=d, mode="close", snapshot=out["snapshot"])
    with pytest.raises(graph.DataValidationError) as ei:
        graph.validate_data_node(state)
    msg = str(ei.value)
    assert "全部数据源不可用" in msg, f"实时缺失消息应保留『全部数据源不可用』，实际：{msg}"
    assert "离线回放快照" not in msg, f"实时缺失消息不应含离线回放字样（原消息不变）：{msg}"


def test_replay_good_snapshot_passes_validation(monkeypatch):
    """B3（坏快照对照组）：index_trend 完整的好快照 → 回放 → validate_data_node
    不抛错（返回 {}），确保按 source 区分消息的实现不引入快照可用性回归。"""
    d = "2026-08-18"
    reviews.save_market_data(d, _make_payload(date=d, mode="close"))
    out = graph.fetch_data_node(_state(date=d, mode="close"))
    assert out["snapshot"]["source"] == "离线回放"
    state = _state(date=d, mode="close", snapshot=out["snapshot"])
    # 好快照核心块完整 → 校验通过，无 DataValidationError
    assert graph.validate_data_node(state) == {}


def test_preheat_script_discovers_and_saves(monkeypatch):
    import scripts.preheat_reviews as preheat  # noqa: E402

    # 构造已存在复盘目录（纯目录即可，脚本只按日期目录发现）
    root = reviews.reviews_root()
    dates = ["2026-07-21", "2026-07-22"]
    for d in dates:
        (root / d).mkdir(parents=True, exist_ok=True)

    def _fake_fetch(mode, date):
        return {"date": date, "mode": mode, "mode_label": "收盘复盘",
                "blocks": _close_blocks(), "degraded": [], "degraded_flag": True}

    # 打桩 fetch_mode_data / 天气情绪 / 渲染，避免真实网络
    monkeypatch.setattr(
        "stock_review_crew.tools.stock_data.fetch_mode_data", _fake_fetch)
    monkeypatch.setattr(preheat, "_compute_weather_text", lambda blocks, d: "晴")
    monkeypatch.setattr(preheat, "_compute_emotion_cycle", lambda blocks: "主升")
    monkeypatch.setattr(preheat, "_render_market_md",
                        lambda blocks, mode, notes: "预热渲染文本")

    n_fail, reasons = preheat.preheat_dates(dates, mode="close")
    assert n_fail == 0 and not reasons
    for d in dates:
        assert reviews.has_market_data(d), f"{d} 应已落盘 market_data.json"
        payload = reviews.load_market_data(d)
        assert payload["mode"] == "close"
        assert payload["market_weather"] == "晴"
        assert payload["emotion_cycle"] == "主升"
        assert payload["market_data"] == "预热渲染文本"
