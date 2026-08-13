# -*- coding: utf-8 -*-
"""同花顺数据中心免费接口爬虫（无需 JS 签名 / 无需登录）。

实测（2026-08-03）：``data.10jqka.com.cn/dataapi/limit_up/limit_up_pool`` 与
``continuous_limit_pool`` 仅需普通 UA/Referer 即可返回真实 JSON，
绕开 akshare 依赖 py_mini_racer 的 ths.js 签名（Windows IOCP 退出死锁已禁用）。

接口约定（契约 §六）：
- 比率一律小数（0.5=50%）；金额元；None=无数据；
- 每个数据块携带 source="同花顺" 与 degraded 标记；
- 超时 ≤15s、重试 2 次；失败抛 ``ThsError``（中文），由数据层降级链处理。
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import requests

_BASE = "https://data.10jqka.com.cn/dataapi"
_TIMEOUT = 15
_RETRIES = 2
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Referer": "https://data.10jqka.com.cn/"}


class ThsError(RuntimeError):
    """同花顺爬虫错误（中文消息，供降级链标注）。"""


def _http_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    last_exc: Optional[Exception] = None
    for attempt in range(_RETRIES):
        try:
            resp = requests.get(_BASE + path, params=params, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ProxyError:
            # 代理客户端未启动/挂掉：直连重试（国内接口直连可用）
            try:
                resp = requests.get(_BASE + path, params=params, headers=_HEADERS,
                                    timeout=_TIMEOUT, proxies={"http": None, "https": None})
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < _RETRIES - 1:
                    time.sleep(0.8)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < _RETRIES - 1:
                time.sleep(0.8)
    raise ThsError(f"同花顺接口请求失败：{last_exc}")


def _fetch_all(path: str, params: dict[str, Any], max_pages: int = 6) -> dict[str, Any]:
    """分页拉取（接口单页上限 50 条，limit 过大返回 status_code=-1 空数据）。"""
    merged: list[dict[str, Any]] = []
    payload: dict[str, Any] = {}
    for page in range(1, max_pages + 1):
        data = _http_json(path, {**params, "page": page, "limit": 50})
        if data.get("status_code") not in (0, None):
            raise ThsError(f"同花顺接口返回异常：status_code={data.get('status_code')}")
        payload = data.get("data") or {}
        info = payload.get("info") or []
        merged.extend(info)
        total = ((payload.get("page") or {}).get("total")) if isinstance(payload.get("page"), dict) else None
        if not info or (total is not None and len(merged) >= int(total)):
            break
    payload = dict(payload)
    payload["info"] = merged
    return payload


def _norm_stock(row: dict[str, Any]) -> dict[str, Any]:
    """单行归一：比率小数、代码补零、None 保留。"""
    try:
        change_rate = float(row.get("change_rate"))
    except (TypeError, ValueError):
        change_rate = None
    return {
        "code": str(row.get("code") or "").zfill(6),
        "name": row.get("name"),
        "pct_change": round(change_rate / 100.0, 6) if change_rate is not None else None,
        "market_type": row.get("market_type"),
        "change_tag": row.get("change_tag"),
        "is_again_limit": row.get("is_again_limit"),
        "raw": {k: v for k, v in row.items() if k in ("code", "name", "change_rate", "market_type", "change_tag", "is_again_limit", "is_new", "high_days_value")},
    }


def fetch_limit_pool(date: str) -> dict[str, Any]:
    """同花顺涨停池：涨停个股 + 今日/昨日封板与开板统计（炸板率可算）。"""
    payload = _fetch_all(
        "/limit_up/limit_up_pool",
        {"field": 199112, "order": "desc", "date": date.replace("-", "")},
    )
    info = payload.get("info") or []
    stocks = [_norm_stock(r) for r in info]
    return {
        "count": len(stocks),
        "stocks": stocks,
        "today": payload.get("limit_up_count", {}).get("today") if isinstance(payload.get("limit_up_count"), dict) else None,
        "yesterday": payload.get("limit_up_count", {}).get("yesterday") if isinstance(payload.get("limit_up_count"), dict) else None,
        "source": "同花顺",
        "degraded": False,
        "degraded_reason": [],
        "note": "同花顺涨停池（数据中心免费接口，无需登录）",
        "units": {"pct_change": "小数(0.05=5%)"},
    }


def fetch_continuous_pool(date: str) -> dict[str, Any]:
    """同花顺连板池 + 涨跌停/炸板元数据（今日/昨日涨停数、封板率、开板数、跌停数）。"""
    payload = _fetch_all(
        "/limit_up/continuous_limit_pool",
        {"field": 199112, "order": "desc", "date": date.replace("-", "")},
    )
    info = payload.get("info") or []
    return {
        "count": len(info),
        "stocks": [_norm_stock(r) for r in info],
        "limit_up_count": payload.get("limit_up_count"),
        "limit_down_count": payload.get("limit_down_count"),
        "date": payload.get("date"),
        "source": "同花顺",
        "degraded": False,
        "degraded_reason": [],
        "note": "同花顺连板池 + 涨跌停统计（免费接口）",
        "units": {"pct_change": "小数(0.05=5%)"},
    }


def build_zt_block(date: str) -> dict[str, Any]:
    """合并涨停池与连板池 → 数据层 zt_pool 块结构（对齐东财口径，缺口处标注）。"""
    limit = fetch_limit_pool(date)
    cont = fetch_continuous_pool(date)
    stocks = limit["stocks"]
    tier: dict[str, int] = {}
    if stocks:
        again = sum(1 for s in stocks if s.get("is_again_limit"))
        tier = {"首板": len(stocks) - again, "2板及以上": again}
    today = (cont.get("limit_up_count") or {}).get("today") or {}
    yesterday = (cont.get("limit_up_count") or {}).get("yesterday") or {}
    limit_down = (cont.get("limit_down_count") or {}).get("today") or {}
    # 跌停为 0 只时，同花顺连板池接口会省略 limit_down_count.today.num 字段
    # （无跌停股票即无该统计项）——此时缺省按 0，而非 None：
    # 涨停池正常返回说明接口有响应，跌停缺省 0 语义是「0 只」，不是「数据缺失」。
    limit_down_count = limit_down.get("num")
    if limit_down_count is None:
        limit_down_count = 0
    touched = today.get("history_num")
    zhaban = today.get("open_num")
    return {
        "limit_up": {
            "count": today.get("num") if today.get("num") is not None else limit["count"],
            "tier": tier,
            "stocks": stocks[:50],
            "note": "连板高度由同花顺 is_again_limit 近似（无法区分具体板数）"
                    if stocks else "涨停池为空",
        },
        "limit_down": {
            "count": limit_down_count,
            "stocks": [],
            "note": "跌停明细由东财/其他源补充",
        },
        "zhaban": {
            "count": zhaban,
            "touched": touched,
            "zhaban_rate": round(zhaban / touched, 6) if zhaban is not None and touched else None,
        },
        "yesterday_zt_count": yesterday.get("num"),
        "seal_rate": round(today.get("num") / touched, 6) if today.get("num") is not None and touched else None,
        "source": "同花顺",
        "degraded": False,
        "degraded_reason": [],
        "units": {"zhaban_rate": "小数(0.17=17%)", "seal_rate": "小数"},
    }


if __name__ == "__main__":
    import sys

    d = sys.argv[1] if len(sys.argv) > 1 else "2026-08-03"
    print(json.dumps(build_zt_block(d), ensure_ascii=False, indent=1)[:1500])
