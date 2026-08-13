"""I3 chat.py 聊天引擎单元测试：单分析师/多分析师交叉/上下文断言/降级/免责声明。"""

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from stock_review_crew import chat
from stock_review_crew.chat import ChatEngine, DISCLAIMER


@pytest.fixture
def chat_tmp():
    """沙箱中 mkdtemp/pytest-tmp 目录带 0o700 ACL 无法二次访问，改用 Path.mkdir 自建。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / f"chat_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


class FakeLLM:
    """最小可注入 LLM：按调用顺序返回文本；fail=True 时模拟服务故障。"""

    def __init__(self, texts=None, fail=False, fail_at=None):
        self.texts = list(texts or ["（mock 回答）"])
        self.fail = fail
        self.fail_at = set(fail_at or [])
        self.calls = []
        self._i = 0

    def invoke(self, messages):
        self.calls.append(list(messages))
        if self.fail:
            raise RuntimeError("mock LLM 故障")
        idx = self._i
        self._i += 1
        if idx in self.fail_at:
            raise RuntimeError("mock LLM 故障")
        text = self.texts[min(idx, len(self.texts) - 1)]
        return type("MockAIMessage", (), {"content": text})()


# R8 流水线脚本化输出：parse/need 阶段返回结构化 JSON，分析/总结返回文本。
PARSE_JSON = json.dumps({"intent": "trend", "target": "600519", "time_range": "近5日"}, ensure_ascii=False)
NEED_JSON = json.dumps([{"data_key": "stock_info", "reason": "看近期走势"}], ensure_ascii=False)


def no_search(query, code=None):
    """R11 R9：默认注入确定性「搜索失败」searcher，避免测试依赖网络。"""
    return {"ok": False, "results": [], "summary": None}


def make_engine(chat_tmp, llm, analysts=("alang",), data_ok=True, searcher=no_search):
    def data_fetcher(target_type, target):
        if data_ok:
            return {
                "available": True,
                "note": None,
                "data": json.dumps({"close": 10.5, "pct_change": 0.02}, ensure_ascii=False),
            }
        return {"available": False, "note": "数据暂时不可用：mock 数据源超时", "data": None}

    return ChatEngine(
        llm=llm,
        storage_root=chat_tmp / "chats",
        history_enabled=False,
        review_searcher=lambda q: "无相关复盘记录",
        data_fetcher=data_fetcher,
        searcher=searcher,
    )


def test_whitelist_from_skills():
    whitelist = chat.get_analyst_whitelist()
    for aid in ("alang", "bingchuan", "baxiaoxian", "yangjia", "tiechui"):
        assert aid in whitelist


def test_create_session_validation(chat_tmp):
    e = make_engine(chat_tmp, FakeLLM())
    with pytest.raises(ValueError):
        e.create_session("bond", "600519", ["alang"])
    with pytest.raises(ValueError):
        e.create_session("stock", "", ["alang"])
    with pytest.raises(ValueError):
        e.create_session("stock", "600519", [])
    with pytest.raises(ValueError) as exc:
        e.create_session("stock", "600519", ["mystery"])
    assert "白名单" in str(exc.value)


def test_single_analyst_reply_and_context(chat_tmp):
    llm = FakeLLM([PARSE_JSON, NEED_JSON, "阿狼的答案", "总结文本"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"], title="白酒")["session_id"]

    resp = e.send_message(sid, "怎么看明天走势？")
    assert resp["disclaimer"] == DISCLAIMER
    assert resp["session_id"] == sid
    assert [m["role"] for m in resp["messages"]] == ["user", "assistant"]

    assistant = resp["messages"][-1]
    assert assistant["content"].endswith(DISCLAIMER)
    assert "阿狼" in assistant["content"]
    assert assistant["summary"] == "总结文本"
    assert assistant["degraded"] == []
    assert len(assistant["analysts"]) == 1
    assert assistant["analysts"][0]["id"] == "alang"
    # R8 新增字段：parse / needs / stage
    assert assistant["parse"] == {"intent": "trend", "target": "600519", "time_range": "近5日"}
    assert assistant["needs"] == [{"data_key": "stock_info", "reason": "看近期走势"}]
    assert assistant["stage"] == "done"

    # 上下文断言：skill 人格 + 标的数据 + 复盘结论 + 当前问题
    assert len(llm.calls) == 4  # parse + need + analyze + summary（R8 六步流水线）
    system, user = llm.calls[2][0]["content"], llm.calls[2][1]["content"]
    assert "阿狼" in system and "焚诀" in system
    assert "600519" in user
    assert '"close": 10.5' in user
    assert "相关复盘结论" in user
    assert "无相关复盘记录" in user
    assert "怎么看明天走势？" in user


def test_multi_analyst_pipeline(chat_tmp):
    llm = FakeLLM([PARSE_JSON, NEED_JSON, NEED_JSON, "首答", "表态", "汇总"])
    e = make_engine(chat_tmp, llm, analysts=("alang", "yangjia"))
    sid = e.create_session("stock", "600519", ["alang", "yangjia"])["session_id"]
    resp = e.send_message(sid, "大盘怎么看？")

    assistant = resp["messages"][-1]
    assert [a["id"] for a in assistant["analysts"]] == ["alang", "yangjia"]
    assert assistant["analysts"][0]["content"] == "首答"
    assert assistant["analysts"][1]["content"] == "表态"
    assert assistant["summary"] == "汇总"
    assert "综合来看：" in assistant["content"]
    assert "阿狼" in assistant["content"] and "炒股养家" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)
    assert assistant["degraded"] == []
    assert assistant["stage"] == "done"
    assert assistant["needs"] == [{"data_key": "stock_info", "reason": "看近期走势"}]  # 跨分析师去重

    # parse(1) + need(2) + analyze(2) + summary(1) = 6 次；每位分析师独立基于数据回答
    assert len(llm.calls) == 6
    for idx in (3, 4):  # 两位分析师的 analyze 调用
        assert "首答" not in llm.calls[idx][1]["content"]  # 无交叉表态注入
        assert "大盘怎么看？" in llm.calls[idx][1]["content"]
    summary_user = llm.calls[5][1]["content"]
    assert "首答" in summary_user and "表态" in summary_user


def test_prev_round_reply_in_context(chat_tmp):
    llm = FakeLLM([PARSE_JSON, NEED_JSON, "第一轮回答", "总结一",
                   PARSE_JSON, NEED_JSON, "第二轮回答", "总结二"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    e.send_message(sid, "第一问")
    e.send_message(sid, "第二问")

    # 第二轮分析师应看到历史消息（含上一轮问答）；每轮完整走 6 步
    assert len(llm.calls) == 8
    second_analyze = llm.calls[6]
    assert [m["role"] for m in second_analyze] == ["system", "user", "assistant", "user"]
    assert "第一问" in second_analyze[1]["content"]      # 历史 user 消息
    assert "第一轮回答" in second_analyze[2]["content"]   # 历史 assistant 消息
    assert "第二问" in second_analyze[3]["content"]       # 当前问题


def test_fetch_failure_aborts_with_missing_reply(chat_tmp):
    """v4 验收 #2：取数失败 → 数据缺失说明，无分析内容，不编造。"""
    llm = FakeLLM([PARSE_JSON, NEED_JSON])
    e = make_engine(chat_tmp, llm, analysts=("alang",), data_ok=False)
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    resp = e.send_message(sid, "怎么看？")

    assistant = resp["messages"][-1]
    assert assistant["stage"] == "error"
    assert assistant["analysts"] == []
    assert assistant["summary"] == ""
    assert "数据缺失说明" in assistant["content"]
    assert "stock_info" in assistant["content"]
    assert "数据暂时不可用" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)
    # 只到 fetch 即终止：无 analyze / summary 调用
    assert len(llm.calls) == 2


