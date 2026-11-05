"""Stock Review Crew 后端 API（FastAPI）：app/生命周期/CORS/异常处理/health/静态托管 + 路由装配。

从单体 main.py 拆分（2026-08-13，纯重构不改行为）：
- backend/jobs.py：JOBS 状态 + 任务执行（_run_review_job）与复盘纯函数；
- backend/snapshot.py：引擎快照 → 前端契约快照（_to_frontend_snapshot）；
- backend/routes_review.py / routes_chat.py / routes_data.py：各 APIRouter。
本文件保留：app/lifespan/RequestValidationError 异常处理/CORS（CORS_ORIGINS 环境变量化）/
GET /api/health/静态托管 frontend/dist + include_router 装配（trades → accounts → 新拆路由）。

契约（docs/requirements.md §五 + docs/requirements-r11.md §四，冻结）：
- POST /api/reviews {date, mode, max_rounds?} -> {job_id}：日期/模式/窗口校验（中文 400）；
- GET  /api/jobs/{job_id} -> 轮询契约（内部 _ 字段不外泄；终态 TTL >1h 移除、保留最近 50 条，
  进行中不删）；
- GET /api/reviews 历史分组（每日一条）、GET /api/reviews/status?date=（R11 R2 自动复盘状态）、
  GET/DELETE /api/reviews/{date}（R11 R4 每日一份，去掉 /{time}）、GET /api/reviews/context；
- 聊天：POST/GET/DELETE /api/chat/sessions[...]，每条消息响应恒含 disclaimer；
- GET /api/health；frontend/dist 存在时静态托管 "/"。
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend import accounts_routes  # noqa: E402  # R12 模拟账户路由
from backend import backtest  # noqa: E402  # 历史回测编排器（回测）
from backend import jobs  # noqa: E402  # JOBS 状态 + 任务执行（拆分自 main）
from backend import routes_backtest  # noqa: E402  # 回测编排路由（回测）
from backend import routes_chat  # noqa: E402  # 聊天路由（拆分自 main）
from backend import routes_data  # noqa: E402  # 行情/名称/自选路由（拆分自 main）
from backend import routes_anomaly  # noqa: E402  # 监管异动（2026-08-17）
from backend import routes_review  # noqa: E402  # 复盘/任务轮询路由（拆分自 main）
from backend import settings_routes  # noqa: E402  # 模拟交易设置路由（初始资金）
from backend import snapshot  # noqa: E402  # 快照转换（拆分自 main）
from backend import trades_routes  # noqa: E402  # R11-R1 交割单分析路由（T4）

logger = logging.getLogger("stock_review_crew.backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 Langfuse 监控并输出状态（2026-08-06：每次启动强制打开）。"""
    try:
        from stock_review_crew import config

        st = config.langfuse_status()
        if st["enabled"] and st["callback_ok"]:
            logger.info("Langfuse 遥测已启用：host=%s（复盘/聊天 LLM 全程监控）", st["host"])
        elif st["enabled"]:
            logger.warning(
                "Langfuse 凭据已配置但回调初始化失败（host=%s）；LLM 调用不带 Langfuse 回调，"
                "不影响主流程，请检查 LANGFUSE_* 配置",
                st["host"],
            )
        else:
            logger.warning(
                "Langfuse 未启用：缺少 LANGFUSE_PUBLIC_KEY/SECRET_KEY（或 LANGFUSE_ENABLED=0），"
                "复盘/聊天无 Langfuse 监控"
            )
    except Exception as exc:  # noqa: BLE001 - 启动检查失败不阻塞服务
        logger.warning("Langfuse 启动检查失败（服务继续启动）：%s", exc)
    yield


def _cors_origins() -> list[str]:
    """CORS 白名单：环境变量 CORS_ORIGINS（逗号分隔）覆盖，默认本地 Vite 开发地址。

    解析失败（未设置/空串/拆分后无有效项/读取异常）回退默认值，保证服务可启动。
    """
    default = ["http://localhost:5173", "http://127.0.0.1:5173"]
    try:
        raw = os.environ.get("CORS_ORIGINS", "")
    except Exception:  # noqa: BLE001 - 环境变量读取异常回退默认
        return default
    if not raw or not raw.strip():
        return default
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or default


def _flatten_included_routers(app: FastAPI) -> None:
    """兼容 fastapi>=0.140 的懒加载 include_router（_IncludedRouter 不扁平化进 app.routes）。

    fastapi 0.140+ 的 APIRouter.include_router 改为追加懒加载 _IncludedRouter
    （app.routes 不扁平化，仅请求时物化匹配）；0.115 及更早版本直接扁平化。
    本项目 include_router 均无 prefix/tags/dependencies，物化后的普通路由与原路由
    等价。为保持拆分前 app.routes 语义（契约探针/测试遍历路径可见全部路由），
    装配阶段把 _IncludedRouter 展开为普通路由；旧版本自动空操作。
    """
    flattened: list[Any] = []
    changed = False
    for route in app.routes:
        if type(route).__name__ != "_IncludedRouter":
            flattened.append(route)
            continue
        changed = True
        for candidate in route.effective_candidates():
            if type(candidate).__name__ == "_IncludedRouter":
                continue  # 嵌套包含（本项目不使用）
            target = getattr(candidate, "starlette_route", None) or getattr(candidate, "original_route", None)
            if target is not None:
                flattened.append(target)
    if changed:
        app.router.routes = flattened


