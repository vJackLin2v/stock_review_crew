# -*- coding: utf-8 -*-
"""R14 A3 复盘批跑脚本（子命令 run / validate）。

契约（docs/requirements-r14.md 第五节/第六节/第七节/第八节，唯一真源）：
- run：
  `python work/review_batch.py run [--start D] [--end D] [--dates d1,d2]
      [--force] [--max-rounds N]`
  - 日期生成：工作日（周末跳过，节假日表不内置；--dates 显式指定优先）；
  - 对每个日期 POST /api/reviews {date, mode:"close", max_rounds}
    → 轮询 /api/jobs/{id} → done 后校验产物
    （report.json + plan.json 存在、meta.degraded 无 LLM 降级标注）
    → 不合格删记录重跑，单日 ≤3 次；单日失败不中断，记 error 继续；
  - 断点续跑：状态存 work/review_batch_state.json（已 done 日期跳过，--force 除外）；
  - 服务未启动（127.0.0.1:8501）→ 中文提示并退出码 2；
- validate：
  `python work/review_batch.py validate [--start D] [--end D]`
  - 对 data/reviews/{date}/ 校验：report.json 存在且 JSON 有效、analyses 完整
    （数量 >= 分析师数，读 skills 目录排除 host/review_assistant）、
    meta.degraded 无 LLM 降级、plan.json 存在且可加载；
  - 输出每日期 {date, ok, reasons[]} + 需重跑清单（控制台 + .tmp/review_validate.json）；
  - 不依赖服务，不写仓库 data/（校验输出仅写 .tmp/）。

退出码：run 0=全部成功/跳过，1=存在失败日期，2=服务不可用/参数错误；
validate 0=校验完成（含判定为需重跑的日期，属正常结果）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

BASE_URL = "http://127.0.0.1:8501"
REPO = Path(__file__).resolve().parent.parent
STATE_FILE = REPO / "work" / "review_batch_state.json"
VALIDATE_OUT = REPO / ".tmp" / "review_validate.json"
REVIEWS_DIR = REPO / "data" / "reviews"
SKILLS_DIR = REPO / "skills"

# 分析师目录排除 host/review_assistant（数据字典 plans[].skill_id 边界）
EXCLUDED_SKILLS = ("host", "review_assistant")
# meta.degraded 中命中即判 LLM 降级的关键词（graph.py 降级标注口径）
LLM_DEGRADED_MARKERS = ("LLM 不可用", "LLM 失败", "降级为规则")

MAX_ATTEMPTS = 3          # 单日最多尝试次数（含首次）
POLL_INTERVAL = 5         # 轮询间隔（秒）
JOB_TIMEOUT = 1200        # 单任务轮询超时（秒）
REQUEST_TIMEOUT = 30      # 单次 HTTP 请求超时（秒）


def _session() -> requests.Session:
    """本地服务会话：不读代理环境变量（PLAYBOOK 沙箱假代理会拦 localhost）。"""
    s = requests.Session()
    s.trust_env = False
    return s


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _date_arg(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"无效日期（应为 YYYY-MM-DD）：{value}")


def trading_dates(start: date, end: date) -> list[date]:
    """工作日序列（周一至周五；节假日表不内置，--dates 可显式指定）。"""
    days: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def parse_dates_arg(raw: Optional[str]) -> list[date]:
    """--dates d1,d2 解析：去重、保序、校验格式。"""
    if not raw:
        return []
    out: list[date] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        d = _date_arg(token)
        if d not in out:
            out.append(d)
    return out


# ────────────────────────── 状态（断点续跑） ──────────────────────────

def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("dates"), dict):
                return data
    except Exception:  # noqa: BLE001 - 状态损坏按空状态重来
        pass
    return {"updated_at": "", "dates": {}}


def save_state(state: dict) -> None:
    state["updated_at"] = _now_iso()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def mark_done(state: dict, d: str, attempts: int) -> None:
    state["dates"][d] = {"status": "done", "attempts": attempts,
                         "last_error": None, "updated_at": _now_iso()}
    save_state(state)


def mark_error(state: dict, d: str, attempts: int, error: str) -> None:
    state["dates"][d] = {"status": "error", "attempts": attempts,
                         "last_error": error, "updated_at": _now_iso()}
    save_state(state)


# ────────────────────────── 产物校验（共用核心） ──────────────────────────

def analyst_ids() -> list[str]:
    """分析师 id 列表：skills 目录（排除 host/review_assistant），升序。"""
    ids: list[str] = []
    if SKILLS_DIR.is_dir():
        for p in sorted(SKILLS_DIR.iterdir()):
            if p.is_dir() and p.name not in EXCLUDED_SKILLS \
                    and not p.name.startswith("."):
                ids.append(p.name)
    return ids


def _analyses_count(report: dict) -> int:
    """analyses 完整计数：list 按条数（排除 host/review_assistant），dict 按键数。"""
    a = report.get("analyses")
    if isinstance(a, list):
        return sum(
            1 for x in a
            if not (isinstance(x, dict)
                    and str(x.get("skill_id") or "") in EXCLUDED_SKILLS))
    if isinstance(a, dict):
        return sum(1 for k in a if k not in EXCLUDED_SKILLS)
    return 0


def _llm_degraded_notes(meta: dict) -> list[str]:
    out: list[str] = []
    for note in meta.get("degraded") or []:
        note = str(note)
        if any(m in note for m in LLM_DEGRADED_MARKERS):
            out.append(note)
    return out


def validate_date(date_str: str) -> tuple[bool, list[str]]:
    """单日全量校验（validate 子命令口径）→ (ok, reasons[])，绝不抛异常。"""
    reasons: list[str] = []
    day_dir = REVIEWS_DIR / date_str
    if not day_dir.is_dir():
        return False, ["复盘目录缺失"]

    # 1) report.json 存在且 JSON 有效
    report: Optional[dict] = None
    report_p = day_dir / "report.json"
    if not report_p.exists():
        reasons.append("报告缺失（report.json 不存在）")
    else:
        try:
            data = json.loads(report_p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                report = data
            else:
                reasons.append("报告损坏（report.json 非 JSON 对象）")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"报告损坏（{exc}）")

    # 2) analyses 完整（数量 >= 分析师数）
    if report is not None:
        need = len(analyst_ids())
        got = _analyses_count(report)
        if need > 0 and got < need:
            reasons.append(f"分析不完整（{got}/{need}）")
        elif got == 0:
            reasons.append("分析缺失（analyses 为空）")

    # 3) meta.degraded 无 LLM 降级
    meta_p = day_dir / "meta.json"
    if not meta_p.exists():
        reasons.append("meta 缺失（meta.json 不存在）")
    else:
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            llm_notes = _llm_degraded_notes(meta) if isinstance(meta, dict) else []
        except Exception as exc:  # noqa: BLE001
            llm_notes = []
            reasons.append(f"meta 损坏（{exc}）")
        if llm_notes:
            reasons.append("LLM 降级：" + "；".join(llm_notes)[:200])

    # 4) plan.json 存在且可加载（计划为空也判需重跑，§七.6/§八）
    plan_p = day_dir / "plan.json"
    if not plan_p.exists():
        reasons.append("计划缺失（plan.json 不存在）")
    else:
        try:
            plan = json.loads(plan_p.read_text(encoding="utf-8"))
            if not isinstance(plan, dict):
                reasons.append("计划损坏（plan.json 非 JSON 对象）")
            elif not isinstance(plan.get("plans"), list):
                reasons.append("计划损坏（plan.json 缺少 plans 数组）")
            elif len(plan["plans"]) == 0:
                reasons.append("计划为空（无交易计划，需重跑）")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"计划损坏（{exc}）")

    return len(reasons) == 0, reasons


def validate_run_product(date_str: str) -> tuple[bool, list[str]]:
    """run 子命令产物校验（契约子集）：report.json+plan.json 存在、
    meta.degraded 无「LLM 不可用/失败」「降级为规则引擎」。"""
    reasons: list[str] = []
    day_dir = REVIEWS_DIR / date_str
    if not day_dir.is_dir():
        return False, ["复盘目录缺失"]
    if not (day_dir / "report.json").exists():
        reasons.append("报告缺失（report.json 不存在）")
    if not (day_dir / "plan.json").exists():
        reasons.append("计划缺失（plan.json 不存在）")
    meta_p = day_dir / "meta.json"
    if not meta_p.exists():
        reasons.append("meta 缺失（meta.json 不存在）")
    else:
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            llm_notes = _llm_degraded_notes(meta) if isinstance(meta, dict) else []
        except Exception as exc:  # noqa: BLE001
            llm_notes = []
            reasons.append(f"meta 损坏（{exc}）")
        if llm_notes:
            reasons.append("LLM 降级：" + "；".join(llm_notes)[:200])
    return len(reasons) == 0, reasons


# ────────────────────────── validate 子命令 ──────────────────────────

def cmd_validate(start: Optional[date], end: Optional[date]) -> int:
    dates: list[date] = []
    if REVIEWS_DIR.is_dir():
        for p in sorted(REVIEWS_DIR.iterdir()):
            if not p.is_dir():
                continue
            try:
                d = datetime.strptime(p.name, "%Y-%m-%d").date()
            except ValueError:
                continue  # 非日期目录（如历史残留）跳过
            if start and d < start:
                continue
            if end and d > end:
                continue
            dates.append(d)

    results: list[dict] = []
    for d in dates:
        date_str = d.isoformat()
        ok, reasons = validate_date(date_str)
        results.append({"date": date_str, "ok": ok, "reasons": reasons})
        if ok:
            print(f"{date_str} | 可用")
        else:
            print(f"{date_str} | 不可用 | {'；'.join(reasons)}")

    rerun = [r["date"] for r in results if not r["ok"]]
    if rerun:
        print(f"\n需重跑（{len(rerun)} 天）：{'、'.join(rerun)}")
    else:
        print("\n需重跑：无")
    print(f"校验完成：共 {len(results)} 天，可用 {len(results) - len(rerun)} 天，"
          f"需重跑 {len(rerun)} 天。仅供参考，不构成投资建议。")

    VALIDATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    VALIDATE_OUT.write_text(json.dumps({
        "generated_at": _now_iso(),
        "range": {"start": start.isoformat() if start else None,
                  "end": end.isoformat() if end else None},
        "dates": results,
        "rerun": rerun,
        "counts": {"checked": len(results), "ok": len(results) - len(rerun),
                   "rerun": len(rerun)},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"明细已写入：{VALIDATE_OUT}")
    return 0


# ────────────────────────── run 子命令 ──────────────────────────

def check_server() -> bool:
    """服务健康检查（run 依赖 127.0.0.1:8501）。"""
    try:
        r = _session().get(f"{BASE_URL}/api/health", timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def fetch_status(sess: requests.Session, d: str) -> Optional[dict]:
    """GET /api/reviews/status?date= → {has_today_review, pending_job_id, ...}。"""
    try:
        r = sess.get(f"{BASE_URL}/api/reviews/status", params={"date": d},
                     timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def poll_job(sess: requests.Session, job_id: str) -> tuple[str, str]:
    """轮询 /api/jobs/{id} 至终态 → (status, note)；status ∈ done/error/timeout。"""
    deadline = time.monotonic() + JOB_TIMEOUT
    while time.monotonic() < deadline:
        try:
            r = sess.get(f"{BASE_URL}/api/jobs/{job_id}", timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                job = r.json()
                status = job.get("status")
                if status == "done":
                    return "done", ""
                if status == "error":
                    return "error", str(job.get("error") or job.get("message")
                                        or "任务失败")
        except requests.RequestException:
            pass
        time.sleep(POLL_INTERVAL)
    return "timeout", f"轮询超时（>{JOB_TIMEOUT}s，任务 {job_id}）"


def delete_review(sess: requests.Session, d: str) -> None:
    """删除单日复盘记录（重跑前置；404/失败不致命，POST 时再校验）。"""
    try:
        sess.delete(f"{BASE_URL}/api/reviews/{d}", timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        pass


def attempt_once(sess: requests.Session, d: str, force: bool,
                 max_rounds: int) -> tuple[str, str]:
    """单次尝试 → (outcome, note)；outcome ∈ done / retry / blocked。"""
    # 1) 消化进行中任务（断点续跑恢复：上次崩溃遗留的 job）
    st = fetch_status(sess, d)
    pending = (st or {}).get("pending_job_id")
    if pending:
        status, note = poll_job(sess, pending)
        if status == "done":
            ok, reasons = validate_run_product(d)
            if ok:
                return "done", ""
            delete_review(sess, d)
            return "retry", "遗留任务产物不合格：" + "；".join(reasons)
        return "retry", note or "遗留任务未完成"

    # 2) 已有记录：非 force 直接判可用则跳过，不合格需 --force
    has_review = bool((st or {}).get("has_today_review", False))
    if has_review and not force:
        ok, reasons = validate_run_product(d)
        if ok:
            return "done", ""
        return "blocked", "已有复盘记录但产物校验不合格（需 --force 重跑）：" \
            + "；".join(reasons)
    if has_review and force:
        delete_review(sess, d)

    # 3) 提交任务
    try:
        r = sess.post(f"{BASE_URL}/api/reviews",
                      json={"date": d, "mode": "close",
                            "max_rounds": max(1, min(max_rounds, 3)),
                            # 2026-08-14：脚本批跑不上报 Langfuse（trace=false），
                            # 关闭 LLM 级 Langfuse 回调——同步 OTLP 导出会把单次 LLM
                            # 调用从 ~15s 拖到 180s+（PLAYBOOK 2026-08-10），批跑会慢 10 倍。
                            "trace": False},
                      timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        return "retry", f"提交任务失败（服务不可达）：{exc}"
    if r.status_code == 409:
        return "retry", "409 该日期已有复盘记录/进行中任务（并发冲突）"
    if r.status_code != 200:
        return "retry", f"POST /api/reviews 失败（HTTP {r.status_code}）"
    try:
        job_id = r.json().get("job_id")
    except (ValueError, AttributeError):
        return "retry", "提交成功但响应缺少 job_id"

    # 4) 轮询至终态，done 后校验产物；不合格删记录重跑
    status, note = poll_job(sess, job_id)
    if status != "done":
        return "retry", note or "任务失败"
    ok, reasons = validate_run_product(d)
    if ok:
        return "done", ""
    delete_review(sess, d)
    return "retry", "产物校验不合格：" + "；".join(reasons)


def run_one_date(sess: requests.Session, d: date, force: bool,
                 max_rounds: int, state: dict) -> bool:
    """单日批跑：成功/跳过返回 True，失败（≤3 次后）返回 False，不中断批次。"""
    date_str = d.isoformat()
    last_note = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        outcome, note = attempt_once(sess, date_str, force, max_rounds)
        last_note = note
        if outcome == "done":
            mark_done(state, date_str, attempt)
            print(f"[run] {date_str} 成功（第 {attempt} 次尝试）")
            return True
        if outcome == "blocked":
            mark_error(state, date_str, attempt, note)
            print(f"[run] {date_str} 跳过：{note}")
            return False
        print(f"[run] {date_str} 第 {attempt} 次失败：{note}，稍后重试…")
        time.sleep(3)
    mark_error(state, date_str, MAX_ATTEMPTS, last_note)
    print(f"[run] {date_str} 失败（重试 {MAX_ATTEMPTS} 次后仍失败）：{last_note}")
    return False


def settle_prev_day(prev_date: str, cur_date: str) -> None:
    """逐日交错（R14）：用 cur_date 完整行情撮合 prev_date 的交易计划（增量撮合）。

    写 data/accounts/，让 cur_date 复盘的分析师 get_positions 能读到前一日持仓
    （含 T+1 冻结/现金变化）。失败不阻断复盘，只打印告警。
    """
    try:
        sys.path.insert(0, str(REPO / "src"))
        from stock_review_crew.accounts.replay import settle_plans

        r = settle_plans(prev_date, cur_date)
        print(f"[settle] {prev_date} 计划 → {cur_date} 行情撮合："
              f"成交 {r.get('filled')} / 拒绝 {r.get('rejected')} / "
              f"行情缺失 {len(r.get('quotes_missing') or [])}")
    except Exception as exc:  # noqa: BLE001
        print(f"[settle] {prev_date}→{cur_date} 撮合失败（不阻断复盘）：{exc}")


def cmd_run(start: Optional[date], end: Optional[date],
            dates_arg: Optional[str], force: bool, max_rounds: int) -> int:
    if not check_server():
        print("未检测到复盘服务（http://127.0.0.1:8501）。")
        print("请先启动守护脚本 scripts/start_backend.ps1，再运行 run 子命令。")
        return 2

    explicit = parse_dates_arg(dates_arg)
    if explicit:
        days = explicit
        print(f"[run] 显式指定 {len(days)} 个日期（--dates）")
    else:
        days = trading_dates(start, end)
        print(f"[run] 工作日序列 {len(days)} 天：{days[0]} → {days[-1]}")

    state = load_state()
    sess = _session()
    failed: list[str] = []
    prev_date: Optional[str] = None
    for d in days:
        date_str = d.isoformat()
        prev = state["dates"].get(date_str)
        if prev and prev.get("status") == "done" and not force:
            print(f"[run] {date_str} 已 done（断点续跑跳过，--force 可重跑）")
            prev_date = date_str
            continue
        # 逐日交错（2026-08-14）：先撮合前一日计划（用当天行情），再复盘当天，
        # 否则当天分析师 get_positions 读不到前一日持仓（会误判「无持仓」）。
        if prev_date is not None:
            settle_prev_day(prev_date, date_str)
        if not run_one_date(sess, d, force, max_rounds, state):
            failed.append(date_str)
        prev_date = date_str

    if failed:
        print(f"\n[run] 完成：{len(days) - len(failed)} 成功/跳过，"
              f"{len(failed)} 失败：{'、'.join(failed)}")
        print("单日失败不中断批次；重跑会按状态文件仅补失败日期（--force 全覆盖）。")
        return 1
    print(f"\n[run] 全部完成：{len(days)} 天。仅供参考，不构成投资建议。")
    return 0


# ────────────────────────── 入口 ──────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="R14 复盘批跑：run（提交+轮询+产物校验+重试）/ validate（存量校验）")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_run = sub.add_parser("run", help="逐交易日批跑复盘（依赖 127.0.0.1:8501 服务）")
    p_run.add_argument("--start", type=_date_arg, default=None,
                       help="起始日期 YYYY-MM-DD（含）")
    p_run.add_argument("--end", type=_date_arg, default=None,
                       help="结束日期 YYYY-MM-DD（含）")
    p_run.add_argument("--dates", default=None,
                       help="显式日期列表（逗号分隔，如 2026-07-01,2026-07-02；"
                            "优先于 --start/--end）")
    p_run.add_argument("--force", action="store_true",
                       help="强制重跑：已 done 日期也重跑，并先删除已有复盘记录")
    p_run.add_argument("--max-rounds", type=int, default=3,
                       help="复盘辩论最大轮数（1-3，默认 3）")

    p_val = sub.add_parser("validate", help="校验已落盘复盘产物（不依赖服务）")
    p_val.add_argument("--start", type=_date_arg, default=None,
                       help="起始日期 YYYY-MM-DD（含，默认不限）")
    p_val.add_argument("--end", type=_date_arg, default=None,
                       help="结束日期 YYYY-MM-DD（含，默认不限）")

    args = parser.parse_args(argv)

    if args.subcommand == "validate":
        return cmd_validate(args.start, args.end)

    # run：--dates 优先；否则必须同时给出 --start/--end
    if not args.dates and (args.start is None or args.end is None):
        print("参数错误：run 需 --dates，或同时提供 --start 与 --end。")
        return 2
    if args.dates and not parse_dates_arg(args.dates):
        print("参数错误：--dates 无法解析出有效日期。")
        return 2
    return cmd_run(args.start, args.end, args.dates, args.force,
                   args.max_rounds)


if __name__ == "__main__":
    sys.exit(main())
