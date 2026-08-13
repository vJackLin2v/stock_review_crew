"""R12 账户存储：data/accounts/{skill_id}/{account.json, trades.json, snapshots.json}。

数据根可用 STOCK_REVIEW_DATA_DIR 覆盖（测试隔离）；读损坏容错（重建并标注），
绝不抛异常。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ACCOUNT_ROOT = "accounts"


def accounts_root() -> Path:
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    base = Path(override) if override else Path(__file__).resolve().parents[3] / "data"
    return base / _ACCOUNT_ROOT


def account_dir(skill_id: str) -> Path:
    return accounts_root() / str(skill_id or "unknown")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 损坏文件重建
        pass
    return default


def load_account(skill_id: str) -> dict:
    """读取账户；不存在/损坏 → 返回 None 标记（调用方负责开户）。"""
    path = account_dir(skill_id) / "account.json"
    return _read_json(path, None)


def load_trades(skill_id: str) -> list[dict]:
    return _read_json(account_dir(skill_id) / "trades.json", [])


def load_snapshots(skill_id: str) -> list[dict]:
    return _read_json(account_dir(skill_id) / "snapshots.json", [])


def load_pending(skill_id: str) -> list[dict]:
    """读取待执行推荐（复盘日记录，次日撮合）；损坏/缺失 → []。"""
    return _read_json(account_dir(skill_id) / "pending.json", [])


def save_pending(skill_id: str, pending: list[dict]) -> Path:
    """落盘待执行推荐（pending.json）。返回账户目录。"""
    d = account_dir(skill_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "pending.json").write_text(
        json.dumps(pending or [], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return d


def save_account(skill_id: str, acct: dict) -> Path:
    """落盘账户（含 trades/snapshots 合并字段）。返回账户目录。"""
    d = account_dir(skill_id)
    d.mkdir(parents=True, exist_ok=True)
    payload = dict(acct)
    payload.pop("trades", None)
    payload.pop("snapshots", None)
    (d / "account.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (d / "trades.json").write_text(
        json.dumps(acct.get("trades") or [], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (d / "snapshots.json").write_text(
        json.dumps(acct.get("snapshots") or [], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return d
