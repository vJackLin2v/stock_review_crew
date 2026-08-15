# -*- coding: utf-8 -*-
"""R14 交易计划唯一真源：data/reviews/{date}/plan.json（A1 计划层）。

数据字典（docs/requirements-r14.md §四.4.1，字段唯一真源）：
- plan.json = {date, mode, generated_at, plans[]}；同日重跑整文件覆盖；
- plans[].rec_date / skill_id / skill_name / code / name / side /
  target_price / position_cheng / qty / source；qty=None 序列化为 JSON null；
- plans[].stop_loss / take_profit / max_hold_days / trailing_drawdown（可选出条件字段，
  requirements-trading-v1 §三/§六）；stop_loss/take_profit/max_hold_days 均可缺省，
  缺省在撮合入口按 买入价×0.95 / 买入价×1.10 / 13 计算；trailing_drawdown 提供 → 移动
  止盈（None → 固定止盈）；提供则须 >0 且 止损 < 买入价 < 止盈；max_hold_days 为正整数；
  trailing_drawdown 须 0<x<1；None 序列化为 JSON null；
- 去重键：(rec_date, skill_id, code, side, target_price)，target_price 容差 <0.01
  （2026-08-10 修复过重复提取，语义必须保留）；
- source 枚举：tool / regex_qty / regex_cheng；skill_id 排除 host/review_assistant；
- name 缺失落盘用 code；code 短数字 zfill(6)。

API（A4 测试契约 + graph/backend 使用）：
- PlanRec：dataclass（from_dict 归一 + 校验 / to_dict 序列化 / same_as 去重语义）；
- validate_plan(rec) -> list[str]：空列表 = 合法（校验器，不做归一）；
- dedup_plans(recs) -> list[PlanRec]：按去重键稳定保留首条（对象恒等）；
- add_plan(rec) -> bool：线程安全 registry 新增（True）/ 重复或非法（False）；
- add(rec: PlanRec | dict, source=...) -> dict：graph place_order 工具契约
  {ok, reason, trade}（同时接受已构造的 PlanRec 与原始 dict）；
- finalize(date, mode="close") -> Path：registry 单点整文件覆盖落盘；
- save_plan(date, recs, mode="close") -> Path：直接整文件覆盖写（并发安全）；
- load_plan(date) -> dict | None：缺失/损坏返回 None，绝不抛异常；
- load(date) -> dict：缺失/损坏返回空信封 {date, mode, generated_at: None, plans: []}。

设计：
- 线程安全 registry（threading.Lock）：graph 五分析师并行 add_plan，finalize 单点
  落盘，避免文件并发写；save_plan 写文件走独立 FILE_LOCK（整文件覆盖原子语义）；
- 数据根尊重 STOCK_REVIEW_DATA_DIR：覆盖时落在 {DATA_DIR}/reviews/{date}/plan.json
  （与默认 data/reviews/{date}/plan.json 结构一致，A4 测试契约），否则复用
  storage.reviews.reviews_root()；
- add/add_plan 校验失败不抛异常（返回 ok:False / False，LLM 可获反馈）；
- finalize 单条坏数据跳过、绝不阻塞；写成功即清空该日期 registry。

仅此模块 + graph.py + backend/main.py 写入 plan.json；accounts/engine.py 撮合口径
冻结（R12），本模块不触碰账户引擎。
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from ..storage.reviews import reviews_root

__all__ = [
    "SOURCES",
    "EXCLUDED_SKILLS",
    "PlanRec",
    "validate_plan",
    "dedup_plans",
    "add_plan",
    "add",
    "finalize",
    "save_plan",
    "load_plan",
    "load",
    "reset",
    "plan_path",
]

# source 枚举（数据字典 §四.4.1）
SOURCES = ("tool", "regex_qty", "regex_cheng")
# 排除 host/review_assistant（数据字典 plans[].skill_id 边界）
EXCLUDED_SKILLS = ("host", "review_assistant")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PRICE_TOLERANCE = 0.01

# 线程安全 registry：{date: [PlanRec, ...]}（有序、去重后）
_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()
_REGISTRY: dict[str, list["PlanRec"]] = {}


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


def _plan_root() -> Path:
    """plan.json 根目录：默认 data/reviews；STOCK_REVIEW_DATA_DIR 覆盖时
    {DATA_DIR}/reviews（与数据字典 data/reviews/{date}/plan.json 结构一致，
    测试隔离契约见 tests/test_account_plan.py）。"""
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    if override:
        return Path(override) / "reviews"
    return reviews_root()


@dataclass
class PlanRec:
    """单条交易计划（数据字典 §四.4.1 plans[] 字段）。"""

    rec_date: str
    skill_id: str
    code: str
    side: str
    target_price: float
    skill_name: str = ""
    name: str = ""
    position_cheng: float = 0.0
    qty: Optional[int] = None
    source: str = "tool"
    # 条件单字段（requirements-trading-v1 §三，均可选，None=走引擎默认）：
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_hold_days: Optional[int] = None
    # 移动止盈（requirements-trading-v1 §六，可选）：比率小数（0.05=5 个百分点）。
    # 提供 → 触发移动止盈；None → 走固定止盈。
    trailing_drawdown: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "PlanRec":
        """从原始 dict 构造；非法字段抛中文 ValueError（写接口显式失败，绝不静默入库）。"""
        if not isinstance(d, dict):
            raise ValueError("计划记录必须是对象")
        rec = cls(
            rec_date=str(d.get("rec_date") or "").strip(),
            skill_id=str(d.get("skill_id") or "").strip(),
            skill_name=str(d.get("skill_name") or "").strip(),
            code=str(d.get("code") or "").strip(),
            name=str(d.get("name") or "").strip(),
            side=str(d.get("side") or "").strip(),
            target_price=_as_float(d.get("target_price"), "target_price"),
            position_cheng=_as_float(d.get("position_cheng", 0) or 0, "position_cheng"),
            qty=_as_qty(d.get("qty")),
            source=str(d.get("source") or "").strip(),
            stop_loss=_as_optional_float(d.get("stop_loss"), "stop_loss"),
            take_profit=_as_optional_float(d.get("take_profit"), "take_profit"),
            max_hold_days=_as_optional_int(d.get("max_hold_days"), "max_hold_days"),
            trailing_drawdown=_as_optional_float(
                d.get("trailing_drawdown"), "trailing_drawdown"),
        )
        errors = validate_plan(rec)
        if errors:
            raise ValueError("；".join(errors))
        # 语义归一（数据字典）：code zfill(6)、name 缺失用 code、skill_name 缺失用
        # skill_id、position_cheng 整值归一、qty 整值归一、max_hold_days 整值归一
        rec.code = rec.code.zfill(6)
        if not rec.name:
            rec.name = rec.code
        if not rec.skill_name:
            rec.skill_name = rec.skill_id
        if rec.position_cheng == int(rec.position_cheng):
            rec.position_cheng = int(rec.position_cheng)
        if rec.qty is not None:
            rec.qty = int(rec.qty)
        if rec.max_hold_days is not None:
            rec.max_hold_days = int(rec.max_hold_days)
        return rec

    def to_dict(self) -> dict:
        """序列化为 plan.json plans[] 条目（None 语义：qty/条件单字段=null）。"""
        return {
            "rec_date": self.rec_date,
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "code": self.code,
            "name": self.name,
            "side": self.side,
            "target_price": self.target_price,
            "position_cheng": self.position_cheng,
            "qty": self.qty,
            "source": self.source,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "max_hold_days": self.max_hold_days,
            "trailing_drawdown": self.trailing_drawdown,
        }

    def same_as(self, other: "PlanRec") -> bool:
        """去重语义（2026-08-10 修复）：同 rec_date/skill_id/code/side，
        target_price 差 <0.01 视为同一计划（来源不同不算新计划）。"""
        return (
            self.rec_date == other.rec_date
            and self.skill_id == other.skill_id
            and self.code == other.code
            and self.side == other.side
            and abs(self.target_price - other.target_price) < _PRICE_TOLERANCE
        )


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数字，收到：{value!r}") from exc


def _as_optional_float(value: Any, field: str) -> Optional[float]:
    """可选数字：None/空串/缺失 → None；其余按数字转换（校验在 validate_plan）。"""
    if value is None or str(value).strip() == "":
        return None
    return _as_float(value, field)


def _as_optional_int(value: Any, field: str) -> Optional[int]:
    """可选整数：None/空串/缺失 → None；其余按整数转换（校验在 validate_plan）。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是整数，收到：{value!r}") from exc


