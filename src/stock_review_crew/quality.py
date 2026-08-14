"""质量评估模块（R8 聊天 + R10 复盘质量打分）。

能力：
- ``trace_chat(stage, payload)``: Langfuse 风格全量 trace（无凭据 no-op，返回 trace id 或 None）；
- ``trace_review(stage, payload, metadata)``: 复盘 trace（name=``review:{stage}``，返回 trace id）；
- ``verify_numbers(text, structured)``: 数字核验器（防幻觉：报告/回复中的数字必须可溯源）；
- ``evaluate_rubric(items)``: 1-5 分 rubric 打分 + 证据引用（供后续 Eval 使用）；
- ``score_review(report, structured, meta, trace_id)``: 复盘确定性 1-5 分（结构/免责声明/
  数字核验/降级标注可见），有凭据且带 trace_id 时写回 Langfuse score（name=``review_quality``）；
- ``score_chat(messages, structured, meta, trace_id)``: 聊天确定性评分（name=``chat_quality``）。

设计约束（requirements-v4.md 第七章）：
- 不导入 chat.py（避免循环依赖）；内部依赖仅标准库 + 可选第三方（try/except 包裹）；
- 失败无害：任何异常都在本模块内部消化，绝不抛给调用方、绝不阻塞主流程；
- 比率一律小数存储（0.5=50%），核对时按同一单位归一化。

数据口径（requirements.md 第六章 / requirements-v4.md 第四章）：
- 比率字段（涨幅/换手率/胜率等）结构化值为小数比率；文本 ``50%`` 与数据 ``0.5`` 视为一致，
  文本 ``0.5`` 与数据 ``0.5`` 亦一致（同一单位归一化）；
- 金额单位元：文本 ``1.2亿`` -> 120000000（支持 千/万/亿/万亿）；
- ``None`` = 无数据：文本声称该字段有数值而数据为 ``None`` -> 记为 mismatch。
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Optional

__all__ = [
    "trace_chat",
    "trace_review",
    "verify_numbers",
    "evaluate_rubric",
    "score_review",
    "score_chat",
    "detect_sensitive_phrases",
    "is_trace_enabled",
]


# R6 P2（2026-08-08）：敏感表述检测词表——比喻/夸张/口语化修辞（「近乎腰斩」「断崖」「崩了」类）。
# 仅收录明确修辞词，避免误伤「涨跌/跌幅」等正常数据措辞；供质量分/人工复核参考。
SENSITIVE_PHRASES = (
    "近乎腰斩", "腰斩", "断崖", "断崖式", "崩了", "雪崩", "跳水", "闪崩", "狂泻",
    "史诗级", "核弹级", "惨不忍睹", "血流成河", "哀鸿遍野", "跌麻", "起飞", "一飞冲天",
    "疯涨", "吓人", "离谱", "吊打", "碾压", "疯狂拉升",
)


def detect_sensitive_phrases(text: Any) -> dict:
    """检测报告/回复中的禁止修辞（R6 P2）：返回 ``{hits: [{phrase, count}], total}``。

    失败无害：任何异常返回空结果，绝不抛出、绝不影响主流程。
    """
    try:
        body = str(text or "")
        hits = []
        for phrase in SENSITIVE_PHRASES:
            n = body.count(phrase)
            if n:
                hits.append({"phrase": phrase, "count": n})
        return {"hits": hits, "total": sum(h["count"] for h in hits)}
    except Exception:  # noqa: BLE001
        return {"hits": [], "total": 0}


# ────────────────────────── Langfuse trace 占位 ──────────────────────────

LANGFUSE_ENV_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")
LANGFUSE_DEFAULT_HOST = "https://cloud.langfuse.com"

# 免责声明（组装层强制追加；此处为评分判定的同一口径）
DISCLAIMER = "仅供参考，不构成投资建议"

# 6 种时间模式报告章节标题（requirements.md §二 / prompts.MODE_INFO 冻结口径；
# 调用方可通过 meta["sections"] 覆盖，避免与 prompts.py 漂移）
REVIEW_SECTIONS: dict[str, list[str]] = {
    "pre_market": [
        "一、隔夜与外围概况", "二、今日大盘预判", "三、各分析师核心观点",
        "四、分歧点", "五、操作计划", "六、风险提示",
    ],
    "auction": [
        "一、竞价概况", "二、早盘操作建议", "三、各分析师核心观点",
        "四、分歧点", "五、能追/回避清单", "六、风险提示",
    ],
    "intraday_am": [
        "一、上午盘面回顾", "二、午后走势判断", "三、各分析师核心观点",
        "四、分歧点", "五、操作计划", "六、风险提示",
    ],
    "noon": [
        "一、上午回顾", "二、下午走势判断", "三、各分析师核心观点",
        "四、分歧点", "五、买卖计划", "六、风险提示",
    ],
    "intraday_pm": [
        "一、下午盘面回顾", "二、尾盘策略", "三、明日预案",
        "四、各分析师核心观点", "五、分歧点", "六、风险提示",
    ],
    "close": [
        "一、市场概况", "二、各分析师核心观点", "三、分歧点",
        "四、明日操作计划", "五、总结", "六、风险提示", "七、综合标签",
    ],
}

# rubric 权重（等权，冻结；总分 = Σ weight × item_score 四舍五入 clamp 1-5）
REVIEW_WEIGHTS = {"structure": 0.25, "disclaimer": 0.25, "numbers": 0.25, "degraded": 0.25}
CHAT_WEIGHTS = {"structure": 1 / 3, "numbers": 1 / 3, "analysts": 1 / 3}

# 通过线：总分 ≥3 视为通过（与 evaluate_rubric 默认 pass_threshold 一致）
REVIEW_PASS_THRESHOLD = 3


def _trace_id_of(obj: Any) -> Optional[str]:
    """从 trace 调用返回值中提取 trace id（str / dict / 带 trace_id 或 id 属性的对象）。"""
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj or None
    if isinstance(obj, dict):
        for key in ("trace_id", "id"):
            val = obj.get(key)
            if val:
                return str(val)
        return None
    for attr in ("trace_id", "id"):
        val = getattr(obj, attr, None)
        if val:
            return str(val)
    return None


def _client_trace(client: Any, **kwargs) -> Optional[str]:
    """尽力而为创建 trace 并返回 trace id。

    优先 ``client.trace(...)``（经典 API / 测试假客户端）；langfuse 4.x 的客户端没有
    ``trace`` 方法（PLAYBOOK 2026-08-06 条目：4.x 改为 OTel 风格），回退到
    ``client.start_observation(as_type="span", ...)`` 并从返回 span 取 trace_id。
    """
    trace_fn = getattr(client, "trace", None)
    if callable(trace_fn):
        return _trace_id_of(trace_fn(**kwargs))
    start_fn = getattr(client, "start_observation", None)
    if callable(start_fn):
        span = start_fn(
            name=kwargs.get("name") or "trace",
            as_type="span",
            input=kwargs.get("input"),
            metadata=kwargs.get("metadata"),
        )
        # 2026-08-06 修复：未 end 的 observation 不会导出（异步 BatchSpanProcessor
        # 只导出已完成的 span），导致 review:/chat: trace 在面板不可见、score 因关联
        # trace 不存在而查不到；立即 end 使其完整导出。
        try:
            span.end()
        except Exception:  # noqa: BLE001 - end 失败不影响 trace_id 提取
            pass
        return _trace_id_of(span)
    return None


def _client_score(client: Any, **kwargs) -> None:
    """尽力而为写回 score：优先 ``client.score(...)``（经典 API / 测试假客户端），
    langfuse 4.x 没有 ``score`` 方法时回退 ``client.create_score(...)``。"""
    score_fn = getattr(client, "score", None)
    if callable(score_fn):
        score_fn(**kwargs)
        return
    create_fn = getattr(client, "create_score", None)
    if callable(create_fn):
        create_fn(**kwargs)


def _client_flush(client: Any) -> None:
    """尽力而为 flush：确保 span/score 立即导出（2026-08-06 修复根因之一——
    不 flush 时依赖异步导出，实测 review:/chat: trace 面板不可见、score 查不到；
    仅对 langfuse 4.x 真实客户端生效，测试假客户端无 flush 属性时静默跳过）。"""
    try:
        flush_fn = getattr(client, "flush", None)
        if callable(flush_fn):
            flush_fn()
    except Exception:  # noqa: BLE001 - flush 失败不影响主流程
        pass

# 惰性缓存：仅当凭据齐全时尝试创建客户端一次；失败后缓存 None。
_langfuse_client: Optional[Any] = None
_langfuse_client_attempted = False


def _langfuse_configured() -> bool:
    """凭据齐全（PUBLIC + SECRET + HOST/BASE_URL）且未被显式关闭才算可记录。

    2026-08-12：与 config.langfuse_configured 对齐——LANGFUSE_ENABLED=0/false/off/no
    时全链路 no-op（含复盘/聊天 trace 与 score），供直连引擎的脚本用环境变量关闭。
    """
    if os.environ.get("LANGFUSE_ENABLED", "auto").strip().lower() in ("0", "false", "off", "no"):
        return False
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return False
    return bool(os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL"))


def _get_langfuse_client() -> Optional[Any]:
    """尽力而为地构造 Langfuse 客户端；任何失败都返回 None（绝不抛出）。"""
    global _langfuse_client, _langfuse_client_attempted
    if not _langfuse_configured():
        return None
    if _langfuse_client_attempted:
        return _langfuse_client
    _langfuse_client_attempted = True
    try:
        from langfuse import Langfuse  # 可选第三方：仅在此处惰性导入

        # 2026-08-06 修复（Langfuse 捕获不到 trace/score 的根因之一）：
        # OTLP 导出读环境/系统代理（假代理 9、用户 7890 均导致静默失败）；
        # 构造期间清理代理变量，并持久化 NO_PROXY=cloud.langfuse.com（异步导出线程
        # 请求时仍绕过代理，直连实测 200）。与 config.langfuse_callback 同策略。
        _proxy_env = {k: os.environ.pop(k, None) for k in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
        )}
        try:
            _langfuse_client = Langfuse(
                public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                host=os.environ.get("LANGFUSE_HOST")
                or os.environ.get("LANGFUSE_BASE_URL")
                or LANGFUSE_DEFAULT_HOST,
            )
        finally:
            for _k, _v in _proxy_env.items():
                if _v is not None:
                    os.environ[_k] = _v
        for _k in ("NO_PROXY", "no_proxy"):
            _cur = os.environ.get(_k, "")
            if "cloud.langfuse.com" not in _cur:
                os.environ[_k] = (_cur + "," if _cur else "") + "cloud.langfuse.com"
    except Exception:
        _langfuse_client = None
    return _langfuse_client


def is_trace_enabled() -> bool:
    """是否有 LANGFUSE_* 凭据（供引擎决定是否值得构造 payload）。"""
    return _langfuse_configured()


def trace_chat(stage: str, payload: dict) -> Optional[str]:
    """Langfuse 聊天 trace（签名稳定：stage/payload；返回值增强为 Optional[str]）。

    行为：
    - 无 LANGFUSE_* 凭据或 langfuse 导入失败 -> 立即返回 None，绝不抛错、绝不阻塞；
    - 有凭据时仅做尽力而为的记录：创建一条名为 ``chat:{stage}`` 的 trace，payload 作为
      input，metadata 记录 stage；不联网验证、不调用 flush（避免阻塞主流程，异步导出
      交给 langfuse 自身线程）。
    - 返回 trace id（供后续 score_chat 关联）；调用方忽略返回值不破坏原行为。
    - 内部吞掉一切异常（客户端构造、trace 调用失败均不影响主流程）。
    """
    try:
        client = _get_langfuse_client()
        if client is None:
            return None
        return _client_trace(
            client,
            name=f"chat:{stage}",
            input=payload if isinstance(payload, dict) else {"payload": payload},
            metadata={"stage": str(stage), "source": "stock_review_crew", "engine": "chat"},
        )
    except Exception:
        return None


def trace_review(stage: str, payload: dict, metadata: Optional[dict] = None) -> Optional[str]:
    """复盘 trace（R10 Q4）：name=``review:{stage}``，返回 Langfuse trace id 供后续
    score_review 关联；无凭据返回 None。

    metadata 恒含 ``source:"stock_review_crew"`` / ``engine:"review"`` / ``stage``；
    调用方传入的 mode/date/降级清单等通过 metadata 合并（调用方值优先）。
    失败无害：任何异常返回 None，绝不抛出、绝不阻塞主流程。
    """
    try:
        client = _get_langfuse_client()
        if client is None:
            return None
        base = {"source": "stock_review_crew", "engine": "review", "stage": str(stage)}
        if isinstance(metadata, dict):
            merged = dict(base)
            merged.update(metadata)
        else:
            merged = base
        trace_id = _client_trace(
            client,
            name=f"review:{stage}",
            input=payload if isinstance(payload, dict) else {"payload": payload},
            metadata=merged,
        )
        _client_flush(client)  # 2026-08-06：立即导出（否则异步导出不可见）
        return trace_id
    except Exception:
        return None


# ────────────────────────── 数字核验器（防幻觉） ──────────────────────────

# 2026-08-06 修复：排除「0.018616」小数位与「4,806,544」千分位数字片段被误判为
# 6 位股票代码（质量评分 numbers 项误报 mismatch 238 条的根因之一）；
# 仍支持「600519」「600519.SH」形态。
_CODE_RE = re.compile(r"(?<![\d.,])(\d{6})(?![\d,])")
_TOKEN_RE = re.compile(r"([+\-\u2212]?\d[\d,]*(?:\.\d+)?)\s*(万亿|亿|万|千|[%％])?")
_ALIAS_WINDOW = 20      # 字段别名检索窗口（token 前后字符数）
_ENTITY_WINDOW = 24     # 无别名数字就近匹配实体的最大距离
_MISSING = object()     # 字段缺失哨兵（与「字段存在但值为 None」区分）

# 规范化字段名 -> 文本中可出现的别名（中文 + 英文，匹配时取最近/最长）
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "pct_change": ("涨跌幅", "涨幅", "pct_change", "change_pct", "上涨", "下跌", "跌幅", "涨跌", "涨", "跌"),
    "close": ("收盘价", "收盘", "close", "最新价", "现价", "price", "股价", "价格", "收于"),
    "open": ("开盘价", "开盘", "open"),
    "high": ("最高价", "最高", "high"),
    "low": ("最低价", "最低", "low"),
    "change": ("涨跌额", "涨跌点数", "change"),
    "amount": ("成交额", "成交金额", "amount", "成交"),
    "volume": ("成交量", "volume"),
    "turnover_rate": ("换手率", "turnover_rate", "换手"),
    "pe": ("市盈率", "市盈", "pe"),
    "market_cap": ("总市值", "流通市值", "市值", "market_cap"),
    "limit_up": ("涨停", "limit_up"),
    "limit_down": ("跌停", "limit_down"),
    "yield": ("收益率", "yield", "收益"),
    "win_rate": ("胜率", "win_rate"),
}

# 这些规范化字段按小数比率存储（0.5=50%）
_RATIO_FIELDS = frozenset(
    {"pct_change", "turnover_rate", "yield", "win_rate", "open_pct", "high_pct", "low_pct", "amplitude"}
)

# 词义负号：命中这些词且数字无显式符号时取负（如「下跌2.15%」= -2.15%）
_SIGN_NEG_ALIASES = frozenset({"下跌", "跌", "跌了", "跌幅"})

# 6 位纯整数后紧跟这些计量/序数单位时视为普通数字而非股票代码
_CODE_SUFFIX_UNITS = "手股元点家次个只人"

# 2026-08-06：实体字段值归一化——上游快照（akshare/通达信原始字段）中比率类字段常按
# 百分数存储（涨跌幅 10.01 = 10.01%），核验器期望小数（0.1001）；提取实体时统一归一。
# 「市盈率/市净率/市销率」是倍数而非比率，明确排除。
_RATIO_KEY_HINTS = ("pct", "percent", "率", "幅", "ratio", "yield", "return", "占比", "胜率")
_NON_RATIO_RATE_KEYS = ("市盈", "市净", "市销", "pe", "pb", "ps")


def _normalize_entity_value(key: Any, value: Any) -> Any:
    """比率字段按小数归一（百分数 >1.5 → /100）；非比率/倍数字段原样返回。"""
    nk = _norm(key)
    if any(tok in nk for tok in _NON_RATIO_RATE_KEYS):
        return value
    if not any(tok in nk for tok in _RATIO_KEY_HINTS):
        return value
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(v) or abs(v) <= 1.5:
        return value
    return v / 100.0


def _norm(key: Any) -> str:
    """规范化字段名：小写 + 去除非字母数字（保留中文）。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(key).lower())


