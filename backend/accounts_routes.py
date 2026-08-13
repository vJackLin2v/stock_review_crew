"""R12 模拟账户 API（APIRouter prefix=/api/accounts，由主 Agent include_router 注册）。

GET /api/accounts            → 每分析师摘要列表
GET /api/accounts/{skill_id} → 完整（账户/持仓/交易/快照/收益率）
错误一律中文；未知 skill_id 自动开户。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from stock_review_crew.accounts import engine as acct_engine
from stock_review_crew.accounts import storage as acct_storage

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

_DISCLAIMER = "仅供参考，不构成投资建议"


def _skill_names() -> dict[str, str]:
    """skill_id → 分析师名（复用 skills 目录；仅分析师角色——有 group 字段，
    排除 host/review_assistant 等非交易角色）。"""
    try:
        from stock_review_crew.skills import list_skills

        return {
            s.get("id", ""): str(s.get("name") or s.get("id", ""))
            for s in list_skills()
            if s.get("group") in ("trend", "sentiment", "both")
        }
    except Exception:  # noqa: BLE001
        return {}


def _load_or_open(skill_id: str) -> dict:
    acct = acct_storage.load_account(skill_id)
    if acct is None:
        acct = acct_engine.open_account(skill_id)
        acct["trades"] = []
        acct["snapshots"] = []
        acct_storage.save_account(skill_id, acct)
    else:
        acct["trades"] = acct_storage.load_trades(skill_id)
        acct["snapshots"] = acct_storage.load_snapshots(skill_id)
    return acct


def _historical_close_prices(acct: dict) -> dict[str, float]:
    """取最近结算日快照里每只持仓的收盘价（历史价，与 total_assets 同源）；
    快照缺失或旧快照无 positions 字段时返回空 dict（展示层按成本价兜底并标注）。"""
    snapshots = acct.get("snapshots") or []
    if not snapshots:
        return {}
    snap_positions = snapshots[-1].get("positions") or {}
    prices: dict[str, float] = {}
    for code, p in snap_positions.items():
        close = p.get("close")
        if close is not None:
            prices[str(code)] = float(close)
    return prices


def _position_rows(acct: dict, close_prices: dict | None = None) -> list[dict]:
    """持仓行：现价/市值/浮盈按**最近结算日快照的历史收盘价**估值（2026-08-13 修复：
    不再调用实时行情，保证与 total_assets 同源一致）；历史价缺失（旧快照无 positions）
    时回退成本价并标注 price_estimated=True，绝不明示为真实市价。"""
    rows = []
    if close_prices is None:
        close_prices = _historical_close_prices(acct)
    for code, pos in (acct.get("positions") or {}).items():
        price = close_prices.get(code)
        price_estimated = price is None
        if price is None:
            price = pos.get("avg_cost")
        market_value = round(float(price or 0) * pos["qty"], 6) if price is not None else None
        cost = round(pos.get("avg_cost", 0) * pos["qty"], 6)
        pnl = round(market_value - cost, 6) if market_value is not None else None
        rows.append({
            "code": code,
            "name": pos.get("name") or code,
            "qty": pos.get("qty", 0),
            "available_qty": pos.get("qty", 0) - pos.get("frozen_qty", 0),
            "avg_cost": pos.get("avg_cost"),
            "price": price,
            "market_value": market_value,
            "pnl": pnl,
            "pnl_rate": round(pnl / cost, 6) if pnl is not None and cost else None,
            "price_estimated": price_estimated,
        })
    return rows


def _summary(skill_id: str, acct: dict, names: dict) -> dict:
    snapshots = acct.get("snapshots") or []
    init = acct_engine.INITIAL_CASH
    # 2026-08-12（用户反馈：收益率曲线与累计收益率对不上）：
    # 对齐同花顺口径——累计收益率 = (总资产-期初)/期初，总资产按**最近收盘快照**估值
    # （与收益率曲线终点同源，恒一致）。
    # 2026-08-13（持仓现价用实时价而非历史价）：持仓浮盈同样按最近结算日快照的
    # 历史收盘价计算（= Σ(市值-成本价×数量)），与 total_assets 同源一致，不再调用实时接口。
    close_prices = _historical_close_prices(acct)
    rows = _position_rows(acct, close_prices)
    unrealized = sum((r.get("pnl") or 0.0) for r in rows)
    total = round(float(snapshots[-1]["total_assets"]), 6) if snapshots \
        else round(acct.get("cash", 0.0), 6)
    stats = acct_engine.trade_stats(acct)
    today = str(snapshots[-1]["date"]) if snapshots else ""
    today_trades = [t for t in (acct.get("trades") or []) if t.get("date") == today]
    # 历史价缺失标注（降级链，绝不静默错报）：无常快照 / 旧快照无 positions / 快照内收盘价缺失
    degraded = []
    if acct.get("positions"):
        if not snapshots:
            degraded.append("无历史快照，持仓现价/浮盈按成本价估值")
        elif not snapshots[-1].get("positions"):
            degraded.append("历史价缺失（旧快照未存持仓收盘价），持仓现价/浮盈按成本价估值")
        elif snapshots[-1].get("estimated_prices"):
            degraded.append("部分持仓结算日收盘价缺失，已按成本价估值")
    return {
        "skill_id": skill_id,
        "name": names.get(skill_id, skill_id),
        "cash": round(acct.get("cash", 0.0), 6),
        "total_assets": round(total, 6),
        "total_return_rate": round((total - init) / init, 6),
        "unrealized_pnl": round(unrealized, 6),
        "realized_pnl": round(acct.get("realized_pnl", 0.0), 6),
        "win_rate": stats.get("win_rate"),
        "profit_loss_ratio": stats.get("profit_loss_ratio"),
        "avg_holding_days": stats.get("avg_holding_days"),
        "today_trades_count": len([t for t in today_trades if t.get("status") == "成交"]),
        "today_rejected_count": len([t for t in today_trades if t.get("status") == "拒绝"]),
        "updated_at": today,
        "price_estimated": any(r.get("price_estimated") for r in rows),
        "degraded": degraded,
    }


@router.get("")
def list_accounts() -> dict[str, Any]:
    """全部分析师摘要（按 skills 目录遍历，缺失自动开户）。"""
    names = _skill_names()
    items = []
    for skill_id in sorted(names.keys()):
        acct = _load_or_open(skill_id)
        items.append(_summary(skill_id, acct, names))
    return {"accounts": items, "disclaimer": _DISCLAIMER}


@router.get("/{skill_id}")
def account_detail(skill_id: str) -> dict[str, Any]:
    """单人完整账户（未知 skill_id 自动开户）。"""
    acct = _load_or_open(skill_id)
    names = _skill_names()
    out = _summary(skill_id, acct, names)
    out.update({
        "positions": _position_rows(acct),
        # 2026-08-13：历史持仓（已全部卖出的股票及其完整买卖周期与盈亏）
        "closed_positions": acct_engine.closed_trades(acct),
        "trades": list(reversed((acct.get("trades") or [])[-100:])),
        "snapshots": (acct.get("snapshots") or [])[-120:],
        "disclaimer": _DISCLAIMER,
    })
    return out
