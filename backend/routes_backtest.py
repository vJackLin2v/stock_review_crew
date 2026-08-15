"""回测编排路由模块（APIRouter，import backtest 编排器，不 import main）。

从 backend 拆分（2026-08-16）：历史回测编排的 HTTP 契约。
契约（docs/requirements-backtest-ui.md §四，唯一真源）：
- POST /api/backtest/start {start, end, force?, max_rounds?, mode?} → {backtest_id}
  （400 非法日期/范围，409 已有 running，中文）；
- GET  /api/backtest/{id} → §三 完整对象；
- POST /api/backtest/{id}/cancel → {ok: true}；
- GET  /api/backtest/history → {runs: [{id,start,end,status,created_at,finished_at,
  days_done,days_total,total_elapsed_ms}]}。
注意：/api/backtest/history 必须在 /api/backtest/{id} 之前注册（避免被 {id} 捕获）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from backend import backtest  # noqa: E402

router = APIRouter()


@router.post("/api/backtest/start")
def backtest_start(payload: Any = Body(...)) -> dict[str, str]:
    """启动一次历史回测（单 running 互斥）。"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    bt_id = backtest.start_backtest(payload)
    return {"backtest_id": bt_id}


@router.get("/api/backtest/history")
def backtest_history(limit: int = 20) -> dict[str, Any]:
    """历史回测列表（按 created_at 倒序，上限 limit）。"""
    limit = max(1, min(int(limit or 20), 100))
    return {"runs": backtest.list_backtests(limit=limit)}


@router.get("/api/backtest/{bt_id}")
def backtest_detail(bt_id: str) -> dict[str, Any]:
    """单次回测完整对象（§三）。"""
    return backtest.get_backtest(bt_id)


@router.post("/api/backtest/{bt_id}/cancel")
def backtest_cancel(bt_id: str) -> dict[str, Any]:
    """取消回测：当日完成后停，status=cancelled。"""
    return backtest.cancel_backtest(bt_id)
