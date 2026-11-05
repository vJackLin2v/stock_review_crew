# -*- coding: utf-8 -*-
"""主线题材个股打标签 —— 语料1-6 方法论引擎。

规则来源与映射表见 docs/短线方法论-语料提炼.md：
- role（身份）：核心·空间龙/连板龙 / 核心·辨识度(主营直接受益/逻辑稀缺, 待人工) / 中军·容量核心 /
  杂毛·尾盘跟风 / 杂毛·反复开板 / 杂毛·无量跟风 / 跟风
- action（操作）：轮动低吸 / 追涨(弱转强确认后) / 回踩低吸 / 观望(不追加速) / 回避
- next_day（次日操作指引，仅核心个股）：按高标加速/分歧转一致/板块龙头/中军 分支 + 通用验证清单

所有输出仅为复盘辅助；免责声明由展示层强制。
"""

from __future__ import annotations

from typing import Any, Optional

import time as _time_mod

from . import zt_analysis as zt_ana
from .storage import zt_tracking as zt_store
from .tools import market_extra
from .tools import kaipanla_api

# 语料4：辨识度候选关键词（用于「主营直接受益/逻辑稀缺」候选标注，需人工确认）
SHIBIE_KEYWORDS = ("主营", "受益", "概念", "龙头", "唯一", "稀缺", "第一", "核心", "龙头")


