# -*- coding: utf-8 -*-
"""涨停复盘工作台路由（APIRouter，手动触发 + 数据包 + 人工标记）。

- POST   /api/data/zt-tracking/run           手动采集（默认当日，支持 dates 列表补历史；job 轮询）
- GET    /api/data/zt-tracking/run/{job_id}  job 状态
- GET    /api/data/zt-tracking?date=         整页数据包（梯队/题材/轮动/核心股/信号/天气/席位）
- GET    /api/data/zt-tracking/rotation-pool 轮动池 + 核心股 + 介入信号（可独立消费）
- POST   /api/data/zt-tracking/tags          人工标记（题材/席位类型/板块标签）
- GET    /api/data/zt-tracking/pending       待人工补充 CSV（最近生成）

口径：docs/开发计划-涨停复盘辅助.md（C1-C19/D1-D14 已全部确认）。
所有投资相关内容响应恒含 disclaimer。
"""

from __future__ import annotations

import logging
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.jobs import _validate_date  # noqa: E402

logger = logging.getLogger("stock_review_crew.backend")
router = APIRouter()

DISCLAIMER = "仅供参考，不构成投资建议"

ZT_JOBS: dict[str, dict[str, Any]] = {}
_job_lock = threading.Lock()


def _update(job_id: str, **kw: Any) -> None:
    with _job_lock:
        ZT_JOBS[job_id].update(kw)
        ZT_JOBS[job_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _invoke(dates: list[str], force: bool) -> dict:
    """后台执行：逐日采集 → 分析（轮动/核心股/信号/待填CSV）。"""
    from stock_review_crew.tools.zt_tracking import run_tracking_day
    from stock_review_crew import zt_analysis as zt_ana

    results: dict[str, Any] = {}
    for d in dates:
        snap = run_tracking_day(d)
        results[d] = {
            "stocks": len(snap.get("stocks", [])),
            "pool_source": (snap.get("pool") or {}).get("source"),
            "boards": len((snap.get("boards") or {}).get("boards", [])),
        }
    zt_ana.update_pools()
    return results


@router.post("/api/data/zt-tracking/run")
def zt_run(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """手动触发采集（C9：不做自动批跑；D14：支持任意历史日期冗余）。

    Body：{dates?: [YYYY-MM-DD...], force?: bool}
    """
    dates = payload.get("dates")
    force = bool(payload.get("force", False))
    today = datetime.now().strftime("%Y-%m-%d")
    if dates is None or not dates:
        dates = [today]
    bad = []
    for d in dates:
        try:
            _validate_date(d)
        except Exception:
            bad.append(str(d))
    if bad:
        raise HTTPException(status_code=400, detail=f"无效日期：{'、'.join(bad)}（应为 YYYY-MM-DD）")
    dates = list(dict.fromkeys(dates))
    job_id = uuid.uuid4().hex[:12]
    with _job_lock:
        ZT_JOBS[job_id] = {"job_id": job_id, "status": "queued", "stage": "排队",
                           "message": "等待执行", "dates": dates, "force": force,
                           "created_at": datetime.now().isoformat(timespec="seconds"),
                           "updated_at": datetime.now().isoformat(timespec="seconds"),
                           "result": None, "error": None}

    def _worker() -> None:
        try:
            _update(job_id, status="running", stage="采集", message="开始逐日采集涨停数据")
            count = 0
            with _job_lock:
                dates_job = list(ZT_JOBS[job_id].get("dates", []))
                force_job = bool(ZT_JOBS[job_id].get("force"))
            total = len(dates_job)
            for i, d in enumerate(dates_job, start=1):
                _update(job_id, stage="采集", message=f"正在采集 {d}（{i}/{total}）")
                _invoke([d], force_job)
                count += 1
            _update(job_id, stage="分析", message="更新轮动池/核心股/介入信号")
            _invoke([], force_job)
            _update(job_id, status="done", stage="完成", message=f"采集 {count} 个日期并完成分析",
                    result={"count": count, "dates": dates_job})
        except Exception as exc:  # noqa: BLE001
            logger.exception("zt-tracking run 失败")
            _update(job_id, status="error", stage="失败", message="执行失败",
                    error=str(exc)[:400])

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job_id, "disclaimer": DISCLAIMER}


@router.get("/api/data/zt-tracking/run/{job_id}")
def zt_run_status(job_id: str) -> dict[str, Any]:
    with _job_lock:
        job = ZT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    out = {k: v for k, v in job.items() if not k.startswith("_")}
    out["disclaimer"] = DISCLAIMER
    return out


def _tier(stocks: list[dict]) -> dict:
    d = {"首板": 0, "2板": 0, "3板": 0, "4板": 0, "5板+": 0}
    for s in stocks:
        lb = s.get("lianban") or 1
        key = "首板" if lb <= 1 else ("2板" if lb == 2 else ("3板" if lb == 3 else ("4板" if lb == 4 else "5板+")))
        d[key] += 1
    return d


@router.get("/api/data/zt-tracking")
def zt_snapshot(date: Optional[str] = None) -> dict[str, Any]:
    """整页数据包（设计稿 v0.2 六张卡片的全部输入）。"""
    from stock_review_crew.storage import zt_tracking as zt_store
    from stock_review_crew.tools import market_extra
    from stock_review_crew import zt_analysis as zt_ana

    dates = zt_store.list_daily_dates()
    if not dates:
        return {"date": None, "empty": True, "message": "暂无数据，请先点击「手动刷新」采集",
                "disclaimer": DISCLAIMER}
    # 快速空值分支：指定日期未落盘或落盘为空白（非交易日/休市）→ 立即返回提示，不重算任何外部数据
    if date is not None and date not in dates:
        return {"date": date, "empty": True,
                "message": "该日期无数据（可能非交易日或尚未采集），可点「采集该日」补跑",
                "disclaimer": DISCLAIMER}
    if date is None:
        date = dates[-1]
    snap = zt_store.load_daily(date) or {}
    from stock_review_crew.tools.zt_tracking import reapply_seat_labels

    snap = reapply_seat_labels(snap) or {}
    boards = (snap.get("boards") or {}).get("boards", [])
    stocks = snap.get("stocks", [])
    if not stocks and not boards:
        return {"date": date, "empty": True,
                "message": "该日期无涨停数据（可能非交易日或休市）",
                "disclaimer": DISCLAIMER}
    try:
        sentiment = market_extra.fetch_market_sentiment(date)
    except Exception as exc:  # noqa: BLE001
        sentiment = {"emotion_score": None, "weather": None, "degraded": True,
                     "degraded_reason": [f"情绪数据失败：{exc}"]}
    try:
        rotation = zt_ana.rotation_status(dates)
        core = zt_ana.core_candidates(date)
        signal = zt_ana.signal(date)
    except Exception as exc:  # noqa: BLE001
        rotation, core, signal = {"boards": [], "note": str(exc)[:120]}, {"shibie": [], "zhongjun": []}, {"items": []}
    try:
        from stock_review_crew.zt_labels import label_mainline_stocks

        labels_blk = label_mainline_stocks(date)
    except Exception as exc:  # noqa: BLE001
        labels_blk = {"date": date, "labels": [], "note": "打标签失败：" + str(exc)[:120]}
    try:
        from stock_review_crew.tools.news_impact import load_news, run_news_impact

        news_blk = load_news(date) or run_news_impact(date)
    except Exception as exc:  # noqa: BLE001
        news_blk = {"date": date, "count": 0, "items": [], "note": "资讯获取失败：" + str(exc)[:100]}
    try:
        del news_blk["labels"]
    except Exception:
        pass
    pools_raw = zt_store.load_pools().get("pools", {})
    pools_summary = {
        name: {
            "status": rec.get("status"), "entry_date": rec.get("entry_date"),
            "exit_date": rec.get("exit_date"), "focus": bool(rec.get("focus")),
            "qualified": bool(rec.get("qualified")), "max_zt10": rec.get("max_zt10"),
            "last_active_date": rec.get("last_active_date"),
            "stocks": rec.get("stocks", []),
            "cumulative": rec.get("cumulative", {}),
            "tracked": rec.get("tracked", []),
            "entry_snapshot": rec.get("entry_snapshot", []),
        }
        for name, rec in pools_raw.items()
    }
    return {
        "date": date,
        "empty": False,
        "pool": snap.get("pool"),
        "filter_note": snap.get("filter_note"),
        "tier": _tier(stocks),
        "boards": boards,
        "stocks": stocks,
        "rotation": rotation,
        "core": core,
        "signal": signal,
        "sentiment": sentiment,
        "pools": pools_summary,
        "labels": labels_blk,
        "news": news_blk,
        "pending": None,
        "disclaimer": DISCLAIMER,
    }


@router.get("/api/data/zt-tracking/rotation-pool")
def zt_rotation(date: Optional[str] = None) -> dict[str, Any]:
    from stock_review_crew.storage import zt_tracking as zt_store
    from stock_review_crew import zt_analysis as zt_ana

    dates = zt_store.list_daily_dates()
    if not dates:
        return {"date": None, "boards": [], "core": {"shibie": [], "zhongjun": []},
                "signal": {"items": []}, "disclaimer": DISCLAIMER}
    if date is None:
        date = dates[-1]
    return {"date": date, "rotation": zt_ana.rotation_status(dates),
            "core": zt_ana.core_candidates(date), "signal": zt_ana.signal(date),
            "disclaimer": DISCLAIMER}


@router.post("/api/data/zt-tracking/tags")
def zt_tag(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """人工标记：题材标签 / 席位类型 / 板块标签 / 核心股确认。"""
    from stock_review_crew.storage import zt_tracking as zt_store

    code = str(body.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="缺少 code（6 位股票代码）")
    manual = zt_store.load_manual_tags()
    seats = zt_store.load_seat_labels()
    written: list[str] = []
    topic = body.get("topic")
    if topic is not None:
        manual.setdefault("topics", {})[code] = str(topic).strip()
        written.append("题材标签")
    seat_name = body.get("seat_name")
    seat_type = body.get("seat_type")
    if seat_name and seat_type:
        t = str(seat_type).strip()
        if t not in ("youzi", "jigou", "lianghua", "waizi", "unknown"):
            raise HTTPException(status_code=400, detail="席位类型仅支持 youzi/jigou/lianghua/waizi/unknown")
        seats.setdefault("labels", {})[str(seat_name).strip()] = {
            "type": t, "alias": body.get("alias") or "", "note": body.get("note") or "",
            "updated_at": datetime.now().isoformat(timespec="seconds"), "source": "manual",
        }
        written.append("席位标注")
    board_tag = body.get("board_tag")
    if board_tag:
        manual.setdefault("board_tags", {})[str(board_tag).strip()] = {
            "tag": str(body.get("tag") or ""), "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        written.append("板块标签")
    core_flag = body.get("core_flag")
    if core_flag:
        manual.setdefault("core_flags", {})[code] = {
            "flag": str(core_flag), "note": body.get("note") or "",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        written.append("核心股确认")
    zt_store.save_manual_tags(manual)
    zt_store.save_seat_labels(seats)
    if not written:
        raise HTTPException(status_code=400, detail="没有可写入的字段（topic/seat_name+seat_type/board_tag/core_flag）")
    return {"ok": True, "written": written, "code": code, "disclaimer": DISCLAIMER}


@router.get("/api/data/zt-tracking/pending")
def zt_pending() -> dict[str, Any]:
    """待人工补充 CSV：返回最近生成文件的相对路径与行数。"""
    from stock_review_crew import zt_analysis as zt_ana

    out = zt_ana.build_pending_csv()
    return {"path": out.get("path"), "rows": out.get("rows"), "dates": out.get("dates"),
            "disclaimer": DISCLAIMER}
