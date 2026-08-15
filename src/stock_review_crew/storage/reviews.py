# -*- coding: utf-8 -*-
"""历史复盘存储（R11 R4：每日一份 + 覆盖前归档 + 旧结构兼容）。

目录结构：
- 新结构（R11 起）：data/reviews/{YYYY-MM-DD}/{meta.json, report.json, snapshot.json}
  —— 每日一份，同日期再次保存为覆盖写；
- 旧结构（R11 前同日多份，只读兼容）：data/reviews/{YYYY-MM-DD}/{HHMMSS}[/-N]/{meta.json, ...}

- 覆盖前归档：保存到已存在日期时，旧版完整目录（含 meta/report/snapshot）用
  shutil.copytree 复制到 output/archive/{YYYY-MM-DD}_{HHMMSS}/；归档失败不阻塞
  主流程，在 meta.degraded 记录 degraded_note（中文）；
- 历史兼容：新结构不存在时读取旧结构（取时间点最新一份）展示，列表按「每日一条」
  归并（record_id 统一为 "{date}"，time 字段保留原时间点）；
- 读写健壮：非法 id / 目录缺失 / JSON 损坏一律返回空结构，绝不抛异常；
- 数据根目录可用环境变量 STOCK_REVIEW_DATA_DIR 覆盖（测试/隔离用），归档根目录
  可用 STOCK_REVIEW_ARCHIVE_DIR 覆盖（测试/隔离用）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "MODE_LABELS",
    "DEFAULT_DISCLAIMER",
    "reviews_root",
    "archive_root",
    "save_review",
    "list_reviews",
    "get_review",
    "delete_review",
    "context",
    "has_review",
    "save_market_data",
    "load_market_data",
    "has_market_data",
]

# 6 种复盘模式中文标签（requirements §二，与提示词/前端保持一致）
MODE_LABELS = {
    "pre_market": "早盘前决策",
    "auction": "竞价复盘",
    "intraday_am": "上午盘中",
    "noon": "午间复盘",
    "intraday_pm": "下午盘中",
    "close": "收盘复盘",
}

DEFAULT_DISCLAIMER = "仅供参考，不构成投资建议"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{6}(?:-\d+)?$")


def reviews_root() -> Path:
    """复盘存储根目录；环境变量 STOCK_REVIEW_DATA_DIR 可覆盖（测试隔离）。

    STOCK_REVIEW_DATA_DIR = **数据根**（与 backend/backtest._data_root、
    accounts/{plan,quotes} 等一致），这里再拼 /reviews 才是复盘子目录；
    真实后端无 env 时走默认分支。默认分支不变。
    """
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    if override:
        return Path(override) / "reviews"
    return Path(__file__).resolve().parents[3] / "data" / "reviews"


def archive_root() -> Path:
    """覆盖前归档根目录（R11 R4：output/archive，用户可查看的保留区）。

    环境变量 STOCK_REVIEW_ARCHIVE_DIR 可覆盖（测试隔离），绝不触碰仓库 output/。
    """
    override = os.environ.get("STOCK_REVIEW_ARCHIVE_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "output" / "archive"


def _ensure_root() -> Path:
    root = reviews_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _valid_date(value: Any) -> bool:
    """YYYY-MM-DD 且为真实日历日期。"""
    value = str(value or "").strip()
    if not _DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _valid_time(value: Any) -> bool:
    """HHMMSS 或 HHMMSS-N（N 为正整数），且为真实时钟时间。"""
    value = str(value or "").strip()
    if not _TIME_RE.match(value):
        return False
    base, _, suffix = value.partition("-")
    try:
        datetime.strptime(base, "%H%M%S")
    except ValueError:
        return False
    if suffix:
        try:
            if int(suffix) < 1:
                return False
        except ValueError:
            return False
    return True


def _within(root: Path, target: Path) -> bool:
    """resolve 后确认 target 位于 root 之内（Windows 大小写不敏感由 pathlib 处理）。"""
    try:
        root_resolved = root.resolve()
        target_resolved = target.resolve()
    except OSError:
        return False
    try:
        return target_resolved.is_relative_to(root_resolved)
    except AttributeError:  # Python < 3.9 兜底
        return str(target_resolved).startswith(str(root_resolved) + os.sep)


def _read_json(path: Path) -> Any:
    """健壮读取：缺失 / 损坏一律返回 {}，绝不抛异常。"""
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _now_stamp() -> str:
    return datetime.now().strftime("%H%M%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _archive_date_dir(date_dir: Path, meta: dict) -> Optional[str]:
    """覆盖前归档：旧版完整目录复制到 archive_root()/{date}_{HHMMSS}/。

    失败不阻塞主流程：返回 None，并在 meta.degraded 记录 degraded_note（中文）。
    """
    archive = archive_root() / f"{date_dir.name}_{_now_stamp()}"
    n = 2
    while archive.exists():
        archive = archive_root() / f"{date_dir.name}_{_now_stamp()}-{n}"
        n += 1
    try:
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(date_dir, archive)
        return str(archive)
    except Exception as exc:  # noqa: BLE001 - 归档失败不阻塞覆盖写（R4 契约）
        meta.setdefault("degraded", []).append(f"覆盖前归档失败：{exc}")
        return None


def save_review(
    meta: Optional[dict],
    report: Any = None,
    snapshot: Any = None,
    *,
    overwrite: bool = False,
) -> str:
    """保存一份复盘记录（R11 R4：每日一份），返回 record_id = "{date}"。

    meta 含 date/mode/mode_label/time/created_at/degraded[]/sources[]/disclaimer/summary，
    缺省键自动补齐：date/time 缺省取当前时间；mode 缺省 close；created_at 缺省当前 ISO 时间。
    time 仅作展示（保留创建时间点 HHMMSS），存储不再按时间点分目录。
    同日期再次保存 = 覆盖写：先把旧版完整目录归档到 output/archive/{date}_{HHMMSS}/
    （归档失败不阻塞，meta.degraded 记录 degraded_note），再写入当日新记录；
    overwrite 参数保留仅为向后兼容（每日一份语义下保存即覆盖）。
    report 可为 str（存入 {"final_report": ...}）或 dict（原样存入，建议含
    final_report/analysts/debate_history）；snapshot 为任意 JSON 结构，None 存 {}。
    日期/时间格式非法时抛中文 ValueError（写接口需要显式失败，避免错放路径）。
    """
    meta = dict(meta or {})
    date = str(meta.get("date") or "").strip() or _today()
    time = str(meta.get("time") or "").strip() or _now_stamp()
    if not _valid_date(date):
        raise ValueError(f"日期格式必须为 YYYY-MM-DD，收到：{date!r}")
    if not _valid_time(time):
        raise ValueError(f"时间格式必须为 HHMMSS（可带 -N 后缀），收到：{time!r}")

    mode = str(meta.get("mode") or "close")
    meta.setdefault("mode", mode)
    meta.setdefault("mode_label", str(meta.get("mode_label") or MODE_LABELS.get(mode, "复盘")))
    meta.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    meta.setdefault("time", time)
    meta.setdefault("degraded", [])
    meta.setdefault("sources", [])
    meta.setdefault("disclaimer", DEFAULT_DISCLAIMER)
    meta.setdefault("summary", "")

    root = _ensure_root()
    date_dir = root / date
    # 2026-08-15：离线回放预热缓存（market_data.json）不属于复盘产物，覆盖旧目录时
    # 必须保留（挪出→重建→放回），否则预热缓存被归档/删除，重跑历史复盘又回实时取数。
    _md_backup: Optional[Path] = None
    if date_dir.is_dir():
        md_path = date_dir / _MARKET_DATA_FILENAME
        if md_path.is_file():
            try:
                _md_backup = date_dir.with_name(f".{date}.md_tmp")
                shutil.copy2(md_path, _md_backup)
            except Exception:  # noqa: BLE001 - 备份失败不阻塞（最坏缓存丢失）
                _md_backup = None
        # R4：覆盖前归档旧版完整目录（新结构单份或旧结构多份均可归档），失败不阻塞
        archived = _archive_date_dir(date_dir, meta)
        if archived:
            meta["archived_to"] = archived
        try:
            shutil.rmtree(date_dir)  # 清理旧内容（旧结构子目录等），失败不阻塞（写入会覆盖同名文件）
        except Exception:  # noqa: BLE001
            pass
    date_dir.mkdir(parents=True, exist_ok=True)
    if _md_backup is not None and _md_backup.exists():
        try:
            shutil.copy2(_md_backup, date_dir / _MARKET_DATA_FILENAME)
        except Exception:  # noqa: BLE001
            pass
        try:
            _md_backup.unlink()
        except Exception:  # noqa: BLE001
            pass

    if isinstance(report, str):
        report_data = {"final_report": report}
    elif isinstance(report, dict):
        report_data = report
    elif report is None:
        report_data = {"final_report": ""}
    else:
        report_data = {"final_report": str(report)}

    snapshot_data = snapshot if isinstance(snapshot, dict) else ({} if snapshot is None else snapshot)

    _write_json(date_dir / "meta.json", meta)
    _write_json(date_dir / "report.json", report_data)
    _write_json(date_dir / "snapshot.json", snapshot_data)
    return date


def _entry_items(date_dir: Path) -> list:
    """某日期下全部有效复盘条目（新结构单份；旧结构时间点倒序）。

    - 新结构（date_dir/meta.json 存在）：单条每日记录，record_id="{date}"；
    - 旧结构兼容：{HHMMSS}[/-N] 子目录条目，按时间点倒序（record_id="{date}_{time}"）；
    meta 损坏用兜底值补齐，绝不抛异常。
    """
    if not date_dir.is_dir():
        return []
    date = date_dir.name
    if (date_dir / "meta.json").is_file():
        meta = _read_json(date_dir / "meta.json")
        if not isinstance(meta, dict) or not meta:
            meta = {}
        mode = str(meta.get("mode") or "close")
        return [
            {
                "record_id": date,
                "date": date,
                "time": str(meta.get("time") or "000000"),
                "mode": mode,
                "mode_label": str(meta.get("mode_label") or MODE_LABELS.get(mode, "复盘")),
                "created_at": meta.get("created_at"),
                "summary": meta.get("summary"),
            }
        ]
    items = []
    for entry in sorted(date_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if not entry.is_dir() or not _valid_time(entry.name):
            continue
        meta = _read_json(entry / "meta.json")
        if not isinstance(meta, dict) or not meta:
            meta = {}
        mode = str(meta.get("mode") or "close")
        items.append(
            {
                "record_id": f"{date}_{entry.name}",
                "date": date,
                "time": entry.name,
                "mode": mode,
                "mode_label": str(meta.get("mode_label") or MODE_LABELS.get(mode, "复盘")),
                "created_at": meta.get("created_at"),
                "summary": meta.get("summary"),
            }
        )
    return items


def list_reviews() -> list:
    """按日期倒序返回分组列表（R11 R4：每日一条）。

    返回 [{date, items: [{record_id, mode, mode_label, time, created_at, summary}]}]；
    旧结构多份数据按「取时间点最新一份」归并为每日一条（record_id 统一为 "{date}"）；
    缺失/损坏的 meta 用兜底值补齐，绝不抛异常。
    """
    root = reviews_root()
    if not root.is_dir():
        return []
    groups = []
    for date_dir in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        if not date_dir.is_dir() or not _valid_date(date_dir.name):
            continue
        items = _entry_items(date_dir)
        if not items:
            continue
        # 列表契约（§五）：items 不含 date 字段（date 即分组键）；每日一条取最新一份
        first = {k: v for k, v in items[0].items() if k != "date"}
        first["record_id"] = date_dir.name
        groups.append({"date": date_dir.name, "items": [first]})
    return groups


def get_review(date: Any, time: Optional[Any] = None) -> dict:
    """读取一份复盘详情（R11 R4：按日期单份；旧结构兼容）。

    新结构（每日一份）：直接读 date_dir 下 meta/report/snapshot（time 参数忽略）；
    旧结构兼容：time 缺省取时间点最新一份，指定 time 读对应条目。
    返回 {meta, report, analyses[], debate_history[], snapshot}；report 为 Markdown 字符串
    （取自 report.json 的 final_report）。非法 id / 目录缺失 / 文件损坏一律返回空结构。
    """
    empty = {"meta": {}, "report": "", "analyses": [], "debate_history": [], "snapshot": {}}
    if not _valid_date(date):
        return empty
    date = str(date).strip()
    root = reviews_root()
    date_dir = root / date
    if not _within(root, date_dir) or not date_dir.is_dir():
        return empty
    time = str(time or "").strip() or None

    if (date_dir / "meta.json").is_file():
        entry = date_dir  # 新结构：每日一份
    elif time is not None and _valid_time(time):
        entry = date_dir / time
        if not _within(root, entry) or not entry.is_dir():
            return empty
    else:
        items = _entry_items(date_dir)  # 旧结构：取时间点最新一份
        if not items:
            return empty
        entry = date_dir / items[0]["time"]

    meta = _read_json(entry / "meta.json")
    if not isinstance(meta, dict) or not meta:
        meta = {}
    snapshot = _read_json(entry / "snapshot.json")
    if not isinstance(snapshot, dict) or not snapshot:
        snapshot = {}

    stored = _read_json(entry / "report.json")
    if isinstance(stored, dict):
        report = stored.get("final_report")
        report = report if isinstance(report, str) else ""
        analyses = stored.get("analysts") or stored.get("analyses") or []
        debate_history = stored.get("debate_history") or []
    elif isinstance(stored, str):
        report, analyses, debate_history = stored, [], []
    else:
        report, analyses, debate_history = "", [], []

    return {
        "meta": meta,
        "report": report,
        "analyses": analyses if isinstance(analyses, list) else [],
        "debate_history": debate_history if isinstance(debate_history, list) else [],
        "snapshot": snapshot,
    }


def delete_review(date: Any, time: Optional[Any] = None) -> bool:
    """删除复盘记录（R11 R4：按日期删除整份；time 指定时仅删旧结构单条目）。

    非法 id / 不存在 / 删除失败均返回 False，绝不抛异常；旧结构条目删空后清理空日期目录。
    """
    if not _valid_date(date):
        return False
    date = str(date).strip()
    root = reviews_root()
    date_dir = root / date
    if not _within(root, date_dir) or not date_dir.is_dir():
        return False
    time = str(time or "").strip() or None
    if time is not None:
        if not _valid_time(time):
            return False
        entry = date_dir / time
        if not _within(root, entry) or not entry.is_dir():
            return False
        try:
            shutil.rmtree(entry)
        except Exception:
            return False
        try:
            if not any(date_dir.iterdir()):
                date_dir.rmdir()
        except Exception:  # noqa: BLE001 - 空目录清理失败不影响删除结果
            pass
        return True
    try:
        shutil.rmtree(date_dir)
    except Exception:
        return False
    return True


def has_review(date: Any) -> bool:
    """该日期是否已有复盘记录（新结构单份或旧结构任一时间点）。

    用于 status 接口（has_today_review）与 POST 并发去重。
    """
    if not _valid_date(date):
        return False
    date = str(date).strip()
    date_dir = reviews_root() / date
    if not date_dir.is_dir():
        return False
    return bool(_entry_items(date_dir))


def context(date: Any, mode: Optional[str] = None) -> dict:
    """复盘上下文：昨日 + 当日更早时间点（requirements §一.5 / §五，R11 每日一份适配）。

    返回 {"yesterday": {...}|None, "earlier_today": [{...}]}：
    - yesterday：最近一个早于该日期、且 mode=close 的复盘摘要（close 优先）；若早于该日期的
      全部复盘都无 close，则取最近一份任意模式；无历史返回 None，绝不编造；
    - earlier_today：该日期已存在的复盘摘要——新结构为当日单份；旧结构按时间点倒序；
    - 摘要项字段：record_id/date/time/mode/mode_label/summary（缺失为 None）。
    mode 参数预留（当前实现不依赖，后续如需按模式过滤当日列表可在此扩展）。
    """
    if not _valid_date(date):
        return {"yesterday": None, "earlier_today": []}
    date = str(date).strip()
    earlier_today = _entry_items(reviews_root() / date)

    yesterday = None
    root = reviews_root()
    if root.is_dir():
        older_dirs = [
            d
            for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True)
            if d.is_dir() and _valid_date(d.name) and d.name < date
        ]
        # 第一优先：最近日期上的 close 复盘
        for date_dir in older_dirs:
            close_items = [it for it in _entry_items(date_dir) if it["mode"] == "close"]
            if close_items:
                yesterday = close_items[0]
                break
        # 兜底：最近日期上任意模式的最新一份
        if yesterday is None:
            for date_dir in older_dirs:
                items = _entry_items(date_dir)
                if items:
                    yesterday = items[0]
                    break

    return {"yesterday": yesterday, "earlier_today": earlier_today}


# ═══════════════════════════════════════════════════════════════
# 完整行情快照落盘（离线回放预热，2026-08-15）
# ═══════════════════════════════════════════════════════════════
#
# market_data.json 保存「喂给 LLM 的完整取数结果」，供重跑历史复盘时的离线回放：
# 复用已落盘的行情快照、避免再次实时调东财/腾讯/akshare（绕开东财 502 抖动、
# 保证 A/B 回测输入确定性一致）。与 meta.json/report.json/snapshot.json/plan.json
# 同目录（data/reviews/{date}/），路径遵循 STOCK_REVIEW_DATA_DIR 测试隔离。
#
# payload 约定（至少）：
#   date / mode / mode_label / blocks（fetch_mode_data 的完整 blocks）/
#   market_data（渲染后的 Markdown 文本） / market_weather / emotion_cycle /
#   raw_news / degraded_notes（下游使用的最终降级标注）/ source / saved_at
#
# 读取端健壮：目录缺失 / 模式不符 / JSON 损坏一律返回 {}（绝不抛异常）。

_MARKET_DATA_FILENAME = "market_data.json"


def market_data_path(date: Any) -> Path:
    """{date}/market_data.json 的路径（不校验存在性）。"""
    if not _valid_date(date):
        return reviews_root() / str(date or "") / _MARKET_DATA_FILENAME
    return reviews_root() / str(date).strip() / _MARKET_DATA_FILENAME


def save_market_data(date: Any, payload: Any) -> str:
    """保存完整行情快照到 {date}/market_data.json，返回 date（YYYY-MM-DD）。

    合法日期才写盘；非法日期抛中文 ValueError（与 save_review 一致）。
    payload 须为 dict；非 dict 抛中文 TypeError。覆盖写不归档（行情快照可安全重建）。
    """
    date = str(date or "").strip()
    if not _valid_date(date):
        raise ValueError(f"日期格式必须为 YYYY-MM-DD，收到：{date!r}")
    if not isinstance(payload, dict):
        raise TypeError("market_data payload 必须为 dict")
    root = _ensure_root()
    date_dir = root / date
    try:
        date_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 - 建目录失败显式抛（写接口需要失败可见）
        raise OSError(f"创建复盘目录失败：{date_dir}（{exc}）") from exc
    _write_json(date_dir / _MARKET_DATA_FILENAME, payload)
    return date


def load_market_data(date: Any) -> dict:
    """读取 {date}/market_data.json；缺失 / JSON 损坏 / 非 dict 一律返回 {}。"""
    path = market_data_path(date)
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def has_market_data(date: Any) -> bool:
    """该日期是否已有完整行情快照（market_data.json 存在且为合法 dict）。"""
    return bool(load_market_data(date))
