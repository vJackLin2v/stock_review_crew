"""6 模式感知复盘引擎：LangGraph 流水线 + 进度钩子 + 无 Key 规则引擎降级（I2）。

流水线：fetch（按 mode 分发取数）→ news → analysts（5 位一次并行：趋势派 2 + 情绪派 3）
→ host → debate（循环 ≤max_rounds）→ report。

- 取数优先调用 I1 纯函数接口 fetch_mode_data(mode, date, ...) -> dict；
  未落地/失败时防御性 import + try/except 降级（旧工具兜底 → 明确标注缺失），绝不阻塞；
- LLM 无 Key/失败 → 确定性规则引擎，degraded=True 可见标注；
- 免责声明在组装层强制追加（ensure_disclaimer）；
- 进度钩子 _emit(stage, pct, message)：pct 单调递增；
  阶段：fetch / news / trend n/2 / sentiment n/3 / host / debate / report / done。
"""

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from langgraph.graph import END, StateGraph


class DataValidationError(RuntimeError):
    """取数校验失败：核心数据缺失，复盘终止（中文消息）。"""

from . import prompts
from .llm_runtime import (
    _INVOKE_TIMEOUT,
    llm_ready as _llm_ready,
    llm_pair as _llm_pair,
    callbacks as _callbacks,
    invoke_llm as _invoke_llm,
    ensure_disclaimer,
    parse_host_output as _parse_host,
)
from .prompts import DEFAULT_MODE, DISCLAIMER, MODE_INFO
from .skills import list_skills
from .state import ReviewState

__all__ = [
    "build_graph",
    "build_result",
    "default_initial_state",
    "set_progress_callback",
    "ensure_disclaimer",
    "_emit",
]


# ═══════════════════════════════════════════════════════════════
# 进度钩子（阶段序列：fetch/news/trend n/2/sentiment n/3/host/debate/report/done）
# ═══════════════════════════════════════════════════════════════

# 2026-08-10：进度钩子改 ContextVar（每 job 独立上下文，多任务并发不互相覆盖；
# 此前模块全局 + _ENGINE_LOCK 串行化，批跑只能一天一天排队）
_progress_var: Any = None
_last_pct_var: Any = None
_progress_lock: Any = None


def _progress_context() -> None:
    global _progress_var, _last_pct_var, _progress_lock
    import contextvars
    import threading

    if _progress_var is None:
        _progress_var = contextvars.ContextVar("review_progress", default=None)
        # 2026-08-12：last_pct 存共享 dict——并行分析师线程 copy_context 后引用同一
        # dict，配合 _progress_lock 做跨线程单调 clamp；此前各线程持独立副本，
        # 完成顺序不同时进度会倒挂（实测 sentiment 40→35，test_progress_stages_monotonic 偶发失败）
        _last_pct_var = contextvars.ContextVar("review_last_pct", default={"last": 0.0})
        _progress_lock = threading.Lock()


def set_progress_callback(callback):
    """注册进度回调 callback(stage, pct, message)；None 表示关闭。"""
    _progress_context()
    _progress_var.set(callback)
    _last_pct_var.set({"last": 0.0})


def _reset_progress():
    _progress_context()
    _last_pct_var.set({"last": 0.0})


def _emit(stage, pct, message):
    """进度钩子：pct 单调递增（clamp 到上一次值），回调异常不影响主流程。

    2026-08-12：clamp 状态为 job 内共享 dict + 锁——并行分析师线程各自 copy_context
    后仍读写同一状态，跨线程单调（此前各线程独立副本可倒挂）。"""
    try:
        _progress_context()
        pct = float(pct)
        state = _last_pct_var.get()
        with _progress_lock:
            if pct < state["last"]:
                pct = state["last"]
            state["last"] = pct
        callback = _progress_var.get()
        if callback is not None:
            callback(stage, pct, message)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# LLM 防御性访问（无 Key/未安装 → 规则引擎降级）
# ═══════════════════════════════════════════════════════════════

def _load_analyst_tools():
    """分析师可用工具（I1 接口）：未落地/不可用返回 (None, None)，不阻塞。"""
    try:
        from .tools.stock_data import get_stock_info, search_history
        return get_stock_info, search_history
    except Exception:
        return None, None


# ═══════════════════════════════════════════════════════════════
# Node1: 模式化取数（fetch 按 mode 分发）
# ═══════════════════════════════════════════════════════════════


def _make_account_tools(skill_id: str):
    """R12 分析师账户辅助工具：
    - get_positions：查询当前持仓（含可卖/T+1 冻结/成本），防止卖出幻觉（如只买 2 成却卖 3 成）
    2026-08-12（用户确认）：calc_qty / place_order 工具已移除——分析师只按冻结格式输出
    推荐行（`买入 代码：买入价XX.XX元，仓位X成`），股数在模拟账户撮合时按成数折算，
    计划由程序解析分析师文本自动记录（正则 regex_*），不再依赖 LLM 调用下单类工具。
    """
    from .accounts import storage as acct_storage

    def get_positions() -> str:
        acct = acct_storage.load_account(skill_id)
        if acct is None:
            return json.dumps({"ok": True, "cash": 100000.0, "positions": []},
                              ensure_ascii=False)
        acct["trades"] = acct_storage.load_trades(skill_id)
        rows = []
        for code, pos in (acct.get("positions") or {}).items():
            total = int(pos.get("qty") or 0)
            frozen = int(pos.get("frozen_qty") or 0)
            rows.append({
                "code": code,
                "name": pos.get("name") or code,
                "qty": total,
                "available": total - frozen,
                "frozen": frozen,
                "avg_cost": pos.get("avg_cost"),
            })
        return json.dumps({
            "ok": True,
            "cash": round(acct.get("cash", 0.0), 2),
            "positions": rows,
        }, ensure_ascii=False)

    get_positions.__name__ = "get_positions"
    get_positions.__doc__ = (
        "查询当前模拟账户持仓：返回现金、每只持仓的代码/名称/数量/可卖数量"
        "（T+1 冻结）/成本价。**卖出前必须先调用本工具确认可卖数量**，"
        "严禁卖出超过可卖数量的仓位。返回 JSON 字符串。"
    )
    return [get_positions]


def _load_pure_fetcher():
    """I1 纯函数接口（约定 fetch_mode_data(mode, date, ...) -> dict）；未落地返回 None。"""
    try:
        from .tools.stock_data import fetch_mode_data
        if callable(fetch_mode_data):
            return fetch_mode_data
    except Exception:
        pass
    return None


_LEGACY_FETCH_TIMEOUT = 20.0


def _legacy_fetch(state):
    """旧 LangChain 工具兜底：防御性 import + 20s 总预算（daemon 线程），超时放弃不阻塞。"""
    result = {}

    def _work():
        try:
            from .tools.stock_data import (
                get_index_trend,
                get_market_macro,
                get_market_micro,
                get_news_headlines,
                get_sentiment,
            )
            from .config import news_enabled

            date = state.get("date", "")
            micro = get_market_micro.invoke({"date": date})
            trend = get_index_trend.invoke({"date": date, "days": 60})
            macro = get_market_macro.invoke({"date": date})
            sentiment = get_sentiment.invoke({"date": date})
            # 2026-08-12：资讯开关——关闭时不取资讯、报告不含资讯段
            news = get_news_headlines.invoke({"date": date}) if news_enabled() else ""
            market_md = (
                f"## 一、今日市场微观数据\n{micro}\n\n"
                f"## 二、近60日指数趋势\n{trend}\n\n"
                f"## 三、宏观外围数据\n{macro}\n\n"
                f"## 四、昨日涨停今日表现\n{sentiment}"
            )
            if news:
                market_md += f"\n\n## 五、今日财经资讯\n{news}"
            result["value"] = {
                "market_data": market_md,
                "raw_news": news,
                "snapshot": {"legacy": True},
            }
        except Exception:
            result["value"] = None

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(timeout=_LEGACY_FETCH_TIMEOUT)
    return result.get("value")