def _as_qty(value: Any) -> Optional[float]:
    """qty 可选：None/空串 → None；其余按数字转换（校验在 validate_plan）。"""
    if value is None or str(value).strip() == "":
        return None
    return _as_float(value, "qty")


def validate_plan(rec: PlanRec) -> list[str]:
    """校验单条计划（数据字典边界）；返回错误列表，空列表 = 合法。

    只校验原始字段（不做归一）：side 不接受大写、source 不接受空串等。
    """
    errors: list[str] = []
    if not isinstance(rec, PlanRec):
        return ["必须是 PlanRec 对象"]
    if not _valid_date(rec.rec_date):
        errors.append(f"rec_date 必须为 YYYY-MM-DD，收到：{rec.rec_date!r}")
    if not rec.skill_id:
        errors.append("skill_id 不能为空")
    elif rec.skill_id in EXCLUDED_SKILLS:
        errors.append(f"skill_id 不允许为 {rec.skill_id}（排除 host/review_assistant）")
    code = str(rec.code or "").strip()
    if not code.isdigit() or len(code.zfill(6)) != 6:
        errors.append(f"code 必须为 6 位数字（短数字自动补零），收到：{code!r}")
    if rec.side not in ("buy", "sell"):
        errors.append(f"side 必须为 buy/sell，收到：{rec.side!r}")
    try:
        price = float(rec.target_price)
        if price != price or price <= 0:
            errors.append(f"target_price 必须 > 0，收到：{rec.target_price!r}")
    except (TypeError, ValueError):
        errors.append(f"target_price 必须是正数，收到：{rec.target_price!r}")
    try:
        cheng = float(rec.position_cheng)
        if cheng != cheng or cheng < 0 or cheng > 10:
            errors.append(f"position_cheng 必须在 0-10 之间，收到：{rec.position_cheng!r}")
        elif 0 < cheng < 1:
            errors.append(f"position_cheng 必须在 1-10 之间（0 表示按股数），收到：{rec.position_cheng!r}")
        elif cheng == 0:
            qty_ok = False
            try:
                qty_ok = rec.qty is not None and float(rec.qty) > 0
            except (TypeError, ValueError):
                qty_ok = False
            if not qty_ok:
                errors.append("position_cheng=0 时须提供 qty>0（工具传 0 时用 qty）")
    except (TypeError, ValueError):
        errors.append(f"position_cheng 必须是数字（0-10），收到：{rec.position_cheng!r}")
    if rec.qty is not None:
        try:
            qf = float(rec.qty)
            if qf != qf or qf <= 0 or abs(qf - int(qf)) > 1e-6:
                errors.append(f"qty 必须为正整数，收到：{rec.qty!r}")
        except (TypeError, ValueError):
            errors.append(f"qty 必须为正整数，收到：{rec.qty!r}")
    if rec.source not in SOURCES:
        errors.append(f"source 必须为 {'/'.join(SOURCES)}，收到：{rec.source!r}")
    # 条件单字段（requirements-trading-v1 §三）：可选；提供则须 >0 且
    # 止损 < 买入价 < 止盈；max_hold_days 为正整数
    if rec.side == "buy":
        try:
            tp = float(rec.target_price)
        except (TypeError, ValueError):
            tp = 0.0
        if rec.stop_loss is not None:
            try:
                sl = float(rec.stop_loss)
                if sl != sl or sl <= 0:
                    errors.append(f"stop_loss 必须 > 0，收到：{rec.stop_loss!r}")
                elif tp > 0 and not (sl < tp):
                    errors.append(
                        f"stop_loss 必须 < target_price，收到：stop_loss={rec.stop_loss!r} "
                        f"target_price={rec.target_price!r}")
            except (TypeError, ValueError):
                errors.append(f"stop_loss 必须为正数，收到：{rec.stop_loss!r}")
        if rec.take_profit is not None:
            try:
                tk = float(rec.take_profit)
                if tk != tk or tk <= 0:
                    errors.append(f"take_profit 必须 > 0，收到：{rec.take_profit!r}")
                elif tp > 0 and not (tk > tp):
                    errors.append(
                        f"take_profit 必须 > target_price，收到：take_profit={rec.take_profit!r} "
                        f"target_price={rec.target_price!r}")
            except (TypeError, ValueError):
                errors.append(f"take_profit 必须为正数，收到：{rec.take_profit!r}")
        if rec.max_hold_days is not None:
            mh = rec.max_hold_days
            if not isinstance(mh, int) or mh <= 0:
                errors.append(
                    f"max_hold_days 必须为正整数，收到：{rec.max_hold_days!r}")
        if rec.trailing_drawdown is not None:
            try:
                tdd = float(rec.trailing_drawdown)
                if tdd != tdd or not (0 < tdd < 1):
                    errors.append(
                        f"trailing_drawdown 必须为 0-1 之间的小数（0.05=回落 5 个百分点），"
                        f"收到：{rec.trailing_drawdown!r}")
            except (TypeError, ValueError):
                errors.append(
                    f"trailing_drawdown 必须为 0-1 之间的小数，收到：{rec.trailing_drawdown!r}")
    return errors


