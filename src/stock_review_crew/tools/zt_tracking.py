# -*- coding: utf-8 -*-
"""涨停复盘数据层（阶段 A）。

提供（均已按已确认口径 C1-C19 实现）：
- fetch_zt_full(date)            全量涨停池（东财 → 同花顺 → Tushare 计算 → 缺失）
- filter_drop(stocks, date)      过滤 ST/退市/上市未满一年/北证（双保险见 C8/C7）
- tag_zt_stocks(...)             个股标签：连板/封板时间/一字T字(C15)/流通市值分档(C1)/龙虎榜 join
- fetch_lhb_seats(date)          龙虎榜席位（东财数据中心直连，已实证）+ seat_labels 识别(C5/D4)
- board_cluster(...)             板块聚类 + 梯队完整(C3) → 重点关注
- run_tracking_day(date)         单日流水线（fetch→filter→tag→seats→cluster→落盘）

降级链铁律：主源 → 备用 → 标注「数据缺失/估算」，绝不静默错报。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from . import stock_data
from . import kaipanla_api
from .. import config
from ..storage import zt_tracking as zt_store

# 东财数据中心（龙虎榜席位报表，2026-08-29 实测可用）
_EM_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"


class ZtError(RuntimeError):
    """涨停复盘数据层错误（中文，供降级链标注）。"""


def _fmt(date: str) -> str:
    return stock_data._d(date)


def _pct(v: Any) -> Optional[float]:
    """百分数 → 小数（契约：比率一律小数）。"""
    f = stock_data._to_finite(v)
    return round(f / 100.0, 6) if f is not None else None


def _n(v: Any) -> Optional[float]:
    return stock_data._to_finite(v)


# ═══════════════════════════════════════════════════════════════
# A1 全量涨停池（不截断，含市值/封板字段）
# ═══════════════════════════════════════════════════════════════

_ZT_ROWS = ("代码", "名称", "涨跌幅", "最新价", "换手率", "连板数", "所属行业", "成交额",
            "封板资金", "首次封板时间", "最后封板时间", "炸板次数", "流通市值", "总市值")


def _em_zt_rows(date: str) -> list[dict]:
    df = stock_data._call_with_timeout(stock_data._em_zt_pool, _fmt(date))
    if df is None or df.empty:
        raise ZtError("东财涨停池无数据")
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "code": str(r.get("代码") or "").zfill(6),
            "name": r.get("名称"),
            "pct_change": _pct(r.get("涨跌幅")),
            "price": _n(r.get("最新价")),
            "turnover": _pct(r.get("换手率")),
            "lianban": int(r["连板数"]) if r.get("连板数") is not None else None,
            "industry": r.get("所属行业"),
            "amount": _n(r.get("成交额")),
            "seal_amount": _n(r.get("封板资金")),
            "first_seal": str(r.get("首次封板时间") or "") or None,
            "last_seal": str(r.get("最后封板时间") or "") or None,
            "zhaban_times": int(r["炸板次数"]) if r.get("炸板次数") is not None else None,
            "cap_float": _n(r.get("流通市值")),
            "cap_total": _n(r.get("总市值")),
            "raw_pool_source": "东财",
        })
    return rows


def _ths_zt_rows(date: str) -> list[dict]:
    """备用：同花顺免费接口（字段不全，标注 note）。"""
    from .ths_crawler import build_zt_block
    block = build_zt_block(date)
    stocks = block.get("limit_up") or {}
    out = []
    for s in stocks.get("stocks", []):
        out.append({
            "code": str(s.get("code") or "").zfill(6),
            "name": s.get("name"),
            "pct_change": s.get("pct_change"),
            "price": None, "turnover": None, "lianban": None,
            "industry": None, "amount": None, "seal_amount": None,
            "first_seal": None, "last_seal": None, "zhaban_times": None,
            "cap_float": None, "cap_total": None, "raw_pool_source": "同花顺",
        })
    return out


def _ts_zt_rows(date: str) -> list[dict]:
    """备用：Tushare 精确涨停价计算（无连板/封板/市值细节，标注）。"""
    fmt = stock_data._d(date)
    df = stock_data._filter_st_bj(stock_data._ts_daily(fmt), stock_data._ts_name_map())
    if df is None or df.empty:
        raise ZtError("Tushare 当日无数据")
    codes = df["ts_code"].astype(str)
    limit_p = (df["pre_close"] * (1 + codes.map(stock_data._limit_ratio))).round(2)
    zt = df[df["close"] >= limit_p]
    out = []
    for _, r in zt.iterrows():
        out.append({
            "code": str(r["ts_code"]).split(".")[0].zfill(6),
            "name": stock_data._ts_name_map().get(str(r["ts_code"])),
            "pct_change": round(float(r["pct_chg"]) / 100.0, 6),
            "price": _n(r.get("close")), "turnover": None, "lianban": None,
            "industry": None, "amount": None, "seal_amount": None,
            "first_seal": None, "last_seal": None, "zhaban_times": None,
            "cap_float": None, "cap_total": None, "raw_pool_source": "Tushare计算",
        })
    return out


def fetch_zt_full(date: str) -> dict:
    """全量涨停池：东财 → 同花顺 → Tushare 计算 → 数据缺失。"""
    errors: list[str] = []
    try:
        return {"date": date, "stocks": _em_zt_rows(date), "source": "东财",
                "degraded": False, "degraded_reason": [], "note": "东财涨停股池（全量）"}
    except Exception as exc:
        errors.append(f"东财: {exc}")
    try:
        rows = _ths_zt_rows(date)
        if rows:
            return {"date": date, "stocks": rows, "source": "同花顺",
                    "degraded": True, "degraded_reason": [f"东财失败：{errors[0]}"],
                    "note": "同花顺免费接口（连板数/封板时间/市值缺失，连板高度为近似）"}
    except Exception as exc:
        errors.append(f"同花顺: {exc}")
    try:
        rows = _ts_zt_rows(date)
        if rows:
            return {"date": date, "stocks": rows, "source": "Tushare计算",
                    "degraded": True, "degraded_reason": [f"东财失败：{errors[0]}"],
                    "note": "Tushare 精确涨停价计算（连板/封板/市值缺失）"}
    except Exception as exc:
        errors.append(f"Tushare: {exc}")
    return {"date": date, "stocks": [], "source": "数据缺失", "degraded": True,
            "degraded_reason": errors or ["各数据源均不可用"],
            "note": "涨停池数据缺失（数据源保留期外或不可用）"}


# ═══════════════════════════════════════════════════════════════
# A5 题材归属（主源=开盘啦 AppAPI；兜底=东财 f129；最后人工+行业）
# ═══════════════════════════════════════════════════════════════

def _em_secid(code: str) -> str:
    if code.startswith("6"):
        return "1." + code
    return "0." + code


def _em_f129_topics(codes: list[str]) -> dict[str, list[str]]:
    """东财单股概念列表（f129，逗号分隔；免登录）。逐只短超时，失败跳过。"""
    out: dict[str, list[str]] = {}
    for code in codes:
        if not code:
            continue
        try:
            data = stock_data._call_with_timeout(
                stock_data._http_get_json,
                "http://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": _em_secid(code), "fields": "f57,f58,f127,f129", "invt": "2", "fltt": "2"},
                headers={"User-Agent": stock_data._UA, "Referer": "https://quote.eastmoney.com/"},
                timeout=8)
            d = (data or {}).get("data") or {}
            f129 = d.get("f129")
            if f129:
                out[code] = [t.strip() for t in str(f129).split(",") if t.strip()]
        except Exception:
            continue
    return out


def fetch_topics(date: str, codes: list[str]) -> dict:
    """题材归属：开盘啦 AppAPI（主）→ 东财 f129（兜底）→ 其余交给人工标注。"""
    topics: dict[str, dict] = {}
    try:
        ti = kaipanla_api.fetch_zhangting_tianti(date)
        for s in ti.get("stocks", []):
            topics[s["code"]] = {
                "topic": s.get("topic"),
                "topics": [s["topic"]] if s.get("topic") else [],
                "seal_time_kpl": s.get("seal_time"),
                "source": "kaipanla",
            }
        try:
            pi = kaipanla_api.fetch_plate_info(date)
            for g in pi.get("groups", []):
                for s in g.get("stocks", []):
                    t = topics.setdefault(s["code"], {"topic": None, "topics": [], "source": None})
                    if g.get("topic") and g["topic"] not in t["topics"]:
                        t["topics"].append(g["topic"])
                    t["topic"] = t["topic"] or g.get("topic")
                    if s.get("reason"):
                        t["reason"] = s["reason"]
                    if s.get("reason_detail"):
                        t["reason_detail"] = s["reason_detail"]
                    if s.get("tags"):
                        t["tags"] = s["tags"]
                    t["source"] = "kaipanla"
        except Exception:
            pass
    except Exception:
        pass
    missing = [c for c in codes if c not in topics or not topics[c].get("topic")]
    em = _em_f129_topics(missing)
    for c, ts in em.items():
        topics[c] = {"topic": ts[0] if ts else None, "topics": ts, "source": "东财f129"}
    return topics


# ═══════════════════════════════════════════════════════════════
# A2 过滤（C7/C8：上市未满一年 + 退市双保险 + ST/北证）
# ═══════════════════════════════════════════════════════════════

_delisting_codes_cache: Optional[set[str]] = None
_recent_list_cache: Optional[set[str]] = None
_today_cache: Optional[str] = None


def _reset_caches() -> None:
    global _delisting_codes_cache, _recent_list_cache, _today_cache
    _delisting_codes_cache = None
    _recent_list_cache = None
    _today_cache = None


def _ts_basic_df():
    return stock_data._ts_stock_basic()


def delisting_codes() -> set[str]:
    """Tushare 退市名单（list_status=D/P 或 delist_date 已至）+ 名称缓存。"""
    global _delisting_codes_cache
    if _delisting_codes_cache is not None:
        return _delisting_codes_cache
    out: set[str] = set()
    try:
        df = _ts_basic_df()
        codes = df["ts_code"].astype(str)
        names = df["name"].astype(str)
        if "list_status" in df.columns:
            st = df["list_status"].astype(str)
            out |= set(codes[(st != "L") | (names.str.contains("退", na=False))])
        else:
            out |= set(codes[names.str.contains("退", na=False)])
        _delisting_codes_cache = {c.split(".")[0] for c in out}
    except Exception:
        _delisting_codes_cache = set()
    return _delisting_codes_cache


def recent_list_codes(asof: str) -> set[str]:
    """上市未满一年（清单日距今 <365 天）的代码集合。"""
    global _recent_list_cache, _today_cache
    if _recent_list_cache is not None and _today_cache == asof:
        return _recent_list_cache
    out: set[str] = set()
    try:
        df = _ts_basic_df()
        asof_dt = datetime.strptime(asof, "%Y-%m-%d").date()
        for _, r in df.iterrows():
            ld = str(r.get("list_date") or "")
            if len(ld) == 8:
                try:
                    list_dt = datetime.strptime(ld, "%Y%m%d").date()
                except ValueError:
                    continue
                if (asof_dt - list_dt).days < 365:
                    out.add(str(r["ts_code"]).split(".")[0])
        _recent_list_cache = out
        _today_cache = asof
    except Exception:
        _recent_list_cache = set()
        _today_cache = asof
    return _recent_list_cache


def filter_drop(stocks: list[dict], date: str, ts_available: bool = True) -> dict:
    """按口径过滤；返回保留列表 + 分桶剔除（dropped 永不静默）。"""
    kept: list[dict] = []
    dropped: dict[str, list[dict]] = {"st_bj": [], "delisting": [], "recent_list": []}
    delist = delisting_codes() if ts_available else set()
    recent = recent_list_codes(date) if ts_available else set()
    for s in stocks:
        code = str(s.get("code") or "")
        name = str(s.get("name") or "")
        if code.startswith(("8", "4", "92")) or "ST" in name.upper():
            dropped["st_bj"].append(s)
            continue
        if "退" in name or (ts_available and code in delist):
            dropped["delisting"].append(s)
            continue
        if ts_available and code in recent:
            dropped["recent_list"].append(s)
            continue
        kept.append(s)
    note = []
    if not ts_available:
        note.append("Tushare 不可用：退市/次新仅按名称规则过滤（退字/ST），次新可能漏网，界面需人工勾选")
    for k, v in dropped.items():
        if v:
            note.append(f"{k} 剔除 {len(v)} 只")
    return {"kept": kept, "dropped": dropped, "note": "；".join(note)}


# ═══════════════════════════════════════════════════════════════
# A3 标签化（连板/封板时间/一字T字/市值分档 + 龙虎榜 join）
# ═══════════════════════════════════════════════════════════════

def _tushare_daily_map(date: str) -> dict[str, dict]:
    """当日日线 map：ts_code(6位) → {open,high,low,close,pre_close}。"""
    fmt = stock_data._d(date)
    df = stock_data._ts_daily(fmt)
    if df is None or df.empty:
        return {}
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        code = str(r["ts_code"]).split(".")[0]
        out[code] = {
            "open": _n(r.get("open")), "high": _n(r.get("high")),
            "low": _n(r.get("low")), "close": _n(r.get("close")),
            "pre_close": _n(r.get("pre_close")),
        }
    return out


def _seal_type(row: dict, daily: Optional[dict]) -> tuple[Optional[str], str]:
    """一字/T字/普通 判定（C15：Tushare 精确优先，近似必须标注）。"""
    if daily:
        lp = None
        try:
            lp = round(daily["pre_close"] * (1 + stock_data._limit_ratio(row["code"])), 2)
        except Exception:
            lp = None
        if lp is not None and daily.get("open") is not None and daily.get("close") is not None:
            op, hi, lo, cl = daily["open"], daily["high"], daily["low"], daily["close"]
            if op >= lp and lo >= lp and (row.get("zhaban_times") in (None, 0)):
                return "一字板", "tushare"
            if op >= lp and cl >= lp:
                return "T字板", "tushare"
            if cl >= lp:
                return "普通涨停", "tushare"
            return None, "tushare"
    # 近似口径（东财池字段）
    fs = str(row.get("first_seal") or "").replace(":", "")
    zb = row.get("zhaban_times")
    if fs.startswith("0925") and zb in (None, 0):
        return "一字板", "em_approx"
    if zb not in (None, 0):
        return "T字板", "em_approx"
    return "普通涨停", "em_approx"


def _cap_class(cap_float: Optional[float]) -> Optional[str]:
    if cap_float is None:
        return None
    if cap_float < 20e8:
        return "微盘"
    if cap_float < 100e8:
        return "小盘"
    return "中军"


def _lhb_map(date: str) -> tuple[dict, Optional[str]]:
    """龙虎榜 join 数据（复用 stock_data.fetch_lhb 降级链）。"""
    try:
        blk = stock_data.fetch_lhb(date)
        m = {}
        for r in blk.get("stocks", []):
            m[str(r.get("code") or "")] = {
                "net": r.get("net_amount"), "buy": r.get("buy_amount"),
                "sell": r.get("sell_amount"), "reason": r.get("reason"),
            }
        return m, blk.get("source")
    except Exception as exc:
        return {}, f"龙虎榜失败：{exc}"


def tag_zt_stocks(full: dict, date: str) -> dict:
    """对全量涨停池打标签，输出 tagged + dropped + 口径标注。"""
    stocks = full.get("stocks", [])
    ts_ok = bool(config.TUSHARE_TOKEN)
    fd = filter_drop(stocks, date, ts_available=ts_ok)
    daily = _tushare_daily_map(date) if ts_ok else {}
    lhb_map, lhb_src = _lhb_map(date)
    seeded: dict[str, Any] = {}
    tagged = []
    for s in fd["kept"]:
        st, derived = _seal_type(s, daily.get(s["code"]))
        s2 = dict(s)
        s2["board_type"] = st
        s2["seal_derived"] = derived
        s2["seal_note"] = ("精确口径(Tushare日线)" if derived == "tushare"
                           else "近似口径(东财炸板/首封时间)，Tushare日线缺失")
        s2["cap_class"] = _cap_class(s.get("cap_float"))
        s2["limit_price"] = None
        d = daily.get(s["code"])
        if d and d.get("pre_close") is not None:
            s2["limit_price"] = round(d["pre_close"] * (1 + stock_data._limit_ratio(s["code"])), 2)
        s2["lhb"] = lhb_map.get(s["code"])
        s2["seats"] = seeded.get(s["code"], {"buy": [], "sell": [], "counts": {}})
        tagged.append(s2)
    return {
        "date": date, "stocks": tagged, "dropped": fd["dropped"],
        "filter_note": fd["note"], "daily_source": "Tushare" if daily else "无",
        "lhb_source": lhb_src, "seats": seeded,
        "units": {"pct_change": "小数(0.05=5%)", "turnover": "小数", "金额": "元"},
    }


# ═══════════════════════════════════════════════════════════════
# A4 龙虎榜席位（东财数据中心直连 + seat_labels 识别）
# ═══════════════════════════════════════════════════════════════

SEAT_TYPE_LABEL = {"youzi": "游资", "jigou": "机构", "lianghua": "量化", "waizi": "外资", "unknown": "未知"}


def _seat_pages(date: str, report: str, sort_col: str) -> list[dict]:
    """分页拉东财席位报表（已实证 2026-08-29）。"""
    out: list[dict] = []
    page = 1
    while page <= 5:
        params = {
            "reportName": report, "columns": "ALL", "pageNumber": str(page),
            "pageSize": "500", "source": "WEB", "client": "WEB",
            "sortTypes": "-1", "sortColumns": sort_col,
            "filter": f"(TRADE_DATE='{date}')",
        }
        data = stock_data._call_with_timeout(
            stock_data._http_get_json, _EM_DATACENTER, params=params,
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        rows = ((data or {}).get("result") or {}).get("data") or []
        if not rows:
            break
        out.extend(rows)
        if len(rows) < 500:
            break
        page += 1
    return out


def _buy_sell_map(date: str) -> dict[str, dict]:
    """当日全部上榜股的买卖前五席位。"""
    buy = _seat_pages(date, "RPT_BILLBOARD_DAILYDETAILSBUY", "BUY")
    sell = _seat_pages(date, "RPT_BILLBOARD_DAILYDETAILSSELL", "SELL")
    m: dict[str, dict] = {}

    def _feed(rows: list[dict], side: str) -> None:
        for r in rows:
            code = str(r.get("SECURITY_CODE") or "").zfill(6)
            name = r.get("OPERATEDEPT_NAME")
            amt = _n(r.get("NET")) or _n(r.get("BUY")) or _n(r.get("SELL"))
            if not code or not name:
                continue
            bucket = m.setdefault(code, {"buy": [], "sell": [], "counts": {}})
            bucket[side].append({"name": name, "amount": amt, "explanation": r.get("EXPLANATION")})

    _feed(buy, "buy")
    _feed(sell, "sell")
    for v in m.values():
        v["buy"] = sorted(v["buy"], key=lambda x: x["amount"] or 0, reverse=True)[:5]
        v["sell"] = sorted(v["sell"], key=lambda x: x["amount"] or 0, reverse=True)[:5]
    return m


def classify_seat(name: str, labels: dict) -> dict:
    """席位类型识别（C5/D4 + 2026-08-29 补充）。

    规则顺序：机构专用→机构；深股通/沪股通专用→外资；seat_labels 命中→对应类型；其余→未知。
    """
    n = str(name or "").strip()
    if n == "机构专用":
        return {"type": "jigou", "label": "机构", "source": "rule"}
    if "深股通" in n or "沪股通" in n:
        return {"type": "waizi", "label": "外资", "source": "rule"}
    hit = labels.get(n)
    if hit:
        t = hit.get("type")
        return {"type": t, "label": SEAT_TYPE_LABEL.get(t, "未知"), "source": "manual",
                "alias": hit.get("alias"), "note": hit.get("note")}
    return {"type": "unknown", "label": "未知", "source": "unlabeled"}


def fetch_lhb_seats(date: str) -> dict:
    """上榜席位 + 类型识别；失败降级（标注缺失，不阻塞）。"""
    labels = zt_store.load_seat_labels().get("labels", {})
    try:
        m = _buy_sell_map(date)
        for code, seats in m.items():
            counts = {"youzi": 0, "jigou": 0, "lianghua": 0, "waizi": 0, "unknown": 0}
            for side in ("buy", "sell"):
                for it in seats[side]:
                    cls = classify_seat(it["name"], labels)
                    it["type"] = cls["type"]
                    it["type_label"] = cls["label"]
                    if "source" in cls:
                        it["type_source"] = cls["source"]
                    counts[cls["type"]] = counts.get(cls["type"], 0) + 1
            seats["counts"] = counts
        return {"seats": m, "source": "东财数据中心", "degraded": False,
                "degraded_reason": [], "note": None}
    except Exception as exc:
        return {"seats": {}, "source": "数据缺失", "degraded": True,
                "degraded_reason": [f"席位获取失败：{exc}"],
                "note": "席位缺失，仅保留人工标记入口"}


# ═══════════════════════════════════════════════════════════════
# A6 板块聚类 + 梯队完整(C3) → 重点关注
# ═══════════════════════════════════════════════════════════════

def reapply_seat_labels(snapshot: Optional[dict]) -> Optional[dict]:
    """用最新 seat_labels 对快照席位实时重打标（标注后刷新即生效；不改落盘文件）。

    同时重算板块 seats_flags，保证「主线题材卡」的席位汇总跟随标注更新。
    """
    if not isinstance(snapshot, dict):
        return snapshot
    labels = zt_store.load_seat_labels().get("labels", {})
    stocks = snapshot.get("stocks") or []
    for s in stocks:
        seats = s.get("seats")
        if not isinstance(seats, dict):
            continue
        counts = {"youzi": 0, "jigou": 0, "lianghua": 0, "waizi": 0, "unknown": 0}
        for side in ("buy", "sell"):
            for it in seats.get(side, []) or []:
                cls = classify_seat(it.get("name"), labels)
                it["type"] = cls["type"]
                it["type_label"] = cls["label"]
                if "source" in cls:
                    it["type_source"] = cls["source"]
                counts[cls["type"]] = counts.get(cls["type"], 0) + 1
        seats["counts"] = counts
    boards = (snapshot.get("boards") or {}).get("boards", [])
    for b in boards:
        flags = []
        for code in b.get("stocks", []):
            s = next((x for x in stocks if x.get("code") == code), None)
            if not s:
                continue
            cnt = (s.get("seats") or {}).get("counts") or {}
            for t, c in cnt.items():
                if c:
                    flags.append({"code": code, "type": t, "count": c})
        b["seats_flags"] = flags
    return snapshot


def board_cluster(tagged: list[dict], manual_topics: Optional[dict] = None,
                  seats_map: Optional[dict] = None) -> dict:
    """按题材分组（开盘啦/人工/行业兜底），输出板块日榜。"""
    manual_topics = manual_topics or {}
    boards: dict[str, dict] = {}
    for s in tagged:
        code = str(s.get("code") or "")
        topic = s.get("topic") or manual_topics.get(code)
        board = topic or s.get("industry") or "未分类"
        topic_src = s.get("topic_source") or ("manual" if manual_topics.get(code)
                                               else ("industry" if s.get("industry") else "none"))
        b = boards.setdefault(board, {
            "board": board, "stocks": [], "zt_count": 0, "max_ladder": 0,
            "ladder_dist": {"首板": 0, "2板": 0, "3板+": 0},
            "zhongjun": [], "seats_flags": [], "earliest_seal": None,
            "topic_source": set(), "lianban_detail": [],
        })
        b["stocks"].append(code)
        b["zt_count"] += 1
        b["topic_source"].add(topic_src)
        lb = s.get("lianban")
        if lb is None:
            lb = 1
        b["max_ladder"] = max(b["max_ladder"], lb)
        if lb >= 3:
            b["ladder_dist"]["3板+"] += 1
        elif lb == 2:
            b["ladder_dist"]["2板"] += 1
        else:
            b["ladder_dist"]["首板"] += 1
        b["lianban_detail"].append({"code": code, "name": s.get("name"), "lianban": lb})
        if (s.get("cap_float") or 0) >= 100e8:
            b["zhongjun"].append({"code": code, "name": s.get("name"),
                                  "cap_float": s.get("cap_float")})
        seats = (seats_map or {}).get(code)
        if seats:
            for t, c in (seats.get("counts") or {}).items():
                if c:
                    b["seats_flags"].append({"code": code, "type": t, "count": c})
        fs = s.get("first_seal")
        if fs:
            fs_n = str(fs).replace(":", "")
            if b["earliest_seal"] is None or fs_n < b["earliest_seal"]:
                b["earliest_seal"] = fs_n
    out = []
    for b in boards.values():
        b["ladder_complete"] = (b["ladder_dist"]["首板"] > 0
                                and (b["ladder_dist"]["2板"] > 0 or b["ladder_dist"]["3板+"] > 0))
        b["focus"] = b["ladder_complete"]
        b["topic_source"] = sorted(b["topic_source"])
        b["top_stocks"] = sorted(b["lianban_detail"], key=lambda x: (-x["lianban"],
                                                                     str(x["code"])))[:8]
        out.append(b)
    out.sort(key=lambda b: (-(b["focus"]), -b["zt_count"], -b["max_ladder"]))
    return {"boards": out,
            "focused": [b["board"] for b in out if b["focus"]]}


# ═══════════════════════════════════════════════════════════════
# 单日流水线（手动触发入口）
# ═══════════════════════════════════════════════════════════════

def run_tracking_day(date: str) -> dict:
    """fetch → filter → tag → seats → cluster → 落盘；返回落盘快照。"""
    full = fetch_zt_full(date)
    tagged_blk = tag_zt_stocks(full, date)
    seats_blk = fetch_lhb_seats(date)
    for s in tagged_blk["stocks"]:
        seats = (seats_blk.get("seats") or {}).get(s["code"])
        if seats:
            s["seats"] = seats
    manual = zt_store.load_manual_tags().get("topics", {})
    topics = fetch_topics(date, [s.get("code") for s in tagged_blk["stocks"]])
    for s in tagged_blk["stocks"]:
        t = topics.get(str(s.get("code") or ""), {})
        s["topic"] = t.get("topic") or s.get("industry")
        s["topics"] = t.get("topics") or []
        s["topic_source"] = t.get("source") or ("industry" if s.get("industry") else "none")
        if t.get("reason"):
            s["reason"] = t["reason"]
        if t.get("reason_detail"):
            s["reason_detail"] = t["reason_detail"]
    boards = board_cluster(tagged_blk["stocks"], manual_topics=manual,
                           seats_map=seats_blk.get("seats") or {})
    snapshot = {
        "date": date,
        "pool": {"source": full.get("source"), "degraded": full.get("degraded"),
                 "degraded_reason": full.get("degraded_reason"), "note": full.get("note")},
        "topic_source": "kaipanla/f129/manual/industry",
        "stocks": tagged_blk["stocks"],
        "dropped": tagged_blk["dropped"],
        "filter_note": tagged_blk["filter_note"],
        "daily_source": tagged_blk["daily_source"],
        "lhb_source": tagged_blk["lhb_source"],
        "seats": seats_blk,
        "boards": boards,
    }
    zt_store.save_daily(date, snapshot)
    return snapshot


# ═══════════════════════════════════════════════════════════════
# 供分析层（zt_analysis）复用的公共接口
# ═══════════════════════════════════════════════════════════════

def fetch_daily_map(date: str) -> dict[str, dict]:
    """当日日线 map（ts_code 6位 → open/high/low/close/pre_close）；Tushare 缺失返回空。"""
    return _tushare_daily_map(date)


def all_stocks_of(date: str) -> list[dict]:
    """已落盘快照的涨停股列表，每只注入 board（题材/行业兜底）归属。"""
    snap = zt_store.load_daily(date)
    if not snap:
        return []
    board_of: dict[str, str] = {}
    for b in (snap.get("boards") or {}).get("boards", []):
        for c in b.get("stocks", []):
            board_of[c] = b["board"]
    out = []
    for s in snap.get("stocks", []):
        s2 = dict(s)
        s2["board"] = board_of.get(str(s.get("code") or ""))
        out.append(s2)
    return out
