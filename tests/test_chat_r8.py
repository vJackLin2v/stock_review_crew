"""R8 聊天智能流程（6 步流水线）——验收测试骨架（文档驱动，测试先行）。

唯一契约：docs/requirements-v4.md（§二 流程 / §四 数据字典 / §五 SSE 事件契约 / §六 验收标准）。
只读参考：tests/conftest.py、tests/test_chat_engine.py 的 fixture 惯例（chat_tmp 自建目录防
WinError5、FakeLLM 注入 invoke/stream、make_engine 注入 storage_root/history_enabled=False/
data_fetcher）。

本文件不读取 src/stock_review_crew/chat.py 实现；R8 流式引擎实现后由集成阶段启用。
"""

import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew import chat as chat_module
from stock_review_crew.chat import ChatEngine, DISCLAIMER


@pytest.fixture
def chat_tmp(monkeypatch):
    """沙箱内 mkdtemp/pytest-tmp 目录带 0o700 ACL 无法二次访问（WinError5），改用自建目录。"""
    root = (
        Path(__file__).resolve().parent.parent
        / ".tmp"
        / f"r8_chat_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    )
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(root))
    yield root
    shutil.rmtree(root, ignore_errors=True)


class FakeLLM:
    """最小可注入 LLM：按调用顺序返回文本；invoke 整段返回，stream 逐字产出；fail=True 模拟故障。"""

    def __init__(self, texts=None, fail=False):
        self.texts = list(texts or ["（mock 回答）"])
        self.fail = fail
        self.calls = []
        self._i = 0

    def _next_text(self):
        text = self.texts[min(self._i, len(self.texts) - 1)]
        self._i += 1
        return text

    def invoke(self, messages):
        self.calls.append(list(messages))
        if self.fail:
            raise RuntimeError("mock LLM 故障")
        return type("MockAIMessage", (), {"content": self._next_text()})()

    def stream(self, messages):
        self.calls.append(list(messages))
        if self.fail:
            raise RuntimeError("mock LLM 故障")
        for ch in self._next_text():
            yield type("MockAIMessage", (), {"content": ch})()


def make_engine(chat_tmp, llm, analysts=("alang",), data_ok=True):
    """注入 storage_root / history_enabled=False / data_fetcher（沿用 test_chat_engine 惯例）。"""

    def data_fetcher(target_type, target):
        if data_ok:
            return {
                "available": True,
                "note": None,
                "data": json.dumps({"close": 10.5, "pct_change": 0.02}, ensure_ascii=False),
            }
        return {"available": False, "note": "数据缺失：mock 数据源超时", "data": None}

    return ChatEngine(
        llm=llm,
        storage_root=chat_tmp / "chats",
        history_enabled=False,
        review_searcher=lambda q: "无相关复盘记录",
        data_fetcher=data_fetcher,
        # R11 R9：默认注入确定性「搜索失败」，避免测试依赖网络
        searcher=lambda query, code=None: {"ok": False, "results": [], "summary": None},
    )


# ---- 事件捕获与断言辅助（契约见 v4 §五）----


def emit_capture():
    """捕获 emit 回调。约定签名 emit(event_type, payload)；兼容单参 emit(payload_dict)。"""
    events = []

    def emit(event_type, payload=None):
        if payload is None:
            events.append(event_type)
        else:
            events.append((event_type, payload))

    return events, emit


def event_type(ev):
    if isinstance(ev, tuple) and len(ev) == 2:
        return ev[0]
    if isinstance(ev, dict):
        return ev.get("event") or ev.get("type")
    return ev if isinstance(ev, str) else None


def event_payload(ev):
    return ev[1] if isinstance(ev, tuple) and len(ev) == 2 else ev


def events_of(events, name):
    return [event_payload(ev) for ev in events if event_type(ev) == name]


def stage_payloads(events):
    return [p for p in events_of(events, "stage") if isinstance(p, dict)]


def stage_index(events, stage):
    for i, ev in enumerate(events):
        p = event_payload(ev)
        if event_type(ev) == "stage" and isinstance(p, dict) and p.get("stage") == stage:
            return i
    return None


def analyst_of(payload):
    if isinstance(payload, dict):
        for key in ("analyst", "analyst_id", "id", "name"):
            if payload.get(key):
                return payload[key]
    return None


def delta_text(payload):
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("delta", "content", "text"):
            if isinstance(payload.get(key), str):
                return payload[key]
    return str(payload)


def find_done(events):
    dones = events_of(events, "done")
    return dones[0] if dones else None


