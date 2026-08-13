# -*- coding: utf-8 -*-
"""通达信协议远程数据源（tdxpy，主站直连）。

背景（2026-08-04 实测）：通达信新版主站对未认证客户端返回哑数据
（行情快照为空 / 分时 price=0.01），但扫描 167 台主站后实测 22 台仍返回真实数据；
本模块内置已验证主站列表并按序轮换，超时/失败抛 ``TdxRemoteError``（中文）由数据层降级链处理。

实测能力（2026-08-04）：
- 实时快照（五档/开高低/昨收/成交额）✅
- 日K线（个股/指数，全历史分页）✅
- 1/5/15/30/60 分钟K线（约 6 个交易日历史）✅
- 分时曲线 / 历史分时 / 逐笔成交 ❌（新版主站协议格式变更，tdxpy/pytdx 均解析失败）

依赖：tdxpy（pyproject 已加入）；未安装时 available()=False，各接入点自动跳过（降级链）。
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Optional

_CONNECT_TIMEOUT = 5
_CALL_TIMEOUT = 15

# 实测可用主站（2026-08-04 扫描 167 台后返回真实日K的 22 台）
_SERVERS: list[tuple[str, int]] = [
    ("115.238.56.198", 7709),
    ("110.41.174.169", 7709),
    ("115.238.90.165", 7709),
    ("117.34.114.13", 7709),
    ("117.34.114.14", 7709),
    ("117.34.114.15", 7709),
    ("117.34.114.16", 7709),
    ("117.34.114.17", 7709),
    ("117.34.114.18", 7709),
    ("117.34.114.20", 7709),
    ("117.34.114.27", 7709),
    ("180.153.18.170", 7709),
    ("180.153.18.172", 80),
    ("202.108.253.139", 80),
    ("218.106.92.182", 7709),
    ("218.106.92.183", 7709),
    ("218.75.126.9", 7709),
    ("220.178.55.71", 7709),
    ("220.178.55.86", 7709),
    ("59.36.5.11", 7709),
    ("60.12.136.250", 7709),
    ("60.191.117.167", 7709),
]


class TdxRemoteError(RuntimeError):
    """通达信协议数据源错误（中文消息，供降级链标注）。"""


def available() -> bool:
    """tdxpy 是否可用（.venv 已装；系统 Python 未装时降级跳过）。"""
    try:
        import tdxpy  # noqa: F401

        return True
    except Exception:
        return False


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _scaled(value: Any, scale: float) -> Optional[float]:
    """价格字段按 scale 归一（沪市基金/ETF ×10 → 元）。"""
    v = _to_float(value)
    if v is None:
        return None
    return round(v / scale, 6) if scale != 1.0 else v


def _market_of(code: str) -> int:
    """市场代码：1=沪 0=深 2=北交所。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9", "5")):
        return 1
    if code.startswith(("4", "8")):
        return 2
    return 0


