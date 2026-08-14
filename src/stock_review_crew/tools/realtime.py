# -*- coding: utf-8 -*-
"""盘中实时数据快照（解决「API 只有昨日数据」问题）。

主源全部为**实时接口**（已实测 2026-08-04）：
- 指数：东财 push2 `ulist.np/get`（HTTP 改写）→ 备用腾讯 `qt.gtimg.cn`（带时间戳）；
- 板块涨幅/主力净流入：东财 `clist`（fs=m:90+t:2，fid=f62）；
- 涨跌停/炸板/封板率：同花顺 dataapi（盘中实时统计）；
- 快讯：新浪 7x24。

约定（契约 §六）：比率小数、金额元、None=「—」、每块 source/degraded 标注、
单块超时 ≤15s；龙虎榜/历史资讯天然非实时（见 docs/REALTIME.md）。
"""

from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Optional

import requests

_TIMEOUT = 10
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_EM = "http://push2.eastmoney.com/api/qt"
_INDEX_SECIDS = "1.000001,0.399001,0.399006,1.000688"
_INDEX_FIELDS = "f2,f3,f4,f12,f14,f15,f16,f17,f18"


class RealtimeError(RuntimeError):
    """实时数据错误（中文消息）。"""


def _get(url: str, **kw: Any):
    """requests.get 封装：代理客户端未启动/挂掉（ProxyError）时自动直连重试。

    国内行情源直连可用（2026-08-04 实测）；代理恢复前不阻断取数。
    """
    try:
        return requests.get(url, **kw)
    except requests.exceptions.ProxyError:
        return requests.get(url, proxies={"http": None, "https": None}, **kw)


