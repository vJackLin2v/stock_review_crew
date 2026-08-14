"""R10 质量评估扩展测试：trace_review / score_review / score_chat。

覆盖（requirements-r10.md Q4）：
- trace_review：无凭据 None、有凭据返回 trace id、metadata 合并（source/engine/stage/mode/date）、
  langfuse 4.x start_observation 回退、异常吞掉；
- score_review：四项 rubric 手算（好报告 5 分、缺免责声明扣分、数字核验 mismatch 扣分、
  降级标注缺失扣分、边界 clamp 1-5、sections 覆盖）；
- score_chat：结构/数字核验/分析师覆盖，name=chat_quality 写回；
- score 写回：有 trace_id 才调用假客户端的 score，参数断言；写回失败不影响 dict；
- 失败无害：rubric 计算异常返回 None。
"""

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_review_crew import quality  # noqa: E402


def _clear_env(monkeypatch):
    for k in quality.LANGFUSE_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)


def test_langfuse_enabled_0_global_kill_switch(monkeypatch):
    """2026-08-12：LANGFUSE_ENABLED=0 时凭据齐全也全链路 no-op（对齐 config 总开关）。"""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_ENABLED", "0")
    monkeypatch.setattr(quality, "_langfuse_client", None)
    monkeypatch.setattr(quality, "_langfuse_client_attempted", False)
    score_calls: list[str] = []
    monkeypatch.setattr(quality, "_client_score", lambda *a, **k: score_calls.append("score"))

    assert quality._langfuse_configured() is False
    assert quality.is_trace_enabled() is False
    assert quality._get_langfuse_client() is None
    assert quality.trace_review("done", {"date": "2026-08-12"}) is None
    res = quality.score_review("报告", {}, {}, "tr-1")
    assert isinstance(res, dict) and "score" in res  # rubric 仍返回（仅不上报 Langfuse）
    assert score_calls == [], "LANGFUSE_ENABLED=0 不应写回 score"


class _FakeLangfuse:
    """记录 trace/score 调用的假客户端（模仿 langfuse 3.x 风格 API）。"""

    def __init__(self, trace_id="tr-abc"):
        self.trace_id = trace_id
        self.traces = []
        self.scores = []

    def trace(self, **kwargs):
        self.traces.append(kwargs)
        return self.trace_id

    def score(self, **kwargs):
        self.scores.append(kwargs)


class _FakeLangfuse4:
    """langfuse 4.x 风格客户端：无 trace/score，走 start_observation/create_score。"""

    def __init__(self):
        self.spans = []
        self.scores = []

    def start_observation(self, **kwargs):
        self.spans.append(kwargs)
        return SimpleNamespace(trace_id="tr-otel", id="span-1")

    def create_score(self, **kwargs):
        self.scores.append(kwargs)


# ────────────────────────── 导出与签名 ──────────────────────────


def test_all_exports_extended():
    assert "trace_chat" in quality.__all__
    for name in ("trace_review", "score_review", "score_chat", "is_trace_enabled", "verify_numbers", "evaluate_rubric"):
        assert name in quality.__all__


def test_new_signatures_stable():
    assert list(inspect.signature(quality.trace_review).parameters) == ["stage", "payload", "metadata"]
    assert list(inspect.signature(quality.score_review).parameters) == ["report", "structured", "meta", "trace_id"]
    assert list(inspect.signature(quality.score_chat).parameters) == ["messages", "structured", "meta", "trace_id"]


# ────────────────────────── trace_review ──────────────────────────


def test_trace_review_none_without_credentials(monkeypatch):
    _clear_env(monkeypatch)
    assert quality.trace_review("close", {"date": "2026-08-06"}) is None


def test_trace_review_returns_id_with_credentials(monkeypatch):
    fake = _FakeLangfuse(trace_id="tr-1")
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    tid = quality.trace_review("close", {"date": "2026-08-06"}, {"mode": "close", "date": "2026-08-06"})
    assert tid == "tr-1"
    assert len(fake.traces) == 1
    tr = fake.traces[0]
    assert tr["name"] == "review:close"
    assert tr["input"] == {"date": "2026-08-06"}
    assert tr["metadata"]["source"] == "stock_review_crew"
    assert tr["metadata"]["engine"] == "review"
    assert tr["metadata"]["stage"] == "close"
    assert tr["metadata"]["mode"] == "close"
    assert tr["metadata"]["date"] == "2026-08-06"


