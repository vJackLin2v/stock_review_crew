"""I2 复盘引擎测试：6 模式取数分发、全流程降级、进度钩子、无辩论契约、结果契约。"""

import sys
import re
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import stock_review_crew.graph as graph
from stock_review_crew.graph import (
    build_graph,
    build_result,
    default_initial_state,
    ensure_disclaimer,
)

def _fake_data():
    return {
        "date": "2026-08-03",
        "index": {
            "shanghai": {"close": 3764.0, "pct_change": 0.0052},
            "shenzhen": {"close": 12000.0, "pct_change": -0.0012},
        },
        "zhangting": {"total": 47, "tier": {"首板": 40, "2": 5, "3": 2}},
        "dieting": {"total": 3},
        "sectors": {"top5": [{"板块": "半导体", "涨跌幅": 0.02}, {"板块": "军工", "涨跌幅": 0.015}]},
        "sentiment": {
            "yesterday_zt_count": 60,
            "avg_return_pct": 1.2,
            "red_rate_pct": 55.0,
            "lianban_rate_pct": 8.0,
        },
        "breadth": {"up": 2800, "down": 1800, "zhaban_rate_pct": 18.0},
        "news": {"caixin": [{"summary": "政策利好"}, {"summary": "行业监管"}], "cctv": ["要闻一"]},
    }


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """离线约束：LLM 用确定性 mock（2026-08-10 用户口径：LLM 不可用时复盘直接终止，
    因此测流水线逻辑的用例必须让 LLM 可用）；数据链路仍全 mock。
    2026-08-15：显式 REVIEW_FORCE_FETCH=1 强制实时取数——本文件测试流水线逻辑，维持
    「每次调用都实时取数」的原始语义（离线回放路径由 test_offline_replay.py 专项覆盖）。"""
    monkeypatch.setenv("REVIEW_FORCE_FETCH", "1")
    monkeypatch.setattr(graph, "_llm_ready", lambda: True)
    llm = _FakeLLM(_llm_factory)
    strict = _FakeLLM(_llm_factory)
    monkeypatch.setattr(graph, "_llm_pair", lambda: (llm, strict))
    monkeypatch.setattr(graph, "_callbacks", lambda: [])
    monkeypatch.setattr(graph, "_load_analyst_tools", lambda: (None, None))


def test_report_node_raises_when_llm_unavailable(fetch_mode, monkeypatch):
    """2026-08-10 用户确认契约：LLM 不可用时 report_node 抛 DataValidationError
    （中文消息，任务终止），不再降级生成规则报告。"""
    fetch_mode(lambda mode, date: _fake_data())
    monkeypatch.setattr(graph, "_llm_ready", lambda: False)
    with pytest.raises(graph.DataValidationError) as ei:
        _invoke()
    assert "LLM 不可用" in str(ei.value)
    assert "本次复盘终止" in str(ei.value)


@pytest.fixture()
def fetch_mode(monkeypatch):
    def _set(fn):
        monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: fn)
    return _set


def _invoke(mode="close", max_rounds=3, yesterday="", earlier="", callback=None):
    app = build_graph(progress_callback=callback)
    initial = default_initial_state(
        "2026-08-03",
        mode=mode,
        max_rounds=max_rounds,
        yesterday_report=yesterday,
        earlier_today=earlier,
    )
    return app.invoke(initial)


# ── 取数：模式分发与降级 ──

def test_fetch_dispatches_by_mode(fetch_mode):
    calls = []

    def fetcher(mode, date):
        calls.append((mode, date))
        return _fake_data()

    fetch_mode(fetcher)
    _invoke(mode="auction")
    assert calls == [("auction", "2026-08-03")]


def test_fetch_six_modes_no_block(fetch_mode):
    for mode in ("pre_market", "auction", "intraday_am", "noon", "intraday_pm", "close"):
        fetch_mode(lambda mode, date: _fake_data())
        state = _invoke(mode=mode)
        assert state["market_data"]
        assert state["snapshot"]["mode"] == mode
        assert state["snapshot"]["data"]