def dedup_plans(recs: Sequence[PlanRec]) -> list[PlanRec]:
    """按去重键 (rec_date, skill_id, code, side, target_price) 去重，稳定保留首条。"""
    kept: list[PlanRec] = []
    for rec in recs:
        if not isinstance(rec, PlanRec):
            continue
        if any(prev.same_as(rec) for prev in kept):
            continue
        kept.append(rec)
    return kept


def plan_path(date: str) -> Path:
    """plan.json 路径：{root}/{date}/plan.json（数据根尊重环境变量）。"""
    return _plan_root() / str(date or "") / "plan.json"


def reset(date: str) -> None:
    """清空某日期的 registry（job 开始前调用，保证同日重跑无残留旧计划）。"""
    key = str(date or "")
    with _LOCK:
        _REGISTRY.pop(key, None)


def add_plan(rec: PlanRec) -> bool:
    """线程安全加入计划 registry；True = 新增，False = 重复或非法（不入 registry）。"""
    if not isinstance(rec, PlanRec):
        return False
    if validate_plan(rec):
        return False
    # 归一（zfill/name 兜底/整值）后入 registry
    item = PlanRec.from_dict(rec.to_dict())
    with _LOCK:
        existing = _REGISTRY.setdefault(item.rec_date, [])
        for prev in existing:
            if prev.same_as(item):
                return False
        existing.append(item)
        return True