def test_trace_review_wraps_non_dict_payload(monkeypatch):
    fake = _FakeLangfuse()
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    quality.trace_review("fetch", "纯文本")
    assert fake.traces[0]["input"] == {"payload": "纯文本"}


def test_trace_review_swallows_client_errors(monkeypatch):
    class _Boom:
        def trace(self, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: _Boom())
    assert quality.trace_review("close", {}) is None


def test_trace_review_langfuse4_fallback(monkeypatch):
    fake = _FakeLangfuse4()
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    tid = quality.trace_review("close", {"date": "2026-08-06"}, {"mode": "close"})
    assert tid == "tr-otel"
    assert len(fake.spans) == 1
    span = fake.spans[0]
    assert span["name"] == "review:close"
    assert span["as_type"] == "span"
    assert span["metadata"]["mode"] == "close"


def test_trace_chat_returns_id_too(monkeypatch):
    """trace_chat 增强：有凭据时同样返回 trace id（原调用方忽略返回值不受影响）。"""
    fake = _FakeLangfuse(trace_id="tr-chat")
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    assert quality.trace_chat("parse", {"q": 1}) == "tr-chat"
    assert fake.traces[0]["name"] == "chat:parse"


# ────────────────────────── score_review：手算 rubric ──────────────────────────


def _good_report() -> str:
    return (
        "## 一、市场概况\n大盘震荡，指数上涨0.5%。\n"
        "## 二、各分析师核心观点\n趋势派：偏多。\n"
        "## 三、分歧点\n无明显分歧。\n"
        "## 四、明日操作计划\n低吸为主。\n"
        "## 五、总结\n震荡格局。\n"
        "## 六、风险提示\n注意退潮。\n"
        "## 七、综合标签\n#震荡\n"
        "仅供参考，不构成投资建议"
    )


def test_score_review_good_report_is_5():
    """手算：7/7 章节 + 免责声明 + 数字全对 + 无降级 -> 各项 5 分，总分 5。"""
    structured = {"pct_change": 0.005}
    r = quality.score_review(_good_report(), structured, {"mode": "close"})
    assert r is not None
    assert r["score"] == 5
    assert r["passed"] is True
    assert [it["item"] for it in r["breakdown"]] == ["structure", "disclaimer", "numbers", "degraded"]
    assert all(it["score"] == 5 and it["max"] == 5 for it in r["breakdown"])
    assert "7/7" in r["breakdown"][0]["evidence"]


def test_score_review_missing_disclaimer_deducts():
    """手算：缺免责声明 -> disclaimer=1，总分 (5+1+5+5)/4 = 4。"""
    report = _good_report().replace("仅供参考，不构成投资建议", "")
    r = quality.score_review(report, {"pct_change": 0.005}, {"mode": "close"})
    assert r["breakdown"][1] == {"item": "disclaimer", "score": 1, "max": 5,
                                 "evidence": "报告缺少免责声明「仅供参考，不构成投资建议」"}
    assert r["score"] == 4
    assert r["passed"] is True


def test_score_review_number_mismatch_deducts():
    """手算（2026-08-06 rubric 最终版）：1 条幻觉代码（000001 不存在）扣 1；
    数值不一致与字段缺失不触发重罚 -> numbers=4，总分 (5+5+4+5)/4 = 4.75 -> 5。"""
    report = (
        "## 一、市场概况\n贵州茅台（600519）上涨3.50%，000001 上涨2.00%，收盘价2000元。\n"
        "## 二、各分析师核心观点\n偏多。\n## 三、分歧点\n无。\n## 四、明日操作计划\n低吸。\n"
        "## 五、总结\n震荡。\n## 六、风险提示\n注意风险。\n## 七、综合标签\n#震荡\n"
        "仅供参考，不构成投资建议"
    )
    structured = {"600519": {"pct_change": 0.0215, "close": 1450.5}}
    r = quality.score_review(report, structured, {"mode": "close"})
    numbers = r["breakdown"][2]
    assert numbers["score"] == 4
    assert "幻觉代码 1 条" in numbers["evidence"]
    assert r["score"] == 5


def test_score_review_soft_mismatch_tolerated():
    """软信号（字段无法溯源）大量出现时只按每 4 条扣 1（上限 2），不把分打爆。"""
    report = (
        "## 一、市场概况\n换手率9%，市盈率20倍，总市值5000亿元，振幅3%。\n"
        "## 二、各分析师核心观点\n偏多。\n"
        "## 三、分歧点\n无。\n## 四、明日操作计划\n低吸。\n## 五、总结\n震荡。\n"
        "## 六、风险提示\n注意风险。\n## 七、综合标签\n#震荡\n仅供参考，不构成投资建议"
    )
    # 4 条带别名但数据无该字段的声称 -> 软信号 4 条 -> 扣 1
    structured = {"600519": {"close": 1450.5}}
    r = quality.score_review(report, structured, {"mode": "close"})
    numbers = r["breakdown"][2]
    assert numbers["score"] >= 4  # 5 - min(0,4) - min(4//4,2) = 4