def test_i1_fetch_mode_data_shape_consumed(fetch_mode):
    """I1 契约结构 {mode_label, blocks, degraded[]} 直接消费：渲染/事实/降级标注。"""
    def fetcher(mode, date):
        return {
            "date": date,
            "mode": mode,
            "mode_label": "竞价复盘",
            "blocks": {
                "auction": {
                    "count": 120, "high_open_count": 80, "low_open_count": 40,
                    "source": "开盘啦", "degraded": False, "degraded_reason": [], "note": None,
                },
                "index_trend": {
                    "indices": {"shanghai": {"close": 3764.0, "pct_change": 0.0052}},
                    "source": "通达信akshare", "degraded": False, "degraded_reason": [],
                },
                "news": {"caixin": [{"summary": "政策利好"}], "cctv": [], "source": "财新"},
            },
            "degraded": ["auction: 开盘啦不可用"],
            "degraded_flag": True,
        }

    fetch_mode(fetcher)
    state = _invoke(mode="auction")
    assert state["snapshot"]["mode_label"] == "竞价复盘"
    # 2026-08-12（用户口径）：主源失败但备用源成功（数据可用）→ 不再降级标注
    assert not any("开盘啦不可用" in n for n in state["degraded_notes"])
    assert state["degraded"] is False
    assert "来源：开盘啦" in state["market_data"]
    assert "政策利好" in state["market_data"]
    # LLM 路径（mock）：报告由 mock LLM 生成；数据消费由 market_data/snapshot/degraded 断言覆盖
    assert state["final_report"].startswith("# 2026-08-03 A股")
    assert "仅供参考，不构成投资建议" in state["final_report"]


def test_close_dieting_extracted_from_nested_zt_pool(fetch_mode):
    """P1-E：close 跌停板从 zt_pool 嵌套 limit_down 提取（渲染 + 规则事实）。"""
    data = {
        "date": "2026-08-03",
        "index_trend": {"indices": {"shanghai": {"close": 3764.0, "pct_change": 0.0052}}},
        "zt_pool": {
            "limit_up": {"count": 47, "stocks": [{"代码": "600001", "名称": "涨停股"}]},
            "limit_down": {"count": 5, "stocks": [{"代码": "600002", "名称": "跌停股"}]},
            "zhaban": {"count": 3, "stocks": []},
            "source": "东财",
        },
        "sectors": {"top5": [{"板块": "半导体", "涨跌幅": 0.02}]},
        "breadth": {"up": 2800, "down": 1800},
        "news": {"caixin": [{"summary": "政策利好"}]},
    }
    fetch_mode(lambda mode, date: data)
    state = _invoke(mode="close")
    assert "### 跌停板" in state["market_data"]
    assert "### 跌停板：数据缺失（无数据）" not in state["market_data"]
    # LLM 路径（mock）：报告文本由 mock LLM 生成；跌停提取由 market_data 断言覆盖


def test_close_critical_blocks_without_minute(fetch_mode):
    """P1-F：close 关键闸门去掉 minute——分钟线缺失不再终止复盘（保留 index_trend）。"""
    data = {
        "date": "2026-08-03",
        "index_trend": {"indices": {"shanghai": {"close": 3764.0, "pct_change": 0.0052}}},
        "minute": {"source": "数据缺失", "degraded": True, "degraded_reason": ["不可用"]},
        "zt_pool": {"limit_up": {"count": 47, "stocks": []}, "limit_down": {"count": 2, "stocks": []}},
        "news": {"caixin": []},
    }
    fetch_mode(lambda mode, date: data)
    state = _invoke(mode="close")
    assert state["final_report"]
    # P1-F：minute 缺失不再终止复盘；LLM 成功路径下 minute 缺失不标记全局降级


def test_intraday_pm_no_dragon_tiger_declaration():
    """P1-F：intraday_pm 不再声明龙虎榜块（盘中本无此数据，避免恒显缺失）。"""
    data = {
        "date": "2026-08-03",
        "index_trend": {"indices": {"shanghai": {"close": 3764.0, "pct_change": 0.0052}}},
        "minute": {"bars": [{"time": "13:00", "close": 3764.0}]},
        "zt_pool": {"limit_up": {"count": 20, "stocks": []}},
        "breadth": {"up": 2000, "down": 2000},
        "news": {"caixin": []},
    }
    md = graph._render_market_markdown(data, "intraday_pm", [])
    assert "龙虎榜" not in md


