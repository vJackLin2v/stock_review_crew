"""分析师聊天路由模块（APIRouter，import 底层模块，不 import main）。

从 backend/main.py 拆分（2026-08-13，纯重构不改行为）：
POST/GET/DELETE /api/chat/sessions[...]；POST /api/chat/sessions/{id}/messages；
POST /api/chat/sessions/{id}/messages/stream（SSE，requirements-v4 §五 事件契约）。
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_review_crew import chat  # noqa: E402
from stock_review_crew.storage import chats as chat_storage  # noqa: E402

logger = logging.getLogger("stock_review_crew.backend")

router = APIRouter()


# ── 聊天 ──

def _chat_engine():
    return chat._get_default_engine()


@router.post("/api/chat/sessions")
def create_chat_session(payload: Any = Body(...)) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    target_type = str(payload.get("target_type") or "").strip()
    target = str(payload.get("target") or "").strip()
    analysts = payload.get("analysts")
    if not isinstance(analysts, list):
        raise HTTPException(status_code=400, detail="analysts 必须为分析师 ID 列表")
    try:
        return _chat_engine().create_session(
            target_type,
            target,
            [str(a) for a in analysts],
            title=payload.get("title"),
        )
    except ValueError as exc:  # 参数/白名单错误 → 400 中文
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("创建聊天会话失败")
        raise HTTPException(status_code=500, detail=f"创建会话失败：{exc}") from exc


@router.get("/api/chat/sessions")
def chat_session_list(target: str = "", date: str = "") -> list[dict[str, Any]]:
    try:
        metas = _chat_engine().list_sessions(target=target or None, date=date or None)
    except ValueError as exc:  # 非法日期 → 400 中文
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result: list[dict[str, Any]] = []
    for meta in metas:
        session = chat_storage.get_session(meta["session_id"])
        messages = (session or {}).get("messages") or []
        result.append(
            {
                "session_id": meta.get("session_id"),
                "target_type": meta.get("target_type"),
                "target": meta.get("target"),
                "target_name": meta.get("target_name") or meta.get("target"),
                "analysts": meta.get("analysts") or [],
                "title": meta.get("title"),
                "created_at": meta.get("created_at"),
                "date": meta.get("date"),
                "last_message": messages[-1].get("content") if messages else "",
                "message_count": len(messages),
            }
        )
    return result


@router.post("/api/chat/sessions/{session_id}/messages")
def send_chat_message(session_id: str, payload: Any = Body(...)) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    try:
        result = _chat_engine().send_message(session_id, payload.get("content"))
    except ValueError as exc:  # 空消息等参数错误 → 400 中文
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    # Q4（R10）：聊天质量 rubric 评分附到响应（无凭据 no-op）
    try:
        from stock_review_crew import quality

        session = _chat_engine().get_session(session_id)
        analysts = ((session or {}).get("meta") or {}).get("analysts") or []
        trace_id = quality.trace_chat(
            "done", {"session_id": session_id, "target": ((session or {}).get("meta") or {}).get("target")}
        )
        q = quality.score_chat(
            result.get("messages") or [],
            {},
            {"analysts": list(analysts)},
            trace_id,
        )
        if q:
            result["quality"] = q
    except Exception as exc:  # noqa: BLE001 - 质量评估失败不影响聊天交付
        logger.warning("聊天质量评估跳过（不影响回复）：%s", exc)
    return result


@router.post("/api/chat/sessions/{session_id}/messages/stream")
def stream_chat_message(session_id: str, payload: Any = Body(...)) -> StreamingResponse:
    """SSE 流式聊天（requirements-v4 §五 事件契约，通用透传）：
    POST 后持续返回 text/event-stream；不设事件名白名单——ChatEngine 的 emit 回调发什么就
    转发什么（事件名/载荷原样透传），已覆盖 v4 §五 全部事件：
    - stage {stage:"parse"|"need"|"fetch"|"summarize", note}  当前步骤提示（仅阶段，不携带清单内容）；
    - analyst_start / analyst_delta* / analyst_end            分析师分析过程（内部执行，前端可只显示状态）；
    - summary_delta* / summary_end                            总结逐字流式输出；
    - done {session_id, messages, disclaimer}                 收尾（免责声明随消息返回）；
    - error {message}                                         取数失败/引擎异常（worker 兜底异常也转 error）。
    长任务在 daemon 线程执行，事件经队列转发；队列空闲时发 SSE 注释行保活（EventSource 忽略，
    不产生事件），总时长超过 30 分钟兜底以 error 断流，避免连接永久挂起；__end__ 仅作内部哨兵不外发。
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    engine = _chat_engine()
    if engine.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    events: "queue.Queue" = queue.Queue()

    def worker() -> None:
        try:
            def emit(ev: str, pl: Any) -> None:
                # Q4（R10）：done 事件附加聊天质量 rubric 评分（无凭据 no-op）
                if ev == "done" and isinstance(pl, dict) and "quality" not in pl:
                    try:
                        from stock_review_crew import quality

                        session = engine.get_session(session_id)
                        analysts = ((session or {}).get("meta") or {}).get("analysts") or []
                        trace_id = quality.trace_chat(
                            "done", {"session_id": session_id, "target": ((session or {}).get("meta") or {}).get("target")}
                        )
                        q = quality.score_chat(pl.get("messages") or [], {}, {"analysts": list(analysts)}, trace_id)
                        if q:
                            pl = dict(pl)
                            pl["quality"] = q
                    except Exception as exc:  # noqa: BLE001 - 质量评估失败不影响聊天交付
                        logger.warning("聊天质量评估跳过（不影响回复）：%s", exc)
                events.put((ev, pl))

            engine.stream_message(session_id, content, emit=emit)
        except Exception as exc:  # noqa: BLE001 - SSE 内兜底，避免断流
            try:
                events.put(("error", {"message": f"聊天失败：{exc}"}))
            except Exception:
                pass
        finally:
            events.put(("__end__", None))

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        keepalive_seconds = 15.0   # 空闲保活间隔：代理默认读超时常见 60s，取 15s 留余量
        deadline_seconds = 1800.0  # 总时长兜底：引擎内部 LLM 120s/取数 30s，最坏多分析师约 20 分钟内
        deadline = time.monotonic() + deadline_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield "event: error\ndata: " + json.dumps(
                    {"message": "聊天处理超时，请重试"}, ensure_ascii=False
                ) + "\n\n"
                break
            try:
                ev, pl = events.get(timeout=min(keepalive_seconds, remaining))
            except queue.Empty:
                yield ": keepalive\n\n"  # SSE 注释行：EventSource 忽略，仅防代理/网关断流
                continue
            if ev == "__end__":
                break
            yield f"event: {ev}\ndata: {json.dumps(pl, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/chat/sessions/{session_id}")
def chat_session_detail(session_id: str) -> dict[str, Any]:
    session = _chat_engine().get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


@router.delete("/api/chat/sessions/{session_id}", status_code=204)
def chat_session_delete(session_id: str) -> Response:
    if not _chat_engine().delete_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return Response(status_code=204)
