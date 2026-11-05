# -*- coding: utf-8 -*-
"""涨停复盘工作台 —— 手动触发脚本（C9/D14：默认当日，支持任意历史日期）。

用法：
  python work/zt_tracking.py run                # 当日（若非交易日自动标注跳过）
  python work/zt_tracking.py run --dates 2026-08-28,2026-08-27   # 补任意历史
  python work/zt_tracking.py run --start 2026-08-15 --end 2026-08-28  # 区间工作日
  python work/zt_tracking.py run --force        # 已落盘的日期重跑
  python work/zt_tracking.py analyze            # 只做分析（轮动池/核心股/信号/待填CSV）
  python work/zt_tracking.py report             # 输出最近一日分析结果（JSON 到 .tmp/zt_tracking_result.json）

流程：每日期 fetch→filter→tag→seats→cluster→落盘；全部日期完成后 update_pools（池生命周期）→
核心股/介入信号 → 待人工补充 CSV。绝不删除历史（落盘幂等覆盖为同结构更新）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from stock_review_crew.tools.zt_tracking import run_tracking_day  # noqa: E402
from stock_review_crew import zt_analysis as za  # noqa: E402
from stock_review_crew.storage import zt_tracking as zt_store  # noqa: E402

STATE_FILE = REPO / ".tmp" / "zt_tracking_state.json"
RESULT_FILE = REPO / ".tmp" / "zt_tracking_result.json"


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(st: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def trading_days(start: date, end: date) -> list[str]:
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def run_dates(dates: list[str], force: bool) -> dict:
    state = _load_state()
    done = state.get("done", [])
    results: dict[str, str] = {}
    for d in dates:
        if d in done and not force:
            results[d] = "跳过(已落盘)"
            continue
        try:
            snap = run_tracking_day(d)
            if not snap.get("stocks"):
                results[d] = f"完成但涨停池为空（可能非交易日）"
            else:
                results[d] = f"完成({len(snap['stocks'])}只)"
            if d not in done:
                done.append(d)
        except Exception as exc:
            results[d] = f"失败: {str(exc)[:120]}"
    state["done"] = sorted(set(done))
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save_state(state)
    return results


def analyze() -> dict:
    rot = za.rotation_status()
    pools = za.update_pools()
    dates = zt_store.list_daily_dates()
    last = dates[-1] if dates else None
    cores = za.core_candidates(last) if last else {"date": None, "shibie": [], "zhongjun": []}
    sig = za.signal(last) if last else {"date": None, "items": []}
    pending = za.build_pending_csv() if last else{"path": None, "rows": 0}
    out = {
        "date": last, "rotation": rot, "pools_stats": pools.get("stats"),
        "pools": pools.get("pools"), "core": cores, "signal": sig, "pending_csv": pending,
    }
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="涨停复盘工作台：手动采集与分析")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="采集涨停数据（当日或指定日期）")
    p_run.add_argument("--dates", type=str, default=None, help="逗号分隔 YYYY-MM-DD")
    p_run.add_argument("--start", type=str, default=None)
    p_run.add_argument("--end", type=str, default=None)
    p_run.add_argument("--force", action="store_true", default=False)
    sub.add_parser("analyze", help="只跑分析（轮动/核心股/信号/待填CSV）")
    sub.add_parser("report", help="输出最近一日分析结果 JSON")
    args = ap.parse_args()

    if args.cmd == "run":
        if args.dates:
            dates = [d.strip() for d in args.dates.split(",") if d.strip()]
        elif args.start and args.end:
            s = datetime.strptime(args.start, "%Y-%m-%d").date()
            e = datetime.strptime(args.end, "%Y-%m-%d").date()
            dates = trading_days(s, e)
        else:
            today = date.today()
            if today.weekday() >= 5:
                today = today - timedelta(days=today.weekday() - 4)  # 回到周五
            dates = [today.strftime("%Y-%m-%d")]
            if today.weekday() >= 5:
                print(f"今日为周末，默认取最近交易日 {dates[0]}")
        results = run_dates(dates, args.force)
        for d, msg in results.items():
            print(f"  {d}: {msg}")
        print("开始分析（轮动池/核心股/信号/待填CSV）…")
        out = analyze()
        print(f"分析完成：池 {len(out['pools'])} 个、核心股 辨识度{len(out['core'].get('shibie', []))} / 中军{len(out['core'].get('zhongjun', []))}、"
              f"信号 {len(out['signal'].get('items', []))}、待填CSV {out['pending_csv'].get('rows', 0)} 行 -> {out['pending_csv'].get('path')}")
        failed = [d for d, m in results.items() if m.startswith("失败")]
        return 1 if failed else 0
    if args.cmd == "analyze":
        out = analyze()
        print(json.dumps({"date": out.get("date"), "pools": len(out.get("pools", {})),
                          "shibie": len(out.get("core", {}).get("shibie", [])),
                          "zhongjun": len(out.get("core", {}).get("zhongjun", [])),
                          "signal": len(out.get("signal", {}).get("items", [])),
                          "pending_csv": out.get("pending_csv", {}).get("rows")}, ensure_ascii=False))
        return 0
    if args.cmd == "report":
        if RESULT_FILE.exists():
            print(RESULT_FILE.read_text(encoding="utf-8"))
            return 0
        print("暂无报告：先执行 run 或 analyze")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())