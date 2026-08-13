"""R8 质量评估模块测试（线 C）：
trace_chat Langfuse no-op 占位 + verify_numbers 数字核验（防幻觉）+ rubric 打分。

覆盖（requirements-v4.md 第六章/第七章）：
- trace_chat：无凭据/导入失败自动 no-op、绝不抛错；有凭据仅尽力而为记录；
- verify_numbers：手算用例（文本与数据一致 -> ok=True；不一致/编造代码 -> mismatches 非空）；
- 百分比小数口径：0.5=50% 同一单位比较；
- 6 位股票代码实体核验；金额 千/万/亿/万亿 单位；None 语义；失败无害。
"""

import builtins
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_review_crew import quality  # noqa: E402


def _clear_env(monkeypatch):
    for k in quality.LANGFUSE_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)


# ────────────────────────── trace_chat ──────────────────────────


def test_trace_chat_signature_stable():
    assert list(inspect.signature(quality.trace_chat).parameters) == ["stage", "payload"]


def test_trace_chat_noop_without_credentials(monkeypatch):
    _clear_env(monkeypatch)
    assert quality.is_trace_enabled() is False
    assert quality.trace_chat("parse", {"question": "你好"}) is None


def test_trace_chat_never_raises_without_credentials(monkeypatch):
    _clear_env(monkeypatch)
    for stage in ("parse", "need", "fetch", "analyze", "summarize", "done", "error"):
        assert quality.trace_chat(stage, None) is None  # payload=None 也不抛
        assert quality.trace_chat(stage, {"k": 1}) is None


class _FakeLangfuse:
    def __init__(self):
        self.traces = []

    def trace(self, **kwargs):
        self.traces.append(kwargs)


def test_trace_chat_best_effort_with_credentials(monkeypatch):
    fake = _FakeLangfuse()
    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: fake)
    _clear_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    quality.trace_chat("fetch", {"session_id": "s1", "ok": True})
    assert len(fake.traces) == 1
    tr = fake.traces[0]
    assert tr["name"] == "chat:fetch"
    assert tr["input"] == {"session_id": "s1", "ok": True}
    assert tr["metadata"]["stage"] == "fetch"


