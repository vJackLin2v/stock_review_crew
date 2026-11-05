# -*- coding: utf-8 -*-
"""涨停复盘分析（阶段 B）：轮动板块池、池生命周期跟踪(D6/D11)、核心股池(C11)、
收盘介入方向提示(C6 收盘版)、待人工补充 CSV(C18/D13)。

数据来源：data/zt_tracking 已落盘快照（storage.zt_tracking）+ Tushare 日线 + 情绪指标。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .tools import stock_data
from .tools import market_extra
from .tools import zt_tracking as zt_tools
from .storage import zt_tracking as zt_store

# 板块标签口径（2026-08-29 四轮拍板）：
# 重点关注 = 连续 3 天有涨停 或 10 日内曾连续 5 天有涨停（排序在最前）
# 轮动 = 5 日 ≥3 天 或 10 日 ≥5 天（未达重点）
ROTATION_DAYS5 = 3
ROTATION_DAYS10 = 5
FOCUS_CONT_DAYS = 3
FOCUS_CONT10_DAYS = 5
# 准入（2026-08-29 五轮）：最近 10 个交易日内存在单日板块涨停家数 ≥10
QUALIFIED_ZT_COUNT = 10


# ═══════════════════════════════════════════════════════════════
# 轮动板块池（C14/D5：5天≥3 或 10天≥5）
# ═══════════════════════════════════════════════════════════════

def _boards_of_date(date: str) -> dict[str, dict]:
    """某日板块名 → 当日板块摘要。"""
    snap = zt_store.load_daily(date)
    out: dict[str, dict] = {}
    if not snap:
        return out
    for b in (snap.get("boards") or {}).get("boards", []):
        out[b["board"]] = b
    return out


def rotation_status(dates: Optional[list[str]] = None) -> dict:
    """按已落盘历史计算关注/轮动板块池（今日窗口）。"""
    dates = dates or zt_store.list_daily_dates()
    if not dates:
        return {"date": None, "boards": [], "note": "无历史数据（请先手动采集）"}
    last = dates[-1]
    today = _boards_of_date(last)
    names: set[str] = set()
    by_date: dict[str, list[str]] = {}
    for d in dates:
        bs = _boards_of_date(d)
        by_date[d] = list(bs.keys())
        names |= set(bs.keys())
    rows = []
    for name in sorted(names):
        seq = [name in by_date[d] for d in dates]
        cont_now = 0
        for v in reversed(seq):
            if v:
                cont_now += 1
            else:
                break
        win = seq[-10:]
        max_cont = 0
        cur = 0
        for v in win:
            cur = cur + 1 if v else 0
            max_cont = max(max_cont, cur)
        days5 = sum(1 for d in dates[-5:] if name in by_date[d])
        days10 = sum(1 for d in dates[-10:] if name in by_date[d])
        b = today.get(name, {})
        max_ladder = max([_boards_of_date(d).get(name, {}).get("max_ladder", 0) for d in dates[-10:]] or [0])
        zts10 = [_boards_of_date(d).get(name, {}).get("zt_count", 0) for d in dates[-10:]]
        max_zt10 = max(zts10) if zts10 else 0
        qualified = max_zt10 >= QUALIFIED_ZT_COUNT
        # 口径（五轮）：重点=连续3天 或 10日内连续5天；轮动=5日≥3 或 10日≥5；均需先过准入（10日内单日≥10家）
        focus = cont_now >= FOCUS_CONT_DAYS or max_cont >= FOCUS_CONT10_DAYS
        lun = days5 >= ROTATION_DAYS5 or days10 >= ROTATION_DAYS10
        in_pool = (focus or lun) and qualified
        tag = "重点关注" if (focus and qualified) else ("轮动" if in_pool else "观察")
        last_seen = next((d for d in reversed(dates) if name in by_date[d]), None)
        rows.append({
            "board": name, "days5": days5, "days10": days10,
            "cont_now": cont_now, "max_cont10": max_cont,
            "max_zt10": max_zt10, "qualified": qualified,
            "zt_count_today": b.get("zt_count", 0), "max_ladder": max_ladder,
            "last_seen": last_seen, "tag": tag, "in_pool": in_pool,
            "focus": focus, "focus_today": bool(b.get("focus")),
            "topic_source": b.get("topic_source", []),
        })
    # 排序：重点全部在最前；重点内部按连续天数（连续5>连续3）再按 10日/5日/今日涨停
    rows.sort(key=lambda r: (-(1 if r["focus"] else 0), -r["max_cont10"], -r["days10"],
                                -r["days5"], -r["zt_count_today"], r["board"]))
    return {"date": last, "boards": rows,
            "note": f"口径（五轮）：准入 = 最近10日内存在单日涨停≥{QUALIFIED_ZT_COUNT}家；重点关注 = 连续{FOCUS_CONT_DAYS}天 或 10日内连续{FOCUS_CONT10_DAYS}天；轮动 = 5天≥{ROTATION_DAYS5} 或 10天≥{ROTATION_DAYS10}"}


# ═══════════════════════════════════════════════════════════════
# 池生命周期（D6/D11）：重放历史 → 入池/出池/跟踪组/逐日走势
# ═══════════════════════════════════════════════════════════════

def _daily_map_cached(date: str, cache: dict) -> dict:
    if date not in cache:
        cache[date] = zt_tools.fetch_daily_map(date)
    return cache[date]


def update_pools(dates: Optional[list[str]] = None) -> dict:
    """幂等重放：维护 pools.json + follow 跟踪记录。

    - 准入（2026-08-29 五轮）：板块须在最近 10 个交易日内存在某一交易日涨停家数 ≥10；
    - 入池：满足准入 且（重点关注 或 轮动规则）→ active；
    - 跟踪起点 = 第一个达标交易日（该日单日涨停 ≥10），入池记录该日涨停个股并持续追加；
    - 累计涨跌幅 = 池内每只股票首次在板块涨停日的次日起，逐交易日涨跌幅复利累乘；
    - 出池：active 板块当日不再满足 → inactive（D11 滚动判定），停止新增跟踪，历史保留。
    """
    dates = dates or zt_store.list_daily_dates()
    import shutil as _shutil
    _fdir = zt_store.zt_root() / "follow"
    if _fdir.exists():
        for _f in _fdir.glob("*.json"):
            _f.unlink()
    pool_map: dict[str, dict] = {}
    daily_map_cache: dict[str, dict] = {}
    stats = {"dates": len(dates), "entered": 0, "exited": 0, "follow_entries": 0}
    for i, d in enumerate(dates, start=1):
        window = dates[max(0, i - 10):i]
        bs = _boards_of_date(d)
        if not bs:
            continue
        win_boards: dict[str, dict[str, dict]] = {}
        for wd in window:
            win_boards[wd] = _boards_of_date(wd)
        # 窗口内板块 → 当日涨停个股事件（达标日/跟踪起点/累计涨跌幅用）
        code_events: dict[str, dict[str, list[str]]] = {}
        for wd, wb in win_boards.items():
            for name2, b in wb.items():
                for c in b.get("stocks", []):
                    code_events.setdefault(name2, {}).setdefault(c, []).append(wd)
        names: set[str] = set()
        for wb in win_boards.values():
            names |= set(wb.keys())
        for name in list(names):
            seq = [name in win_boards[wd] for wd in window]
            days5 = sum(1 for wd in window[-5:] if name in win_boards[wd])
            days10 = sum(1 for wd in window if name in win_boards[wd])
            cont_now = 0
            for v in reversed(seq):
                if v:
                    cont_now += 1
                else:
                    break
            max_cont = 0
            cur = 0
            for v in seq:
                cur = cur + 1 if v else 0
                max_cont = max(max_cont, cur)
            zts = [win_boards[wd].get(name, {}).get("zt_count", 0) for wd in window]
            max_zt10 = max(zts) if zts else 0
            qualified = max_zt10 >= QUALIFIED_ZT_COUNT
            entry_day = next((wd for wd in window
                              if (win_boards[wd].get(name, {}) or {}).get("zt_count", 0) >= QUALIFIED_ZT_COUNT),
                             None)
            focus = cont_now >= FOCUS_CONT_DAYS or max_cont >= FOCUS_CONT10_DAYS
            lun = days5 >= ROTATION_DAYS5 or days10 >= ROTATION_DAYS10
            satisfy = (focus or lun) and qualified
            rec = pool_map.get(name)
            today_stocks = [s for s in zt_tools.all_stocks_of(d) if s.get("board") == name]
            if satisfy and rec is None:
                pool_map[name] = {
                    "board": name, "entry_date": entry_day or d, "last_active_date": d,
                    "focus": focus, "cont_now": cont_now, "max_cont10": max_cont,
                    "max_zt10": max_zt10, "qualified": qualified,
                    "exit_date": None, "status": "active",
                    "stocks": sorted({s["code"] for s in today_stocks}),
                    "entry_snapshot": [{"code": s["code"], "name": s["name"],
                                        "lianban": s.get("lianban"),
                                        "board_type": s.get("board_type")} for s in today_stocks],
                }
                stats["entered"] += 1
            elif satisfy and rec is not None:
                rec["status"] = "active"
                rec["focus"] = focus
                rec["cont_now"] = cont_now
                rec["max_cont10"] = max_cont
                rec["max_zt10"] = max_zt10
                rec["qualified"] = qualified
                rec["last_active_date"] = d
                rec["exit_date"] = None
                rec["stocks"] = sorted(set(rec.get("stocks", [])) | {s["code"] for s in today_stocks})
            elif not satisfy and rec is not None and rec.get("status") == "active":
                rec["status"] = "inactive"
                rec["exit_date"] = d
                stats["exited"] += 1
        # follow：active 板块当日记录池内个股走势（D6）
        dm = _daily_map_cached(d, daily_map_cache)
        for name, rec in pool_map.items():
            if rec.get("status") != "active":
                continue
            fol = zt_store.load_follow(name)
            fol.setdefault("entries", [])
            if any(e.get("date") == d for e in fol["entries"]):
                continue  # 幂等：该日已记录
            entry = {"date": d, "stocks": []}
            for code in rec.get("stocks", []):
                row = dm.get(code)
                entry["stocks"].append(_follow_row(code, d, row))
            fol["entries"].append(entry)
            zt_store.save_follow(name, fol)
            stats["follow_entries"] += 1
    # 累计统计（2026-08-29 七轮）：统计起点 = 板块第一次「单日涨停≥10」的交易日（entry_date，绝不重置）；
    # 记录统计日起到最新交易日所有涨停过的股票（tracked 全量）；每股累计涨跌幅统一从统计起点次日复利累乘。
    all_events: dict[str, dict[str, list[str]]] = {}
    for wd in dates:
        wb = _boards_of_date(wd)
        for name2, b in wb.items():
            for c in b.get("stocks", []):
                all_events.setdefault(name2, {}).setdefault(c, []).append(wd)
    for wd in dates:
        _daily_map_cached(wd, daily_map_cache)
    date_stocks: dict[str, dict[str, dict]] = {}
    for wd in dates:
        snap_d = zt_store.load_daily(wd) or {}
        m: dict[str, dict] = {}
        for s in snap_d.get("stocks", []):
            m[str(s.get("code") or "")] = {"name": s.get("name"),
                                            "lianban": s.get("lianban"),
                                            "board_type": s.get("board_type")}
        date_stocks[wd] = m
    for name, rec in pool_map.items():
        ev = all_events.get(name, {})
        base = rec.get("entry_date")  # 板块统计起点 = 第一次达标日，之后再次达标不重置
        rec["cumulative"] = {}
        tracked: list[dict] = []
        for code in rec.get("stocks", []):
            seen = ev.get(code) or []
            first = min(seen) if seen else base
            prod = 1.0
            counted = 0
            for wd2 in dates:
                if wd2 <= base:
                    continue
                row = daily_map_cache.get(wd2, {}).get(code)
                if row and row.get("close") is not None and row.get("pre_close"):
                    try:
                        p = (row["close"] - row["pre_close"]) / row["pre_close"]
                    except Exception:
                        p = 0.0
                    prod *= (1 + p)
                    counted += 1
            rec["cumulative"][code] = {"first_seen": first, "base": base,
                                       "cum_pct": round(prod - 1, 6) if counted else None}
            last_d = seen[-1] if seen else base
            info = date_stocks.get(str(last_d), {}).get(code) or date_stocks.get(str(base), {}).get(code) or {}
            tracked.append({"code": code, "name": info.get("name") or code, "first_seen": first,
                            "lianban": info.get("lianban"), "board_type": info.get("board_type")})
        rec["tracked"] = sorted(tracked, key=lambda x: -(x.get("lianban") or 1))
    zt_store.save_pools({"schema_version": zt_store.SCHEMA_VERSION, "pools": pool_map})
    return {"stats": stats, "pools": pool_map}



def _follow_row(code: str, date: str, row: Optional[dict]) -> dict:
    if not row or row.get("close") is None or row.get("pre_close") is None:
        return {"code": code, "date": date, "close": None, "pct_change": None,
                "sealed": None, "limit_down": None, "note": "日线缺失"}
    pc = None
    try:
        pc = round((row["close"] - row["pre_close"]) / row["pre_close"], 6)
    except Exception:
        pc = None
    limit_p = None
    try:
        limit_p = round(row["pre_close"] * (1 + stock_data._limit_ratio(code)), 2)
    except Exception:
        limit_p = None
    sealed = bool(limit_p and row["close"] >= limit_p)
    limit_down = bool(limit_p and row["close"] <= round(row["pre_close"] * (1 - stock_data._limit_ratio(code)), 2))
    return {"code": code, "date": date, "close": row.get("close"),
            "pct_change": pc, "sealed": sealed, "limit_down": limit_down}


# ═══════════════════════════════════════════════════════════════
# 核心股池（C11：辨识度核心 + 中军）
# ═══════════════════════════════════════════════════════════════

def core_candidates(date: str) -> dict:
    snap = zt_store.load_daily(date)
    stocks = zt_tools.all_stocks_of(date)  # 带 board 注入
    boards = ((snap.get("boards") or {}).get("boards", [])) if snap else []
    # 六轮（2026-08-29）：核心股只关注主线题材（已过准入 + 重点/轮动规则的入池板块）
    rot = rotation_status(zt_store.list_daily_dates())
    main_boards = {r["board"] for r in rot.get("boards", []) if r.get("in_pool")}
    focus_boards = [b for b in boards if b["board"] in main_boards]
    shibie: list[dict] = []
    zhongjun: list[dict] = []
    for b in focus_boards:
        bstocks = [s for s in stocks if s.get("board") == b["board"]]
        mx = max([s.get("lianban") or 1 for s in bstocks] or [1])
        for s in bstocks:
            code = s.get("code")
            seats = s.get("seats") or {}
            counts = seats.get("counts") or {}
            jy = s.get("cap_float") or 0
            base = {"code": code, "name": s.get("name"), "board": b["board"],
                    "lianban": s.get("lianban"), "board_type": s.get("board_type"),
                    "first_seal": s.get("first_seal"),
                    "cap_float": jy, "cap_class": s.get("cap_class"),
                    "seats": counts, "reason": []}
            if jy >= 100e8 and (counts.get("youzi") or 0) + (counts.get("jigou") or 0) > 0:
                base["type"] = "中军"
                base["jiyou_liandong"] = bool(counts.get("youzi") and counts.get("jigou"))
                base["reason"].append("机游联动" if base["jiyou_liandong"] else "大额资金")
                zhongjun.append(base)
            elif (s.get("lianban") or 1) >= max(2, mx - 1):
                base["type"] = "辨识度核心"
                base["subtype"] = "空间龙/连板龙"
                base["reason"].append("最高板梯度")
                shibie.append(base)
            elif b.get("zt_count", 0) >= 5 and (s.get("lianban") or 1) >= 2:
                base["type"] = "辨识度核心"
                base["subtype"] = "待人工确认（主营直接受益/逻辑稀缺需盘感）"
                base["reason"].append("板块批量涨停，需人工判辨识度")
                shibie.append(base)
    shibie.sort(key=lambda x: -(x.get("lianban") or 1))
    zhongjun.sort(key=lambda x: -(x.get("cap_float") or 0))
    return {"date": date, "shibie": shibie, "zhongjun": zhongjun}


# ═══════════════════════════════════════════════════════════════
# 收盘介入方向提示（C6 收盘版：低吸/追涨/观望）
# ═══════════════════════════════════════════════════════════════

def signal(date: str) -> dict:
    """情绪周期 + 板块位置 → 核心股方向提示（仅为收盘复盘辅助，不构成投资建议）。"""
    try:
        sent = market_extra.fetch_market_sentiment(date)
    except Exception as exc:
        sent = {"emotion_score": None, "break_rate": None, "red_rate": None,
                "degraded": True, "degraded_reason": [f"情绪数据失败：{exc}"]}
    score = sent.get("emotion_score")
    brk = sent.get("break_rate")
    red = sent.get("red_rate")
    warm = (score is not None and score >= 60) and (brk is None or brk < 0.30)
    risky = (score is not None and score < 45) or (brk is not None and brk > 0.35)
    # 持续至少 2 天的基本面（轮动池）
    rot = rotation_status()
    rot_by_board = {r["board"]: r for r in rot.get("boards", [])}
    cores = core_candidates(date)
    out = []
    for item in cores.get("shibie", []) + cores.get("zhongjun", []):
        b = rot_by_board.get(item["board"]) or {}
        cont = (b.get("days5") or 0) >= 2 or (b.get("days10") or 0) >= 3
        sig = "观察"
        note = []
        if risky:
            sig = "退潮观望"
            note.append("情绪降温/炸板率高，不追高")
        elif warm and cont and item.get("type") == "辨识度核心":
            if item.get("board_type") in ("T字板", None) or (b.get("zt_count_today") or 0) >= 5:
                sig = "分歧低吸"
                note.append("主线持续+情绪偏暖，分歧日低吸核心辨识度")
            else:
                sig = "追涨观察"
                note.append("板块强势，弱转强确认后再追")
        elif warm and cont and item.get("type") == "中军":
            sig = "回踩低吸"
            note.append("中军走强反哺题材（机游联动），回踩关键均线低吸")
        else:
            note.append("板块持续性或情绪不足，先观察")
        out.append({"code": item["code"], "name": item["name"], "type": item["type"],
                    "board": item["board"], "signal": sig, "note": "；".join(note),
                    "evidence": {"板块5日天数": b.get("days5"), "板块10日天数": b.get("days10"),
                                 "情绪温度": score}})
    return {"date": date, "items": out, "sentiment": sent,
            "disclaimer": "仅供参考，不构成投资建议"}


# ═══════════════════════════════════════════════════════════════
# 待人工补充 CSV（C18/D13：日期、代码、名称、题材标签、席位标注、备注）
# ═══════════════════════════════════════════════════════════════

def build_pending_csv(date: Optional[str] = None) -> dict:
    """导出待人工补充 CSV（C18/D13）：题材标签空缺 + 席位未知待确认。

    date=None 时汇总全部已落盘日期，按 code 去重（题材标签是股票级全局归属）。
    """
    dates = [date] if date else zt_store.list_daily_dates()
    manual_topics = zt_store.load_manual_tags().get("topics", {})
    rows: list[dict] = []
    seen_codes: set[str] = set()
    first_date: dict[str, str] = {}
    for d in dates:
        snap = zt_store.load_daily(d)
        if not snap:
            continue
        for s in snap.get("stocks", []):
            code = str(s.get("code") or "")
            if code in seen_codes:
                continue
            seen_codes.add(code)
            first_date[code] = d
    for d in dates:
        snap = zt_store.load_daily(d)
        if not snap:
            continue
        for s in snap.get("stocks", []):
            code = str(s.get("code") or "")
            if first_date.get(code) != d:
                continue  # 只输出首次出现日期
            need_topic = (s.get("board") is None or s.get("board") == "未分类"
                          or s.get("board") == s.get("industry"))
            seats = s.get("seats") or {}
            unknown_seats = []
            for side in ("buy", "sell"):
                for it in seats.get(side, []):
                    if it.get("type") == "unknown":
                        unknown_seats.append(f"{'买' if side == 'buy' else '卖'}:{it['name']}")
            if need_topic or unknown_seats:
                note_parts = []
                if need_topic and code not in manual_topics:
                    note_parts.append("待填题材标签")
                if unknown_seats:
                    note_parts.append("席位待确认类型")
                rows.append({
                    "date": d, "code": code, "name": s.get("name") or "",
                    "topic": manual_topics.get(code, ""),
                    "seat": "；".join(unknown_seats[:4]),
                    "note": "、".join(note_parts),
                })
    prefix = f"zt_pending_{dates[-1]}" if dates else "zt_pending"
    path = zt_store.write_pending_csv(rows, prefix=prefix)
    return {"path": str(path), "rows": len(rows), "dates": len(dates)}