def test_fetch_unavailable_no_block(fetch_mode, monkeypatch):
    fetch_mode(None)
    monkeypatch.setattr(graph, "_legacy_fetch", lambda state: None)
    state = _invoke()
    assert "数据缺失" in state["market_data"] or "无数据" in state["market_data"]
    assert state["degraded"] is True
    assert state["degraded_notes"]
    assert state["final_report"]


def test_legacy_fallback_marks_degraded(fetch_mode, monkeypatch):
    fetch_mode(None)
    monkeypatch.setattr(graph, "_legacy_fetch", lambda state: {
        "market_data": "## 一、今日市场微观数据\n{}",
        "raw_news": "{}",
    })
    state = _invoke()
    assert "今日市场微观数据" in state["market_data"]
    assert state["degraded"] is True


def test_legacy_fetch_has_timeout_budget(monkeypatch):
    """旧工具兜底有总预算：慢工具不阻塞主流程（注入假模块 + 缩短预算）。"""
    fake = types.ModuleType("stock_review_crew.tools.stock_data")

    def _slow(**kwargs):
        time.sleep(5)
        return "{}"

    for name in (
        "get_market_micro", "get_index_trend", "get_market_macro",
        "get_sentiment", "get_news_headlines", "get_stock_info", "search_history",
    ):
        setattr(fake, name, _slow)

    saved = sys.modules.get("stock_review_crew.tools.stock_data")
    sys.modules["stock_review_crew.tools.stock_data"] = fake
    old_timeout = graph._LEGACY_FETCH_TIMEOUT
    graph._LEGACY_FETCH_TIMEOUT = 0.3
    try:
        start = time.time()
        out = graph._legacy_fetch({"date": "2026-08-03"})
        elapsed = time.time() - start
    finally:
        graph._LEGACY_FETCH_TIMEOUT = old_timeout
        if saved is not None:
            sys.modules["stock_review_crew.tools.stock_data"] = saved
        else:
            sys.modules.pop("stock_review_crew.tools.stock_data", None)
    assert out is None
    assert elapsed < 2


def test_news_empty_not_block(fetch_mode):
    fetch_mode(lambda mode, date: {"index": {"shanghai": {"pct_change": 0.001}}})
    state = _invoke()
    assert "今日无重大资讯影响" in state["market_data"]


def test_fetch_emotion_cycle_injected(fetch_mode, monkeypatch):
    """R16：fetch_data_node 注入情绪周期定位——数据层返回值（blocks dict 入参）直通 state；
    数据层异常/未落地 → 兜底「数据不足」，绝不阻塞复盘。"""
    fetch_mode(lambda mode, date: _fake_data())
    calls = []
    monkeypatch.setattr(
        "stock_review_crew.tools.market_extra.classify_emotion_cycle",
        lambda blocks: calls.append(blocks) or "主升（红盘率65%、涨停60家）")
    state = _invoke(mode="close")
    assert state["emotion_cycle"] == "主升（红盘率65%、涨停60家）"
    assert calls and isinstance(calls[0], dict), "传入的应为 blocks dict"

    def boom(blocks):
        raise RuntimeError("数据层情绪周期异常")

    monkeypatch.setattr(
        "stock_review_crew.tools.market_extra.classify_emotion_cycle", boom)
    state2 = _invoke(mode="close")
    assert state2["emotion_cycle"] == "数据不足"


def test_news_rule_extraction_marks_degraded(monkeypatch):
    """资讯分析 LLM 失败 → 规则提取 + 降级标注（资讯节点保留的降级路径）。"""
    monkeypatch.setattr(graph, "_llm_ready", lambda: True)

    def boom(llm, messages, timeout=150.0):
        raise RuntimeError("mock LLM 资讯失败")

    monkeypatch.setattr(graph, "_invoke_llm", boom)
    out = graph.news_analyst_node({
        "market_data": "快照正文",
        "raw_news": '{"caixin": [{"summary": "降息预期升温"}], "cctv": ["央行发布会"]}',
        "degraded": False,
        "degraded_notes": [],
    })
    assert "降息预期升温" in out["market_data"]
    assert "降级" in out["market_data"]
    assert out["degraded"] is True
    assert out["degraded_notes"] == ["LLM 不可用/失败，资讯分析降级为规则提取"]


