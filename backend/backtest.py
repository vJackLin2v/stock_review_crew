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
- 断点续跑（resume）：失败的 error 回测可从首个非 done 日续跑，保留已 done 天
  产物与账户状态（_run_loop start_idx + resume_backtest）。

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

MAX_ATTEMPTS = 3  # data 类失败（取数/校验/其他）单日最多尝试次数（含首次）；屡败即 fail-fast 中止
# LLM 超时类失败最多尝试次数（慢是持续状态，重试收益低 → 首败后上限降到 2，避免第 3 次整日重跑浪费）
LLM_MAX_ATTEMPTS = 2
RETRY_SLEEP = 2   # 失败重试间隔（秒；回测用短间隔便于测试/观察，重试语义同 review_batch）
STAGES = ("fetch", "analysts", "host", "report")  # 资讯默认关，无 news


def _is_llm_failure(exc: BaseException) -> bool:
    """LLM 超时类失败：llm_runtime 硬超时抛 TimeoutError（慢/挂起为持续状态，
    整日重试收益低 → 限制尝试次数）。取数校验 DataValidationError/其他按 data 类。"""
    return isinstance(exc, TimeoutError)

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
                          plan_p: Optional[Path] = None,
                          allow_llm_degrade: bool = False) -> tuple[bool, list[str]]:
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
    if llm_notes and not allow_llm_degrade:
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
        "accounts": None,
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

    def _update_current(stage: str, message: str, pct: float, node_started_at: float = None,
                        persist: bool = True) -> None:
        """运行中实时更新 bt['current']（只读展示数据）。

        analyst 事件高频，只更新内存不落盘（_persist=False）；阶段切换照常更新+落盘。
        node_started_at 为内部实时计时字段，仅在 GET 时计算 elapsed_ms，不用持久化对外。
        """
        if node_started_at is None:
            node_started_at = time.monotonic()
        bt["current"] = {
            "date": date_str,
            "stage": stage,
            "message": message,
            "pct": pct,
            "attempt": attempt,
            "attempts": max_attempts,
            "last_error": last_error or None,
            "node_started_at": node_started_at,
        }
        if persist:
            _persist(bt)

    def _timing_cb(stage: str, pct: float, message: str, meta: Any = None) -> None:
        # 阶段/分析师事件都更新内存 current（需求回测实时诊断）；analyst 高频不落盘
        is_analyst = isinstance(meta, dict) and meta.get("type") == "analyst"
        _update_current(stage, message or "", pct, persist=not is_analyst)

    # 当天 review 累计耗时：所有尝试（含失败与重试间隔）都计入，避免重试过的天被低估
    review_total_ms = 0
    # 每次尝试一条记录（成功/失败都写，落盘）：attempt/ok/reason/elapsed_ms/retry_sleep_ms
    attempts_log: list[dict] = []
    # 实际生效的尝试上限（动态）：默认 data 类 MAX_ATTEMPTS(3)；一旦某次失败判为 LLM 超时类
    # → 降为 LLM_MAX_ATTEMPTS(2) 并 fail-fast，避免第 3 次整日重跑浪费
    max_attempts = MAX_ATTEMPTS
    for attempt in range(1, MAX_ATTEMPTS + 1):
        # 尝试开始前：若已超过当前生效上限（如 LLM 类降到 2 后）→ 直接 fail-fast 跳出
        if attempt > max_attempts:
            break
        day["attempt"] = attempt
        # attempts 为实际生效上限（LLM 类日 =2，data 类日 =3），原 bug 错写成当前次数
        day["attempts"] = max_attempts
        _update_current("review", "", 0.0, persist=True)
        _t1 = time.monotonic()
        # 快速模式（lite）：仅在开启时传 lite=True 给 run_review_day；非 lite 不传关键字，
        # 兼容既有 mock 注入实现（make_fake_day 等签名不接受 lite）与既有契约。
        _review_kw: dict[str, Any] = {}
        if params.get("lite"):
            _review_kw["lite"] = True
        if params.get("analysts"):
            _review_kw["analysts"] = params["analysts"]
        try:
            result = _run_review_day_impl(
                date_str, params.get("mode", "close"),
                params.get("max_rounds", 3), trace=False,
                progress_cb=_timing_cb,
                **_review_kw,
            )
        except Exception as exc:  # noqa: BLE001 - DataValidationError 等一律按单日失败处理
            logger.warning("[backtest] %s 第 %d 次失败：%s", date_str, attempt, exc)
            last_error = str(exc)
            # LLM 超时类失败：慢/挂起为持续状态，重试收益低 → 后续尝试上限降为 LLM_MAX_ATTEMPTS
            if _is_llm_failure(exc):
                max_attempts = LLM_MAX_ATTEMPTS
            elapsed_ms = int((time.monotonic() - _t1) * 1000)
            # 该次失败后重试等待：最后一次（不再重试）为 0
            retry_sleep_ms = int(RETRY_SLEEP * 1000) if attempt < max_attempts else 0
            # 本次尝试耗时计入累计（失败不算命中）
            review_total_ms += elapsed_ms
            attempts_log.append({
                "attempt": attempt, "ok": False, "reason": last_error,
                "elapsed_ms": elapsed_ms, "retry_sleep_ms": retry_sleep_ms,
            })
            if attempt < max_attempts:
                _update_current("review", "失败准备重试", 0.0, persist=True)
                # 重试间隔等待也计入当天统计时间
                time.sleep(RETRY_SLEEP)
                review_total_ms += retry_sleep_ms
            continue
        review_ms = int((time.monotonic() - _t1) * 1000)
        # 校验产物（对齐 review_batch 口径）；不合格删记录重跑
        allow_llm_degrade = bool(params.get("allow_llm_degrade"))
        ok, reasons = validate_run_product(date_str, allow_llm_degrade=allow_llm_degrade)
        if not ok:
            last_error = "；".join(reasons)
            retry_sleep_ms = int(RETRY_SLEEP * 1000) if attempt < max_attempts else 0
            attempts_log.append({
                "attempt": attempt, "ok": False, "reason": last_error,
                "elapsed_ms": review_ms, "retry_sleep_ms": retry_sleep_ms,
            })
            # 产物不合格视为单日失败（重跑覆盖写入，交付时再校验）
            try:
                from stock_review_crew.storage import reviews as reviews_store

                # 防复发（requirements-backtest-resume H4）：产物校验失败清理时**保留行情快照**
                # （market_data.json）——好快照是离线回放的预热缓存，误删后会破坏离线回放
                # （回放 miss → 实时取数 → 保存坏快照 → 取数校验失败），只删产物不删快照。
                reviews_store.delete_review(date_str, keep_snapshot=True)
            except Exception:  # noqa: BLE001
                pass
            # 本次尝试耗时计入累计（产物不合格也算一次失败尝试）
            review_total_ms += review_ms
            if attempt < max_attempts:
                _update_current("review", last_error, 0.0, persist=True)
                # 重试间隔等待也计入当天统计时间
                time.sleep(RETRY_SLEEP)
                review_total_ms += retry_sleep_ms
                continue
            break

        # 成功：本次尝试耗时计入累计，当天 review 总耗时 = 各次尝试（含失败/重试）之和
        review_total_ms += review_ms
        attempts_log.append({
            "attempt": attempt, "ok": True, "reason": "",
            "elapsed_ms": review_ms, "retry_sleep_ms": 0,
        })
        day["status"] = "done"
        day["attempts_log"] = attempts_log
        day["elapsed_ms"] = review_total_ms + int(settle_result.get("elapsed_ms") or 0)
        day["review"] = {"elapsed_ms": review_total_ms}
        day["stages"] = _build_stages(result)
        # 记录分析师耗时（overwrite day.stages[analysts].analysts）
        _fill_analyst_gauge(day, result.get("analyst_timings") or {})
        last_error = ""
        bt["current"] = None
        break

    if day["status"] == "running":
        day["status"] = "failed"
        day["error"] = last_error or "单日复盘失败"
        day["attempts_log"] = attempts_log
        # fail-fast：累计全部尝试耗时（含失败与重试间隔），前端折线/明细不再低估
        day["elapsed_ms"] = review_total_ms + int(settle_result.get("elapsed_ms") or 0)
        day["review"] = {"elapsed_ms": review_total_ms}
        bt["status"] = "error"
        bt["finished_at"] = datetime.now().isoformat(timespec="seconds")
        bt["total_elapsed_ms"] = _total_run_ms(bt)
        bt["current"] = {
            "date": date_str, "stage": "error", "message": day["error"],
            "pct": 0.0, "attempt": day["attempt"], "attempts": max_attempts,
            "last_error": last_error or None,
            # 终态无节点已耗时（GET 不计算 elapsed_ms，前端显示「—」）
            "node_started_at": None,
        }
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


