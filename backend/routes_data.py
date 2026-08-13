"""行情数据/股票名称/自选路由模块（APIRouter，import jobs/底层模块，不 import main）。

从 backend/main.py 拆分（2026-08-13，纯重构不改行为）：
- GET /api/data/realtime、/api/data/stock/{code}、/api/data/quote、/api/data/search、
  POST /api/data/stock-names（M5 批量代码→名称：_stock_name_tx_symbol /
  _stock_names_via_tx_batch / resolve_stock_names / _name_cache）；
- R10（M2）：market-weather / us-nasdaq / sector-heat / extra-indices /
  market-sentiment / market-style（走 jobs._market_extra_call 60s 缓存）；
- 观察列表：GET/POST /api/watchlist、DELETE /api/watchlist/{code}、
  PUT /api/watchlist/order。
"""

from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Response

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.jobs import _host, _market_extra_call, _validate_date  # noqa: E402

logger = logging.getLogger("stock_review_crew.backend")

router = APIRouter()


@router.get("/api/data/realtime")
def realtime_snapshot() -> dict[str, Any]:
    """盘中实时数据快照（指数/涨跌停统计/板块资金流/快讯）。
    同步 def → FastAPI 线程池执行，不阻塞事件循环；单块失败独立降级标注。"""
    from stock_review_crew.tools.realtime import fetch_realtime_snapshot

    try:
        data = fetch_realtime_snapshot()
        # R9：指数涨跌家数补充（通达信协议，60s 缓存；失败不影响主数据）
        try:
            from stock_review_crew.tools.realtime import enrich_indices_updown

            data = enrich_indices_updown(data)
        except Exception:  # noqa: BLE001 - enrichment 失败仅跳过
            pass
        return data
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"实时数据获取失败：{exc}") from exc


@router.get("/api/data/stock/{code}")
def stock_detail(code: str, date: str = "", days: int = 60) -> dict[str, Any]:
    """R9 个股详情聚合：实时快照/日K/分时/5分钟K/1分钟K/均线，各块独立降级标注。"""
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="股票代码应为 6 位数字，如 600519")
    if days < 5 or days > 250:
        raise HTTPException(status_code=400, detail="days 应在 5-250 之间")
    from stock_review_crew.tools.stock_data import DataSourceError, fetch_stock_detail

    try:
        return fetch_stock_detail(code, date=date.strip() or None, days=days)
    except DataSourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"个股详情获取失败：{exc}") from exc


@router.get("/api/data/quote")
def realtime_quote(code: str = "") -> dict[str, Any]:
    """个股实时行情（东财 push2；代码 6 位数字校验）。"""
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="股票代码应为 6 位数字，如 600519")
    from stock_review_crew.tools.realtime import RealtimeError, fetch_stock_quote

    try:
        return fetch_stock_quote(code)
    except RealtimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"个股行情获取失败：{exc}") from exc


@router.get("/api/data/search")
def stock_search(q: str = "") -> list[dict[str, Any]]:
    """股票名称/代码搜索（东财 suggest → 腾讯 smartbox），供个股查询用名称输入。"""
    q = str(q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="请输入股票名称或代码")
    from stock_review_crew.tools.realtime import RealtimeError, search_stocks

    try:
        return search_stocks(q)
    except RealtimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"股票搜索失败：{exc}") from exc


# ---------- M5：股票代码批量转名称（2026-08-12 新增，仅供前端展示用） ----------
_STOCK_CODE_RE = re.compile(r"^\d{6}$")
_NAME_CACHE_TTL_SECONDS = 5.0
# 名称缓存：code -> (name|None, 命中时刻)。5 秒 TTL，聊天/报告渲染高频去重、防限流；
# 失败也缓存 None，避免同一批无效代码反复打数据源。
_name_cache: dict[str, tuple[Optional[str], float]] = {}


def _stock_name_tx_symbol(code: str) -> str:
    """转腾讯行情代码格式（与 tools.realtime._tx_symbol 同口径：6/9/5→sh、4/8/92→bj、其余→sz）。"""
    code = str(code or "").strip().zfill(6)
    if code.startswith(("6", "9", "5")):  # 5xx=沪市基金/ETF
        return f"sh{code}"
    if code.startswith(("4", "8", "92")):
        return f"bj{code}"
    return f"sz{code}"