def test_llm_failure_degrades_without_raise(chat_tmp):
    llm = FakeLLM(fail=True)
    e = make_engine(chat_tmp, llm, analysts=("alang", "yangjia"))
    sid = e.create_session("stock", "600519", ["alang", "yangjia"])["session_id"]
    resp = e.send_message(sid, "怎么看？")

    assistant = resp["messages"][-1]
    assert assistant["degraded"]
    assert assistant["analysts"] == []
    assert assistant["stage"] == "error"
    assert "问题解析失败" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)


def test_no_llm_degrades_without_raise(chat_tmp, monkeypatch):
    monkeypatch.setattr(chat, "_get_default_llm", lambda: None)
    e = make_engine(chat_tmp, None, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    resp = e.send_message(sid, "怎么看？")

    assistant = resp["messages"][-1]
    assert assistant["degraded"]
    assert "LLM 未配置" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)


def test_send_to_missing_session_returns_none(chat_tmp):
    e = make_engine(chat_tmp, FakeLLM())
    assert e.send_message("2099-01-01_000000", "你好") is None
    assert e.get_session("2099-01-01_000000") is None
    assert e.delete_session("2099-01-01_000000") is False


def test_empty_content_rejected(chat_tmp):
    e = make_engine(chat_tmp, FakeLLM())
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    with pytest.raises(ValueError):
        e.send_message(sid, "   ")


