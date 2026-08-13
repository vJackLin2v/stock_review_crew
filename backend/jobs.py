"""复盘后台任务模块：JOBS 状态 + 任务执行（无状态副作用，不 import backend.main）。

从 backend/main.py 拆分（2026-08-13，纯重构不改行为）：
- 常量与状态：JOB_TTL_SECONDS / MAX_JOBS / _lock / JOBS / _MARKET_EXTRA_CACHE /
  _MARKET_EXTRA_CACHE_TTL / MODES / MODE_LABELS / INTRADAY_MODES / MODE_WINDOWS；
- 纯函数：_now / _nearest_mode / _is_trading_day / _resolve_latest_date /
  _pending_job_id / _window_error / _validate_date / _update / _prune_jobs /
  _progress_cb / _make_summary / _quality_structured / _norm_key /
  _context_item_text / _yesterday_report_text / _snapshot_sources / _market_extra_call；
- 任务执行：_run_review_job(job_id, date, mode, max_rounds, trace=True)。

测试兼容 seam（_host）：测试通过 monkeypatch.setattr(backend.main, name, ...) 替换的
协作名（_now / JOB_TTL_SECONDS / MAX_JOBS / _stock_names_via_tx_batch /
_NAME_CACHE_TTL_SECONDS），本模块内部按模块全局名解析时必须经 _host 实时读取宿主
模块（backend.main）当前绑定，补丁才生效；经 sys.modules 取宿主模块，避免模块级
循环导入。其余名字一律模块内直接引用，不绕行。
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_review_crew import config  # noqa: E402
from stock_review_crew import graph  # noqa: E402
from stock_review_crew.prompts import DISCLAIMER, MODE_INFO  # noqa: E402
from stock_review_crew.storage import reviews  # noqa: E402

logger = logging.getLogger("stock_review_crew.backend")


def _host(name: str) -> Any:
    """实时读取宿主模块（backend.main）当前绑定。

    测试用 monkeypatch.setattr(backend.main, name, ...) 注入假值后，本模块内部
    按模块全局名解析该名字的函数必须看到补丁；通过 sys.modules 在调用时取
    backend.main 的属性（宿主先于请求加载，运行时必然已初始化），不产生循环导入。
    """
    return getattr(sys.modules["backend.main"], name)


# ═══════════════════════════════════════════════════════════════
# 常量：模式 / 窗口 / JOBS TTL
# ═══════════════════════════════════════════════════════════════

MODES = list(MODE_INFO.keys())
MODE_LABELS = {m: MODE_INFO[m]["label"] for m in MODES}
INTRADAY_MODES = ("auction", "intraday_am", "noon", "intraday_pm")

# 模式窗口（分钟制 [start, end)，与 frontend/src/modes.js WINDOW 严格一致）
MODE_WINDOWS = {
    "pre_market": (0, 9 * 60 + 15),
    "auction": (9 * 60 + 15, 9 * 60 + 25),
    "intraday_am": (9 * 60 + 30, 11 * 60 + 30),
    "noon": (11 * 60 + 30, 13 * 60),
    "intraday_pm": (13 * 60, 15 * 60),
    "close": (15 * 60, 24 * 60),
}

JOB_TTL_SECONDS = 3600  # 终态任务（done/error）超过 1 小时移除
MAX_JOBS = 50  # JOBS 保留的终态任务上限（保留最近 N 条，进行中不删）

_lock = threading.Lock()
JOBS: dict[str, dict[str, Any]] = {}
# 2026-08-06：R10 行情接口 60s 成功缓存（防首页并发重复取数/打上游）
_MARKET_EXTRA_CACHE: dict[str, tuple[float, Any]] = {}
_MARKET_EXTRA_CACHE_TTL = 60.0


def _now() -> datetime:
    """当前时间（测试可 monkeypatch 固定）。"""
    return datetime.now()


def _nearest_mode(minutes: int) -> str:
    """当前时间点最合适的模式（与前端 currentMode 规则一致）。"""
    if minutes < 9 * 60 + 15:
        return "pre_market"
    if minutes < 9 * 60 + 30:
        return "auction"
    if minutes < 11 * 60 + 30:
        return "intraday_am"
    if minutes < 13 * 60:
        return "noon"
    if minutes < 15 * 60:
        return "intraday_pm"
    return "close"


def _is_trading_day(day: datetime) -> bool:
    """交易日判定（对齐 frontend/src/modes.js isTradingDay）：周一至周五为交易日。

    可接受误差：不处理法定节假日调休（与前端契约同源，前端 2026-08-08 同款实现）。
    """
    return day.weekday() < 5


def _resolve_latest_date(date_str: str, now: datetime) -> str:
    """R11 R2 latest_date：date 当天 15:00 前 → 最近一个交易日（含跨周：周一 15:00 前 =
    上周五；非交易日 → 最近交易日）；15:00 后且 date 为当日交易日 → date 本身。

    其余情况（date 非当日/非交易日）取严格早于 date 的最近交易日。
    """
    d = datetime.strptime(date_str, "%Y-%m-%d")
    if now.hour * 60 + now.minute >= 15 * 60 and now.date() == d.date() and _is_trading_day(d):
        return date_str
    cursor = d - timedelta(days=1)
    for _ in range(30):  # 30 天内必有交易日，兜底返回原日期
        if _is_trading_day(cursor):
            return cursor.strftime("%Y-%m-%d")
        cursor -= timedelta(days=1)
    return date_str


def _pending_job_id(date: str) -> Optional[str]:
    """当天已提交且未完成的任务 id（JOBS 内存态查找，找不到为 None）。"""
    with _lock:
        for jid, job in JOBS.items():
            if job.get("_date") == date and job.get("status") in ("queued", "running"):
                return jid
    return None


def _window_error(date_str: str, mode: str, now: Optional[datetime] = None) -> Optional[str]:
    """模式窗口判定：返回中文错误提示；None = 放行（历史日期补做任意模式）。"""
    now = now or _host("_now")()
    if date_str != now.strftime("%Y-%m-%d"):
        return None  # 历史日期补做放行
    minutes = now.hour * 60 + now.minute
    if now.weekday() >= 5 and mode in INTRADAY_MODES:
        return (
            "今日为非交易日（周末），盘中数据源无当日数据，复盘将按降级链标注缺失；"
            "建议切换「收盘复盘」或「早盘前决策」，或选择历史日期补做"
        )
    start, end = MODE_WINDOWS[mode]
    if start <= minutes < end:
        return None
    label = MODE_LABELS[mode]
    # 竞价刚结束（09:25-09:30 缓冲段）与已结束（09:30 后）给定向建议（requirements §二.1 示例）
    if mode == "auction" and minutes >= 9 * 60 + 30:
        return "竞价数据已结束，建议切换上午盘中或午间复盘"
    if mode == "auction" and minutes >= 9 * 60 + 25:
        return "当前处于 09:25–09:30 竞价数据刚结束的缓冲段，建议切换「竞价复盘」或「上午盘中」"
    nearest = _nearest_mode(minutes)
    return f"当前时间不在「{label}」窗口内（{MODE_INFO[mode]['window']}），建议切换为「{MODE_LABELS[nearest]}」"


def _validate_date(value: Any) -> str:
    """校验 YYYY-MM-DD 且为真实日历日期；非法抛 400 中文。"""
    s = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise HTTPException(status_code=400, detail=f"日期格式必须为 YYYY-MM-DD，收到：{s or '（空）'}")
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"不是真实存在的日期：{s}")
    return s


# ═══════════════════════════════════════════════════════════════
# JOBS：TTL / 更新 / 进度回调
# ═══════════════════════════════════════════════════════════════

def _update(job_id: str, **kwargs: Any) -> None:
    with _lock:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)


def _prune_jobs(now: Optional[float] = None) -> None:
    """JOBS TTL 清理：终态超过 JOB_TTL_SECONDS 移除；超过 MAX_JOBS 淘汰最旧终态；进行中不删。"""
    if now is None:
        now = time.monotonic()
    ttl_seconds = _host("JOB_TTL_SECONDS")  # 测试可 monkeypatch backend.main.JOB_TTL_SECONDS
    max_jobs = _host("MAX_JOBS")  # 测试可 monkeypatch backend.main.MAX_JOBS
    with _lock:
        stale = [
            jid
            for jid, job in JOBS.items()
            if job.get("status") in ("done", "error")
            and job.get("_finished") is not None
            and now - job["_finished"] > ttl_seconds
        ]
        for jid in stale:
            JOBS.pop(jid, None)
        terminal = sorted(
            (
                (jid, job.get("_finished", 0.0))
                for jid, job in JOBS.items()
                if job.get("status") in ("done", "error")
            ),
            key=lambda item: item[1],
        )
        for jid, _ in terminal[: max(0, len(terminal) - max_jobs)]:
            JOBS.pop(jid, None)


def _progress_cb(job_id: str):
    """构造引擎进度回调：analysts n/5 解析为 analysts_done/total（R15/R2 统一阶段）。"""

    def progress(stage: str, pct: float, message: str) -> None:
        updates: dict[str, Any] = {"stage": stage, "pct": pct, "message": message}
        mt = re.search(r"(\d+)/(\d+)", str(message or ""))
        if mt and stage == "analysts":
            done, total = int(mt.group(1)), int(mt.group(2))
            updates["analysts_done"] = done
            updates["analysts_total"] = total
        _update(job_id, **updates)

    return progress


# ═══════════════════════════════════════════════════════════════
# 复盘摘要 / 质量核验 / 上下文注入
# ═══════════════════════════════════════════════════════════════

def _make_summary(report: str, mode: str, date: str) -> str:
    """摘要：取报告正文第一个非标题/非引用段落；兜底模板。"""
    for line in (report or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(">"):
            continue
        s = s.lstrip("-*• ").strip()
        if s and "不构成投资建议" not in s:
            return s[:100]
    return f"{MODE_LABELS.get(mode, mode)}（{date}）：复盘完成，详见报告正文"


def _quality_structured(snapshot: Any) -> dict:
    """Q4 数字核验用结构化数据：从引擎快照递归收集 {代码: {数值字段}} 实体表。

    引擎快照（blocks 嵌套：zt_pool.limit_up.stocks 等）不经拍平时 verify_numbers
    无法溯源报告中的股票代码（曾误判 238 条 mismatch）；此处递归提取所有含 6 位
    代码的对象为 ``{"stocks": {code: data}}`` 容器（quality._extract_entities 约定）。
    """
    entities: dict[str, dict[str, Any]] = {}

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            code: Optional[str] = None
            for key in ("code", "代码", "证券代码", "股票代码"):
                val = obj.get(key)
                if val is not None and re.fullmatch(r"\d{6}", str(val).strip()):
                    code = str(val).strip()
                    break
            if code:
                data: dict[str, Any] = {}
                for k, v in obj.items():
                    if _norm_key(k) in ("code", "代码", "证券代码", "股票代码"):
                        continue
                    if isinstance(v, (dict, list)):
                        continue
                    data[k] = v
                if code not in entities:
                    entities[code] = data
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    raw = snapshot if isinstance(snapshot, dict) else {}
    _walk(raw.get("data") if isinstance(raw.get("data"), dict) else raw)
    return {"stocks": entities}


def _norm_key(key: Any) -> str:
    """字段键规范化（小写去符号，用于排除代码键）。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(key).lower())