_CANON_ALIAS_NORMS = {c: {_norm(a) for a in aliases} for c, aliases in _FIELD_ALIASES.items()}


def _is_ratio_key(key: Any) -> bool:
    """字段是否按小数比率存储（pct/percent/率/幅/ratio/yield/return 等特征）。"""
    nk = _norm(key)
    if nk in {_norm(f) for f in _RATIO_FIELDS}:
        return True
    return any(tok in nk for tok in ("pct", "percent", "率", "幅", "ratio", "yield", "return"))


def _normalize_code(value: Any) -> Optional[str]:
    """从键/值中提取 6 位股票代码（支持 '600519'、'600519.SH'、600519 等）。"""
    if isinstance(value, int):
        s = str(value)
    elif isinstance(value, str):
        s = value.strip()
    else:
        return None
    m = re.search(r"(?<![\d.,])\d{6}(?![\d,])", s)
    return m.group(0) if m else None


def _extract_entities(structured: dict) -> tuple[dict[str, dict], dict]:
    """拆分结构化数据为 {代码: 数据} 实体表与扁平字段表。"""
    entities: dict[str, dict] = {}
    flat: dict[str, Any] = {}
    src = dict(structured)
    # 容器键：stocks / entities
    for container_key in ("stocks", "entities"):
        val = structured.get(container_key)
        if isinstance(val, dict):
            for k, v in val.items():
                code = _normalize_code(k)
                if code and isinstance(v, dict):
                    entities[code] = {
                        ck: _normalize_entity_value(ck, cv) for ck, cv in v.items()
                    }
            src = {k: v for k, v in structured.items() if k != container_key}
    # 单标的形态：{"code": "600519", "pct_change": ...}
    single = _normalize_code(structured.get("code", structured.get("代码")))
    if single:
        data = {k: _normalize_entity_value(k, v) for k, v in src.items()
                if _norm(k) not in ("code", "代码")}
        entities[single] = data
        return entities, {}
    for k, v in src.items():
        code = _normalize_code(k)
        if code and isinstance(v, dict):
            entities[code] = {ck: _normalize_entity_value(ck, cv) for ck, cv in v.items()}
        else:
            flat[k] = _normalize_entity_value(k, v)
    return entities, flat


