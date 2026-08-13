# -*- coding: utf-8 -*-
"""Run full reviews for 2026-07-01..07-03, collect every no-data/degraded mark.

Usage:
    python work/run_review_july0103.py

Output:
    work/review_july0103_result.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import requests

BASE = "http://127.0.0.1:8501"
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(_HERE, "..")
DAYS = ["2026-07-01", "2026-07-02", "2026-07-03"]


def submit(date_str: str, max_rounds: int) -> str:
    # R11: daily single-copy -> delete existing before re-running
    try:
        requests.delete(f"{BASE}/api/reviews/{date_str}", timeout=15)
    except Exception:
        pass
    r = requests.post(f"{BASE}/api/reviews",
                      json={"date": date_str, "mode": "close",
                            "max_rounds": min(max_rounds, 2)},  # 2026-08-10：辩论 3→2 轮省 ~2 分钟/天
                      timeout=30)
    r.raise_for_status()
    return r.json()["job_id"]


def job_status(job_id: str) -> dict:
    r = requests.get(f"{BASE}/api/jobs/{job_id}", timeout=15)
    r.raise_for_status()
    return r.json()


def collect_no_data(date_str: str) -> dict:
    """Collect every no-data / degraded mark from the day's report."""
    p = os.path.join(REPO, "data", "reviews", date_str, "report.json")
    if not os.path.exists(p):
        return {"report_file": str(p), "exists": False}
    with open(p, encoding="utf-8") as f:
        raw = f.read()
    try:
        r = json.loads(raw)
    except Exception as exc:
        return {"report_file": str(p), "exists": True, "json_error": str(exc)}

    meta = r.get("meta") or {}
    degraded = meta.get("degraded") or []
    blocks = meta.get("blocks") or {}
    block_status = {}
    if isinstance(blocks, dict):
        for name, b in blocks.items():
            if not isinstance(b, dict):
                continue
            block_status[name] = {
                "source": b.get("source"),
                "degraded": bool(b.get("degraded")),
                "reasons": (b.get("degraded_reason") or [])[:3],
            }

    # Scan full text for no-data marks with context
    text = r.get("report") or r.get("content") or r.get("text") or raw
    marks = []
    for m in re.finditer(r".{18}(?:无数据|数据缺失|降级|不可用|失败|缺失|无存档|未找到|未能获取).{22}", text):
        snippet = m.group(0).replace("\n", " ")
        if snippet not in marks:
            marks.append(snippet)

    analysts = {}
    for k, v in r.items():
        if isinstance(v, dict) and (v.get("report") or v.get("content")):
            analysts[k] = v

    return {
        "report_file": str(p),
        "exists": True,
        "status": r.get("status"),
        "analysts_total": r.get("analysts_total"),
        "analysts_done": r.get("analysts_done"),
        "degraded": degraded,
        "blocks": block_status,
        "no_data_marks": marks[:60],
        "mark_count": len(marks),
    }


def main() -> None:
    out = []
    for day in DAYS:
        t0 = time.time()
        try:
            jid = submit(day, 3)
        except Exception as exc:
            out.append({"date": day, "submit_error": str(exc)})
            print(f"[{day}] submit FAIL: {exc}", flush=True)
            continue
        print(f"[{day}] job={jid} @ {time.strftime('%H:%M:%S')}", flush=True)
        while True:
            try:
                j = job_status(jid)
            except Exception:
                time.sleep(10)
                continue
            st = j.get("status")
            if st == "done":
                break
            if st == "error":
                out.append({"date": day, "job_error": j.get("error"),
                            "elapsed": round(time.time() - t0)})
                print(f"[{day}] ERROR: {j.get('error')}", flush=True)
                break
            if int(time.time() - t0) % 60 < 10:
                print(f"[{day}] {st} stage={j.get('stage')} pct={j.get('pct')} "
                      f"elapsed={int(time.time() - t0)}s", flush=True)
            time.sleep(10)
        else:
            continue
        if j.get("status") == "done":
            rec = collect_no_data(day)
            rec["elapsed"] = round(time.time() - t0)
            out.append({"date": day, **rec})
            deg = rec.get("degraded") or []
            print(f"[{day}] DONE {rec['elapsed']}s degraded={len(deg)} "
                  f"marks={rec.get('mark_count')}", flush=True)
            for d in deg:
                print(f"    DEG: {str(d)[:120]}", flush=True)
            for m in rec.get("no_data_marks", [])[:12]:
                print(f"    MARK: {m[:120]}", flush=True)

    with open(os.path.join(_HERE, "review_july0103_result.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n==== saved work/review_july0103_result.json ====", flush=True)


if __name__ == "__main__":
    main()