def test_score_review_degraded_not_visible_deducts():
    """手算：meta.degraded 非空但报告无「降级」字样 -> degraded=1，总分 4。"""
    r = quality.score_review(
        _good_report(), {"pct_change": 0.005},
        {"mode": "close", "degraded": ["涨停数据源降级", "板块数据降级"]},
    )
    degraded = r["breakdown"][3]
    assert degraded["score"] == 1
    assert "缺少「降级」标注" in degraded["evidence"]
    assert r["score"] == 4


def test_score_review_degraded_visible_full_marks():
    report = _good_report().replace("## 六、风险提示\n注意退潮。", "## 六、风险提示\n注意退潮。\n数据降级说明：涨停数据源降级。")
    r = quality.score_review(report, {"pct_change": 0.005}, {"mode": "close", "degraded": ["涨停数据源降级"]})
    assert r["breakdown"][3]["score"] == 5
    assert r["score"] == 5


def test_score_review_clamp_to_1():
    """手算（2026-08-06 最终版）：无章节/无免责声明/4 条幻觉代码/降级不可见 -> 各项 1 分。
    4 条代码在数据中不存在且无非一致信号 -> 核验器可信，按幻觉代码重罚 numbers=1。"""
    report = "今日市场震荡。600519 上涨，000001 上涨，000002 上涨，000003 上涨，000004 上涨。"
    structured = {"600519": {"pct_change": 0.005}}
    r = quality.score_review(report, structured, {"mode": "close", "degraded": ["数据降级"]})
    assert r["score"] == 1
    assert r["passed"] is False
    assert all(it["score"] == 1 for it in r["breakdown"])


def test_score_review_sections_override():
    """meta.sections 覆盖内置章节表：命中 1/2 -> structure=1+4*0.5=3。"""
    report = "## 甲、市场温度\n偏热。\n仅供参考，不构成投资建议"
    r = quality.score_review(report, {}, {"mode": "close", "sections": ["甲、市场温度", "乙、操作要点"]})
    assert r["breakdown"][0]["score"] == 3
    assert "1/2" in r["breakdown"][0]["evidence"]


# ────────────────────────── score_review：写回与失败无害 ──────────────────────────


def test_score_review_no_client_no_score_write(monkeypatch):
    _clear_env(monkeypatch)
    r = quality.score_review(_good_report(), {"pct_change": 0.005}, {"mode": "close"}, trace_id="tr-1")
    assert r is not None and r["score"] == 5  # 无凭据：只返回 dict，不 score


def test_score_review_writes_score_with_trace_id(monkeypatch):
    fake = _FakeLangfuse()
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    r = quality.score_review(_good_report(), {"pct_change": 0.005}, {"mode": "close"}, trace_id="tr-1")
    assert r["score"] == 5
    assert len(fake.scores) == 1
    call = fake.scores[0]
    assert call["name"] == "review_quality"
    assert call["value"] == 5
    assert call["trace_id"] == "tr-1"
    detail = json.loads(call["comment"])
    assert detail["score"] == 5
    assert len(detail["breakdown"]) == 4


def test_score_review_skips_write_without_trace_id(monkeypatch):
    fake = _FakeLangfuse()
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    quality.score_review(_good_report(), {"pct_change": 0.005}, {"mode": "close"})
    assert fake.scores == []


def test_score_review_score_write_failure_keeps_dict(monkeypatch):
    class _Boom:
        def score(self, **kwargs):
            raise RuntimeError("export failed")

    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: _Boom())
    r = quality.score_review(_good_report(), {"pct_change": 0.005}, {"mode": "close"}, trace_id="tr-1")
    assert r is not None and r["score"] == 5  # 写回失败静默，不影响确定性 dict


def test_score_review_langfuse4_create_score(monkeypatch):
    fake = _FakeLangfuse4()
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    r = quality.score_review(_good_report(), {"pct_change": 0.005}, {"mode": "close"}, trace_id="tr-otel")
    assert r["score"] == 5
    assert len(fake.scores) == 1
    assert fake.scores[0]["name"] == "review_quality"
    assert fake.scores[0]["trace_id"] == "tr-otel"