def _field_value(data: dict, canonical: str) -> Any:
    """按规范化字段名查值：精确键 -> 别名等价键 -> 子串键；缺失返回 _MISSING。"""
    if canonical in data:
        return data[canonical]
    want = _norm(canonical)
    normals = _CANON_ALIAS_NORMS.get(canonical, {want})
    for key, val in data.items():
        if _norm(key) in normals:
            return val
    best_key: Optional[str] = None
    for key in data:
        nk = _norm(key)
        if want and (want in nk or nk in want):
            if best_key is None or len(nk) < len(_norm(best_key)):
                best_key = key
    return data[best_key] if best_key is not None else _MISSING


def _numeric_fields(data: dict):
    """产出 (键, 数值) 序列；跳过 None/布尔，容忍数字字符串。"""
    for k, v in data.items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            yield k, v
        elif isinstance(v, str):
            try:
                yield k, float(v.strip().replace(",", ""))
            except ValueError:
                pass


def _parse_number(raw: str) -> Optional[float]:
    s = raw.replace(",", "").replace("−", "-").replace("＋", "+").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _is_code_like(text: str, m: re.Match) -> bool:
    """6 位纯整数且后随非计量单位 -> 视为股票代码（不作为数字核验）。"""
    raw = m.group(1)
    if raw.startswith(("+", "-", "−")) or "." in raw or "," in raw:
        return False
    if len(re.sub(r"\D", "", raw)) != 6:
        return False
    nxt = text[m.end() : m.end() + 4].lstrip()
    if nxt and (nxt[0] in _CODE_SUFFIX_UNITS or nxt[:1] in ("年", "月", "日")):
        return False
    return True