def _fmt_time(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    return str(ts).replace(":", "").zfill(6)


def _hms_to_min(hms: str) -> int:
    try:
        return int(hms[:2]) * 60 + int(hms[2:4])
    except Exception:
        return 15 * 60 + 30  # 解析失败按 15:30


def _board_ctx(bs: list[dict]) -> dict:
    mx = max([s.get("lianban") or 1 for s in bs] or [1])
    earliest = None
    for s in bs:
        fs = _fmt_time(s.get("first_seal"))
        if fs and (earliest is None or fs < earliest):
            earliest = fs
    return {"max_ladder": mx, "zt_count": len(bs),
            "earliest_seal": earliest}


def _role(s: dict, ctx: dict, has_seats: bool) -> tuple[str, list[str]]:
    """身份标签（语料2/3/4）。"""
    lb = s.get("lianban") or 1
    mx = ctx["max_ladder"]
    cap = s.get("cap_float") or 0
    seats = (s.get("seats") or {}).get("counts") or {}
    reason: list[str] = []

    # 中军（语料3：机构容量、机游联动）
    if cap >= 100e8 and (seats.get("youzi") or 0) + (seats.get("jigou") or 0) > 0:
        r = ["流通市值≥100亿且游资/机构席位参与"]
        if seats.get("youzi") and seats.get("jigou"):
            r.append("机游联动（语料3）")
        if lb == mx and mx >= 2:
            r.append("同时为板块最高板")
            return "核心·空间龙/连板龙(兼中军)", r
        return "中军·容量核心", r
    # 核心·空间龙/连板龙（语料2/4）
    if lb == mx and mx >= 2:
        r = ["板块内连板数最高（" + str(mx) + "板），率先打出高度"]
        if s.get("board_type") == "一字板":
            r.append("今日一字加速")
        return "核心·空间龙/连板龙", r
    # 核心·辨识度候选（语料4，需人工确认）
    tags = "".join(s.get("topics") or []) + str(s.get("reason") or "") + str(s.get("tags") or "")
    if lb >= 2 and tags and any(k in tags for k in SHIBIE_KEYWORDS):
        r = ["涨停原因/题材标签含辨识度特征词（主营/稀缺/龙头/核心…），需人工确认"]
        return "辨识度核心(待人工确认)", r
    # 杂毛判定（语料2：抓弱弃强；尾盘跟风/反复开板）
    fs = _fmt_time(s.get("first_seal"))
    zhaban = s.get("zhaban_times") or 0
    if lb == 1 and fs and _hms_to_min(fs) >= 14 * 60:
        r = ["首板尾盘封板（≥14:00），偷袭一日游嫌疑"]
        return "杂毛·尾盘跟风", r
    if lb == 1 and zhaban >= 2:
        r = ["首板反复开板（炸板次数≥2），承接弱"]
        return "杂毛·反复开板", r
    if lb == 1 and not has_seats and fs and _hms_to_min(fs) >= 13 * 60:
        r = ["首板午后封板且无席位，板块内地位低"]
        return "杂毛·无量跟风", r
    if lb >= 2 and lb >= mx - 1:
        return "核心·梯队接力(次高位)", ["板块次高位连板（" + str(lb) + "板，最高" + str(mx) + "板），人气接力（语料2 龙头不唯一）"]
    if lb >= 2:
        return "跟风·中位板", ["非最高板连板（板块" + str(mx) + "板，该股" + str(lb) + "板）"]
    return "跟风·首板", ["板块" + str(ctx["zt_count"]) + "家涨停中的普通首板，无明显辨识度"]


def _phase(sent: dict) -> tuple[str, float, Optional[float]]:
    score = sent.get("emotion_score")
    brk = sent.get("break_rate")
    if score is None:
        return "数据不足", 0.0, None
    if score >= 80 or (brk is not None and brk > 0.35):
        return "退潮/高潮风险", score, brk
    if score >= 60:
        return "发酵", score, brk
    if score >= 45:
        return "分歧", score, brk
    return "冰点", score, brk


def _action(s: dict, role: str, phase: str, ctx: dict) -> tuple[str, list[str]]:
    """操作标签（语料4/5：分歧冰点低吸核心，高潮不追加速；杂毛回避）。"""
    lb = s.get("lianban") or 1
    fs = _fmt_time(s.get("first_seal"))
    early_seal = bool(fs and _hms_to_min(fs) < 10 * 60)
    zhaban = s.get("zhaban_times") or 0
    board_type = s.get("board_type") or ""
    bs_batch = (ctx.get("zt_count") or 0) >= 8
    reasons: list[str] = []
    if role.startswith("杂毛") or role.startswith("跟风"):
        return "回避", ["杂毛/跟风股，抓弱弃强（语料2）：不因板块强而做弱势跟风"]
    if "断板走趋势" in role or "人气" in role:
        if phase in ("退潮/高潮风险", "冰点"):
            return "观望(等回踩)", ["趋势核心：退潮/冰点不接，等回踩 5/10 日线企稳（语料3）"]
        return "回踩低吸", ["走势/人气核心（非涨停亦核心）：回踩不破关键均线分批低吸（语料3/5）"]
    if phase in ("退潮/高潮风险", "冰点") :
        if role.startswith("核心"):
            if phase == "退潮/高潮风险":
                return "观望(不追加速)", ["情绪退潮或高潮加速段（语料4）：核心也不追高，等分歧/冰点低吸"]
            if board_type == "T字板" or zhaban > 0 or (fs and _hms_to_min(fs) >= 10 * 60):
                return "轮动低吸", ["冰点/大分歧日，核心辨识度出现分歧（T字/开板/午后封）→ 低吸窗口（语料4）"]
            return "观察(等分歧)", ["情绪冰点但该股封板仍稳，等分歧再低吸，不追高（语料4）"]
        return "观望", ["非核心，情绪弱势期不动"]
    if phase == "分歧":
        if role.startswith("核心"):
            if board_type == "T字板" or zhaban > 0:
                return "轮动低吸", ["板块分歧 + 核心个股开板回封，分歧低吸（语料2/4）"]
            return "追涨(弱转强确认后)", ["板块分歧但该股封板坚决，次日竞价转强确认后追涨（语料5）"]
        return "观察", ["分歧期只做核心（语料2），跟风让路"]
    # 发酵
    if role.startswith("核心"):
        if board_type == "一字板" or (early_seal and zhaban == 0):
            return "追涨(弱转强确认后)", ["情绪发酵 + 强封（一字/早封/零炸板），弱转强确认后追涨（语料2/5）"]
        return "轮动低吸", ["情绪发酵 + 板块批量" + (str(ctx.get("zt_count"))) + "家，核心分歧即可低吸"]
    if role.startswith("中军"):
        if bs_batch:
            return "回踩低吸", ["中军筑牢板块底线（语料3），板块批量涨停时回踩低吸"]
        return "观望", ["中军但板块强度一般，等回踩/放量"]
    return "观望", ["情绪发酵但个股地位一般，先观察"]


def _board_leaders(date: str, main_boards: list[str], pools: dict) -> dict[str, str]:
    """开盘啦 GetFeaturedSection：板块 → 龙头股（人气共识）。

    对每个主线板块取一只成分股（池内优先）查询，解析 info 行
    [板块代码, 板块名, 涨幅, 龙头代码, 龙头名称, 龙头涨幅, flag]。
    并行探测（≤4 线程、单请求短超时），失败静默降级（不影响规则核心）。
    """
    from concurrent.futures import ThreadPoolExecutor

    def _probe(board: str, code: str):
        try:
            j = kaipanla_api._call(kaipanla_api._common() | {"a": "GetFeaturedSection", "c": "StockL2Data",
                                       "StockID": str(code)}, method="GET", timeout=2)
            for row in j.get("info") or []:
                if isinstance(row, (list, tuple)) and len(row) >= 6 and row[1] == board and row[3]:
                    return board, str(row[3]).zfill(6)
        except Exception:
            pass
        return None

    jobs = []
    for board in main_boards:
        rec = pools.get(board) or {}
        codes = rec.get("stocks") or []
        if codes:
            jobs.append((board, str(codes[0])))
    out: dict[str, str] = {}
    if not jobs:
        return out
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as ex:
        for r in ex.map(lambda p: _probe(p[0], p[1]), jobs):
            if r is not None:
                out[r[0]] = r[1]
    return out


def _heat_score(role: str, cum: Optional[float], lianban: int, has_seats: bool, is_leader: bool) -> tuple[int, str]:
    """热度估算（开盘啦暂无公开人气字段，用代理指标；高/中/低）。"""
    sc = 0
    if is_leader:
        sc += 2
    elif role.startswith("核心") or role.startswith("中军"):
        sc += 1
    if cum is not None:
        sc += 2 if cum >= 0.5 else (1 if cum >= 0.2 else 0)
    if lianban >= 2:
        sc += 1
    if has_seats:
        sc += 1
    level = "高" if sc >= 4 else ("中" if sc >= 2 else "低")
    return sc, level


def _next_day(s: dict, role: str, ctx: dict) -> dict:
    """次日操作指引（语料2/5，仅核心个股）；含通用验证清单。"""
    lb = s.get("lianban") or 1
    mx = ctx.get("max_ladder") or 1
    board_type = s.get("board_type") or ""
    zhaban = s.get("zhaban_times") or 0
    fs = _fmt_time(s.get("first_seal"))
    early = bool(fs and _hms_to_min(fs) < 10 * 60)
    common = ["次日盘中验证（严禁隔夜无脑预判，语料2）",
              "板块联动是否延续（跟风股活跃度）",
              "龙头竞价态度不弱（竞价走强=表现态度，语料5）",
              "盘口承接：回踩不破关键位 / 主动买盘 / 封板坚决"]
    exits = ["放量不板反跌 → 离场一部分（语料5）",
             "低开破位无承接 → 止损",
             "加速缩量冲高不板 → 分批兑现（语料5）"]
    if role.startswith("中军"):
        plan = "回踩 5/10 日线分批低吸，趋势不破则持有（语料3 中军机游联动）"
        return {"plan": plan, "checklist": common, "exits": exits}
    if "断板走趋势" in role or "人气" in role:
        plan = "断板/人气趋势核心：板块未退潮时回踩 5/10 日线低吸或趋势持有；放量滞涨/失速则减（语料3/5）"
        return {"plan": plan, "checklist": common, "exits": exits}
    if lb >= 4 and (board_type == "一字板" or (early and zhaban == 0)):
        plan = "高标缩量加速：次日竞价不强即分批兑现；只做弱转强回封，不追高开加速（语料4/5）"
    elif board_type == "T字板" or zhaban > 0:
        plan = "分歧转一致：次日竞价不弱且板块未退潮 → 分歧低吸；竞价弱（低开破位/核按钮）→ 放弃（语料5）"
    elif lb == mx and mx >= 2:
        plan = "板块高度龙头：验证板块联动与竞价态度；弱转强确认可低吸，承接弱则兑现（语料2/5）"
    else:
        plan = "观察验证：板块续强且竞价承接好再介入（语料2）"
    return {"plan": plan, "checklist": common, "exits": exits}


# 标签计算缓存（60s，避免同日期重复查看慢算；历史日期冷启动仍可能 ~20s）
_label_cache: dict[str, tuple[float, dict]] = {}
_LABEL_CACHE_TTL = 60.0


def label_mainline_stocks(date: str) -> dict:
    now_t = _time_mod.time()
    hit = _label_cache.get(date)
    if hit and now_t - hit[0] < _LABEL_CACHE_TTL:
        return hit[1]
    """对主线题材（已过准入的入池板块）标注：当日涨停股 + 池内走势核心（非涨停也是核心）。

    - limit_up=True：当日涨停股（身份/操作/次日）；
    - limit_up=False：池内跟踪股中今日未涨停但具走势/人气核心地位
      （开盘啦板块龙头命中 或 累计涨幅领跑），热度按代理指标估算（高/中/低）。
    """
    snap = zt_store.load_daily(date) or {}
    stocks = zt_ana.zt_tools.all_stocks_of(date)
    rot = zt_ana.rotation_status(zt_store.list_daily_dates())
    main = {r["board"] for r in rot.get("boards", []) if r.get("in_pool")}
    pools = zt_store.load_pools().get("pools", {})
    try:
        sent = market_extra.fetch_market_sentiment(date)
    except Exception as exc:
        sent = {"emotion_score": None, "break_rate": None, "degraded_reason": [str(exc)[:100]]}
    phase, score, brk = _phase(sent)
    leaders = _board_leaders(date, sorted(main), pools)
    daily_map = zt_ana.zt_tools.fetch_daily_map(date)

    def _amp(code: str) -> Optional[float]:
        """当日振幅 (high-low)/pre_close；日线缺失返回 None。"""
        d = daily_map.get(code) or {}
        try:
            if d.get("high") is not None and d.get("low") is not None and d.get("pre_close"):
                return round((d["high"] - d["low"]) / d["pre_close"], 4)
        except Exception:
            pass
        return None
    per_board: dict[str, list[dict]] = {}
    for s in stocks:
        b = s.get("board") or ""
        if b in main:
            per_board.setdefault(b, []).append(s)
    labels: list[dict] = []

    def _role_rank(role: str) -> int:
        """身份层级（2026-08-29 十一轮）：核心(含中军/人气) 0 → 跟风 1 → 杂毛 2。"""
        if role.startswith(("核心", "中军", "辨识度", "人气")):
            return 0
        if role.startswith("杂毛"):
            return 2
        return 1

    # 1) 当日涨停股（累计涨幅同样用池内统一口径，含今日）
    for board, bs in per_board.items():
        ctx = _board_ctx(bs)
        board_cum = (pools.get(board) or {}).get("cumulative", {}) or {}
        for s in bs:
            seats = (s.get("seats") or {}).get("counts") or {}
            has_seats = (seats.get("youzi") or 0) + (seats.get("jigou") or 0) + (seats.get("waizi") or 0) > 0
            is_leader = leaders.get(board) == str(s.get("code") or "")
            role, role_reason = _role(s, ctx, has_seats)
            if is_leader:
                role = "核心·板块龙头（人气共识）"
                role_reason = role_reason + ["开盘啦板块龙头命中（市场人气共识）"]
            action, action_reason = _action(s, role, phase, ctx)
            nd = _next_day(s, role, ctx) if role.startswith(("核心", "中军", "辨识度")) else None
            heat, heat_level = _heat_score(role, None, s.get("lianban") or 1, has_seats, is_leader)
            amplitude = _amp(str(s.get("code") or "")) if role.startswith(("核心", "中军")) or (s.get("lianban") or 1) >= max(2, ctx["max_ladder"] - 1) else None
            day_cum = (board_cum.get(str(s.get("code") or "")) or {}).get("cum_pct")
            labels.append({
                "code": str(s.get("code") or ""), "name": s.get("name"),
                "board": board, "lianban": s.get("lianban") or 1,
                "board_type": s.get("board_type"), "first_seal": s.get("first_seal"),
                "cap_float": s.get("cap_float"), "cap_class": s.get("cap_class"),
                "limit_up": True, "cum_pct": day_cum, "amplitude": amplitude,
                "leader": is_leader, "heat": heat, "heat_level": heat_level,
                "role": role, "role_reason": role_reason,
                "action": action, "action_reason": action_reason,
                "next_day": nd,
            })
    # 2) 池内走势核心（今日未涨停，累计涨幅领跑 / 龙头命中）
    today_codes = {s.get("code") for s in stocks}
    for board in sorted(main):
        rec = pools.get(board) or {}
        cum = rec.get("cumulative", {}) or {}
        tracked = rec.get("tracked", []) or []
        if not tracked:
            continue
        ranked = sorted([t for t in tracked if t.get("code") not in today_codes
                         and (cum.get(t.get("code")) or {}).get("cum_pct") is not None],
                        key=lambda t: -(cum[t["code"]]["cum_pct"]))
        # 2026-08-29 十轮：持续跟踪（含断板）全部展示，不再只取前5
        for t in ranked:
            code = t.get("code")
            cp = cum[code]["cum_pct"]
            is_leader = leaders.get(board) == code
            has_seats = False
            if is_leader and cp >= 0.10:
                role, rreason = "核心·板块龙头（人气共识，断板）", ["开盘啦板块龙头命中 + 累计涨幅 " + str(round(cp * 100, 1)) + "%"]
            elif cp >= 0.30:
                role, rreason = "核心·断板走趋势", ["累计涨幅 " + str(round(cp * 100, 1)) + "%（统计日起），断板后趋势领跑"]
            elif cp >= 0.15 and (t.get("lianban") or 1) >= 2:
                role, rreason = "人气·次核", ["累计涨幅 " + str(round(cp * 100, 1)) + "%，历史连板人气"]
            elif cp >= 0.15:
                role, rreason = "跟风·断板跟踪", ["累计涨幅 " + str(round(cp * 100, 1)) + "%，持续性一般，跟随观察"]
            else:
                role, rreason = "跟风·池内跟踪", ["累计涨幅 " + str(round(cp * 100, 1)) + "%，低位补涨/冷门，谨防一日游"]
            if role.startswith(("核心", "人气")):
                action, action_reason = _action({"board_type": "普通涨停", "zhaban_times": 0, "first_seal": None,
                                                 "cap_float": None}, role, phase, {"zt_count": len(per_board.get(board, []))})
                nd = _next_day({**t, "board_type": "断板"}, role, {"max_ladder": 1})
            else:
                action, action_reason = "观察", ["池内持续跟踪，收敛后只看核心（铁律7：板块强则个股强）"]
                nd = None
            heat, heat_level = _heat_score(role, cp, t.get("lianban") or 1, has_seats, is_leader)
            amplitude = _amp(code)
            labels.append({
                "code": code, "name": t.get("name"), "board": board,
                "lianban": t.get("lianban") or 1, "board_type": "断板(未涨停)",
                "first_seal": None, "cap_float": None, "cap_class": None,
                "limit_up": False, "cum_pct": cp, "amplitude": amplitude,
                "leader": is_leader, "heat": heat, "heat_level": heat_level,
                "role": role, "role_reason": rreason,
                "action": action, "action_reason": action_reason,
                "next_day": nd,
            })
    # 排序：身份层级 核心→跟风→杂毛；层内 当日涨停在前，再按连板/累计涨幅
    labels.sort(key=lambda x: (x["board"], _role_rank(x.get("role", "")),
                               -int(x.get("limit_up") or 0), -(x.get("lianban") or 1),
                               -(x.get("cum_pct") if x.get("cum_pct") is not None else -1)))
    # 板块赚钱效应（平均/中位数累计涨幅） + 炒作末期警告（核心/前排同时大振幅）
    import statistics as _st
    by_board: dict[str, list[dict]] = {}
    for l in labels:
        by_board.setdefault(l["board"], []).append(l)
    board_stats: list[dict] = []
    for board in sorted(main):
        rec = pools.get(board) or {}
        cum = rec.get("cumulative", {}) or {}
        vals = [v.get("cum_pct") for v in cum.values() if v.get("cum_pct") is not None]
        earn_mean = round(sum(vals) / len(vals), 6) if vals else None
        earn_median = round(_st.median(vals), 6) if vals else None
        earn_score = round((earn_mean + earn_median) / 2 * 100) if earn_mean is not None and earn_median is not None else None
        big_amp: list[str] = []
        for l in by_board.get(board, []):
            amp = l.get("amplitude")
            coreish = l.get("role", "").startswith(("核心", "中军")) or (l.get("lianban") or 1) >= 2
            if coreish and amp is not None and amp >= 0.12:
                big_amp.append(l.get("name") or "")
        warn = len(big_amp) >= 2
        board_stats.append({
            "board": board,
            "earn_mean": earn_mean, "earn_median": earn_median, "earn_score": earn_score,
            "zt_count_today": len([l for l in by_board.get(board, []) if l.get("limit_up")]),
            "trend_count": len([l for l in by_board.get(board, []) if not l.get("limit_up")]),
            "warn": warn,
            "warn_reason": ("核心/前排同时大振幅（" + "、".join(big_amp[:3]) + "），炒作末期风险" if warn else None),
        })
    board_stats.sort(key=lambda x: -(x["earn_score"] if x["earn_score"] is not None else -1))
    _cache_out = {"date": date, "phase": phase, "emotion_score": score, "break_rate": brk,
                 "leaders": leaders, "labels": labels, "board_stats": board_stats,
                 "note": "标签规则来源：docs/短线方法论-语料提炼.md（语料1-6）；热度为代理指标估算（开盘啦暂无公开人气字段）"}
    _label_cache[date] = (_time_mod.time(), _cache_out)
    return {"date": date, "phase": phase, "emotion_score": score, "break_rate": brk,
            "leaders": leaders, "labels": labels, "board_stats": board_stats,
            "note": "标签规则来源：docs/短线方法论-语料提炼.md（语料1-6）；热度为代理指标估算（开盘啦暂无公开人气字段）"}
