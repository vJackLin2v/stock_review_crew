"""I3 聊天引擎：会话 / 单分析师 / 多分析师交叉问答 / 历史对话检索 / 免责声明强制

契约（requirements.md §一.4 / §五 / §六、issues I3）：
- 上下文 = 分析师 skill 人格（只读 skills/**）+ 标的实时/历史数据（防御性调用 tools
  纯函数接口，I1 并行重写中，失败降级为「数据暂时不可用」）+ chroma 相关复盘结论
  （knowledge_store.search 只读调用）+ 历史消息；
- 多分析师交叉问答：用户消息后，首位分析师回答，后续逐位对上一轮回答表态
  （同意/反对/补充理由），串行执行，LLM 超时 120s（环境变量 CHAT_LLM_TIMEOUT 可配置），
  最后汇总为一条回复（含每位分析师片段 + 汇总）；单分析师模式直接由该分析师回答；
- 免责声明在组装层强制追加，响应恒含 ``disclaimer`` 字段；
- LLM 无 Key/失败 → 中文说明 + 免责声明，绝不抛异常。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

from .storage import chats as chat_storage
from .llm_runtime import invoke_llm

DISCLAIMER = "仅供参考，不构成投资建议"
DEFAULT_ANALYST_IDS = ["alang", "bingchuan", "baxiaoxian", "yangjia", "tiechui", "daxianzun"]
DEFAULT_LLM_TIMEOUT = 120
CHROMA_COLLECTION = "chat_history"
CHROMA_CHUNK_SIZE = 400
CHROMA_CHUNK_OVERLAP = 100
HISTORY_WINDOW = 10
MAX_CONTEXT_CHARS = 1600


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _compute_pending_round(messages: Optional[list]) -> bool:
    """R11 R10 在途轮次判定：最后一条消息是 user 且其后无 assistant →
    视为在途（未完成落盘）返回 True；完成/落盘（末条为 assistant）返回 False。"""
    for m in reversed(messages or []):
        role = m.get("role") if isinstance(m, dict) else None
        if role == "assistant":
            return False
        if role == "user":
            return True
    return False


# ═══════════════════════════════════════════════════════════
# 分析师白名单（只读 skills/**）
# ═══════════════════════════════════════════════════════════
def get_analyst_whitelist() -> list[str]:
    """分析师白名单：5 位分析师（skills/** 与内置名单交集，排除主持人/复盘助手）。"""
    try:
        from stock_review_crew.skills import list_skills

        present = {s.get("id") for s in list_skills() if s.get("id")}
        ids = [sid for sid in DEFAULT_ANALYST_IDS if sid in present]
        if ids:
            return ids
    except Exception:
        pass
    return list(DEFAULT_ANALYST_IDS)


def load_analyst(analyst_id: str) -> Optional[dict]:
    """加载分析师 skill（含 prompt 人格）；缺失返回 None。"""
    try:
        from stock_review_crew.skills import load_skill

        return load_skill(analyst_id)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
# LLM 调用与数据层降级
# ═══════════════════════════════════════════════════════════
def _call_llm(llm: Any, messages: list[dict]) -> str:
    """调用 LLM 并抽取文本；任何异常向上抛给调用方做降级。

    统一走 llm_runtime.invoke_llm（graph.py / trades 同款共享适配层）：
    获得 150s 硬超时（守护线程 + join）+ 失败重试 2 次 + Langfuse 回调故障隔离。
    """
    if llm is None:
        raise RuntimeError("LLM 未配置")
    result = invoke_llm(llm, messages)
    if hasattr(result, "content"):
        text = result.content
    else:
        text = result
    text = str(text or "").strip()
    if not text:
        raise RuntimeError("LLM 返回空内容")
    return text


def _stream_llm(llm: Any, messages: list[dict], on_delta) -> str:
    """流式调用 LLM：逐块回调 on_delta(text)；不支持/失败时回退整段单次回调。

    **流式路径未走 invoke_llm**：llm.stream(...) 逐 token 迭代 + SSE 逐段推送
    无法套用 invoke_llm 的「一次性 invoke + 守护线程 join」模型（会破坏流式语义），
    超时依赖 httpx timeout（make_llm 120s）；仅当流式整体失败时回退到
    _call_llm（invoke_llm 通道，含 150s 硬超时 + 重试）。
    """
    if llm is None:
        raise RuntimeError("LLM 未配置")
    full: list[str] = []
    streamed = False
    try:
        for chunk in llm.stream(messages):
            piece = getattr(chunk, "content", chunk)
            if piece:
                piece = str(piece)
                full.append(piece)
                on_delta(piece)
                streamed = True
    except Exception:
        streamed = False
        full = []
    if not streamed:
        text = _call_llm(llm, messages)
        if text:
            on_delta(text)
        return text
    text = "".join(full).strip()
    if not text:
        raise RuntimeError("LLM 流式返回空内容")
    return text


def _call_tool(func: Any, kwargs: dict) -> str:
    """兼容 langchain Tool（.invoke）与纯函数两种接口（I1 重写中）。"""
    try:
        if hasattr(func, "invoke"):
            return str(func.invoke(kwargs))
        return str(func(**kwargs))
    except TypeError:
        return str(func(kwargs))


def _fetch_target_data(target_type: str, target: str) -> dict:
    """防御性调用 I1 数据层纯函数接口；失败降级为「数据暂时不可用」。"""
    import importlib

    try:
        tools = importlib.import_module("stock_review_crew.tools.stock_data")
        today = datetime.now().strftime("%Y-%m-%d")
        if target_type == "stock":
            raw = _call_tool(tools.get_stock_info, {"code": target, "date": today})
        else:
            raw = _call_tool(tools.get_market_micro, {"date": today})
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("error"):
                return {"available": False, "note": f"数据暂时不可用：{parsed['error']}", "data": None}
        except (TypeError, ValueError):
            pass
        return {"available": True, "note": None, "data": raw}
    except Exception as e:
        return {"available": False, "note": f"数据暂时不可用：{e}", "data": None}


# ═══════════════════════════════════════════════════════════
# R8 聊天智能流程：问题解析 / 数据需求 / 取数 / 分析 / 总结
# ═══════════════════════════════════════════════════════════

INTENTS = ("trend", "sentiment", "buy_point", "risk", "compare", "other")
TIME_RANGES = ("当日", "近5日", "近20日", "近60日")

# data_key 白名单（v4 §四）：仅允许以下键，映射到 tools 的 fetch_* 纯函数
# 或 realtime 实时接口；每个函数自带降级链（主源→备用→缓存→数据缺失标注）。
DATA_KEY_WHITELIST = {
    "stock_info": "个股基本信息、近期走势与均线（含近5日日K recent5）",
    "stock_quote": "个股实时行情（盘中）",
    "minute_kline": "当日分时/分钟线",
    "minute_data": "当日分时/分钟线（tools fetch_minute_data 契约键名）",
    "index_trend": "指数近 N 日走势与多周期均线",
    "market_micro": "当日微观聚合（指数/涨跌停/涨跌家数/板块）",
    "zt_pool": "涨停/跌停/炸板池与梯队",
    "sectors": "板块涨跌幅与资金排名",
    "breadth": "全市场涨跌家数与炸板率",
    "market_breadth": "全市场涨跌家数与炸板率（tools fetch_market_breadth 契约键名）",
    "sentiment": "情绪指标（昨涨今表现/连板率/核按钮率）",
    "news": "财经头条与资讯",
    "news_headlines": "财经头条与资讯（tools fetch_news_headlines 契约键名）",
    "lhb": "龙虎榜",
    "market_macro": "宏观外围（纳指/A50/股指期货）",
    "auction_quote": "竞价数据（高开幅度/竞价金额）",
    "auction": "竞价复盘模式取数（开盘啦→同花顺→东财推算）",
    "pre_market": "早盘前决策模式取数（外盘/消息/指数均线）",
    "intraday_am": "上午盘中模式取数",
    "noon": "午间复盘模式取数",
    "intraday_pm": "下午盘中模式取数",
    "close": "收盘复盘模式取数（指数/涨跌停/板块/情绪/龙虎榜/资讯）",
    "mode_data": "按 6 模式取数（聊天默认 close 口径）",
    "realtime_indices": "四大指数实时行情",
    "realtime_zt": "涨跌停/炸板/封板率实时统计",
    "realtime_sector_flow": "板块实时资金流向",
    "realtime_breadth": "全市场实时涨跌家数",
    "realtime_news": "财经快讯（新浪7x24）",
    "realtime_sentiment": "昨日涨停今日表现实时计算",
}

# 关键数据（缺失即终止本轮，v4 Q5）：回答的核心数据支柱；
# 其余键（竞价/龙虎榜/资讯/模式聚合等）缺失 → 降级标注并继续回答，
# 防止单块可选数据缺失导致整轮失败（2026-08-04 用户实测：auction_quote 缺失整轮终止）。
CRITICAL_KEYS = {
    "stock_info", "stock_quote", "market_micro", "index_trend", "zt_pool",
    "sectors", "breadth", "market_breadth", "sentiment",
    "realtime_indices", "realtime_zt", "realtime_breadth", "realtime_sentiment",
}

# data_key 别名归一：同一取数函数的双键名只保留一个（避免重复取数与重复缺失）
_ALIAS_TO_CANONICAL = {
    "minute_data": "minute_kline",
    "market_breadth": "breadth",
    "news_headlines": "news",
}

# (模块名, 函数名, kwargs 构造器)；函数在调用时按名解析，便于 monkeypatch。
FETCH_DISPATCH = {
    "stock_quote": ("realtime", "fetch_stock_quote", lambda p, m, t, d, tg: {"code": tg}),
    "minute_kline": ("stock_data", "fetch_minute_data", lambda p, m, t, d, tg: {"symbol": tg, "date": t, "period": 1}),
    "minute_data": ("stock_data", "fetch_minute_data", lambda p, m, t, d, tg: {"symbol": tg, "date": t, "period": 1}),
    "index_trend": ("stock_data", "fetch_index_trend", lambda p, m, t, d, tg: {"date": t, "days": d}),
    "zt_pool": ("stock_data", "fetch_zt_pool", lambda p, m, t, d, tg: {"date": t}),
    "sectors": ("stock_data", "fetch_sectors", lambda p, m, t, d, tg: {"date": t}),
    "breadth": ("stock_data", "fetch_market_breadth", lambda p, m, t, d, tg: {"date": t}),
    "market_breadth": ("stock_data", "fetch_market_breadth", lambda p, m, t, d, tg: {"date": t}),
    "sentiment": ("stock_data", "fetch_sentiment", lambda p, m, t, d, tg: {"date": t}),
    "news": ("stock_data", "fetch_news_headlines", lambda p, m, t, d, tg: {"date": t}),
    "news_headlines": ("stock_data", "fetch_news_headlines", lambda p, m, t, d, tg: {"date": t}),
    "lhb": ("stock_data", "fetch_lhb", lambda p, m, t, d, tg: {"date": t}),
    "market_macro": ("stock_data", "fetch_market_macro", lambda p, m, t, d, tg: {"date": t}),
    "auction_quote": ("stock_data", "fetch_auction_quote", lambda p, m, t, d, tg: {"date": t}),
    "auction": ("stock_data", "fetch_auction", lambda p, m, t, d, tg: {"date": t}),
    "pre_market": ("stock_data", "fetch_pre_market", lambda p, m, t, d, tg: {"date": t}),
    "intraday_am": ("stock_data", "fetch_intraday_am", lambda p, m, t, d, tg: {"date": t}),
    "noon": ("stock_data", "fetch_noon", lambda p, m, t, d, tg: {"date": t}),
    "intraday_pm": ("stock_data", "fetch_intraday_pm", lambda p, m, t, d, tg: {"date": t}),
    "close": ("stock_data", "fetch_close", lambda p, m, t, d, tg: {"date": t}),
    "mode_data": ("stock_data", "fetch_mode_data", lambda p, m, t, d, tg: {"mode": "close", "date": t}),
    "realtime_indices": ("realtime", "fetch_indices", None),
    "realtime_zt": ("realtime", "fetch_zt_stats", lambda p, m, t, d, tg: {"date": t}),
    "realtime_sector_flow": ("realtime", "fetch_sector_flow", None),
    "realtime_breadth": ("realtime", "fetch_breadth_realtime", None),
    "realtime_news": ("realtime", "fetch_news", None),
    "realtime_sentiment": ("realtime", "fetch_sentiment_realtime", lambda p, m, t, d, tg: {"date": t}),
}

_TIME_RANGE_ALIASES = {
    "当日": "当日", "今日": "当日", "今天": "当日", "当天": "当日",
    "近5日": "近5日", "近五日": "近5日", "5日": "近5日", "5天": "近5日", "一周": "近5日",
    "近20日": "近20日", "近二十日": "近20日", "20日": "近20日", "20天": "近20日",
    "一月": "近20日", "一个月": "近20日", "近一月": "近20日", "近一个月": "近20日",
    "近60日": "近60日", "近六十日": "近60日", "60日": "近60日", "60天": "近60日",
    "两月": "近60日", "两个月": "近60日", "近两月": "近60日", "近两个月": "近60日",
}


class PipelineError(RuntimeError):
    """流水线终止（解析失败/标的不明/取数失败），消息为中文说明。"""


def search_stocks(query: str) -> list[dict]:
    """股票名称/代码搜索（realtime.search_stocks 只读包装，便于测试打补丁）。"""
    from .tools.realtime import search_stocks as _search

    return _search(query)


def _extract_json(text: Any) -> Any:
    """从 LLM 输出中提取 JSON（兼容 ```json 代码围栏与前后杂文本）。"""
    t = str(text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t, flags=re.S)
    t = t.strip()
    # 以首个非空白字符决定顶层类型：数组优先于对象，避免把数组首元素误当顶层
    order = ("[", "]") if t.startswith("[") else ("{", "}")
    for open_ch, close_ch in (order, ("[", "]") if order[0] == "{" else ("{", "}")):
        s, e = t.find(open_ch), t.rfind(close_ch)
        if s != -1 and e > s:
            return json.loads(t[s : e + 1])
    raise ValueError("未找到 JSON 内容")


def _normalize_time_range(value: Any) -> str:
    """时间范围归一：非法/缺失默认「近5日」（v4 §四）。"""
    s = str(value or "").strip()
    if s in _TIME_RANGE_ALIASES:
        return _TIME_RANGE_ALIASES[s]
    if "60" in s:
        return "近60日"
    if "20" in s:
        return "近20日"
    if "两" in s or "二" in s or "2" in s:
        return "近60日" if "月" in s else "近20日"
    if "月" in s:
        return "近20日"
    if "5" in s or "周" in s or "7" in s:
        return "近5日"
    return "近5日"


def _time_range_days(time_range: Any) -> int:
    return {"当日": 1, "近5日": 5, "近20日": 20, "近60日": 60}.get(time_range, 5)


def _resolve_target(target_raw: str, meta: dict) -> str:
    """标的归一：6 位代码直通；名称用 realtime.search_stocks 解析；
    板块会话保留名称；解析失败抛 PipelineError（取数前终止）。"""
    target_type = meta.get("target_type", "stock")
    if target_type == "sector":
        if not target_raw:
            raise PipelineError("未能识别标的：缺少板块名称")
        return target_raw
    if re.fullmatch(r"\d{6}", target_raw):
        return target_raw.zfill(6)
    name = target_raw or str(meta.get("target") or "")
    if re.fullmatch(r"\d{6}", name):
        return name.zfill(6)
    if not name:
        raise PipelineError("未能识别标的：问题未指明且会话标的不明")
    try:
        hits = search_stocks(name)
    except Exception as exc:  # noqa: BLE001 - 中文说明即可
        raise PipelineError(f"未能识别标的：{name}（{exc}）") from exc
    if not hits:
        raise PipelineError(f"未能识别标的：{name}")
    code = str(hits[0].get("code") or "").zfill(6)
    if not code or code == "000000":
        raise PipelineError(f"未能识别标的：{name}")
    return code


def _parse_question(llm: Any, meta: dict, content: str, history_text: str) -> dict:
    """步骤 1：一个 LLM 调用解析用户问题 → {intent, target, time_range}。"""
    target_type = meta.get("target_type", "stock")
    session_target = str(meta.get("target") or "")
    system = (
        "你是 A 股聊天的问题解析器。把用户问题解析为结构化 JSON，只输出 JSON，不要任何解释。\n"
        '格式：{"intent":"<intent>","target":"<代码或名称>","time_range":"<时间范围>"}\n'
        "intent 只允许：trend(趋势/走势)、sentiment(情绪/赚钱效应)、buy_point(买点/介入)、"
        "risk(风险/回避)、compare(对比/比较)、other(其他)。\n"
        "time_range 只允许：当日、近5日、近20日、近60日（默认近5日）。\n"
        "target：用户提到的股票名称或 6 位代码；用户未指明标的时使用会话标的；"
        "板块会话中输出板块名称。"
    )
    user = json.dumps(
        {
            "会话标的类型": target_type,
            "会话标的": session_target,
            "最近对话": str(history_text or "")[-800:],
            "用户问题": content,
        },
        ensure_ascii=False,
    )
    text = _call_llm(llm, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    raw = _extract_json(text)
    if not isinstance(raw, dict):
        raise PipelineError("解析结果不是 JSON 对象")
    intent = str(raw.get("intent") or "other").strip().lower()
    if intent not in INTENTS:
        intent = "other"
    time_range = _normalize_time_range(raw.get("time_range"))
    target = _resolve_target(str(raw.get("target") or "").strip(), meta)
    return {"intent": intent, "target": target, "time_range": time_range}


def _parse_need_items(text: Any) -> Optional[list[dict]]:
    """解析分析师数据清单输出；失败返回 None（由调用方走默认清单）。"""
    try:
        obj = _extract_json(text)
    except Exception:  # noqa: BLE001
        return None
    items = obj.get("needs") if isinstance(obj, dict) else obj
    if not isinstance(items, list):
        return None
    out: list[dict] = []
    for it in items:
        if isinstance(it, str):
            key = str(it).strip()
            if key:
                out.append({"data_key": key, "reason": ""})
        elif isinstance(it, dict):
            key = str(it.get("data_key") or it.get("key") or "").strip()
            if key:
                out.append({"data_key": key, "reason": str(it.get("reason") or "")[:200]})
    return out or None


def _default_needs(intent: str, target_type: str) -> list[dict]:
    """分析师清单缺失/无效时的默认数据需求（按意图与标的类型）。"""
    if target_type == "sector":
        table = {
            "trend": [("index_trend", "大盘指数走势与均线"), ("sectors", "板块涨跌与资金"), ("breadth", "全市场涨跌家数")],
            "sentiment": [("sentiment", "情绪指标"), ("zt_pool", "涨停池"), ("breadth", "涨跌家数")],
            "buy_point": [("sectors", "板块强弱"), ("zt_pool", "涨停梯队"), ("sentiment", "情绪")],
            "risk": [("market_micro", "当日微观数据"), ("news", "财经资讯")],
            "compare": [("index_trend", "指数走势"), ("sectors", "板块排名"), ("news", "资讯")],
            "other": [("index_trend", "指数走势"), ("sectors", "板块"), ("news", "资讯")],
        }
    else:
        table = {
            "trend": [("stock_info", "个股近期走势与均线"), ("index_trend", "大盘指数走势"), ("stock_quote", "个股实时行情")],
            "sentiment": [("sentiment", "情绪指标"), ("zt_pool", "涨停池"), ("breadth", "涨跌家数")],
            "buy_point": [("stock_info", "个股走势与均线"), ("stock_quote", "个股实时行情"), ("zt_pool", "涨停梯队"), ("sectors", "板块强弱")],
            "risk": [("stock_info", "个股走势"), ("market_micro", "当日微观数据"), ("news", "财经资讯")],
            "compare": [("stock_info", "个股走势"), ("index_trend", "指数走势"), ("sectors", "板块排名"), ("news", "资讯")],
            "other": [("stock_info", "个股走势"), ("index_trend", "指数走势"), ("news", "资讯")],
        }
    return [{"data_key": k, "reason": r} for k, r in table.get(intent, table["other"])]


def _ask_need(analyst: dict, parse: dict, content: str, llm: Any) -> Optional[list[dict]]:
    """步骤 2：一位分析师基于解析结果输出数据清单；失败返回 None。"""
    name = analyst.get("name", analyst.get("id", "分析师"))
    whitelist = "、".join(DATA_KEY_WHITELIST)
    system = (
        f"{analyst.get('prompt', '')}\n\n"
        f"【本次任务】你是「{name}」的数据需求规划者。为回答用户问题，列出你需要的 A 股数据清单。\n"
        f"可用数据键（白名单，只能从中选择）：{whitelist}\n"
        "选择原则：优先核心数据键（stock_info/stock_quote/minute_kline/index_trend/"
        "sectors/breadth/sentiment/zt_pool）；竞价（auction_quote/auction）、龙虎榜（lhb）、"
        "资讯（news/news_headlines）等仅在问题明确涉及该维度时才请求；清单不超过 6 项。\n"
        "只输出 JSON 数组，不要任何解释：\n"
        '[{"data_key":"<白名单键>","reason":"一句话说明用途"}]'
    )
    user = json.dumps({"问题解析": parse, "用户问题": content}, ensure_ascii=False)
    text = _call_llm(llm, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    return _parse_need_items(text)


def _merge_needs(
    per_analyst: list[tuple[str, Optional[list[dict]]]],
    intent: str,
    target_type: str,
    degraded: list[str],
) -> list[dict]:
    """合并各分析师清单：白名单过滤 + 去重；无效清单回退默认。"""
    merged: list[dict] = []
    seen: set[str] = set()
    for analyst_id, items in per_analyst:
        used_default = False
        if not items:
            items = _default_needs(intent, target_type)
            used_default = True
        valid: list[dict] = []
        for it in items:
            key = str(it.get("data_key") or "")
            key = _ALIAS_TO_CANONICAL.get(key, key)
            if key not in DATA_KEY_WHITELIST:
                degraded.append(f"分析师 {analyst_id} 请求白名单外数据键「{key}」，已忽略")
                continue
            valid.append({"data_key": key, "reason": str(it.get("reason") or "")})
        if not valid:
            valid = _default_needs(intent, target_type)
            used_default = True
        if used_default:
            degraded.append(f"分析师 {analyst_id} 数据清单无效，已使用默认清单")
        for it in valid:
            key = str(it.get("data_key") or "")
            if key in seen:
                continue
            seen.add(key)
            merged.append({"data_key": key, "reason": str(it.get("reason") or "") or DATA_KEY_WHITELIST[key]})
    return merged


def _call_tools_func(module_name: str, func_name: str, **kwargs: Any) -> Any:
    """调用时按名解析 tools 函数（monkeypatch 友好），失败上抛由取数层降级。"""
    import importlib

    mod = importlib.import_module(f"stock_review_crew.tools.{module_name}")
    return getattr(mod, func_name)(**kwargs)


def _normalize_block(key: str, raw: Any) -> dict:
    """把 fetch_* 返回块归一为 {key, available, data, source, note}。
    失败判定：异常由调用方捕获；此处判定 source=数据缺失 / error 标记 / 空结果。"""
    if raw is None:
        return {"key": key, "available": False, "data": None, "source": None, "note": f"{key} 返回空数据"}
    if isinstance(raw, dict):
        if raw.get("source") == "数据缺失" or raw.get("error"):
            note = str(raw.get("note") or raw.get("error") or f"{key} 数据缺失")
            return {"key": key, "available": False, "data": None, "source": None, "note": note}
        return {"key": key, "available": True, "data": raw, "source": raw.get("source"), "note": raw.get("note")}
    try:
        obj = json.loads(str(raw))
        if isinstance(obj, dict):
            if obj.get("source") == "数据缺失" or obj.get("error"):
                note = str(obj.get("note") or obj.get("error") or f"{key} 数据缺失")
                return {"key": key, "available": False, "data": None, "source": None, "note": note}
            return {"key": key, "available": True, "data": obj, "source": obj.get("source"), "note": obj.get("note")}
    except (TypeError, ValueError):
        pass
    return {"key": key, "available": True, "data": str(raw)[:MAX_CONTEXT_CHARS], "source": None, "note": None}


def _normalize_injected(key: str, res: Any) -> dict:
    """归一注入的 data_fetcher / _fetch_target_data 结果（available 协议）。"""
    res = res or {}
    if not res.get("available"):
        return {"key": key, "available": False, "data": None, "source": None,
                "note": str(res.get("note") or "数据暂时不可用")}
    data = res.get("data")
    if isinstance(data, str):
        try:
            obj = json.loads(data)
            if isinstance(obj, dict) and (obj.get("source") == "数据缺失" or obj.get("error")):
                return {"key": key, "available": False, "data": None, "source": None,
                        "note": str(obj.get("note") or obj.get("error") or f"{key} 数据缺失")}
        except (TypeError, ValueError):
            pass
    elif isinstance(data, dict) and (data.get("source") == "数据缺失" or data.get("error")):
        return {"key": key, "available": False, "data": None, "source": None,
                "note": str(data.get("note") or data.get("error") or f"{key} 数据缺失")}
    return {"key": key, "available": True, "data": data, "source": res.get("source") or "注入数据",
            "note": res.get("note")}


def _news_enabled() -> bool:
    """资讯开关（config.news_enabled 惰性导入；异常按关闭处理，绝不阻塞）。"""
    try:
        from .config import news_enabled

        return news_enabled()
    except Exception:
        return False


def _fetch_data(key: str, parse: dict, meta: dict, data_fetcher: Optional[Callable[[str, str], dict]] = None) -> dict:
    """步骤 3 的单键取数（模块级，可整体 monkeypatch）。
    stock_info / market_micro 优先走注入的 data_fetcher（保留 I3 注入契约），
    其余键按 FETCH_DISPATCH 映射到 tools fetch_* 纯函数（自带降级链）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    target = str(parse.get("target") or meta.get("target") or "")
    days = _time_range_days(parse.get("time_range"))
    if key in ("stock_info", "market_micro"):
        target_type = "stock" if key == "stock_info" else meta.get("target_type", "sector")
        try:
            if data_fetcher is not None:
                res = data_fetcher(target_type, target)
            else:
                res = _fetch_target_data(target_type, target)
        except Exception as exc:  # noqa: BLE001
            return {"key": key, "available": False, "data": None, "source": None,
                    "note": f"{key} 取数失败：{exc}"}
        return _normalize_injected(key, res)
    spec = FETCH_DISPATCH.get(key)
    if spec is None:
        return {"key": key, "available": False, "data": None, "source": None, "note": f"未知数据键：{key}"}
    # 2026-08-12：资讯开关——关闭时 news 系键不取数，返回「资讯已关闭」
    if key in ("news", "news_headlines", "realtime_news") and not _news_enabled():
        return {"key": key, "available": False, "data": None, "source": None,
                "note": "资讯已关闭（NEWS_ENABLED=0）"}
    module_name, func_name, kw_builder = spec
    kwargs = kw_builder(parse, meta, today, days, target) if kw_builder else {}
    try:
        raw = _call_tools_func(module_name, func_name, **kwargs)
        return _normalize_block(key, raw)
    except Exception as exc:  # noqa: BLE001
        if key == "realtime_indices":  # 指数实时备用：东财 → 腾讯
            try:
                raw = _call_tools_func("realtime", "fetch_indices_tx")
                return _normalize_block(key, raw)
            except Exception as exc2:  # noqa: BLE001
                return {"key": key, "available": False, "data": None, "source": None,
                        "note": f"{key} 取数失败：{exc2}"}
        return {"key": key, "available": False, "data": None, "source": None,
                "note": f"{key} 取数失败：{exc}"}


def _trace_quality(stage: str, payload: dict) -> None:
    """质量 Agent 的 trace_chat 惰性钩子（可选模块）；不存在/抛错均静默跳过。"""
    try:
        from stock_review_crew import quality  # type: ignore

        fn = getattr(quality, "trace_chat", None)
        if fn is None:
            return
        try:
            fn(stage=stage, payload=payload)
        except TypeError:
            fn(stage, payload)
    except Exception:  # noqa: BLE001
        pass


@lru_cache(maxsize=1)
def _get_default_llm() -> Any:
    """默认 LLM（DeepSeek，超时 120s，CHAT_LLM_TIMEOUT 可覆盖）；不可用返回 None。"""
    try:
        from stock_review_crew.config import make_llm

        timeout = float(os.environ.get("CHAT_LLM_TIMEOUT", str(DEFAULT_LLM_TIMEOUT)))
        return make_llm(temperature=0.7, timeout=timeout, max_retries=1)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
# chroma chat_history 集合（复用 knowledge_store 客户端模式，不改其代码）
# ═══════════════════════════════════════════════════════════
def _get_chroma_client():
    """复用 knowledge_store 的客户端单例；测试可用环境变量 CHROMA_DB_DIR 隔离。"""
    try:
        import chromadb
        from chromadb.config import Settings

        env_dir = os.environ.get("CHROMA_DB_DIR")
        if not env_dir:
            from stock_review_crew import knowledge_store

            return knowledge_store._get_client()
        Path(env_dir).mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(path=env_dir, settings=Settings(anonymized_telemetry=False))
    except BaseException:  # chroma 可能 PanicException（BaseException），降级兜底
        return None


def _get_chat_history_collection():
    """chat_history 集合：优先 ONNX 嵌入，失败回退默认嵌入；全部失败返回 None。"""
    client = _get_chroma_client()
    if client is None:
        return None
    try:
        import chromadb

        embedding_function = chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2()
        return client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
            embedding_function=embedding_function,
        )
    except BaseException:
        try:
            return client.get_or_create_collection(name=CHROMA_COLLECTION)
        except BaseException:
            return None


def _chunk_text(text: str, size: int = CHROMA_CHUNK_SIZE, overlap: int = CHROMA_CHUNK_OVERLAP) -> list[str]:
    chunks = []
    i = 0
    while i < len(text):
        chunk = text[i : i + size]
        if chunk.strip():
            chunks.append(chunk)
        i += size - overlap
    return chunks


def save_chat_history(
    session_id: str,
    role: str,
    content: str,
    analyst: Optional[str] = None,
) -> bool:
    """把一条对话消息分块写入 chroma chat_history 集合；失败静默返回 False。"""
    try:
        col = _get_chat_history_collection()
        if col is None:
            return False
        chunks = _chunk_text(str(content or ""))
        if not chunks:
            return False
        docs, metas, ids = [], [], []
        for j, chunk in enumerate(chunks):
            docs.append(chunk)
            metas.append(
                {
                    "date": session_id[:10],
                    "session_id": session_id,
                    "role": role,
                    "analyst": analyst or "",
                }
            )
            ids.append(f"{session_id.replace('-', '')}_{role}_{j}_{uuid.uuid4().hex[:8]}")
        col.add(documents=docs, metadatas=metas, ids=ids)
        return True
    except BaseException:
        return False


def search_chat_history(query: str, n_results: int = 5) -> dict:
    """检索历史对话；返回 ``{"available", "note", "data"}``，data 为 JSON 字符串或 None。"""
    try:
        col = _get_chat_history_collection()
        if col is None:
            return {"available": False, "note": "历史对话检索不可用（chroma 未就绪）", "data": None}
        results = col.query(query_texts=[query], n_results=n_results)
        if not results or not results.get("documents") or not results["documents"][0]:
            return {
                "available": True,
                "note": None,
                "data": json.dumps({"query": query, "results": [], "note": "无相关历史对话"}, ensure_ascii=False),
            }
        docs = []
        for i, (doc, meta, dist) in enumerate(
            zip(results["documents"][0], results["metadatas"][0], results["distances"][0])
        ):
            docs.append(
                {
                    "rank": i + 1,
                    "session_id": (meta or {}).get("session_id", "?"),
                    "date": (meta or {}).get("date", "?"),
                    "role": (meta or {}).get("role", "?"),
                    "content": doc,
                    "score": round(1 - dist, 3) if dist else 0,
                }
            )
        return {
            "available": True,
            "note": None,
            "data": json.dumps({"query": query, "results": docs}, ensure_ascii=False),
        }
    except BaseException as e:
        return {"available": False, "note": f"历史对话检索不可用：{e}", "data": None}


# ═══════════════════════════════════════════════════════════
# 聊天引擎
# ═══════════════════════════════════════════════════════════
class ChatEngine:
    """聊天引擎：会话管理 + 上下文组装 + 交叉问答 + 免责声明强制。"""

    def __init__(
        self,
        llm: Any = None,
        storage_root: Optional[Any] = None,
        timeout: Optional[float] = None,
        history_enabled: bool = True,
        review_searcher: Optional[Callable[[str], str]] = None,
        data_fetcher: Optional[Callable[[str, str], dict]] = None,
        searcher: Optional[Callable[[str, Optional[str]], dict]] = None,
    ):
        self.llm = llm
        self.storage_root = storage_root
        self.timeout = float(timeout) if timeout is not None else float(
            os.environ.get("CHAT_LLM_TIMEOUT", str(DEFAULT_LLM_TIMEOUT))
        )
        self.history_enabled = history_enabled
        self.review_searcher = review_searcher
        self.data_fetcher = data_fetcher
        # R11 R9：联网搜索函数（query, code）-> {ok, results, summary}；默认走 tools.search.search
        self.searcher = searcher

    # ── 会话管理 ──
    def create_session(
        self,
        target_type: str,
        target: str,
        analysts: list[str],
        title: Optional[str] = None,
    ) -> dict:
        if target_type not in ("stock", "sector"):
            raise ValueError("target_type 仅支持 stock 或 sector")
        if not target or not str(target).strip():
            raise ValueError("target 不能为空")
        if not analysts:
            raise ValueError("至少选择一位分析师")
        whitelist = get_analyst_whitelist()
        unknown = [a for a in analysts if a not in whitelist]
        if unknown:
            raise ValueError(f"分析师不在白名单中：{unknown}；可选：{whitelist}")
        analysts = list(dict.fromkeys(analysts))
        meta = chat_storage.create_session(
            target_type, str(target).strip(), analysts, title=title, root=self.storage_root
        )
        return {"session_id": meta["session_id"]}

    def list_sessions(self, target: Optional[str] = None, date: Optional[str] = None) -> list[dict]:
        return chat_storage.list_sessions(target=target, date=date, root=self.storage_root)

    def get_session(self, session_id: str) -> Optional[dict]:
        session = chat_storage.get_session(session_id, root=self.storage_root)
        if session is None:
            return None
        # R11 R10：meta 动态附加 pending_round（最后一条是 user 且其后无 assistant →
        # 在途/未完成落盘；完成/落盘后自然为 False），供前端恢复轮询判定。
        meta = dict(session["meta"])
        meta["pending_round"] = _compute_pending_round(session["messages"])
        return {"meta": meta, "messages": session["messages"], "disclaimer": DISCLAIMER}

    def delete_session(self, session_id: str) -> bool:
        return chat_storage.delete_session(session_id, root=self.storage_root)

    # ── 发送消息 ──
    def send_message(self, session_id: str, content: str) -> Optional[dict]:
        """追加用户消息 → R8 六步流水线生成回复（含免责声明）→ 落盘。

        会话不存在返回 None；解析失败/取数失败时回复「数据缺失说明 + 免责声明」，
        绝不抛异常（LLM 异常同样降级）；worker 异常兜底落盘「回复中断，可点击重试」
        （R11 R10），保证会话最后消息不再是 user。
        """
        content = str(content or "").strip()
        if not content:
            raise ValueError("消息内容不能为空")
        try:
            return self._run_pipeline(session_id, content, emit=None)
        except Exception:  # noqa: BLE001 - 兜底落盘说明消息，绝不向上抛
            return self._interrupt_fallback(session_id, content)

    def stream_message(self, session_id: str, content: str, emit) -> Optional[dict]:
        """流式版 send_message：emit(event, payload) 逐步推送事件；返回与 send_message 相同的字典。

        事件序列（R8 v4 §五；R11 R9 搜索不产生 SSE 事件）：meta → user_msg
                  → stage{parse} →（解析失败：error → done）
                  → stage{need} → stage{fetch} →（取数失败：error → done）
                  →（每分析师）analyst_start → analyst_delta* → analyst_end
                  → stage{summarize} → summary_start → summary_delta* → summary_end
                  → done（最终消息字典）。
        stage 事件只携带当前阶段提示（不含数据清单内容，v4 Q7）。
        worker 异常兜底落盘「回复中断，可点击重试」并 emit error → done（R11 R10）。
        """
        content = str(content or "").strip()
        if not content:
            raise ValueError("消息内容不能为空")
        try:
            return self._run_pipeline(session_id, content, emit=emit)
        except Exception:  # noqa: BLE001 - 兜底落盘说明消息，绝不向上抛
            result = self._interrupt_fallback(session_id, content)
            if result is None:
                return None
            try:
                emit("error", {"message": "回复中断，可点击重试"})
                emit("done", result)
            except Exception:  # noqa: BLE001 - emit 失败不影响落盘结果
                pass
            return result

    # ── R8 六步流水线 ──
    def _run_pipeline(self, session_id: str, content: str, emit: Optional[Callable[[str, dict], None]]) -> Optional[dict]:
        """1 parse → 2 need → 3 fetch → 4 analyze → 5 summarize → 6 组装流式输出。"""
        session = chat_storage.get_session(session_id, root=self.storage_root)
        if session is None:
            return None
        meta = session["meta"]
        degraded: list[str] = []

        def _emit(ev: str, payload: dict) -> None:
            if emit is not None:
                emit(ev, payload)

        _emit("meta", {"session_id": session_id})

        user_msg = {"role": "user", "content": content, "created_at": _now_iso()}
        try:
            chat_storage.append_message(session_id, user_msg, root=self.storage_root)
        except Exception as e:
            degraded.append(f"消息持久化失败：{e}")
        _emit("user_msg", user_msg)

        llm = self.llm if self.llm is not None else _get_default_llm()
        if llm is None:
            degraded.append("LLM 未配置或无可用 Key")

        history_text = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '分析师'}：{str(m.get('content') or '')[:300]}"
            for m in session["messages"][-HISTORY_WINDOW:]
        )

        # 1) 问题解析（一个 LLM 调用；失败/标的不明 → 取数前终止）
        _trace_quality("parse", {"session_id": session_id, "stage": "parse", "note": "问题解析"})
        _emit("stage", {"stage": "parse", "note": "正在解析问题…"})
        try:
            parse = _parse_question(llm, meta, content, history_text)
        except Exception as exc:  # noqa: BLE001 - 解析失败一律终止本轮
            # 2026-08-07：401（Invalid API key）是凭据问题，透传原文用户看不懂——
            # 改为中文指引；其余错误保留原样以便排查。
            exc_text = str(exc)
            if "401" in exc_text and ("Invalid API key" in exc_text or "AuthError" in exc_text):
                msg = (
                    "问题解析失败：LLM 凭据无效或已过期（401 Invalid API key）。"
                    "请在 .env 更新 DEEPSEEK_API_KEY 后重启服务（双击 启动服务.bat）"
                )
            else:
                msg = f"问题解析失败：{exc}"
            degraded.append(msg)
            return self._finish_abort(
                session_id, user_msg, session, [msg], degraded,
                emit=emit, parse=None, needs=[],
            )

        # 1.5) 联网搜索 → 整理简报（R11 R9；搜索失败不阻塞，简报标注「（未联网搜索）」）
        # 注：不发 stage 事件——后端 SSE 事件契约（v4 §五）不含 search，保持兼容
        _trace_quality("search", {"session_id": session_id, "stage": "search", "note": "联网搜索"})
        search_brief = self._search_and_brief(parse, content, llm, degraded)

        # 2) 数据需求（每位选中分析师各出一个白名单清单）
        _trace_quality("need", {"session_id": session_id, "stage": "need", "note": "数据需求确认", "parse": parse})
        _emit("stage", {"stage": "need", "note": "分析师正在确认所需数据…"})
        per_analyst: list[tuple[str, Optional[list[dict]]]] = []
        for analyst_id in meta["analysts"]:
            analyst = load_analyst(analyst_id)
            if analyst is None:
                per_analyst.append((analyst_id, None))
                degraded.append(f"分析师 {analyst_id} 人格缺失")
                continue
            try:
                items = _ask_need(analyst, parse, content, llm)
            except Exception as exc:  # noqa: BLE001
                items = None
                degraded.append(f"分析师 {analyst_id} 数据清单生成失败：{exc}")
            per_analyst.append((analyst_id, items))
        needs = _merge_needs(per_analyst, parse.get("intent"), meta.get("target_type", "stock"), degraded)
        if not needs:  # 防御：无任何需求时给默认清单
            needs = _default_needs(parse.get("intent"), meta.get("target_type", "stock"))

        # 3) 取数（按清单执行；任一关键数据失败 → 本轮不回答）
        _trace_quality("fetch", {"session_id": session_id, "stage": "fetch", "note": "取数", "needs": needs})
        _emit("stage", {"stage": "fetch", "note": "正在获取数据…"})
        fetch_result = self._fetch_needs(needs, parse, meta)
        if fetch_result["missing"]:
            missing_msgs = [
                f"{m['data_key']}（{m['reason'] or '必需数据'}）：{m['note']}"
                for m in fetch_result["missing"]
            ]
            degraded.extend(fetch_result["issues"])
            return self._finish_abort(
                session_id, user_msg, session, missing_msgs, degraded,
                emit=emit, parse=parse, needs=needs,
            )
        degraded.extend(fetch_result["issues"])

        # 4) 分析（每位分析师基于真实数据回答）
        context, ctx_issues = self._build_context(
            meta, parse, needs, fetch_result["fetched"], content, search_brief=search_brief
        )
        degraded.extend(ctx_issues)
        segments = []
        for i, analyst_id in enumerate(meta["analysts"]):
            analyst = load_analyst(analyst_id)
            if analyst is None:
                text = f"（分析师 {analyst_id} 人格信息缺失，暂无法回答）"
                segments.append({"id": analyst_id, "name": analyst_id, "content": text})
                degraded.append(f"分析师 {analyst_id} 人格缺失")
                continue
            name = analyst.get("name", analyst_id)
            _emit("analyst_start", {"index": i, "analyst_id": analyst_id, "name": name})
            try:
                text = self._ask_analyst(
                    analyst, i, parse, context, session["messages"], llm,
                    on_delta=lambda delta, idx=i, nm=name: (
                        _emit("analyst_delta", {"index": idx, "analyst_id": analyst_id, "name": nm, "delta": delta})
                    ),
                )
                segments.append({"id": analyst_id, "name": name, "content": text})
            except Exception as e:
                text = f"（{name}：分析服务暂时不可用，请稍后重试）"
                segments.append({"id": analyst_id, "name": name, "content": text})
                degraded.append(f"分析师 {analyst_id} 生成失败：{e}")
                _emit("analyst_delta", {"index": i, "analyst_id": analyst_id, "name": name, "delta": text})
            _emit("analyst_end", {"index": i, "analyst_id": analyst_id, "name": name, "content": text})

        # 5) 综合总结（一个 LLM，流式输出）
        _trace_quality("summarize", {"session_id": session_id, "stage": "summarize", "note": "综合总结"})
        _emit("stage", {"stage": "summarize", "note": "正在综合总结…"})
        _emit("summary_start", {})
        summary = self._make_summary(
            content, parse, segments, llm, degraded,
            on_delta=lambda delta: _emit("summary_delta", {"delta": delta}),
        )
        _emit("summary_end", {"content": summary})

        # 6) 组装：逐位片段 + 汇总 + 降级说明 + 免责声明（程序级强制）
        body = "\n\n".join(f"{s['name']}：\n{s['content']}" for s in segments)
        body += f"\n\n综合来看：\n{summary}"
        if degraded:
            body += "\n\n（数据降级说明：" + "；".join(dict.fromkeys(degraded)) + "）"
        body += f"\n\n{DISCLAIMER}"

        assistant_msg = {
            "role": "assistant",
            "content": body,
            "analysts": segments,
            "summary": summary,
            "degraded": degraded,
            "parse": parse,
            "needs": needs,
            "stage": "done",
            "created_at": _now_iso(),
        }
        try:
            chat_storage.append_message(session_id, assistant_msg, root=self.storage_root)
        except Exception as e:
            degraded.append(f"消息持久化失败：{e}")
            assistant_msg["degraded"] = degraded

        if self.history_enabled:
            save_chat_history(session_id, "user", content)
            save_chat_history(session_id, "assistant", body, analyst="all")

        # 以内存消息为准：持久化失败时仍返回完整对话，磁盘问题不阻塞主流程
        messages = session["messages"] + [user_msg, assistant_msg]
        result = {"session_id": session_id, "messages": messages, "disclaimer": DISCLAIMER}
        _trace_quality("done", {"session_id": session_id, "stage": "done", "note": "完成"})
        _emit("done", result)
        return result

    # ── 上下文组装 ──
    def _build_context(
        self,
        meta: dict,
        parse: dict,
        needs: list[dict],
        fetched: dict,
        user_content: str,
        search_brief: Optional[str] = None,
    ):
        """返回 (上下文文本, 降级说明列表)。解析结果/数据块/检索结果全部可见。"""
        issues: list[str] = []
        target_type, target = meta.get("target_type", ""), meta.get("target", "")
        lines = [
            f"标的类型：{'个股' if target_type == 'stock' else '板块'}（{target_type}）",
            f"标的：{target}",
        ]
        if meta.get("title"):
            lines.append(f"会话标题：{meta['title']}")

        lines.append(
            f"问题解析：意图={parse.get('intent')}，标的={parse.get('target')}，"
            f"时间范围={parse.get('time_range')}"
        )
        # R11 R9：联网检索简报与 market_data 并列注入（标题固定「## 联网检索简报」）
        if search_brief:
            lines.append("\n## 联网检索简报\n" + str(search_brief)[:800])
        lines.append("\n## 数据清单与取数结果")
        for need in needs:
            key = need["data_key"]
            block = fetched.get(key)
            if block is None or not block.get("available"):
                lines.append(f"- {key}（{need.get('reason', '')}）：数据缺失")
                continue
            data = block.get("data")
            if isinstance(data, str):
                # 2026-08-06 修复：stock_info 的完整 JSON 中 recent5（近5日日K）位于
                # 尾部，被 1600 字符截断切掉 → 分析师报「近5日日K缺失」；特化紧凑展示，
                # 优先保留 收盘/均线/近5日日K。
                if key == "stock_info":
                    try:
                        obj = json.loads(data)
                        if isinstance(obj, dict):
                            compact = {k: obj.get(k) for k in (
                                "code", "close", "pre_close", "pct_change",
                                "ma5", "ma10", "recent5", "source",
                            )}
                            lines.append(
                                f"- {key}（{need.get('reason', '')}）："
                                f"{json.dumps(compact, ensure_ascii=False)[:MAX_CONTEXT_CHARS]}"
                            )
                            continue
                    except (TypeError, ValueError):
                        pass
                # 2026-08-12：LLM 输入脱敏——JSON 字符串去股票名称（只留代码）
                from .llm_sanitize import sanitize_json_text

                safe = sanitize_json_text(data)
                lines.append(f"- {key}（{need.get('reason', '')}）：{safe[:MAX_CONTEXT_CHARS]}")
            else:
                # 2026-08-12：LLM 输入脱敏——dict 数据去股票名称（只留代码）
                from .llm_sanitize import sanitize_for_llm

                safe_data = sanitize_for_llm(data)
                lines.append(
                    f"- {key}（{need.get('reason', '')}）："
                    f"{json.dumps(safe_data, ensure_ascii=False)[:MAX_CONTEXT_CHARS]}"
                )

        review = self._search_review(user_content)
        if review.get("available"):
            lines.append("\n## 相关复盘结论\n" + str(review.get("data") or "")[:MAX_CONTEXT_CHARS])
        else:
            note = str(review.get("note") or "复盘检索不可用")
            lines.append(f"\n## 相关复盘结论\n（{note}）")
            issues.append(note)

        if self.history_enabled:
            chat_h = search_chat_history(user_content)
        else:
            chat_h = {"available": False, "note": "历史对话检索未启用", "data": None}
        if chat_h.get("available"):
            lines.append("\n## 相关历史对话\n" + str(chat_h.get("data") or "")[:MAX_CONTEXT_CHARS])
        else:
            note = str(chat_h.get("note") or "历史对话检索不可用")
            lines.append(f"\n## 相关历史对话\n（{note}）")
            if self.history_enabled:  # 主动关闭检索属于配置，不属于降级
                issues.append(note)

        lines.append("\n## 当前问题\n" + user_content)
        return "\n".join(lines), issues

    # ── 终止路径：数据缺失说明 + 免责声明，无分析内容 ──
    def _finish_abort(
        self,
        session_id: str,
        user_msg: dict,
        session: dict,
        missing_items: list[str],
        degraded: list[str],
        emit: Optional[Callable[[str, dict], None]] = None,
        parse: Optional[dict] = None,
        needs: Optional[list[dict]] = None,
    ) -> dict:
        """解析失败/取数失败时落盘「数据缺失说明 + 免责声明」；流式路径发 error → done。"""

        def _emit(ev: str, payload: dict) -> None:
            if emit is not None:
                emit(ev, payload)

        if parse is None:
            parse = {"intent": "other", "target": None, "time_range": None}
        needs = needs or []
        if not degraded:
            degraded.append("本轮因解析/取数失败终止，未生成分析内容")
        body = self._build_abort_body(missing_items, degraded)
        assistant_msg = {
            "role": "assistant",
            "content": body,
            "analysts": [],
            "summary": "",
            "degraded": degraded,
            "parse": parse,
            "needs": needs,
            "stage": "error",
            "created_at": _now_iso(),
        }
        try:
            chat_storage.append_message(session_id, assistant_msg, root=self.storage_root)
        except Exception as e:
            degraded.append(f"消息持久化失败：{e}")
            assistant_msg["degraded"] = degraded
        if self.history_enabled:
            save_chat_history(session_id, "user", str(user_msg.get("content") or ""))
            save_chat_history(session_id, "assistant", body, analyst="all")
        messages = session["messages"] + [user_msg, assistant_msg]
        result = {"session_id": session_id, "messages": messages, "disclaimer": DISCLAIMER}
        _trace_quality("error", {"session_id": session_id, "stage": "error", "note": "；".join(missing_items)})
        _emit("error", {"message": "；".join(missing_items)})
        _emit("done", result)
        return result

    def _build_abort_body(self, missing_items: list[str], degraded: list[str]) -> str:
        lines = ["本轮未能回答：数据缺失说明。", "", "缺失项："]
        lines += [f"- {item}" for item in missing_items]
        if degraded:
            lines += ["", "（数据降级说明：" + "；".join(dict.fromkeys(degraded)) + "）"]
        lines += ["", DISCLAIMER]
        return "\n".join(lines)

    # ── R11 R9 联网搜索 → 整理简报 ──
    def _search_and_brief(
        self,
        parse: dict,
        content: str,
        llm: Any,
        degraded: list[str],
    ) -> str:
        """联网搜索 → LLM 整理 ≤200 字结构化简报（事件/影响方向/相关板块与个股）。

        - 代码优先公告（parse 已归一化的 6 位代码），其余按查询关键词过滤新浪 7x24 快讯；
        - 搜索失败/无结果 → 返回「（未联网搜索）」（简报自身已标注，不再进降级说明），
          绝不阻塞流程；搜索抛异常 → 进降级说明并返回「（未联网搜索）」；
        - LLM 整理失败 → 降级返回原始结果拼接（有结果不丢弃，铁律 7 绝不静默错报）。
        """
        try:
            if self.searcher is not None:
                result = self.searcher(content, str(parse.get("target") or "") if parse.get("target") else None)
            else:
                from .tools import search as search_tools

                code = str(parse.get("target") or "") if str(parse.get("target") or "").isdigit() else None
                result = search_tools.search(content, code=code)
        except Exception as exc:  # noqa: BLE001 - 搜索异常降级
            degraded.append(f"联网搜索失败：{exc}")
            return "（未联网搜索）"
        if not isinstance(result, dict) or not result.get("ok") or not result.get("results"):
            return "（未联网搜索）"
        results = result["results"]
        if not results:
            return "（未联网搜索）"
        return self._make_search_brief(content, results, llm, degraded)

    def _make_search_brief(
        self,
        query: str,
        results: list[dict],
        llm: Any,
        degraded: list[str],
    ) -> str:
        """一个 LLM 调用把搜索结果整理为 ≤200 字结构化简报；失败降级为原始结果拼接。"""
        payload = {
            "query": query,
            "results": [
                {
                    "source": r.get("source"),
                    "title": r.get("title"),
                    "date": r.get("date"),
                    "summary": r.get("summary"),
                }
                for r in results[:10]
            ],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 A 股资讯整理助手。把搜索结果整理成 ≤200 字结构化简报，格式严格为：\n"
                    "事件：<一句话>\n"
                    "影响方向：<利好/利空/中性，一句话>\n"
                    "相关板块与个股：<名称列表，无则写无>\n"
                    "只输出简报正文，禁止编造结果中不存在的信息。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            text = _call_llm(llm, messages).strip()
            return text[:400] if text else "（未联网搜索）"
        except Exception as exc:  # noqa: BLE001 - LLM 整理失败不阻塞
            degraded.append(f"联网搜索简报生成失败：{exc}")
            titles = "；".join(
                f"[{r.get('source', '')}]{r.get('title', '')}" for r in results[:5]
            )
            return f"（搜索摘要生成失败，原始结果：{titles[:180]}）"

    # ── R11 R10 worker 异常兜底落盘 ──
    def _interrupt_fallback(self, session_id: str, content: str) -> Optional[dict]:
        """SSE/worker 异常时落盘一条中文说明消息，保证会话最后消息不再是 user。

        内容固定为「回复中断，可点击重试」；会话不存在返回 None（与主流程一致）。
        """
        session = chat_storage.get_session(session_id, root=self.storage_root)
        if session is None:
            return None
        body = "回复中断，可点击重试。\n\n" + DISCLAIMER
        assistant_msg = {
            "role": "assistant",
            "content": body,
            "analysts": [],
            "summary": "",
            "degraded": ["回复中断：worker 异常，已落盘说明消息"],
            "parse": {"intent": "other", "target": None, "time_range": None},
            "needs": [],
            "stage": "interrupted",
            "created_at": _now_iso(),
        }
        try:
            chat_storage.append_message(session_id, assistant_msg, root=self.storage_root)
        except Exception:  # noqa: BLE001 - 落盘失败不再抛
            pass
        messages = session["messages"] + [assistant_msg]
        return {"session_id": session_id, "messages": messages, "disclaimer": DISCLAIMER}

    def _search_review(self, query: str) -> dict:
        """chroma 相关复盘结论（knowledge_store.search 只读调用）。"""
        if self.review_searcher is not None:
            try:
                text = str(self.review_searcher(query) or "")
                return {"available": True, "note": None, "data": text}
            except Exception as e:
                return {"available": False, "note": f"复盘检索不可用：{e}", "data": None}
        try:
            from stock_review_crew import knowledge_store

            text = str(knowledge_store.search(query, n_results=5) or "")
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("error"):
                return {"available": False, "note": f"复盘检索不可用：{parsed['error']}", "data": None}
            if isinstance(parsed, dict) and not parsed.get("results"):
                return {"available": True, "note": None, "data": "无相关复盘记录"}
            return {"available": True, "note": None, "data": text}
        except BaseException as e:
            return {"available": False, "note": f"复盘检索不可用：{e}", "data": None}

    # ── 取数 ──
    def _fetch_needs(self, needs: list[dict], parse: dict, meta: dict) -> dict:
        """按需求清单取数（去重）；返回 {fetched, missing, issues}。

        终止规则（v4 Q5 细化，2026-08-04 稳定性修复）：
        - 任一**关键数据**（CRITICAL_KEYS）失败，或全部请求数据均失败 → missing 非空，
          本轮不回答（由调用方终止）；
        - 仅非关键数据（竞价/龙虎榜/资讯/模式聚合等）失败 → 继续回答，
          失败项进入 issues（数据降级说明），分析师上下文会标注该键「数据缺失」。
        """
        fetched: dict[str, dict] = {}
        failed: list[dict] = []
        issues: list[str] = []
        seen: set[str] = set()
        for need in needs:
            key = _ALIAS_TO_CANONICAL.get(need["data_key"], need["data_key"])
            if key in seen or key in fetched:
                continue
            seen.add(key)
            try:
                block = _fetch_data(key, parse, meta, self.data_fetcher)
            except Exception as exc:  # noqa: BLE001 - 单键失败不阻断其余键
                block = {"key": key, "available": False, "data": None, "source": None,
                         "note": f"{key} 取数失败：{exc}"}
            if not block.get("available"):
                failed.append({
                    "data_key": key,
                    "reason": need.get("reason", ""),
                    "note": block.get("note") or "数据缺失",
                    "critical": key in CRITICAL_KEYS,
                })
                continue
            fetched[key] = block
            data = block.get("data")
            if isinstance(data, dict):
                for reason in data.get("degraded_reason") or []:
                    issues.append(f"{key}：{reason}")
        critical_failed = [f for f in failed if f["critical"]]
        if critical_failed or not fetched:
            # 终止路径：列出全部缺失项（含非关键），如实告知
            return {"fetched": fetched, "missing": failed, "issues": issues}
        # 继续路径：非关键缺失降级标注，不阻断回答
        for f in failed:
            issues.append(f"{f['data_key']}：{f['note']}")
        return {"fetched": fetched, "missing": [], "issues": issues}

    # ── 分析师回答 ──
    def _ask_analyst(
        self,
        analyst: dict,
        index: int,
        parse: dict,
        context: str,
        history_messages: list[dict],
        llm: Any,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> str:
        name = analyst.get("name", analyst.get("id", "分析师"))
        system = (
            f"{analyst.get('prompt', '')}\n\n"
            f"【本次任务】你是「{name}」，正在与用户就标的进行专业聊天。"
            f"问题解析：意图={parse.get('intent')}，标的={parse.get('target')}，"
            f"时间范围={parse.get('time_range')}。回答必须围绕该标的与时间范围。"
            "请基于下方【上下文】中的真实数据回答问题：禁止编造任何数据；"
            "数据缺失或降级时必须在回答中明确说明缺失/降级项。"
            "输出要求：给出精炼分析简报（150-250 字），先给结论，再给 1-2 条关键依据，"
            "数据缺失/降级用一句话说明；用自然口语化中文，不要使用任何 Markdown 标记"
            "（不要 **、#、- 列表、表格）。"
        )
        messages: list[dict] = [{"role": "system", "content": system}]
        # Q4：历史消息以 user/assistant 角色原样入上下文（多轮追问上下文保留）
        for m in history_messages[-HISTORY_WINDOW:]:
            role = "user" if m.get("role") == "user" else "assistant"
            messages.append({"role": role, "content": str(m.get("content") or "")[:800]})
        messages.append({"role": "user", "content": context})
        if on_delta is not None:
            return _stream_llm(llm, messages, on_delta)
        return _call_llm(llm, messages)

    def _make_summary(
        self,
        user_content: str,
        parse: dict,
        segments: list[dict],
        llm: Any,
        degraded: list[str],
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> str:
        """一个 LLM 综合各位分析师结论输出最终总结；失败降级为中文说明。"""
        if not segments:
            degraded.append("汇总生成失败：无分析师观点")
            return "（无分析师观点）"
        if llm is None:
            degraded.append("汇总生成失败：LLM 未配置")
            return "（分析服务暂不可用，未能生成汇总）"
        payload = {
            "user": user_content,
            "parse": parse,
            "analysts": [{"name": s["name"], "content": s["content"]} for s in segments],
        }
        messages = [
            {
                "role": "system",
                "content": "你是多分析师聊天的汇总角色。请用中文综合各位分析师的回答："
                           "共同观点、分歧点、以及给用户的可执行结论（300 字以内）。"
                           "口语化输出，不要使用任何 Markdown 标记（不要 **、#、列表）。",
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            if on_delta is not None:
                return _stream_llm(llm, messages, on_delta)
            return _call_llm(llm, messages)
        except Exception as e:
            degraded.append(f"汇总生成失败：{e}")
            return "（以上为各位分析师的逐位回答，汇总生成失败——服务降级）"


# ═══════════════════════════════════════════════════════════
# 模块级便捷入口（供 I5 后端集成）
# ═══════════════════════════════════════════════════════════
_default_engine: Optional[ChatEngine] = None


def _get_default_engine() -> ChatEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = ChatEngine()
    return _default_engine


def reset_default_engine() -> None:
    """重置默认引擎（测试用）。"""
    global _default_engine
    _default_engine = None
    # 测试会 monkeypatch 掉 _get_default_llm（替换为无 cache_clear 的普通函数），此处守卫跳过
    if hasattr(_get_default_llm, "cache_clear"):
        _get_default_llm.cache_clear()


def create_session(target_type: str, target: str, analysts: list[str], title: Optional[str] = None) -> dict:
    return _get_default_engine().create_session(target_type, target, analysts, title=title)


def send_message(session_id: str, content: str) -> Optional[dict]:
    return _get_default_engine().send_message(session_id, content)


def list_sessions(target: Optional[str] = None, date: Optional[str] = None) -> list[dict]:
    return _get_default_engine().list_sessions(target=target, date=date)


def get_session(session_id: str) -> Optional[dict]:
    return _get_default_engine().get_session(session_id)


def delete_session(session_id: str) -> bool:
    return _get_default_engine().delete_session(session_id)


__all__ = [
    "ChatEngine",
    "DISCLAIMER",
    "INTENTS",
    "TIME_RANGES",
    "DATA_KEY_WHITELIST",
    "FETCH_DISPATCH",
    "PipelineError",
    "search_stocks",
    "create_session",
    "send_message",
    "list_sessions",
    "get_session",
    "delete_session",
    "get_analyst_whitelist",
    "load_analyst",
    "save_chat_history",
    "search_chat_history",
    "_compute_pending_round",
    "reset_default_engine",
]