def _run_loop(bt: dict, start_idx: int = 0) -> None:
    """后台逐日循环：settle(prev) → review → 校验；fail-fast / cancel。

    start_idx：断点续跑（resume）从该日索引开始（此前 done 的天保留不动），
    默认 0 为全新回测。
    """
    try:
        for i in range(start_idx, len(bt["days"])):
            with _lock:
                if bt["_cancel"]:
                    bt["status"] = "cancelled"
                    bt["finished_at"] = datetime.now().isoformat(timespec="seconds")
                    bt["total_elapsed_ms"] = _total_run_ms(bt)
                    bt["current"] = {"date": bt["days"][i]["date"], "stage": "cancelled"}
                    _persist_day_and_meta(bt)
                    return
            _run_one_day(bt, i)
            if bt.get("_cancel"):
                bt["status"] = "cancelled"
                bt["finished_at"] = datetime.now().isoformat(timespec="seconds")
                bt["current"] = {"date": bt["days"][i]["date"], "stage": "cancelled"}
                _persist(bt)
                return
            bt["days_done"] = sum(1 for d in bt["days"] if d["status"] == "done")
            bt["total_elapsed_ms"] = _total_run_ms(bt)
            bt["current"] = None
            _persist(bt)
        bt["status"] = "done"
        bt["finished_at"] = datetime.now().isoformat(timespec="seconds")
        bt["total_elapsed_ms"] = _total_run_ms(bt)
        bt["days_done"] = len(bt["days"])
        bt["current"] = None
        # 2026-08-17：完成时保存各分析师账户摘要，便于跨回测对比表现
        try:
            bt["accounts"] = _account_summary(bt.get("params", {}).get("analysts"))
        except Exception:  # noqa: BLE001 - 摘要失败不影响回测终态
            bt["accounts"] = {}
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