def test_score_review_rubric_error_returns_none():
    # meta.sections 不可迭代 -> rubric 计算异常 -> 返回 None（失败无害，不抛出）
    assert quality.score_review("报告", {}, {"sections": 123}) is None
    # meta=None 会被容错为 {}，正常出分（不抛错）
    assert quality.score_review("报告", {}, None) is not None


# ────────────────────────── score_chat：手算 rubric ──────────────────────────


def test_score_chat_good_reply_is_5():
    """手算：回复含免责声明 + 数字全对 + 无 analysts 名单（不适用满分）-> 总分 5。"""
    messages = [
        {"role": "user", "content": "怎么看今天的大盘？"},
        {"role": "assistant", "content": "今天上涨0.5%。仅供参考，不构成投资建议"},
    ]
    r = quality.score_chat(messages, {"pct_change": 0.005}, {})
    assert r is not None
    assert r["score"] == 5
    assert r["passed"] is True
    assert [it["item"] for it in r["breakdown"]] == ["structure", "numbers", "analysts"]
    assert all(it["score"] == 5 for it in r["breakdown"])


def test_score_chat_missing_disclaimer_deducts():
    """手算：末条回复无免责声明 -> structure=1，总分 (1+5+5)/3 = 3.67 -> 4。"""
    messages = [{"role": "assistant", "content": "今天上涨0.5%，建议观望。"}]
    r = quality.score_chat(messages, {"pct_change": 0.005}, {})
    assert r["breakdown"][0]["score"] == 1
    assert r["score"] == 4


def test_score_chat_number_mismatch_deducts():
    """手算：3 条 mismatch -> numbers=2，总分 (5+2+5)/3 = 4.0 -> 4。"""
    messages = [{"role": "assistant",
                 "content": "600519 上涨3.50%，收盘价2000元，换手率9%。仅供参考，不构成投资建议"}]
    r = quality.score_chat(messages, {"600519": {"pct_change": 0.0215, "close": 1450.5}}, {})
    assert r["breakdown"][1]["score"] == 2
    assert r["score"] == 4


def test_score_chat_analyst_coverage():
    """手算：analysts 2 位仅 1 位有回复 -> 覆盖 1/2 -> analysts=1+4*0.5=3。"""
    messages = [{"role": "assistant", "analyst": "趋势派",
                 "content": "趋势派观点：偏多。仅供参考，不构成投资建议"}]
    r = quality.score_chat(messages, {}, {"analysts": ["趋势派", "情绪派"]})
    assert r["breakdown"][2]["score"] == 3
    assert "1/2" in r["breakdown"][2]["evidence"]
    assert r["score"] == 4


def test_score_chat_all_analysts_covered_full_marks():
    messages = [
        {"role": "assistant", "analyst": "趋势派", "content": "趋势派：偏多。"},
        {"role": "assistant", "analyst": "情绪派", "content": "情绪派：回暖。"},
        {"role": "assistant", "content": "仅供参考，不构成投资建议"},
    ]
    r = quality.score_chat(messages, {}, {"analysts": ["趋势派", "情绪派"]})
    assert r["breakdown"][2]["score"] == 5
    assert r["score"] == 5


def test_score_chat_writes_chat_quality(monkeypatch):
    fake = _FakeLangfuse()
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    messages = [{"role": "assistant", "content": "上涨0.5%。仅供参考，不构成投资建议"}]
    r = quality.score_chat(messages, {"pct_change": 0.005}, {}, trace_id="tr-chat")
    assert r["score"] == 5
    assert len(fake.scores) == 1
    call = fake.scores[0]
    assert call["name"] == "chat_quality"
    assert call["value"] == 5
    assert call["trace_id"] == "tr-chat"


def test_score_chat_skips_write_without_trace_id(monkeypatch):
    fake = _FakeLangfuse()
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    quality.score_chat([{"role": "assistant", "content": "仅供参考，不构成投资建议"}], {}, {})
    assert fake.scores == []


def test_score_chat_rubric_error_returns_none():
    # 消息含不可 JSON 序列化的值 -> 文本提取异常 -> 返回 None（失败无害）
    assert quality.score_chat([{"content": object()}], {}, {}) is None
    assert quality.score_chat([], {}, None) is not None


def test_score_chat_empty_messages_failure_safe():
    r = quality.score_chat([], {}, {})
    assert r is not None
    assert r["score"] == 4  # (1+5+5)/3 -> 4：空回复不抛错，正常出分
