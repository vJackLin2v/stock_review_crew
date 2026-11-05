# -*- coding: utf-8 -*-
"""A 股涨跌幅偏离异动计算（监管异动）。

口径（2026-08，已按用户确认与交易所常见规则定稿）：
- 适用范围：沪深主板 / 创业板 / 科创板 A 股；跳过 ST / 北交所 / 退市 / 新股。
- 偏离值：当日偏离 = 个股当日涨跌幅 - 对应板块基准指数当日涨跌幅；
  N 日偏离 = 近 N 个交易日的当日偏离累加。
- 一般异动（异常波动）：
    主板：3 日偏离累计 ≥ +20% 或 ≤ -20%；
    创业板/科创板：3 日偏离累计 ≥ +30% 或 ≤ -30%。
- 严重异常波动：
    10 日偏离累计 ≥ +100% 或 ≤ -50%；
    30 日偏离累计 ≥ +200% 或 ≤ -70%；
    10 日内同向异常波动 ≥ 4 次。
- 未来模拟：默认假设基准指数走平，推算 T+N 后的窗口偏离与“安全上涨空间”。

所有输出仅供参考，不构成投资建议。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from . import stock_data
from . import tdx_remote

DISCLAIMER = "仅供参考，不构成投资建议"

def _stock_name(code: str) -> str:
    """尝试取股票名称（实时接口优先，Tushare 名称表兜底）；失败返回空。"""
    try:
        from .realtime import fetch_stock_quote
        q = fetch_stock_quote(code)
        n = q.get("name")
        if n:
            return str(n)
    except Exception:
        pass
    try:
        m = stock_data._ts_name_map()
        if m and m.get(code):
            return str(m.get(code))
    except Exception:
        pass
    return ""
_BOARD_RULES = [
    # prefix, board, threshold_3d, index_symbol(按 沪/深 分), index_code, index_market, index_name
    # 偏离值基准按板块指数：沪主板→上证指数(000001)；深主板→深证A股指数(399107)；创业板→创业板综指(399102)；科创板→科创50指数(000688)
    ("688", "科创板", 0.30, "sh000688", "000688", 1, "科创50指数"),
    ("689", "科创板", 0.30, "sh000688", "000688", 1, "科创50指数"),
    ("60", "沪主板", 0.20, "sh000001", "000001", 1, "上证指数"),
    ("000", "深主板", 0.20, "sz399107", "399107", 0, "深证A股指数"),
    ("001", "深主板", 0.20, "sz399107", "399107", 0, "深证A股指数"),
    ("002", "深主板", 0.20, "sz399107", "399107", 0, "深证A股指数"),
    ("003", "深主板", 0.20, "sz399107", "399107", 0, "深证A股指数"),
    ("300", "创业板", 0.30, "sz399102", "399102", 0, "创业板综指"),
    ("301", "创业板", 0.30, "sz399102", "399102", 0, "创业板综指"),
]

# 严重异常波动阈值
SEVERE_10 = (1.00, -0.50)   # (+100%, -50%)
SEVERE_30 = (2.00, -0.70)   # (+200%, -70%)


def board_of(code: str) -> Optional[dict]:
    code = str(code or "").zfill(6)
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("4", "8", "92")):
        return None  # 北交所/三板（不处理）
    for prefix, board, t3, sym, icode, mkt, iname in _BOARD_RULES:
        if code.startswith(prefix):
            return {
                "board": board,
                "threshold_3d": t3,
                "index_symbol": sym,
                "index_code": icode,
                "index_market": mkt,
                "index_name": iname,
            }
    return None


def _series_from(bars: list[dict]) -> list[dict]:
    """规范化日K：{date, close}，按日期升序去重。"""
    out: dict[str, float] = {}
    for b in bars or []:
        d = str(b.get("date") or b.get("datetime") or "")
        c = b.get("close")
        if d and c is not None:
            try:
                out.setdefault(d, float(c))
            except (TypeError, ValueError):
                continue
    return [{"date": d, "close": c} for d, c in sorted(out.items())]


def _tdx_daily(code: str, is_index: bool, market: Optional[int] = None) -> Optional[list[dict]]:
    try:
        return tdx_remote.fetch_daily(code, count=45, is_index=is_index, market=market)
    except Exception:
        return None


def _fallback_daily(symbol: str) -> Optional[list[dict]]:
    for fn in (stock_data._records_from_tx_kline, stock_data._records_from_em_kline):
        try:
            return fn(symbol)
        except Exception:
            continue
    return None


def _stock_series(code: str) -> tuple[list[dict], str]:
    """个股近 45 日收盘序列。优先腾讯日K（实测更接近同花顺/监管异动口径），
    备用通达信协议、东财。"""
    sym = "sh" + code if code.startswith(("6", "9")) else "sz" + code
    for _attempt in range(3):  # 腾讯源偶发空/超时，重试后再换 TDX
        try:
            bars = stock_data._records_from_tx_kline(sym)
            if bars:
                return _series_from(bars), "腾讯日K"
        except Exception:
            pass
        import time as _t
        _t.sleep(0.2 * (_attempt + 1))
    bars = _tdx_daily(code, False)
    if bars:
        return _series_from(bars), "通达信协议"
    bars = _fallback_daily(sym)
    if bars:
        return _series_from(bars), "东财"
    return [], "数据缺失"


def _index_series(sym: str, code: str, market: int) -> tuple[list[dict], str]:
    for _attempt in range(3):
        try:
            bars = stock_data._records_from_tx_kline(sym)
            if bars:
                return _series_from(bars), "腾讯日K"
        except Exception:
            pass
        import time as _t
        _t.sleep(0.2 * (_attempt + 1))
    bars = _tdx_daily(code, True, market)
    if bars:
        return _series_from(bars), "通达信协议"
    bars = _fallback_daily(sym)
    if bars:
        return _series_from(bars), "东财"
    return [], "数据缺失"

def _realtime_quote(symbol: str) -> tuple[Optional[float], Optional[str]]:
    """腾讯实时报价：返回 (现价, 时间戳 YYYYMMDDHHMMSS)；失败返回 (None, None)。

    参考图是盘中 11:23 的截图（股价 9.02、上证A股 4101.83），与收盘价略有差异，
    因此计算时用“最新现价”替换当日日K收盘价，使 UI 与参考图 1:1 对齐。
    """
    import re as _re
    import requests as _rq
    try:
        resp = _rq.get(
            "http://qt.gtimg.cn/q=" + str(symbol).lower(),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
            proxies={"http": None, "https": None},
        )
        text = resp.content.decode("gbk", errors="ignore")
        m = _re.search(r'="([^"]*)"', text)
        if not m:
            return None, None
        parts = m.group(1).split("~")
        if len(parts) < 31:
            return None, None
        price = float(parts[3])
        ts = str(parts[30]).strip()
        return price, ts
    except Exception:
        return None, None


def _apply_realtime_snapshot(stock_symbol: str, stock: list[dict],
                             index_symbol: str, index: list[dict]) -> None:
    """若当日盘中/收盘且有实时价，则用最新价替换当日日K收盘价（仅当日）。

    盘中查询能复现参考图（如 600664 @11:23 = 9.02 → 3日1.87/10日44.70/30日197.85）。
    收盘后实时价即收盘价，无副作用；失败则静默维持日K收盘价。
    """
    if not stock or not index:
        return
    s_date = stock[-1]["date"]
    i_date = index[-1]["date"]
    if s_date != i_date:
        return
    s_price, s_ts = _realtime_quote(stock_symbol)
    i_price, i_ts = _realtime_quote(index_symbol)
    _today = s_date.replace("-", "")
    if s_price is not None and s_ts and s_ts[:8] == _today and s_ts[8:14] >= "093000":
        stock[-1] = {**stock[-1], "close": s_price}
    if i_price is not None and i_ts and i_ts[:8] == _today and i_ts[8:14] >= "093000":
        index[-1] = {**index[-1], "close": i_price}



def _pct(prev_close: float, close: float) -> Optional[float]:
    if not prev_close:
        return None
    return round((close - prev_close) / prev_close, 6)


def _aligned(stock: list[dict], index: list[dict]) -> list[dict]:
    """按日期对齐，输出逐日 {date, stock_close, idx_close, stock_pct, idx_pct, dev}。"""
    idx_map = {r["date"]: r["close"] for r in index}
    out = []
    prev_s: Optional[float] = None
    prev_i: Optional[float] = None
    for r in stock:
        d = r["date"]
        ic = idx_map.get(d)
        if ic is None:
            continue
        sp = _pct(prev_s, r["close"]) if prev_s is not None else None
        ip = _pct(prev_i, ic) if prev_i is not None else None
        dev = round(sp - ip, 6) if sp is not None and ip is not None else None
        out.append({
            "date": d,
            "stock_close": r["close"],
            "idx_close": ic,
            "stock_pct": sp,
            "idx_pct": ip,
            "dev": dev,
        })
        prev_s = r["close"]
        prev_i = ic
    return [x for x in out if x["dev"] is not None]


def _rolling_sum(rows: list[dict], n: int) -> float:
    """N 日累计涨跌幅偏离（复利）：个股累计涨幅 - 基准指数累计涨幅。"""
    window = rows[-n:]
    sp = 1.0
    ip = 1.0
    for x in window:
        sp *= 1.0 + float(x.get("stock_pct") or 0.0)
        ip *= 1.0 + float(x.get("idx_pct") or 0.0)
    return round(sp - ip, 6)


def _max_cum_dev(rows: list[dict], n: int) -> tuple[float, int]:
    """区间最大累计偏离（复利）：在近 n 个交易日内，遍历尾长 L=1..n，
    取累计偏离最大者及对应天数（对齐图片“区间最大 X%（N天）”）。"""
    best = -float("inf")
    best_days = 1
    for L in range(1, n + 1):
        d = _rolling_sum(rows, L)
        if d > best:
            best, best_days = d, L
    return round(best, 6), best_days


def _same_direction_count(rows: list[dict], threshold: float) -> tuple[int, str]:
    """近 10 个交易日内，3 日偏离达到 ±阈值 的同向次数（取上/下方向较多者）。"""
    up = down = 0
    recent = rows[-10:]
    for i in range(len(recent)):
        dev = _rolling_sum(recent[: i + 1], 3)
        if dev >= threshold:
            up += 1
        elif dev <= -threshold:
            down += 1
    return max(up, down), ("↑" if up >= down else "↓" if down > up else "—")


def compute_anomaly(code: str, days: int = 2, date: Optional[str] = None) -> dict:
    """单股异动计算主入口。返回结构化结果（绝不抛异常，失败标注）。"""
    code = str(code or "").zfill(6)
    board = board_of(code)
    if board is None:
        return {
            "ok": False, "code": code, "error": "仅支持沪深主板/创业板/科创板（跳过 ST/北交所）",
            "disclaimer": DISCLAIMER,
        }
    stock, s_src = _stock_series(code)
    idx, i_src = _index_series(board["index_symbol"], board["index_code"], board["index_market"])
    if not stock or not idx:
        return {
            "ok": False, "code": code, "error": "行情数据不足，无法计算异动", "disclaimer": DISCLAIMER,
        }
    if date:
        # 历史 as-of 口径：截断到指定日期，不叠加实时快照
        stock = [r for r in stock if r["date"] <= date]
        idx = [r for r in idx if r["date"] <= date]
    else:
        stock_sym = ("sh" if code.startswith(("6", "9")) else "sz") + code
        _apply_realtime_snapshot(stock_sym, stock, board["index_symbol"], idx)
    aligned = _aligned(stock, idx)
    if len(aligned) < 5:
        return {"ok": False, "code": code, "error": "对齐后交易日不足，无法计算", "disclaimer": DISCLAIMER}

    t3 = board["threshold_3d"]
    dev3 = _rolling_sum(aligned, 3)
    dev10 = _rolling_sum(aligned, 10)
    dev30 = _rolling_sum(aligned, 30)
    same_cnt, same_dir = _same_direction_count(aligned, t3)

    last = aligned[-1]
    close = last["stock_close"]
    warn_10 = dev10 >= SEVERE_10[0] or dev10 <= SEVERE_10[1]
    warn_30 = dev30 >= SEVERE_30[0] or dev30 <= SEVERE_30[1]
    warn_3 = dev3 >= t3 or dev3 <= -t3
    severe = bool(warn_10 or warn_30 or same_cnt >= 4)

    # 窗口详情（当前值 + 区间最大累计偏离/天数 + 剩余空间/触发价，基准走平假设）
    def _window(dev: float, up: float, down: float, n: int) -> dict:
        # 触发价采用“复利门槛”口径，且以“区间最大偏离(max)”为基准：
        #   触发价 = 当前价 × (目标复利值 / (1 + 区间最大偏离) - 1)
        # 与图片一致：30日 max197.85% → 触发9.09；10日 max44.70% → 12.46。
        max_dev, max_days = _max_cum_dev(aligned, n)
        base_prod = 1.0 + max_dev
        up_prod = 1.0 + up
        down_prod = 1.0 + down
        factor_up = max(up_prod / base_prod - 1.0, 0.0) if base_prod > 0 else 0.0
        factor_down = max(1.0 - down_prod / base_prod, 0.0) if base_prod > 0 else 0.0
        return {
            "current": round(dev * 100, 2),
            "threshold_up_pp": round(up * 100, 2),
            "threshold_down_pp": round(down * 100, 2),
            "max": round(max_dev * 100, 2),
            "max_days": max_days,
            "remain_up_pp": round(factor_up * 100, 2),
            "remain_down_pp": round(factor_down * 100, 2),
            "trigger_up_price": round(close * (1.0 + factor_up), 4),
            "trigger_down_price": round(close * (1.0 - factor_down), 4),
        }

    triggers = {
        "3d": _window(dev3, t3, -t3, 3),
        "10d": _window(dev10, SEVERE_10[0], SEVERE_10[1], 10),
        "30d": _window(dev30, SEVERE_30[0], SEVERE_30[1], 30),
    }

    # 未来模拟（基准走平：追加 days 天, 个股/指数涨跌幅均按 0）
    simulation = []
    sim = list(aligned)
    base_date = datetime.strptime(sim[-1]["date"], "%Y-%m-%d")
    for i in range(1, days + 1):
        nd = base_date + timedelta(days=i)
        sim.append({
            "date": nd.strftime("%Y-%m-%d"),
            "stock_close": close,
            "idx_close": last["idx_close"],
            "stock_pct": 0.0,
            "idx_pct": 0.0,
            "dev": 0.0,
        })
        s3 = _rolling_sum(sim, 3)
        s10 = _rolling_sum(sim, 10)
        s30 = _rolling_sum(sim, 30)
        # 安全上涨空间 = 10日/30日剩余上涨空间（%），取更小者
        safe = min(
            max((SEVERE_10[0] - s10) * 100, 0.0),
            max((SEVERE_30[0] - s30) * 100, 0.0),
        )
        simulation.append({
            "t": i,
            "date": nd.strftime("%Y-%m-%d"),
            "dev3": round(s3 * 100, 2),
            "dev10": round(s10 * 100, 2),
            "dev30": round(s30 * 100, 2),
            "safe_upside_pct": round(safe, 2),
        })
        # 模拟只追加最新日期，保持对齐
        sim = sim[-45:]

    daily_30 = [
        {"date": x["date"], "dev3": round(_rolling_sum(aligned[: i + 1], 3) * 100, 2),
         "dev10": round(_rolling_sum(aligned[: i + 1], 10) * 100, 2)}
        for i, x in enumerate(aligned)
        if i >= 4
    ][-30:][::-1]

    return {
        "ok": True,
        "code": code,
        "name": _stock_name(code),
        "board": board["board"],
        "threshold_3d_pct": round(t3 * 100, 0),
        "date": last["date"],
        "close": close,
        "stock_pct": round(last.get("stock_pct") or 0.0, 4),
        "index_name": board["index_name"],
        "index_pct": round(last.get("idx_pct") or 0.0, 4),
        "windows": {
            "3d": triggers["3d"], "10d": triggers["10d"], "30d": triggers["30d"],
            "same_direction": {"count": same_cnt, "dir": same_dir, "threshold": 4,
                               "severe": same_cnt >= 4},
        },
        "alerts": {
            "3d": warn_3, "10d": warn_10, "30d": warn_30, "severe": severe,
        },
        "simulation": simulation,
        "daily_30": daily_30,
        "sources": {"stock": s_src, "index": i_src},
        "disclaimer": DISCLAIMER,
    }


__all__ = ["compute_anomaly", "board_of", "DISCLAIMER"]
