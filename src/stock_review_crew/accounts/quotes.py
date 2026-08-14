# -*- coding: utf-8 -*-
"""R14 行情缓存：``data/quotes_cache/{date}.json`` 的 load/save/get_day_quotes。

口径（docs/requirements-r14.md §四.4.2，字段唯一真源）：
- 缓存文件结构：{date, fetched_at, source, quotes[code]={name, pre_close, high,
  low, close}, degraded[]}；缺失代码**不在文件里**（而非 None）；
- 未命中 → 走现有降级链抓取（主源 stock_data.fetch_stock_info，
  腾讯 qt.gtimg.cn 兜底），失败绝不静默：degraded 标注 + 该代码保持缺失；
- 数据根尊重 STOCK_REVIEW_DATA_DIR（测试隔离），绝不写仓库 data/；
- fetched_at 仅是缓存元数据（抓取时间），绝不进入重放产物（确定性要求）；
- fixture 模式 = 确定性样例行情（不读缓存、不取网络），供测试/回归。

用法：:

    from stock_review_crew.accounts.quotes import get_day_quotes
    payload = get_day_quotes("2026-08-07", ["600519", "000001"])
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

_QUOTES_DIR = "quotes_cache"

# 腾讯兜底前清理代理环境变量（PLAYBOOK §1：沙箱注入假代理会阻断直连）
def _base_root() -> Path:
    """数据根：优先 STOCK_REVIEW_DATA_DIR（测试隔离），否则仓库 data/。"""
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data"


def quotes_cache_root() -> Path:
    """行情缓存根目录：``data/quotes_cache``（可被 STOCK_REVIEW_DATA_DIR 覆盖）。"""
    return _base_root() / _QUOTES_DIR


def cache_path(date_str: str) -> Path:
    """单日缓存文件路径。"""
    return quotes_cache_root() / f"{date_str}.json"


def quote_path(date_str: str) -> Path:
    """单日缓存文件路径（cache_path 别名）。"""
    return cache_path(date_str)


def load_quotes(date_str: str) -> Optional[dict]:
    """读取单日行情缓存；不存在/损坏 → None（由 ensure_quotes 重建）。"""
    try:
        p = cache_path(date_str)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001 - 损坏缓存重建，绝不抛异常
        pass
    return None


def load_cache(date_str: str) -> Optional[dict]:
    """读取单日行情缓存（load_quotes 别名）。"""
    return load_quotes(date_str)


def save_quotes(date_str: str, payload: dict, fetched_at: Optional[str] = None) -> Path:
    """落盘单日行情缓存（幂等，可覆盖）。返回缓存文件路径。"""
    d = quotes_cache_root()
    d.mkdir(parents=True, exist_ok=True)
    body = dict(payload or {})
    body["date"] = str(body.get("date") or date_str)
    body.setdefault("quotes", {})
    body.setdefault("degraded", [])
    body.setdefault("source", "unknown")
    if fetched_at is None:
        fetched_at = datetime.now().isoformat(timespec="seconds")
    body["fetched_at"] = fetched_at
    p = cache_path(date_str)
    p.write_text(
        json.dumps(body, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return p


def _to_f(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # 拒绝 NaN


def fetch_stock_name(code: str) -> Optional[str]:
    """获取股票名称：腾讯行情（Session 级直连，不动进程环境变量）→ None。

    2026-08-11 修复：此前 os.environ.pop 永久删除代理变量且不恢复，
    污染后续测试/服务进程（东财取数被迫直连被限流）。
    requests.Session(trust_env=False) 已足够绕过代理。
    """
    try:
        import requests
        s = requests.Session()
        s.trust_env = False
        prefix = "sh" if code.startswith(("6", "9")) else (
            "bj" if code.startswith(("4", "8")) else "sz")
        r = s.get(f"https://qt.gtimg.cn/q={prefix}{code}", timeout=10)
        parts = r.text.split("~")
        if len(parts) > 1 and parts[1]:
            return str(parts[1]).strip()
    except Exception:  # noqa: BLE001 - 兜底失败返回 None，由调用方降级标注
        pass
    return None


def _fetch_tx_quote(code: str) -> Optional[dict]:
    """腾讯 qt.gtimg.cn 实时兜底：name/pre_close/high/low/close（实时价，非历史）。"""
    try:
        import requests
        s = requests.Session()
        s.trust_env = False
        prefix = "sh" if code.startswith(("6", "9")) else (
            "bj" if code.startswith(("4", "8")) else "sz")
        r = s.get(f"https://qt.gtimg.cn/q={prefix}{code}", timeout=10)
        parts = r.text.split("~")
        # 字段：1=名称 3=最新价 4=昨收 33=最高 34=最低
        if len(parts) > 34:
            name = str(parts[1]).strip() or code
            close = _to_f(parts[3])
            pre_close = _to_f(parts[4])
            high = _to_f(parts[33])
            low = _to_f(parts[34])
            if pre_close is not None and high is not None and low is not None:
                return {
                    "name": name,
                    "pre_close": pre_close,
                    "high": high,
                    "low": low,
                    "close": close if close is not None else pre_close,
                }
    except Exception:  # noqa: BLE001 - 兜底失败返回 None
        pass
    return None


def _fetch_day_quote_ex(code: str, date_str: str) -> tuple[Optional[dict], str]:
    """内部实现：返回 (行情, 来源)。来源：fetch_stock_info / 腾讯qt.gtimg.cn / ""。

    2026-08-13（缺陷修复）：腾讯 qt.gtimg.cn 是实时价（非历史），仅允许在
    ``date_str`` 为当天时兜底；历史日期主源失败 → 返回 (None, "")，绝不把
    实时价当作历史行情去撮合历史计划（避免历史撮合被实时价污染）。
    """
    try:
        from stock_review_crew.tools.stock_data import fetch_stock_info

        info = fetch_stock_info(code, date_str)
        name = None
        quote = info.get("quote") if isinstance(info.get("quote"), dict) else {}
        if isinstance(quote, dict) and quote.get("name"):
            name = str(quote["name"])
        if name is None and info.get("name"):
            name = str(info["name"])
        if name is None:
            daily = info.get("daily") or []
            if daily and daily[-1].get("name"):
                name = str(daily[-1]["name"])
        if name is None:
            name = fetch_stock_name(code)
        pre_close = quote.get("pre_close")
        high = quote.get("high")
        low = quote.get("low")
        close = quote.get("close")
        if pre_close is None:
            pre_close = info.get("pre_close")
        if high is None:
            high = info.get("high")
        if low is None:
            low = info.get("low")
        if close is None:
            close = info.get("close")
        if pre_close is None or high is None or low is None:
            daily = info.get("daily") or []
            if daily:
                last = daily[-1]
                pre_close = last.get("pre_close")
                high = last.get("high")
                low = last.get("low")
                close = last.get("close")
        if pre_close is None or high is None or low is None:
            return None, ""
        return {
            "date": date_str,
            "name": name,
            "pre_close": float(pre_close),
            "high": float(high),
            "low": float(low),
            "close": float(close) if close is not None else float(pre_close),
        }, "fetch_stock_info"
    except Exception:  # noqa: BLE001 - 主源失败；仅当天允许腾讯实时兜底
        pass
    if date_str != datetime.now().strftime("%Y-%m-%d"):
        # 历史日期：不采信实时价（实时价 ≠ 历史行情），标记缺失由调用方降级
        return None, ""
    tx = _fetch_tx_quote(code)
    if tx is not None:
        tx["date"] = date_str
        return tx, "腾讯qt.gtimg.cn"
    return None, ""


def fetch_day_quote(code: str, date_str: str) -> Optional[dict]:
    """复用现有降级链获取当日行情（pre_close/high/low/close）：
    主源 stock_data.fetch_stock_info → 腾讯 qt.gtimg.cn 兜底（实时价，非历史，
    **仅当天允许**；历史日期主源失败返回 None，不采信实时价）。
    失败返回 None（调用方负责 degraded 标注，绝不静默）。
    """
    quote, _ = _fetch_day_quote_ex(code, date_str)
    return quote


def fixture_quotes(date_str: str, codes: list[str]) -> dict:
    """确定性样例行情（供测试/回归）：按 code+date 哈希生成稳定价格。
    当日 +2%（非涨停），区间 [前收×0.98, 前收×1.05] 覆盖典型目标价
    （=前收/收盘附近），保证引擎链路可成交；非真实价格。
    """
    quotes: dict[str, dict] = {}
    for code in codes:
        seed = int(hashlib.sha256(f"{code}|{date_str}".encode("utf-8"))
                   .hexdigest()[:8], 16)
        pre_close = round(10.0 + (seed % 2000) / 100.0, 2)  # 10.00–29.99
        close = round(pre_close * 1.02, 2)
        high = round(pre_close * 1.05, 2)
        low = round(pre_close * 0.98, 2)
        quotes[code] = {
            "name": f"样例{code}",
            "pre_close": pre_close,
            "high": high,
            "low": low,
            "close": close,
        }
    return quotes


def get_day_quotes(date_str: str, codes: list[str] | tuple[str, ...] | set[str],
                   fetcher: Optional[Callable[[str], Optional[dict]]] = None,
                   fixture: bool = False) -> dict:
    """确保 date_str 日 codes 的行情可用，返回 payload：{date, source, quotes,
    degraded, fetched_at}。

    - 缓存文件已存在 → 已命中的代码以缓存为权威（不重抓、不联网、保持确定性），
      缺失代码逐 code 补抓并合并落盘（2026-08-14 起）；重放离线可用且确定性
      （同缓存同输出）；补抓仍失败的代码保持缺失并在 degraded 标注，绝不静默；
    - 缓存文件不存在/损坏 → 未命中逐 code 调用 fetcher 抓取：fetcher(code) ->
      {name, pre_close, high, low, close} | None；None/抛异常 = 该代码失败 →
      degraded 标注且不在结果里（绝不静默）；fetcher 缺省 = 真实降级链
      （fetch_stock_info → 腾讯 qt.gtimg.cn 兜底）；抓取后落盘缓存文件
      （{date, fetched_at, source, quotes, degraded}）；
    - fixture=True：生成确定性样例行情，不读缓存/不取网络。
    """
    codes = sorted({str(c) for c in (codes or [])})
    if fixture:
        return {
            "date": date_str,
            "source": "fixture",
            "fetched_at": "",
            "quotes": fixture_quotes(date_str, codes),
            "degraded": [f"fixture 确定性样例行情（{date_str}，非真实价格）"],
        }

    payload = load_quotes(date_str)
    if payload is None:
        # 无缓存文件（或损坏）→ 从空缓存走降级链抓取并落盘
        payload = {"date": date_str, "source": "cache", "quotes": {},
                   "degraded": [], "fetched_at": ""}
    # 缓存文件已存在时：已命中的代码以缓存为权威（不重抓、保持确定性/离线可用），
    # 缺失代码逐 code 补抓并合并落盘。2026-08-14 修复：复盘/历史重放生成的缓存是
    # 部分快照（只含当时取数过的股票），整体当作权威直接返回会把计划股的行情挡在
    # 缓存外 → 逐日撮合「行情缺失」0 成交。
    quotes = payload.setdefault("quotes", {})
    degraded = payload.setdefault("degraded", [])
    missing = [c for c in codes if c not in quotes]
    if not missing:
        return payload

    use_chain = fetcher is None
    sources: list[str] = []
    for code in missing:
        quote = None
        source = ""
        if use_chain:
            quote, source = _fetch_day_quote_ex(code, date_str)
        else:
            try:
                quote = fetcher(code)  # type: ignore[misc]
                source = "fetcher"
            except Exception:  # noqa: BLE001 - 抓取异常按失败处理（降级标注）
                quote = None
        if (quote is not None and quote.get("pre_close") is not None
                and quote.get("high") is not None and quote.get("low") is not None):
            close = quote.get("close")
            quotes[code] = {
                "name": quote.get("name") or code,
                "pre_close": float(quote["pre_close"]),
                "high": float(quote["high"]),
                "low": float(quote["low"]),
                "close": float(close) if close is not None else float(quote["pre_close"]),
            }
            if source and source not in sources:
                sources.append(source)
            if source == "腾讯qt.gtimg.cn":
                note = (f"{code} {date_str} 历史行情主源不可用，"
                        "使用腾讯实时行情兜底（非历史精确值）")
                if note not in degraded:
                    degraded.append(note)
        else:
            if use_chain and date_str != datetime.now().strftime("%Y-%m-%d"):
                note = (f"{code} {date_str} 历史行情主源不可用，"
                        "且不采信实时价，标记缺失")
            else:
                note = (f"{code} {date_str} 行情获取失败"
                        "（主源+腾讯兜底均不可用）" if use_chain
                        else f"{code} {date_str} 行情获取失败（抓取器返回空/异常）")
            if note not in degraded:
                degraded.append(note)
    payload["source"] = ("+".join(sources) if sources
                         else ("degraded" if missing
                               else payload.get("source") or "cache"))
    payload["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    save_quotes(date_str, payload)
    return payload


def ensure_quotes(date_str: str, codes: list[str] | tuple[str, ...] | set[str],
                  fixture: bool = False) -> dict:
    """确保 date_str 日 codes 的行情可用（get_day_quotes 别名，默认真实降级链）。
    供账户重放等批量入口使用。"""
    return get_day_quotes(date_str, codes, fetcher=None, fixture=fixture)
