"""模拟交易设置路由：分析师初始资金（GET/PUT /api/settings/accounts）。

2026-08-17：初始资金从 engine.INITIAL_CASH 常量迁移为可配置设置，
存储于 <数据根>/settings/accounts.json（尊重 STOCK_REVIEW_DATA_DIR 测试隔离）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_review_crew.accounts import storage as acct_storage  # noqa: E402
from stock_review_crew.skills import SKILLS_DIR, load_skill  # noqa: E402

router = APIRouter()

MIN_INITIAL_CASH = 10_000.0
MAX_INITIAL_CASH = 1_000_000_000.0


@router.get("/api/settings/accounts")
def get_account_settings() -> dict[str, Any]:
    return {
        "initial_cash": acct_storage.load_initial_cash(),
        "min": MIN_INITIAL_CASH,
        "max": MAX_INITIAL_CASH,
    }


@router.put("/api/settings/accounts")
def update_account_settings(payload: Any = Body(...)) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    try:
        value = float(payload.get("initial_cash"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="initial_cash 必须是数字") from None
    if value < MIN_INITIAL_CASH or value > MAX_INITIAL_CASH:
        raise HTTPException(
            status_code=400,
            detail=f"initial_cash 必须在 {MIN_INITIAL_CASH:,.0f} - {MAX_INITIAL_CASH:,.0f} 元之间",
        )
    path = acct_storage.save_initial_cash(value)
    return {"ok": True, "initial_cash": round(value, 2), "path": str(path)}


# ---------------------------------------------------------------------------
# 分析师提示词管理
# ---------------------------------------------------------------------------


LLM_MODEL_OPTIONS = [
    "deepseek-v4-flash",
    "deepseek-v4",
    "deepseek-r1-0528",
    "deepseek-chat",
    "deepseek-reasoner",
]


@router.get("/api/settings/llm")
def get_llm_settings() -> dict:
    """读取当前 LLM 模型与可选模型列表。"""
    from stock_review_crew import config
    return {
        "model": config.DEEPSEEK_MODEL,
        "options": config.LLM_MODEL_OPTIONS,
    }


@router.put("/api/settings/llm")
def update_llm_settings(payload: Any = Body(...)) -> dict:
    """保存并立即切换 LLM 模型。"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    model = str(payload.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model 不能为空")
    try:
        from stock_review_crew import config
        return config.set_llm_model(model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"切换模型失败：{exc}") from exc

_SKILL_TEXT_FIELDS = ("prompt", "style_anchor", "trading_model")


def _skill_list() -> list[dict[str, Any]]:
    if not SKILLS_DIR.is_dir():
        return []
    items = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        f = d / "skill.json"
        if not f.exists():
            continue
        try:
            data = load_skill(d.name) or {}
        except Exception as exc:  # noqa: BLE001 - 单个 skill 损坏不阻塞列表
            data = {"id": d.name, "error": str(exc)}
        if data.get("group") not in ("trend", "sentiment", "both"):
            continue
        items.append({
            "id": d.name,
            "name": data.get("name") or d.name,
            "role": data.get("role") or "",
            "group": data.get("group") or "",
            "prompt": data.get("prompt") or "",
            "style_anchor": data.get("style_anchor") or "",
            "trading_model": data.get("trading_model") or "",
            "trading_params": data.get("trading_params") or {},
        })
    return items


@router.get("/api/skills")
def list_skills_api() -> dict[str, Any]:
    """查看所有分析师提示词配置（阿狼/拔小弦/爱在冰川/铁锤/炒股养家）。"""
    return {"skills": _skill_list()}


@router.put("/api/skills/{skill_id}")
def update_skill_api(skill_id: str, payload: Any = Body(...)) -> dict[str, Any]:
    """修改分析师提示词文本字段：prompt / style_anchor / trading_model。"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    skill_path = SKILLS_DIR / str(skill_id) / "skill.json"
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"分析师 {skill_id} 不存在")
    try:
        data = load_skill(skill_id) or {}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"读取 skill.json 失败：{exc}") from exc
    for field in _SKILL_TEXT_FIELDS:
        if field in payload:
            value = payload.get(field)
            if not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"{field} 必须是字符串")
            data[field] = value
    try:
        skill_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"写入 skill.json 失败：{exc}") from exc
    return {"ok": True, "skill_id": str(skill_id), "updated_fields": list(payload.keys())}

