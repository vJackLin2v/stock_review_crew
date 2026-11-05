"""R12 账户存储：data/accounts/{skill_id}/{account.json, trades.json, snapshots.json}。

数据根可用 STOCK_REVIEW_DATA_DIR 覆盖（测试隔离）；读损坏容错（重建并标注），
绝不抛异常。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ACCOUNT_ROOT = "accounts"
_SETTINGS_ROOT = "settings"
DEFAULT_INITIAL_CASH = 100_000.0


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


def settings_root() -> Path:
    """模拟交易设置根目录：<数据根>/settings。"""
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    base = Path(override) if override else Path(__file__).resolve().parents[3] / "data"
    return base / _SETTINGS_ROOT


def accounts_settings_path() -> Path:
    return settings_root() / "accounts.json"


def load_initial_cash() -> float:
    """读取分析师初始资金；缺失/损坏/非法 → DEFAULT_INITIAL_CASH（绝不抛异常）。"""
    path = accounts_settings_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                value = float(data.get("initial_cash", DEFAULT_INITIAL_CASH))
                if value > 0:
                    return value
    except Exception:  # noqa: BLE001 - 设置损坏按默认值
        pass
    return DEFAULT_INITIAL_CASH


def save_initial_cash(value: float) -> Path:
    """写入分析师初始资金设置。返回设置文件路径。"""
    d = settings_root()
    d.mkdir(parents=True, exist_ok=True)
    path = accounts_settings_path()
    path.write_text(
        json.dumps({"initial_cash": round(float(value), 2)}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return path


def llm_settings_path() -> Path:
    """LLM 模型设置文件：<数据根>/settings/llm.json。"""
    return settings_root() / "llm.json"


def load_llm_model(default: str = "") -> str:
    """读取当前 LLM 模型；缺失/损坏 → default（绝不抛异常）。"""
    path = llm_settings_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("model"):
                return str(data["model"]).strip()
    except Exception:  # noqa: BLE001 - 设置损坏按默认值
        pass
    return default or ""


def save_llm_model(model: str) -> Path:
    """写入当前 LLM 模型设置。返回设置文件路径。"""
    d = settings_root()
    d.mkdir(parents=True, exist_ok=True)
    path = llm_settings_path()
    path.write_text(
        json.dumps({"model": str(model).strip()}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return path

