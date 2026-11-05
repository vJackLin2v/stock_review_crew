# -*- coding: utf-8 -*-
"""监管异动路由：GET /api/anomaly/stock?code=&days=（2026-08-17）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from stock_review_crew.tools import anomaly

router = APIRouter(prefix="/api/anomaly", tags=["anomaly"])


@router.get("/stock")
def anomaly_stock(code: str = Query(..., description="6 位股票代码"),
                  days: int = Query(2, ge=1, le=10),
                  date: Optional[str] = Query(None, description="历史口径日期 YYYY-MM-DD，缺省用最新实时价")) -> dict[str, Any]:
    """单股异动计算：3/10/30 日偏离、异动预警、同向计数与未来模拟。"""
    code = str(code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="股票代码应为 6 位数字")
    if date is not None:
        date = date.strip()
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            raise HTTPException(status_code=400, detail="date 应为 YYYY-MM-DD")
    result = anomaly.compute_anomaly(code, days=days, date=date)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error") or "异动计算失败")
    return result