def test_trace_chat_swallows_client_errors(monkeypatch):
    class _Boom:
        def trace(self, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(quality, "_get_langfuse_client", lambda: _Boom())
    assert quality.trace_chat("summarize", {}) is None


def test_get_client_import_failure_is_noop(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setattr(quality, "_langfuse_client", None)
    monkeypatch.setattr(quality, "_langfuse_client_attempted", False)

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "langfuse" or name.startswith("langfuse."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    assert quality._get_langfuse_client() is None
    assert quality.trace_chat("parse", {}) is None


# ────────────────────────── verify_numbers ──────────────────────────


def test_verify_numbers_signature_stable():
    assert list(inspect.signature(quality.verify_numbers).parameters) == ["text", "structured"]


def test_verify_consistent_numbers():
    """手算：文本数字与数据一致 -> ok=True、mismatches 为空。"""
    text = "贵州茅台（600519）今日上涨2.15%，收盘价1450.50元。"
    structured = {"600519": {"pct_change": 0.0215, "close": 1450.5}}
    r = quality.verify_numbers(text, structured)
    assert r["ok"] is True
    assert r["mismatches"] == []
    assert r["checked"] == 2
    assert r["verified"] == 2


def test_verify_percent_decimal_same_unit():
    """手算：0.5=50%，比率按同一单位比较。"""
    # 50%（文本）== 0.5（数据）；0.5（文本）== 0.5（数据）
    assert quality.verify_numbers("上涨50%", {"600519": {"pct_change": 0.5}})["ok"] is True
    assert quality.verify_numbers("上涨0.5", {"600519": {"pct_change": 0.5}})["ok"] is True
    # 0.5% != 50%；50% != 0.5%
    assert quality.verify_numbers("上涨0.5%", {"600519": {"pct_change": 0.5}})["ok"] is False
    assert quality.verify_numbers("上涨50%", {"600519": {"pct_change": 0.005}})["ok"] is False
    # 0.5% == 0.005
    assert quality.verify_numbers("上涨0.5%", {"600519": {"pct_change": 0.005}})["ok"] is True


def test_verify_fabricated_code():
    """手算：编造代码 -> mismatches 非空。"""
    r = quality.verify_numbers("600999 今日涨停，走势强劲。", {"600519": {"pct_change": 0.0215}})
    assert r["ok"] is False
    assert any(m["entity"] == "600999" for m in r["mismatches"])
    assert any(m["reason"] == "数据中不存在该 6 位代码" for m in r["mismatches"])


def test_verify_inconsistent_number():
    """手算：数值不一致 -> mismatch（expected 数据值 0.0215，found 文本 3.5% -> 0.035）。"""
    r = quality.verify_numbers("贵州茅台（600519）上涨3.50%。", {"600519": {"pct_change": 0.0215}})
    assert r["ok"] is False
    m = r["mismatches"][0]
    assert m["entity"] == "600519"
    assert m["field"] == "pct_change"
    assert m["expected"] == 0.0215
    assert m["found"] == pytest.approx(0.035)


def test_verify_word_sign_negation():
    """手算：「下跌2.15%」按 -2.15% 处理。"""
    # 数据为 -0.0215：一致
    assert quality.verify_numbers(
        "600519 下跌2.15%。", {"600519": {"pct_change": -0.0215}}
    )["ok"] is True
    # 数据为 +0.0215：方向相反 -> mismatch
    r = quality.verify_numbers("600519 下跌2.15%。", {"600519": {"pct_change": 0.0215}})
    assert r["ok"] is False
    assert r["mismatches"][0]["found"] == pytest.approx(-0.0215)


def test_verify_multi_entity():
    text = "600519上涨2.15%，300750上涨3.10%，两者均强势。"
    structured = {"600519": {"pct_change": 0.0215}, "300750": {"pct_change": 0.031}}
    assert quality.verify_numbers(text, structured)["ok"] is True


def test_verify_claim_without_data_field():
    """手算：声称的字段在数据中不存在 -> 无法溯源 -> mismatch。"""
    r = quality.verify_numbers("600519 换手率5.0%。", {"600519": {"pct_change": 0.0215}})
    assert r["ok"] is False
    assert r["mismatches"][0]["field"] == "turnover_rate"
    assert r["mismatches"][0]["expected"] is None


def test_verify_flat_structured():
    text = "今日大盘上涨0.5%，成交额1.2万亿元。"
    structured = {"pct_change": 0.005, "amount": 1.2e12}
    assert quality.verify_numbers(text, structured)["ok"] is True


def test_verify_wan_yi_units():
    """手算：金额单位（元 存储）支持 亿/万。"""
    text = "600519 成交额1.2亿元，成交量5.2万手。"
    structured = {"600519": {"amount": 120000000, "volume": 52000}}
    assert quality.verify_numbers(text, structured)["ok"] is True


def test_verify_single_stock_by_name():
    """无代码但结构化只有一只标的时，按名称提及也能溯源。"""
    assert quality.verify_numbers(
        "贵州茅台今日上涨2.15%。", {"600519": {"pct_change": 0.0215}}
    )["ok"] is True


def test_verify_no_alias_number_near_entity():
    """无别名数字就近命中实体字段 -> 已溯源。"""
    assert quality.verify_numbers(
        "600519 最终收报1450元。", {"600519": {"close": 1450.0}}
    )["ok"] is True


def test_verify_no_alias_unmatched_not_flagged():
    """无别名且未命中数据的数字（如轮次）不指控。"""
    r = quality.verify_numbers("600519 本周讨论3次。", {"600519": {"close": 1450.0}})
    assert r["ok"] is True
    assert r["skipped"] >= 1


def test_verify_skip_unmappable_numbers():
    text = "2026年8月4日复盘，共3轮讨论，免责声明见文末。"
    r = quality.verify_numbers(text, {"600519": {"pct_change": 0.0215}})
    assert r["ok"] is True
    assert r["skipped"] >= 3


def test_verify_empty_inputs():
    assert quality.verify_numbers("", {})["ok"] is True
    assert quality.verify_numbers(None, None)["ok"] is True
    assert quality.verify_numbers("无数字文本", {"600519": {"close": 1.0}})["ok"] is True


def test_verify_none_value_is_mismatch():
    """数据为 None（无数据）而文本声称有数值 -> mismatch。"""
    r = quality.verify_numbers("600519 上涨2.15%。", {"600519": {"pct_change": None}})
    assert r["ok"] is False
    assert r["mismatches"][0]["expected"] is None


# ────────────────────────── rubric（可选） ──────────────────────────


def test_rubric_scores_and_evidence():
    items = [
        {"name": "可溯源性", "score": 5, "evidence": "verify_numbers 全绿，mismatches=0"},
        {"name": "免责声明", "score": 4, "evidence": "回复恒含 disclaimer 字段"},
    ]
    r = quality.evaluate_rubric(items)
    assert r["total"] == 9
    assert r["max_total"] == 10
    assert r["average"] == pytest.approx(4.5)
    assert r["passed"] is True
    assert all(x["valid"] for x in r["items"])


def test_rubric_invalid_items():
    r = quality.evaluate_rubric([{"name": "x", "score": 7, "evidence": ""}])
    assert r["passed"] is False
    assert r["items"][0]["valid"] is False
    assert r["items"][0]["score"] == 7.0


def test_rubric_empty_and_bad_input():
    assert quality.evaluate_rubric([])["passed"] is False
    assert quality.evaluate_rubric(None)["passed"] is False