# ── 全流程（无 Key 规则引擎降级）──

def test_full_flow_with_mock_llm(fetch_mode):
    """全流程（LLM mock 可用）：分析/主持人/报告节点均走 LLM 路径，数据无降级。"""
    fetch_mode(lambda mode, date: _fake_data())
    state = _invoke()
    assert len(state["analyses"]) == 6
    groups = [a["group"] for a in state["analyses"]]
    assert groups.count("trend") == 2
    assert groups.count("sentiment") == 4
    assert state["degraded"] is False
    assert not state["degraded_notes"]
    assert "仅供参考，不构成投资建议" in state["final_report"]
    assert any("无昨日报告，跳过验证" in a["analysis"] for a in state["analyses"])
    # R16：辩论节点已移除——debate_history 恒空；主持人分歧判断写入 host_analysis
    assert state["debate_history"] == []
    assert "分歧判断" in state["host_analysis"]
    # R16：情绪周期定位注入（数据层已落地 → 周期标签字符串；异常/未落地 → 「数据不足」）
    assert isinstance(state["emotion_cycle"], str) and state["emotion_cycle"]


def test_yesterday_missing_vs_present(fetch_mode):
    fetch_mode(lambda mode, date: _fake_data())
    missing = _invoke(yesterday="")
    assert any("无昨日报告，跳过验证" in a["analysis"] for a in missing["analyses"])

    present = _invoke(yesterday="昨日报告：预判看多", earlier="午间复盘：维持看多")
    assert present["yesterday_report"] == "昨日报告：预判看多"
    assert present["earlier_today"] == "午间复盘：维持看多"
    assert "仅供参考，不构成投资建议" in present["final_report"]


def test_no_debate_rounds_always_empty(fetch_mode):
    """R16：辩论节点已移除——无论 max_rounds 如何，debate_history 恒空、
    主持人只执行一次（round_count 恒 1），不再有辩论轮次。"""
    fetch_mode(lambda mode, date: _fake_data())
    state = _invoke(max_rounds=1)
    assert state["debate_history"] == []
    state3 = _invoke(max_rounds=3)
    assert state3["debate_history"] == []
    assert state3["round_count"] == 1


def test_host_rule_fallback_analysis(monkeypatch):
    """R16：主持人 LLM 失败 → 规则降级生成 host_analysis（分歧判断：有/无 + 讨论议题格式），
    字段名与注入位置不回归。"""
    monkeypatch.setattr(graph, "_llm_ready", lambda: True)

    def boom(llm, messages, timeout=150.0):
        raise RuntimeError("mock LLM 主持人失败")

    monkeypatch.setattr(graph, "_invoke_llm", boom)
    out = graph.host_node({
        "analyses": [], "snapshot": {}, "round_count": 0, "max_rounds": 3,
        "degraded": False, "degraded_notes": [],
    })
    assert out["host_analysis"].startswith("分歧判断：")
    assert "讨论议题：" in out["host_analysis"]
    assert "降级模式" in out["host_analysis"]
    assert out["degraded"] is True
    assert out["discussion_done"] is True  # 无分歧 → 直接进入报告


def test_no_fake_debate_without_stance_conflict(fetch_mode, monkeypatch):
    """主持人 LLM 判定无分歧 → 跳过辩论（不再依赖规则立场判定）。"""
    fetch_mode(lambda mode, date: _fake_data())
    monkeypatch.setattr(
        graph, "_llm_pair",
        lambda: (_FakeLLM(_no_conflict_factory), _FakeLLM(_no_conflict_factory)))
    state = _invoke(max_rounds=3)
    assert state["debate_history"] == []


