# -*- coding: utf-8 -*-
"""真实数据冒烟脚本：提交复盘任务并轮询至完成，输出结构化冒烟记录。

用法（示例）：
    python scripts/smoke_review.py 2026-08-03 close
    python scripts/smoke_review.py 2026-07-31 pre_market --port 8501
    python scripts/smoke_review.py 2026-08-03 auction --expect-window-error
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _proxy_handler():
    return urllib.request.ProxyHandler(
        {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
    )


def call(opener, base: str, method: str, path: str, body=None, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def run_smoke(date: str, mode: str, port: int = 8501, expect_window_error: bool = False,
              max_rounds: int = 1, timeout: float = 480.0) -> dict:
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(_proxy_handler())
    st, res = call(opener, base, "POST", "/api/reviews", {"date": date, "mode": mode, "max_rounds": max_rounds})
    if st != 200:
        detail = str(res.get("detail", res)) if isinstance(res, dict) else str(res)
        print(f"[{date} {mode}] POST → {st}: {detail[:200]}")
        return {"date": date, "mode": mode, "status": "rejected", "http": st, "detail": detail[:500]}
    job_id = res["job_id"]
    print(f"[{date} {mode}] job={job_id} 提交成功")
    stages: list[str] = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, snap = call(opener, base, "GET", f"/api/jobs/{job_id}")
        if snap and snap.get("stage") not in stages:
            stages.append(snap["stage"])
            print(f"  [{time.time()-t0:.0f}s] {snap['stage']} {snap.get('pct')}% {str(snap.get('message'))[:60]}")
        if snap and snap.get("status") in ("done", "error"):
            print(f"[{date} {mode}] FINAL {snap['status']} stages={stages}")
            if snap["status"] == "done":
                result = snap["result"]
                meta = result.get("meta", {})
                report = result.get("report", {})
                fr = report if isinstance(report, str) else (report or {}).get("final_report", "")
                record = {
                    "date": date, "mode": mode, "status": "done",
                    "record_id": result.get("record_id"),
                    "mode_label": meta.get("mode_label"),
                    "time": meta.get("time"),
                    "degraded": meta.get("degraded", []),
                    "sources": meta.get("sources", []),
                    "report_len": len(fr or ""),
                    "summary": str(meta.get("summary"))[:200],
                }
                print(json.dumps(record, ensure_ascii=False, indent=1)[:1600])
                return record
            print("  ERROR:", str(snap.get("error"))[:400])
            return {"date": date, "mode": mode, "status": "error", "error": str(snap.get("error"))[:500]}
        time.sleep(2)
    print(f"[{date} {mode}] TIMEOUT stages={stages}")
    return {"date": date, "mode": mode, "status": "timeout", "stages": stages}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("mode")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--expect-window-error", action="store_true")
    ap.add_argument("--max-rounds", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=480)
    args = ap.parse_args()
    record = run_smoke(args.date, args.mode, args.port, args.expect_window_error,
                       args.max_rounds, args.timeout)
    sys.exit(0 if record.get("status") in ("done", "rejected") else 1)