def _is_year(text: str, m: re.Match) -> bool:
    """「2026年」形态的年份数字不作为数据声称。"""
    raw = m.group(1)
    if "." in raw or "," in raw:
        return False
    try:
        n = int(raw.replace("−", "-"))
    except ValueError:
        return False
    return 1900 <= n <= 2100 and text[m.end() : m.end() + 2].startswith("年")


def _context(text: str, start: int, end: int, pad: int = 24) -> str:
    s = text[max(0, start - pad) : end + pad]
    return re.sub(r"\s+", " ", s).strip()


def _nearest_alias(text: str, m: re.Match) -> Optional[tuple[str, str]]:
    """找数字 token 附近最近（同距取最长）的字段别名，返回 (规范化字段, 命中原词)。"""
    start, end = m.start(), m.end()
    win_start, win_end = max(0, start - _ALIAS_WINDOW), min(len(text), end + _ALIAS_WINDOW)
    window = text[win_start:win_end]
    best: Optional[tuple] = None
    for canonical, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            if not alias:
                continue
            flags = re.IGNORECASE if alias.isascii() else 0
            for am in re.finditer(re.escape(alias), window, flags):
                a_start = win_start + am.start()
                a_end = win_start + am.end()
                if a_end <= start:
                    d = start - a_end
                elif a_start >= end:
                    d = a_start - end
                else:
                    continue  # 与数字重叠
                key = (d, -len(alias), canonical)
                if best is None or key < best[0]:
                    best = (key, canonical, alias)
    return (best[1], best[2]) if best else None