def test_host_analysis_records_disagreement_no_debate(fetch_mode, monkeypatch):
    """R16：主持人判定有分歧 → host_analysis 记录分歧判断（LLM 原文），
    但不再触发辩论——debate_history 恒空、无逐轮转写。"""
    data = {
        "date": "2026-08-03",
        "index": {
            "shanghai": {"close": 3764.0, "pct_change": 0.0052},
            "chuangye": {"close": 2400.0, "pct_change": 0.012},
            "kechuang": {"close": 900.0, "pct_change": 0.008},
        },
        "zhangting": {"total": 47, "tier": {"首板": 40, "2": 5, "3": 2}},
        "dieting": {"total": 3},
        "sectors": {"top5": [{"板块": "半导体", "涨跌幅": 0.02}]},
        "sentiment": {
            "yesterday_zt_count": 60,
            "avg_return_pct": -1.2,
            "red_rate_pct": 30.0,
            "lianban_rate_pct": 8.0,
        },
        "breadth": {"up": 2800, "down": 1800, "zhaban_rate_pct": 18.0},
        "news": {"caixin": [{"summary": "政策利好"}]},
    }
    fetch_mode(lambda mode, date: data)
    _one_round_factory.done = False
    monkeypatch.setattr(
        graph, "_llm_pair",
        lambda: (_FakeLLM(_one_round_factory), _FakeLLM(_one_round_factory)))
    state = _invoke(max_rounds=3)
    assert state["debate_history"] == []
    assert "分歧判断：有" in state["host_analysis"]
    assert "多空方向分歧" in state["host_analysis"]


def test_progress_stages_monotonic(fetch_mode, monkeypatch):
    fetch_mode(lambda mode, date: _fake_data())
    # 主持人判定无分歧 → 无辩论路径，进度百分比严格单调
    monkeypatch.setattr(
        graph, "_llm_pair",
        lambda: (_FakeLLM(_no_conflict_factory), _FakeLLM(_no_conflict_factory)))
    events = []
    _invoke(max_rounds=2, callback=lambda stage, pct, msg: events.append((stage, pct, msg)))

    stages = [e[0] for e in events]
    assert stages[0] == "fetch"
    for required in ("news", "analysts", "host", "report", "done"):
        assert required in stages
    # R16：辩论节点已移除——阶段序列无 debate，host 后直接 report
    assert "debate" not in stages
    assert stages[-1] == "done"

    pcts = [e[1] for e in events]
    assert pcts == sorted(pcts)
    assert pcts[-1] == 100.0

    msgs = "|".join(m for _, _, m in events)
    assert "分析师 1/6" in msgs and "分析师 6/6" in msgs
    assert "主持人判断" in msgs and "报告生成" in msgs and "复盘完成" in msgs


def test_all_six_modes_rule_flow(fetch_mode):
    for mode in ("pre_market", "auction", "intraday_am", "noon", "intraday_pm", "close"):
        fetch_mode(lambda mode, date: _fake_data())
        state = _invoke(mode=mode)
        result = build_result(state)
        assert result["final_report"]
        # LLM 成功路径：数据无降级 → degraded False（2026-08-10 契约：LLM 不可用直接终止，不再整图降级）
        assert state["degraded"] is False


# ── LLM 路径（mock，禁止真调用）──

class _FakeResponse:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeLLM:
    def __init__(self, factory):
        self._factory = factory

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        return _FakeResponse(self._factory(messages))


def _llm_factory(messages):
    """按消息内容确定性返回 mock 回复（线程安全，无共享迭代器）。"""
    user = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            user = m.get("content", "")
    if "## 资讯内容" in user:
        return "利多板块：无。"
    if "实质性分歧" in user:
        return "分歧判断：有\n讨论议题：多空方向分歧\n总结：观点分歧。"
    if "## 讨论议题" in user:
        # 辩论回复按分析师身份区分（system 消息「你是XX」），保证各分析师回复不同
        sys_text = ""
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                sys_text = m.get("content", "")
        name = "分析师"
        m_name = re.search(r"你是([^，。、\s（(]+)", sys_text)
        if m_name:
            name = m_name.group(1)
        return f"（测试）{name}：同意部分观点并补充：谨慎为主。"
    if "报告要求" in user:
        return "# 2026-08-03 A股收盘复盘报告\n## 一、市场概况\n略\n## 综合标签\n#测试标签A\n#测试标签B"
    return "## 1、昨日复盘验证\n无昨日报告，跳过验证\n## 2、今日判断（今日大盘判断）\n看多\n## 3、操作计划（明日计划）\n进攻"