def run_stream(engine, sid, content, emit):
    """优先 ChatEngine.stream_message，其次模块级 stream_message（契约见 v4 §五）。"""
    if engine is not None and hasattr(engine, "stream_message"):
        return engine.stream_message(sid, content, emit)
    if chat_module is not None and hasattr(chat_module, "stream_message"):
        return chat_module.stream_message(sid, content, emit)
    raise AssertionError("R8 引擎未提供 stream_message（尚未实现）")


def parse_json(intent="trend", target="600519", time_range="近5日"):
    return json.dumps({"intent": intent, "target": target, "time_range": time_range}, ensure_ascii=False)


def needs_json(*needs):
    return json.dumps(list(needs), ensure_ascii=False)


# 依据 docs/requirements-v4.md §四 与 src/stock_review_crew/tools/__init__.py 只读导出
# （fetch_* 去前缀）推断的 data_key 白名单；引擎若扩展须同步本常量。
DATA_KEY_WHITELIST = {
    "market_micro",
    "index_trend",
    "market_macro",
    "stock_info",
    "sentiment",
    "news_headlines",
    "auction_quote",
    "minute_data",
    "lhb",
    "zt_pool",
    "market_breadth",
    "sectors",
    "pre_market",
    "auction",
    "intraday_am",
    "noon",
    "intraday_pm",
    "close",
    "mode_data",
}


# ---- 验收测试（全部按文档契约编写）----


def test_stream_six_step_event_sequence(chat_tmp):
    """验收 #1：6 步事件序列 parse→need→fetch→analyst_*→summarize→summary_delta*→summary_end→done。"""
    llm = FakeLLM(
        [
            parse_json(),
            needs_json({"data_key": "stock_info", "reason": "查看标的"}),
            needs_json({"data_key": "stock_info", "reason": "查看标的"}),
            "阿狼：趋势向上。",
            "养家：情绪回暖。",
            "综合来看，短期偏强。",
        ]
    )
    e = make_engine(chat_tmp, llm, analysts=("alang", "yangjia"))
    sid = e.create_session("stock", "600519", ["alang", "yangjia"])["session_id"]
    events, emit = emit_capture()
    run_stream(e, sid, "怎么看未来5日走势？", emit)

    # 1) 四个 stage 事件齐全且严格递增
    stages = [p.get("stage") for p in stage_payloads(events)]
    for s in ("parse", "need", "fetch", "summarize"):
        assert s in stages, f"缺少 stage{{{s}}}"
    assert stages.index("parse") < stages.index("need") < stages.index("fetch") < stages.index("summarize")

    # 2) stage{need} 不携带需求清单内容（Q7）
    need_payload = stage_payloads(events)[stages.index("need")]
    assert "data_key" not in json.dumps(need_payload, ensure_ascii=False)

    # 3) 每位分析师：start 先于 end，delta 夹在中间；分析师事件整体位于 fetch 与 summarize 之间
    fetch_i = stage_index(events, "fetch")
    summarize_i = stage_index(events, "summarize")
    assert fetch_i is not None and summarize_i is not None
    analyst_evs = [
        i
        for i, ev in enumerate(events)
        if event_type(ev) in ("analyst_start", "analyst_delta", "analyst_end")
    ]
    assert analyst_evs and all(fetch_i < i < summarize_i for i in analyst_evs)
    for aid in ("alang", "yangjia"):
        start_i = [
            i
            for i, ev in enumerate(events)
            if event_type(ev) == "analyst_start" and analyst_of(event_payload(ev)) == aid
        ]
        end_i = [
            i
            for i, ev in enumerate(events)
            if event_type(ev) == "analyst_end" and analyst_of(event_payload(ev)) == aid
        ]
        assert start_i and end_i, f"分析师 {aid} 缺少 analyst_start/analyst_end"
        assert start_i[0] < end_i[-1]
        for i, ev in enumerate(events):
            if event_type(ev) == "analyst_delta" and analyst_of(event_payload(ev)) == aid:
                assert start_i[0] < i < end_i[-1]

    # 4) summary 流式：summarize 之后、done 之前
    sum_evs = [i for i, ev in enumerate(events) if event_type(ev) in ("summary_delta", "summary_end")]
    done_i = [i for i, ev in enumerate(events) if event_type(ev) == "done"]
    assert sum_evs and done_i, "缺少 summary_delta/summary_end/done"
    assert all(summarize_i < i < done_i[0] for i in sum_evs)
    assert events_of(events, "summary_delta"), "总结必须流式输出（Q3）"

    # 5) done 事件契约
    done = find_done(events)
    assert done["session_id"] == sid
    assert done["messages"][-1]["role"] == "assistant"
    assert done["disclaimer"] == DISCLAIMER
    # meta/user_msg 事件允许存在，本测试不断言其顺序细节