def _normalize_snapshot(data, mode, date, notes, source):
    """把取数结果包装为快照（供 I4 落盘/前端图表/规则引擎）。"""
    return {
        "date": date,
        "mode": mode,
        "source": source,
        "data": data if isinstance(data, dict) else {},
        "degraded": list(notes),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _extract_news(data):
    """从取数结果中提取资讯原始文本（JSON 字符串）。"""
    if not isinstance(data, dict):
        return "{}"
    news = data.get("news")
    if news is None:
        for key in ("headlines", "caixin", "cctv"):
            if data.get(key):
                news = data[key]
                break
    if news is None:
        return "{}"
    if isinstance(news, str):
        return news
    try:
        return json.dumps(news, ensure_ascii=False, indent=2)
    except Exception:
        return str(news)


def _news_enabled() -> bool:
    """资讯开关（config.news_enabled 惰性导入；任何异常按关闭处理，绝不阻塞）。"""
    try:
        from .config import news_enabled

        return news_enabled()
    except Exception:
        return False


_MODE_BLOCKS = {
    "pre_market": ["index", "trend", "external", "news"],
    "auction": ["auction", "index", "zhangting", "news"],
    "intraday_am": ["index", "intraday", "sectors", "zhangting", "sentiment", "news"],
    "noon": ["index", "intraday", "sectors", "zhangting", "sentiment", "news"],
    # P1-F（2026-08-07）：盘中不声明龙虎榜块（盘中本无此数据，避免恒显缺失）
    "intraday_pm": ["index", "intraday", "sectors", "zhangting", "sentiment", "news"],
    "close": ["index", "zhangting", "dieting", "sectors", "sentiment", "breadth", "dragon_tiger", "news"],
}

_BLOCK_ALIASES = {
    "index": ("指数表现", ("index", "indices", "index_data", "zhishu", "index_trend")),
    "trend": ("近60日指数趋势", ("trend", "ma", "index_trend", "avg_lines")),
    "external": ("宏观外围数据", ("external", "macro", "overnight", "overseas")),
    "news": ("今日财经资讯", ("news", "headlines")),
    "zhangting": ("涨停板", ("zhangting", "zt", "zt_pool", "limit_up")),
    "dieting": ("跌停板", ("dieting", "dt", "dt_pool", "limit_down")),
    "sectors": ("板块表现", ("sectors", "sector", "board", "industry")),
    "sentiment": ("昨日涨停今日表现（情绪核心）", ("sentiment", "emotion", "yesterday_zt")),
    "breadth": ("全市场涨跌分布", ("breadth", "market_breadth", "up_down")),
    "auction": ("竞价数据", ("auction", "jingjia", "auction_data", "call_auction")),
    "intraday": ("分时数据", ("intraday", "fenshi", "minute", "minline")),
    "dragon_tiger": ("龙虎榜", ("dragon_tiger", "lhb", "longhubang")),
}


def _find_block(data, names):
    for key in names:
        if key in data and data[key] is not None:
            return data[key]
    # P1-E（2026-08-07）：close 跌停板从 zt_pool 嵌套 limit_down 提取
    if "limit_down" in names and isinstance(data.get("zt_pool"), dict):
        inner = data["zt_pool"].get("limit_down")
        if inner is not None:
            return inner
    if "limit_up" in names and isinstance(data.get("zt_pool"), dict):
        inner = data["zt_pool"].get("limit_up")
        if inner is not None:
            return inner
    return None


def _fmt_value(v):
    if v is None:
        return "无数据"
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def _dict_to_md(value, indent=0):
    lines = []
    prefix = "  " * indent
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("raw", "daily"):
                continue
            if isinstance(v, (dict, list)):
                lines.append(f"{prefix}- {k}:")
                lines.extend(_dict_to_md(v, indent + 1))
            else:
                lines.append(f"{prefix}- {k}: {_fmt_value(v)}")
    elif isinstance(value, list):
        if not value:
            lines.append(f"{prefix}（空列表）")
        elif all(isinstance(x, dict) and ("time" in x or "datetime" in x or "分钟" in x)
                 for x in value[:3]):
            # 分时/分钟线：给 LLM 完整摘要（首尾+极值+采样），禁止只截前 10 根
            n = len(value)
            def _bar(b):
                return "，".join(
                    f"{k}={_fmt_value(v)}" for k, v in b.items()
                    if k != "raw" and not isinstance(v, (dict, list))
                )
            lines.append(f"{prefix}- 共 {n} 根（完整）")
            lines.append(f"{prefix}- 首根: {_bar(value[0])}")
            lines.append(f"{prefix}- 当前: {_bar(value[-1])}")
            highs = [x.get("high") or x.get("close") for x in value
                     if (x.get("high") or x.get("close")) is not None]
            lows = [x.get("low") or x.get("close") for x in value
                    if (x.get("low") or x.get("close")) is not None]
            if highs:
                lines.append(f"{prefix}- 最高: {max(highs)}，最低: {min(lows)}")
            step = max(1, n // 40)
            for item in value[::step][:40]:
                lines.append(f"{prefix}- {_bar(item)}")
        else:
            for item in value[:10]:
                if isinstance(item, dict):
                    fields = "，".join(
                        f"{k}={_fmt_value(v)}" for k, v in item.items()
                        if k not in ("raw",) and not isinstance(v, (dict, list))
                    )
                    lines.append(f"{prefix}- {fields or '（对象）'}")
                else:
                    lines.extend(_dict_to_md(item, indent + 1))
    else:
        lines.append(f"{prefix}{_fmt_value(value)}")
    return lines


def _block_md(label, value, cap=40):
    # 2026-08-12：LLM 输入脱敏——渲染前清洗股票名称（只留代码），其他字段不丢
    from .llm_sanitize import sanitize_for_llm

    value = sanitize_for_llm(value)
    header = f"### {label}"
    if isinstance(value, dict) and value.get("source"):
        header += f"（来源：{value['source']}）"
    lines = [header, ""]
    if isinstance(value, dict):
        if value.get("degraded"):
            reasons = "；".join(str(r) for r in (value.get("degraded_reason") or []))
            lines.append(f"- 降级：{reasons or '数据源降级'}")
        if value.get("note"):
            lines.append(f"- 说明：{value['note']}")
    lines.extend(_dict_to_md(value))
    if len(lines) > cap:
        lines = lines[:cap] + ["", f"（数据过长，仅展示前 {cap - 2} 行）"]
    return "\n".join(lines)


def _render_market_markdown(data, mode, notes):
    """把取数结果渲染为 Markdown 数据块（缺失块明确标注，绝不编造）。"""
    if not isinstance(data, dict):
        return "（无数据）"
    # 旧工具兜底路径：直接复用已组合的 Markdown
    if isinstance(data.get("market_data"), str) and data.get("raw_news") is not None:
        return data["market_data"]

    mode = mode if mode in _MODE_BLOCKS else DEFAULT_MODE
    date = data.get("date") or ""
    label = MODE_INFO.get(mode, {}).get("label", mode)
    lines = [f"# {date} 行情数据快照（模式：{label}）", ""]
    if notes:
        lines.append("> 降级说明：" + "；".join(notes))
        lines.append("")
    for blk in _MODE_BLOCKS[mode]:
        # 2026-08-12：资讯开关——关闭时跳过资讯块（既不渲染内容也不渲染「缺失」占位）
        if blk == "news" and not _news_enabled():
            continue
        block_label, names = _BLOCK_ALIASES[blk]
        value = _find_block(data, names)
        if value is None:
            lines.append(f"### {block_label}：数据缺失（无数据）")
            lines.append("")
            continue
        if blk == "index":
            # 2026-08-10 修复：指数块每指数一行摘要（避免 40 行截断丢科创50）
            idx_lines = [f"### {block_label}", ""]
            idx = value.get("indices") if isinstance(value, dict) else value
            if isinstance(idx, dict):
                for name, m in idx.items():
                    if not isinstance(m, dict):
                        idx_lines.append(f"- {name}：无数据")
                        continue
                    pct = m.get("pct_change")
                    pct_s = f"{pct * 100:+.2f}%" if pct is not None else "无数据"
                    # 2026-08-10：摘要行带全字段（一行显示，避免 _block_md 40 行截断丢列）
                    open_s = m.get("open") or "无数据"
                    high_s = m.get("high") or "无数据"
                    low_s = m.get("low") or "无数据"
                    amt_s = m.get("amount") or "无数据"
                    idx_lines.append(
                        f"- {name}：收盘 {m.get('close') or '无数据'}，"
                        f"开盘 {open_s}，最高 {high_s}，最低 {low_s}，"
                        f"涨跌幅 {pct_s}，成交额 {amt_s}，MA5 {m.get('ma5') or '无数据'}"
                    )
            elif isinstance(idx, list):
                for it in idx[:10]:
                    idx_lines.append(f"- {_fmt_value(it)[:120]}")
            else:
                idx_lines.append(f"- {_fmt_value(value)[:200]}")
            lines.append("\n".join(idx_lines))
            lines.append("")
            continue
        lines.append(_block_md(block_label, value))
        lines.append("")
    known = {n for _, names in _BLOCK_ALIASES.values() for n in names}
    extra = [k for k in data if k not in known and not str(k).startswith("_")]
    if extra:
        lines.append("### 其他数据")
        for k in extra[:5]:
            lines.append(f"- {k}: {_fmt_value(data[k])[:200]}")
    return "\n".join(lines)


def fetch_data_node(state):
    """Node1：按 mode 分发取数（I1 纯函数优先，防御性降级，绝不阻塞）。"""
    mode = state.get("mode") or DEFAULT_MODE
    date = state.get("date") or ""
    _reset_progress()
    _emit("fetch", 3, "正在拉取行情数据…")

    notes = []
    fetcher = _load_pure_fetcher()
    data = None
    source = "missing"
    if fetcher is not None:
        try:
            data = fetcher(mode=mode, date=date)
            source = "fetch_mode_data"
        except Exception as exc:
            notes.append(f"模式化取数失败：{exc}")
    if not isinstance(data, dict) or not data:
        legacy = _legacy_fetch(state)
        if isinstance(legacy, dict) and legacy.get("market_data"):
            data = legacy
            source = "legacy"
            notes.append("模式化取数不可用，已使用旧取数工具降级")
        else:
            data = {}
            notes.append("取数工具不可用/离线：行情数据缺失，禁止编造")

    # I1 契约结构：{date, mode, mode_label, blocks, degraded[], degraded_flag}
    mode_label = None
    degraded_notes = []
    if isinstance(data, dict) and isinstance(data.get("blocks"), dict):
        degraded_notes = [str(r) for r in (data.get("degraded") or [])]
        mode_label = data.get("mode_label")
        data = data["blocks"]

    # 2026-08-12：资讯开关——关闭时剥离资讯块（快照/market_data 均不含资讯，
    # 报告 prompt 无资讯数据 → LLM 不会生成资讯内容）
    if isinstance(data, dict) and not _news_enabled():
        data.pop("raw_news", None)
        data.pop("news", None)
        for _k in ("headlines", "caixin", "cctv"):
            data.pop(_k, None)

    # 2026-08-12（用户口径）：只保留「全链路失败、数据缺失」的降级标注——
    # 主源失败但备用源成功（数据可用）的不标注（快照/报告/meta 同步干净）
    notes.extend(_filter_usable_degraded(degraded_notes, data if isinstance(data, dict) else {}))

    snapshot = _normalize_snapshot(data, mode, date, notes, source)
    if mode_label:
        snapshot["mode_label"] = mode_label
    if _news_enabled():
        raw_news = data.get("raw_news") if isinstance(data, dict) else None
        if raw_news is None:
            raw_news = _extract_news(data)
    else:
        raw_news = ""
    market_md = _render_market_markdown(data, mode, notes) or "（无数据）"

    # R12：市场天气/情绪温度（复盘指标，prompts 第1节必须引用）。
    # 优先复用已取 blocks（zt_pool/breadth/sentiment 与天气三块口径一致，省重复取数 ~20s）；
    # 兜底 fetch_market_weather（与首页同源，内部并行 + 60s 缓存，绝不抛异常）。
    weather_text = "无数据"
    try:
        from .tools.market_extra import compute_market_weather, fetch_market_weather

        w = compute_market_weather(data)
        if not (w.get("weather") and w.get("weather") != "数据缺失"):
            w = fetch_market_weather(date)  # 复用失败（如 zt_pool 并发缺失）→ 独立取数
        if w.get("weather") and w.get("weather") != "数据缺失":
            label = w.get("emotion_label")
            score = w.get("emotion_score")
            if label and score is not None:
                weather_text = f"{w['weather']}（{label} {score} 分）"
            else:
                weather_text = str(w["weather"])
    except Exception:  # noqa: BLE001 - 天气失败不阻断复盘
        weather_text = "无数据"

    _emit("fetch", 8, "行情数据获取完成")
    return {
        "market_data": market_md,
        "market_weather": weather_text,
        "raw_news": raw_news,
        "snapshot": snapshot,
        "degraded": bool(notes) or bool(state.get("degraded")),
        "degraded_notes": notes,
    }


# 各模式核心数据块：缺失（source=数据缺失）即无复盘必要（R7 取数校验）
CRITICAL_BLOCKS = {
    "pre_market": ("index_trend",),
    "auction": ("auction",),
    "intraday_am": ("index_trend", "minute"),
    "noon": ("index_trend", "minute"),
    "intraday_pm": ("index_trend", "minute"),
    # P1-F（2026-08-07）：close 的关键闸门去掉 minute（收盘后分钟线无意义）
    "close": ("index_trend",),
}


def _block_usable(blk) -> bool:
    if blk is None:
        return False
    if isinstance(blk, dict) and blk.get("source") in ("数据缺失", "数据缺失/估算"):
        return False
    return True


def _filter_usable_degraded(notes: list, blocks: dict) -> list:
    """2026-08-12（用户口径）：降级标注只保留「全链路失败、数据确实缺失」的条目；
    主源失败但备用源成功（数据可用）的不再标注——例如「sectors: 同花顺不可用，
    降级腾讯行业排行」这类降级链正常工作信息从快照/报告/meta 中剔除。

    note 前缀匹配块名（如 sectors/minute/index_trend）或中文标签（如 涨停板），
    该块数据可用（_block_usable）即丢弃该条；匹配不到块的保留（保守）。
    """
    kept = []
    for note in notes:
        head = str(note).split(":", 1)[0].strip()
        # 2026-08-12：资讯开关关闭时 news 块不取数（不存在是预期）——
        # news 相关降级标注一律丢弃，不进入快照/报告/meta
        if head == "news" and not _news_enabled():
            continue
        usable = False
        for blk, (label, names) in _BLOCK_ALIASES.items():
            if head == blk or head == label or head in names:
                usable = _block_usable(_find_block(blocks, names))
                break
        if not usable:
            kept.append(note)
    return kept


def validate_data_node(state):
    mode = state.get("mode") or "close"
    snapshot = state.get("snapshot") or {}
    blocks = snapshot.get("data") if isinstance(snapshot, dict) else None
    if not isinstance(blocks, dict) or not blocks:
        return {}
    missing = [name for name in CRITICAL_BLOCKS.get(mode, ())
               if name in blocks and not _block_usable(blocks.get(name))]
    if missing:
        names = "、".join(missing)
        raise DataValidationError(
            "取数校验失败：" + names + "数据缺失（全部数据源不可用），本次复盘终止。请检查网络/代理/数据源后重试。"
        )
    return {}

# ═══════════════════════════════════════════════════════════════
# Node1b: 资讯分析
# ═══════════════════════════════════════════════════════════════

def _rule_news_analysis(raw_news):
    """降级资讯分析：原文罗列（规则提取标题），不做 AI 多空归类，禁止编造。"""
    try:
        payload = json.loads(raw_news)
    except Exception:
        return "资讯内容无法解析（降级），无法提取利多利空。"
    lines = []
    for item in (payload.get("caixin") or [])[:5]:
        if isinstance(item, dict) and item.get("summary"):
            lines.append(f"- [财新] {str(item['summary'])[:120]}")
    for title in (payload.get("cctv") or [])[:5]:
        lines.append(f"- [央视] {title}")
    for ev in (payload.get("economic_calendar") or [])[:3]:
        if isinstance(ev, dict) and ev.get("event"):
            lines.append(f"- [经济日历] {ev.get('time', '')} {ev.get('event', '')}")
    if not lines:
        return "资讯内容为空（降级），无法提取利多利空。"
    return "### 今日要闻（规则引擎提取）\n" + "\n".join(lines)


def news_analyst_node(state):
    """Node1b：资讯分析（LLM 失败 → 规则提取 + 降级标注）。"""
    # 2026-08-12：资讯开关——关闭时静默跳过（不 emit news 阶段，
    # job 阶段序列不含「资讯」，前端进度条不显示该步骤）
    if not _news_enabled():
        return {"market_data": state.get("market_data", "")}
    raw_news = state.get("raw_news") or ""
    _emit("news", 12, "正在分析资讯…")
    if not raw_news.strip() or raw_news.strip() == "{}":
        analysis = "今日无重大资讯影响。"
        _emit("news", 15, "资讯分析完成（无资讯）")
        return {"market_data": f"{state.get('market_data', '')}\n\n## 资讯分析\n\n{analysis}"}

    content = None
    degraded = False
    if _llm_ready():
        try:
            _, strict = _llm_pair()
            content = _invoke_llm(
                strict,
                [
                    {"role": "system", "content": prompts.NEWS_ANALYST_SYSTEM_PROMPT},
                    {"role": "user", "content": prompts.build_news_prompt(raw_news)},
                ],
            ).content
        except Exception:
            content = None
    if content is None:
        content = _rule_news_analysis(raw_news)
        degraded = True
    if degraded:
        content += "\n\n> （降级模式：LLM 不可用，资讯仅原文罗列，未做 AI 利多/利空归类）"
    _emit("news", 15, "资讯分析完成")
    return {
        "market_data": f"{state.get('market_data', '')}\n\n## 资讯分析\n\n{content}",
        "degraded": bool(state.get("degraded")) or degraded,
        "degraded_notes": ["LLM 不可用/失败，资讯分析降级为规则提取"] if degraded else [],
    }


# ═══════════════════════════════════════════════════════════════
# Node2: 分析师复盘（趋势派 2 + 情绪派 3，一次并行；方案 B 不注入 trend_context）
# ═══════════════════════════════════════════════════════════════

_EMPTY_OPINION_PATTERNS = (
    "查询返回为空", "无明确观点", "工具查询完成，无", "无法获取", "本轮工具", "无观点输出",
)


def _is_empty_opinion(text) -> bool:
    """R15/M4 空话检测：非空但无实质（过短 <30 字符或命中模式）→ 视为空 content。

    防御：检测自身失败一律返回 True——宁可工具摘要/规则引擎兜底，不可空话进报告。
    真实取证：data/reviews/2026-08-07「本轮工具查询返回为空，无明确观点输出」。
    """
    try:
        t = str(text or "").strip()
        if not t:
            return True
        if len(t) < 30:
            return True
        return any(p in t for p in _EMPTY_OPINION_PATTERNS)
    except Exception:  # noqa: BLE001 - 检测失败按空话兜底
        return True


def _sanitize_tool_result(result):
    """R15/R1 工具结果脱敏：只影响追加给 LLM 的 tool 消息（落盘 tool_log 保留原样）。
    dict → sanitize_for_llm → JSON 文本；JSON 文本 → parse → sanitize → dumps；失败原样。"""
    try:
        from .llm_sanitize import sanitize_for_llm

        if isinstance(result, dict):
            return json.dumps(sanitize_for_llm(result), ensure_ascii=False)
        if isinstance(result, str) and result.strip():
            try:
                obj = json.loads(result)
            except (TypeError, ValueError):
                return result
            return json.dumps(sanitize_for_llm(obj), ensure_ascii=False)
        return result
    except Exception:  # noqa: BLE001 - 脱敏失败绝不阻塞
        return result


def _normalize_no_data(tool_name, text, args):
    """R15/M4 工具空结果显式化：空字符串/空壳 dict（空 results、数据缺失等）→
    {"error": "无数据（代码X）", ...}，给 LLM 明确「无数据」而非空壳，杜绝空话。"""
    try:
        key = str(args.get("code") or args.get("query") or "?")
        if not isinstance(text, str) or not text.strip():
            return json.dumps({"error": f"无数据（代码{key}）", "code": key,
                               "note": "查询无返回，请基于已有行情数据输出观点"},
                              ensure_ascii=False)
        obj = None
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            try:
                obj = json.loads(stripped)
            except (TypeError, ValueError):
                obj = None
        empty_shell = False
        if isinstance(obj, dict):
            if not obj:
                empty_shell = True
            elif obj.get("results") == []:
                empty_shell = True  # search_history 无命中
            elif obj.get("source") == "数据缺失":
                empty_shell = True  # get_stock_info 全字段缺失
            elif "close" in obj and obj.get("close") is None:
                empty_shell = True
        elif isinstance(obj, list) and not obj:
            empty_shell = True
        if empty_shell:
            return json.dumps({"error": f"无数据（代码{key}）", "code": key,
                               "note": "查询无命中或数据缺失，请基于已有行情数据输出观点"},
                              ensure_ascii=False)
        return text
    except Exception:  # noqa: BLE001 - 显式化失败原样返回
        return text

def _llm_analyst(state, skill, group, trend_context):
    """单分析师 LLM 推理（含工具调用循环）；任何失败返回 None → 规则引擎。"""
    try:
        llm, _ = _llm_pair()
        base_prompt = prompts.build_analyst_prompt(state, group, trend_context, style_anchor=skill.get("style_anchor", ""))
        # R12：注入模拟账户状态与 A 股规则铁律（仅查持仓工具，下单类工具已移除——
        # 2026-08-12 用户确认：分析师只按冻结格式输出推荐行，计划由程序解析文本自动记录）
        from .accounts import engine as acct_engine
        from .accounts import storage as acct_storage

        skill_id = skill.get("id", "")
        acct = acct_storage.load_account(skill_id)
        if acct is None:
            acct = acct_engine.open_account(skill_id)
            acct["trades"] = []
        else:
            acct["trades"] = acct_storage.load_trades(skill_id)
        account_block = prompts.build_account_block(acct, state.get("date") or "")
        if account_block:
            base_prompt = f"{base_prompt}\n\n{account_block}"
        get_stock_info, search_history = _load_analyst_tools()
        use_tools = get_stock_info is not None and search_history is not None
        tools = [get_stock_info, search_history] if use_tools else []
        account_tools = _make_account_tools(skill_id)
        tools.extend(account_tools)
        runner = llm.bind_tools(tools) if tools else llm

        messages = [
            {"role": "system", "content": skill.get("prompt", "")},
            {"role": "user", "content": base_prompt},
        ]
        response = _invoke_llm(runner, messages)
        tool_log = []
        tool_rounds = 0
        # 2026-08-10：工具轮上限保持 3——曾改 2 导致 LLM 分析被截断
        # （get_positions→search→get_stock_info 需多轮），最终无推荐/无成交
        while getattr(response, "tool_calls", None) and tool_rounds < 3:
            messages.append(response)
            for tc in response.tool_calls:
                args = tc.get("args", {}) if isinstance(tc.get("args"), dict) else {}
                tool_name = tc.get("name", "?")
                if tool_name == "search_history":
                    try:
                        result = search_history.invoke(args)
                    except BaseException as exc:  # chroma Rust panic 是 BaseException
                        result = json.dumps({"error": f"历史检索不可用：{exc}"}, ensure_ascii=False)
                    tool_log.append({"type": "search", "query": args.get("query", "?"), "result": result[:200]})
                elif tool_name == "get_stock_info":
                    try:
                        result = get_stock_info.invoke(args)
                    except BaseException as exc:
                        result = json.dumps({"error": f"个股查询不可用：{exc}"}, ensure_ascii=False)
                    tool_log.append({"type": "get_stock_info", "code": args.get("code", "?"),
                                     "result": result[:300]})
                elif tool_name == "get_positions":
                    try:
                        result = account_tools[0]()
                    except BaseException as exc:  # noqa: BLE001
                        result = json.dumps({"ok": False, "reason": f"持仓查询失败：{exc}"},
                                            ensure_ascii=False)
                    tool_log.append({"type": "get_positions", "result": result[:300]})
                else:
                    tool_log.append({"type": "unknown", "result": str(args)[:200]})
                    result = str(args)
                # R15/M4：工具结果脱敏（只影响发给 LLM 的文本，tool_log 落盘保留原样）
                # + 空/无命中显式化为「无数据（代码X）」结构，避免空壳引导 LLM 输出空话
                llm_result = _sanitize_tool_result(result)
                llm_result = _normalize_no_data(tool_name, llm_result, args)
                # R12 修复（2026-08-09）：每个 tool_call 必须立即追加对应的 tool 回复，
                # opencode 端点严格要求 tool_calls 消息后必须逐条回复每个 tool_call_id，
                # 缺一条 → 400 Bad Request → 分析师整体降级（此前 7 月复盘全降级的根因）
                messages.append({"role": "tool", "content": llm_result, "tool_call_id": tc["id"]})
            response = _invoke_llm(runner, messages)
            tool_rounds += 1

        # 2026-08-12 R15/M4 修复（阿狼 8-10 案例）：工具循环达到轮数上限退出时，
        # LLM 最后一轮回复可能只有 tool_calls、没有正文（9 次工具调用后 content 为空，
        # 4 位同事正常、辩论阶段也正常——证明是「循环截断在 LLM 想总结之前」而非 LLM 故障）。
        # 追加一次**强制总结**调用（不带工具，明示直接输出最终分析），仍为空才走兜底。
        if tool_log:
            _final0 = response.content if hasattr(response, "content") else None
            if not (_final0 and str(_final0).strip()):
                try:
                    _forced_msgs = list(messages)
                    _forced_msgs.append({
                        "role": "system",
                        "content": (
                            "工具查询已全部完成，所有工具结果都在上面的对话里。"
                            "请立即输出你的最终分析：严格按照要求输出「## 1、今日判断」与"
                            "「## 2、操作计划」两个章节（基于工具结果与今日行情数据，只写股票代码），"
                            "不要再调用任何工具。"
                        ),
                    })
                    _forced = _invoke_llm(llm, _forced_msgs)  # 无工具 runner，杜绝再调工具
                    if getattr(_forced, "content", None) and str(_forced.content).strip():
                        response = _forced
                except BaseException:  # noqa: BLE001 - 强制总结失败走原兜底
                    pass

        # R12 自动下单兜底：解析分析师输出中结构化/冻结格式的买入卖出推荐
        # 支持：
        #   结构化：买入 002384 东山精密：买入价189.00元，买入100股
        #           卖出 000967 盈峰环境：卖出价12.50元，卖出200股
        #   冻结式：002384 东山精密：买入价189.00元，买入100股 / 仓位2成
        # 程序化执行（不依赖 LLM 自觉调工具）。
        # R14 A1：直接写 plan registry（不再经 place_order 工具，避免 bind_tools
        # 签名膨胀；source 区分 tool/regex_qty/regex_cheng）。
        # R15/P2-8：三段正则归一 → accounts/plan_extractor.extract_plans（单正则统一
        # qty式/成数式/结构化成数式；side 判定、qty/cheng 互斥、卖出无持仓跳过均在其中）
        try:
            from .accounts.plan_extractor import extract_plans

            _held_codes = {str(c).zfill(6) for c in (acct.get("positions") or {})}
            tool_log.extend(extract_plans(
                response.content or "", state.get("date") or "",
                skill.get("id", ""), skill.get("name", "") or skill.get("id", ""),
                _held_codes,
            ))
        except BaseException:  # noqa: BLE001 - 解析失败不阻塞
            pass
        # 刷新账户（计划已由文本解析写入 plan registry，账户撮合在次日重放）
        try:
            from .accounts import storage as _acct_storage

            _fresh = _acct_storage.load_account(skill_id)
            if _fresh is not None:
                _fresh["trades"] = _acct_storage.load_trades(skill_id)
                acct = _fresh
        except BaseException:  # noqa: BLE001
            pass

        # 2026-08-10 兜底：LLM 工具循环后内容为空（偶发）时，
        # 有工具调用 → 用工具摘要生成兜底文本；完全空 → 返回 None 走规则引擎
        # R15/M4：非空但为「空话」（过短/命中模式，如「本轮工具查询返回为空，无明确观点输出」）
        # → 同样按空 content 处理，绝不放行空话进报告（检测失败默认按空话兜底）
        _final = response.content if hasattr(response, "content") else None
        _empty_opinion = _final is not None and _is_empty_opinion(_final)
        if not (_final and str(_final).strip()) or _empty_opinion:
            if tool_log:
                _kinds = {}
                for _c in tool_log:
                    _t = str(_c.get("type") or "?")
                    _kinds[_t] = _kinds.get(_t, 0) + 1
                # 2026-08-12 R15/M4：兜底不再输出「LLM 回复为空」占位——
                # 改用规则引擎观点（基于快照事实，有实质内容）+ 工具摘要附录
                try:
                    _rule = _rule_analyst(state, skill, group)
                    _rule_text = _rule.get("analysis", "") if isinstance(_rule, dict) else ""
                except BaseException:  # noqa: BLE001
                    _rule_text = ""
                _summary = "；".join(f"{k}×{v}" for k, v in _kinds.items())
                _final = (
                    f"{_rule_text}\n\n"
                    f"### 工具查询摘要\n{_summary}\n\n"
                    "（LLM 未输出正文，按规则引擎观点与工具查询结果整理；"
                    "仅供参考，不构成投资建议）"
                )
            else:
                return None

        # 2026-08-12（8-03 拔小弦案例）：LLM 最终输出只有自然语言总结、没有标准推荐行
        # （「买入 代码：买入价XX.XX元，仓位X成」）→ 重试一次（明示按冻结格式输出）；
        # 仍无格式行则接受原样（不自动补齐——2026-08-12 用户确认：下单工具已移除，
        # 计划只来自程序解析分析师文本，重试是唯一的补救手段）
        _REC_LINE_RE_ = re.compile(
            r"(?:买入|卖出)\s*\d{6}|^\s*\d{6}\s*[：:]\s*(?:买入价|卖出价)", re.M)
        if _final is not None and str(_final).strip() and not _REC_LINE_RE_.search(str(_final)):
            try:
                _retry_msgs = list(messages)
                _retry_msgs.append({
                    "role": "system",
                    "content": (
                        "你的上一版回复缺少标准推荐行。请按冻结格式逐只输出推荐行"
                        "（每行一只，只写股票代码）：`买入 代码：买入价XX.XX元，仓位X成`，"
                        "价格必须是你基于行情数据得出的目标价。禁止用自然语言总结代替格式行。"
                    ),
                })
                _retry = _invoke_llm(llm, _retry_msgs)  # 无工具 runner，强制输出格式行
                if getattr(_retry, "content", None) and str(_retry.content).strip():
                    _retry_text = str(_retry.content)
                    if _REC_LINE_RE_.search(_retry_text):
                        _final = _retry_text
            except BaseException:  # noqa: BLE001 - 重试失败接受原样
                pass
        return {
            "skill_name": skill.get("name", ""),
            "skill_id": skill.get("id", ""),
            "analysis": str(_final),
            "tool_used": bool(tool_log),
            "tool_calls": tool_log,
            "group": group,
            "account": acct,  # R12：本地交易后的账户（含今日成交/拒绝记录）
        }
    except BaseException:  # chroma Rust panic（PanicException）是 BaseException，必须兜住
        return None



def _as_ratio(value, key):
    """契约：比率一律小数；旧字段 *_pct 为百分数，进契约前 ÷100 归一。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not _isfinite(v):
        return None
    if str(key).endswith("_pct"):
        return v / 100.0
    return v


def _isfinite(v):
    try:
        import math
        return math.isfinite(v)
    except Exception:
        return True


def _rule_facts(snapshot):
    """从快照中提取规则引擎可用事实（只取语义明确字段，禁止编造）。"""
    if not isinstance(snapshot, dict):
        return {}
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else snapshot
    if not isinstance(data, dict):
        return {}
    facts = {}

    index = _find_block(data, ("index", "indices", "index_data", "zhishu", "index_trend"))
    index_dir = {}
    if isinstance(index, dict):
        index_items = index.get("indices") if isinstance(index.get("indices"), dict) else index
        for name, item in index_items.items():
            if isinstance(item, dict) and item.get("pct_change") is not None:
                try:
                    index_dir[name] = float(item["pct_change"])
                except (TypeError, ValueError):
                    continue
    facts["index"] = index_dir

    zt = _find_block(data, ("zhangting", "zt", "zt_pool", "limit_up"))
    if isinstance(zt, dict):
        limit_up = zt.get("limit_up")
        if isinstance(limit_up, dict) and limit_up.get("count") is not None:
            facts["zt_total"] = limit_up["count"]
        elif zt.get("sealed_count") is not None:
            facts["zt_total"] = zt["sealed_count"]
        elif zt.get("total") is not None:
            facts["zt_total"] = zt["total"]
        if zt.get("zhaban_rate") is not None:
            facts["zhaban_rate"] = _as_ratio(zt["zhaban_rate"], "zhaban_rate")
    dt = _find_block(data, ("dieting", "dt", "dt_pool", "limit_down"))
    if isinstance(dt, dict):
        limit_down = dt.get("limit_down")
        if isinstance(limit_down, dict) and limit_down.get("count") is not None:
            facts["dt_total"] = limit_down["count"]
        elif dt.get("count") is not None:
            # P1-E（2026-08-07）：zt_pool.limit_down 嵌套结构直接含 count
            facts["dt_total"] = dt["count"]
        elif dt.get("total") is not None:
            facts["dt_total"] = dt["total"]

    sentiment = _find_block(data, ("sentiment", "emotion", "yesterday_zt"))
    if isinstance(sentiment, dict):
        for src_key, out_key in (
            ("avg_return_pct", "avg_return"),
            ("avg_return", "avg_return"),
            ("red_rate_pct", "red_rate"),
            ("red_rate", "red_rate"),
            ("lianban_rate_pct", "lianban_rate"),
            ("lianban_rate", "lianban_rate"),
            ("hean_rate_pct", "hean_rate"),
            ("hean_rate", "hean_rate"),
        ):
            if src_key in sentiment and sentiment[src_key] is not None and out_key not in facts:
                ratio = _as_ratio(sentiment[src_key], src_key)
                if ratio is not None:
                    facts[out_key] = ratio
        if sentiment.get("up_count") is not None:
            facts["up"] = sentiment["up_count"]
        if sentiment.get("down_count") is not None:
            facts["down"] = sentiment["down_count"]
        if sentiment.get("zhaban_rate") is not None and "zhaban_rate" not in facts:
            facts["zhaban_rate"] = _as_ratio(sentiment["zhaban_rate"], "zhaban_rate")

    breadth = _find_block(data, ("breadth", "market_breadth", "up_down"))
    if isinstance(breadth, dict):
        if breadth.get("up") is not None:
            facts["up"] = breadth["up"]
        if breadth.get("down") is not None:
            facts["down"] = breadth["down"]
        if breadth.get("up_down_ratio") is not None and "up_down_ratio" not in facts:
            try:
                facts["up_down_ratio"] = float(breadth["up_down_ratio"])
            except (TypeError, ValueError):
                pass
        if breadth.get("limit_up") is not None and "zt_total" not in facts:
            facts["zt_total"] = breadth["limit_up"]
        if breadth.get("limit_down") is not None and "dt_total" not in facts:
            facts["dt_total"] = breadth["limit_down"]
        for src_key in ("zhaban_rate_pct", "zhaban_rate"):
            if src_key in breadth and breadth[src_key] is not None:
                ratio = _as_ratio(breadth[src_key], src_key)
                if ratio is not None:
                    facts["zhaban_rate"] = ratio
                break

    sectors = _find_block(data, ("sectors", "sector", "board", "industry"))
    top = []
    if isinstance(sectors, dict) and isinstance(sectors.get("top5"), list):
        for s in sectors["top5"][:3]:
            if isinstance(s, dict):
                name = s.get("name") or s.get("板块") or s.get("板块名称")
                if name:
                    top.append(str(name))
    facts["sectors"] = top

    auction = _find_block(data, ("auction", "jingjia", "auction_data", "call_auction"))
    facts["auction_available"] = isinstance(auction, dict) and auction.get("count") is not None
    return facts


def _facts_sentence(facts):
    parts = []
    if facts.get("zt_total") is not None:
        parts.append(f"涨停 {facts['zt_total']} 家")
    if facts.get("dt_total") is not None:
        parts.append(f"跌停 {facts['dt_total']} 家")
    if facts.get("up") is not None and facts.get("down") is not None:
        parts.append(f"涨 {facts['up']} 家 / 跌 {facts['down']} 家")
    if facts.get("avg_return") is not None:
        parts.append(f"昨日涨停今日平均收益 {facts['avg_return'] * 100:.2f}%")
    if facts.get("red_rate") is not None:
        parts.append(f"昨日涨停今日红盘率 {facts['red_rate'] * 100:.1f}%")
    if facts.get("zhaban_rate") is not None:
        parts.append(f"炸板率 {facts['zhaban_rate'] * 100:.1f}%")
    if facts.get("sectors"):
        parts.append("领涨板块：" + "、".join(facts["sectors"]))
    if not parts:
        return "关键数据缺失（无数据）"
    return "；".join(parts)


_INDEX_LABELS = {"shanghai": "上证", "shenzhen": "深证", "chuangye": "创业板", "kechuang": "科创50"}


def _direction_sentence(facts):
    idx = facts.get("index") or {}
    if not idx:
        return "指数方向：无数据"
    parts = []
    for name, v in idx.items():
        label = _INDEX_LABELS.get(name, name)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        direction = "上涨" if v > 0 else ("下跌" if v < 0 else "持平")
        parts.append(f"{label}{direction}（涨跌幅数据源值 {v}）")
    return "指数方向：" + "；".join(parts)


_RULE_PLAN = {
    "trend": "方向未确认前控制总仓位，按均线纪律执行：破位收缩、企稳再谈进攻；无明确信号时空仓等待。",
    "sentiment": "情绪数据不足或退潮时不追高、不接力；等涨停梯队与量能确认后只做前排。",
}


def _rule_analyst(state, skill, group):
    """规则引擎分析师条目：仅引用快照事实，缺失标注，绝不编造。"""
    facts = _rule_facts(state.get("snapshot"))
    info = prompts.mode_info(state.get("mode"))
    yesterday = (state.get("yesterday_report") or "").strip()
    verify = "无昨日报告，跳过验证" if not yesterday else "未找到本人昨日预判，无法验证"
    # 2026-08-06 修复：禁止把 skill.prompt 提示词原文写进降级分析（提示词泄漏）；
    # 只展示 skill.role 人设一句话（P0-2）
    persona_line = (skill.get("role") or "").strip() or "（无角色描述）"
    analysis = (
        f"## 1、昨日复盘验证\n{verify}\n\n"
        f"## 2、今日判断（{info['focus_zh']}）\n"
        f"{persona_line}\n\n"
        f"基于降级数据快照：{_facts_sentence(facts)}\n"
        f"{_direction_sentence(facts)}\n\n"
        f"## 3、操作计划（{info['plan_zh']}）\n"
        f"{_RULE_PLAN.get(group, '')}\n"
        f"（降级模式：规则引擎生成，仅基于快照数据；数据缺失项不猜测）"
    )
    return {
        "skill_name": skill.get("name", ""),
        "skill_id": skill.get("id", ""),
        "analysis": analysis,
        "tool_used": False,
        "tool_calls": [],
        "group": group,
        "degraded": True,
    }


def _run_analysts(state):
    """一次并行执行全部分析师（趋势派 2 + 情绪派 3，同一批线程、一次提交）。

    R15/R2 方案 B：情绪派不再引用趋势派结论（trend_context 不注入，传空串）；
    R15/R2（2026-08-12 用户反馈）：进度事件统一为单一「分析师」阶段（分析师 n/5），
    不再分趋势派/情绪派两个阶段；全部完成后按组排序统一发射，pct 单调（20→40）。
    """
    skills = [s for s in list_skills() if s.get("group") in ("trend", "sentiment", "both")]
    if not skills:
        return []
    _ANALYST_TIMEOUT = 300.0  # R12 修复：单批分析师整体超时，超时者降级为规则引擎

    def _group_of(skill):
        return "trend" if skill.get("group") in ("trend", "both") else "sentiment"

    def _invoke_one(skill, index):
        group = _group_of(skill)
        entry = None
        if _llm_ready():
            # 2026-08-10 兜底：LLM 偶发失败（超时/网络/限流）时重试 2 次，
            # 仍失败才降级规则引擎——避免单次偶发失败导致整篇降级
            import time as _time
            for _attempt in range(3):
                try:
                    entry = _llm_analyst(state, skill, group, "")
                    if entry is not None:
                        break
                except BaseException:  # noqa: BLE001 - 超时/异常 → 重试
                    entry = None
                if _attempt < 2:
                    _time.sleep(5 * (_attempt + 1))
        if entry is None:
            entry = _rule_analyst(state, skill, group)
        return entry

    results = []
    import contextvars

    # 2026-08-10：传播进度 ContextVar 到线程池——每个任务独立 copy_context
    # （同一 Context 对象不能并发进入多个线程，会抛 already entered）
    with ThreadPoolExecutor(max_workers=max(1, len(skills))) as pool:
        futures = {pool.submit(contextvars.copy_context().run, _invoke_one, s, i): i
                   for i, s in enumerate(skills, 1)}
        try:
            for future in as_completed(futures, timeout=_ANALYST_TIMEOUT):
                results.append((futures[future], future.result()))
        except TimeoutError:
            # 超时：未完成的分析师降级为规则引擎
            done_ids = {r[0] for r in results}
            for i, s in enumerate(skills, 1):
                if i not in done_ids:
                    try:
                        results.append((i, _rule_analyst(state, s, _group_of(s))))
                    except BaseException:  # noqa: BLE001
                        results.append((i, None))
    results.sort(key=lambda x: x[0])

    # 进度：统一「分析师 n/5」阶段，按组排序后一次发射（trend 在前，保证 pct 单调）。
    ordered = ([s for s in skills if _group_of(s) == "trend"]
               + [s for s in skills if _group_of(s) == "sentiment"])
    for i, s in enumerate(ordered, 1):
        _emit("analysts", 20 + (i - 1) * 5,
              f"分析师 {i}/{len(ordered)}：{s.get('name', '')}完成")
    return [r for _, r in results if r is not None]


def analysts_node(state):
    """Node2：分析师复盘（一次并行 5 位：趋势派 2 + 情绪派 3，方案 B 不注入 trend_context）。"""
    results = _run_analysts(state)
    degraded = any(r.get("degraded") for r in results)
    # R12：聚合各分析师模拟账户快照 {skill_id: account}——
    # 只收 skill_id 非空且 account 为 dict 的条目；规则引擎降级条目无 account 跳过。
    accounts = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        skill_id = r.get("skill_id")
        acct = r.get("account")
        if skill_id and isinstance(acct, dict):
            accounts[skill_id] = _compact_account(acct)
    return {
        "analyses": results,
        "analyst_accounts": accounts,
        "degraded": bool(state.get("degraded")) or degraded,
        "degraded_notes": ["分析师 LLM 不可用/失败，分析降级为规则引擎"] if degraded else [],
    }


# R12：账户快照瘦身——meta.accounts 只保留核心字段，避免 state 膨胀；
# 完整 trades/snapshots 仍保留在 analyses[i].account 中（报告落盘不丢信息）。
_ACCOUNT_CORE_KEYS = ("cash", "positions", "realized_pnl", "current_date")
_POSITION_CORE_KEYS = ("name", "qty", "frozen_qty", "avg_cost")


def _compact_account(acct: dict) -> dict:
    """账户快照裁剪：仅保留 cash/positions/realized_pnl/current_date，
    每只持仓仅保留 name/qty/frozen_qty/avg_cost——丢弃 trades/snapshots 大字段。"""
    compact = {k: acct[k] for k in _ACCOUNT_CORE_KEYS if k in acct}
    positions = acct.get("positions")
    if isinstance(positions, dict):
        compact["positions"] = {
            code: ({k: pos[k] for k in _POSITION_CORE_KEYS if k in pos}
                   if isinstance(pos, dict) else pos)
            for code, pos in positions.items()
        }
    return compact


# ═══════════════════════════════════════════════════════════════
# Node3: 主持人（分歧判断）
# ═══════════════════════════════════════════════════════════════

_BULLISH_WORDS = ("看多", "看涨", "低吸", "买入", "加仓", "进攻", "抄底", "持有", "反弹", "追涨")
_BEARISH_WORDS = ("看空", "看跌", "减仓", "卖出", "防守", "空仓", "观望", "离场", "止损", "退潮", "回避")


def _rule_host(state):
    """规则版主持人：按多空关键词判断是否存在实质性分歧。"""
    # P1-C（2026-08-07）：降级模式下分析师文本是规则模板（含"进攻/空仓"等词），
    # 关键词判断必然误报；统一改为按快照数据判断——
    # 指数方向（多）与情绪/涨跌家数（空）对立时视为存在分歧。
    facts = _rule_facts(state.get("snapshot"))
    idx = facts.get("index") or {}
    idx_dirs = [_sign(v) for v in idx.values() if isinstance(v, (int, float))]
    if idx_dirs:
        idx_dir = 1 if any(d > 0 for d in idx_dirs) and not any(d < 0 for d in idx_dirs) else (
            -1 if any(d < 0 for d in idx_dirs) and not any(d > 0 for d in idx_dirs) else 0)
    else:
        idx_dir = 0
    sentiment_dir = 0
    avg = facts.get("avg_return")
    if avg is not None:
        sentiment_dir = _sign(avg)
    elif facts.get("red_rate") is not None:
        sentiment_dir = 1 if float(facts["red_rate"]) > 0.5 else (-1 if float(facts["red_rate"]) < 0.5 else 0)
    if idx_dir != 0 and sentiment_dir != 0 and idx_dir != sentiment_dir:
        return True, "多空方向分歧：今日应进攻还是防守？"
    return False, ""


def host_node(state):
    """Node3：主持人判断分歧并提炼议题；LLM 失败 → 规则关键词判断。"""
    round_label = "首轮" if not state.get("debate_history") else f"第{state.get('round_count', 0) + 1}轮"
    _emit("host", 50, f"主持人判断（{round_label}）…")

    content = None
    degraded = False
    if _llm_ready():
        try:
            _, strict = _llm_pair()
            content = _invoke_llm(
                strict,
                [
                    {"role": "system", "content": prompts.HOST_SYSTEM_PROMPT},
                    {"role": "user", "content": prompts.build_host_prompt(state, round_label)},
                ],
            ).content
        except Exception:
            content = None
    if content is None:
        degraded = True
        has_disagreement, topic = _rule_host(state)
    else:
        has_disagreement, topic = _parse_host(content)

    new_round = state.get("round_count", 0) + 1
    max_rounds = max(1, min(int(state.get("max_rounds") or 3), 3))
    discussion_done = (not has_disagreement) or (new_round > max_rounds) or (not topic)
    return {
        "discussion_topic": topic,
        "discussion_done": discussion_done,
        "round_count": new_round,
        "degraded": bool(state.get("degraded")) or degraded,
        "degraded_notes": ["主持人 LLM 不可用/失败，分歧判断降级为规则引擎"] if degraded else [],
    }


# ═══════════════════════════════════════════════════════════════
# Node4: 辩论（交叉引用，循环 ≤ max_rounds）
# ═══════════════════════════════════════════════════════════════

_RULE_DEBATE = {
    "alang": "趋势未破前底仓不动，回踩关键均线企稳是观察点；破位则收缩，不赌反弹。",
    "bingchuan": "方向之争不重要，逻辑在不在才重要；主升逻辑未被证伪就不必恐慌，证伪当天纠错。",
    "baxiaoxian": "情绪分歧期重仓才是大忌；断板缩量先观察，换手放量确认再低吸。",
    "yangjia": "一致转分歧是必然过程；高潮后先兑现一部分，等恐慌释放再考虑机会。",
    "tiechui": "行情是走出来的不是猜出来的；现在多看少动，等确定性信号再动手。",
}


def debate_node(state):
    """Node4：对议题逐位表态，引用上一轮他人观点（交叉引用）。"""
    topic = state.get("discussion_topic", "")
    current_round = state.get("round_count", 1) or 1
    _emit("debate", 55 + (current_round - 1) * 10, f"辩论第 {current_round} 轮…")
    if not topic:
        return {"debate_history": [{"round": current_round, "topic": "无议题", "responses": []}]}

    skills = {s["name"]: s for s in list_skills()}
    all_names = [a["skill_name"] for a in (state.get("analyses") or [])]
    all_names = list(dict.fromkeys(n for n in all_names if n in skills))

    prev_response = {}
    if state.get("debate_history"):
        last_entry = state["debate_history"][-1]
        for r in last_entry.get("responses", []):
            prev_response[r["skill_name"]] = r["response"]

    responses = []
    degraded = False
    for i, name in enumerate(all_names):
        skill = skills[name]
        others_text = "".join(
            f"\n【{other_name}的观点】\n{other_resp[:500]}\n"
            for other_name, other_resp in prev_response.items() if other_name != name
        )
        response = None
        if _llm_ready():
            try:
                llm, _ = _llm_pair()
                response = _invoke_llm(
                    llm,
                    [
                        {"role": "system", "content": skill.get("prompt", "")},
                        {"role": "user", "content": prompts.build_debate_prompt(skill, topic, others_text)},
                    ],
                ).content
            except Exception:
                response = None
        if response is None:
            degraded = True
            base = _RULE_DEBATE.get(skill.get("id") or "", _RULE_DEBATE["tiechui"])
            response = f"（降级模式）关于「{topic}」：{base}"
        responses.append({
            "skill_name": name,
            "skill_id": skill.get("id", ""),
            "response": response,
        })
        _emit("debate", 56 + (current_round - 1) * 10 + i, f"辩论第 {current_round} 轮：{name}表态完成")

    return {
        "debate_history": [{"round": current_round, "topic": topic, "responses": responses}],
        "degraded": bool(state.get("degraded")) or degraded,
        "degraded_notes": ["辩论 LLM 不可用/失败，观点降级为规则引擎"] if degraded else [],
    }


# ═══════════════════════════════════════════════════════════════
# Node5: 最终报告（唯一 Markdown + 免责声明强制 + 综合标签）
# ═══════════════════════════════════════════════════════════════

def _extract_tags(content):
    """从「## 综合标签」章节提取 # 标签（其他章节出现的 # 不提取）。"""
    tags = []
    m = re.search(r"##\s*综合标签(.*?)(?:\n##|\Z)", content or "", re.S)
    section = m.group(1) if m else ""
    for t in re.findall(r"#([^\s#，,。；;【】]+)", section):
        t = t.strip()
        if t and t not in tags:
            tags.append(t)
    return tags[:6]


def _sign(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0
    return 1 if v > 0 else (-1 if v < 0 else 0)


def _summary_sentence(facts):
    idx = facts.get("index") or {}
    if not idx:
        return "降级模式下仅依据有限快照，今日方向无法确认（数据不足）。"
    signs = [_sign(v) for v in idx.values()]
    up = sum(1 for s in signs if s > 0)
    down = sum(1 for s in signs if s < 0)
    if up and not down:
        return "主要指数方向偏强，但降级模式数据有限，建议以真实行情复核。"
    if down and not up:
        return "主要指数方向偏弱，降级模式下建议防守优先。"
    return "指数方向分化，降级模式下建议控制仓位、等待确认。"


def _rule_tags(state):
    """规则版综合标签（降级可见 + 方向/情绪标签，全部源自快照）。"""
    facts = _rule_facts(state.get("snapshot"))
    tags = ["降级模式"]
    signs = [_sign(v) for v in (facts.get("index") or {}).values()]
    if signs:
        up = sum(1 for s in signs if s > 0)
        down = sum(1 for s in signs if s < 0)
        if up and not down:
            tags.append("大盘偏强")
        elif down and not up:
            tags.append("大盘偏弱")
        else:
            tags.append("方向分化")
    red = facts.get("red_rate")
    if red is not None:
        tags.append("情绪回暖" if red >= 0.5 else ("情绪偏冷" if red < 0.4 else "情绪中性"))
    zhaban = facts.get("zhaban_rate")
    if zhaban is not None and zhaban >= 0.4:
        tags.append("炸板率高企")
    if not any(k for k in facts if k not in ("index", "sectors", "auction_available")):
        tags.append("数据有限")
    return tags[:6]


def _section_index(sections, *hints, default=0):
    for i, s in enumerate(sections):
        if any(h in s for h in hints):
            return i
    return default


def _rule_report(state):
    """规则版最终报告：按模式章节结构生成，缺失标注，降级可见。"""
    mode = state.get("mode") or DEFAULT_MODE
    info = prompts.mode_info(mode)
    date = state.get("date") or ""
    sections = info["sections"]
    facts = _rule_facts(state.get("snapshot"))

    content_by_title = {}

    overview = [
        f"- 复盘日期：{date}；模式：{info['label']}（{info['window']}）",
        f"- {_facts_sentence(facts)}",
        f"- {_direction_sentence(facts)}",
    ]
    if mode == "auction" and not facts.get("auction_available"):
        overview.append("- 竞价数据缺失（无 Cookie/数据源不可用），无法给出追高建议")
    content_by_title[sections[0]] = overview

    views_idx = _section_index(sections, "核心观点")
    views = []
    for a in (state.get("analyses") or []):
        views += ["", f"### {a.get('skill_name', '分析师')}", a.get("analysis", "")]
    content_by_title[sections[views_idx]] = views

    debate_idx = _section_index(sections, "分歧")
    debate_lines = []
    debate = state.get("debate_history") or []
    if debate:
        for entry in debate:
            debate_lines += ["", f"### 第{entry.get('round', '?')}轮：{entry.get('topic', '')}"]
            for r in entry.get("responses", []):
                debate_lines.append(f"- **{r.get('skill_name', '')}**：{r.get('response', '')}")
    else:
        debate_lines += ["", "本轮分析未发现实质性分歧（或分歧判断数据不足）。"]
    content_by_title[sections[debate_idx]] = debate_lines

    plan_idx = _section_index(sections, "操作", "计划", "清单", "买卖", "尾盘")
    content_by_title[sections[plan_idx]] = [
        f"- {_RULE_PLAN['trend']}",
        f"- {_RULE_PLAN['sentiment']}",
        "- 数据缺失项未做推测；请以真实行情数据为准。",
    ]

    sum_idx = _section_index(sections, "总结", "预案")
    if sum_idx != 0 or "总结" in sections[0] or "预案" in sections[0]:
        content_by_title[sections[sum_idx]] = [f"- {_summary_sentence(facts)}"]

    risk_lines = [
        "- 市场有风险，本报告基于降级规则引擎与有限快照数据生成，仅供参考。",
        "- 历史数据不代表未来表现，请独立判断，控制仓位。",
    ]
    risk_idx = _section_index(sections, "风险")
    if "风险" in sections[risk_idx]:
        content_by_title[sections[risk_idx]] = risk_lines
    elif "总结" in sections[sum_idx]:
        content_by_title[sections[sum_idx]] = content_by_title.get(sections[sum_idx], []) + ["", "**风险提示**"] + risk_lines

    tag_idx = _section_index(sections, "综合标签")
    if "综合标签" in sections[tag_idx]:
        tags = _rule_tags(state)
        content_by_title[sections[tag_idx]] = [" ".join(f"#{t}" for t in tags) if tags else "暂无"]

    lines = [
        f"# {prompts.mode_report_title(date, mode)}",
        "",
        "> （降级模式：本报告由规则引擎基于数据快照生成，缺失项标注「无数据」，不构成任何预测）",
        "",
    ]
    for s in sections:
        lines += ["", f"## {s}", ""]
        lines.extend(content_by_title.get(s, ["（降级模式：该章节无可用数据，见上方数据快照）"]))
    return "\n".join(lines)


_REC_CODE_RE = re.compile(r"(\d{6})")
_REC_BRACKET_CODE_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z]{2,12}（\d{6}）")


def _recommendation_format_ok(report: str) -> bool:
    """R15（2026-08-12，用户反馈）：报告推荐行格式校验——分析师只输出代码。

    违规（触发报告重试）：
    - 推荐行（含 买入价/卖出价）出现股票名称（代码后跟中文名 / 「名称（代码）」），
      或一行 ≥2 个代码——用户口径：推荐只写代码，名称会引发代码-名称不匹配；
    - 无买入价但像推荐行（含括号代码 + 元/成 + 推荐/仓位/成数）——如
      「弘信电子（002379）21.34元，2成」。
    不误伤：描述行（如「光迅科技（002281）今日+0.52%...」不含元/成组合）放行。
    """
    if not report:
        return True
    for line in report.splitlines():
        codes = _REC_CODE_RE.findall(line)
        if not codes:
            continue
        bracket = bool(_REC_BRACKET_CODE_RE.search(line))
        if ("买入价" in line or "卖出价" in line):
            # 推荐行只允许代码：代码后跟中文名称、括号代码、一行多代码均违规
            name_after_code = bool(re.search(r"\d{6}\s+[\u4e00-\u9fa5]{2,12}", line))
            if bracket or name_after_code or len(codes) > 1:
                return False
            continue
        # 无买入价：仅当形如「名称（代码）xx元，x成/推荐」的推荐行才算违规
        if bracket and ("成" in line or "仓位" in line) and ("元" in line or "推荐" in line):
            return False
    return True


def report_node(state):
    """Node5：LLM 汇总生成报告。
    2026-08-10（用户确认）：LLM 不可用/失败时**直接终止复盘**（抛 DataValidationError →
    任务标记 error），不再降级生成规则报告——LLM 都不可用时复盘无意义。
    """
    _emit("report", 85, "正在生成复盘报告…")
    report = None
    tags = []
    if not _llm_ready():
        raise DataValidationError("LLM 不可用（无 API Key 或模型未配置），无法生成复盘报告，本次复盘终止。")
    try:
        llm, _ = _llm_pair()
        # R15（2026-08-12）：推荐行格式校验——违规重试一次（prompt 加提示），
        # 仍违规接受（不无限重试）；LLM 输出格式被官方建议用结构化约束 + 程序校验
        report = None
        for _attempt in range(2):
            _user = prompts.build_report_prompt(state)
            if _attempt > 0:
                _user += (
                    "\n\n⚠️ 上一版报告「个股推荐」格式违规：必须每行一只，格式"
                    "`代码 名称：买入价XX.XX元，仓位X成`，禁止「名称（代码）」、"
                    "禁止一行多只、禁止区间价。请严格重写推荐相关行。"
                )
            report = _invoke_llm(
                llm,
                [
                    {"role": "system", "content": prompts.REVIEW_ASSISTANT_SYSTEM_PROMPT},
                    {"role": "user", "content": _user},
                ],
            ).content
            if _recommendation_format_ok(report or ""):
                break
        tags = _extract_tags(report)
    except Exception as exc:
        # 2026-08-10 调试：Content Exists Risk 定位——失败时转储报告 prompt 各段。
        # 2026-08-13：转储收敛——仅当 DEBUG_DUMP_PROMPT=1/true/on/yes（忽略大小写）时写盘；
        # 路径改用 pathlib 计算（STOCK_REVIEW_DATA_DIR 或项目根 parents[2] 下的 .tmp）。
        if str(os.environ.get("DEBUG_DUMP_PROMPT", "")).strip().lower() in ("1", "true", "on", "yes"):
            try:
                import json as _json
                from pathlib import Path as _Path

                _dump_dir = _Path(
                    os.environ.get("STOCK_REVIEW_DATA_DIR")
                    or _Path(__file__).resolve().parents[2]
                ) / ".tmp"
                _dump_dir.mkdir(parents=True, exist_ok=True)
                with open(_dump_dir / "report_prompt_dump.json",
                          "w", encoding="utf-8") as _f:
                    _f.write(_json.dumps({
                        "date": state.get("date"),
                        "system": prompts.REVIEW_ASSISTANT_SYSTEM_PROMPT,
                        "user": prompts.build_report_prompt(state),
                        "market_data": (state.get("market_data") or "")[:3000],
                        "analyses": [(a.get("skill_name"), (a.get("analysis") or "")[:800])
                                     for a in (state.get("analyses") or [])],
                        "debate": [(e.get("round"), e.get("topic"),
                                    [(r.get("skill_name"), (r.get("response") or "")[:400])
                                     for r in e.get("responses", [])])
                                   for e in (state.get("debate_history") or [])],
                    }, ensure_ascii=False, indent=1))
            except Exception:  # noqa: BLE001 - 调试转储失败不影响主流程
                pass
        raise DataValidationError(f"LLM 生成报告失败，本次复盘终止：{exc}") from exc
    report = ensure_disclaimer(report)
    _emit("report", 90, "报告生成完成")
    _emit("done", 100, "复盘完成")
    return {
        "final_report": report,
        "overall_tags": tags or [],
        "degraded": bool(state.get("degraded")),
        "degraded_notes": state.get("degraded_notes") or [],
    }


# ═══════════════════════════════════════════════════════════════
# 图构建 / 结果组装
# ═══════════════════════════════════════════════════════════════

def router(state):
    """host 节点后判断：继续辩论还是出报告（≤ max_rounds）。"""
    if state.get("discussion_done", True):
        return "report"
    max_rounds = max(1, min(int(state.get("max_rounds") or 3), 3))
    if (state.get("round_count") or 0) >= max_rounds:
        return "report"
    # P1-C（2026-08-07）：降级模式（规则引擎）最多辩论 1 轮，
    # 避免重复生成逐字相同的规则观点，浪费轮次与报告篇幅。
    # round_count>=2 表示已辩论过 1 轮，再次回到 host 时结束。
    if state.get("degraded") and (state.get("round_count") or 0) >= 2:
        return "report"
    return "debate"


def default_initial_state(date, mode=DEFAULT_MODE, max_rounds=3,
                          yesterday_report="", earlier_today="", skill_names=None):
    """完整初始状态（I4 上下文注入：yesterday_report / earlier_today，缺失为空串）。"""
    return {
        "date": date,
        "mode": mode,
        "max_rounds": max_rounds,
        "yesterday_report": yesterday_report or "",
        "earlier_today": earlier_today or "",
        "skill_names": list(skill_names or []),
        "market_data": "",
        "raw_news": "",
        "snapshot": {},
        "analyses": [],
        "discussion_topic": "",
        "discussion_done": False,
        "round_count": 0,
        "debate_history": [],
        "final_report": "",
        "overall_tags": [],
        "disclaimer": DISCLAIMER,
        "degraded": False,
        "degraded_notes": [],
    }


def build_graph(progress_callback=None):
    """构建并编译 LangGraph：fetch → news → analysts → host ⇄ debate → report。"""
    set_progress_callback(progress_callback)
    _reset_progress()
    graph = StateGraph(ReviewState)

    graph.add_node("fetch_data", fetch_data_node)
    graph.add_node("validate_data", validate_data_node)
    graph.add_node("news_analyst", news_analyst_node)
    graph.add_node("analysts", analysts_node)
    graph.add_node("host", host_node)
    graph.add_node("debate", debate_node)
    graph.add_node("report", report_node)

    graph.set_entry_point("fetch_data")
    graph.add_edge("fetch_data", "validate_data")
    graph.add_edge("validate_data", "news_analyst")
    graph.add_edge("news_analyst", "analysts")
    graph.add_edge("analysts", "host")
    graph.add_conditional_edges("host", router, {
        "report": "report",
        "debate": "debate",
    })
    graph.add_edge("debate", "host")
    graph.add_edge("report", END)
    return graph.compile()


def build_result(state) -> dict:
    """§五 result.report 严格结构：{final_report, analysts[], debate_history[],
    overall_tags[], disclaimer, degraded, round_count}；免责声明组装层强制。"""
    return {
        "final_report": ensure_disclaimer(state.get("final_report") or ""),
        "analyses": state.get("analyses") or [],
        "debate_history": state.get("debate_history") or [],
        "overall_tags": state.get("overall_tags") or [],
        "disclaimer": DISCLAIMER,
        "degraded": bool(state.get("degraded")),
        "round_count": state.get("round_count") or 0,
    }