def add(rec: Any, *, source: Optional[str] = None) -> dict:
    """graph place_order 工具契约：返回 {ok, reason, trade}（校验失败不抛异常）。"""
    try:
        if isinstance(rec, PlanRec):
            item = PlanRec.from_dict(rec.to_dict())
        elif isinstance(rec, dict):
            payload = dict(rec)
            payload.setdefault("source", source or "tool")
            item = PlanRec.from_dict(payload)
        else:
            return {"ok": False, "reason": f"计划参数无效：必须是 PlanRec/dict，收到 {type(rec).__name__}", "trade": None}
    except ValueError as exc:
        return {"ok": False, "reason": f"计划参数无效：{exc}", "trade": None}

    with _LOCK:
        existing = _REGISTRY.setdefault(item.rec_date, [])
        for prev in existing:
            if prev.same_as(item):
                return {
                    "ok": True,
                    "reason": "已存在相同推荐（%s %s 目标价 %.2f 元），跳过重复记录"
                    % (item.code, item.side, item.target_price),
                    "trade": None,
                }
        existing.append(item)

    if item.qty is not None:
        qty_desc = f"{item.qty} 股"
    else:
        qty_desc = f"仓位 {item.position_cheng:.0f} 成"
    return {
        "ok": True,
        "reason": "已记录收盘后推荐：%s %s 目标价 %.2f 元 %s，将在下一交易日按目标价撮合"
        % (item.code, item.side, item.target_price, qty_desc),
        "trade": None,
    }


def save_plan(date: str, recs: Sequence[PlanRec], *, mode: str = "close") -> Path:
    """整文件覆盖写入 plan.json（信封 + plans），并发安全（单次写入结果完整）。

    - 日期非法抛中文 ValueError（写接口显式失败，避免错放路径）；
    - 单条记录非法抛 ValueError（写接口不允许静默跳过）。
    """
    key = str(date or "")
    if not _valid_date(key):
        raise ValueError(f"日期格式必须为 YYYY-MM-DD，收到：{date!r}")
    plans: list[dict] = []
    for r in recs:
        if isinstance(r, PlanRec):
            plans.append(PlanRec.from_dict(r.to_dict()).to_dict())
        elif isinstance(r, dict):
            plans.append(PlanRec.from_dict(r).to_dict())
        else:
            raise ValueError(f"计划记录必须是 PlanRec/dict，收到：{type(r).__name__}")
    payload = {
        "date": key,
        "mode": str(mode or "close"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "plans": plans,
    }
    path = plan_path(key)
    with _FILE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def finalize(date: str, mode: str = "close") -> Path:
    """把该日期 registry 内计划单点整文件覆盖落盘（写成功即清空 registry）。

    单条坏数据跳过、不阻塞整体；同日重跑整文件覆盖，无残留旧计划（验收七.1）。
    """
    key = str(date or "")
    if not _valid_date(key):
        raise ValueError(f"日期格式必须为 YYYY-MM-DD，收到：{date!r}")
    with _LOCK:
        recs = list(_REGISTRY.get(key, []))
    validated: list[PlanRec] = []
    for r in recs:
        try:
            validated.append(PlanRec.from_dict(r.to_dict()))
        except ValueError:
            continue  # 防御：registry 内容应已合法，单条坏数据跳过
    validated.sort(key=lambda r: (r.skill_id, r.code, r.side, round(r.target_price, 2)))
    path = save_plan(key, validated, mode=mode)
    with _LOCK:
        _REGISTRY.pop(key, None)
    return path


def load_plan(date: str) -> Optional[dict]:
    """健壮读取 plan.json；缺失 / 损坏 / 非法日期 → None，绝不抛异常。"""
    if not _valid_date(date):
        return None
    path = plan_path(str(date))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except Exception:  # noqa: BLE001 - 缺失/损坏一律 None（A4 契约）
        return None


def load(date: str) -> dict:
    """健壮读取 plan.json；缺失 / 损坏 / 非法日期 → 空信封（plans=[]），绝不抛异常。"""
    data = load_plan(date)
    if data is None:
        return {"date": str(date or ""), "mode": "close", "generated_at": None, "plans": []}
    return data