def test_parse_structure_hand_computed(chat_tmp):
    """手算：mock LLM 返回 parse JSON，assistant 消息须含 parse{intent,target,time_range}（v4 §四）。"""
    llm = FakeLLM(
        [
            parse_json(intent="trend", target="600519", time_range="近5日"),
            needs_json({"data_key": "stock_info", "reason": "查看标的"}),
            "阿狼：近5日趋势向上。",
            "综合来看，短期偏强。",
        ]
    )
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events, emit = emit_capture()
    run_stream(e, sid, "怎么看未来5日走势？", emit)

    done = find_done(events)
    assert done is not None
    assistant = done["messages"][-1]
    parse = assistant.get("parse")
    assert isinstance(parse, dict), "assistant 消息应携带 parse 字段（v4 §四）"
    assert parse.get("intent") == "trend"
    assert parse.get("target") == "600519"
    assert parse.get("time_range") == "近5日"


def test_parse_name_normalized_to_code(chat_tmp):
    """手算：名称输入（贵州茅台）须归一化为 6 位代码；契约允许解析失败时显式终止。"""
    llm = FakeLLM(
        [
            parse_json(intent="trend", target="贵州茅台", time_range="近5日"),
            needs_json({"data_key": "stock_info", "reason": "查看标的"}),
            "阿狼：茅台趋势向上。",
            "综合来看，短期偏强。",
        ]
    )
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events, emit = emit_capture()
    run_stream(e, sid, "怎么看贵州茅台未来5日走势？", emit)

    done = find_done(events)
    assistant = done["messages"][-1] if done else {}
    parse = assistant.get("parse") if isinstance(assistant, dict) else None
    if isinstance(parse, dict) and parse.get("target"):
        # 归一化成功：6 位代码，且与会话标的一致（解析器+搜索，会话标的兜底）
        assert re.fullmatch(r"\d{6}", str(parse["target"])), parse["target"]
        assert parse["target"] == "600519"
    else:
        # 契约允许解析失败：须显式终止并说明，不得带分析内容
        err_texts = [str(p) for p in events_of(events, "error")]
        if done and isinstance(assistant, dict):
            err_texts.append(str(assistant.get("content", "")))
        assert any(("解析失败" in t or "无法识别" in t or "未找到" in t) for t in err_texts), err_texts
        if done and isinstance(assistant, dict):
            assert str(assistant.get("content", "")).endswith(DISCLAIMER)


def test_need_data_key_whitelist(chat_tmp):
    """数据字典：needs[].data_key 仅允许白名单内 key（v4 §四），reason 非空；非法 key 被过滤。"""
    llm = FakeLLM(
        [
            parse_json(),
            needs_json(
                {"data_key": "stock_info", "reason": "查看标的的基本面"},
                {"data_key": "bogus_key", "reason": "不应通过白名单"},
            ),
            "阿狼：分析。",
            "综合来看，短期偏强。",
        ]
    )
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events, emit = emit_capture()
    run_stream(e, sid, "怎么看未来5日走势？", emit)

    done = find_done(events)
    assert done is not None
    assistant = done["messages"][-1]
    needs = assistant.get("needs", [])
    assert needs, "assistant 消息应携带 needs 审计字段（前端不展示，Q7）"
    for need in needs:
        assert need["data_key"] in DATA_KEY_WHITELIST, f"白名单外 key: {need['data_key']}"
        assert isinstance(need.get("reason"), str) and need["reason"].strip(), "reason 必须非空"
    assert all(n["data_key"] != "bogus_key" for n in needs), "非法 key 必须被过滤"


