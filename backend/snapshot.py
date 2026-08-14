"""快照转换模块：引擎快照（I2/I1 blocks）→ 前端契约快照（纯函数，不 import main/jobs）。

从 backend/main.py 拆分（2026-08-13，纯重构不改行为）。对齐 frontend/src/mock.js
buildSnapshot：顶层仅 index_minute / limit_ladder / sectors / sectors_bottom /
sentiment / breadth / source / degraded 键；比率一律小数；None 语义保留。
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════
# 快照对齐：引擎快照（I2/I1 blocks）→ 前端契约快照（normalize.js/mock.js 唯一真源）
# ═══════════════════════════════════════════════════════════════

_INDEX_NAMES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sh000300": "沪深300",
}


def _find_block(data: Any, names: tuple[str, ...]) -> Any:
    if not isinstance(data, dict):
        return None
    for key in names:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pick_int(*values: Any) -> Optional[int]:
    for v in values:
        n = _as_int(v)
        if n is not None:
            return n
    return None


def _ratio(value: Any, max_abs: float = 1.5) -> Optional[float]:
    """比率归一：非有限数 → None；疑似百分数（>max_abs）÷100 为小数。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if abs(v) > max_abs:
        return v / 100.0
    return v


def _pick_ratio(src: Any, keys: tuple[str, ...], max_abs: float = 1.5) -> Optional[float]:
    if not isinstance(src, dict):
        return None
    for key in keys:
        if src.get(key) is not None:
            value = src[key]
            if str(key).endswith("_pct"):  # 上游百分数字段（如 red_rate_pct）→ 小数
                try:
                    return float(value) / 100.0
                except (TypeError, ValueError):
                    return None
            return _ratio(value, max_abs=max_abs)
    return None


def _block_degraded(blk: Any) -> list[str]:
    if not isinstance(blk, dict):
        return []
    return [str(r) for r in (blk.get("degraded_reason") or []) if r]


def _snap_index_minute(data: Any) -> list[dict]:
    """指数分时：优先 I1 直接给出的 index_minute；否则从 minute 块 bars 组装。"""
    if isinstance(data, dict) and isinstance(data.get("index_minute"), list):
        return data["index_minute"]
    minute = _find_block(data, ("minute", "fenshi", "intraday", "minline", "index_minute_block"))
    if not isinstance(minute, dict):
        return []
    points = []
    for bar in minute.get("bars") or []:
        if not isinstance(bar, dict):
            continue
        t = bar.get("time") or bar.get("datetime")
        v = bar.get("close")
        if v is None:
            v = bar.get("value")
        if t is None or v is None:
            continue
        points.append({"time": str(t), "value": v})
    if not points:
        return []
    symbol = str(minute.get("symbol") or "")
    name = str(minute.get("name") or "") or _INDEX_NAMES.get(symbol, symbol or "指数")
    return [{"name": name, "points": points}]


def _ladder_label(key: Any) -> Optional[str]:
    k = str(key).strip()
    if k in ("首板", "1板", "1"):
        return "首板"
    if k in ("5板+", "5+", "≥5", "5板以上"):
        return "5板+"
    m = re.fullmatch(r"(\d+)板?", k)
    if m:
        n = int(m.group(1))
        return "5板+" if n >= 5 else f"{n}板"
    return None


def _ladder_stocks(sub: Any) -> list[str]:
    names = []
    for item in (sub.get("stocks") if isinstance(sub, dict) else None) or []:
        if isinstance(item, dict):
            name = item.get("名称") or item.get("name") or item.get("股票名称") or item.get("代码")
            if name is None:
                continue
            names.append(str(name))
        elif item:
            names.append(str(item))
    return names[:5]


