"""R15/R2+R4 分析师节点合并 + 空观点修复 + 工具结果脱敏（M2+M4）专项测试。

覆盖：
- 图结构：单一 analysts 节点（无 trend_analysts/sentiment_analysts）；
- 一次并行 5 位分析师，进度事件 trend（n/2）→ sentiment（n/3）按组排序发射，
  pct 严格单调，backend 契约（analysts_done 累计单调至 5）成立；
- 方案 B：情绪派提示词不再注入趋势派结论（trend_context 不注入）；
- R4：空话 content（如「本轮工具查询返回为空，无明确观点输出」）→ 工具摘要兜底/None；
- R4：工具结果脱敏（追加给 LLM 的 tool 消息无股票名称，tool_log 落盘保留原样）；
- R4：工具空结果显式化为「无数据（代码X）」结构。
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import stock_review_crew.graph as graph
from stock_review_crew.graph import build_graph, default_initial_state


def _fake_data():
    return {
        "date": "2026-08-03",
        "index": {
            "shanghai": {"close": 3764.0, "pct_change": 0.0052},
            "shenzhen": {"close": 12000.0, "pct_change": -0.0012},
        },
        "zhangting": {"total": 47, "tier": {"首板": 40, "2": 5, "3": 2}},
        "dieting": {"total": 3},
        "sectors": {"top5": [{"板块": "半导体", "涨跌幅": 0.02}]},
        "sentiment": {
            "yesterday_zt_count": 60,
            "avg_return_pct": 1.2,
            "red_rate_pct": 55.0,
            "lianban_rate_pct": 8.0,
        },
        "breadth": {"up": 2800, "down": 1800, "zhaban_rate_pct": 18.0},
        "news": {"caixin": [{"summary": "政策利好"}], "cctv": ["要闻一"]},
    }


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """离线约束：LLM 用确定性 mock，数据链路全 mock（同 test_graph.py 契约）。"""
    monkeypatch.setattr(graph, "_llm_ready", lambda: True)
    llm = _FakeLLM(_llm_factory)
    strict = _FakeLLM(_llm_factory)
    monkeypatch.setattr(graph, "_llm_pair", lambda: (llm, strict))
    monkeypatch.setattr(graph, "_callbacks", lambda: [])
    monkeypatch.setattr(graph, "_load_analyst_tools", lambda: (None, None))


@pytest.fixture()
def fetch_mode(monkeypatch):
    def _set(fn):
        monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: fn)
    return _set


class _Resp:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeLLM:
    """全流程 mock：bind_tools 原样返回；invoke 走工厂（确定性，线程安全）。"""

    def __init__(self, factory):
        self._factory = factory

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        return _Resp(self._factory(messages))


def _llm_factory(messages):
    user = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            user = m.get("content", "")
    if "## 资讯内容" in user:
        return "利多板块：无。"
    if "实质性分歧" in user:
        return "分歧判断：无\n总结：观点一致，无需辩论。"
    if "报告要求" in user:
        return "# 2026-08-03 A股收盘复盘报告\n## 一、市场概况\n略\n## 综合标签\n#测试标签A"
    return ("## 1、昨日复盘验证\n无昨日报告，跳过验证\n"
            "## 2、今日判断（今日大盘判断）\n看多\n"
            "## 3、操作计划（明日计划）\n进攻")


def _invoke(callback=None):
    app = build_graph(progress_callback=callback)
    initial = default_initial_state("2026-08-03")
    return app.invoke(initial)


def _skill(skill_id="alang", group="trend", name="阿狼"):
    return {"id": skill_id, "name": name, "group": group, "prompt": f"你是{name}，资深分析师。"}


class _Tool:
    """模拟 langchain 工具：invoke(args_dict) → fn(**args)。"""

    def __init__(self, fn):
        self._fn = fn

    def invoke(self, args):
        return self._fn(**args)


def _capturing_invoke_llm(monkeypatch, llm):
    """包装 _invoke_llm：记录每次调用时的消息快照，仍走真实 _invoke_llm 调用 fake。"""
    captured = []
    real = graph._invoke_llm

    def _wrapped(runner, messages, timeout=graph._INVOKE_TIMEOUT):
        captured.append([dict(m) if isinstance(m, dict) else m for m in messages])
        return real(runner, messages, timeout=timeout)

    monkeypatch.setattr(graph, "_invoke_llm", _wrapped)
    monkeypatch.setattr(graph, "_llm_pair", lambda: (llm, llm))
    return captured


# ── R2：节点合并 ──

def test_graph_has_single_analysts_node():
    nodes = set(build_graph().get_graph().nodes)
    assert "analysts" in nodes
    assert "trend_analysts" not in nodes
    assert "sentiment_analysts" not in nodes
    assert "news_analyst" in nodes and "host" in nodes
    # R16：辩论节点已移除——图里无 debate 节点，host 后直接 report
    assert "debate" not in nodes


def test_full_flow_five_analysts_one_batch(fetch_mode):
    fetch_mode(lambda mode, date: _fake_data())
    state = _invoke()
    assert len(state["analyses"]) == 6
    groups = [a["group"] for a in state["analyses"]]
    assert groups.count("trend") == 2
    assert groups.count("sentiment") == 4
    assert state["degraded"] is False
    assert "仅供参考，不构成投资建议" in state["final_report"]


def test_progress_ordered_and_job_contract(fetch_mode):
    fetch_mode(lambda mode, date: _fake_data())
    events = []
    _invoke(callback=lambda stage, pct, msg: events.append((stage, pct, msg)))

    stages = [e[0] for e in events]
    # R15/R2（2026-08-12）：统一「分析师」阶段，不再分趋势派/情绪派
    assert "analysts" in stages and "trend" not in stages and "sentiment" not in stages
    pcts = [e[1] for e in events]
    assert pcts == sorted(pcts)
    assert pcts[-1] == 100.0

    msgs = [e[2] for e in events]
    assert "分析师 1/6" in "|".join(msgs)
    assert "分析师 6/6" in "|".join(msgs)

    # 模拟 backend._progress_cb：analysts 阶段消息 n/m → analysts_done 单调至 5
    done_list = []
    for stage, _pct, msg in events:
        mt = re.search(r"(\d+)/(\d+)", msg or "")
        if mt and stage == "analysts":
            done, total = int(mt.group(1)), int(mt.group(2))
            done_list.append((done, total))
    assert done_list[-1] == (6, 6)
    assert [d for d, _t in done_list] == sorted(d for d, _t in done_list)


def test_sentiment_prompt_has_no_trend_context(monkeypatch):
    """方案 B：情绪派提示词不再注入趋势派结论（trend_block 为空）。"""
    llm = _FakeLLM(lambda messages: _llm_factory(messages))
    captured = _capturing_invoke_llm(monkeypatch, llm)
    state = default_initial_state("2026-08-03")

    entry = graph._llm_analyst(state, _skill("yangjia", "sentiment", "炒股养家"), "sentiment", "")
    assert entry is not None and entry["group"] == "sentiment"
    user = ""
    for m in captured[0]:
        if m.get("role") == "user":
            user = m.get("content", "")
    assert "趋势分析师的大盘判断" not in user
    assert "趋势派" not in user


# ── R4：空观点修复 ──

def test_empty_opinion_without_tools_returns_none(monkeypatch):
    """空话 content + 无工具调用 → 返回 None（规则引擎兜底），空话不进报告。"""
    llm = _FakeLLM(lambda messages: "本轮工具查询返回为空，无明确观点输出")
    _capturing_invoke_llm(monkeypatch, llm)
    state = default_initial_state("2026-08-03")
    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is None


def test_empty_opinion_with_tools_uses_tool_summary(monkeypatch):
    """空话 content + 有工具调用 → 工具摘要兜底：非空且不含空话句子。"""
    script = [
        _Resp("", tool_calls=[{"id": "call_1", "name": "search_history",
                               "args": {"query": "连板"}}]),
        _Resp("本轮工具查询返回为空，无明确观点输出"),
    ]

    class _ScriptedLLM:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, config=None):
            return script.pop(0)

    def _fake_search_history(query):
        return json.dumps({"query": query, "results": [{"rank": 1, "content": "历史记录"}],
                           "note": "ok"}, ensure_ascii=False)

    llm = _ScriptedLLM()
    _capturing_invoke_llm(monkeypatch, llm)
    monkeypatch.setattr(
        graph, "_load_analyst_tools",
        lambda: (None, _Tool(_fake_search_history)))
    state = default_initial_state("2026-08-03")

    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is not None
    assert entry["analysis"]
    assert "本轮工具查询返回为空" not in entry["analysis"]
    assert "无明确观点" not in entry["analysis"]
    assert entry["tool_used"] is True


def test_short_content_treated_as_empty(monkeypatch):
    """过短 content（<30 字符）按空处理：无工具 → None。"""
    llm = _FakeLLM(lambda messages: "无。")
    _capturing_invoke_llm(monkeypatch, llm)
    state = default_initial_state("2026-08-03")
    assert graph._llm_analyst(state, _skill(), "trend", "") is None


def test_forced_summary_after_tool_loop_empty(monkeypatch):
    """2026-08-12（阿狼/爱在冰川 8-10/8-12 案例）：工具循环后 LLM 回复为空 →
    追加一次强制总结调用（无工具），用其输出作为分析正文，不再显示「LLM 回复为空」占位。"""
    script = [
        _Resp("", tool_calls=[{"id": "call_1", "name": "get_positions", "args": {}}]),
        _Resp(""),  # 工具结果后 LLM 仍无正文 → 循环退出（无 tool_calls）
        _Resp("## 1、今日判断\n修复首日，情绪回暖。\n## 2、操作计划\n002379：买入价21.34元，仓位2成"),
    ]

    class _ScriptedLLM:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, config=None):
            return script.pop(0)

    llm = _ScriptedLLM()
    captured = _capturing_invoke_llm(monkeypatch, llm)
    monkeypatch.setattr(graph, "_load_analyst_tools", lambda: (None, None))
    state = default_initial_state("2026-08-03")

    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is not None
    assert "修复首日，情绪回暖" in entry["analysis"]          # 强制总结正文
    assert "LLM 回复为空" not in entry["analysis"]            # 不再占位
    assert "工具查询摘要" not in entry["analysis"]            # 未走工具摘要兜底
    assert len(captured) == 3                                  # 第 3 次为强制总结调用
    last_msgs = captured[-1]
    assert any("不要再调用任何工具" in str(m.get("content", "")) for m in last_msgs
               if isinstance(m, dict))


def test_natural_language_summary_retried_to_format(monkeypatch):
    """2026-08-12（8-03 拔小弦案例）：LLM 只输出自然语言总结、无推荐行 →
    重试一次（明示按冻结格式输出），重试成功用重试内容；不依赖任何下单工具。"""
    script = [
        _Resp("股数计算完成（002202买1000股、000400买400股，合计约2.8成），记录次日买入计划。"),
        _Resp("## 1、今日判断\n修复首日，情绪回暖。\n## 2、操作计划\n买入 002202：买入价18.60元，仓位2成"),
    ]

    class _ScriptedLLM:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, config=None):
            return script.pop(0)

    llm = _ScriptedLLM()
    captured = _capturing_invoke_llm(monkeypatch, llm)
    monkeypatch.setattr(graph, "_load_analyst_tools", lambda: (None, None))
    state = default_initial_state("2026-08-03")

    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is not None
    assert "买入 002202：买入价18.60元，仓位2成" in entry["analysis"]  # 重试后的格式行
    assert "股数计算完成" not in entry["analysis"]                    # 自然语言总结被替换
    assert len(captured) == 2                                          # 首轮 + 1 次重试


def test_sell_plan_skipped_when_no_position(monkeypatch):
    """2026-08-12（铁锤 8-07 案例）：文本含卖出计划但账户无该持仓 →
    程序兜底跳过（不写 plan），tool_log 记录 sell_skipped；有持仓的卖出正常记录。"""
    from stock_review_crew.accounts import storage as acct_storage

    added: list[dict] = []
    monkeypatch.setattr("stock_review_crew.accounts.plan.add",
                        lambda rec: added.append(rec) or {"ok": True, "reason": "ok", "trade": None})
    monkeypatch.setattr(
        acct_storage, "load_account",
        lambda sid: {"cash": 100000.0,
                     "positions": {"600519": {"qty": 1000, "frozen_qty": 0,
                                              "avg_cost": 10.0, "name": "x"}},
                     "trades": []})
    monkeypatch.setattr(acct_storage, "load_trades", lambda sid: [])

    llm = _FakeLLM(lambda messages: (
        "## 1、今日判断\n看多\n"
        "## 2、操作计划\n"
        "卖出 600519：卖出价10.50元，卖出100股\n"
        "卖出 003001：卖出价20.00元，卖出400股\n"))
    _capturing_invoke_llm(monkeypatch, llm)
    monkeypatch.setattr(graph, "_load_analyst_tools", lambda: (None, None))
    state = default_initial_state("2026-08-03")

    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is not None
    sells = [r for r in added if r.get("side") == "sell"]
    assert [r["code"] for r in sells] == ["600519"]          # 只有有持仓的卖出被记录
    skipped = [t for t in (entry.get("tool_calls") or []) if t.get("type") == "sell_skipped_no_position"]
    assert any(t.get("code") == "003001" for t in skipped)   # 无持仓卖出被跳过并记录


# ── R15：报告推荐行格式校验 ──

def test_recommendation_format_ok():
    """2026-08-12（用户反馈）：推荐行只写代码——禁止名称/括号代码/一行多只/区间。"""
    good = "## 四、明日操作计划\n002379：买入价21.34元，仓位2成\n600519：买入价1320.00元，仓位3成\n无"
    assert graph._recommendation_format_ok(good) is True
    assert graph._recommendation_format_ok("无推荐") is True
    assert graph._recommendation_format_ok("") is True
    # 代码后跟名称（旧格式，用户要求禁止）
    assert graph._recommendation_format_ok(
        "002379 弘信电子：买入价21.34元，仓位2成") is False
    # 括号代码（用户 8-12 案例）
    assert graph._recommendation_format_ok(
        "弘信电子（002379）买入价21.34元，仓位2成") is False
    # 一行多只
    assert graph._recommendation_format_ok(
        "002379：买入价21.34元，仓位2成、600519：买入价30.00元，仓位1成") is False
    # 无买入价但括号代码 + 成/元（用户原文「弘信电子（002379）21.34元，2成」）
    assert graph._recommendation_format_ok("弘信电子（002379）21.34元，2成") is False
    # 描述行不误伤（正文转述，非推荐行）
    assert graph._recommendation_format_ok(
        "光迅科技（002281）今日+0.52%收185.03站上MA5，通信板块净流入130亿居首") is True


# ── R15：降级标注过滤（用户口径：除非全链路缺失，否则不标注）──

def test_filter_usable_degraded():
    """主源失败但备用源成功（数据可用）→ 丢弃标注；数据缺失 → 保留。"""
    blocks = {
        "sectors": {"top5": [], "source": "腾讯行业排行", "degraded": True,
                    "degraded_reason": ["同花顺不可用，降级腾讯行业排行"]},
        "minute": {"bars": [{"time": "09:30", "close": 1.0}], "source": "腾讯分时"},
        "news": {"caixin": [], "source": "数据缺失"},
    }
    notes = [
        "sectors: 同花顺不可用，降级腾讯行业排行",
        "minute: 东财不可用，降级腾讯分时",
        "news: 央视无数据",
        "index_trend: 通达信akshare失败，降级在线日K备用源",
    ]
    kept = graph._filter_usable_degraded(notes, blocks)
    assert "sectors: 同花顺不可用，降级腾讯行业排行" not in kept   # 数据可用 → 丢弃
    assert "minute: 东财不可用，降级腾讯分时" not in kept           # 数据可用 → 丢弃
    assert "news: 央视无数据" in kept                                 # 数据缺失 → 保留
    assert "index_trend: 通达信akshare失败，降级在线日K备用源" in kept  # 无法匹配块 → 保守保留


def test_normal_content_unaffected(monkeypatch):
    """正常 content 不受空话检测影响。"""
    normal = ("## 1、昨日复盘验证\n无昨日报告，跳过验证\n"
              "## 2、今日判断（今日大盘判断）\n指数放量上行，情绪回暖\n"
              "## 3、操作计划（明日计划）\n进攻")
    llm = _FakeLLM(lambda messages: normal)
    _capturing_invoke_llm(monkeypatch, llm)
    state = default_initial_state("2026-08-03")
    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is not None
    assert entry["analysis"] == normal


# ── R4：工具结果脱敏 + 空结果显式化 ──

def _tool_loop_llm(tool_name="get_stock_info", tool_args=None, final=None):
    """脚本化 LLM：首轮发工具调用，次轮返回最终观点。"""
    tool_args = tool_args or {"code": "600519", "date": "2026-08-03"}
    final = final or ("## 1、昨日复盘验证\n无昨日报告，跳过验证\n"
                      "## 2、今日判断（今日大盘判断）\n看多\n"
                      "## 3、操作计划（明日计划）\n进攻")

    class _ScriptedLLM:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, config=None):
            if not getattr(self, "_tool_round_done", False):
                self._tool_round_done = True
                return _Resp("", tool_calls=[
                    {"id": "call_1", "name": tool_name, "args": tool_args}])
            return _Resp(final)

    return _ScriptedLLM()


def test_tool_result_sanitized_before_llm(monkeypatch):
    """工具返回含股票名称 → 追加给 LLM 的 tool 消息无名称、保留代码；
    落盘 tool_log 保留原样（R1：只影响发给 LLM 的文本）。"""
    def _fake_get_stock_info(code, date="2026-08-03"):
        return json.dumps({"code": code, "name": "贵州茅台", "close": 1320.0},
                          ensure_ascii=False)

    llm = _tool_loop_llm(tool_name="get_stock_info")
    captured = _capturing_invoke_llm(monkeypatch, llm)
    monkeypatch.setattr(graph, "_load_analyst_tools",
                        lambda: (_Tool(_fake_get_stock_info), None))
    state = default_initial_state("2026-08-03")

    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is not None
    tool_msg = [m for m in captured[1] if isinstance(m, dict) and m.get("role") == "tool"][0]["content"]
    assert "贵州茅台" not in tool_msg
    assert "600519" in tool_msg
    # tool_log 落盘保留原样（报告层展示不受影响）
    assert "贵州茅台" in entry["tool_calls"][0]["result"]


def test_tool_empty_result_explicit_no_data(monkeypatch):
    """工具空返回（search_history 无命中）→ 显式化为「无数据（代码X）」结构。"""
    def _fake_search_history(query):
        return json.dumps({"query": query, "results": [], "note": "无相关历史记录"},
                          ensure_ascii=False)

    llm = _tool_loop_llm(tool_name="search_history", tool_args={"query": "连板股"})
    captured = _capturing_invoke_llm(monkeypatch, llm)
    monkeypatch.setattr(graph, "_load_analyst_tools",
                        lambda: (None, _Tool(_fake_search_history)))
    state = default_initial_state("2026-08-03")

    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is not None
    tool_msg = [m for m in captured[1] if isinstance(m, dict) and m.get("role") == "tool"][0]["content"]
    assert "无数据" in tool_msg
    assert "连板股" in tool_msg


def test_get_stock_info_missing_explicit_no_data(monkeypatch):
    """get_stock_info 数据缺失（全 None + source=数据缺失）→ 「无数据（代码X）」。"""
    def _fake_get_stock_info(code, date="2026-08-03"):
        return json.dumps({"code": code, "name": None, "close": None,
                           "source": "数据缺失", "degraded": True},
                          ensure_ascii=False)

    llm = _tool_loop_llm(tool_name="get_stock_info")
    captured = _capturing_invoke_llm(monkeypatch, llm)
    monkeypatch.setattr(graph, "_load_analyst_tools",
                        lambda: (_Tool(_fake_get_stock_info), None))
    state = default_initial_state("2026-08-03")

    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is not None
    tool_msg = [m for m in captured[1] if isinstance(m, dict) and m.get("role") == "tool"][0]["content"]
    assert "无数据（代码600519）" in tool_msg


def test_place_order_result_no_side_effect(monkeypatch):
    """place_order 返回（账户相关，无名称）经脱敏幂等不变。"""
    def _fake_get_stock_info(code, date="2026-08-03"):
        return json.dumps({"code": code, "close": 10.5}, ensure_ascii=False)

    llm = _tool_loop_llm(tool_name="get_stock_info")
    captured = _capturing_invoke_llm(monkeypatch, llm)
    monkeypatch.setattr(graph, "_load_analyst_tools",
                        lambda: (_Tool(_fake_get_stock_info), None))
    state = default_initial_state("2026-08-03")

    entry = graph._llm_analyst(state, _skill(), "trend", "")
    assert entry is not None
    tool_msg = [m for m in captured[1] if isinstance(m, dict) and m.get("role") == "tool"][0]["content"]
    assert "10.5" in tool_msg


# ── R12：分析师账户快照聚合（meta.accounts 数据源，修复 meta.accounts 恒空）──

def _fake_acct(cash=100000.0, realized_pnl=0.0, current_date="2026-08-03", positions=None):
    """模拟 _llm_analyst 返回的 account：核心字段 + trades/snapshots 大字段。"""
    return {
        "skill_id": "x",
        "cash": cash,
        "positions": positions or {},
        "current_date": current_date,
        "realized_pnl": realized_pnl,
        # 大字段（快照瘦身时丢弃，完整 trades 仍在 analyses[i].account）
        "trades": [{"side": "buy", "code": "600519", "status": "成交", "qty": 100}],
        "snapshots": [{"date": "2026-08-02", "total_assets": cash}],
    }


def test_analysts_node_aggregates_account_snapshots(monkeypatch):
    """R12：analysts_node 聚合 {skill_id: account}——仅 skill_id 非空且 account 为 dict
    的条目入选；规则引擎降级（无 account）/skill_id 空/account 非 dict 跳过；
    快照瘦身为核心字段（trades/snapshots 不进 meta）。"""
    results = [
        {"skill_id": "alang", "skill_name": "阿狼", "analysis": "看多", "degraded": False,
         "account": _fake_acct(cash=98000.0, positions={
             "600519": {"name": "贵州茅台", "qty": 100, "frozen_qty": 0,
                        "avg_cost": 1500.0, "extra": "裁剪字段"}})},
        {"skill_id": "yangjia", "skill_name": "炒股养家", "analysis": "观望", "degraded": False,
         "account": _fake_acct(cash=101000.0, realized_pnl=-500.0)},
        {"skill_id": "baxiaoxian", "skill_name": "拔小弦", "analysis": "规则引擎观点",
         "degraded": True},                                     # 无 account（规则引擎降级）
        {"skill_id": "", "skill_name": "无名", "analysis": "x", "degraded": False,
         "account": _fake_acct()},                              # skill_id 空 → 跳过
        {"skill_id": "tiechui", "skill_name": "铁锤", "analysis": "y", "degraded": False,
         "account": "not-a-dict"},                              # account 非 dict → 跳过
    ]
    monkeypatch.setattr(graph, "_run_analysts", lambda state: results)
    out = graph.analysts_node({"date": "2026-08-03"})

    assert set(out["analyst_accounts"]) == {"alang", "yangjia"}
    alang = out["analyst_accounts"]["alang"]
    assert alang["cash"] == 98000.0
    assert alang["realized_pnl"] == 0.0
    assert alang["current_date"] == "2026-08-03"
    assert alang["positions"]["600519"] == {
        "name": "贵州茅台", "qty": 100, "frozen_qty": 0, "avg_cost": 1500.0}
    assert "extra" not in alang["positions"]["600519"]          # 持仓额外字段裁剪
    assert "trades" not in alang and "snapshots" not in alang   # 大字段不进 meta
    assert out["analyst_accounts"]["yangjia"]["cash"] == 101000.0
    assert out["analyst_accounts"]["yangjia"]["realized_pnl"] == -500.0
    # analyses/degraded/degraded_notes 行为不变
    assert out["analyses"] is results
    assert out["degraded"] is True
    assert out["degraded_notes"] == ["分析师 LLM 不可用/失败，分析降级为规则引擎"]


def test_analysts_node_no_accounts_empty_dict(monkeypatch):
    """R12：全部条目无 account（全规则引擎降级）→ analyst_accounts 为空 dict 不报错。"""
    results = [
        {"skill_id": "alang", "skill_name": "阿狼", "analysis": "规则观点", "degraded": True},
        {"skill_id": "yangjia", "skill_name": "炒股养家", "analysis": "规则观点", "degraded": True},
    ]
    monkeypatch.setattr(graph, "_run_analysts", lambda state: results)
    out = graph.analysts_node({"date": "2026-08-03", "degraded": False})
    assert out["analyst_accounts"] == {}
    assert out["degraded"] is True
    assert out["analyses"] == results


def test_full_flow_analyst_accounts_populated(fetch_mode):
    """R12 全流程：graph 跑完后 state.analyst_accounts 已聚合（键=skill_id，值=账户 dict），
    backend main.py 读 state["analyst_accounts"] 写入 meta["accounts"] 不再恒空。"""
    fetch_mode(lambda mode, date: _fake_data())
    state = _invoke()
    assert state["analyst_accounts"]
    assert set(state["analyst_accounts"]) == {
        a["skill_id"] for a in state["analyses"] if a.get("skill_id")}
    assert all(isinstance(v, dict) for v in state["analyst_accounts"].values())
