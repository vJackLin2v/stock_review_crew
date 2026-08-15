# -*- coding: utf-8 -*-
"""历史复盘行情预热脚本：为 data/reviews/ 下的历史日期预先落盘完整行情快照
market_data.json（供「离线回放」复用，重跑复盘不再实时调东财/腾讯/akshare）。

背景（2026-08-15）：
- 复盘落盘时 snapshot.json 只含前端展示的 6 键，完整取数结果（index 日K/龙虎榜等）
  从未落盘 → 每次重跑历史复盘都要重新实时取数（东财 502 抖动 + A/B 回测输入不一致）。
- 本脚本一次性取数并落盘 market_data.json（blocks + 渲染文本 + 天气/情绪 + 降级标注），
  之后重跑历史复盘会直接离线回放，确定性一致。

用法：
    python scripts/preheat_reviews.py                          # 全部已存在复盘日期（close）
    python scripts/preheat_reviews.py --start 2026-08-01 --end 2026-08-14
    python scripts/preheat_reviews.py --dates 2026-08-03,2026-08-05
    python scripts/preheat_reviews.py --force                   # 覆盖已存在的 market_data.json
    python scripts/preheat_reviews.py --fail-fast               # 单日失败即停止（默认继续）
    python scripts/preheat_reviews.py --mode close              # 指定取数模式（默认 close）

惯例（对齐 work/review_batch.py fail-fast 风格）：
- 单日失败打印中文原因并默认继续（绝不静默跳过）；--fail-fast 立即中止并退出码 1；
- 已存在 market_data.json 的日期默认跳过（断点续跑），--force 覆盖；
- 退出码：0=全部成功/已跳过，1=存在失败日期（或 --fail-fast 触发中断），2=参数错误。
- 建议运行前设置 STOCK_DATA_CACHE_ENABLED=1 顺带填满 data/cache（可选）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DATE_FMT = "%Y-%m-%d"


def _date_arg(value: str) -> date_type:
    try:
        return datetime.strptime(value, DATE_FMT).date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"无效日期（应为 YYYY-MM-DD）：{value}")


def _reviews_root() -> Path:
    """复盘根目录：尊重 STOCK_REVIEW_DATA_DIR（env=数据根，与 storage.reviews 同源，
    拼 /reviews 才是复盘子目录；2026-08-16 统一语义）。"""
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    if override:
        return Path(override) / "reviews"
    return REPO / "data" / "reviews"


def _discover_dates(start: Optional[date_type], end: Optional[date_type],
                    explicit: Optional[list[date_type]]) -> list[str]:
    root = _reviews_root()
    if explicit:
        return [d.strftime(DATE_FMT) for d in explicit]
    if not root.is_dir():
        return []
    existing = sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name)
    )
    if start is None and end is None:
        return existing
    out = []
    for name in existing:
        d = datetime.strptime(name, DATE_FMT).date()
        if start is not None and d < start:
            continue
        if end is not None and d > end:
            continue
        out.append(name)
    return out


def _compute_weather_text(blocks: dict, date_str: str) -> str:
    """复刻 graph.fetch_data_node 的天气文本组装（compute → fetch_market_weather 兜底）。"""
    from stock_review_crew.tools.market_extra import compute_market_weather, fetch_market_weather
    try:
        w = compute_market_weather(blocks)
        if not (w.get("weather") and w.get("weather") != "数据缺失"):
            w = fetch_market_weather(date_str)
        if w.get("weather") and w.get("weather") != "数据缺失":
            label = w.get("emotion_label")
            score = w.get("emotion_score")
            if label and score is not None:
                return f"{w['weather']}（{label} {score} 分）"
            return str(w["weather"])
    except Exception:  # noqa: BLE001 - 天气失败不阻断预热
        pass
    return "无数据"


def _compute_emotion_cycle(blocks: dict) -> str:
    from stock_review_crew.tools.market_extra import classify_emotion_cycle
    try:
        return classify_emotion_cycle(blocks) or "数据不足"
    except Exception:  # noqa: BLE001
        return "数据不足"


def _render_market_md(blocks: dict, mode: str, notes: list) -> str:
    """渲染喂给 LLM 的 market_data 文本（与 graph.fetch_data_node 同源，含降级说明）。"""
    from stock_review_crew.graph import _render_market_markdown
    try:
        return _render_market_markdown(blocks, mode, notes) or "（无数据）"
    except Exception:  # noqa: BLE001
        return "（无数据）"


def preheat_dates(dates: list[str], mode: str = "close", force: bool = False,
                  fail_fast: bool = False) -> tuple[int, list[str]]:
    """预热日期列表；返回 (失败数, 失败原因列表)。"""
    from stock_review_crew.storage.reviews import has_market_data, save_market_data
    from stock_review_crew.tools.stock_data import fetch_mode_data

    failures: list[str] = []
    for date_str in dates:
        if not force and has_market_data(date_str):
            print(f"[{date_str}] 已存在 market_data.json，跳过（--force 可覆盖）")
            continue
        try:
            result = fetch_mode_data(mode, date_str)
            blocks = (result.get("blocks") if isinstance(result.get("blocks"), dict)
                      else result)
            if not isinstance(blocks, dict):
                raise RuntimeError(f"{mode} 取数未返回 blocks")
            degraded_raw = [str(r) for r in (result.get("degraded") or [])]
            weather_text = _compute_weather_text(blocks, date_str)
            emotion_cycle = _compute_emotion_cycle(blocks)
            market_md = _render_market_md(blocks, mode, degraded_raw)
            save_market_data(date_str, {
                "date": date_str,
                "mode": mode,
                "mode_label": result.get("mode_label"),
                "blocks": blocks,
                "market_data": market_md,
                "market_weather": weather_text,
                "emotion_cycle": emotion_cycle,
                "raw_news": "",
                "degraded_notes": list(degraded_raw),
                "source": "preheat:fetch_mode_data",
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            })
            print(f"[{date_str}] 完成 → data/reviews/{date_str}/market_data.json")
        except Exception as exc:  # noqa: BLE001 - 单日失败打印中文原因
            msg = f"[{date_str}] 取数/落盘失败：{exc}"
            print(msg)
            failures.append(msg)
            if fail_fast:
                return len(failures), failures
    return len(failures), failures


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="历史复盘行情预热：为历史日期落盘 market_data.json（离线回放用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "如无 --start/--end/--dates，默认取 data/reviews/ 下全部已存在日期。\n"
            "建议设置 STOCK_DATA_CACHE_ENABLED=1 顺带填满 data/cache（可选）。"
        ),
    )
    parser.add_argument("--start", type=_date_arg, default=None, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=_date_arg, default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--dates", type=str, default=None,
                        help="显式日期，逗号分隔，如 2026-08-03,2026-08-05（优先于 --start/--end）")
    parser.add_argument("--mode", type=str, default="close", help="取数模式（默认 close）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 market_data.json")
    parser.add_argument("--fail-fast", action="store_true", help="单日失败立即中止（默认继续）")
    parser.add_argument("--cache", action="store_true",
                        help="等价于设置 STOCK_DATA_CACHE_ENABLED=1（顺带填满块级缓存）")
    args = parser.parse_args(argv)

    if args.cache:
        os.environ.setdefault("STOCK_DATA_CACHE_ENABLED", "1")

    explicit: Optional[list[date_type]] = None
    if args.dates:
        explicit = []
        for part in args.dates.split(","):
            part = part.strip()
            if not part:
                continue
            explicit.append(_date_arg(part))

    dates = _discover_dates(args.start, args.end, explicit)
    if not dates:
        print("没有需要预热的日期（data/reviews/ 为空，或与区间无交集）。")
        return 0

    print(f"待预热 {len(dates)} 个日期（mode={args.mode}" 
          + (f"，区间 {args.start}~{args.end}" if args.start or args.end else "")
          + ("，--force 覆盖" if args.force else "，已存在默认跳过") + "）：")
    for d in dates:
        print(f"  - {d}")

    n_fail, reasons = preheat_dates(dates, mode=args.mode, force=args.force,
                                    fail_fast=args.fail_fast)
    if n_fail:
        print(f"\n失败 {n_fail} 个日期（--fail-fast={args.fail_fast}）：")
        for r in reasons:
            print(f"  ! {r}")
        return 1
    print(f"\n预热完成：{len(dates) - n_fail}/{len(dates)} 成功，失败 0。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