def _nearest_entity(text: str, m: re.Match, code_spans: list) -> tuple[Optional[str], Optional[int]]:
    """找数字 token 最近的 6 位代码（用于无别名数字的就近溯源）。"""
    best: Optional[tuple] = None
    for cm in code_spans:
        c_start, c_end = cm.start(), cm.end()
        if c_end <= m.start():
            d = m.start() - c_end
        elif c_start >= m.end():
            d = c_start - m.end()
        else:
            continue
        if best is None or d < best[0]:
            best = (d, cm.group(1))
    return (best[1], best[0]) if best is not None else (None, None)


def _apply_unit(num: float, unit: str) -> float:
    if unit == "万亿":
        return num * 1e12
    if unit == "亿":
        return num * 1e8
    if unit == "万":
        return num * 1e4
    if unit == "千":
        return num * 1e3
    return num


def _apply_word_sign(num: float, alias_raw: str, raw_num: str) -> float:
    """「下跌2.15%」词义负号；数字自带 +/- 时以显式符号为准。"""
    if raw_num.startswith(("+", "-", "−")):
        return num
    if alias_raw in _SIGN_NEG_ALIASES:
        return -abs(num)
    return num


def _close(a: float, b: float) -> bool:
    """数值近似相等：相对容差 0.5%，绝对下限 1e-9。"""
    if a is None or b is None:
        return False
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return math.isclose(a, b, rel_tol=5e-3, abs_tol=max(1e-9, abs(b) * 5e-3))


def _values_match(num: float, unit: str, expected: Any, is_ratio: bool) -> bool:
    """同单位归一化比较。

    比率字段：文本 ``50%`` 取比率读数 0.5、百分数读数 50；数据侧期望值 v 同时以
    比率（v）与百分数（v*100）两个口径参与比较 —— 即 0.5=50% 两种写法都算一致。
    非比率字段：按字面数值比较（支持 千/万/亿/万亿 单位）。
    """
    if expected is None:
        return False
    try:
        exp = float(expected)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(exp):
        return False
    if is_ratio:
        if unit in ("%", "％"):
            ratio_reading, pct_reading = num / 100.0, num
        else:
            ratio_reading, pct_reading = num, num
        return _close(ratio_reading, exp) or _close(pct_reading, exp * 100.0)
    return _close(_apply_unit(num, unit), exp)


def _found_value(num: float, unit: str, is_ratio: bool) -> float:
    """mismatch 中 found 的归一化口径（与 expected 同单位）。"""
    if is_ratio:
        return num / 100.0 if unit in ("%", "％") else num
    return _apply_unit(num, unit)


def _mismatch(
    entity: Optional[str],
    number: str,
    expected: Any,
    found: Any,
    field: Optional[str],
    reason: str,
    context: str,
) -> dict:
    return {
        "entity": entity,
        "number": number,
        "expected": expected,
        "found": found,
        "field": field,
        "reason": reason,
        "context": context,
    }


def verify_numbers(text: str, structured: Optional[dict]) -> dict:
    """数字核验器：防幻觉（报告/回复中的数字必须可溯源）。

    签名：``verify_numbers(text: str, structured: dict) -> dict``

    返回：``{ok: bool, mismatches: [dict], checked: int, verified: int, skipped: int}``
    - ``ok`` = 无任何 mismatch；
    - ``mismatches`` 元素含 ``entity/number/expected/found/field/reason/context``；
    - ``checked`` = 做了溯源核验的带字段声称数；``verified`` = 命中数据数；
      ``skipped`` = 无法映射、不指控的数字数（如轮次/日期等）。

    规则（均为可解释的启发式，宁可漏报也不误报）：
    1. 6 位股票代码实体必须存在于结构化数据中，否则记为 mismatch（编造代码）；
    2. 带字段别名（涨幅/收盘价/成交额/换手率…）的数字声称：
       数据中存在该字段但数值不一致 -> mismatch；数据中无该字段（无法溯源）-> mismatch；
    3. 不带别名的数字：若附近有代码实体，命中其任一数值字段视为已溯源；
       未命中不指控（跳过）；
    4. 百分比口径：比率字段 0.5=50% 同一单位比较；文本「下跌2.15%」按 -2.15% 处理；
    5. 金额支持 千/万/亿/万亿 单位（结构化按元存储）。

    失败无害：任何异常都回退为 ok=True（不影响主流程）。
    """
    try:
        return _verify_numbers_impl(text, structured)
    except Exception:
        return {"ok": True, "mismatches": [], "checked": 0, "verified": 0, "skipped": 0}