def _http_json(url: str, params: Optional[dict] = None) -> Any:
    """HTTP GET → JSON；东财多子域轮换重试（本机网络对 eastmoney 偶发 502/Reset）。"""
    attempts = [url]
    if "eastmoney.com" in url:
        for host in ("http://80.push2.eastmoney.com", "http://17.push2.eastmoney.com"):
            cand = url.replace("http://push2.eastmoney.com", host)
            if cand not in attempts:
                attempts.append(cand)
    attempts = attempts[:2]  # 实时快照限时：最多 2 次尝试（10s×2=20s 上限）
    last_exc: Optional[Exception] = None
    for candidate in attempts:
        try:
            resp = _get(candidate, params=params, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise RealtimeError(f"HTTP 请求失败：{last_exc}")


def _to_float(value: Any) -> Optional[float]:
    if value in (None, "-", ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def fetch_indices() -> dict[str, Any]:
    """四大指数实时（东财 fltt=2 已是小数价格；涨跌幅为百分数值 → /100 归一）。"""
    # 2026-08-07：东财实时推送域（push2/push2his）整体 502 时兜底 push2delay 延迟行情域
    # （实测可用、字段格式一致），返回 source 如实标注「东财延迟行情」。
    try:
        data = _http_json(
            f"{_EM}/ulist.np/get",
            {"secids": _INDEX_SECIDS, "fields": _INDEX_FIELDS, "fltt": 2, "invt": 2},
        )
        source = "东财实时"
    except RealtimeError:
        data = _http_json(
            "http://push2delay.eastmoney.com/api/qt/ulist.np/get",
            {"secids": _INDEX_SECIDS, "fields": _INDEX_FIELDS, "fltt": 2, "invt": 2},
        )
        source = "东财延迟行情"
    diff = (data.get("data") or {}).get("diff") or []
    rows: list[dict[str, Any]] = []
    for r in diff:
        pct = _to_float(r.get("f3"))
        rows.append(
            {
                "name": r.get("f14"),
                "code": str(r.get("f12") or ""),
                "price": _to_float(r.get("f2")),
                "pct_change": round(pct / 100.0, 6) if pct is not None else None,
                "open": _to_float(r.get("f17")),
                "high": _to_float(r.get("f15")),
                "low": _to_float(r.get("f16")),
                "pre_close": _to_float(r.get("f18")),
                "source": source,
            }
        )
    if not rows:
        raise RealtimeError("东财指数实时无数据")
    degraded = source != "东财实时"
    return {
        "indices": rows,
        "source": source,
        "degraded": degraded,
        "degraded_reason": ["东财实时行情接口不可用，使用延迟行情"] if degraded else [],
    }


def fetch_indices_tx() -> dict[str, Any]:
    """备用：腾讯行情（qt.gtimg.cn，实时，带时间戳）。"""
    resp = _get(
        "http://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000688",
        headers={"User-Agent": _UA},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    rows: list[dict[str, Any]] = []
    for line in resp.text.split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        parts = line.split("=", 1)[1].strip('"').split("~")
        if len(parts) < 35:
            continue
        pct = _to_float(parts[32])
        rows.append(
            {
                "name": parts[1],
                "code": parts[2],
                "price": _to_float(parts[3]),
                "pct_change": round(pct / 100.0, 6) if pct is not None else None,
                "pre_close": _to_float(parts[4]),
                "open": _to_float(parts[5]),
                "high": _to_float(parts[33]),
                "low": _to_float(parts[34]),
                "time": parts[30] if len(parts) > 30 else None,
                "source": "腾讯实时",
            }
        )
    if not rows:
        raise RealtimeError("腾讯指数实时无数据")
    return {"indices": rows, "source": "腾讯实时", "degraded": False, "degraded_reason": []}


def fetch_sector_flow() -> dict[str, Any]:
    """行业板块涨幅 + 主力净流入：东财 clist → 腾讯行业排行 → 浏览器同花顺。"""
    em_err: Optional[Exception] = None
    try:
        data = _http_json(
            f"{_EM}/clist/get",
            {
                "pn": 1, "pz": 60, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f62", "fs": "m:90+t:2+f:!50",
                "fields": "f3,f12,f14,f62",
            },
        )
        diff = (data.get("data") or {}).get("diff") or []
        rows = []
        for r in diff:
            pct = _to_float(r.get("f3"))
            net = _to_float(r.get("f62"))
            rows.append(
                {
                    "name": r.get("f14"),
                    "pct_change": round(pct / 100.0, 6) if pct is not None else None,
                    "net_inflow": net,  # 元
                }
            )
        if rows:
            with_pct = [r for r in rows if r.get("pct_change") is not None]
            with_flow = [r for r in rows if r.get("net_inflow") is not None]
            return {
                "top": sorted(with_pct, key=lambda x: x["pct_change"], reverse=True)[:5],
                "bottom": sorted(with_pct, key=lambda x: x["pct_change"])[:5],
                "flow_in": sorted(with_flow, key=lambda x: x["net_inflow"], reverse=True)[:5],
                "flow_out": sorted(with_flow, key=lambda x: x["net_inflow"])[:5],
                "source": "东财实时",
                "degraded": False,
                "degraded_reason": [],
                "units": {"pct_change": "小数(0.05=5%)", "net_inflow": "元"},
            }
    except Exception as exc:  # noqa: BLE001
        em_err = exc
    try:
        return _sector_flow_from_tx()
    except Exception as exc:  # noqa: BLE001
        tx_err = exc
    # 备用：浏览器解析同花顺板块页（页面 JS 签名自动执行）
    try:
        from .browser_crawler import fetch_ths_sector_rows

        rows = fetch_ths_sector_rows()
        with_pct = [r for r in rows if r.get("涨跌幅") is not None]
        with_flow = [r for r in rows if r.get("净流入") is not None]
        norm = lambda r: {  # noqa: E731
            "name": r.get("板块"),
            "pct_change": round(float(r["涨跌幅"]) / 100.0, 6),
            "net_inflow": r.get("净流入"),
        }
        return {
            "top": sorted((norm(r) for r in with_pct), key=lambda x: x["pct_change"], reverse=True)[:5],
            "bottom": sorted((norm(r) for r in with_pct), key=lambda x: x["pct_change"])[:5],
            "flow_in": sorted((norm(r) for r in with_flow), key=lambda x: x["net_inflow"] or 0, reverse=True)[:5],
            "flow_out": sorted((norm(r) for r in with_flow), key=lambda x: x["net_inflow"] or 0)[:5],
            "source": "同花顺(浏览器)",
            "degraded": False,
            "degraded_reason": [],
            "units": {"pct_change": "小数(0.05=5%)", "net_inflow": "元"},
        }
    except Exception as exc:  # noqa: BLE001
        raise RealtimeError(f"板块数据失败：东财({em_err})；腾讯({tx_err})；浏览器({exc})") from exc


def _sector_flow_from_tx() -> dict[str, Any]:
    """备用：腾讯行业排行（proxy.finance.qq.com，实测可用；资金单位为万元）。"""
    resp = _get(
        "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank",
        params={"board_type": "hy", "sort_type": "price", "direct": "down",
                "offset": 0, "count": 30},
        headers={"User-Agent": _UA, "Referer": "https://gu.qq.com/"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    rank = ((resp.json().get("data") or {}).get("rank_list")) or []
    rows = []
    for r in rank:
        pct = _to_float(r.get("zdf"))
        net = _to_float(r.get("zljlr"))
        leader = r.get("lzg") or {}
        leader_pct = _to_float(leader.get("zdf"))
        rows.append(
            {
                "name": r.get("name"),
                "pct_change": round(pct / 100.0, 6) if pct is not None else None,
                "net_inflow": net * 10000 if net is not None else None,  # 腾讯为万元
                "leading_stock": leader.get("name"),
                "leading_pct_change": round(leader_pct / 100.0, 6) if leader_pct is not None else None,
            }
        )
    if not rows:
        raise RealtimeError("腾讯行业排行无数据")
    with_pct = [r for r in rows if r.get("pct_change") is not None]
    with_flow = [r for r in rows if r.get("net_inflow") is not None]
    return {
        "top": sorted(with_pct, key=lambda x: x["pct_change"], reverse=True)[:5],
        "bottom": sorted(with_pct, key=lambda x: x["pct_change"])[:5],
        "flow_in": sorted(with_flow, key=lambda x: x["net_inflow"], reverse=True)[:5],
        "flow_out": sorted(with_flow, key=lambda x: x["net_inflow"])[:5],
        "source": "腾讯行业",
        "degraded": False,
        "degraded_reason": [],
        "units": {"pct_change": "小数(0.05=5%)", "net_inflow": "元"},
    }


def _secid(code: str) -> str:
    code = str(code or "").strip().zfill(6)
    if code.startswith(("6", "9", "5")):  # 5xx=沪市基金/ETF
        return f"1.{code}"
    return f"0.{code}"


def _tx_symbol(code: str) -> str:
    """转腾讯行情代码格式（sh600519 / sz000001 / bj830799）。"""
    code = str(code or "").strip().zfill(6)
    if code.startswith(("6", "9", "5")):  # 5xx=沪市基金/ETF
        return f"sh{code}"
    if code.startswith(("4", "8", "92")):
        return f"bj{code}"
    return f"sz{code}"


def fetch_stock_quote(code: str) -> dict[str, Any]:
    """个股实时行情（东财 push2 stock/get，fltt=2 已小数化；涨跌幅百分数 → /100）。"""
    em_err: Optional[Exception] = None
    try:
        secid = _secid(code)
        data = _http_json(
            f"{_EM}/stock/get",
            {"secid": secid, "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170",
             "fltt": 2, "invt": 2},
        )
        d = data.get("data") or {}
        if d and d.get("f43") not in (None, "-"):
            pct = _to_float(d.get("f170"))
            turnover = _to_float(d.get("f169"))
            return {
                "code": str(d.get("f57") or code).zfill(6),
                "name": d.get("f58"),
                "price": _to_float(d.get("f43")),
                "pct_change": round(pct / 100.0, 6) if pct is not None else None,
                "open": _to_float(d.get("f46")),
                "high": _to_float(d.get("f44")),
                "low": _to_float(d.get("f45")),
                "pre_close": _to_float(d.get("f60")),
                "volume": _to_float(d.get("f47")),
                "amount": _to_float(d.get("f48")),
                "turnover_rate": round(turnover / 100.0, 6) if turnover is not None else None,
                "bids": [],  # 东财 stock/get 未取五档，由腾讯/通达信协议兜底
                "asks": [],
                "source": "东财实时",
                "units": {"pct_change": "小数(0.05=5%)", "turnover_rate": "小数"},
            }
    except Exception as exc:  # noqa: BLE001
        em_err = exc
    tx_note = "腾讯行情不可用"
    # 备用：腾讯行情（qt.gtimg.cn，实测可用）
    try:
        import requests as _rq

        resp = _get(
            f"https://qt.gtimg.cn/q={_tx_symbol(code)}",
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.text
        if "=" not in text:
            raise RealtimeError(f"腾讯行情返回异常：{code}")
        parts = text.split("=", 1)[1].strip('"').split("~")
        if len(parts) < 35:
            raise RealtimeError(f"腾讯行情字段不足：{code}")
        pct = _to_float(parts[32])
        turnover = _to_float(parts[38]) if len(parts) > 38 else None
        amount_wan = _to_float(parts[37]) if len(parts) > 37 else None  # 腾讯成交额单位：万元

        def _levels(price_start: int, vol_start: int) -> list[dict[str, Any]]:
            out = []
            for i in range(5):
                p = _to_float(parts[price_start + i * 2])
                if p is not None:
                    out.append({"price": p, "volume": _to_float(parts[vol_start + i * 2])})
            return out

        return {
            "code": str(parts[2]).zfill(6),
            "name": parts[1],
            "price": _to_float(parts[3]),
            "pct_change": round(pct / 100.0, 6) if pct is not None else None,
            "open": _to_float(parts[5]),
            "high": _to_float(parts[33]),
            "low": _to_float(parts[34]),
            "pre_close": _to_float(parts[4]),
            "volume": _to_float(parts[6]),
            "amount": round(amount_wan * 10000, 2) if amount_wan is not None else None,
            "turnover_rate": round(turnover / 100.0, 6) if turnover is not None else None,
            "bids": _levels(9, 10),
            "asks": _levels(19, 20),
            "source": "腾讯实时",
            "units": {"pct_change": "小数(0.05=5%)", "turnover_rate": "小数"},
        }
    except Exception as tx_exc:  # noqa: BLE001 - 腾讯失败落到通达信协议备用
        tx_note = f"腾讯({tx_exc})"
    # 备用2：通达信协议直连（tdxpy，实测 22 台主站可用；东财/腾讯双挂时兜底）
    try:
        from .tdx_remote import TdxRemoteError, fetch_quote as tdx_fetch_quote

        q = tdx_fetch_quote(code)

        def _levels_tdx(prefix: str) -> list[dict[str, Any]]:
            out = []
            for i in range(1, 6):
                p = q.get(f"{prefix}{i}")
                if p is not None:
                    out.append({"price": p, "volume": q.get(f"{prefix}_vol{i}")})
            return out

        return {
            "code": q.get("code") or str(code).zfill(6),
            "name": q.get("name"),
            "price": q.get("price"),
            "pct_change": q.get("pct_change"),
            "open": q.get("open"),
            "high": q.get("high"),
            "low": q.get("low"),
            "pre_close": q.get("pre_close"),
            "volume": q.get("volume"),
            "amount": q.get("amount"),
            "turnover_rate": None,
            "bids": _levels_tdx("bid"),
            "asks": _levels_tdx("ask"),
            "source": "通达信协议",
            "units": {"pct_change": "小数(0.05=5%)"},
        }
    except TdxRemoteError as tdx_exc:
        raise RealtimeError(
            f"未查询到 {code} 的实时行情：东财({em_err})；{tx_note}；通达信({tdx_exc})"
        ) from tdx_exc
    except Exception as exc:  # noqa: BLE001
        raise RealtimeError(
            f"未查询到 {code} 的实时行情：东财({em_err})；{tx_note}；通达信({exc})"
        ) from exc


def search_stocks(query: str) -> list[dict[str, Any]]:
    """股票名称/代码搜索：东财 suggest（主）→ 腾讯 smartbox（备）。"""
    q = str(query or "").strip()
    if not q:
        raise RealtimeError("请输入股票名称或代码")
    if re.fullmatch(r"\d{6}", q):
        code = q
        return [{"code": code, "name": None, "market": "sh" if code.startswith(("6", "9")) else "sz",
                 "type": "GP"}]
    try:
        data = _http_json(
            "http://searchapi.eastmoney.com/api/suggest/get",
            {"input": q, "type": 14, "token": "D43BF722C8E33BDC906FB84D85E326E8", "count": 8},
        )
        items = ((data.get("QuotationCodeTable") or {}).get("Data")) or []
        out = []
        for it in items:
            code = str(it.get("Code") or "").zfill(6)
            if not code or code == "000000":
                continue
            mkt = str(it.get("MktNum") or "")
            market = ("sh" if mkt == "1" or code.startswith(("6", "9"))
                      else "bj" if code.startswith(("4", "8", "92")) else "sz")
            out.append({"code": code, "name": it.get("Name"), "market": market,
                        "type": it.get("SecurityTypeName")})
        if out:
            return out
    except Exception:  # noqa: BLE001 - 走腾讯备用
        pass
    try:
        resp = _get(
            "https://smartbox.gtimg.cn/s3/",
            params={"v": 2, "q": q, "t": "gp"},
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        m = re.search(r'v_hint="([^"]*)"', resp.text)
        out = []
        if m:
            for part in m.group(1).split("^"):
                f = part.split("~")
                if len(f) >= 3 and f[2]:
                    code = str(f[1]).zfill(6)
                    out.append({"code": code, "name": f[2], "market": f[0], "type": "GP"})
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    raise RealtimeError(f"未找到与「{q}」匹配的股票")


def _parse_sina_batch(text: str) -> list[dict[str, Any]]:
    """解析新浪批量行情（hq.sinajs.cn）：name,open,pre_close,price,high,low,...,volume,amount,时间。"""
    out = []
    for m in re.finditer(r'hq_str_([a-z]{2}\d{6})="([^"]*)"', text):
        sym, fields = m.group(1), m.group(2)
        f = fields.split(",")
        if len(f) < 32:
            continue
        out.append({
            "code": sym[2:],
            "name": f[0],
            "open": _to_float(f[1]), "pre_close": _to_float(f[2]),
            "price": _to_float(f[3]), "high": _to_float(f[4]), "low": _to_float(f[5]),
            "volume": _to_float(f[8]), "amount": _to_float(f[9]),
        })
    return out


def fetch_sentiment_realtime(date: Optional[str] = None) -> dict[str, Any]:
    """盘中「昨日涨停今日表现」明细：东财昨日涨停池 + 新浪批量实时报价。
    口径：昨收为基准；连板率=今日再涨停÷昨日涨停；核按钮率=今日跌停÷昨日涨停；
    主板10%/创业科创20%；过滤 ST/北证。"""
    today = date or datetime.now().strftime("%Y-%m-%d")
    # 2026-08-10 修复：昨日涨停池应取**上一个交易日**（周一 → 上周五），
    # 直接减 1 天在周末会请求到周六/周日 → 空池误判失败
    _d = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)
    while _d.weekday() >= 5:
        _d -= timedelta(days=1)
    prev = _d.strftime("%Y-%m-%d")
    resp = _get(
        "http://push2ex.eastmoney.com/getTopicZTPool",
        params={"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
                "Pageindex": 0, "pagesize": 600, "sort": "fbt:asc",
                "date": prev.replace("-", "")},
        headers={"User-Agent": _UA},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    pool = ((resp.json().get("data") or {}).get("pool")) or []
    if not pool:
        raise RealtimeError("东财昨日涨停池无数据（可能非交易日或超出保留期）")
    codes = [str(x.get("c") or "").zfill(6) for x in pool]
    codes = [c for c in codes if c and not c.startswith(("4", "8", "92"))]
    symbols = ",".join(("sh" if c.startswith(("6", "9")) else "sz") + c for c in codes)
    r = _get(
        "http://hq.sinajs.cn/list=" + symbols,
        headers={"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    quotes = _parse_sina_batch(r.text)
    if not quotes:
        raise RealtimeError("新浪批量行情无数据")
    rows = []
    for q in quotes:
        price, pre = q.get("price"), q.get("pre_close")
        if price is None or pre is None or pre <= 0:
            continue
        q["pct_change"] = round((price - pre) / pre, 6)
        rows.append(q)
    if not rows:
        raise RealtimeError("批量行情解析为空")
    n = len(rows)
    pcts = [x["pct_change"] for x in rows]
    is_ge = lambda c: c.startswith(("30", "68"))  # noqa: E731
    limit_cnt = sum(1 for x in rows
                    if x["pct_change"] >= (0.199 if is_ge(x["code"]) else 0.099))
    down_limit = sum(1 for x in rows
                     if x["pct_change"] <= (-0.199 if is_ge(x["code"]) else -0.099))
    best = sorted(rows, key=lambda x: x["pct_change"], reverse=True)[:3]
    worst = sorted(rows, key=lambda x: x["pct_change"])[:3]
    return {
        "yesterday": prev, "today": today,
        "yesterday_zt_count": len(codes), "matched_today": n,
        "avg_return": round(sum(pcts) / n, 6),
        "median_return": None,
        "red_rate": round(sum(1 for p in pcts if p > 0) / n, 6),
        "lianban_count": limit_cnt, "lianban_rate": round(limit_cnt / n, 6),
        "hean_count": down_limit, "hean_rate": round(down_limit / n, 6),
        "best3": [{"code": x["code"], "name": x["name"], "pct_change": x["pct_change"]} for x in best],
        "worst3": [{"code": x["code"], "name": x["name"], "pct_change": x["pct_change"]} for x in worst],
        "zhaban": None, "touched": None, "zhaban_rate": None,
        "source": "东财昨日池+新浪实时",
        "degraded": True,
        "degraded_reason": ["Tushare 为收盘后口径，盘中使用实时计算"],
        "note": "盘中实时口径（昨收为基准；仅覆盖昨日涨停池）",
    }


def fetch_breadth_realtime() -> dict[str, Any]:
    """全市场涨跌家数（盘中实时）：东财 clist 全 A 统计，过滤 ST/北证。"""
    em_err: Optional[Exception] = None
    try:
        data = _http_json(
            f"{_EM}/clist/get",
            {"pn": 1, "pz": 7000, "po": 1, "np": 1, "fltt": 2, "invt": 2,
             "fid": "f3", "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
             "fields": "f3,f12,f14"},
        )
        diff = (data.get("data") or {}).get("diff") or []
        up = down = flat = 0
        total = 0
        for r in diff:
            code = str(r.get("f12") or "")
            name = str(r.get("f14") or "")
            if code.startswith(("4", "8", "92")) or "ST" in name.upper() or "退" in name:
                continue
            pct = _to_float(r.get("f3"))
            if pct is None:
                continue
            total += 1
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            else:
                flat += 1
        # 2026-08-14：东财 clist 曾限流仅返回 100 只（fid=f3 降序 → 涨幅榜前 100 全涨），
        # pz=7000 不生效 → total 远小于全市场，up/down 严重失真（down=0 致涨跌比=1）。
        # 加完整性校验：total < 3000 视为接口限流/不完整，抛异常降级新浪全市场备用源。
        if total and total >= 3000:
            return {
                "total": total, "up": up, "down": down, "flat": flat,
                "up_down_ratio": round(up / down, 4) if down else None,
                "source": "东财实时",
                "degraded": False,
                "degraded_reason": [],
                "note": "盘中实时全市场统计（过滤 ST/北证）",
            }
        raise RealtimeError(f"东财全A统计不完整（仅 {total} 只，疑似接口限流）")
    except Exception as exc:  # noqa: BLE001
        em_err = exc
    # 备用：新浪全市场（akshare stock_zh_a_spot，实测 ~20s/5500 只）
    try:
        import akshare as ak

        df = ak.stock_zh_a_spot()
        if df is None or df.empty:
            raise RealtimeError("新浪全市场行情为空")
        col_pct = "\u6da8\u8dcc\u5e45"       # 涨跌幅
        col_code = "\u4ee3\u7801"             # 代码
        col_name = "\u540d\u79f0"             # 名称
        up = down = flat = 0
        total = 0
        for _, r in df.iterrows():
            code = str(r.get(col_code) or "")
            name = str(r.get(col_name) or "")
            if code.startswith(("4", "8", "92")) or "ST" in name.upper() or "\u9000" in name:
                continue
            pct = _to_float(r.get(col_pct))
            if pct is None:
                continue
            total += 1
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            else:
                flat += 1
        if not total:
            raise RealtimeError("新浪全市场统计为空")
        return {
            "total": total, "up": up, "down": down, "flat": flat,
            "up_down_ratio": round(up / down, 4) if down else None,
            "source": "新浪实时",
            "degraded": True,
            # 2026-08-07：对外不拼英文异常原文（em_err 仅保留在异常链），降级标注说人话
            "degraded_reason": ["东财 clist 不可用，降级新浪全市场行情"],
            "note": "新浪全市场统计（过滤 ST/北证）",
        }
    except RealtimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RealtimeError("全市场涨跌家数失败：东财与新浪数据源均不可用") from exc


def fetch_zt_stats(date: Optional[str] = None) -> dict[str, Any]:
    """涨跌停/炸板/封板率实时统计（同花顺 dataapi，盘中即更新）。"""
    from .ths_crawler import ThsError, build_zt_block

    d = date or datetime.now().strftime("%Y-%m-%d")
    try:
        block = build_zt_block(d)
    except ThsError as exc:
        raise RealtimeError(f"同花顺实时统计失败：{exc}") from exc
    limit_up = block.get("limit_up") or {}
    limit_down = block.get("limit_down") or {}
    zhaban = block.get("zhaban") or {}
    return {
        "limit_up_count": limit_up.get("count"),
        "limit_down_count": limit_down.get("count"),
        "zhaban_count": zhaban.get("count"),
        "touched_count": zhaban.get("touched"),
        "zhaban_rate": zhaban.get("zhaban_rate"),
        "seal_rate": block.get("seal_rate"),
        "yesterday_zt_count": block.get("yesterday_zt_count"),
        "tier": limit_up.get("tier", {}),
        "source": "同花顺实时",
        "degraded": False,
        "degraded_reason": [],
        "units": {"zhaban_rate": "小数(0.17=17%)", "seal_rate": "小数"},
    }


# R8（2026-08-08，requirements-r11 §R8）：快讯 A 股相关关键词白名单——集中定义，供测试引用。
# 覆盖 板块/个股/指数/政策/宏观/资金/外盘/公司公告 等类别；不含任一关键词的快讯一律剔除，
# 保留股市相关（剔除体育/娱乐/社会/国际无关/广告）。
A_SHARE_NEWS_KEYWORDS = (
    "A股", "沪深", "股市", "股民", "大盘", "涨停", "跌停", "炸板", "封板", "涨停潮",
    "板块", "个股", "指数", "上证", "深证", "创业板", "科创板", "北交所", "北证",
    "沪指", "深成指", "创指", "权重股", "龙头股",
    "政策", "证监会", "央行", "降准", "降息", "LPR", "MLF", "逆回购", "北向", "外资",
    "基金", "ETF", "公募", "私募", "业绩", "财报", "中报", "年报", "并购", "重组",
    "增持", "减持", "回购", "分红", "集采", "国资委", "财政部", "发改委", "国务院",
    "监管", "立案", "处罚", "退市", "IPO", "上市", "停牌", "复牌", "公告",
    "半导体", "芯片", "人工智能", "机器人", "算力", "新能源", "光伏", "锂电", "白酒",
    "医药", "银行", "券商", "保险", "房地产", "汽车", "军工", "消费", "黄金", "原油",
    "大宗商品", "美股", "港股", "纳指", "道指", "标普", "汇率", "人民币", "美元",
    "宏观", "GDP", "CPI", "PMI", "社融", "M2", "资金", "主力", "游资", "龙虎榜",
    "成交额", "成交量", "放量", "缩量", "反弹", "回调", "震荡", "牛市", "熊市",
    "利好", "利空", "风险", "仓位",
)


def filter_a_share_news(news_list: Any) -> list[dict]:
    """R8：按 A 股相关关键词白名单过滤快讯；含任一关键词即保留，其余剔除。

    兼容 ``fetch_news()`` 的 ``{"news": [...]}`` 字典、字典列表或字符串列表。
    """
    items: list[Any] = []
    if isinstance(news_list, dict):
        items = news_list.get("news") or []
    elif isinstance(news_list, list):
        items = news_list
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("title") or "")
            wrapped = item
        else:
            text = str(item)
            wrapped = {"text": text, "source": "新浪7x24"}
        if any(kw in text for kw in A_SHARE_NEWS_KEYWORDS):
            out.append(wrapped)
    return out


def fetch_news() -> dict[str, Any]:
    """财经快讯（新浪 7x24，实时滚动；R8 返回前按 A 股相关关键词白名单过滤）。"""
    data = _http_json(
        "https://zhibo.sina.com.cn/api/zhibo/feed",
        {"page": 1, "page_size": 10, "zhibo_id": 152, "tag_id": 0, "dire": "f", "dpc": 1},
    )
    feed = ((data.get("result") or {}).get("data") or {}).get("feed") or {}
    items = feed.get("list") or []
    news = []
    for item in items:
        text = str(item.get("rich_text") or item.get("text") or "").strip()
        if text:
            news.append(
                {
                    "time": item.get("create_time"),
                    "text": text[:200],
                    "source": "新浪7x24",
                }
            )
    if not news:
        raise RealtimeError("新浪7x24快讯无数据")
    filtered = filter_a_share_news(news)
    if not filtered:
        return {
            "news": [],
            "source": "新浪7x24",
            "degraded": True,
            "degraded_reason": ["当前快讯均与 A 股无关，已按白名单过滤"],
        }
    return {"news": filtered[:10], "source": "新浪7x24", "degraded": False, "degraded_reason": []}


def market_status(now: Optional[datetime] = None) -> dict[str, Any]:
    """交易时段判定（中国 A 股）：交易日 09:15-15:00 各阶段。"""
    now = now or datetime.now()
    weekday = now.weekday()
    hm = now.hour * 60 + now.minute
    is_trading_day = weekday < 5
    if not is_trading_day:
        phase = "非交易日"
    elif hm < 9 * 60 + 15:
        phase = "盘前"
    elif hm <= 9 * 60 + 25:
        phase = "集合竞价"
    elif hm < 9 * 60 + 30:
        phase = "开盘准备"
    elif hm <= 11 * 60 + 30:
        phase = "交易中(上午)"
    elif hm < 13 * 60:
        phase = "午间休市"
    elif hm <= 15 * 60:
        phase = "交易中(下午)"
    else:
        phase = "已收盘"
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "is_trading_day": is_trading_day,
        "phase": phase,
    }


def fetch_realtime_snapshot() -> dict[str, Any]:
    """完整实时快照：指数/涨跌停/板块/快讯**并行**抓取（总耗时≈最慢单块，≤20s）；
    每块独立降级标注。"""
    status = market_status()
    snapshot: dict[str, Any] = {"status": status, "indices": None, "zt": None,
                                "sectors": None, "news": None, "auction": None,
                                "sources": [], "degraded": []}

    def _indices() -> None:
        try:
            snapshot["indices"] = fetch_indices()
            snapshot["sources"].append("东财实时")
        except Exception as exc:  # noqa: BLE001
            snapshot["degraded"].append(f"指数: {exc}")
            try:
                snapshot["indices"] = fetch_indices_tx()
                snapshot["sources"].append("腾讯实时")
            except Exception as exc2:  # noqa: BLE001
                snapshot["degraded"].append(f"指数备用: {exc2}")

    def _zt() -> None:
        try:
            snapshot["zt"] = fetch_zt_stats(status["date"])
            snapshot["sources"].append("同花顺实时")
        except Exception as exc:  # noqa: BLE001
            snapshot["degraded"].append(f"涨跌停统计: {exc}")

    def _sectors() -> None:
        try:
            snapshot["sectors"] = fetch_sector_flow()
            snapshot["sources"].append("东财实时")
        except Exception as exc:  # noqa: BLE001
            snapshot["degraded"].append(f"板块资金流: {exc}")

    def _news() -> None:
        # 2026-08-12：资讯开关——关闭时不抓新浪7x24快讯，返回空列表（前端条件渲染自动隐藏）
        try:
            from ..config import news_enabled

            if not news_enabled():
                snapshot["news"] = {"news": [], "source": "closed"}
                return
        except Exception:
            pass
        try:
            snapshot["news"] = fetch_news()
            snapshot["sources"].append("新浪7x24")
        except Exception as exc:  # noqa: BLE001
            snapshot["degraded"].append(f"快讯: {exc}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fn) for fn in (_indices, _zt, _sectors, _news)]
        for f in as_completed(futures):
            f.result()  # 内部已全部兜底，不会上抛
    # 竞价窗口提示
    hm = datetime.now().hour * 60 + datetime.now().minute
    if status.get("is_trading_day") and 9 * 60 + 15 <= hm <= 9 * 60 + 25:
        snapshot["auction"] = {"window": True, "note": "当前处于集合竞价窗口（09:15-09:25），可执行竞价复盘"}
    else:
        snapshot["auction"] = {"window": False, "note": "非竞价窗口（09:15-09:25），竞价数据不可用"}
    snapshot["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return snapshot


_tdx_ud_cache: dict[str, Any] = {"t": 0.0, "data": {}}


def enrich_indices_updown(snapshot: dict[str, Any]) -> dict[str, Any]:
    """指数涨跌家数补充（通达信协议，60s 缓存；失败不影响主数据）。

    东财/腾讯指数接口不含成分涨跌家数；通达信 get_index_bars 提供 up_count/down_count。
    """
    import time as _time

    now = _time.time()
    if now - _tdx_ud_cache["t"] > 60:
        try:
            from .tdx_remote import fetch_index_updown_counts

            _tdx_ud_cache.update({"t": now, "data": fetch_index_updown_counts()})
        except Exception:  # noqa: BLE001 - enrichment 失败仅跳过
            pass
    ud = _tdx_ud_cache.get("data") or {}
    if not ud:
        return snapshot
    for row in ((snapshot.get("indices") or {}).get("indices")) or []:
        code = str(row.get("code") or "").zfill(6)
        if code in ud:
            row["up_count"] = ud[code].get("up_count")
            row["down_count"] = ud[code].get("down_count")
            row["updown_source"] = "通达信协议"
    return snapshot


if __name__ == "__main__":
    print(json.dumps(fetch_realtime_snapshot(), ensure_ascii=False, indent=1)[:2000])