def _ladder_rows(block: Any, direction: str) -> list[dict]:
    """涨跌停梯队：{label, count, stocks}，对齐 mock limit_ladder.up/down。"""
    if not isinstance(block, dict):
        return []
    sub = block.get("limit_up") if direction == "up" else block.get("limit_down")
    if not isinstance(sub, dict):
        sub = block  # 扁平结构兜底
    if not isinstance(sub, dict):
        return []
    tier = sub.get("tier") if isinstance(sub.get("tier"), dict) else {}
    total = _as_int(sub.get("count"))
    fallback_label = "涨停" if direction == "up" else "跌停"
    rows: list[dict] = []
    if tier:
        agg: dict[str, int] = {}
        for key, value in tier.items():
            label = _ladder_label(key)
            n = _as_int(value)
            if label and n is not None:
                agg[label] = agg.get(label, 0) + n
        for label in ("首板", "2板", "3板", "4板", "5板+"):
            if label in agg:
                rows.append(
                    {
                        "label": label,
                        "count": agg[label],
                        "stocks": _ladder_stocks(sub) if label == "首板" else [],
                    }
                )
    if not rows and total is not None:
        rows.append({"label": fallback_label, "count": total, "stocks": _ladder_stocks(sub)})
    return rows


def _snap_ladder(data: Any) -> dict:
    zt = _find_block(data, ("zhangting", "zt", "zt_pool", "limit_up"))
    dt = _find_block(data, ("dieting", "dt", "dt_pool", "limit_down"))
    # I1 的 zt_pool 块同时含 limit_up/limit_down；dieting 块缺失时从 zt_pool 兜底
    return {"up": _ladder_rows(zt, "up"), "down": _ladder_rows(dt if dt is not None else zt, "down")}


def _snap_sectors(data: Any) -> list[dict]:
    sec = _find_block(data, ("sectors", "sector", "board", "industry"))
    if not isinstance(sec, dict):
        return []
    items: list[Any] = []
    for key in ("top5", "top10", "list", "items"):
        if isinstance(sec.get(key), list):
            items = sec[key]
            break
    rows = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        name = item.get("板块") or item.get("name") or item.get("板块名称") or item.get("行业")
        if not name:
            continue
        pct = item.get("涨跌幅")
        if pct is None:
            pct = item.get("pct_change")
        leader = item.get("领涨股") or item.get("leader") or item.get("领涨") or ""
        rows.append({"name": str(name), "pct_change": _ratio(pct), "leader": str(leader)})
    rows.sort(key=lambda r: r["pct_change"] if r["pct_change"] is not None else -999.0, reverse=True)
    return rows


def _snap_sectors_bottom(data: Any) -> list[dict]:
    """板块跌幅榜（P1-K）：bottom5/bottom10 → 前端契约行（跌幅最大在前）。"""
    sec = _find_block(data, ("sectors", "sector", "board", "industry"))
    if not isinstance(sec, dict):
        return []
    items: list[Any] = []
    for key in ("bottom5", "bottom10", "bottom"):
        if isinstance(sec.get(key), list):
            items = sec[key]
            break
    rows = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        name = item.get("板块") or item.get("name") or item.get("板块名称") or item.get("行业")
        if not name:
            continue
        pct = item.get("涨跌幅")
        if pct is None:
            pct = item.get("pct_change")
        leader = item.get("领涨股") or item.get("leader") or item.get("领涨") or ""
        rows.append({"name": str(name), "pct_change": _ratio(pct), "leader": str(leader)})
    rows.sort(key=lambda r: r["pct_change"] if r["pct_change"] is not None else 999.0)
    return rows