def _stock_names_via_tx_batch(codes: list[str]) -> dict[str, str]:
    """腾讯 qt.gtimg.cn 一次请求批量取名称（realtime.fetch_stock_quote 腾讯备用链同源，实测可用）。
    返回 {code: name}，未命中的代码不出现；失败抛异常由调用方兜底。"""
    import requests

    resp = requests.get(
        f"https://qt.gtimg.cn/q={','.join(_stock_name_tx_symbol(c) for c in codes)}",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=8,
    )
    resp.raise_for_status()
    text = resp.text
    out: dict[str, str] = {}
    for code in codes:
        sym = _stock_name_tx_symbol(code)
        m = re.search(rf'v_{re.escape(sym)}="([^"]*)"', text)
        if not m:
            continue
        parts = m.group(1).split("~")
        if len(parts) >= 2 and parts[1]:
            out[code] = str(parts[1]).strip()
    return out


def resolve_stock_names(codes: list[str]) -> dict[str, Optional[str]]:
    """批量股票代码 → 名称：无效代码忽略、重复去重；缓存优先（5s TTL）；
    腾讯批量行情 → 单代码实时行情兜底；查不到返回 None，绝不抛错。"""
    now = time.time()
    valid: list[str] = []
    seen: set[str] = set()
    for code in codes:
        c = str(code or "").strip()
        if c and c not in seen and _STOCK_CODE_RE.match(c):
            seen.add(c)
            valid.append(c)
    out: dict[str, Optional[str]] = {}
    missing: list[str] = []
    ttl_seconds = _host("_NAME_CACHE_TTL_SECONDS")  # 测试可 monkeypatch backend.main._NAME_CACHE_TTL_SECONDS
    for c in valid:
        hit = _name_cache.get(c)
        if hit is not None and now - hit[1] < ttl_seconds:
            out[c] = hit[0]
        else:
            missing.append(c)
    if not missing:
        return out
    try:
        names = _host("_stock_names_via_tx_batch")(missing)  # 测试可 monkeypatch backend.main._stock_names_via_tx_batch
    except Exception:  # noqa: BLE001 - 批量失败走单代码兜底
        names = {}
    if not isinstance(names, dict):
        names = {}
    for c in missing:
        name = names.get(c)
        if not name:
            try:
                from stock_review_crew.tools.realtime import fetch_stock_quote

                quote = fetch_stock_quote(c)
                name = (quote or {}).get("name")
            except Exception:  # noqa: BLE001 - 单代码失败返回 null，不抛错
                name = None
        if name is not None:
            name = str(name).strip() or None
        _name_cache[c] = (name, now)
        out[c] = name
    return out