app = FastAPI(title="Stock Review Crew API", version="0.1.0", lifespan=lifespan)
app.include_router(trades_routes.router)  # R11-R1 交割单分析：POST /api/trades/*（T4）
app.include_router(accounts_routes.router)  # R12 模拟账户：GET /api/accounts/*（A1）
# 以下为拆分自 main.py 的路由（追加在 trades/accounts 之后，保持原有注册顺序）
app.include_router(routes_review.router)  # 复盘：POST /api/reviews、GET /api/jobs/*、GET/DELETE /api/reviews/*
app.include_router(routes_backtest.router)  # 回测：POST /api/backtest/start、GET /api/backtest/*
app.include_router(routes_chat.router)  # 聊天：POST/GET/DELETE /api/chat/sessions[...]
app.include_router(routes_data.router)  # 行情数据：/api/data/*、/api/watchlist
from backend import routes_zt  # noqa: E402  # 涨停复盘工作台：/api/data/zt-tracking/*
app.include_router(routes_zt.router)  # 涨停复盘工作台（手动采集/数据包/人工标记）
app.include_router(routes_anomaly.router)  # 监管异动：/api/anomaly/*
app.include_router(settings_routes.router)  # 模拟交易设置：/api/settings/accounts
_flatten_included_routers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """参数校验错误一律中文 400（FastAPI 默认 422 英文文案不满足契约）。"""
    return JSONResponse(status_code=400, content={"detail": "请求参数格式错误，请检查请求体与字段类型"})


@app.get("/api/health")
def health() -> dict[str, Any]:
    """健康检查：附带 Langfuse 监控状态与资讯开关（供前端/运维确认）。"""
    try:
        from stock_review_crew import config

        langfuse = config.langfuse_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse 状态检查失败：%s", exc, exc_info=True)
        langfuse = {"enabled": False, "callback_ok": False, "host": None, "mode": "error"}
    # 2026-08-12：news_enabled 供前端决定进度条是否显示「资讯」阶段
    return {"status": "ok", "langfuse": langfuse, "news_enabled": config.news_enabled()}


# ── 静态托管（必须在 API 路由之后挂载）──

DIST = ROOT / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")


# ═══════════════════════════════════════════════════════════════
# 测试兼容 re-export（tests 直接 import backend.main.XXX / monkeypatch backend.main.XXX）
# 保持拆分前 backend.main 命名空间可用；补丁值经 jobs._host() 实时读取本模块绑定生效。
# ═══════════════════════════════════════════════════════════════

from backend.jobs import (  # noqa: E402
    INTRADAY_MODES,
    JOB_TTL_SECONDS,
    JOBS,
    MAX_JOBS,
    MODES,
    MODE_LABELS,
    MODE_WINDOWS,
    _MARKET_EXTRA_CACHE,
    _MARKET_EXTRA_CACHE_TTL,
    _context_item_text,
    _host,
    _is_trading_day,
    _lock,
    _make_summary,
    _market_extra_call,
    _nearest_mode,
    _norm_key,
    _now,
    _pending_job_id,
    _progress_cb,
    _prune_jobs,
    _quality_structured,
    _resolve_latest_date,
    _run_review_job,
    _snapshot_sources,
    _update,
    _validate_date,
    _window_error,
    _yesterday_report_text,
    run_review_day,
)
from backend.snapshot import (  # noqa: E402
    _INDEX_NAMES,
    _as_int,
    _block_degraded,
    _find_block,
    _ladder_label,
    _ladder_rows,
    _ladder_stocks,
    _pick_int,
    _pick_ratio,
    _ratio,
    _snap_breadth,
    _snap_index_minute,
    _snap_ladder,
    _snap_sectors,
    _snap_sectors_bottom,
    _snap_sentiment,
    _to_frontend_snapshot,
)
from backend.routes_data import (  # noqa: E402
    _NAME_CACHE_TTL_SECONDS,
    _STOCK_CODE_RE,
    _name_cache,
    _resolve_stock_name,
    _stock_name_tx_symbol,
    _stock_names_via_tx_batch,
    _watchlist_storage,
    resolve_stock_names,
)
from backend.routes_review import _status_payload  # noqa: E402
from backend.routes_chat import _chat_engine  # noqa: E402
