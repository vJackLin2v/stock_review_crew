# -*- coding: utf-8 -*-
"""旧 output/ 复盘导入脚本（Issue I4 / requirements §一.5、§七 Q10；R11 R4 每日一份）。

把 output/{date}/复盘_{HHMMSS}.md 幂等导入为每日一份：
data/reviews/{date}/{meta.json, report.json, snapshot.json}（R11 R4 新结构）：

- 模式按文件时间点推断：<09:15 pre_market / 09:15-09:25 auction /
  09:30-11:30 intraday_am / 11:30-13:00 noon / 13:00-15:00 intraday_pm / >=15:00 close；
  09:25-09:30 空档及其它非法时间点归入 close，并在 meta.mode_note 标注原因；
- 同一日期存在多份旧复盘时，按「每日一份」契约仅导入最新时间点一份，较早版本
  保留在 output/ 源目录并明确提示（历史版本可自行归档）；
- report.json 写入 final_report（整份 md 内容），analysts[] 尽力从「### 分析师名」段落解析，
  debate_history 无法从旧文件还原，置空数组；
- snapshot 缺失，以 meta/snapshot 中的 imported_from_legacy 标注；
- 幂等：目标日期已有记录（新结构或旧多份结构）且 meta 含 imported_from_legacy 则跳过，
  --force 可重导（覆盖前自动把旧版完整目录归档到 output/archive/{date}_{HHMMSS}/）；
- 全部中文输出；目录创建健壮（已存在不报错）。

用法：
  python scripts/import_output_history.py [--dry-run] [--force]
      [--output-dir PATH] [--data-dir PATH] [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 让脚本可直接以 `python scripts/import_output_history.py` 运行（src 布局）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_review_crew.storage.reviews import (  # noqa: E402
    DEFAULT_DISCLAIMER,
    MODE_LABELS,
    has_review,
    reviews_root,
    save_review,
)

_FILE_RE = re.compile(r"^复盘_(\d{6})\.md$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{6}$")

# 旧 md 中「### 」数据小节（非分析师段落），用于无「参与分析师」行时的兜底过滤
_DATA_HEADING_BLACKLIST = {
    "指数表现",
    "涨停板",
    "跌停板",
    "今日要闻",
    "财新头条",
    "近60日均线系统",
    "外围市场",
    "板块涨幅前五 (同花顺)",
    "板块净流出前五",
    "板块涨幅前五 (同花顺(历史,60行业))",
    "三大指数表现",
    "涨停板情况",
    "核心涨停个股",
    "涨停行业分布TOP5",
    "连板高标",
    "市场情绪判断",
    "主要分歧点",
    "最终共识",
    "讨论过程总结",
    "各流派建议",
    "需要关注的股票/板块",
    "风险提示",
}


def _valid_date(value: str) -> bool:
    if not _DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def infer_mode(time_str: str) -> tuple[str, Optional[str]]:
    """按时间点推断复盘模式；返回 (mode, 标注说明或 None)。"""
    t = str(time_str or "").strip()
    if not _TIME_RE.match(t):
        return "close", f"时间点 {t!r} 不是合法的 HHMMSS，按契约归入 close"
    try:
        parsed = datetime.strptime(t, "%H%M%S")
    except ValueError:
        return "close", f"时间点 {t!r} 不是合法时钟时间，按契约归入 close"
    minutes = parsed.hour * 60 + parsed.minute
    if minutes < 9 * 60 + 15:
        return "pre_market", None
    if minutes < 9 * 60 + 25:
        return "auction", None
    if minutes < 9 * 60 + 30:
        return (
            "close",
            f"时间点 {t!r} 处于 09:25-09:30 空档（竞价已结束、盘中未开始），按契约归入 close",
        )
    if minutes < 11 * 60 + 30:
        return "intraday_am", None
    if minutes < 13 * 60:
        return "noon", None
    if minutes < 15 * 60:
        return "intraday_pm", None
    return "close", None


def extract_analysts(md: str) -> list:
    """尽力从 md 解析分析师名单；失败返回空数组。

    优先用「参与分析师」行与「### 分析师名」段落互相校验（取二者交集）；
    无参与行时，用「### 」标题减去已知数据小节黑名单兜底。
    """
    if not md:
        return []
    headings: list[str] = []
    seen = set()
    for m in re.finditer(r"^###\s+(.+?)\s*$", md, re.M):
        name = m.group(1).strip().strip("*").strip()
        if name and name not in seen:
            seen.add(name)
            headings.append(name)

    participants: list[str] = []
    pm = re.search(r"参与分析师\s*\*{0,2}\s*[:：]\s*([^\n]+)", md)
    if pm:
        for raw in re.split(r"[,，、;；\s]+", pm.group(1).strip()):
            name = raw.strip().strip("*").strip()
            if name and name not in participants:
                participants.append(name)

    if participants:
        matched = [p for p in participants if p in seen]
        return matched if matched else participants
    return [h for h in headings if h not in _DATA_HEADING_BLACKLIST]


def _make_summary(md: str, max_chars: int = 120) -> str:
    """从「# 最终报告」段落后提取首个非空行作为摘要；无标记则用全文首行。"""
    text = md
    marker = "# 最终报告"
    if marker in text:
        text = text.split(marker, 1)[1]
    for line in text.splitlines():
        line = line.strip().strip("#").strip()
        if line:
            return line[:max_chars]
    return md.strip()[:max_chars]


