# -*- coding: utf-8 -*-
"""自选股（观察列表）本地存储（R10 F9，requirements-r10 §二/§三）。

持久化文件：data/watchlist.json（数据根目录可用环境变量 STOCK_REVIEW_DATA_DIR 覆盖，
测试隔离见 PLAYBOOK §5）。记录字段：{code, name, note, pinned, created_at}。

- code：6 位数字字符串（唯一键）；name 未知为 None（前端显示「—」）；
- note：备注（无则 None）；pinned：置顶标记（默认 False）；created_at：ISO 时间；
- add_watchlist 重复代码抛中文 ValueError（路由层转 409）；格式非法同样抛中文 ValueError（路由层转 400）；
- delete_watchlist 不存在返回 None；reorder_watchlist 缺失/非法代码忽略；
- 写入原子（先写同目录临时文件再 os.replace 替换）；目录缺失自动创建；
- 磁盘写失败一律不抛异常：写操作返回 False 并记录中文 warning（路由层据此返回 500 中文）；
- 读取失败/文件损坏返回空列表，绝不抛异常（与 reviews.py 同风格）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "watchlist_file",
    "list_watchlist",
    "add_watchlist",
    "delete_watchlist",
    "reorder_watchlist",
]

logger = logging.getLogger("stock_review_crew.watchlist")

_CODE_RE = re.compile(r"^\d{6}$")
_LOCK = threading.Lock()  # 串行化 读-改-写，避免并发添加/删除互相覆盖


def watchlist_file() -> Path:
    """观察列表 JSON 路径；STOCK_REVIEW_DATA_DIR 可覆盖数据根（测试隔离）。"""
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    if override:
        return Path(override) / "watchlist.json"
    return Path(__file__).resolve().parents[3] / "data" / "watchlist.json"


def _normalize(record: Any) -> Optional[dict]:
    """单条记录规范化：字段缺失补齐、类型收窄；非法记录返回 None。"""
    if not isinstance(record, dict):
        return None
    code = str(record.get("code") or "").strip()
    if not _CODE_RE.match(code):
        return None
    name = record.get("name")
    note = record.get("note")
    return {
        "code": code,
        "name": str(name) if name is not None else None,
        "note": str(note) if note is not None else None,
        "pinned": bool(record.get("pinned")),
        "created_at": str(record.get("created_at") or ""),
    }


def _load() -> list[dict]:
    """健壮读取：缺失 / 损坏一律返回 []，绝不抛异常。"""
    try:
        path = watchlist_file()
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 读取失败按空列表处理
        return []
    if not isinstance(data, list):
        return []
    return [r for r in (_normalize(item) for item in data) if r is not None]


def _save(records: list[dict]) -> bool:
    """原子写：同目录临时文件 → os.replace 替换；目录缺失自动创建。

    磁盘写失败不抛异常：返回 False 并记录中文 warning（路由层据此返回 500 中文）。
    """
    path = watchlist_file()
    tmp: Optional[Path] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("观察列表写入失败（%s）：%s", path, exc)
        if tmp is not None:
            try:
                tmp.unlink()
            except Exception:  # noqa: BLE001
                pass
        return False


def list_watchlist() -> list[dict]:
    """全部自选记录（按存储顺序）；读取失败返回 []。"""
    return _load()


def add_watchlist(code: str, note: Any = None, *, name: Any = None):
    """添加自选（唯一 code）。成功返回记录；重复抛中文 ValueError（路由层转 409）。

    磁盘写失败时返回 False（不抛异常）；code 非 6 位数字抛中文 ValueError。
    name 为可选关键字（路由层行情兜底解析结果，未知为 None，前端显示「—」）。
    """
    code = str(code or "").strip()
    if not _CODE_RE.match(code):
        raise ValueError("股票代码应为 6 位数字，如 600519")
    record = {
        "code": code,
        "name": str(name) if name is not None else None,
        "note": str(note) if note is not None else None,
        "pinned": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _LOCK:
        records = _load()
        if any(item["code"] == code for item in records):
            raise ValueError(f"该代码已存在于观察列表：{code}")
        records.append(record)
        if not _save(records):
            return False
    return record


def delete_watchlist(code: str):
    """删除自选。成功返回被删记录；不存在返回 None；写失败返回 False（均不抛）。"""
    code = str(code or "").strip()
    if not _CODE_RE.match(code):
        return None
    with _LOCK:
        records = _load()
        kept = [item for item in records if item["code"] != code]
        if len(kept) == len(records):
            return None
        if not _save(kept):
            return False
    return next(item for item in records if item["code"] == code)


def reorder_watchlist(codes: Any):
    """按 codes 顺序重排；缺失 / 多余 / 非法格式代码忽略，未提及记录保持原相对顺序。

    成功返回重排后的完整记录列表；codes 非列表抛中文 ValueError；写失败返回 False。
    """
    if not isinstance(codes, list):
        raise ValueError("codes 必须为股票代码列表")
    wanted = [str(c).strip() for c in codes if _CODE_RE.match(str(c).strip())]
    with _LOCK:
        records = _load()
        by_code = {item["code"]: item for item in records}
        ordered = [by_code[c] for c in wanted if c in by_code]
        seen = {item["code"] for item in ordered}
        ordered.extend(item for item in records if item["code"] not in seen)
        if not _save(ordered):
            return False
    return ordered