def test_fetch_failure_terminates_without_fabrication(chat_tmp, monkeypatch):
    """验收 #2：取数全失败 → 本轮终止：数据缺失说明 + 免责声明 + degraded 非空，无编造分析。"""
    llm = FakeLLM(
        [
            parse_json(),
            needs_json({"data_key": "stock_info", "reason": "查看标的"}),
            "编造的分析内容（绝不应出现）",
            "编造的总结内容（绝不应出现）",
        ]
    )
    e = make_engine(chat_tmp, llm, analysts=("alang",), data_ok=False)
    # 显式 monkeypatch 兜底：即使注入方式变化，也保证取数全失败
    monkeypatch.setattr(
        e,
        "data_fetcher",
        lambda target_type, target: {"available": False, "note": "数据缺失：mock 数据源超时", "data": None},
        raising=False,
    )
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events, emit = emit_capture()
    run_stream(e, sid, "怎么看未来5日走势？", emit)

    assert events_of(events, "error"), "取数失败须发 error 事件（v4 §五）"
    done = find_done(events)
    assert done is not None
    assistant = done["messages"][-1]
    assert "数据缺失" in assistant["content"], "须如实告知缺失项"
    assert "编造" not in assistant["content"], "不得出现分析/总结内容"
    assert assistant["content"].endswith(DISCLAIMER)
    assert assistant.get("degraded"), "degraded 必须非空可见"


def test_summary_structure(chat_tmp):
    """验收 #5：summary 非空字符串、消息尾部免责声明、analysts[] 含 id/name/content。"""
    summary_text = "综合来看，短期趋势偏强，注意回调风险。"
    llm = FakeLLM(
        [
            parse_json(),
            needs_json({"data_key": "stock_info", "reason": "查看标的"}),
            "阿狼：基于真实数据，趋势向上。",
            summary_text,
        ]
    )
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events, emit = emit_capture()
    run_stream(e, sid, "怎么看未来5日走势？", emit)

    done = find_done(events)
    assert done is not None
    assistant = done["messages"][-1]
    assert isinstance(assistant["summary"], str) and assistant["summary"].strip()
    assert assistant["summary"] == summary_text
    assert assistant["content"].endswith(DISCLAIMER)
    analysts = assistant.get("analysts", [])
    assert analysts, "analysts[] 非空"
    for a in analysts:
        assert a.get("id"), "analyst.id 缺失"
        assert a.get("name"), "analyst.name 缺失"
        assert isinstance(a.get("content"), str) and a["content"].strip(), "analyst.content 缺失"

    # 流式一致性：summary_delta 拼接 == summary（Q3 逐字流式）
    deltas = [delta_text(p) for p in events_of(events, "summary_delta")]
    if deltas:
        assert "".join(deltas).strip() == assistant["summary"].strip()


def test_second_round_includes_first_round_history(chat_tmp):
    """验收 #4（Q4）：第二轮 LLM 输入须含第一轮 user/assistant 历史消息。"""
    q1 = "第一问：怎么看当前走势？"
    a1 = "第一轮回答：短期震荡偏强。"
    llm = FakeLLM(
        [
            parse_json(),
            needs_json({"data_key": "stock_info", "reason": "查看标的"}),
            "阿狼：第一轮分析。",
            a1,
            parse_json(),
            needs_json({"data_key": "stock_info", "reason": "查看标的"}),
            "阿狼：第二轮分析。",
            "第二轮总结：维持偏强判断。",
        ]
    )
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    ev1, emit1 = emit_capture()
    run_stream(e, sid, q1, emit1)
    assert find_done(ev1) is not None, "第一轮必须正常结束"

    n_before = len(llm.calls)
    ev2, emit2 = emit_capture()
    run_stream(e, sid, "第二问：再分析一下？", emit2)
    assert find_done(ev2) is not None, "第二轮必须正常结束"

    round2_calls = llm.calls[n_before:]
    assert round2_calls, "第二轮必须调用 LLM"
    found = False
    for call in round2_calls:
        has_user = any(m.get("role") == "user" and q1 in str(m.get("content", "")) for m in call)
        has_assistant = any(m.get("role") == "assistant" and a1 in str(m.get("content", "")) for m in call)
        if has_user and has_assistant:
            found = True
            break
    assert found, "第二轮至少一次 LLM 输入应同时包含第一轮 user/assistant 消息"


def test_done_payload_contract(chat_tmp):
    """done 事件 payload 必须含 session_id / messages / disclaimer（v4 §五）。"""
    llm = FakeLLM(
        [
            parse_json(),
            needs_json({"data_key": "stock_info", "reason": "查看标的"}),
            "阿狼：分析。",
            "综合来看，短期偏强。",
        ]
    )
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    events, emit = emit_capture()
    run_stream(e, sid, "怎么看未来5日走势？", emit)

    done = find_done(events)
    assert done is not None
    assert set(done) >= {"session_id", "messages", "disclaimer"}
    assert done["session_id"] == sid
    assert done["disclaimer"] == DISCLAIMER
    assert isinstance(done["messages"], list) and len(done["messages"]) >= 2
    assert done["messages"][0]["role"] == "user"
    assert done["messages"][-1]["role"] == "assistant"