def _read_meta_robust(entry: Path) -> dict:
    try:
        if not (entry / "meta.json").exists():
            return {}
        data = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def import_one(
    md_path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """导入单个旧复盘文件。

    返回 {"status": "imported"|"skipped"|"dry_run"|"error", ...}。
    """
    date = md_path.parent.name
    m = _FILE_RE.match(md_path.name)
    if not m or not _valid_date(date):
        return {"status": "error", "reason": f"文件或日期目录不合法：{md_path}"}
    time = m.group(1)
    mode, note = infer_mode(time)
    target = reviews_root() / date  # R11 R4：每日一份（日期目录即记录目录）
    exists = has_review(date)

    if exists and not force:
        meta = _read_meta_robust(target)
        if meta.get("imported_from_legacy"):
            return {
                "status": "skipped",
                "reason": "目标已存在且为旧数据导入记录（幂等跳过）",
                "target": str(target),
            }
        return {
            "status": "skipped",
            "reason": "目标已存在（每日一份），跳过（如需覆盖请加 --force）",
            "target": str(target),
        }

    try:
        md_text = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"status": "error", "reason": f"读取失败：{exc}", "path": str(md_path)}

    analysts = extract_analysts(md_text)
    meta = {
        "date": date,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, "复盘"),
        "time": time,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "degraded": ["旧 output 导入：无数据快照，分析师列表为尽力解析"],
        "sources": ["legacy_output"],
        "disclaimer": DEFAULT_DISCLAIMER,
        "summary": _make_summary(md_text),
        "imported_from_legacy": True,
        "source_file": str(md_path),
    }
    if note:
        meta["mode_note"] = note
    report = {"final_report": md_text, "analysts": analysts, "debate_history": []}
    snapshot = {"imported_from_legacy": True, "note": "旧 output 导入，无数据快照"}

    if dry_run:
        return {
            "status": "dry_run",
            "target": str(target),
            "mode": mode,
            "analysts": analysts,
            "overwritten": bool(exists),
            "note": note,
        }

    record_id = save_review(meta, report, snapshot, overwrite=force and exists)
    return {
        "status": "imported",
        "record_id": record_id,
        "target": str(target),
        "mode": mode,
        "analysts": analysts,
        "overwritten": bool(exists),
        "note": note,
    }


