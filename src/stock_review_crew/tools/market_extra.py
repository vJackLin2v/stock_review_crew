# -*- coding: utf-8 -*-
"""R10 市场附加数据层（M1）：市场天气/情绪温度 + 纳斯达克 + 板块温度榜 + 额外指数 + 风格坐标。

契约：docs/requirements-r10.md §二（F3/F6/F5/F2/F4 数据字典，字段唯一真源）与 §四（AKShare 探测）。
数据源链路已按主编排 2026-08-06 提权实测结论冻结：
  - F6 纳指：新浪 index_us_stock_sina（主）→ 东财 index_global_hist_em(symbol="纳斯达克")（备，仅 NDX）；
    注意：东财接口内部裸 requests 走 env 代理会失败，本模块 import stock_data 时已安装
    HTTPS→HTTP 改写（_install_eastmoney_http_rewrite），必须保持该导入顺序。
  - F2 额外指数：平均股价=通达信协议 tdx_remote.fetch_daily("880003", is_index=True, market=1)
    （88 开头指数必须显式 market=1）；微盘股=东财板块 90.BK1158、高股息股=90.BK1641
    （push2his stock/get，HTTP 直连多子域轮换，不能走代理）。
  - F4 风格坐标：大盘=沪深300(000300)、小盘=中证1000(000852)（通达信协议→腾讯日K→东财指数）；
    价值=红利股 BK1641、成长=小盘成长 BK1667（东财板块实时；PE 分档兜底）。
  - F5 板块温度榜：东财 clist 含领涨股 f128（主）→ realtime.fetch_sector_flow → stock_data.fetch_sectors。
只读复用 stock_data 的降级链设施（_call_with_timeout/_get/_http_get_json/_to_finite/
fetch_mode_data/fetch_sectors/_records_from_tx_kline），不修改任何既有文件。

设计约定（对齐铁律 6/7）：
  - 比率一律小数（0.05 = 5%）；None = 无数据；全中文 source/note/degraded_reason；
  - 每源超时 ≤30s（沿用 stock_data.SOURCE_TIMEOUT_SECONDS，经 _timeout_call 包装）；
  - 所有公开函数绝不抛异常：失败 → 对应字段缺失 + degraded/degraded_reason 中文标注；
  - 沙箱（假代理 127.0.0.1:9）内真实网络必失败属预期，自动走降级路径。
"""

from __future__ import annotations

import statistics
import threading
import time
from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any, Optional

from . import stock_data
from .stock_data import DataSourceError

_UA = stock_data._UA


# ═══════════════════════════════════════════════════════════════
# 基础工具（只读复用 stock_data 的清洗/超时设施）
# ═══════════════════════════════════════════════════════════════


def _f(value: Any) -> Optional[float]:
    """非有限数 → None（契约：None = 无数据）。"""
    return stock_data._to_finite(value)


def _i(value: Any) -> Optional[int]:
    """整数化（None/非法 → None）。"""
    v = _f(value)
    return None if v is None else int(v)


def _timeout_call(fn, *args, **kwargs):
    """每源超时包装（默认 ≤30s）；测试可 monkeypatch 本模块或 stock_data._call_with_timeout。"""
    return stock_data._call_with_timeout(fn, *args, **kwargs)


# 2026-08-06 优化：60s 内存缓存（防首页并发重复打上游；长任务接口（如天气三块取数）
# 缓存后二次请求毫秒级返回，解决前端 30s 超时降级）。
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 60.0


def _cached(key: str, producer):
    """TTL 缓存包装；缓存超 128 条时整体清空（简单淘汰）。"""
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and now - hit[0] < _CACHE_TTL:
            return hit[1]
    value = producer()
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)
        if len(_CACHE) > 128:
            _CACHE.clear()
    return value


def clear_cache() -> None:
    """清空内存缓存（测试隔离用）。"""
    with _CACHE_LOCK:
        _CACHE.clear()


def _pick_col(df, *names):
    """按候选列名取列；不存在返回 None。"""
    if df is None or not hasattr(df, "columns"):
        return None
    for n in names:
        if n in df.columns:
            return n
    return None