def _reset_accounts() -> None:
    """回测前重置模拟账户：清空数据根下 accounts/（回到 10 万初始资金）。
    方案 A（用户确认）：每次回测从 10 万重头跑所选区间，不叠加区间之前的历史持仓。"""
    import shutil
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "src"))
    from stock_review_crew.accounts.storage import accounts_root

    root = accounts_root()
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def _account_summary(skill_ids: Optional[list[str]] = None) -> dict:
    """读取当前 data/accounts/ 下各分析师账户，生成回测期末摘要。

    用于在回测完成时随 manifest/progress 持久化，方便后续按区间对比
    「每位分析师的期末资产/收益率/已实现盈亏/成交拒绝/最大回撤/胜率」。
    账户缺失或损坏时跳过对应分析师，绝不抛异常。
    """
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "src"))
    try:
        from stock_review_crew.accounts import engine as acct_engine
        from stock_review_crew.accounts import storage as acct_storage
    except Exception:  # noqa: BLE001 - 导入失败不阻断回测收尾
        return {}
    try:
        from stock_review_crew.skills import list_skills
        _skill_names = {str(s.get("id") or ""): str(s.get("name") or "") for s in list_skills()}
    except Exception:  # noqa: BLE001 - 名称映射失败仅影响展示
        _skill_names = {}


    root = acct_storage.accounts_root()
    if not root.is_dir():
        return {}
    out: dict[str, dict] = {}
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if skill_ids:
        sids_set = set(skill_ids)
        dirs = [d for d in dirs if d.name in sids_set]
    for d in sorted(dirs):
        sid = str(d.name)
        acct = acct_storage.load_account(sid)
        if not isinstance(acct, dict):
            continue
        trades = acct_storage.load_trades(sid) or []
        snaps = acct_storage.load_snapshots(sid) or []
        acct["trades"] = trades
        acct["snapshots"] = snaps
        try:
            initial = float(acct.get("initial_cash") or acct_storage.load_initial_cash())
        except (TypeError, ValueError):
            initial = 100_000.0
        total_assets = acct.get("cash")
        if isinstance(snaps, list) and snaps:
            try:
                total_assets = float(snaps[-1].get("total_assets") or total_assets)
            except (TypeError, ValueError):
                pass
        try:
            total_assets = float(total_assets or 0.0)
        except (TypeError, ValueError):
            total_assets = 0.0
        realized_pnl = 0.0
        try:
            realized_pnl = float(acct.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            realized_pnl = 0.0
        filled = sum(1 for t in trades if t.get("status") == "成交")
        rejected = sum(1 for t in trades if t.get("status") == "拒绝")
        max_drawdown = 0.0
        if isinstance(snaps, list):
            peak = -1e18
            for s in snaps:
                try:
                    v = float(s.get("total_assets") or 0.0)
                except (TypeError, ValueError):
                    continue
                peak = max(peak, v)
                if peak > 0:
                    max_drawdown = max(max_drawdown, 1.0 - v / peak)
        stats = {}
        try:
            stats = acct_engine.trade_stats(acct)
        except Exception:  # noqa: BLE001 - 统计失败不影响摘要
            stats = {}
        out[sid] = {
            "skill_id": sid,
            "name": _skill_names.get(sid) or str(acct.get("skill_id") or sid),
            "initial_cash": round(initial, 2),
            "total_assets": round(total_assets, 2),
            "return_rate": round(total_assets / initial - 1.0, 6) if initial else 0.0,
            "realized_pnl": round(realized_pnl, 2),
            "filled": filled,
            "rejected": rejected,
            "max_drawdown": round(max_drawdown, 6),
            "win_rate": stats.get("win_rate"),
            "profit_loss_ratio": stats.get("profit_loss_ratio"),
            "avg_holding_days": stats.get("avg_holding_days"),
        }
    return out


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
    # 快速模式（lite）：跳过主持人/报告 LLM，仅数据 + 分析师判断
    lite = bool(params.get("lite"))
    # 可多选分析师：未传/空列表 = 全部交易型分析师
    analysts = params.get("analysts")
    if analysts is not None:
        if not isinstance(analysts, list) or not all(isinstance(x, str) for x in analysts):
            raise HTTPException(status_code=400, detail="analysts 必须为字符串数组")
        from stock_review_crew.skills import list_skills
        valid = {x.get("id") for x in list_skills()}
        bad = [x for x in analysts if x not in valid]
        if bad:
            raise HTTPException(status_code=400, detail=f"不存在的分析师 id：{', '.join(bad)}")
        analysts = [x for x in analysts if x]
    days = trading_dates(start, end)
    if not days:
        raise HTTPException(status_code=400, detail=f"所选范围（{start} 至 {end}）内无交易日")

    with _lock:
        for bt in BACKTESTS.values():
            if bt.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail="已有回测在进行中，请先等待其完成或被取消")

    # 方案 A：回测前强制重置模拟账户（每次从 10 万初始资金重跑所选区间，不叠加历史持仓）
    _reset_accounts()

    clean = {
        "start": start,
        "end": end,
        "force": force,
        "lite": lite,
        "max_rounds": max_rounds,
        "mode": mode,
        "analysts": analysts,
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
        # 可能已清出内存（重启丢失）：尝试从磁盘重建，支持取消重启前遗留的 running 记录
        try:
            bt = _load_backtest_from_disk(bt_id)
        except Exception:
            bt = None
    if bt is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    if bt.get("status") in ("done", "error", "cancelled"):
        raise HTTPException(status_code=409, detail=f"回测已结束（{bt['status']}），无法取消")
    with _lock:
        bt["_cancel"] = True
        # 取消后当日仍正常跑完，由 _run_loop 在日界把状态置为 cancelled（保持“当日完成后停”语义）
        _persist(bt)
    return {"ok": True}


def _load_backtest_from_disk(bt_id: str) -> Optional[dict]:
    """从落盘（manifest.json + progress.json）重建回测对象，供断点续跑。

    磁盘进度是「公开字段」persist 的（无 _ 内部字段），重建时补齐
    id/start/end/params/created_at/finished_at/status/days/current/days_done/
    days_total/total_elapsed_ms/_cancel=False 等字段。磁盘无 → 返回 None。
    """
    p = _run_dir(bt_id) / "progress.json"
    mf = _run_dir(bt_id) / "manifest.json"
    if not (p.exists() or mf.exists()):
        return None
    data = {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 磁盘损坏容错
        pass
    if not isinstance(data, dict) or not data.get("days"):
        data = {}
    params = data.get("params") or {}
    m = {}
    try:
        m = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    except Exception:  # noqa: BLE001
        m = {}
    if not isinstance(m, dict):
        m = {}
    bt = {
        "_cancel": False,
        "id": data.get("id") or m.get("id") or bt_id,
        "start": data.get("start") or m.get("start") or params.get("start"),
        "end": data.get("end") or m.get("end") or params.get("end"),
        "params": params,
        "created_at": data.get("created_at") or m.get("created_at"),
        "finished_at": data.get("finished_at") or m.get("finished_at"),
        "status": str(data.get("status") or m.get("status") or "pending"),
        "days": data.get("days") or [],
        "current": data.get("current"),
        "days_done": int(data.get("days_done") or 0),
        "days_total": int(data.get("days_total") or len(data.get("days") or [])),
        "total_elapsed_ms": int(data.get("total_elapsed_ms") or 0),
        "accounts": data.get("accounts"),
    }
    return bt


def resume_backtest(bt_id: str) -> dict:
    """断点续跑：从首个非 done 的日续跑，保留已 done 天的产物与账户状态。

    语义（requirements-backtest-resume H1）：
    - 单 running 互斥：已有 running/queued 回测 → 409；
    - status=done → 409「已完成」；
    - 找首个 `status != done` 的日索引 start_idx（全 done → 409）；
    - 重置 days[start_idx:] 为初始 pending（清 error/attempts_log/settle/review/
      stages/elapsed_ms/attempt/attempts）；
    - status=running、finished_at=None、_cancel=False、current={首日, queued}、
      days_done=done 数；落盘；
    - **不重置模拟账户**（续跑延续已 done 天的账户状态，绝不调用 _reset_accounts）；
    - 启动 daemon 线程 _run_loop(bt, start_idx)。
    """
    with _lock:
        # 单 running 互斥：任一 running/queued 回测存在即 409（含被续跑的自身之外）
        for other in BACKTESTS.values():
            if other.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail="已有回测运行中")
        bt = BACKTESTS.get(bt_id)
    if bt is None:
        # 内存无 → 从磁盘重建（重启后断点续跑）
        bt = _load_backtest_from_disk(bt_id)
    if bt is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    if bt.get("status") == "done":
        raise HTTPException(status_code=409, detail="回测已完成，无需续跑")

    days = bt.get("days") or []
    start_idx = next(
        (i for i, d in enumerate(days) if d.get("status") != "done"),
        None,
    )
    if start_idx is None or not days:
        raise HTTPException(status_code=409, detail="回测已完成，无需续跑")

    # 重置 days[start_idx:] 为初始 pending 态（清产物失败/未完成残留）
    for i in range(start_idx, len(days)):
        day = days[i]
        day["status"] = "pending"
        day["error"] = None
        day["attempt"] = 0
        day["attempts"] = 0
        day["settle"] = None
        day["review"] = None
        day["stages"] = []
        day["elapsed_ms"] = None
        day.pop("attempts_log", None)

    bt["status"] = "running"
    bt["finished_at"] = None
    bt["_cancel"] = False
    bt["days_done"] = sum(1 for d in days if d["status"] == "done")
    bt["days_total"] = len(days)
    bt["total_elapsed_ms"] = sum(
        int(d.get("elapsed_ms") or 0) for d in days if d.get("status") == "done"
    )
    bt["current"] = {"date": days[start_idx]["date"], "stage": "queued"}
    with _lock:
        BACKTESTS[bt_id] = bt
    _persist(bt)
    try:
        threading.Thread(target=_run_loop, args=(bt, start_idx), daemon=True).start()
    except Exception as exc:  # noqa: BLE001 - 启动失败清理 registry
        with _lock:
            BACKTESTS.pop(bt_id, None)
        raise HTTPException(status_code=500, detail=f"续跑启动失败：{exc}") from exc
    return {
        "ok": True,
        "backtest_id": bt["id"],
        "resume_from": days[start_idx]["date"],
    }


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
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        else:
            # 文件重建态：剔除内部实时计时字段 node_started_at（旧进程 monotonic 无意义，
            # 不对外泄漏；文件态无实时 elapsed_ms，前端显示「—」）
            cur = data.get("current") if isinstance(data, dict) else None
            if isinstance(cur, dict):
                cur.pop("node_started_at", None)
            return data
    raise HTTPException(status_code=404, detail="回测不存在")


def _apply_live_elapsed(public: dict) -> None:
    """对内存态回测的公开对象实时计算 current.elapsed_ms（毫秒）。

    仅当 current 存在且含内部计时字段 node_started_at 时计算并剔除此内部字段
    （不污染内存态）。文件重建态（重启后查历史）无实时值 → 原样返回，elapsed_ms 缺失。
    """
    cur = public.get("current")
    if not isinstance(cur, dict):
        return
    nsa = cur.get("node_started_at")
    if isinstance(nsa, (int, float)):
        cur["elapsed_ms"] = int((time.monotonic() - nsa) * 1000) if nsa >= 0 else 0
    cur.pop("node_started_at", None)


def _public(bt: dict) -> dict:
    """内存态回测 → 公开字段浅拷贝；current 独立拷贝并附实时 elapsed_ms。"""
    public = {k: bt[k] for k in bt if not k.startswith("_")}
    cur = public.get("current")
    if isinstance(cur, dict):
        # 独立拷贝，确保内部计时字段/实时 elapsed 只加到返回对象，不污染内存态
        public["current"] = dict(cur)
    _apply_live_elapsed(public)
    return public


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
