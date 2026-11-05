"""复盘/任务轮询路由模块（APIRouter，import jobs/底层模块，不 import main）。

从 backend/main.py 拆分（2026-08-13，纯重构不改行为）：
- POST /api/reviews（创建复盘任务）、GET /api/jobs/{job_id}（轮询契约）；
- GET /api/reviews（历史分组）、GET /api/reviews/status?date=（R11 R2 自动复盘状态）、
  GET /api/reviews/context、GET/DELETE /api/reviews/{date}（R11 R4 每日一份）。
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Response

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.jobs import (  # noqa: E402
    MODES,
    JOBS,
    _host,
    _is_trading_day,
    _lock,
    _nearest_mode,
    _pending_job_id,
    _prune_jobs,
    _resolve_latest_date,
    _run_review_job,
    _validate_date,
    _window_error,
)
from stock_review_crew.storage import reviews  # noqa: E402

router = APIRouter()


@router.post("/api/reviews")
def create_review(payload: Any = Body(...)) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    date = _validate_date(payload.get("date"))
    mode = str(payload.get("mode") or "").strip()
    if not mode:
        raise HTTPException(status_code=400, detail="缺少参数：mode")
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"无效模式：{mode}（可选：{'、'.join(MODES)}）")
    analysts = payload.get("analysts")
    if analysts is not None:
        if not isinstance(analysts, list) or not all(isinstance(x, str) for x in analysts):
            raise HTTPException(status_code=400, detail="analysts 必须为字符串数组")
        from stock_review_crew.skills import list_skills
        valid = {x.get("id") for x in list_skills()}
        bad = [x for x in analysts if x not in valid]
        if bad:
            raise HTTPException(status_code=400, detail=f"不存在的分析师 id：{', '.join(bad)}")
        analysts = [x for x in analysts if x]
    max_rounds = 3
    if payload.get("max_rounds") is not None:
        try:
            max_rounds = int(payload["max_rounds"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="max_rounds 必须为整数（1-3）")
        max_rounds = max(1, min(max_rounds, 3))
    # 2026-08-12：trace 开关——脚本批量跑传 trace=false 跳过 Langfuse 复盘级 trace；
    # 前端页面不传该字段，默认 true（保持监控）
    trace = payload.get("trace", True)
    if not isinstance(trace, bool):
        raise HTTPException(status_code=400, detail="trace 必须为布尔值（true/false）")
    error = _window_error(date, mode)
    if error:
        raise HTTPException(status_code=400, detail=error)

    # R11 R4：每天单份语义——已有 done 记录 / 进行中 job 不重复创建（前端自动触发去重）
    if reviews.has_review(date):
        raise HTTPException(
            status_code=409,
            detail=f"该日期（{date}）已有复盘记录，每日仅保留一份；如需重新生成请先删除后重试",
        )
    pending = _pending_job_id(date)
    if pending:
        raise HTTPException(status_code=409, detail="该日期已有复盘任务进行中，请勿重复提交")

    _prune_jobs()
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "pct": 0,
            "message": "等待开始",
            "analysts_done": 0,
            "analysts_total": len(analysts) if analysts else 5,
            "result": None,
            "error": None,
            "_created": time.monotonic(),
            "_finished": None,
            "_date": date,
            "_trace": trace,
            "_analysts": analysts,
        }
    try:
        threading.Thread(
            target=_run_review_job,
            args=(job_id, date, mode, max_rounds, trace, analysts),
            daemon=True,
        ).start()
    except Exception as exc:  # noqa: BLE001
        with _lock:
            JOBS.pop(job_id, None)
        raise HTTPException(status_code=500, detail=f"任务启动失败：{exc}") from exc
    return {"job_id": job_id}


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    _prune_jobs()
    with _lock:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {key: value for key, value in job.items() if not key.startswith("_")}


@router.get("/api/reviews")
def review_list() -> list[dict[str, Any]]:
    return reviews.list_reviews()


@router.get("/api/reviews/status")
def review_status(date: str = "") -> dict[str, Any]:
    """R11 R2 自动复盘状态（requirements-r11.md §四）：

    GET /api/reviews/status?date=YYYY-MM-DD
    → {date, is_trading_day, latest_date, latest_mode, has_today_review, can_auto, pending_job_id}
    - latest_date：date 当天 15:00 前 → 最近一个交易日（周一 15:00 前 = 上周五；
      非交易日 → 最近交易日）；15:00 后且 date 为当日交易日 → date 本身；
    - has_today_review：data/reviews/{date} 是否有复盘记录（含旧结构兼容）；
    - can_auto：交易日 && 当前时间 ≥15:00 && 无当日复盘 && 无进行中 job；
    - pending_job_id：当天已提交且未完成的任务 id（JOBS 内存态查找，无则 null）。
    时间判定复用 _now/_nearest_mode；交易日判定 _is_trading_day（与前端 modes.js 同源）。
    """
    if not date:
        raise HTTPException(status_code=400, detail="缺少参数：date")
    date = _validate_date(date)
    now = _host("_now")()
    return _status_payload(date, now)


def _status_payload(date: str, now: datetime) -> dict[str, Any]:
    """R11 R2 status 载荷计算（独立函数，便于单元/手工验证；now 由调用方注入）。"""
    minutes = now.hour * 60 + now.minute
    is_trading_day = _is_trading_day(datetime.strptime(date, "%Y-%m-%d"))
    has_today_review = reviews.has_review(date)
    pending_job_id = _pending_job_id(date)
    can_auto = (
        is_trading_day
        and minutes >= 15 * 60
        and not has_today_review
        and pending_job_id is None
    )
    return {
        "date": date,
        "is_trading_day": is_trading_day,
        "latest_date": _resolve_latest_date(date, now),
        "latest_mode": _nearest_mode(minutes),
        "has_today_review": has_today_review,
        "can_auto": can_auto,
        "pending_job_id": pending_job_id,
    }


@router.get("/api/reviews/context")
def review_context(date: str = "", mode: str = "") -> dict[str, Any]:
    if not date:
        raise HTTPException(status_code=400, detail="缺少参数：date")
    date = _validate_date(date)
    if mode and mode not in MODES:
        raise HTTPException(status_code=400, detail=f"无效模式：{mode}（可选：{'、'.join(MODES)}）")
    return reviews.context(date, mode or None)


@router.get("/api/reviews/{date}")
def review_detail(date: str) -> dict[str, Any]:
    """单日复盘详情（R11 R4：每日一份；旧多份结构取时间点最新一份兼容展示）。"""
    detail = reviews.get_review(date)
    if not detail.get("meta"):
        raise HTTPException(status_code=404, detail="复盘记录不存在")
    return detail


@router.delete("/api/reviews/{date}", status_code=204)
def review_delete(date: str) -> Response:
    """删除单日复盘（R11 R4：整日一份，含旧多份结构整日清理）。"""
    if not reviews.delete_review(date):
        raise HTTPException(status_code=404, detail="复盘记录不存在")
    return Response(status_code=204)
