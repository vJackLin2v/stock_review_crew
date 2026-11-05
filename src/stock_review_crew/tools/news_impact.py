# -*- coding: utf-8 -*-
"""资讯影响分析（2026-08-29 用户需求）。

- 资讯源：新浪 7x24 快讯（realtime.fetch_news，A股白名单；开盘啦暂无公开资讯接口，已探测）；
- 网络搜索增强：对「命中主线题材板块」的资讯调用 search.search（东财公告+快讯过滤）补充证据，
  失败降级（≤15s/条，绝不阻塞）；
- 影响标注：板块词库 + 主线板块名/个股名匹配；利好/利空词典计分；
  命中主线题材的板块/个股打 mainline=True（前端高亮）；
- 输出：data/zt_tracking/news_{date}.json（手动采集时生成，可离线读取）。

免责声明：本模块输出仅为复盘辅助，不构成投资建议。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import realtime
from . import search as search_tools
from ..storage import zt_tracking as zt_store

# 广义板块/题材词库（命中即标注影响板块；与主线板块同名 → 主线高亮）
BOARD_KEYWORDS = [
    "AI", "人工智能", "算力", "芯片", "半导体", "光模块", "CPO", "存储", "数据中心",
    "医药", "创新药", "中药", "医疗器械", "农业", "种业", "粮食", "猪肉", "黄金", "白银",
    "机器人", "减速器", "军工", "低空经济", "商业航天", "大飞机", "新能源", "光伏", "锂电",
    "储能", "充电桩", "汽车", "智能驾驶", "华为", "消费电子", "面板", "PCB", "通信", "5G", "6G",
    "房地产", "基建", "金融", "券商", "银行", "保险", "白酒", "食品", "零售", "消费",
    "化工", "有色", "稀土", "煤炭", "电力", "石油", "中字头", "国企改革", "并购重组",
    "网络安全", "数据要素", "信创", "云计算", "游戏", "传媒", "教育", "旅游", "出海", "高股息",
]
# 利好词（计数）
GOOD_WORDS = ["中标", "获批", "批复", "核准", "增持", "回购", "预增", "增长", "涨价", "提价",
              "政策", "支持", "补贴", "签约", "突破", "扩产", "订单", "合作", "新高", "领跑",
              "利好", "降准", "降息", "放量", "提速", "上修", "扭亏", "倍增", "受益"]
# 利空词（计数）
BAD_WORDS = ["减持", "立案", "调查", "处罚", "退市", "下滑", "亏损", "预减", "风险", "暴跌",
             "跌停", "违约", "诉讼", "警示", "暂停", "扣非", "贬值", "裁员", "利空", "爆雷",
             "违规", "问询", "商誉减值", "计提"]


def _direction(text: str) -> tuple[str, str]:
    good = sum(1 for w in GOOD_WORDS if w in text)
    bad = sum(1 for w in BAD_WORDS if w in text)
    if good > bad:
        return "利好", "利好词命中（" + "、".join([w for w in GOOD_WORDS if w in text][:3]) + "）"
    if bad > good:
        return "利空", "利空词命中（" + "、".join([w for w in BAD_WORDS if w in text][:3]) + "）"
    return "中性", "未见明显利好/利空词"


def _match(text: str, main_boards: list[str], main_stocks: list[dict]) -> tuple[list[str], list[dict]]:
    """命中板块（含通用词库）与主线个股。返回 (板块名列表, 主线个股列表)。"""
    boards: list[str] = []
    for w in BOARD_KEYWORDS:
        if w in text and w not in boards:
            boards.append(w)
    for b in main_boards:
        if b in text and b not in boards:
            boards.append(b)
    stocks: list[dict] = []
    for s in main_stocks:
        name = str(s.get("name") or "")
        code = str(s.get("code") or "")
        if (name and name in text) or (code and code in text):
            stocks.append(s)
    return boards, stocks


def _search_evidence(text: str) -> dict:
    """网络搜索增强（对命中主线板块的资讯）；失败降级标注。"""
    try:
        r = search_tools.search(str(text)[:120])
        if r and r.get("ok"):
            return {"ok": True, "results": (r.get("results") or [])[:3],
                    "summary": r.get("summary")}
        return {"ok": False, "results": [], "summary": None,
                "note": "（未联网搜索，网络不可用或超时）"}
    except Exception as exc:
        return {"ok": False, "results": [], "summary": None,
                "note": "（未联网搜索：" + str(exc)[:60] + "）"}


def fetch_market_news(limit: int = 10) -> list[dict]:
    """资讯主源：新浪 7x24 快讯（A股白名单）。"""
    try:
        blk = realtime.fetch_news()
        items = blk.get("news") or []
        return items[:limit]
    except Exception:
        return []


def analyze_impact(news: list[dict], main_boards: list[str], main_stocks: list[dict]) -> list[dict]:
    """对每条资讯做影响分析（方向/板块/个股/搜索证据）。"""
    out: list[dict] = []
    for n in news:
        text = str(n.get("text") or n.get("title") or "").strip()
        if not text:
            continue
        direction, dr = _direction(text)
        boards, stocks = _match(text, main_boards, main_stocks)
        searched = False
        evidence: dict = {"ok": False, "results": [], "summary": None, "note": None}
        main_hit = any(b in main_boards for b in boards) or bool(stocks)
        if main_hit:
            evidence = _search_evidence(text)
            searched = True
        out.append({
            "time": n.get("time"),
            "text": text[:200],
            "source": n.get("source") or "新浪7x24",
            "direction": direction,
            "direction_reason": dr,
            "boards": boards,
            "stocks": [{"code": s.get("code"), "name": s.get("name"), "mainline": True} for s in stocks],
            "mainline_hit": main_hit,
            "searched": searched,
            "evidence": evidence,
        })
    return out


def run_news_impact(date: str) -> dict:
    """抓取最新资讯 → 影响分析 → 落盘 data/zt_tracking/news_{date}.json。"""
    from .. import zt_analysis as zt_ana

    rot = zt_ana.rotation_status(zt_store.list_daily_dates())
    main_boards = [r["board"] for r in rot.get("boards", []) if r.get("in_pool")]
    try:
        blades = zt_ana.label_mainline_stocks(date)
        main_stocks = [{"code": l.get("code"), "name": l.get("name")} for l in blades.get("labels", [])]
    except Exception:
        main_stocks = []
    news = fetch_market_news()
    items = analyze_impact(news, main_boards, main_stocks)
    blk = {
        "date": date, "created_at": datetime.now().isoformat(timespec="seconds"),
        "main_boards": main_boards,
        "count": len(items), "items": items,
        "source": "新浪7x24（开盘啦无公开资讯接口，已探测）+ 联网搜索增强",
        "note": "利好/利空为关键词规则判定；命中主线题材的板块/个股已标注",
    }
    path = zt_store.zt_root() / ("news_" + date + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blk, ensure_ascii=False, indent=2), encoding="utf-8")
    return blk


def load_news(date: str) -> Optional[dict]:
    path = zt_store.zt_root() / ("news_" + date + ".json")
    if path.exists():
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None