def _snap_sentiment(data: Any) -> dict:
    sent = _find_block(data, ("sentiment", "emotion", "yesterday_zt"))
    zt = _find_block(data, ("zhangting", "zt", "zt_pool", "limit_up"))
    breadth = _find_block(data, ("breadth", "market_breadth", "up_down"))
    src = sent if isinstance(sent, dict) else {}

    zt_up = zt.get("limit_up") if isinstance(zt, dict) and isinstance(zt.get("limit_up"), dict) else None
    zt_down = zt.get("limit_down") if isinstance(zt, dict) and isinstance(zt.get("limit_down"), dict) else None
    limit_up_count = _pick_int(
        src.get("limit_up_count"),
        zt_up.get("count") if zt_up else None,
        breadth.get("limit_up") if isinstance(breadth, dict) else None,
    )
    limit_down_count = _pick_int(
        src.get("limit_down_count"),
        zt_down.get("count") if zt_down else None,
        breadth.get("limit_down") if isinstance(breadth, dict) else None,
    )
    break_rate = _pick_ratio(src, ("zhaban_rate", "zhaban_rate_pct"), 1.5)
    if break_rate is None:
        break_rate = _pick_ratio(zt, ("zhaban_rate", "zhaban_rate_pct"), 1.5)
    if break_rate is None:
        break_rate = _pick_ratio(breadth, ("zhaban_rate", "zhaban_rate_pct"), 1.5)

    degraded: list[str] = []
    for blk in (src, zt, breadth):
        for reason in _block_degraded(blk):
            if reason and reason not in degraded:
                degraded.append(reason)

    up_down_ratio = None
    if src.get("up_down_ratio") is not None:
        up_down_ratio = _ratio(src.get("up_down_ratio"), max_abs=999.0)

    return {
        "up_count": _pick_int(src.get("up_count")),
        "down_count": _pick_int(src.get("down_count")),
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "red_rate": _pick_ratio(src, ("red_rate", "red_rate_pct"), 1.5),
        "continue_rate": _pick_ratio(src, ("lianban_rate", "lianban_rate_pct", "continue_rate"), 1.5),
        "break_rate": break_rate,
        "button_rate": _pick_ratio(src, ("hean_rate", "hean_rate_pct", "button_rate"), 1.5),
        "avg_return": _pick_ratio(src, ("avg_return", "avg_return_pct"), 1.0),
        "up_down_ratio": up_down_ratio,
        "source": str(
            src.get("source")
            or (zt.get("source") if isinstance(zt, dict) else None)
            or (breadth.get("source") if isinstance(breadth, dict) else None)
            or "数据缺失"
        ),
        "degraded": degraded,
    }


def _snap_breadth(data: Any) -> Optional[dict]:
    """涨跌家数快照：保留 up/down/limit_up/limit_down/涨跌比（报告正文「上涨X家、
    下跌X家」与前端 KPI 用）；「9 档分布 distribution」已移除（2026-08-13）。"""
    blk = _find_block(data, ("breadth", "market_breadth", "up_down"))
    if not isinstance(blk, dict):
        return None
    return {
        "total": _as_int(blk.get("total")),
        "up": _as_int(blk.get("up")),
        "down": _as_int(blk.get("down")),
        "flat": _as_int(blk.get("flat")),
        "limit_up": _pick_int(blk.get("limit_up")),
        "limit_down": _pick_int(blk.get("limit_down")),
        "up_down_ratio": _pick_ratio(blk, ("up_down_ratio",), 999.0),
        "zhaban_rate": _pick_ratio(blk, ("zhaban_rate", "zhaban_rate_pct"), 1.5),
        "source": str(blk.get("source") or "数据缺失"),
        "degraded": _block_degraded(blk),
        # P1-H：备用源/降级时 note 携带「分布明细缺失」等说明，必须透传前端展示
        "note": str(blk.get("note") or ""),
    }


def _to_frontend_snapshot(engine_snapshot: Any) -> dict:
    """引擎快照（{date, mode, source, data: blocks, degraded}）→ 前端契约快照。

    对齐 frontend/src/mock.js buildSnapshot：顶层仅 index_minute / limit_ladder / sectors /
    sectors_bottom / sentiment / breadth / source / degraded 键；比率一律小数；None 语义保留。
    """
    raw = engine_snapshot if isinstance(engine_snapshot, dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    sentiment = _snap_sentiment(data)
    degraded = [str(d) for d in (raw.get("degraded") or []) if d]
    for reason in sentiment.get("degraded") or []:
        if reason and reason not in degraded:
            degraded.append(reason)
    return {
        "index_minute": _snap_index_minute(data),
        "limit_ladder": _snap_ladder(data),
        "sectors": _snap_sectors(data),
        "sectors_bottom": _snap_sectors_bottom(data),
        "sentiment": sentiment,
        "breadth": _snap_breadth(data),
        "source": str(raw.get("source") or sentiment.get("source") or "数据缺失"),
        "degraded": degraded,
    }
