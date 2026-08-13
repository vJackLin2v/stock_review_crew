# -*- coding: utf-8 -*-
"""July sector batch probe (THS v4/v6 line direct).

Usage:
    python work/run_sectors_july.py

Output:
    work/sectors_july_result.json (per-trading-day block result)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
os.environ.pop("ALL_PROXY", None)

from stock_review_crew.tools import stock_data as sd  # noqa: E402


def trading_days(start: str, end: str) -> list[str]:
    days = []
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while d <= end_d:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def main() -> None:
    days = trading_days("2026-07-01", "2026-07-31")
    out = []
    ok = 0
    for day in days:
        t0 = time.time()
        row = {"date": day}
        try:
            r = sd.fetch_sectors(day)
            row.update({
                "ok": True,
                "has_data": bool(r.get("top5") or r.get("bottom5")),
                "elapsed": round(time.time() - t0, 1),
                "source": r.get("source"),
                "degraded": bool(r.get("degraded")),
                "reasons": (r.get("degraded_reason") or [])[:2],
                "note": r.get("note"),
                "top5": [{"name": x.get("name") or x.get("板块"), "pct": x.get("pct_change"),
                          "amount": x.get("amount") or x.get("成交额")} for x in (r.get("top5") or [])],
                "bottom5": [{"name": x.get("name") or x.get("板块"), "pct": x.get("pct_change"),
                             "amount": x.get("amount")} for x in (r.get("bottom5") or [])],
            })
            if row["has_data"] and not row["degraded"]:
                ok += 1
            mark = "OK " if (row["has_data"] and not row["degraded"]) else "DEG"
            print(f"{day} {mark} {row['elapsed']}s source={row['source']} "
                  f"top={[(t['name'], t['pct']) for t in row['top5'][:2]]}")
        except Exception as exc:
            row.update({"ok": False, "has_data": False,
                        "elapsed": round(time.time() - t0, 1),
                        "reasons": [f"{type(exc).__name__}: {str(exc)[:90]}"]})
            print(f"{day} FAIL {row['elapsed']}s {row['reasons']}")
        out.append(row)

    with open(os.path.join(_HERE, "sectors_july_result.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    full = sum(1 for r in out if r.get("has_data"))
    deg = sum(1 for r in out if r.get("has_data") and r.get("degraded"))
    print(f"\n==== summary: {len(out)} days, has_data={full}, degraded={deg}, "
          f"fully_ok={ok} ====")


if __name__ == "__main__":
    main()
