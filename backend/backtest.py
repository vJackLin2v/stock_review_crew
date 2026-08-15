# -*- coding: utf-8 -*-
"""历史回测编排器（backend）：后台线程逐日跑「撮合前一计划 + 复盘当天 + 产物校验」，
带精确到每天/每阶段/每分析师的耗时采集，并落盘持久化。

契约（docs/requirements-backtest-ui.md §三/§四/§五/§六，唯一真源）：
- 数据模型：/api/backtest/{id} 返回 §三 完整对象；
- 状态：queued | running | done | error | cancelled；每天 status 为
  pending | running | done | failed | skipped；
- 单 running 互斥：再次 start 返回 409 中文（绝不并发跑）；
- 交易日列表：start..end 周一至周五（_is_trading_day，与前端/单日复盘同源）；
- 逐日：`settle_plans(prev, day)`（记 settle 耗时 + filled/rejected/quotes_missing）
  → `run_review_day(day, mode, max_rounds, trace=False, ...)`（记 review 耗时 +
  阶段/分析师耗时）→ 校验产物（对齐 work/review_batch.py 的 validate_run_product
  口径）→ 记当天 elapsed/status；
- fail-fast：单日重试 MAX_ATTEMPTS(3) 次仍失败 → 中止，status=error，中文 error；
  绝不静默跳过；
- cancel：置标志位，当日完成后停止后续日期，status=cancelled；
- 落盘：manifest.json（含 created_at/finished_at/params/status）+ progress.json
  （每次阶段/天变化时写）；尊重 STOCK_REVIEW_DATA_DIR 隔离；
- 历史：list_backtests() 读 data/backtests/*/manifest.json，按 created_at 倒序，
  上限 20 条；
- 断点续跑不做（回测是重跑性质）。

所有面向用户的投资内容必须带「仅供参考，不构成投资建议」。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import HTTPException

from backend.jobs import MODES, _is_trading_day, _validate_date, run_review_day

logger = logging.getLogger("stock_review_crew.backend")

ROOT = Path(__file__).resolve().parent.parent

MAX_ATTEMPTS = 3  # 单日最多尝试次数（含首次）；屡败即 fail-fast 中止
RETRY_SLEEP = 2   # 失败重试间隔（秒；回测用短间隔便于测试/观察，重试语义同 review_batch）
STAGES = ("fetch", "analysts", "host", "report")  # 资讯默认关，无 news

# meta.degraded 中命中即判 LLM 降级的关键词（复核 work/review_batch.LLM_DEGRADED_MARKERS 口径）
LLM_DEGRADED_MARKERS = ("LLM 不可用", "LLM 失败", "降级为规则")

_lock = threading.Lock()
BACKTESTS: dict[str, dict[str, Any]] = {}

# 生成器注册点：run_review_day 与 settle_plans 可在测试中注入替代实现（mock，不真调 LLM/网络）
_run_review_day_impl: Callable[..., dict[str, Any]] = run_review_day
# 单位：{prev_date, day} → settle 结果 dict（reduced_plans 返回结构）
_settle_impl: Optional[Callable[[str, str], dict]] = None


# ═══════════════════════════════════════════════════════════════
# 数据根 / 落盘
# ═══════════════════════════════════════════════════════════════

def _data_root() -> Path:
    """数据根：STOCK_REVIEW_DATA_DIR 覆盖（测试隔离），否则仓库 data/。"""
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return ROOT / "data"


def backtests_root() -> Path:
    """回测落盘根目录：<数据根>/backtests。"""
    return _data_root() / "backtests"


def _run_dir(bt_id: str) -> Path:
    return backtests_root() / bt_id


def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - 落盘失败不中断主流程（内存态仍准）
        logger.warning("回测落盘失败（%s）：%s", path, exc)


def _persist(bt: dict) -> None:
    """公开字段写 progress.json；manifest.json 只写生命周期字段（created/finished/params/status）。"""
    public = {k: bt[k] for k in bt if not k.startswith("_")}
    _write_json(_run_dir(bt["id"]) / "progress.json", public)
    _write_json(
        _run_dir(bt["id"]) / "manifest.json",
        {
            "id": bt["id"],
            "start": bt["start"],
            "end": bt["end"],
            "status": bt["status"],
            "params": bt["params"],
            "created_at": bt["created_at"],
            "finished_at": bt["finished_at"],
        },
    )


# ═══════════════════════════════════════════════════════════════
# 交易日 + 产物校验（复用 review_batch.validate_run_product 口径）
# ═══════════════════════════════════════════════════════════════

def trading_dates(start: str, end: str) -> list[str]:
    """工作日序列（周一至周五），含边界；start..end 有序。"""
    out: list[str] = []
    cur = datetime.strptime(start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d")
    while cur <= stop:
        if _is_trading_day(cur):
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _llm_degraded_notes(meta: dict) -> list[str]:
    out: list[str] = []
    for note in meta.get("degraded") or []:
        s = str(note)
        if any(m in s for m in LLM_DEGRADED_MARKERS):
            out.append(s)
    return out


def validate_run_product(date_str: str, meta_path: Optional[Path] = None,
                         report_path: Optional[Path] = None,
                         plan_p: Optional[Path] = None) -> tuple[bool, list[str]]:
    """run 产物校验（对齐 work/review_batch.validate_run_product 口径）：
    report.json + plan.json 存在、meta.degraded 无「LLM 不可用/失败」「降级为规则引擎」。

    路径默认按当前 STOCK_REVIEW_DATA_DIR 隔离根解析（与 run_review_day 落盘一致）；
    可注入 Path 便于测试覆写。
    """
    reasons: list[str] = []
    from stock_review_crew.storage import reviews as reviews_store
    from stock_review_crew.accounts import plan as plan_store

    rp = report_path or (reviews_store.reviews_root() / date_str / "report.json")
    mp = meta_path or (reviews_store.reviews_root() / date_str / "meta.json")
    pp = plan_p or plan_store.plan_path(date_str)

    if not rp.exists():
        reasons.append("报告缺失（report.json 不存在）")
    if not pp.exists():
        reasons.append("计划缺失（plan.json 不存在）")
    if not mp.exists():
        reasons.append("meta 缺失（meta.json 不存在）")
        return False, reasons
    try:
        meta = json.loads(mp.read_text(encoding="utf-8"))
        llm_notes = _llm_degraded_notes(meta) if isinstance(meta, dict) else []
    except Exception as exc:  # noqa: BLE001
        llm_notes = []
        reasons.append(f"meta 损坏（{exc}）")
    if llm_notes:
        reasons.append("LLM 降级：" + "；".join(llm_notes)[:200])
    return len(reasons) == 0, reasons


# ═══════════════════════════════════════════════════════════════
# 编排器
# ═══════════════════════════════════════════════════════════════

def _new_backtest(params: dict) -> dict:
    """构造新回测对象（§三结构），写入 manifest，返回内存态对象。"""
    now = datetime.now()
    bt_id = f"bt_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
    start = params["start"]
    end = params["end"]
    days = trading_dates(start, end)
    bt: dict[str, Any] = {
        "id": bt_id,
        "status": "queued",
        "start": start,
        "end": end,
        "params": params,
        "created_at": now.isoformat(timespec="seconds"),
        "finished_at": None,
        "days_total": len(days),
        "days_done": 0,
        "current": None,
        "total_elapsed_ms": 0,
        "days": [
            {
                "date": d,
                "status": "pending",
                "attempt": 0,
                "attempts": 0,
                "elapsed_ms": None,
                "settle": None,
                "review": None,
                "stages": [],
                "error": None,
            }
            for d in days
        ],
        "_running_idx": 0,
        "_cancel": False,
    }
    with _lock:
        BACKTESTS[bt_id] = bt
    _persist(bt)
    return bt


def _settle(prev_date: str, day: str) -> dict:
    """撮合 prev_date 计划到 day 行情（增量）。失败返回 {~'error'}，绝不在编排层抛。"""
    if _settle_impl is not None:
        try:
            return _settle_impl(prev_date, day)
        except Exception as exc:  # noqa: BLE001 - 注入替代失败按失败处理
            return {"error": f"撮合失败：{exc}", "filled": 0, "rejected": 0,
                    "quotes_missing": [], "unsettled": [], "degraded": []}
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "src"))
        from stock_review_crew.accounts.replay import settle_plans
        return settle_plans(prev_date, day)
    except Exception as exc:  # noqa: BLE001 - 撮合失败不阻断复盘（review_batch 同款容忍）
        logger.warning("[backtest] 撮合失败 %s→%s（不阻断复盘）：%s", prev_date, day, exc)
        return {"error": str(exc), "filled": 0, "rejected": 0,
                "quotes_missing": [], "unsettled": [], "degraded": []}


def _run_one_day(bt: dict, day_index: int) -> None:
    """跑单个交易日：settle(prev) → review(day) → 校验；fail-fast 循环与状态落盘。"""
    day = bt["days"][day_index]
    date_str = day["date"]
    prev_date = bt["days"][day_index - 1]["date"] if day_index > 0 else None
    params = bt["params"]
    day["status"] = "running"
    bt["current"] = {"date": date_str, "stage": "settle"}
    bt["status"] = "running"
    _persist(bt)

    settle_result: dict = {"filled": 0, "rejected": 0, "quotes_missing": 0, "elapsed_ms": 0}
    if prev_date is not None:
        _t0 = time.monotonic()
        r = _settle(prev_date, date_str)
        settle_result = {
            "elapsed_ms": int((time.monotonic() - _t0) * 1000),
            "filled": int(r.get("filled") or 0),
            "rejected": int(r.get("rejected") or 0),
            "quotes_missing": len(r.get("quotes_missing") or []),
        }
        if r.get("error"):
            settle_result["note"] = str(r["error"])
    day["settle"] = settle_result

    last_error = ""

    def _timing_cb(stage: str, pct: float, message: str, meta: Any = None) -> None:
        # 阶段/分析师事件用于运行中实时更新 current + 落盘（进度可见性）
        if not (isinstance(meta, dict) and meta.get("type") == "analyst"):
            bt["current"] = {"date": date_str, "stage": stage}
            _persist(bt)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        day["attempt"] = attempt
        day["attempts"] = attempt
        _persist(bt)
        _t1 = time.monotonic()
        try:
            result = _run_review_day_impl(
                date_str, params.get("mode", "close"),
                params.get("max_rounds", 3), trace=False,
                progress_cb=_timing_cb,
            )
        except Exception as exc:  # noqa: BLE001 - DataValidationError 等一律按单日失败处理
            logger.warning("[backtest] %s 第 %d 次失败：%s", date_str, attempt, exc)
            last_error = str(exc)
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_SLEEP)
            continue
        review_ms = int((time.monotonic() - _t1) * 1000)
        # 校验产物（对齐 review_batch 口径）；不合格删记录重跑
        ok, reasons = validate_run_product(date_str)
        if not ok:
            last_error = "；".join(reasons)
            # 产物不合格视为单日失败（重跑覆盖写入，交付时再校验）
            try:
                from stock_review_crew.storage import reviews as reviews_store

                reviews_store.delete_review(date_str)
            except Exception:  # noqa: BLE001
                pass
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_SLEEP)
                continue
            break

        # 成功：结算当天总耗时 = settle + review
        day["status"] = "done"
        day["elapsed_ms"] = review_ms + int(settle_result.get("elapsed_ms") or 0)
        day["review"] = {"elapsed_ms": _total_stage_ms(result.get("stage_timings"))}
        day["stages"] = _build_stages(result)
        # 记录分析师耗时（overwrite day.stages[analysts].analysts）
        _fill_analyst_gauge(day, result.get("analyst_timings") or {})
        last_error = ""
        break

    if day["status"] == "running":
        day["status"] = "failed"
        day["error"] = last_error or "单日复盘失败"
        bt["status"] = "error"
        bt["finished_at"] = datetime.now().isoformat(timespec="seconds")
        bt["total_elapsed_ms"] = _total_run_ms(bt)
        bt["current"] = {"date": date_str, "stage": "error"}
        _persist_day_and_meta(bt)
        raise _BacktestStopped(day["error"], bt)


class _BacktestStopped(Exception):
    """回测中止信号（fail-fast / cancel）：携带回测 id 与原因中文。"""

    def __init__(self, message: str, bt: dict):
        super().__init__(message)
        self.bt = bt


def _total_stage_ms(stage_timings: dict) -> int:
    if not stage_timings:
        return 0
    return sum(int(v["elapsed_ms"]) for v in stage_timings.values() if v.get("elapsed_ms"))


def _build_stages(result: dict) -> list[dict]:
    st = result.get("stage_timings") or {}
    out: list[dict] = []
    for key in STAGES:
        v = st.get(key)
        out.append({"key": key, "elapsed_ms": int(v["elapsed_ms"]) if v and v.get("elapsed_ms") else 0})
    return out


def _fill_analyst_gauge(day: dict, analyst_timings: dict) -> None:
    for node in day.get("stages") or []:
        if node.get("key") == "analysts":
            rows = []
            for sid, v in (analyst_timings or {}).items():
                if isinstance(v, dict):
                    rows.append({
                        "skill_id": v.get("skill_id", sid),
                        "name": v.get("name", v.get("skill_id", sid)),
                        "elapsed_ms": v.get("elapsed_ms"),
                    })
            node["analysts"] = sorted(rows, key=lambda x: (x.get("skill_id") or ""))


def _total_run_ms(bt: dict) -> int:
    return sum(int(d.get("elapsed_ms") or 0) for d in bt["days"])


def _persist_day_and_meta(bt: dict) -> None:
    """单日结束/中止时统一落盘（progress + manifest）。"""
    bt["days_done"] = sum(1 for d in bt["days"] if d["status"] == "done")
    _persist(bt)


def _run_loop(bt: dict) -> None:
    """后台逐日循环：settle(prev) → review → 校验；fail-fast / cancel。"""
    try:
        for i in range(len(bt["days"])):
            with _lock:
                if bt["_cancel"]:
                    bt["status"] = "cancelled"
                    bt["finished_at"] = datetime.now().isoformat(timespec="seconds")
                    bt["total_elapsed_ms"] = _total_run_ms(bt)
                    bt["current"] = {"date": bt["days"][i]["date"], "stage": "cancelled"}
                    _persist_day_and_meta(bt)
                    return
            _run_one_day(bt, i)
            bt["days_done"] = sum(1 for d in bt["days"] if d["status"] == "done")
            bt["total_elapsed_ms"] = _total_run_ms(bt)
            bt["current"] = None
            _persist(bt)
        bt["status"] = "done"
        bt["finished_at"] = datetime.now().isoformat(timespec="seconds")
        bt["total_elapsed_ms"] = _total_run_ms(bt)
        bt["days_done"] = len(bt["days"])
        bt["current"] = None
        _persist(bt)
    except _BacktestStopped:
        # _run_one_day 已将 bt.status 置 error/cancelled 并落盘
        _persist_day_and_meta(bt)
    except Exception as exc:  # noqa: BLE001 - 编排器自身异常 → error
        logger.exception("回测编排器异常：id=%s", bt["id"])
        bt["status"] = "error"
        bt["finished_at"] = datetime.now().isoformat(timespec="seconds")
        bt["total_elapsed_ms"] = _total_run_ms(bt)
        idx = _first_open_day_index(bt)
        bt["current"] = {"date": bt["days"][idx]["date"], "stage": "error"}
        if not bt["days"][idx].get("error"):
            bt["days"][idx]["error"] = f"回测编排异常：{exc}"
        _persist_day_and_meta(bt)


def _first_open_day_index(bt: dict) -> int:
    """首个非终态（待跑/进行中）日索引，兜底 0。"""
    for i, d in enumerate(bt["days"]):
        if d["status"] not in ("done", "failed", "skipped"):
            return i
    return 0


def start_backtest(params: dict) -> str:
    """启动一次回测：单 running 互斥 + 落盘 + 后台线程逐日。返回 backtest_id。"""
    start = _validate_date(params.get("start"))
    end = _validate_date(params.get("end"))
    if start > end:
        raise HTTPException(status_code=400, detail="起始日期不能晚于结束日期")
    mode = str(params.get("mode") or "close").strip() or "close"
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"无效模式：{mode}（可选：{'、'.join(MODES)}）")
    try:
        max_rounds = int(params.get("max_rounds") or 3)
    except (TypeError, ValueError):
        max_rounds = 3
    max_rounds = max(1, min(max_rounds, 3))
    force = bool(params.get("force"))
    days = trading_dates(start, end)
    if not days:
        raise HTTPException(status_code=400, detail=f"所选范围（{start} 至 {end}）内无交易日")

    with _lock:
        for bt in BACKTESTS.values():
            if bt.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail="已有回测在进行中，请先等待其完成或被取消")

    clean = {
        "start": start,
        "end": end,
        "force": force,
        "max_rounds": max_rounds,
        "mode": mode,
    }
    bt = _new_backtest(clean)
    try:
        threading.Thread(target=_run_loop, args=(bt,), daemon=True).start()
    except Exception as exc:  # noqa: BLE001 - 启动失败清理 registry
        with _lock:
            BACKTESTS.pop(bt["id"], None)
        raise HTTPException(status_code=500, detail=f"回测任务启动失败：{exc}") from exc
    return bt["id"]


def cancel_backtest(bt_id: str) -> dict:
    """取消回测：置标志位，当日完成后停，status=cancelled。终态取消失败。"""
    with _lock:
        bt = BACKTESTS.get(bt_id)
    if bt is None:
        # 可能已清出内存（重启丢失，断点续跑不做），容错
        raise HTTPException(status_code=404, detail="回测不存在")
    if bt.get("status") in ("done", "error", "cancelled"):
        raise HTTPException(status_code=409, detail=f"回测已结束（{bt['status']}），无法取消")
    with _lock:
        bt["_cancel"] = True
    return {"ok": True}


def get_backtest(bt_id: str) -> dict:
    """返回 §三 完整对象（公开字段，去掉 _ 内部字段）。"""
    with _lock:
        bt = BACKTESTS.get(bt_id)
    if bt is not None:
        return _public(bt)
    # 内存不带 → 从落盘 manifest progress 重建（重启可查历史）
    p = _run_dir(bt_id) / "progress.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    raise HTTPException(status_code=404, detail="回测不存在")


def _public(bt: dict) -> dict:
    return {k: bt[k] for k in bt if not k.startswith("_")}


def list_backtests(limit: int = 20) -> list[dict]:
    """历史回测：data/backtests/*/manifest.json → {id,start,end,status,created_at,
    finished_at,days_done,days_total,total_elapsed_ms}，按 created_at 倒序，上限 20。"""
    root = backtests_root()
    runs: list[dict] = []
    if root.is_dir():
        for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if not d.is_dir():
                continue
            mf = d / "manifest.json"
            if not mf.exists():
                continue
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            bt_id = m.get("id") or d.name
            prog = {}
            try:
                prog = json.loads((d / "progress.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
            runs.append({
                "id": bt_id,
                "start": m.get("start") or prog.get("start"),
                "end": m.get("end") or prog.get("end"),
                "status": m.get("status") or prog.get("status"),
                "created_at": m.get("created_at") or prog.get("created_at"),
                "finished_at": m.get("finished_at") or prog.get("finished_at"),
                "days_done": prog.get("days_done") if prog else 0,
                "days_total": prog.get("days_total") if prog else 0,
                "total_elapsed_ms": prog.get("total_elapsed_ms") if prog else 0,
            })
    runs.sort(key=lambda r: (r.get("created_at") or ""), reverse=True)
    return runs[:limit]