def run_import(
    output_root: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    date: Optional[str] = None,
) -> dict:
    """扫描 output_root 下全部（或指定日期）复盘文件并导入；返回统计。"""
    output_root = Path(output_root)
    if not output_root.is_dir():
        raise FileNotFoundError(f"输出目录不存在：{output_root}")
    if date is not None and not _valid_date(date):
        raise ValueError(f"日期参数必须为 YYYY-MM-DD，收到：{date!r}")

    stats = {"scanned": 0, "imported": 0, "skipped": 0, "dry_run": 0, "errors": 0, "invalid": 0}
    date_dirs = sorted(output_root.iterdir(), key=lambda p: p.name, reverse=True)
    for date_dir in date_dirs:
        if not date_dir.is_dir():
            continue
        if not _valid_date(date_dir.name):
            if date is None:
                print(f"⚠️ 跳过非日期目录：{date_dir.name}")
                stats["invalid"] += 1
            continue
        if date is not None and date_dir.name != date:
            continue
        file_list = []
        for md_path in sorted(date_dir.iterdir(), key=lambda p: p.name):
            if not md_path.is_file() or md_path.suffix.lower() != ".md":
                continue
            fm = _FILE_RE.match(md_path.name)
            if not fm:
                print(f"⚠️ 跳过不匹配文件：{md_path.name}（应为 复盘_HHMMSS.md）")
                stats["invalid"] += 1
                continue
            file_list.append((fm.group(1), md_path))
        if not file_list:
            continue
        file_list.sort()  # 按 HHMMSS 升序，最新一份在最后
        latest_time, latest_path = file_list[-1]
        stats["scanned"] += len(file_list)  # 全部有效文件计入扫描
        for old_time, old_path in file_list[:-1]:
            # R11 R4：同一日期多份旧复盘仅保留最新时间点（较早版本保留在 output/ 源目录）
            stats["skipped"] += 1
            print(
                f"⏭️ 跳过 {date_dir.name} {old_path.stem}：同一日期多份旧复盘"
                f"（每日一份），仅保留最新时间点 {latest_time}，较早版本保留在 output/ 源目录"
            )
        result = import_one(latest_path, force=force, dry_run=dry_run)
        status = result["status"]
        stats[status if status in ("imported", "skipped", "dry_run", "error") else "errors"] += 1
        label = MODE_LABELS.get(result.get("mode", "close"), "复盘")
        if status == "imported":
            prefix = "⚠️ 覆盖导入" if result.get("overwritten") else "✅ 导入"
            print(f"{prefix} {date_dir.name} {result.get('record_id', '')}（{label}）→ {result['target']}")
        elif status == "skipped":
            print(f"⏭️ 跳过 {date_dir.name} {latest_path.stem}：{result['reason']}")
        elif status == "dry_run":
            verb = "覆盖" if result.get("overwritten") else "导入"
            print(f"🔍 将{verb} {date_dir.name} {latest_path.stem}（{label}）→ {result['target']}")
        else:
            print(f"❌ 失败 {result.get('path', latest_path)}：{result['reason']}")

    verb = "预览" if dry_run else "完成"
    print(
        f"\n汇总（{verb}）：扫描 {stats['scanned']} 份，"
        f"新导入 {stats['imported']} 份，跳过 {stats['skipped']} 份，"
        f"预览 {stats['dry_run']} 份，忽略不合法项 {stats['invalid']} 个，失败 {stats['errors']} 个"
    )
    return stats


def main(argv: Optional[list] = None) -> int:
    # 中文输出兼容：Windows 默认 GBK 控制台无法打印 emoji/部分字符，重配为 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="旧 output/ 复盘导入脚本（幂等，可重跑）")
    parser.add_argument("--dry-run", action="store_true", help="只预览不写入任何文件")
    parser.add_argument("--force", action="store_true", help="目标已存在时强制重导（覆盖旧文件）")
    parser.add_argument("--output-dir", default=None, help="旧 output 根目录（默认仓库 output/）")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="目标 data/reviews 根目录（默认仓库 data/reviews，可用 STOCK_REVIEW_DATA_DIR 覆盖）",
    )
    parser.add_argument("--date", default=None, help="只导入指定日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ["STOCK_REVIEW_DATA_DIR"] = str(Path(args.data_dir).resolve())
    output_root = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else (Path(__file__).resolve().parent.parent / "output")
    )
    try:
        run_import(output_root, force=args.force, dry_run=args.dry_run, date=args.date)
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌ {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
