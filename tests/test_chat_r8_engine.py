"""R8 聊天智能流程引擎测试（线 A-1）：6 步流水线 / SSE 事件 / 取数失败终止 /
白名单映射 / 多轮上下文 / quality 惰性钩子。"""

import json
import os
import shutil
import sys
import types
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

import stock_review_crew
from stock_review_crew import chat
from stock_review_crew.chat import ChatEngine, DISCLAIMER


@pytest.fixture
def chat_tmp():
    """沙箱中 mkdtemp/pytest-tmp 目录带 0o700 ACL 无法二次访问，改用 Path.mkdir 自建。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / f"r8_engine_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


class FakeLLM:
    """最小可注入 LLM：按调用顺序返回文本；fail_at 指定调用序号抛异常。"""

    def __init__(self, texts, fail_at=None):
        self.texts = list(texts)
        self.fail_at = set(fail_at or [])
        self.calls = []
        self._i = 0

    def invoke(self, messages):
        self.calls.append(list(messages))
        idx = self._i
        self._i += 1
        if idx in self.fail_at:
            raise RuntimeError("mock LLM 故障")
        text = self.texts[min(idx, len(self.texts) - 1)]
        return type("MockAIMessage", (), {"content": text})()


class StreamLLM(FakeLLM):
    """带 .stream() 的 FakeLLM：每次调用返回整段文本（单块流）。"""

    def stream(self, messages):
        result = self.invoke(messages)
        yield type("MockAIMessage", (), {"content": result.content})()


def _j(obj):
    return json.dumps(obj, ensure_ascii=False)


PARSE = {"intent": "trend", "target": "600519", "time_range": "近5日"}
NEED = [{"data_key": "stock_info", "reason": "看近期走势"}]

# 真实取数分发的引用（离线 fixture 会替换 chat._fetch_data，个别测试需还原）
REAL_FETCH_DATA = chat._fetch_data


@pytest.fixture(autouse=True)
def offline_fetch(monkeypatch):
    """离线确定性：默认所有 data_key 返回可用 mock 块；失败路径由用例覆盖。"""
    monkeypatch.setattr(
        chat, "_fetch_data",
        lambda key, parse, meta, data_fetcher=None: _ok_block(key),
    )


def make_engine(chat_tmp, llm, analysts=("alang",), data_fetcher=None, history_enabled=False):
    return ChatEngine(
        llm=llm,
        storage_root=chat_tmp / "chats",
        history_enabled=history_enabled,
        review_searcher=lambda q: "无相关复盘记录",
        data_fetcher=data_fetcher,
        # R11 R9：默认注入确定性「搜索失败」，避免测试依赖网络
        searcher=lambda query, code=None: {"ok": False, "results": [], "summary": None},
    )


def _ok_block(key, data=None):
    return {"key": key, "available": True, "data": data if data is not None else {"close": 10.5},
            "source": "mock", "note": None}


# ═══════════════ 全流程 ═══════════════

def test_full_pipeline_fields_and_llm_calls(chat_tmp):
    llm = FakeLLM([_j(PARSE), _j(NEED), "阿狼分析", "总结文本"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "怎么看明天走势？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "done"
    assert assistant["parse"] == PARSE
    assert assistant["needs"] == NEED
    assert assistant["analysts"][0]["content"] == "阿狼分析"
    assert assistant["summary"] == "总结文本"
    assert assistant["content"].endswith(DISCLAIMER)
    assert resp["disclaimer"] == DISCLAIMER

    # 单分析师：parse(1) + need(1) + analyze(1) + summary(1)
    assert len(llm.calls) == 4
    # 分析师上下文包含解析结果、数据块、复盘、历史与问题
    analyze_user = llm.calls[2][1]["content"]
    assert "问题解析" in analyze_user and "trend" in analyze_user
    assert "600519" in analyze_user
    assert "10.5" in analyze_user
    assert "相关复盘结论" in analyze_user
    assert "怎么看明天走势？" in analyze_user
    # 总结输入包含解析与分析师回答
    summary_user = llm.calls[3][1]["content"]
    assert "阿狼分析" in summary_user
    assert "trend" in summary_user


def test_stream_events_order_and_stage_payloads(chat_tmp):
    llm = StreamLLM([_j(PARSE), _j(NEED), "阿狼分析", "总结文本"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events = []

    result = e.stream_message(sid, "怎么看？", emit=lambda ev, pl: events.append((ev, pl)))

    ev_names = [ev for ev, _ in events]
    assert ev_names == [
        "meta", "user_msg",
        "stage",              # parse
        "stage",              # need
        "stage",              # fetch
        "analyst_start", "analyst_delta", "analyst_end",
        "stage",              # summarize
        "summary_start", "summary_delta", "summary_end",
        "done",
    ]
    stages = [pl for ev, pl in events if ev == "stage"]
    assert [s["stage"] for s in stages] == ["parse", "need", "fetch", "summarize"]
    # Q7：stage 事件不携带数据清单内容
    for s in stages:
        assert "data_key" not in s
        assert "stock_info" not in json.dumps(s, ensure_ascii=False)
    assert all(pl.get("note") for ev, pl in events if ev == "stage")

    # analyst/summary 事件载荷与 done 载荷
    assert events[5][1] == {"index": 0, "analyst_id": "alang", "name": "阿狼"}
    assert events[6][1]["delta"] == "阿狼分析"
    assert events[7][1]["content"] == "阿狼分析"
    assert events[9][0] == "summary_start"
    assert events[10][1]["delta"] == "总结文本"
    assert events[11][1]["content"] == "总结文本"
    done = events[-1][1]
    assert done["session_id"] == sid
    assert done["disclaimer"] == DISCLAIMER
    assert result == done
    assert [m["role"] for m in done["messages"]] == ["user", "assistant"]


# ═══════════════ 终止路径（验收 #2） ═══════════════

def test_fetch_failure_aborts_no_analysis(chat_tmp, monkeypatch):
    """monkeypatch 取数全失败 → 数据缺失说明，无分析、无编造。"""
    calls = []

    def boom_fetch(key, parse, meta, data_fetcher=None):
        calls.append(key)
        return {"key": key, "available": False, "data": None, "source": None,
                "note": f"{key} 数据源不可用（mock）"}

    monkeypatch.setattr(chat, "_fetch_data", boom_fetch)
    llm = FakeLLM([_j(PARSE), _j(NEED)])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "怎么看？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "error"
    assert assistant["analysts"] == []
    assert assistant["summary"] == ""
    assert assistant["needs"] == NEED
    assert "数据缺失说明" in assistant["content"]
    assert "stock_info" in assistant["content"]
    assert "数据源不可用" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)
    # 只到 fetch 即终止：无 analyze / summary LLM 调用
    assert len(llm.calls) == 2
    assert calls == ["stock_info"]


def test_stream_fetch_failure_emits_error_then_done(chat_tmp, monkeypatch):
    monkeypatch.setattr(
        chat, "_fetch_data",
        lambda key, parse, meta, data_fetcher=None: {"key": key, "available": False,
                                                     "data": None, "source": None, "note": "全挂"},
    )
    llm = FakeLLM([_j(PARSE), _j(NEED)])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events = []

    e.stream_message(sid, "怎么看？", emit=lambda ev, pl: events.append((ev, pl)))

    ev_names = [ev for ev, _ in events]
    assert ev_names == ["meta", "user_msg", "stage", "stage", "stage", "error", "done"]
    error_pl = [pl for ev, pl in events if ev == "error"][0]
    assert "stock_info" in error_pl["message"]
    assert "数据缺失" in error_pl["message"] or "全挂" in error_pl["message"]
    assert events[-1][0] == "done"
    assert events[-1][1]["messages"][-1]["stage"] == "error"


def test_non_critical_fetch_failure_continues(chat_tmp, monkeypatch):
    """稳定性修复（2026-08-04）：仅非关键数据（如 auction_quote）缺失 → 继续回答并降级标注。"""

    def partial_fetch(key, parse, meta, data_fetcher=None):
        if key == "auction_quote":
            return {"key": key, "available": False, "data": None, "source": None,
                    "note": "竞价数据缺失（Cookie 未配置或全部数据源失败）"}
        return _ok_block(key)

    monkeypatch.setattr(chat, "_fetch_data", partial_fetch)
    need = [{"data_key": "stock_info", "reason": "看近期走势"},
            {"data_key": "auction_quote", "reason": "次日竞价验证"}]
    llm = FakeLLM([_j(PARSE), _j(need), "阿狼：基于数据回答。", "综合结论"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "怎么看？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "done"
    assert len(assistant["analysts"]) == 1  # 未终止，有分析内容
    assert "阿狼：基于数据回答。" in assistant["content"]
    assert assistant["summary"] == "综合结论"
    assert any("auction_quote" in d for d in assistant["degraded"])
    # 分析师上下文可见该键「数据缺失」标注
    analyze_user = llm.calls[2][1]["content"]
    assert "auction_quote" in analyze_user
    assert "数据缺失" in analyze_user


def test_alias_keys_dedupe_to_canonical(chat_tmp, monkeypatch):
    """别名键归一：minute_data 折叠为 minute_kline，避免重复取数与重复缺失。"""
    calls: list[str] = []
    monkeypatch.setattr(
        chat, "_fetch_data",
        lambda key, parse, meta, data_fetcher=None: calls.append(key) or _ok_block(key),
    )
    need = [{"data_key": "minute_kline", "reason": "分时"},
            {"data_key": "minute_data", "reason": "分时别名"}]
    llm = FakeLLM([_j(PARSE), _j(need), "阿狼分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "怎么看？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "done"
    assert calls.count("minute_kline") == 1
    assert "minute_data" not in calls
    assert [n["data_key"] for n in assistant["needs"]] == ["minute_kline"]


def test_parse_failure_aborts_before_fetch(chat_tmp, monkeypatch):
    fetch_calls = []
    monkeypatch.setattr(
        chat, "_fetch_data",
        lambda key, parse, meta, data_fetcher=None: (fetch_calls.append(key), _ok_block(key))[1],
    )
    llm = FakeLLM(["这不是 JSON"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events = []

    resp = e.stream_message(sid, "怎么看？", emit=lambda ev, pl: events.append((ev, pl)))

    assistant = resp["messages"][-1]
    assert assistant["stage"] == "error"
    assert assistant["analysts"] == []
    assert "问题解析失败" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)
    assert fetch_calls == []  # 取数前终止
    assert len(llm.calls) == 1  # 只有 parse 一次调用
    ev_names = [ev for ev, _ in events]
    assert ev_names == ["meta", "user_msg", "stage", "error", "done"]


def test_target_resolution_failure_aborts(chat_tmp, monkeypatch):
    monkeypatch.setattr(
        chat, "search_stocks",
        lambda q: (_ for _ in ()).throw(RuntimeError("搜索服务不可用（mock）")),
    )
    llm = FakeLLM([_j({"intent": "trend", "target": "某神秘股票", "time_range": "近5日"})])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "某神秘股票怎么样？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "error"
    assert "未能识别标的" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)
    assert len(llm.calls) == 1


def test_target_name_resolved_via_search(chat_tmp, monkeypatch):
    monkeypatch.setattr(chat, "search_stocks", lambda q: [{"code": "600519", "name": "贵州茅台"}])
    fetched = {}
    monkeypatch.setattr(
        chat, "_fetch_data",
        lambda key, parse, meta, data_fetcher=None: (
            fetched.update({"key": key, "target": parse.get("target")}),
            _ok_block(key),
        )[1],
    )
    llm = FakeLLM([_j({"intent": "trend", "target": "贵州茅台", "time_range": "当日"}),
                   _j(NEED), "分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "贵州茅台怎么看？")
    assistant = resp["messages"][-1]
    assert assistant["parse"]["target"] == "600519"  # 名称归一为 6 位代码
    assert assistant["parse"]["time_range"] == "当日"
    assert fetched["target"] == "600519"
    assert assistant["stage"] == "done"


# ═══════════════ need 白名单 ═══════════════

def test_need_whitelist_filter_and_dedupe(chat_tmp):
    need_llm = [
        {"data_key": "stock_info", "reason": "走势"},
        {"data_key": "secret_db", "reason": "白名单外"},
        "index_trend",
        {"data_key": "stock_info", "reason": "重复"},
    ]
    llm = FakeLLM([_j(PARSE), _j(need_llm), "分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "怎么看？")
    assistant = resp["messages"][-1]
    assert assistant["needs"] == [
        {"data_key": "stock_info", "reason": "走势"},
        {"data_key": "index_trend", "reason": chat.DATA_KEY_WHITELIST["index_trend"]},
    ]
    assert any("白名单外" in d and "secret_db" in d for d in assistant["degraded"])
    assert assistant["stage"] == "done"


def test_default_needs_fallback_on_invalid_need(chat_tmp, monkeypatch):
    """分析师 need 输出无效 → 默认清单 + 降级说明（不终止）。"""
    fetched_keys = []

    def spy_fetch(key, parse, meta, data_fetcher=None):
        fetched_keys.append(key)
        return _ok_block(key)

    llm = FakeLLM([_j(PARSE), "我只需要直觉", "分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    monkeypatch.setattr(chat, "_fetch_data", spy_fetch)
    resp = e.send_message(sid, "趋势怎么看？")

    assistant = resp["messages"][-1]
    assert assistant["stage"] == "done"
    assert any("默认清单" in d for d in assistant["degraded"])
    # trend 意图默认清单（个股）：stock_info / index_trend / stock_quote
    assert fetched_keys == ["stock_info", "index_trend", "stock_quote"]


# ═══════════════ 多轮上下文（Q4） ═══════════════

def test_multi_turn_history_context(chat_tmp):
    llm = FakeLLM([_j(PARSE), _j(NEED), "第一轮回答", "总结一",
                   _j(PARSE), _j(NEED), "第二轮回答", "总结二"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    e.send_message(sid, "第一问")
    e.send_message(sid, "第二问")

    assert len(llm.calls) == 8  # 每轮完整 6 步（单分析师 4 次调用）
    second_analyze = llm.calls[6]
    assert [m["role"] for m in second_analyze] == ["system", "user", "assistant", "user"]
    assert "第一问" in second_analyze[1]["content"]      # 历史 user 消息
    assert "第一轮回答" in second_analyze[2]["content"]   # 历史 assistant 消息
    assert "第二问" in second_analyze[3]["content"]       # 当前问题
    # 第二轮 parse 也看到最近对话
    second_parse = llm.calls[4][1]["content"]
    assert "第一问" in second_parse


# ═══════════════ 流式降级 / 分析失败 ═══════════════

def test_summary_stream_fallback_for_invoke_only_llm(chat_tmp):
    llm = FakeLLM([_j(PARSE), _j(NEED), "分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events = []

    e.stream_message(sid, "怎么看？", emit=lambda ev, pl: events.append((ev, pl)))

    deltas = [pl["delta"] for ev, pl in events if ev == "summary_delta"]
    assert deltas == ["总结"]  # invoke-only LLM：整段单次回调
    ends = [pl for ev, pl in events if ev == "summary_end"]
    assert ends[0]["content"] == "总结"


def test_analyst_failure_degrades_but_round_continues(chat_tmp):
    """分析阶段 LLM 故障 → 该分析师降级占位，总结仍执行，免责声明恒有。"""
    # invoke_llm 失败重试 3 次：分析调用消耗 mock 序号 2/3/4（均 fail），总结落到序号 5
    llm = FakeLLM([_j(PARSE), _j(NEED), "阿狼分析", "总结文本"], fail_at={2, 3, 4})
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "怎么看？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "done"
    assert assistant["analysts"][0]["content"] == "（阿狼：分析服务暂时不可用，请稍后重试）"
    assert any("生成失败" in d for d in assistant["degraded"])
    assert assistant["summary"] == "总结文本"
    assert assistant["content"].endswith(DISCLAIMER)


def test_no_llm_aborts_with_missing_data_reply(chat_tmp, monkeypatch):
    monkeypatch.setattr(chat, "_get_default_llm", lambda: None)
    e = make_engine(chat_tmp, None, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events = []

    resp = e.stream_message(sid, "怎么看？", emit=lambda ev, pl: events.append((ev, pl)))

    assistant = resp["messages"][-1]
    assert assistant["stage"] == "error"
    assert assistant["analysts"] == []
    assert "LLM 未配置" in assistant["content"]
    assert "降级说明" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)
    assert [ev for ev, _ in events] == ["meta", "user_msg", "stage", "error", "done"]


# ═══════════════ quality 惰性钩子 / 注入 ═══════════════

def test_quality_trace_lazy_hook(chat_tmp, monkeypatch):
    recorded = []

    def trace_chat(stage=None, payload=None):
        recorded.append((stage, payload.get("stage") if isinstance(payload, dict) else None))
        if stage == "summarize":
            raise RuntimeError("quality 服务故障（应被吞掉）")

    quality_mod = types.ModuleType("stock_review_crew.quality")
    quality_mod.trace_chat = trace_chat
    # 真实 quality.py 已被导入并缓存在包属性上：替换包属性以注入假模块
    monkeypatch.setattr(stock_review_crew, "quality", quality_mod)
    monkeypatch.setitem(sys.modules, "stock_review_crew.quality", quality_mod)

    llm = FakeLLM([_j(PARSE), _j(NEED), "分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "怎么看？")
    assert resp["messages"][-1]["stage"] == "done"  # trace 异常不影响主流程
    assert [s for s, _ in recorded] == ["parse", "search", "need", "fetch", "summarize", "done"]


def test_quality_trace_skipped_when_no_trace_chat(chat_tmp, monkeypatch):
    """quality 模块存在但没有 trace_chat 能力 → 静默跳过，不阻塞主流程。"""
    quality_mod = types.ModuleType("stock_review_crew.quality")
    monkeypatch.setattr(stock_review_crew, "quality", quality_mod)
    monkeypatch.setitem(sys.modules, "stock_review_crew.quality", quality_mod)
    llm = FakeLLM([_j(PARSE), _j(NEED), "分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    resp = e.send_message(sid, "怎么看？")
    assert resp["messages"][-1]["stage"] == "done"


def test_real_quality_trace_chat_is_noop_without_creds(chat_tmp, monkeypatch):
    """真实 quality.trace_chat：无 LANGFUSE 凭据时 no-op，不抛错。"""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    llm = FakeLLM([_j(PARSE), _j(NEED), "分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    resp = e.send_message(sid, "怎么看？")
    assert resp["messages"][-1]["stage"] == "done"


def test_data_fetcher_injection_used_for_stock_info(chat_tmp, monkeypatch):
    seen = []

    def data_fetcher(target_type, target):
        seen.append((target_type, target))
        return {"available": True, "note": None,
                "data": json.dumps({"close": 88.8}, ensure_ascii=False)}

    monkeypatch.setattr(chat, "_fetch_data", REAL_FETCH_DATA)  # 真实分发：stock_info 走注入
    llm = FakeLLM([_j(PARSE), _j(NEED), "分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",), data_fetcher=data_fetcher)
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "怎么看？")
    assert seen == [("stock", "600519")]
    analyze_user = llm.calls[2][1]["content"]
    assert "88.8" in analyze_user
    assert resp["messages"][-1]["stage"] == "done"


def test_real_fetch_data_uses_data_fetcher_and_normalizes(chat_tmp):
    """真实 _fetch_data：stock_info 走注入 data_fetcher；source=数据缺失 判失败。"""
    meta = {"target_type": "stock", "target": "600519", "analysts": ["alang"]}
    ok = REAL_FETCH_DATA("stock_info", PARSE, meta, data_fetcher=lambda tt, tg: {
        "available": True, "note": None, "data": json.dumps({"close": 1.0}, ensure_ascii=False),
    })
    assert ok["available"] is True
    missing = REAL_FETCH_DATA("stock_info", PARSE, meta, data_fetcher=lambda tt, tg: {
        "available": True, "note": None,
        "data": json.dumps({"source": "数据缺失", "note": "个股数据缺失"}, ensure_ascii=False),
    })
    assert missing["available"] is False
    assert "数据缺失" in missing["note"]


# ═══════════════ 解析归一单元 ═══════════════

def test_time_range_normalization():
    assert chat._normalize_time_range("近一个月") == "近20日"
    assert chat._normalize_time_range("60天") == "近60日"
    assert chat._normalize_time_range("本周") == "近5日"
    assert chat._normalize_time_range("当天") == "当日"
    assert chat._normalize_time_range("很久很久") == "近5日"  # 默认
    assert chat._time_range_days("近20日") == 20
    assert chat._time_range_days("未知") == 5


def test_intent_normalization_fallback(chat_tmp):
    llm = FakeLLM([_j({"intent": "外星人", "target": "600519", "time_range": "不知道"}),
                   _j(NEED), "分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    resp = e.send_message(sid, "随便聊聊")
    assistant = resp["messages"][-1]
    assert assistant["parse"]["intent"] == "other"
    assert assistant["parse"]["time_range"] == "近5日"
    assert assistant["stage"] == "done"


def test_sector_session_keeps_name_target(chat_tmp):
    llm = FakeLLM([_j({"intent": "sentiment", "target": "半导体", "time_range": "近5日"}),
                   _j([{"data_key": "sectors", "reason": "板块强弱"}]),
                   "板块分析", "总结"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("sector", "半导体", ["alang"])["session_id"]
    resp = e.send_message(sid, "半导体板块怎么看？")
    assistant = resp["messages"][-1]
    assert assistant["parse"]["target"] == "半导体"
    assert assistant["needs"] == [{"data_key": "sectors", "reason": "板块强弱"}]
    assert assistant["stage"] == "done"