def test_persistence_failure_does_not_block_reply(chat_tmp, monkeypatch):
    llm = FakeLLM(["回答"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    def boom(*args, **kwargs):
        raise OSError("磁盘写入失败")

    monkeypatch.setattr(chat.chat_storage, "append_message", boom)
    resp = e.send_message(sid, "怎么看？")
    assistant = resp["messages"][-1]
    assert "消息持久化失败" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)


def test_engine_roundtrip_and_filters(chat_tmp):
    llm = FakeLLM(["回"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("sector", "半导体", ["alang"])["session_id"]
    e.send_message(sid, "问题一")
    e.send_message(sid, "问题二")

    detail = e.get_session(sid)
    assert len(detail["messages"]) == 4
    assert detail["disclaimer"] == DISCLAIMER
    assert detail["meta"]["target"] == "半导体"

    lst = e.list_sessions(target="半导体")
    assert [i["session_id"] for i in lst] == [sid]
    assert e.list_sessions(target="白酒") == []
    assert e.delete_session(sid) is True
    assert e.get_session(sid) is None


def test_module_level_functions(chat_tmp, monkeypatch):
    """模块级便捷入口（I5 后端集成用）走默认引擎 + 环境变量隔离。"""
    monkeypatch.setenv("CHATS_DATA_DIR", str(chat_tmp / "chats"))
    chat.reset_default_engine()
    try:
        llm = FakeLLM(["模块级回答"])
        monkeypatch.setattr(chat, "_get_default_llm", lambda: llm)
        monkeypatch.setattr(
            chat,
            "_fetch_target_data",
            lambda target_type, target: {"available": True, "note": None, "data": '{"close": 1.0}'},
        )
        monkeypatch.setattr("stock_review_crew.knowledge_store.search", lambda *a, **k: "无相关复盘记录")
        monkeypatch.setattr(chat, "_get_chroma_client", lambda: None)  # 默认引擎不触碰仓库 chroma_db
        resp = chat.create_session("stock", "600519", ["alang"])
        sid = resp["session_id"]
        out = chat.send_message(sid, "你好")
        assert out["disclaimer"] == DISCLAIMER
        assert out["messages"][-1]["content"].endswith(DISCLAIMER)
        assert chat.get_session(sid) is not None
        assert chat.delete_session(sid) is True
    finally:
        chat.reset_default_engine()


# ══════════════════════════════════════════════════════════════════
# R11 R9：联网搜索 → 整理简报 → 注入分析师上下文
# ══════════════════════════════════════════════════════════════════


def _ok_searcher(query, code=None):
    """R9 注入：搜索成功返回 2 条结果（东财公告 + 新浪快讯）。"""
    return {
        "ok": True,
        "results": [
            {"source": "东财公告", "title": "贵州茅台关于中期分红的公告",
             "date": "2026-08-08", "url": "https://example.com/ann", "summary": None},
            {"source": "新浪7x24", "title": "白酒板块午后拉升", "date": None,
             "url": None, "summary": None},
        ],
        "summary": None,
    }


def test_r11_search_brief_injected_when_search_ok(chat_tmp):
    """R9：搜索成功 → 一次 LLM 整理调用 → 简报注入分析师上下文（标题「## 联网检索简报」）。"""
    llm = FakeLLM([PARSE_JSON, "事件：中期分红落地\n影响方向：利好\n相关板块与个股：白酒",
                   NEED_JSON, "阿狼的答案", "总结文本"])
    e = make_engine(chat_tmp, llm, analysts=("alang",), searcher=_ok_searcher)
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "贵州茅台最近有什么公告？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "done"
    # parse(1) + search_brief(1) + need(1) + analyze(1) + summary(1) = 5
    assert len(llm.calls) == 5

    # 搜索整理调用：system 要求结构化简报，user 含搜索结果
    brief_system = llm.calls[1][0]["content"]
    assert "结构化简报" in brief_system
    brief_user = llm.calls[1][1]["content"]
    assert "贵州茅台关于中期分红的公告" in brief_user
    assert "白酒板块午后拉升" in brief_user

    # 分析师上下文注入（与 market_data 并列）
    analyze_user = llm.calls[3][1]["content"]
    assert "## 联网检索简报" in analyze_user
    assert "中期分红落地" in analyze_user
    assert "## 数据清单与取数结果" in analyze_user  # 与数据清单并列
    assert assistant["degraded"] == []


def test_r11_search_failure_does_not_block(chat_tmp):
    """R9：搜索失败 → 简报「（未联网搜索）」且不进降级说明，流程不阻塞、不增加 LLM 调用。"""
    llm = FakeLLM([PARSE_JSON, NEED_JSON, "阿狼的答案", "总结文本"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))  # 默认 searcher：ok=False
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    resp = e.send_message(sid, "怎么看明天走势？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "done"
    assert len(llm.calls) == 4  # 搜索失败不调用 LLM 整理
    analyze_user = llm.calls[2][1]["content"]
    assert "## 联网检索简报" in analyze_user
    assert "（未联网搜索）" in analyze_user
    assert assistant["degraded"] == []  # 简报已标注，不重复进降级说明


def test_r11_search_exception_degrades(chat_tmp, monkeypatch):
    """R9：searcher 抛异常 → 降级「（未联网搜索）」且不阻塞（绝不向上抛）。"""
    def boom(query, code=None):
        raise RuntimeError("搜索服务不可用（mock）")

    llm = FakeLLM([PARSE_JSON, NEED_JSON, "阿狼的答案", "总结文本"])
    e = make_engine(chat_tmp, llm, analysts=("alang",), searcher=boom)
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    resp = e.send_message(sid, "怎么看？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "done"
    assert any("搜索失败" in d and "mock" in d for d in assistant["degraded"])
    analyze_user = llm.calls[2][1]["content"]
    assert "（未联网搜索）" in analyze_user


def test_r11_search_brief_llm_failure_falls_back_to_raw(chat_tmp):
    """R9：LLM 整理失败 → 降级为原始结果拼接（有结果不丢弃），流程继续。"""
    # 调用顺序（invoke_llm 失败重试 3 次，各消耗一个 mock 序号）：
    # parse(0) → 搜索简报(1/2/3, fail×3) → need(4) → analyze(5) → summary(6)
    llm = FakeLLM(
        [PARSE_JSON, "（占位：index1 抛异常）", "（占位：index2 抛异常）", "（占位：index3 抛异常）",
         NEED_JSON, "阿狼的答案", "总结文本"],
        fail_at={1, 2, 3},
    )
    e = make_engine(chat_tmp, llm, analysts=("alang",), searcher=_ok_searcher)
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    resp = e.send_message(sid, "怎么看？")
    assistant = resp["messages"][-1]
    assert assistant["stage"] == "done"
    assert any("简报生成失败" in d for d in assistant["degraded"])
    analyze_user = llm.calls[5][1]["content"]  # index5 = 分析师上下文
    assert "## 联网检索简报" in analyze_user
    assert "贵州茅台关于中期分红的公告" in analyze_user  # 原始结果拼接降级


# ══════════════════════════════════════════════════════════════════
# R11 R10：pending_round 在途判定 + worker 异常落盘说明
# ══════════════════════════════════════════════════════════════════


def test_r11_pending_round_flag(chat_tmp):
    """R10：meta.pending_round——末条 user 且无后续 assistant → True；落盘完成 → False。"""
    assert chat._compute_pending_round([]) is False
    assert chat._compute_pending_round([{"role": "assistant", "content": "a"}]) is False
    assert chat._compute_pending_round([{"role": "user", "content": "q"}]) is True
    assert chat._compute_pending_round(
        [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
         {"role": "user", "content": "q2"}]
    ) is True
    assert chat._compute_pending_round(
        [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    ) is False

    e = make_engine(chat_tmp, FakeLLM())
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]
    assert e.get_session(sid)["meta"]["pending_round"] is False  # 空会话
    # 模拟在途：仅落盘 user 消息（worker 尚未完成）
    chat.chat_storage.append_message(sid, {"role": "user", "content": "在途问题"},
                                     root=chat_tmp / "chats")
    assert e.get_session(sid)["meta"]["pending_round"] is True

    # 完整跑一轮（落盘 assistant）→ False
    llm = FakeLLM([PARSE_JSON, NEED_JSON, "阿狼的答案", "总结文本"])
    e2 = make_engine(chat_tmp, llm, analysts=("alang",))
    e2.send_message(sid, "正式问题")
    detail = e2.get_session(sid)
    assert detail["meta"]["pending_round"] is False
    assert detail["messages"][-1]["role"] == "assistant"


def test_r11_worker_exception_persists_interrupt_message(chat_tmp, monkeypatch):
    """R10：worker 异常 → 落盘「回复中断，可点击重试」，末条不再是 user。"""
    llm = FakeLLM([PARSE_JSON, NEED_JSON, "阿狼的答案", "总结文本"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    def boom_context(*args, **kwargs):
        raise RuntimeError("上下文组装异常（mock worker）")

    monkeypatch.setattr(ChatEngine, "_build_context", boom_context)

    resp = e.send_message(sid, "怎么看明天走势？")
    assert resp["disclaimer"] == DISCLAIMER
    assistant = resp["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["stage"] == "interrupted"
    assert "回复中断，可点击重试" in assistant["content"]
    assert assistant["content"].endswith(DISCLAIMER)

    detail = e.get_session(sid)
    assert detail["messages"][-1]["role"] == "assistant"
    assert detail["meta"]["pending_round"] is False


def test_r11_stream_worker_exception_emits_error_done(chat_tmp, monkeypatch):
    """R10：流式路径 worker 异常 → 落盘说明 + error → done 事件，最后消息为 assistant。"""
    llm = FakeLLM([PARSE_JSON, NEED_JSON, "阿狼的答案", "总结文本"])
    e = make_engine(chat_tmp, llm, analysts=("alang",))
    sid = e.create_session("stock", "600519", ["alang"])["session_id"]

    def boom_context(*args, **kwargs):
        raise RuntimeError("上下文组装异常（mock worker）")

    monkeypatch.setattr(ChatEngine, "_build_context", boom_context)
    events = []
    resp = e.stream_message(sid, "怎么看明天走势？", emit=lambda ev, pl: events.append((ev, pl)))

    # meta/user_msg + 3 个 stage（parse/need/fetch；R9 搜索不发 SSE 事件）+ error + done
    assert [ev for ev, _ in events] == ["meta", "user_msg",
                                        "stage", "stage", "stage",
                                        "error", "done"]
    assert "回复中断，可点击重试" in events[-1][1]["messages"][-1]["content"]
    assert resp["messages"][-1]["role"] == "assistant"
    assert e.get_session(sid)["meta"]["pending_round"] is False