def _norm_date(v: Any) -> Optional[str]:
    """日期值归一为 YYYY-MM-DD 字符串。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date_type):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


_ROW_KEYS = (
    ("date", ("日期", "date", "Date", "时间", "trade_date", "交易日")),
    ("close", ("最新价", "收盘", "close", "收盘价", "close_price")),
    ("open", ("今开", "开盘", "open", "开盘价")),
    ("high", ("最高", "high", "最高价")),
    ("low", ("最低", "low", "最低价")),
    ("pct", ("涨跌幅", "pct_change", "涨跌幅(%)")),
)


def _normalize_rows(df: Any) -> list[dict]:
    """DataFrame/记录列表 → [{"date","close","open","high","low","pct"}, ...]（按日期升序）。

    容忍：None → []；list[dict] → 原样；缺列 → None 占位；日期无法解析的保持原顺序。
    """
    if df is None:
        return []
    if isinstance(df, list):
        rows = [dict(r) for r in df if isinstance(r, dict)]
        return _sort_rows(rows)
    if not hasattr(df, "iterrows") or not hasattr(df, "columns"):
        return []
    cols = {key: _pick_col(df, *names) for key, names in _ROW_KEYS}
    rows = []
    for _, r in df.iterrows():
        row = {}
        for key, col in cols.items():
            row[key] = r.get(col) if col is not None else None
        rows.append(row)
    return _sort_rows(rows)


def _sort_rows(rows: list[dict]) -> list[dict]:
    """能解析日期的行按日期升序；解析失败的行保持在尾部原顺序。"""
    dated = []
    undated = []
    for r in rows:
        s = _norm_date(r.get("date"))
        try:
            parsed = datetime.strptime(str(s), "%Y-%m-%d") if s else None
        except ValueError:
            parsed = None
        if parsed is None:
            undated.append(r)
        else:
            dated.append((parsed, r))
    dated.sort(key=lambda x: x[0])
    return [r for _, r in dated] + undated


def _latest_pct(rows: list[dict]) -> Optional[float]:
    """最近一行涨跌幅（小数）：优先上游涨跌幅列（%→/100），否则收盘价环比计算。"""
    if not rows or len(rows) < 2:
        return None
    last, prev = rows[-1], rows[-2]
    pct = _f(last.get("pct"))
    if pct is not None:
        return round(pct / 100.0, 6)
    close, prev_close = _f(last.get("close")), _f(prev.get("close"))
    if close is None or not prev_close:
        return None
    return round((close - prev_close) / prev_close, 6)


def _em_http_json(url: str, params: Optional[dict] = None,
                  headers: Optional[dict] = None, timeout: float = 15) -> Any:
    """东财 push2his HTTP 直连（多子域轮换；复用 stock_data._get 的 ProxyError 直连重试）。

    东财不可走 7890 代理（ProxyError/RemoteDisconnected 偶发），必须 HTTP 直连多子域轮换。
    """
    attempts = [
        url,
        url.replace("http://push2his.eastmoney.com", "http://90.push2his.eastmoney.com"),
        url.replace("http://push2his.eastmoney.com", "http://80.push2his.eastmoney.com"),
    ]
    attempts = list(dict.fromkeys(attempts))
    last_exc: Optional[Exception] = None
    for candidate in attempts:
        try:
            resp = stock_data._get(candidate, params=params,
                                   headers=headers or {"User-Agent": _UA}, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - 轮换重试，最后一次异常上抛
            last_exc = exc
    # 2026-08-07 修复：对外只给中文短句（含 HTTP 状态码），不透传英文原文/URL
    # （此前 502 Server Error: Bad Gateway for url: ... 原文直达用户横幅）。
    _resp = getattr(last_exc, "response", None)
    status_code = getattr(_resp, "status_code", None) or getattr(last_exc, "code", None)
    if status_code:
        raise DataSourceError(f"东财数据源请求失败（HTTP {status_code}）") from last_exc
    raise DataSourceError("东财数据源请求失败（网络连接异常）") from last_exc


def _zh_source_error(exc: Exception, label: str) -> DataSourceError:
    """兜底异常 → 对外中文短句（铁律 7：降级标注说人话，英文细节留在异常链）。"""
    if isinstance(exc, DataSourceError) and str(exc).strip():
        return exc
    msg = str(exc).strip()
    # 消息本身为中文（无连续英文单词）时保留，如「取数失败」；
    # 含英文原文/URL/日期解析报错（如 time data 'i' does not match ...）则归一为中文短句。
    if msg and not __import__("re").search(r"[A-Za-z]{2,}", msg):
        return DataSourceError(msg)
    return DataSourceError(f"{label}数据源异常")


# ═══════════════════════════════════════════════════════════════
# AKShare / 通达信协议 / 东财 适配层（模块级函数，测试可 monkeypatch）
# ═══════════════════════════════════════════════════════════════


def _ak_us_index_sina(symbol: str):
    """新浪美股指数（实测可用：date/open/high/low/close/volume/amount）。"""
    import akshare as ak
    return ak.index_us_stock_sina(symbol=symbol)


def _ak_us_index_hist_em(symbol: str):
    """东财全球指数（实测 symbol="纳斯达克" 返回 NDX；列：日期/代码/名称/今开/最新价/最高/最低/振幅）。

    内部裸 requests 走 env 代理会失败：本模块已 import stock_data（HTTPS→HTTP 改写 + 直连重试），
    必须保持该导入顺序。
    """
    import akshare as ak
    return ak.index_global_hist_em(symbol=symbol)


def _ak_index_hist_em(symbol: str, start_date: str, end_date: str):
    import akshare as ak
    return ak.index_zh_a_hist(symbol=symbol, period="daily",
                              start_date=start_date, end_date=end_date)


def _ak_tx_index_daily(symbol: str):
    import akshare as ak
    return ak.stock_zh_index_daily_tx(symbol=symbol)


def _ak_spot_em():
    import akshare as ak
    return ak.stock_zh_a_spot_em()


# ═══════════════════════════════════════════════════════════════
# F3 市场天气/情绪温度（纯函数 + close 模式取数包装）
# ═══════════════════════════════════════════════════════════════


def _fmt_score(p: int) -> str:
    return f"+{p}" if p >= 0 else str(p)


def _weather_of(score: int) -> str:
    if score >= 75:
        return "晴"
    if score >= 60:
        return "晴间多云"
    if score >= 40:
        return "多云"
    return "偏冷有雨"


def _emotion_of(score: int) -> str:
    if score >= 80:
        return "火热"
    if score >= 65:
        return "偏暖"
    if score >= 45:
        return "中性"
    if score >= 30:
        return "偏冷"
    return "冰点"


def _tier_height(tier: Any) -> Optional[int]:
    """最高连板数：tier 字典取最大板数（「首板」=1）；tier 为 int 时直接使用。"""
    if tier is None:
        return None
    if isinstance(tier, dict):
        best: Optional[int] = None
        for key in tier.keys():
            k = str(key).strip()
            if k in ("首板", "1板", "1"):
                h = 1
            else:
                digits = "".join(ch for ch in k if ch.isdigit())
                if not digits:
                    continue
                try:
                    h = int(digits)
                except ValueError:
                    continue
            best = h if best is None else max(best, h)
        return best
    v = _f(tier)
    return None if v is None else int(v)


def compute_market_weather(blocks: dict) -> dict:
    """F3 市场天气/情绪温度（纯函数，评分规则冻结于 requirements-r10 §二，手算可验证）。

    输入：fetch_mode_data 的 blocks（zt_pool/breadth/sentiment 嵌套结构）；
    亦兼容扁平字段（zt_total/dt_total/zhaban_rate/lianban_height/up/down/red_rate）。
    None 输入项不加分，对应 reason 标注「数据缺失」。永不抛异常。
    """
    out = {
        "date": None,
        "weather": None,
        "emotion_score": None,
        "emotion_label": None,
        "reasons": [],
        "source": "规则引擎（基于现有行情快照）",
        "degraded": False,
        "degraded_reason": [],
    }
    try:
        if not isinstance(blocks, dict):
            blocks = {}
        zt_pool = blocks.get("zt_pool") if isinstance(blocks.get("zt_pool"), dict) else {}
        breadth = blocks.get("breadth") if isinstance(blocks.get("breadth"), dict) else {}
        sentiment = blocks.get("sentiment") if isinstance(blocks.get("sentiment"), dict) else {}
        limit_up = zt_pool.get("limit_up") if isinstance(zt_pool.get("limit_up"), dict) else {}
        limit_down = zt_pool.get("limit_down") if isinstance(zt_pool.get("limit_down"), dict) else {}

        zt_total = _i(limit_up.get("count"))
        if zt_total is None:
            zt_total = _i(blocks.get("zt_total"))
        dt_total = _i(limit_down.get("count"))
        if dt_total is None:
            dt_total = _i(blocks.get("dt_total"))
        zhaban_rate = _f(zt_pool.get("zhaban_rate"))
        if zhaban_rate is None:
            zhaban_rate = _f(blocks.get("zhaban_rate"))
        lianban_height = _tier_height(limit_up.get("tier"))
        if lianban_height is None:
            lianban_height = _i(blocks.get("lianban_height"))
        up = _f(breadth.get("up"))
        if up is None:
            up = _f(blocks.get("up"))
        down = _f(breadth.get("down"))
        if down is None:
            down = _f(blocks.get("down"))
        red_rate = _f(sentiment.get("red_rate"))
        if red_rate is None:
            red_rate = _f(blocks.get("red_rate"))

        reasons: list[str] = []
        missing: list[str] = []
        total = 0

        # 涨跌家数比 r = up/(up+down)
        if up is not None and down is not None and (up + down) > 0:
            r = up / (up + down)
            if r > 0.6:
                p = 8
            elif r > 0.5:
                p = 4
            elif r >= 0.4:
                p = 0
            elif r >= 0.3:
                p = -8
            else:
                p = -14
            total += p
            reasons.append(f"涨跌家数比 {r:.2f}：{_fmt_score(p)}")
        else:
            missing.append("涨跌家数比")
            reasons.append("涨跌家数比：数据缺失")

        # 涨停-跌停差：每 10 家 ±1，上限/下限 ±12（2026-08-06 严格化）
        if zt_total is not None and dt_total is not None:
            d = zt_total - dt_total
            if d >= 0:
                p = 1 * min(d // 10, 12)
            else:
                p = 1 * max(-((-d) // 10), -12)
            total += p
            reasons.append(f"涨停 {zt_total} 家、跌停 {dt_total} 家：{_fmt_score(p)}")
        else:
            missing.append("涨停/跌停家数")
            reasons.append("涨停/跌停家数：数据缺失")

        # 炸板率
        if zhaban_rate is not None:
            if zhaban_rate < 0.25:
                p = 6
            elif zhaban_rate <= 0.4:
                p = 0
            else:
                p = -12
            total += p
            reasons.append(f"炸板率 {zhaban_rate:.2f}：{_fmt_score(p)}")
        else:
            missing.append("炸板率")
            reasons.append("炸板率：数据缺失")

        # 连板高度
        if lianban_height is not None:
            if lianban_height >= 5:
                p = 6
            elif lianban_height >= 3:
                p = 3
            else:
                p = -4
            total += p
            reasons.append(f"连板高度 {lianban_height}：{_fmt_score(p)}")
        else:
            missing.append("连板高度")
            reasons.append("连板高度：数据缺失")

        # 昨日涨停红盘率
        if red_rate is not None:
            if red_rate > 0.6:
                p = 6
            elif red_rate < 0.4:
                p = -8
            else:
                p = 0
            total += p
            reasons.append(f"昨日涨停红盘率 {red_rate:.2f}：{_fmt_score(p)}")
        else:
            missing.append("昨日涨停红盘率")
            reasons.append("昨日涨停红盘率：数据缺失")

        out["reasons"] = reasons
        if missing:
            out["degraded"] = True
            out["degraded_reason"] = [f"数据缺失：{'、'.join(missing)}"]
        if len(missing) >= 5:
            out["weather"] = "数据缺失"
            out["emotion_score"] = None
            out["emotion_label"] = None
        else:
            score = max(0, min(100, 50 + total))
            out["weather"] = _weather_of(score)
            out["emotion_score"] = score
            out["emotion_label"] = _emotion_of(score)
    except Exception as exc:  # noqa: BLE001 - 纯函数绝不抛异常
        out["weather"] = "数据缺失"
        out["emotion_score"] = None
        out["emotion_label"] = None
        out["reasons"] = []
        out["degraded"] = True
        out["degraded_reason"] = ["规则计算异常"]
    return out


def _fetch_weather_blocks(date: str,
                          join_timeout: Optional[float] = None) -> tuple[dict, list[str]]:
    """并行取天气三块（zt_pool/breadth/sentiment）并收集降级信息。

    每块独立超时：zt_pool/sentiment 15s；breadth 30s（R12：东财 502 时降级链
    走到新浪全市场兜底（实测 ~20s），15s 会误判超时导致「涨跌家数比数据缺失」）。
    join_timeout 由调用方指定（天气 20s / 情绪 35s），超时未返回的块记「缺块」。
    返回 (blocks, degraded)：blocks 为成功块 dict，degraded 为降级原因 list。
    绝不抛异常。"""
    blocks: dict = {}
    degraded: list[str] = []

    def _fetch(name: str) -> None:
        try:
            if name == "zt_pool":
                blocks["zt_pool"] = _timeout_call(stock_data.fetch_zt_pool, date, timeout=15)
            elif name == "breadth":
                blocks["breadth"] = _timeout_call(stock_data.fetch_market_breadth, date, timeout=30)
            else:
                blocks["sentiment"] = _timeout_call(stock_data.fetch_sentiment, date, timeout=15)
        except Exception as exc:  # noqa: BLE001 - 单块失败降级，不拖垮整体
            degraded.append(str(_zh_source_error(exc, name)))

    threads = [threading.Thread(target=_fetch, args=(n,), daemon=True)
               for n in ("zt_pool", "breadth", "sentiment")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=join_timeout)
    for name in ("zt_pool", "breadth", "sentiment"):
        if name not in blocks:
            degraded.append(f"缺块：{name}")
    return blocks, degraded


def fetch_market_weather(date: str) -> dict:
    """F3 取数包装：只取天气所需三块（涨跌停/涨跌分布/情绪），并行 + 每块 ≤15s，
    避免 close 全量取数（指数/板块/龙虎榜/资讯，实测 78s）拖垮首页（前端 30s 超时降级）。
    业务口径不变（compute_market_weather 规则冻结）；60s 缓存防并发重复取数。
    绝不抛异常。"""

    def _produce() -> dict:
        blocks, errors = _fetch_weather_blocks(date, join_timeout=20)
        out = compute_market_weather(blocks)
        out["date"] = date
        if errors:
            out["degraded"] = True
            out["degraded_reason"] = list(dict.fromkeys(
                (out.get("degraded_reason") or []) + errors))
        if out["weather"] is None:
            out["weather"] = "数据缺失"
            out["emotion_score"] = None
            out["emotion_label"] = None
        return out

    return _cached(f"weather:{date}", _produce)

def fetch_market_sentiment(date: str) -> dict:
    """R12 统一情绪数据源：涨跌家数比/炸板率/连板高度/涨停跌停/红盘率等结构化指标，
    与 fetch_market_weather 同源三块（zt_pool/breadth/sentiment），供首页天气卡与
    复盘展示共用（一处取数、全局使用）；口径与 compute_market_weather 冻结规则一致；
    60s 缓存；绝不抛异常。"""

    def _produce() -> dict:
        blocks, errors = _fetch_weather_blocks(date, join_timeout=35)

        out = compute_market_weather(blocks)
        zt_pool = blocks.get("zt_pool") if isinstance(blocks.get("zt_pool"), dict) else {}
        breadth = blocks.get("breadth") if isinstance(blocks.get("breadth"), dict) else {}
        sentiment = blocks.get("sentiment") if isinstance(blocks.get("sentiment"), dict) else {}
        limit_up = zt_pool.get("limit_up") if isinstance(zt_pool.get("limit_up"), dict) else {}
        limit_down = zt_pool.get("limit_down") if isinstance(zt_pool.get("limit_down"), dict) else {}
        up, down = _f(breadth.get("up")), _f(breadth.get("down"))
        return {
            "date": date,
            "weather": out.get("weather"),
            "emotion_score": out.get("emotion_score"),
            "emotion_label": out.get("emotion_label"),
            "up_down_ratio": round(up / (up + down), 4)
            if up is not None and down is not None and (up + down) > 0 else None,
            "up_count": _i(breadth.get("up")),
            "down_count": _i(breadth.get("down")),
            "limit_up_count": _i(limit_up.get("count")),
            "limit_down_count": _i(limit_down.get("count")),
            "break_rate": _f(zt_pool.get("zhaban_rate")),
            "lianban_height": _tier_height(limit_up.get("tier")),
            "red_rate": _f(sentiment.get("red_rate")),
            "continue_rate": _f(sentiment.get("continue_rate")),
            "button_rate": _f(sentiment.get("button_rate")),
            "avg_return": _f(sentiment.get("avg_return")),
            "reasons": out.get("reasons") or [],
            "source": out.get("source"),
            "degraded": bool(out.get("degraded")) or bool(errors),
            "degraded_reason": list(dict.fromkeys(
                (out.get("degraded_reason") or []) + errors)),
        }

    return _cached(f"sentiment:{date}", _produce)



# ═══════════════════════════════════════════════════════════════
# F6 纳斯达克（综合 .IXIC + 纳指100 .NDX；新浪 → 东财全球指数 → 标注缺失）
# ═══════════════════════════════════════════════════════════════


US_INDEX_DEFS = [
    {"name": "纳斯达克综合", "symbol": ".IXIC", "em_symbol": None},
    # 东财 index_global_hist_em 仅支持「纳斯达克」（返回 NDX），.IXIC 无备用
    {"name": "纳斯达克100", "symbol": ".NDX", "em_symbol": "纳斯达克"},
]
NASDAQ_POINTS = 60


def _us_index_item(name: str, symbol: str, em_symbol: Optional[str]):
    """单指数：腾讯直连（主）→ 新浪 akshare（备）→ 东财（备，仅 NDX）。

    2026-08-06 修复：akshare 新浪源（index_us_stock_sina）内部用 py_mini_racer 解码，
    服务环境已禁用（Windows IOCP 退出死锁）→ 新浪恒失败致整卡降级；腾讯美股日K
    实测 0.1s/320 根且无需 JS 引擎，升级为主源。成功返回 (item, source)。
    """
    errors: list[str] = []
    try:
        rows = _normalize_rows(_timeout_call(_us_kline_from_tx, symbol))
        if rows:
            return _us_item_from_rows(name, symbol, rows), "腾讯"
        errors.append("腾讯无数据")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "腾讯")))
    try:
        rows = _normalize_rows(_timeout_call(_ak_us_index_sina, symbol))
        if rows:
            return _us_item_from_rows(name, symbol, rows), "新浪"
        errors.append("新浪无数据")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "新浪")))
    if em_symbol:
        try:
            rows = _normalize_rows(_timeout_call(_ak_us_index_hist_em, em_symbol))
            if rows:
                return _us_item_from_rows(name, symbol, rows), "东财"
            errors.append("东财无数据")
        except Exception as exc:  # noqa: BLE001
            errors.append(str(_zh_source_error(exc, "东财")))
    raise DataSourceError(f"{name}（{symbol}）：{'；'.join(errors)}")


def _tx_us_kline(tx_symbol: str) -> list:
    """腾讯美股日K原始响应（day 数组；实测 web.ifzq.gtimg.cn 0.1s/320 根）。"""
    resp = stock_data._get(
        "https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get",
        params={"param": f"{tx_symbol},day,,,320,qfq"},
        headers={"User-Agent": stock_data._UA},
        timeout=12,
    )
    resp.raise_for_status()
    data = (resp.json().get("data") or {}).get(tx_symbol) or {}
    return data.get("qfqday") or data.get("day") or []


def _us_kline_from_tx(symbol: str) -> list[dict]:
    """腾讯美股日K解析：.IXIC→usIXIC/.NDX→usNDX；行序 date/open/close/high/low。"""
    tx_symbol = "us" + str(symbol).lstrip(".")
    rows = []
    for line in _timeout_call(_tx_us_kline, tx_symbol, timeout=12):
        if not isinstance(line, list) or len(line) < 5:
            continue
        rows.append({
            "date": str(line[0]),
            "open": _f(line[1]),
            "close": _f(line[2]),
            "high": _f(line[3]),
            "low": _f(line[4]),
            "volume": _f(line[5]) if len(line) > 5 else None,
            "amount": _f(line[8]) if len(line) > 8 else None,
        })
    if not rows:
        raise DataSourceError(f"腾讯美股日K无数据：{symbol}")
    return rows


def _us_item_from_rows(name: str, symbol: str, rows: list[dict]) -> dict:
    last = rows[-1]
    close = _f(last.get("close"))
    if close is None:
        raise DataSourceError(f"{name}（{symbol}）无有效收盘价")
    prev = rows[-2] if len(rows) >= 2 else None
    prev_close = _f(prev.get("close")) if prev else None
    pct = _f(last.get("pct"))
    if pct is not None:
        pct = round(pct / 100.0, 6)
    elif prev_close:
        pct = round((close - prev_close) / prev_close, 6)
    points = []
    for r in rows[-NASDAQ_POINTS:]:
        d = _norm_date(r.get("date"))
        c = _f(r.get("close"))
        if d is not None and c is not None:
            points.append({"date": d, "close": c})
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "name": name,
        "symbol": symbol,
        "close": close,
        "pct_change": pct,
        "pre_close": prev_close,
        "open": _f(last.get("open")),
        "high": _f(last.get("high")),
        "low": _f(last.get("low")),
        "points": points,
        "is_today": _norm_date(last.get("date")) == today,
    }


def fetch_us_nasdaq() -> dict:
    """F6 纳斯达克：两指数独立降级，单指数失败不拖垮另一指数；60s 缓存防并发。
    绝不抛异常。"""

    def _produce() -> dict:
        items = []
        reasons: list[str] = []
        sources: set[str] = set()
        for d in US_INDEX_DEFS:
            try:
                item, src = _us_index_item(d["name"], d["symbol"], d.get("em_symbol"))
                items.append(item)
                sources.add(src)
            except Exception as exc:  # noqa: BLE001
                reasons.append(str(exc))
                items.append({
                    "name": d["name"], "symbol": d["symbol"],
                    "close": None, "pct_change": None, "pre_close": None,
                    "open": None, "high": None, "low": None,
                    "points": [], "is_today": False,
                })
        degraded = bool(reasons)
        if sources == {"腾讯"}:
            source = "腾讯"
        elif sources == {"新浪"}:
            source = "新浪"
        elif sources == {"东财"}:
            source = "东财"
        elif sources:
            source = "腾讯/新浪/东财"
        else:
            source = "数据缺失"
        has_data = any(i.get("close") is not None for i in items)
        stale = has_data and any(
            i.get("close") is not None and i.get("is_today") is False for i in items)
        note = None
        if stale:
            note = "最新数据非当日（美股交易日历/时差），走势为最近 60 交易日"
        return {
            "items": items,
            "source": source,
            "degraded": degraded,
            "degraded_reason": reasons or [],
            "note": note,
        }

    return _cached("us_nasdaq", _produce)


# ═══════════════════════════════════════════════════════════════
# F5 板块温度榜（东财 clist 含领涨股 f128 → realtime 板块链 → stock_data 板块链）
# ═══════════════════════════════════════════════════════════════


def _sector_heat_from_em() -> dict:
    """东财行业板块实时 top5/bottom5（含领涨股 f128；fltt=2 涨跌幅为百分数 → /100）。"""
    data = _timeout_call(
        stock_data._http_get_json,
        "http://push2.eastmoney.com/api/qt/clist/get",
        params={"pn": 1, "pz": 60, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f3", "fs": "m:90+t:2+f:!50",
                "fields": "f3,f12,f14,f128"},
        headers={"User-Agent": _UA},
        timeout=15,
    )
    diff = ((data.get("data") or {}).get("diff")) or []
    rows = []
    for r in diff:
        pct = _f(r.get("f3"))
        if pct is None:
            continue
        rows.append({
            "name": r.get("f14"),
            "pct_change": round(pct / 100.0, 6),
            "leader": r.get("f128"),
        })
    if not rows:
        raise DataSourceError("东财板块实时无数据")
    hot = sorted(rows, key=lambda x: x["pct_change"], reverse=True)[:5]
    cold = sorted(rows, key=lambda x: x["pct_change"])[:5]
    return {"hot": hot, "cold": cold, "source": "东财行业",
            "degraded": False, "degraded_reason": []}


_SECTOR_SOURCE_MAP = {
    "同花顺": "同花顺行业",
    "同花顺(浏览器)": "同花顺(浏览器)行业",
    "腾讯行业": "腾讯行业",
    "东财": "东财行业",
    "本地缓存": "本地缓存",
    "本地缓存(旧版)": "本地缓存(旧版)",
    "数据缺失": "数据缺失",
}


def fetch_sector_heat() -> dict:
    """F5 板块温度榜：涨幅 top5 + 跌幅 bottom5（name/pct_change 小数/leader 领涨股）。

    链：东财 clist（含领涨股）→ realtime.fetch_sector_flow → stock_data.fetch_sectors → 缺失。
    """
    errors: list[str] = []
    # 主源：东财 clist（含领涨股 f128）
    try:
        return _sector_heat_from_em()
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "东财")))
    # 备用1：现有实时板块链（东财→腾讯→浏览器；领涨股可能缺失）
    try:
        from .realtime import fetch_sector_flow

        b = _timeout_call(fetch_sector_flow)

        def row(x: Any) -> dict:
            return {
                "name": x.get("name") if isinstance(x, dict) else None,
                "pct_change": _f(x.get("pct_change")) if isinstance(x, dict) else None,
                "leader": x.get("leading_stock") if isinstance(x, dict) else None,
            }

        hot = [row(x) for x in (b.get("top") or [])]
        cold = [row(x) for x in (b.get("bottom") or [])]
        if hot or cold:
            return {
                "hot": hot, "cold": cold,
                "source": str(b.get("source") or "行业板块"),
                "degraded": True,
                "degraded_reason": ["东财实时（含领涨股）不可用，降级现有板块链（领涨股可能缺失）"],
                "note": b.get("note"),
            }
        errors.append("板块链无数据")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "板块链")))
    # 备用2：stock_data.fetch_sectors（同花顺→腾讯→东财→缓存，含领涨股）
    try:
        blk = _timeout_call(stock_data.fetch_sectors, datetime.now().strftime("%Y-%m-%d"))
        if isinstance(blk, dict):
            def rows(items: Any) -> list[dict]:
                out = []
                for r in items or []:
                    if not isinstance(r, dict):
                        continue
                    out.append({
                        "name": r.get("name"),
                        "pct_change": _f(r.get("pct_change")),
                        "leader": r.get("leading_stock"),
                    })
                return out

            hot = rows(blk.get("top5"))
            cold = rows(blk.get("bottom5"))
            if hot or cold:
                raw_source = str(blk.get("source") or "数据缺失")
                return {
                    "hot": hot, "cold": cold,
                    "source": _SECTOR_SOURCE_MAP.get(raw_source, raw_source),
                    "degraded": True,
                    "degraded_reason": ["东财实时/板块链不可用，降级历史板块链"] +
                                       list(blk.get("degraded_reason") or [])[:2],
                    "note": blk.get("note"),
                }
        errors.append("历史板块链无数据")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "历史板块")))
    return {
        "hot": [], "cold": [], "source": "数据缺失", "degraded": True,
        "degraded_reason": errors or ["各数据源均不可用"],
        "note": "板块温度榜数据缺失",
    }


# ═══════════════════════════════════════════════════════════════
# F2 额外指数（平均股价/微盘股/高股息股）
# 平均股价：通达信协议日K（88 开头指数须显式 market=1）；微盘/高股息：东财板块实时。
# 代码已由主编排 2026-08-06 提权实测冻结。
# ═══════════════════════════════════════════════════════════════


EXTRA_INDEX_DEFS = [
    {"name": "平均股价", "code": "880003", "source": "通达信协议", "secid": None,
     "note": "通达信协议日K（88 开头指数，market=1；已实测）"},
    {"name": "微盘股", "code": "BK1158", "source": "东财板块", "secid": "90.BK1158",
     "note": "东财板块实时（已实测）"},
    {"name": "高股息股", "code": "BK1641", "source": "东财板块", "secid": "90.BK1641",
     "note": "东财板块实时·红利股代理（已实测）"},
]


def _em_board_quote(secid: str) -> tuple[dict, bool]:
    """东财板块/指数实时快照（push2his stock/get；HTTP 直连多子域轮换）。

    无 fltt 参数 → 原值 ×100：f43 最新点位、f60 昨收（÷100）；
    f170 涨跌幅为 ×100 的百分数（3→0.03%，÷10000 转小数）。

    2026-08-07：push2his 实时域整体 502（东财行情节点限流）时兜底 push2delay
    延迟行情域（实测可用，字段格式一致）；返回 (data, delayed) 供调用方如实标注来源。
    """
    try:
        data = _timeout_call(
            _em_http_json,
            "http://push2his.eastmoney.com/api/qt/stock/get",
            params={"secid": secid, "fields": "f43,f44,f45,f46,f57,f58,f60,f169,f170,f171"},
            headers={"User-Agent": _UA},
            timeout=15,
        )
        delayed = False
    except DataSourceError:
        data = _timeout_call(
            _em_http_json,
            "http://push2delay.eastmoney.com/api/qt/stock/get",
            params={"secid": secid, "fields": "f43,f44,f45,f46,f57,f58,f60,f169,f170,f171"},
            headers={"User-Agent": _UA},
            timeout=15,
        )
        delayed = True
    d = (data.get("data") or {}) if isinstance(data, dict) else {}
    if not d:
        raise DataSourceError(f"东财板块实时无数据：{secid}")
    return d, delayed


def _em_board_item(name: str, code: str, secid: str, source: str) -> dict:
    d, delayed = _em_board_quote(secid)
    close_raw = _f(d.get("f43"))
    pct_raw = _f(d.get("f170"))
    if close_raw is None or pct_raw is None:
        raise DataSourceError(f"{name}（{secid}）东财板块字段缺失")
    return {
        "name": name,
        "code": code,
        "close": round(close_raw / 100.0, 6),
        "pct_change": round(pct_raw / 10000.0, 6),
        "source": "东财板块·延迟行情" if delayed else source,
    }


def _tdx_index_item(name: str, code: str, source: str) -> dict:
    """通达信协议日K（88 开头指数必须显式 market=1；_market_of 会误判为深市）。"""
    try:
        from .tdx_remote import fetch_daily

        bars = _timeout_call(fetch_daily, code=code, count=250, is_index=True, market=1)
        if len(bars) >= 2:
            last, prev = bars[-1], bars[-2]
            close = _f(last.get("close"))
            prev_close = _f(prev.get("close"))
            if close is not None and prev_close:
                return {
                    "name": name, "code": code, "close": close,
                    "pct_change": round((close - prev_close) / prev_close, 6),
                    "source": source,
                }
        raise DataSourceError(f"{name}（{code}）通达信协议日K不足")
    except DataSourceError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _zh_source_error(exc, "通达信协议") from exc


def _extra_index_item(defn: dict):
    """单指数：东财板块（BK 系）或通达信协议（88 系）→ akshare 兜底；返回 (item, source)。"""
    name, code = defn["name"], defn["code"]
    if defn.get("secid"):
        # BK 系（东财板块代码）：东财板块实时为唯一数据源，失败直接中文标注。
        # 2026-08-07 修复：此前的 akshare 通达信日线兜底对板块代码无意义——
        # stock_zh_index_daily_tx("BK1158") 对无效代码解析日期抛 ValueError
        # （time data 'i' does not match ...），英文异常原文被透传到前端横幅。
        try:
            return _em_board_item(name, code, defn["secid"], defn["source"]), defn["source"]
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DataSourceError):
                raise DataSourceError(f"{name}（{code}）：{exc}") from exc
            raise DataSourceError(f"{name}（{code}）数据源异常") from exc
    # 88 系（平均股价）：通达信协议 → akshare 通达信日线 → 东财指数日线
    tdx_err = ""
    try:
        return _tdx_index_item(name, code, defn["source"]), defn["source"]
    except Exception as exc:  # noqa: BLE001
        tdx_err = str(_zh_source_error(exc, "通达信协议"))
    try:
        rows = _normalize_rows(_timeout_call(_ak_tx_index_daily, code))
        if rows:
            return _extra_item_from_rows(name, code, rows, "通达信指数"), "通达信指数"
    except Exception as exc:  # noqa: BLE001
        tdx_err = f"{tdx_err}；{_zh_source_error(exc, 'akshare通达信')}"
    start = (datetime.now() - timedelta(days=150)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    try:
        rows = _normalize_rows(_timeout_call(_ak_index_hist_em, code, start, end))
        if rows:
            return _extra_item_from_rows(name, code, rows, "东财指数"), "东财指数"
    except Exception as exc:  # noqa: BLE001
        tdx_err = f"{tdx_err}；{_zh_source_error(exc, '东财指数')}"
    raise DataSourceError(f"{name}（{code}）：{tdx_err}")


def _extra_item_from_rows(name: str, code: str, rows: list[dict], src: str) -> dict:
    last = rows[-1]
    close = _f(last.get("close"))
    if close is None:
        raise DataSourceError(f"{name}（{src}）无有效收盘价")
    prev = rows[-2] if len(rows) >= 2 else None
    prev_close = _f(prev.get("close")) if prev else None
    pct = _f(last.get("pct"))
    if pct is not None:
        pct = round(pct / 100.0, 6)
    elif prev_close:
        pct = round((close - prev_close) / prev_close, 6)
    return {
        "name": name,
        "code": code,
        "close": close,
        "pct_change": pct,
        "source": src,
    }


def fetch_extra_indices() -> dict:
    """F2 额外指数：3 指数独立降级；缺失整卡标注，不阻塞首页。绝不抛异常。"""
    items = []
    reasons: list[str] = []
    for d in EXTRA_INDEX_DEFS:
        try:
            item, _src = _extra_index_item(d)
            items.append(item)
        except Exception as exc:  # noqa: BLE001
            reasons.append(str(exc))
            items.append({
                "name": d["name"], "code": d["code"],
                "close": None, "pct_change": None, "source": "数据缺失",
            })
    degraded = bool(reasons)
    has_data = any(i.get("close") is not None for i in items)
    note = None
    if degraded:
        note = "部分指数缺失（数据源失败或代码待复核），已独立降级标注"
    return {
        "items": items,
        "source": "AKShare" if has_data else "数据缺失",
        "degraded": degraded,
        "degraded_reason": reasons or [],
        "note": note,
    }


# ═══════════════════════════════════════════════════════════════
# F4 风格坐标（大盘 vs 小盘；价值 vs 成长）
# 大盘=沪深300(000300)、小盘=中证1000(000852)；价值=红利股 BK1641、成长=小盘成长 BK1667。
# ═══════════════════════════════════════════════════════════════


STYLE_LARGE = {"name": "沪深300", "code": "000300", "tx_symbol": "sh000300"}
STYLE_SMALL = {"name": "中证1000", "code": "000852", "tx_symbol": "sh000852"}
STYLE_VALUE = {"name": "红利股", "code": "BK1641", "secid": "90.BK1641"}
STYLE_GROWTH = {"name": "小盘成长", "code": "BK1667", "secid": "90.BK1667"}


def _style_index_pct(code: str, tx_symbol: str, name: str):
    """指数日线涨跌幅（小数）：通达信协议 → 腾讯日K → 东财指数日线。失败抛 DataSourceError。"""
    errors: list[str] = []
    try:
        from .tdx_remote import fetch_daily

        bars = _timeout_call(fetch_daily, code=code, count=250, is_index=True, market=1)
        if len(bars) >= 2:
            last, prev = bars[-1], bars[-2]
            close, prev_close = _f(last.get("close")), _f(prev.get("close"))
            if close is not None and prev_close:
                return round((close - prev_close) / prev_close, 6)
        errors.append("通达信协议日K不足")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "通达信协议")))
    try:
        recs = _timeout_call(stock_data._records_from_tx_kline, tx_symbol)
        if len(recs) >= 2:
            last, prev = recs[-1], recs[-2]
            close, prev_close = _f(last.get("close")), _f(prev.get("close"))
            if close is not None and prev_close:
                return round((close - prev_close) / prev_close, 6)
        errors.append("腾讯日K不足")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "腾讯日K")))
    start = (datetime.now() - timedelta(days=150)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    try:
        rows = _normalize_rows(_timeout_call(_ak_index_hist_em, code, start, end))
        pct = _latest_pct(rows)
        if pct is not None:
            return pct
        errors.append("东财指数日线不足")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "东财指数")))
    raise DataSourceError(f"{name}：{'；'.join(errors)}")


def _style_board_pct(name: str, secid: str) -> float:
    """东财板块涨跌幅（小数）：f170 = 涨跌幅 ×100 的百分数（3→0.03%→0.0003）。"""
    d, delayed = _em_board_quote(secid)
    pct_raw = _f(d.get("f170"))
    if pct_raw is None:
        raise DataSourceError(f"{name}（{secid}）东财板块涨跌幅缺失")
    return round(pct_raw / 10000.0, 6), delayed


def _pe_bucket_pcts():
    """全市场 PE 分档兜底：PE≤中位数=价值档，PE>中位数=成长档；返回 (价值, 成长) 小数。"""
    df = _timeout_call(_ak_spot_em)
    if df is None or df.empty:
        raise DataSourceError("全市场行情为空")
    code_col = _pick_col(df, "代码", "code")
    name_col = _pick_col(df, "名称", "name")
    pct_col = _pick_col(df, "涨跌幅", "pct_change")
    pe_col = _pick_col(df, "市盈率-动态", "市盈率", "pe", "pe_ttm")
    if not (code_col and pct_col and pe_col):
        raise DataSourceError("全市场行情缺少必要列（代码/涨跌幅/市盈率）")
    samples: list[tuple[float, float]] = []
    for _, r in df.iterrows():
        code = str(r.get(code_col) or "")
        name = str(r.get(name_col) or "")
        if code.startswith(("4", "8", "92")) or "ST" in name.upper() or "退" in name:
            continue
        pe = _f(r.get(pe_col))
        pct = _f(r.get(pct_col))
        if pe is None or pe <= 0 or pct is None:
            continue  # 剔除亏损股（PE≤0）与缺失行
        samples.append((pe, pct / 100.0))
    if len(samples) < 20:
        raise DataSourceError(f"PE 分档有效样本不足（{len(samples)}）")
    samples.sort(key=lambda x: x[0])
    median = statistics.median([x[0] for x in samples])
    value = [x[1] for x in samples if x[0] <= median]
    growth = [x[1] for x in samples if x[0] > median]
    if not value or not growth:
        raise DataSourceError("PE 分档后价值/成长档为空")
    return round(sum(value) / len(value), 6), round(sum(growth) / len(growth), 6)


def _value_growth_pcts():
    """价值/成长强弱：东财板块（红利股 vs 小盘成长）→ 全市场 PE 分档兜底。"""
    errors: list[str] = []
    try:
        v, v_delayed = _style_board_pct(STYLE_VALUE["name"], STYLE_VALUE["secid"])
        g, g_delayed = _style_board_pct(STYLE_GROWTH["name"], STYLE_GROWTH["secid"])
        src = "东财板块·延迟行情" if (v_delayed or g_delayed) else "东财板块实时"
        return v, g, src
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "东财板块")))
    try:
        v, g = _pe_bucket_pcts()
        return v, g, "全市场PE分档"
    except Exception as exc:  # noqa: BLE001
        errors.append(str(_zh_source_error(exc, "PE分档")))
    raise DataSourceError("价值/成长：东财板块与 PE 分档均不可用（" + "；".join(errors) + "）")


def _dim_score(a_pct: float, b_pct: float) -> int:
    """维度得分：score = 50 + (a - b) × 200（2026-08-07 灵敏度翻倍：
    1 个百分点 = 2 分，短线强弱差异更可辨），clamp 0-100 取整。"""
    s = 50.0 + (a_pct - b_pct) * 200.0
    return int(round(max(0.0, min(100.0, s))))


def _dim_label(score: int, a_pct: float, b_pct: float, a_name: str, b_name: str) -> str:
    """维度结论细分（2026-08-07）：按双方涨跌幅差值分档，不再把 ±2pp 判成「均衡」。
    |差值| <0.5pp 均衡；0.5–1.5pp 略占优；≥1.5pp 明显占优。"""
    diff_pp = abs(a_pct - b_pct) * 100
    if diff_pp < 0.5:
        return "均衡"
    dominant = a_name if a_pct > b_pct else b_name
    return f"{dominant}明显占优" if diff_pp >= 1.5 else f"{dominant}略占优"


def fetch_style_quad() -> dict:
    """F4 风格坐标：两个维度独立计算，任一维度缺失 → 该维度「数据缺失」，不整体降级。"""
    reasons: list[str] = []
    sources: set[str] = set()

    large_vs_small = {
        "score": None, "label": "数据缺失",
        "desc": "沪深300/中证1000 数据缺失",
    }
    try:
        large_pct = _style_index_pct(
            STYLE_LARGE["code"], STYLE_LARGE["tx_symbol"], STYLE_LARGE["name"])
        small_pct = _style_index_pct(
            STYLE_SMALL["code"], STYLE_SMALL["tx_symbol"], STYLE_SMALL["name"])
        sources.add("指数日线")
        score = _dim_score(large_pct, small_pct)
        label = _dim_label(score, large_pct, small_pct, "大盘", "小盘")
        large_vs_small = {
            "score": score,
            "label": label,
            "desc": f"沪深300 {large_pct * 100:+.2f}%，中证1000 {small_pct * 100:+.2f}%",
        }
    except Exception as exc:  # noqa: BLE001
        reasons.append(str(_zh_source_error(exc, "大盘vs小盘")))

    value_vs_growth = {
        "score": None, "label": "数据缺失",
        "desc": "价值/成长数据缺失",
    }
    try:
        v_pct, g_pct, vg_source = _value_growth_pcts()
        sources.add(vg_source)
        score = _dim_score(v_pct, g_pct)
        label = _dim_label(score, v_pct, g_pct, "价值", "成长")
        value_vs_growth = {
            "score": score,
            "label": label,
            "desc": f"红利股 {v_pct * 100:+.2f}%（价值），小盘成长 {g_pct * 100:+.2f}%（成长）",
        }
    except Exception as exc:  # noqa: BLE001
        reasons.append(str(_zh_source_error(exc, "价值vs成长")))

    degraded = bool(reasons)
    if "东财板块实时" in sources or "东财板块·延迟行情" in sources:
        source = "指数代理+东财板块实时"
    elif sources:
        source = "指数代理+全市场PE分档"
    else:
        source = "数据缺失"
    return {
        "large_vs_small": large_vs_small,
        "value_vs_growth": value_vs_growth,
        "source": source,
        "degraded": degraded,
        "degraded_reason": reasons or [],
        "note": None,
    }


__all__ = [
    "compute_market_weather", "fetch_market_weather", "fetch_market_sentiment", "fetch_us_nasdaq",
    "fetch_sector_heat", "fetch_extra_indices", "fetch_style_quad",
]