def _context_item_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    parts = []
    if item.get("mode_label"):
        parts.append(str(item["mode_label"]))
    if item.get("time"):
        parts.append(f"时间点 {item['time']}")
    if item.get("record_id"):
        parts.append(f"记录 {item['record_id']}")
    if item.get("summary"):
        parts.append(f"摘要：{item['summary']}")
    return "；".join(parts)


def _yesterday_report_text(item: Any) -> str:
    """昨日复盘注入文本：摘要 + 报告全文前 5000 字符（R11-5 修复）。

    此前只注入一行摘要，分析师无法找到本人昨日预判，只能写「未找到本人昨日预判/
    无昨日报告」；注入全文后 LLM 可真正对照验证。缺失/读取失败回退摘要文本。
    """
    if not isinstance(item, dict):
        return ""
    summary = _context_item_text(item)
    try:
        detail = reviews.get_review(item.get("date") or "", item.get("time") or "")
        report = (detail or {}).get("report") or ""
        if report.strip():
            return f"{summary}\n\n【昨日收盘复盘全文】\n{report[:5000]}"
    except Exception:  # noqa: BLE001 - 读取失败回退摘要
        pass
    return summary


def _snapshot_sources(engine_snapshot: Any) -> list[str]:
    """meta.sources[]：数据块 source 去重（含引擎整体 source）。"""
    raw = engine_snapshot if isinstance(engine_snapshot, dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    sources: list[str] = []
    if isinstance(data, dict):
        for blk in data.values():
            if isinstance(blk, dict) and blk.get("source"):
                s = str(blk["source"])
                if s and s not in sources:
                    sources.append(s)
    if raw.get("source"):
        s = str(raw["source"])
        if s and s not in sources:
            sources.append(s)
    return sources


# ═══════════════════════════════════════════════════════════════
# 复盘任务
# ═══════════════════════════════════════════════════════════════

def _run_review_job(job_id: str, date: str, mode: str, max_rounds: int, trace: bool = True) -> None:
    """后台任务线程（daemon）：上下文注入 → 引擎 → 落盘 → done；任何异常 → error 中文。"""
    try:
        # 初始阶段用契约阶段序列首项 fetch（§五：取数/资讯/趋势派/情绪派/主持人/辩论/报告/完成）
        _update(job_id, status="running", stage="fetch", pct=1, message="正在注入历史上下文并拉取行情…")
        ctx = reviews.context(date, mode)
        initial = graph.default_initial_state(
            date,
            mode=mode,
            max_rounds=max_rounds,
            yesterday_report=_yesterday_report_text(ctx.get("yesterday")),
            earlier_today="\n".join(
                _context_item_text(item) for item in (ctx.get("earlier_today") or [])
            ),
        )
        # 2026-08-10：进度钩子已改 ContextVar（每 job 独立上下文），多任务可并发执行
        # R14 A1：清空当日计划 registry，保证同日重跑整文件覆盖、无残留旧计划
        from stock_review_crew.accounts import plan as plan_store

        plan_store.reset(date)
        compiled = graph.build_graph(progress_callback=_progress_cb(job_id))
        # 2026-08-12：LLM 级 Langfuse 回调按 job 开关——前端页面复盘（trace=true）
        # 开启，面板可见全流程（LLM 调用/取数等子 span）；脚本批量（trace=false）关闭
        # （性能 + 不刷屏）。ContextVar 随分析师线程池 copy_context 传播，并发 job 互不干扰。
        with config.langfuse_callbacks_scope(trace):
            state = compiled.invoke(initial)
        report_dict = graph.build_result(state)

        # Q4（R10）：Langfuse 质量 trace + 确定性 rubric 评分（无凭据自动 no-op，绝不阻塞）
        # 2026-08-12：trace 开关——脚本批量复盘传 trace=false 跳过 Langfuse（页面默认 true）
        quality_summary: Optional[dict] = None
        if trace:
            try:
                from stock_review_crew import quality

                degraded_notes = list(state.get("degraded_notes") or [])
                trace_id = quality.trace_review(
                    "done",
                    {
                        "date": date,
                        "mode": mode,
                        "degraded": degraded_notes,
                        "report_head": (report_dict.get("final_report") or "")[:2000],
                    },
                    metadata={
                        "date": date,
                        "mode": mode,
                        "mode_label": MODE_LABELS.get(mode, mode),
                        "degraded": degraded_notes,
                    },
                )
                quality_summary = quality.score_review(
                    report_dict.get("final_report") or "",
                    _quality_structured(state.get("snapshot") or {}),
                    {
                        "sections": list(MODE_INFO.get(mode, {}).get("sections", [])),
                        "degraded": degraded_notes,
                    },
                    trace_id,
                )
            except Exception as exc:  # noqa: BLE001 - 质量评估失败不影响复盘交付
                logger.warning("复盘质量评估跳过（不影响结果）：%s", exc)

        now = datetime.now()
        meta = {
            "date": date,
            "mode": mode,
            "mode_label": MODE_LABELS.get(mode, mode),
            "time": now.strftime("%H%M%S"),
            "created_at": now.isoformat(timespec="seconds"),
            "degraded": list(state.get("degraded_notes") or []),
            "sources": _snapshot_sources(state.get("snapshot")),
            "disclaimer": DISCLAIMER,
            "summary": _make_summary(report_dict.get("final_report", ""), mode, date),
        }
        # R12：各分析师模拟账户快照（附加字段，报告 Markdown 正文不变）
        _accounts = state.get("analyst_accounts") or {}
        if _accounts:
            meta["accounts"] = _accounts
        from backend.snapshot import _to_frontend_snapshot  # noqa: E402 - 快照转换模块（叶子依赖）

        snapshot = _to_frontend_snapshot(state.get("snapshot") or {})
        record_id: Optional[str] = None
        try:
            record_id = reviews.save_review(meta, report_dict, snapshot)
        except Exception as exc:  # 磁盘写失败：记录错误，不阻塞任务完成
            logger.warning("复盘落盘失败（不阻塞返回）：%s", exc)
            meta.setdefault("degraded", []).append(f"复盘记录落盘失败：{exc}")
        # R14 A1：invoke 完成后单点落盘 plan.json（唯一真源）。
        # 注意：必须在 save_review **之后**执行——save_review 会归档旧目录
        # （2026-08-11 实测：finalize 在 save_review 前会把 plan.json 一起归档到
        # output/archive/，导致批跑校验 data/reviews/{date}/plan.json 缺失）。
        # finalize 失败只追加 degraded 标注，绝不阻塞 job done
        try:
            plan_store.finalize(date, mode)
        except Exception as exc:  # noqa: BLE001 - 计划落盘失败不阻塞任务完成
            logger.warning("交易计划落盘失败（不阻塞任务完成）：%s", exc)
            meta.setdefault("degraded", []).append(f"交易计划落盘失败：{exc}")
        _update(
            job_id,
            status="done",
            pct=100,
            message="复盘完成",
            result={
                "record_id": record_id,
                "meta": meta,
                "report": report_dict.get("final_report", ""),
                "snapshot": snapshot,
                "quality": quality_summary,
            },
            _finished=time.monotonic(),
        )
    except graph.DataValidationError as exc:
        text = str(exc)
        _update(job_id, status="error", message=text, error=text, _finished=time.monotonic())
    except Exception as exc:  # noqa: BLE001 - 任何异常都转为中文 error，绝不 500 崩线程
        logger.exception("复盘任务异常：job=%s", job_id)
        text = f"复盘失败：{exc}"
        _update(job_id, status="error", message=text, error=text, _finished=time.monotonic())


# ═══════════════════════════════════════════════════════════════
# R10（M2）：行情数据缓存调用（market_extra 契约函数，60s 成功缓存）
# ═══════════════════════════════════════════════════════════════

def _market_extra_call(func_name: str, unavailable: str, failed: str, *args: Any) -> Any:
    """惰性调用 M1 market_extra 契约函数（契约 §三，M1 并行开发中）。

    模块导入失败 / 函数缺失 → 503 中文「功能暂不可用」（M1 完成后自动可用）；
    其余异常 → 500 中文，绝不崩线程。返回结构由 M1 按数据字典 §二 冻结。
    2026-08-06 优化：成功结果 60s 内存缓存（首页并发重复请求不再重算/重打上游，
    解决前端 30s 超时降级；market-weather 首次仍可能慢，缓存后毫秒级）。
    """
    cache_key = f"{func_name}:{args}"
    now = time.monotonic()
    with _lock:
        hit = _MARKET_EXTRA_CACHE.get(cache_key)
        if hit is not None and now - hit[0] < _MARKET_EXTRA_CACHE_TTL:
            return hit[1]
    try:
        from stock_review_crew.tools import market_extra
    except Exception as exc:  # noqa: BLE001 - 模块未完成/导入失败 → 503
        logger.warning("market_extra 模块不可用（%s）：%s", func_name, exc)
        raise HTTPException(status_code=503, detail=unavailable) from exc
    fn = getattr(market_extra, func_name, None)
    if not callable(fn):
        logger.warning("market_extra.%s 未实现（M1 开发中）", func_name)
        raise HTTPException(status_code=503, detail=unavailable)
    try:
        result = fn(*args)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 任何异常 500 中文
        logger.exception("market_extra.%s 调用异常", func_name)
        raise HTTPException(status_code=500, detail=f"{failed}：{exc}") from exc
    with _lock:
        _MARKET_EXTRA_CACHE[cache_key] = (time.monotonic(), result)
        if len(_MARKET_EXTRA_CACHE) > 64:
            _MARKET_EXTRA_CACHE.clear()
    return result