def _call_with_timeout(fn, *args, timeout: float = _CALL_TIMEOUT, **kwargs):
    """tdxpy 同步阻塞调用带超时（线程包装，避免单台主站挂死拖垮取数）。"""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["r"] = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一转 TdxRemoteError
            box["e"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TdxRemoteError(f"通达信主站调用超时（>{timeout}s）")
    if "e" in box:
        raise TdxRemoteError(f"通达信主站调用失败：{box['e']}")
    return box.get("r")


def _connect():
    """按序轮换主站；返回 (api, host)；全部失败抛 TdxRemoteError。"""
    if not available():
        raise TdxRemoteError("tdxpy 未安装，通达信协议源不可用")
    from tdxpy.hq import TdxHq_API

    last: Optional[Exception] = None
    for host, port in _SERVERS:
        api = TdxHq_API()
        try:
            ok = _call_with_timeout(
                api.connect, host, port, time_out=_CONNECT_TIMEOUT,
                timeout=_CONNECT_TIMEOUT + 3,
            )
            if not ok:
                raise TdxRemoteError(f"连接失败 {host}:{port}")
            try:
                api.setup()
            except Exception:
                pass
            return api, host
        except Exception as exc:  # noqa: BLE001 - 单台失败换下一台
            last = exc
            try:
                api.disconnect()
            except Exception:
                pass
    raise TdxRemoteError(f"通达信主站全部不可用（{len(_SERVERS)} 台）：{last}")


def _disconnect(api) -> None:
    try:
        api.disconnect()
    except Exception:
        pass


def fetch_quote(code: str) -> dict:
    """实时快照（五档）→ 归一化块；比率小数、金额元。"""
    api, host = _connect()
    try:
        rows = _call_with_timeout(api.get_security_quotes, [(_market_of(code), str(code).zfill(6))])
        if not rows:
            raise TdxRemoteError(f"通达信快照为空：{code}（主站 {host}）")
        r = rows[0]
        price = _to_float(r.get("price"))
        pre = _to_float(r.get("last_close"))
        pct = round((price - pre) / pre, 6) if price and pre else None
        # 通达信协议对沪市基金/ETF（5xx）价格按 ×10 返回，需归一（实测 0.865→8.65）
        scale = 10.0 if str(code).startswith("5") else 1.0
        if scale != 1.0:
            price = price / scale if price is not None else None
            pre = pre / scale if pre is not None else None
        return {
            "code": str(code).zfill(6),
            "name": None,
            "price": price,
            "pct_change": pct,
            "open": _scaled(r.get("open"), scale),
            "high": _scaled(r.get("high"), scale),
            "low": _scaled(r.get("low"), scale),
            "pre_close": pre,
            "volume": _to_float(r.get("vol")),
            "amount": _to_float(r.get("amount")),
            **{f"bid{i}": _scaled(r.get(f"bid{i}"), scale) for i in range(1, 6)},
            **{f"bid_vol{i}": r.get(f"bid_vol{i}") for i in range(1, 6)},
            **{f"ask{i}": _scaled(r.get(f"ask{i}"), scale) for i in range(1, 6)},
            **{f"ask_vol{i}": r.get(f"ask_vol{i}") for i in range(1, 6)},
            "time": str(r.get("servertime") or ""),
            "source": "通达信协议",
            "units": {"pct_change": "小数(0.05=5%)", "amount": "元"},
            "note": None,
        }
    finally:
        _disconnect(api)


def fetch_index_updown_counts() -> dict[str, dict]:
    """四大指数涨跌家数（单连接批量；返回 {code: {up_count, down_count}}）。"""
    api, host = _connect()
    try:
        out: dict[str, dict] = {}
        for code, mkt in (("000001", 1), ("399001", 0), ("399006", 0), ("000688", 1)):
            rows = _call_with_timeout(api.get_index_bars, 9, mkt, code, 0, 1)
            if rows:
                out[code] = {
                    "up_count": rows[-1].get("up_count"),
                    "down_count": rows[-1].get("down_count"),
                }
        if not out:
            raise TdxRemoteError("指数涨跌家数为空")
        return out
    finally:
        _disconnect(api)


def fetch_daily(code: str, count: int = 250, is_index: bool = False,
                market: Optional[int] = None) -> list[dict]:
    """日K线（个股或指数，按时间正序返回最近 count 根）。

    指数（如 000001 上证/399006 创业板）不能按代码前缀判市场，须由调用方传 market
    （1=沪 0=深）；未传时按指数代码段推断（000 系→沪，399 系→深），个股按代码前缀判定。
    """
    if count < 1 or count > 800:
        raise TdxRemoteError(f"日K数量超范围：{count}（1-800）")
    api, host = _connect()
    try:
        fn = api.get_index_bars if is_index else api.get_security_bars
        if market is None:
            if is_index:
                market = 1 if str(code).startswith("000") else (
                    0 if str(code).startswith("399") else _market_of(code))
            else:
                market = _market_of(code)
        mkt = market
        rows = _call_with_timeout(fn, 9, mkt, str(code).zfill(6), 0, count)
        if not rows:
            raise TdxRemoteError(f"通达信日K为空：{code}（主站 {host}）")
        # 实测：ETF 快照价格 ×10，但 K线价格已是真实元值，不缩放
        return [_bar(r) for r in rows]
    finally:
        _disconnect(api)


def fetch_minute_kline(code: str, count: int = 240, category: int = 8) -> list[dict]:
    """分钟K线（category：8=1分钟 0=5分钟 1=15分钟 2=30分钟 3=60分钟）。"""
    if count < 1 or count > 800:
        raise TdxRemoteError(f"分钟K数量超范围：{count}（1-800）")
    api, host = _connect()
    try:
        rows = _call_with_timeout(
            api.get_security_bars, category, _market_of(code), str(code).zfill(6), 0, count
        )
        if not rows:
            raise TdxRemoteError(f"通达信分钟K为空：{code}（主站 {host}）")
        return [_bar(r) for r in rows]
    finally:
        _disconnect(api)


def _bar(r: dict, scale: float = 1.0) -> dict:
    """K线行归一：datetime → date/time；amount 元。"""
    dt = str(r.get("datetime") or "")
    date = dt[:10] if len(dt) >= 10 else dt
    tm = dt[11:16] if len(dt) >= 16 else (dt[11:] if len(dt) > 10 else None)
    return {
        "date": date,
        "time": tm,
        "datetime": dt,
        "open": _scaled(r.get("open"), scale),
        "high": _scaled(r.get("high"), scale),
        "low": _scaled(r.get("low"), scale),
        "close": _scaled(r.get("close"), scale),
        "volume": _to_float(r.get("vol")),
        "amount": _to_float(r.get("amount")),
        "up_count": r.get("up_count"),
        "down_count": r.get("down_count"),
    }
