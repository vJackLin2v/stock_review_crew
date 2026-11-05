# -*- coding: utf-8 -*-
"""涨停复盘工作台 —— 数据落盘（data/zt_tracking，D1 已确认）。

目录结构（schema 版本化，幂等覆盖）：
- {date}.json              每日全量快照（涨停池+标签+席位+板块日榜）
- pools.json               关注/轮动板块池状态（入池/出池/池内股票/生命周期，D6）
- follow/{board}.json      池内个股逐日走势跟踪（出池后停止新增、历史保留不删）
- seat_labels.json         席位人工标注库（营业部名 → 类型/别名/备注，D4）
- manual_tags.json         人工覆盖（题材标签/核心股确认/板块标签）
- pending_manual/          待人工补充 CSV（C18/D13 格式）

数据根：默认 <项目根>/data/zt_tracking；STOCK_REVIEW_DATA_DIR 覆盖（测试隔离，与 reviews 一致）。
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DISCLAIMER = "仅供参考，不构成投资建议"


def zt_root() -> Path:
    """涨停跟踪数据根目录（D1：data/zt_tracking；STOCK_REVIEW_DATA_DIR 覆盖）。"""
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    if override:
        return Path(override) / "zt_tracking"
    return Path(__file__).resolve().parents[3] / "data" / "zt_tracking"


def _check_date(date: str) -> str:
    if not _DATE_RE.match(str(date)):
        raise ValueError(f"无效日期：{date}（应为 YYYY-MM-DD）")
    return str(date)


def daily_path(date: str) -> Path:
    return zt_root() / (_check_date(date) + ".json")


def save_daily(date: str, payload: dict) -> Path:
    """写每日全量快照（幂等覆盖；真实数据不入 git，data/ 已忽略）。"""
    path = daily_path(date)
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = dict(payload)
    obj.setdefault("schema_version", SCHEMA_VERSION)
    obj.setdefault("saved_at", datetime.now().isoformat(timespec="seconds"))
    obj.setdefault("date", _check_date(date))
    path.write_text(_dumps(obj), encoding="utf-8")
    return path


def load_daily(date: str) -> Optional[dict]:
    path = daily_path(date)
    if not path.exists():
        return None
    try:
        obj = _loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def list_daily_dates() -> list[str]:
    """已落盘日期（升序）。"""
    out = []
    for p in sorted(zt_root().glob("????-??-??.json")):
        if _DATE_RE.match(p.stem):
            out.append(p.stem)
    return out


# ── pools.json：关注/轮动板块池生命周期（D6/D11） ──────────────────────────

def pools_path() -> Path:
    return zt_root() / "pools.json"


def load_pools() -> dict:
    p = pools_path()
    if p.exists():
        try:
            obj = _loads(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and isinstance(obj.get("pools"), dict):
                return obj
        except Exception:
            pass
    return {"schema_version": SCHEMA_VERSION, "pools": {}}


def save_pools(obj: dict) -> Path:
    p = pools_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    obj = dict(obj)
    obj.setdefault("schema_version", SCHEMA_VERSION)
    p.write_text(_dumps(obj), encoding="utf-8")
    return p


# ── follow/{board}.json：池内个股逐日走势跟踪（D6） ─────────────────────────

def follow_path(board: str) -> Path:
    return zt_root() / "follow" / (board + ".json")


def load_follow(board: str) -> dict:
    p = follow_path(board)
    if p.exists():
        try:
            obj = _loads(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return {"schema_version": SCHEMA_VERSION, "board": board, "entries": []}


def save_follow(board: str, obj: dict) -> Path:
    p = follow_path(board)
    p.parent.mkdir(parents=True, exist_ok=True)
    obj = dict(obj)
    obj.setdefault("schema_version", SCHEMA_VERSION)
    obj["board"] = board
    p.write_text(_dumps(obj), encoding="utf-8")
    return p


# ── seat_labels.json：席位人工标注库（D4） ─────────────────────────────────

def seat_labels_path() -> Path:
    return zt_root() / "seat_labels.json"


def load_seat_labels() -> dict:
    p = seat_labels_path()
    if p.exists():
        try:
            obj = _loads(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and isinstance(obj.get("labels"), dict):
                return obj
        except Exception:
            pass
    return {"schema_version": SCHEMA_VERSION, "labels": {}}


def save_seat_labels(obj: dict) -> Path:
    p = seat_labels_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    obj = dict(obj)
    obj.setdefault("schema_version", SCHEMA_VERSION)
    p.write_text(_dumps(obj), encoding="utf-8")
    return p


# ── manual_tags.json：人工覆盖（题材/核心股/板块标签） ──────────────────────

def manual_tags_path() -> Path:
    return zt_root() / "manual_tags.json"


def load_manual_tags() -> dict:
    p = manual_tags_path()
    if p.exists():
        try:
            obj = _loads(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return {"schema_version": SCHEMA_VERSION, "topics": {}, "board_tags": {}, "core_flags": {}}


def save_manual_tags(obj: dict) -> Path:
    p = manual_tags_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    obj = dict(obj)
    obj.setdefault("schema_version", SCHEMA_VERSION)
    p.write_text(_dumps(obj), encoding="utf-8")
    return p


# ── pending_manual/：待人工补充 CSV（C18/D13） ─────────────────────────────

def pending_dir() -> Path:
    return zt_root() / "pending_manual"


def write_pending_csv(rows: list[dict], prefix: str = "zt_pending") -> Path:
    """导出待人工补充 CSV（utf-8-sig，便于 Excel 打开）。

    rows 建议字段：date, code, name, topic, seat, note —— 与 D13 确认格式一致。
    """
    d = pending_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    if rows:
        fields = list(rows[0].keys())
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    else:
        path.write_text("", encoding="utf-8")
    return path


def _dumps(obj: Any) -> str:
    import json
    import math

    def _convert(o: Any) -> Any:
        if isinstance(o, (datetime,)):
            return str(o)
        if isinstance(o, float) and not math.isfinite(o):
            return None
        raise TypeError(f"对象 {type(o).__name__} 不可序列化")

    return json.dumps(obj, ensure_ascii=False, indent=2, default=_convert)


def _loads(text: str) -> Any:
    import json

    return json.loads(text)