def _no_conflict_factory(messages):
    """主持人判定无分歧（用于无辩论流程/进度单调场景）。"""
    user = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            user = m.get("content", "")
    if "实质性分歧" in user:
        return "分歧判断：无\n总结：观点一致，无需辩论。"
    return _llm_factory(messages)


def _one_round_factory(messages):
    """主持人判定：首轮有分歧 → 辩论 1 轮；后续轮次判定无分歧（确定性收敛）。"""
    user = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            user = m.get("content", "")
    if "实质性分歧" in user:
        if _one_round_factory.done:
            return "分歧判断：无\n总结：观点已收敛。"
        _one_round_factory.done = True
        return "分歧判断：有\n讨论议题：多空方向分歧\n总结：观点分歧。"
    return _llm_factory(messages)


_one_round_factory.done = False


def test_llm_path_mocked(fetch_mode, monkeypatch):
    fetch_mode(lambda mode, date: _fake_data())
    monkeypatch.setattr(graph, "_llm_ready", lambda: True)
    llm = _FakeLLM(_llm_factory)
    strict = _FakeLLM(_llm_factory)
    monkeypatch.setattr(graph, "_llm_pair", lambda: (llm, strict))

    state = _invoke(max_rounds=2)
    assert state["degraded"] is False
    assert state["final_report"].startswith("# 2026-08-03 A股收盘复盘报告")
    assert "仅供参考，不构成投资建议" in state["final_report"]
    assert state["overall_tags"] == ["测试标签A", "测试标签B"]
    # R16：无辩论——主持人分歧判断进 host_analysis，debate_history 恒空
    assert state["debate_history"] == []
    assert "多空方向分歧" in state["host_analysis"]


# ── 结果契约（§五 result.report）与免责声明 ──

def test_build_result_structure():
    state = default_initial_state("2026-08-03")
    state["final_report"] = "正文"
    state["analyses"] = [{"skill_name": "阿狼", "analysis": "看多"}]
    state["debate_history"] = []
    state["overall_tags"] = ["降级模式"]
    state["degraded"] = True
    state["round_count"] = 2
    result = build_result(state)
    assert set(result.keys()) == {
        "final_report", "analyses", "debate_history", "overall_tags",
        "disclaimer", "degraded", "round_count",
    }
    assert result["disclaimer"] == "仅供参考，不构成投资建议"
    assert result["final_report"].endswith("> 仅供参考，不构成投资建议")
    assert result["degraded"] is True
    # R16：无辩论——debate_history 恒空、round_count 恒 0（即使 state 里有旧值）
    assert result["round_count"] == 0
    assert result["debate_history"] == []


def test_disclaimer_forced_and_idempotent():
    once = ensure_disclaimer("报告正文")
    assert once.endswith("> 仅供参考，不构成投资建议")
    assert "报告正文" in once
    assert ensure_disclaimer(once) == once
    assert ensure_disclaimer("") == "> 仅供参考，不构成投资建议"


def test_default_initial_state_full():
    s = default_initial_state("2026-08-03", mode="noon")
    assert s["mode"] == "noon"
    assert s["date"] == "2026-08-03"
    assert s["yesterday_report"] == ""
    assert s["earlier_today"] == ""
    for key in (
        "market_data", "raw_news", "snapshot", "emotion_cycle", "analyses",
        "discussion_topic", "discussion_done", "round_count", "host_analysis",
        "debate_history", "final_report", "overall_tags", "disclaimer",
        "degraded", "degraded_notes",
    ):
        assert key in s


def test_main_entry_partial_state_compat(fetch_mode):
    """main.py 旧 initial_state（缺 mode/earlier_today/snapshot 等）仍可运行。"""
    fetch_mode(lambda mode, date: _fake_data())
    app = build_graph()
    partial = {
        "date": "2026-08-03",
        "max_rounds": 2,
        "market_data": "",
        "analyses": [],
        "discussion_topic": "",
        "discussion_done": False,
        "round_count": 0,
        "skill_names": [],
        "debate_history": [],
        "final_report": "",
        "yesterday_report": "",
        "raw_news": "",
    }
    state = app.invoke(partial)
    assert state["final_report"]
    assert "仅供参考，不构成投资建议" in state["final_report"]