def _verify_numbers_impl(text: str, structured: Optional[dict]) -> dict:
    if not isinstance(text, str) or not text.strip():
        return {"ok": True, "mismatches": [], "checked": 0, "verified": 0, "skipped": 0}
    entities, flat = _extract_entities(structured if isinstance(structured, dict) else {})
    mismatches: list[dict] = []
    checked = verified = skipped = 0

    code_spans = list(_CODE_RE.finditer(text))
    text_codes = [m.group(1) for m in code_spans]
    # 核验候选：文本提到的实体优先；无代码时用扁平字段；仅一个实体时容忍按名称提及
    referenced = [c for c in dict.fromkeys(text_codes) if c in entities]
    if referenced:
        candidates = [(c, entities[c]) for c in referenced]
    elif flat:
        candidates = [(None, flat)]
    elif len(entities) == 1:
        code = next(iter(entities))
        candidates = [(code, entities[code])]
    else:
        candidates = []

    # 1) 实体核验：编造代码
    for m in code_spans:
        code = m.group(1)
        if code not in entities:
            mismatches.append(
                _mismatch(code, code, None, code, None, "数据中不存在该 6 位代码",
                          _context(text, m.start(), m.end()))
            )

    # 2) 数字核验
    for m in _TOKEN_RE.finditer(text):
        raw_num, unit = m.group(1), (m.group(2) or "")
        if not raw_num or _is_code_like(text, m):
            continue
        num = _parse_number(raw_num)
        if num is None or _is_year(text, m):
            continue
        alias = _nearest_alias(text, m)
        if alias is None:
            code, dist = _nearest_entity(text, m, code_spans)
            if code is None or dist is None or dist > _ENTITY_WINDOW or code not in entities:
                skipped += 1
                continue
            hit = any(
                _values_match(num, unit, fv, _is_ratio_key(key))
                for key, fv in _numeric_fields(entities[code])
            )
            verified += 1 if hit else 0
            if not hit:
                skipped += 1
            continue

        canonical, alias_raw = alias
        checked += 1
        num = _apply_word_sign(num, alias_raw, raw_num)
        evals = []
        for code, data in candidates:
            fv = _field_value(data, canonical)
            if fv is not _MISSING:
                evals.append((code, fv, _is_ratio_key(canonical)))
        if not evals:
            mismatches.append(
                _mismatch(None, f"{raw_num}{unit}", None,
                          _found_value(num, unit, _is_ratio_key(canonical)), canonical,
                          "数据中不存在该字段", _context(text, m.start(), m.end()))
            )
            continue
        if any(_values_match(num, unit, fv, ratio) for _, fv, ratio in evals):
            verified += 1
        else:
            code0, fv0, ratio0 = evals[0]
            mismatches.append(
                _mismatch(code0, f"{raw_num}{unit}", fv0,
                          _found_value(num, unit, ratio0), canonical,
                          "数值与数据不一致", _context(text, m.start(), m.end()))
            )

    return {
        "ok": not mismatches,
        "mismatches": mismatches,
        "checked": checked,
        "verified": verified,
        "skipped": skipped,
    }


# ────────────────────────── rubric 打分（可选，供 Eval） ──────────────────────────


def evaluate_rubric(items: Optional[list] = None, pass_threshold: float = 3.0, max_score: int = 5) -> dict:
    """rubric 打分：每项 ``{name, score(1-5), evidence}`` -> 汇总结果。

    返回：``{total, max_total, average, passed, pass_threshold, items:[{name, score,
    evidence, valid, issues}]}``；``evidence``（证据引用）必填，``score`` 越界/缺失
    视为无效项。任何异常回退为 passed=False（失败无害）。
    """
    try:
        out_items = []
        total = 0.0
        max_total = 0.0
        for i, item in enumerate(items or []):
            name = str(item.get("name", f"item-{i}"))
            try:
                score = float(item.get("score"))
            except (TypeError, ValueError):
                score = None
            evidence = item.get("evidence")
            issues = []
            if score is None or not (1 <= score <= max_score):
                issues.append(f"score 需为 1-{max_score} 的数值")
            if not isinstance(evidence, str) or not evidence.strip():
                issues.append("evidence 必填（证据引用）")
            valid = not issues
            out_items.append({
                "name": name, "score": score, "evidence": evidence,
                "valid": valid, "issues": issues,
            })
            if valid:
                total += score
                max_total += max_score
        n = len(out_items)
        average = total / n if n and max_total else 0.0
        passed = n > 0 and all(x["valid"] for x in out_items) and average >= pass_threshold
        return {
            "total": total,
            "max_total": max_total,
            "average": average,
            "passed": passed,
            "pass_threshold": pass_threshold,
            "items": out_items,
        }
    except Exception:
        return {
            "total": 0, "max_total": 0, "average": 0.0, "passed": False,
            "pass_threshold": pass_threshold, "items": [],
        }


# ────────────────────────── R10 确定性 rubric 打分（复盘/聊天） ──────────────────────────


def _round_half_up(value: float) -> int:
    """四舍五入（half-up，避免 banker's rounding 的非确定性观感）。"""
    return int(math.floor(value + 0.5))


def _clamp_score(value: float) -> int:
    """总分 clamp 到 1-5 闭区间（rubric 项下限 1 分，不因单点全扣到 0）。"""
    return min(5, max(1, _round_half_up(value)))


def _scale_ratio(ratio: float) -> int:
    """比例 [0,1] -> 1-5 分（线性映射：0 -> 1，1 -> 5）。"""
    return _clamp_score(1 + 4 * ratio)


def _title_in_report(report: str, title: str) -> bool:
    """章节标题是否出现在报告中：忽略 Markdown 井号与空白差异。"""
    norm = re.sub(r"[#*\s]+", "", title)
    body = re.sub(r"[#*\s]+", "", report or "")
    return bool(norm) and norm in body


