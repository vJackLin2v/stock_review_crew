"""R11 R9 联网搜索：垂直多源聚合（无 Key，2026-08-08 定案，见 requirements-r11 §七 Q5）。

能力：
- ``search_stock_news(code)``：东财公告接口（np-anotice-stock，无 Referer 也可，加 UA），
  返回最近 10-15 条公告（title/notice_date/url）；
- ``filter_news_by_keywords(news_list, keywords)``：对已有快讯（新浪 7x24，复用
  ``realtime.fetch_news`` 链路）按关键词过滤；
- ``search(query, code=None)``：编排——解析出的股票代码优先公告，其余关键词过滤快讯；
  返回 ``{"ok", "results": [{source, title, date, url?, summary?}], "summary": None}``；
  全失败 → ``ok=False``；
- BOCHA_API_KEY 存在且 ``config.BOCHA_SEARCH_ENABLED=true`` 时才做全网补充（默认关）。

降级铁律：所有请求 ≤15s 超时；异常绝不抛出（一律转 ``ok=False`` 由调用方标注
「（未联网搜索）」继续流程）。
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Optional

SEARCH_TIMEOUT = 15
_EM_ANNOUNCE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_ZH_WORD_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def extract_stock_code(query: str) -> Optional[str]:
    """从查询文本中提取 6 位股票代码；无则返回 None。"""
    m = _CODE_RE.search(str(query or ""))
    return m.group(1) if m else None


def _extract_keywords(query: str, code: Optional[str] = None) -> list[str]:
    """从查询中提取关键词：去除代码后取 2 字以上中文片段（去重、限长）。"""
    q = str(query or "").strip()
    if code:
        q = q.replace(code, " ")
    words = []
    for m in _ZH_WORD_RE.findall(q):
        words.append(m)
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return seen[:5]


def search_stock_news(code: str, page_size: int = 15) -> list[dict]:
    """东财公告：按股票代码拉最近公告（无 Key、无 Referer，仅 UA）。

    返回 ``[{source, title, date, url, summary: None}]``；接口/解析失败抛异常，
    由 ``search`` 统一降级为 ``ok=False``。
    """
    code = str(code or "").strip().zfill(6)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"非法股票代码：{code}")
    url = (
        f"{_EM_ANNOUNCE_URL}?sr=-1&page_size={int(page_size)}&page_index=1"
        f"&ann_type=A&client_source=web&stock_list={code}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    items = ((data.get("data") or {}).get("list")) or []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        url_value = str(item.get("url") or "").strip()
        if url_value.startswith("//"):
            url_value = "https:" + url_value
        notice = str(item.get("notice_date") or "").strip()
        out.append(
            {
                "source": "东财公告",
                "title": title,
                "date": notice[:10] if notice else None,
                "url": url_value or None,
                "summary": None,
            }
        )
    return out[: max(1, int(page_size))]


def filter_news_by_keywords(news_list: Any, keywords: list[str]) -> list[dict]:
    """对已有快讯按关键词过滤：任一词出现在标题/正文即保留。

    ``news_list`` 支持 realtime.fetch_news() 的 ``{"news": [...]}`` 字典、
    ``[{text, ...}]`` 字典列表或字符串列表（兼容三种形态）。
    """
    items: list[Any] = []
    if isinstance(news_list, dict):
        items = news_list.get("news") or []
    elif isinstance(news_list, list):
        items = news_list
    words = [str(k or "").strip() for k in (keywords or []) if str(k or "").strip()]
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("title") or "")
            wrapped = item
        else:
            text = str(item)
            wrapped = {"text": text, "source": "新浪7x24"}
        if not text:
            continue
        if not words or any(w in text for w in words):
            out.append(wrapped)
    return out


def _bocha_enabled() -> bool:
    """博查全网补充开关：需同时满足 Key 存在 + BOCHA_SEARCH_ENABLED=true（默认关）。"""
    try:
        from stock_review_crew import config

        return bool(getattr(config, "BOCHA_API_KEY", None) and getattr(config, "BOCHA_SEARCH_ENABLED", False))
    except Exception:  # noqa: BLE001 - 配置异常按关闭处理
        return False


def _search_bocha(query: str) -> list[dict]:
    """可选全网补充：博查 web-search（默认关）；任何异常抛出由调用方降级。"""
    from stock_review_crew import config

    payload = json.dumps(
        {"query": str(query)[:120], "freshness": "oneDay", "summary": True, "count": 5},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.bochaai.com/v1/web-search",
        data=payload,
        headers={
            "User-Agent": _UA,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.BOCHA_API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    out: list[dict] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        title = str(page.get("name") or "").strip()
        if not title:
            continue
        out.append(
            {
                "source": "博查全网",
                "title": title,
                "date": str(page.get("datePublished") or "")[:10] or None,
                "url": str(page.get("url") or "") or None,
                "summary": str(page.get("snippet") or page.get("summary") or "")[:200] or None,
            }
        )
    return out[:5]


def search(query: str, code: Optional[str] = None) -> dict:
    """R9 编排：代码优先公告，其余关键词过滤快讯；全失败 → ok=False，绝不抛异常。

    返回 ``{"ok": bool, "results": [...], "summary": None}``；每条结果为
    ``{source, title, date, url?, summary?}``。
    """
    results: list[dict] = []
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "results": [], "summary": None}
    stock_code = str(code or "").strip().zfill(6) if code else extract_stock_code(query)

    # 1) 代码 → 东财公告（垂直源一，无 Key）
    if stock_code:
        try:
            results.extend(search_stock_news(stock_code))
        except Exception:  # noqa: BLE001 - 降级：公告失败不影响快讯/博查
            pass

    # 2) 关键词 → 新浪 7x24 快讯（复用 realtime 链路）
    keywords = _extract_keywords(query, stock_code)
    if keywords:
        try:
            from .realtime import fetch_news

            raw_news = fetch_news()
            results.extend(filter_news_by_keywords(raw_news, keywords))
        except Exception:  # noqa: BLE001 - 快讯失败降级
            pass

    # 3) 可选全网补充（默认关）
    if _bocha_enabled():
        try:
            results.extend(_search_bocha(query))
        except Exception:  # noqa: BLE001 - 全网补充失败降级
            pass

    # 去重（同标题保留首条）并截断
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in results:
        key = str(r.get("title") or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return {
        "ok": bool(deduped),
        "results": deduped[:15],
        "summary": None,
    }


__all__ = [
    "search",
    "search_stock_news",
    "filter_news_by_keywords",
    "extract_stock_code",
    "SEARCH_TIMEOUT",
]