@router.post("/api/data/stock-names")
def stock_names(payload: Any = Body(...)) -> dict[str, Any]:
    """批量股票代码 → 名称（M5）：{codes: [...]} → {names: {code: name|null}}。
    空数组 → {}；非 6 位代码忽略；重复去重；单代码失败返回 null，绝不抛错。"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    codes = payload.get("codes")
    if codes is None:
        raise HTTPException(status_code=400, detail="缺少参数：codes")
    if not isinstance(codes, list):
        raise HTTPException(status_code=400, detail="codes 必须是数组")
    return {"names": resolve_stock_names(codes)}


# ═══════════════════════════════════════════════════════════════
# R10（M2）：行情天气/纳指/板块热度/额外指数/风格坐标 + 观察列表
# ═══════════════════════════════════════════════════════════════

@router.get("/api/data/market-weather")
def market_weather(date: str = "") -> dict[str, Any]:
    """行情天气/情绪温度（R10 F3，P0）：date 可选（默认今日），透传 M1 契约结构。"""
    date = (date or "").strip() or datetime.now().strftime("%Y-%m-%d")
    date = _validate_date(date)
    return _market_extra_call("fetch_market_weather", "行情天气功能暂不可用", "行情天气获取失败", date)


@router.get("/api/data/us-nasdaq")
def us_nasdaq() -> dict[str, Any]:
    """美股·纳斯达克（R10 F6）：纳指综合 + 纳指 100，透传 M1 契约结构。"""
    return _market_extra_call("fetch_us_nasdaq", "纳斯达克行情功能暂不可用", "纳斯达克行情获取失败")


@router.get("/api/data/sector-heat")
def sector_heat() -> dict[str, Any]:
    """板块热度榜（R10 F5）：涨幅 top5 / 跌幅 top5，透传 M1 契约结构。"""
    return _market_extra_call("fetch_sector_heat", "板块热度功能暂不可用", "板块热度获取失败")


@router.get("/api/data/extra-indices")
def extra_indices() -> dict[str, Any]:
    """额外指数（R10 F2）：平均股价/微盘股/高股息，透传 M1 契约结构。"""
    return _market_extra_call("fetch_extra_indices", "额外指数功能暂不可用", "额外指数获取失败")


@router.get("/api/data/market-sentiment")
def market_sentiment() -> dict[str, Any]:
    """R12 统一情绪数据源：涨跌家数比/炸板率/连板高度等结构化指标（全局共用）。
    与市场天气同源三块，60s 缓存；绝不抛异常。"""
    return _market_extra_call("fetch_market_sentiment", "情绪指标暂不可用", "情绪指标获取失败",
                              datetime.now().strftime("%Y-%m-%d"))


@router.get("/api/data/market-style")
def market_style() -> dict[str, Any]:
    """风格坐标（R10 F4）：大盘 vs 小盘 / 价值 vs 成长，透传 M1 契约结构。"""
    return _market_extra_call("fetch_style_quad", "市场风格功能暂不可用", "市场风格获取失败")


def _watchlist_storage():
    from stock_review_crew.storage import watchlist

    return watchlist


def _resolve_stock_name(code: str) -> Any:
    """自选名称兜底：行情接口可取则取，失败/降级一律 None（前端显示「—」），绝不阻塞添加。"""
    try:
        from stock_review_crew.tools.realtime import fetch_stock_quote

        quote = fetch_stock_quote(code)
        name = (quote or {}).get("name")
        return str(name) if name else None
    except Exception:  # noqa: BLE001 - 名称解析失败不影响添加
        return None


@router.get("/api/watchlist")
def watchlist_list() -> list[dict[str, Any]]:
    """观察列表（R10 F9）：[{code, name, note, pinned, created_at}]。"""
    return _watchlist_storage().list_watchlist()


@router.post("/api/watchlist")
def watchlist_add(payload: Any = Body(...)) -> dict[str, Any]:
    """添加自选：Body {code, note?} → 200 记录；格式错 400 中文；重复 409 中文。"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    code = str(payload.get("code") or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="股票代码应为 6 位数字，如 600519")
    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(status_code=400, detail="note 必须为字符串")
    note = note.strip() if note else None
    name = _resolve_stock_name(code)
    try:
        record = _watchlist_storage().add_watchlist(code, note, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if record is False:
        raise HTTPException(status_code=500, detail="观察列表保存失败，请稍后重试")
    return record


@router.delete("/api/watchlist/{code}", status_code=204)
def watchlist_delete(code: str) -> Response:
    """删除自选：204 成功；格式错 400 中文；不存在 404 中文。"""
    if not re.fullmatch(r"\d{6}", str(code or "")):
        raise HTTPException(status_code=400, detail="股票代码应为 6 位数字，如 600519")
    result = _watchlist_storage().delete_watchlist(code)
    if result is None:
        raise HTTPException(status_code=404, detail="该股票不在观察列表中")
    if result is False:
        raise HTTPException(status_code=500, detail="观察列表保存失败，请稍后重试")
    return Response(status_code=204)


@router.put("/api/watchlist/order")
def watchlist_reorder(payload: Any = Body(...)) -> list[dict[str, Any]]:
    """观察列表重排：Body {codes: [...]} → 200 重排后完整列表；缺失/非法代码忽略。"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    codes = payload.get("codes")
    if not isinstance(codes, list):
        raise HTTPException(status_code=400, detail="codes 必须为股票代码列表")
    try:
        result = _watchlist_storage().reorder_watchlist(codes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is False:
        raise HTTPException(status_code=500, detail="观察列表保存失败，请稍后重试")
    return result