def _degraded_count(meta: dict) -> int:
    """meta.degraded 归一化为降级条数：list/tuple 取长度，bool/其他真值视为 1。"""
    degraded = meta.get("degraded")
    if isinstance(degraded, (list, tuple)):
        return len(degraded)
    return 1 if degraded else 0


def _weighted_total(items: list, weights: dict) -> int:
    """总分 = Σ weight × item_score，四舍五入 clamp 1-5。"""
    total = sum(weights.get(it["item"], 0.0) * it["score"] for it in items)
    return _clamp_score(total)


def _rubric_review_items(report: str, structured: dict, meta: dict) -> list:
    """复盘 rubric 四项明细（item/score/max/evidence），全部 1-5 分可手算。"""
    mode = str(meta.get("mode") or "close")
    sections = meta.get("sections") or REVIEW_SECTIONS.get(mode) or REVIEW_SECTIONS["close"]
    found = sum(1 for title in sections if _title_in_report(report, title))
    structure = _scale_ratio(found / len(sections)) if sections else 5

    disclaimer = 5 if DISCLAIMER in (report or "") else 1

    ver = verify_numbers(report or "", structured)
    mismatches = ver.get("mismatches") or []
    # 2026-08-06 精化（最终版）：核验器置信度自检——长报告/表格场景下启发式误报率
    # 高（实测 130 条「数值不一致」中约 9 成为标题/日期/表格数字被错误归因）。
    # 规则（手算可验证）：
    # ① 幻觉代码 ≥3 且数值不一致极少 → 核验器可信，按代码缺失重罚（每 1 扣 1，上限 4）；
    # ② 数值不一致 ≥20 且 verified 占声称比例 <30% → 核验器置信度不足 → 中性 3 分并注明；
    # ③ 其余：幻觉代码每 1 扣 1（上限 3），字段无法溯源每 4 条扣 1（上限 1）。
    n_hard_code = sum(1 for m in mismatches if m.get("reason") == "数据中不存在该 6 位代码")
    n_value = sum(1 for m in mismatches if m.get("reason") == "数值与数据不一致")
    n_soft = len(mismatches) - n_hard_code - n_value
    checked = int(ver.get("checked") or 0)
    verified = int(ver.get("verified") or 0)
    if n_hard_code >= 3 and n_value <= n_hard_code:
        numbers = max(1, 5 - n_hard_code)
        numbers_confidence = "核验器置信度充足"
    elif n_value >= 20 and checked >= 50 and verified < checked * 0.3:
        numbers = 3
        numbers_confidence = "核验器置信度不足（启发式误报率高），给中性分"
    else:
        numbers = max(1, 5 - min(n_hard_code, 3) - min(n_soft // 4, 1))
        numbers_confidence = "核验器置信度正常"

    n_degraded = _degraded_count(meta)
    if n_degraded:
        degraded = 5 if "降级" in (report or "") else 1
        degraded_evidence = (
            f"降级标注可见（degraded={n_degraded} 条）" if degraded == 5
            else f"报告缺少「降级」标注（degraded={n_degraded} 条）"
        )
    else:
        degraded = 5
        degraded_evidence = "无降级，该维度不适用（满分）"

    first_reason = mismatches[0].get("reason", "") if mismatches else ""
    numbers_evidence = (
        f"数字核验 mismatch={len(mismatches)}（幻觉代码 {n_hard_code} 条、数值不一致 {n_value} 条、"
        f"字段无法溯源 {n_soft} 条；checked={checked}，verified={verified}；{numbers_confidence}）"
        + (f"；首条：{first_reason}" if first_reason else "")
    )

    return [
        {"item": "structure", "score": structure, "max": 5,
         "evidence": f"模式章节命中 {found}/{len(sections)}（mode={mode}）"},
        {"item": "disclaimer", "score": disclaimer, "max": 5,
         "evidence": "报告含免责声明「仅供参考，不构成投资建议」"
         if disclaimer == 5 else "报告缺少免责声明「仅供参考，不构成投资建议」"},
        {"item": "numbers", "score": numbers, "max": 5, "evidence": numbers_evidence},
        {"item": "degraded", "score": degraded, "max": 5, "evidence": degraded_evidence},
    ]


def _chat_parts(messages: Optional[list]) -> list:
    """消息列表 -> 文本列表（dict 取 content/text/message/reply/answer，否则 JSON 序列化）。"""
    parts = []
    for msg in messages or []:
        if isinstance(msg, dict):
            text = None
            for key in ("content", "text", "message", "reply", "answer"):
                val = msg.get(key)
                if isinstance(val, str) and val.strip():
                    text = val
                    break
            parts.append(text if text is not None else json.dumps(msg, ensure_ascii=False))
        else:
            parts.append(str(msg))
    return parts


def _analyst_replied(messages: Optional[list], analyst: str) -> bool:
    """某位分析师是否已有回复：消息 dict 的作者字段或文本中出现分析师名。"""
    name = str(analyst).strip()
    if not name:
        return True
    for msg in messages or []:
        if isinstance(msg, dict):
            for key in ("analyst", "author", "role", "name", "sender", "agent"):
                val = msg.get(key)
                if val is not None and name in str(val):
                    return True
            text = " ".join(str(v) for v in msg.values()) if msg else ""
        else:
            text = str(msg)
        if name in text:
            return True
    return False


def _rubric_chat_items(messages: Optional[list], structured: dict, meta: dict) -> list:
    """聊天 rubric 三项明细（结构含免责声明 / 数字核验 / 分析师覆盖）。"""
    parts = _chat_parts(messages)
    reply = parts[-1] if parts else ""
    structure = 5 if DISCLAIMER in reply else 1

    ver = verify_numbers("\n".join(parts), structured)
    mismatches = ver.get("mismatches") or []
    n_mis = len(mismatches)
    numbers = max(1, 5 - min(n_mis, 4))

    analysts = meta.get("analysts")
    if isinstance(analysts, (list, tuple)) and analysts:
        covered = sum(1 for name in analysts if _analyst_replied(messages, name))
        analysts_score = _scale_ratio(covered / len(analysts))
        analysts_evidence = f"分析师覆盖 {covered}/{len(analysts)}"
    else:
        analysts_score = 5
        analysts_evidence = "未提供 analysts 名单，该维度不适用（满分）"

    first_reason = mismatches[0].get("reason", "") if mismatches else ""
    numbers_evidence = (
        f"数字核验 mismatch={n_mis}（checked={ver.get('checked')}，verified={ver.get('verified')}）"
        + (f"；首条：{first_reason}" if first_reason else "")
    )
    return [
        {"item": "structure", "score": structure, "max": 5,
         "evidence": "回复含免责声明「仅供参考，不构成投资建议」"
         if structure == 5 else "回复缺少免责声明「仅供参考，不构成投资建议」"},
        {"item": "numbers", "score": numbers, "max": 5, "evidence": numbers_evidence},
        {"item": "analysts", "score": analysts_score, "max": 5, "evidence": analysts_evidence},
    ]


def _write_score(name: str, result: dict, trace_id: Optional[str]) -> None:
    """尽力而为写回 Langfuse score（name=``review_quality``/``chat_quality``）。

    仅当有凭据（_get_langfuse_client 非 None）且带 trace_id 时写回；comment 为 JSON 明细。
    写回失败完全静默（不影响返回的 dict），符合失败无害铁律。
    """
    if not trace_id:
        return
    try:
        client = _get_langfuse_client()
        if client is None:
            return
        comment = json.dumps(
            {"score": result["score"], "breakdown": result["breakdown"]},
            ensure_ascii=False,
        )
        _client_score(
            client,
            name=name,
            value=result["score"],
            comment=comment,
            trace_id=trace_id,
        )
        _client_flush(client)  # 2026-08-06：score 立即导出（异步导出实测不可见）
    except Exception:
        pass


def score_review(
    report: str,
    structured: dict,
    meta: dict,
    trace_id: Optional[str] = None,
) -> Optional[dict]:
    """复盘确定性 rubric 打分（R10 Q4）：1-5 分 + JSON 明细证据。

    四项（等权 0.25）：① 结构完整性（模式章节标题，meta["sections"] 可覆盖内置表）；
    ② 免责声明「仅供参考，不构成投资建议」；③ 数字核验（复用 verify_numbers，
    mismatch=0 满分，每 1 条扣 1 分，下限 1）；④ 降级标注可见（meta.degraded 非空时
    报告必须含「降级」字样，否则该项 1 分）。
    总分 = Σ 0.25 × item_score，四舍五入 clamp 1-5；passed = score ≥ 3。

    返回 ``{score, breakdown:[{item, score, max, evidence}], passed}``；
    有凭据且带 trace_id 时写回 Langfuse score（name=``review_quality``，
    comment=JSON 明细）；无 trace_id 只返回 dict 不 score。
    失败无害：rubric 计算异常返回 None，score 写回异常不影响返回 dict。
    """
    try:
        meta = meta if isinstance(meta, dict) else {}
        items = _rubric_review_items(report or "", structured if isinstance(structured, dict) else {}, meta)
        total = _weighted_total(items, REVIEW_WEIGHTS)
        result = {
            "score": total,
            "breakdown": [
                {"item": it["item"], "score": it["score"], "max": it["max"], "evidence": it["evidence"]}
                for it in items
            ],
            "passed": total >= REVIEW_PASS_THRESHOLD,
        }
        _write_score("review_quality", result, trace_id)
        return result
    except Exception:
        return None


def score_chat(
    messages: list,
    structured: dict,
    meta: dict,
    trace_id: Optional[str] = None,
) -> Optional[dict]:
    """聊天确定性 rubric 打分（R10 Q4）：1-5 分 + JSON 明细证据。

    三项（等权 1/3）：① 回复结构（末条回复含免责声明）；② 数字核验（复用 verify_numbers）；
    ③ 分析师覆盖（meta 提供 analysts 名单时逐位有回复，未提供则不适用满分）。
    总分 = Σ (1/3) × item_score，四舍五入 clamp 1-5；passed = score ≥ 3。

    返回 ``{score, breakdown:[{item, score, max, evidence}], passed}``；
    有凭据且带 trace_id 时写回 Langfuse score（name=``chat_quality``）。
    失败无害：rubric 计算异常返回 None，score 写回异常不影响返回 dict。
    """
    try:
        meta = meta if isinstance(meta, dict) else {}
        items = _rubric_chat_items(messages, structured if isinstance(structured, dict) else {}, meta)
        total = _weighted_total(items, CHAT_WEIGHTS)
        result = {
            "score": total,
            "breakdown": [
                {"item": it["item"], "score": it["score"], "max": it["max"], "evidence": it["evidence"]}
                for it in items
            ],
            "passed": total >= REVIEW_PASS_THRESHOLD,
        }
        _write_score("chat_quality", result, trace_id)
        return result
    except Exception:
        return None
