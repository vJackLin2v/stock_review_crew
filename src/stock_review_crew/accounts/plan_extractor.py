# -*- coding: utf-8 -*-
"""R15 自动下单文本解析统一入口：graph._llm_analyst 三段正则归一（P2-8）。

原 graph.py 内三段几乎相同的正则（qty式 / 成数式 / 结构化成数式）合并为
单个 _PLAN_RE + extract_plans()，行为与旧逻辑保持一致：

- side 判定：有「买入/卖出」前缀用前缀；无前缀用价格关键字（买入价→buy、卖出价→sell）；
- qty/cheng 互斥：匹配「N股」→ qty 语义（source=regex_qty），
  匹配「仓位N成」→ 成数语义（source=regex_cheng）；
- 买入可选条件单字段（requirements-trading-v1 §三/§六）：止损XX.XX元 / 止盈XX.XX元 /
  持有N天 / 回落N%（或 回落N个百分点，→ trailing_drawdown = N/100）；
  解析不到保持 None（撮合入口按引擎默认计算）；
- 买入/卖出可选「原因是XXX」→ 计划 reason 字段（撮合写入 trade.buy_reason）；
  捕获不到保持空串（plan 库按 None 归一）；
- qty 关键字（买入N股/卖出N股）出现时须与 side 一致（旧 qty 式要求，防止矛盾输入入库）；
- 卖出且 code 不在 held_codes → 跳过并记录 {"type":"sell_skipped_no_position",...}；
- 通过 accounts.plan.add 写入 plan registry（复用校验/去重/归一），单笔失败不阻塞；
- 返回 tool_log 条目列表（place_order_auto / place_order_auto_qty / sell_skipped_no_position）。

数据字典：docs/requirements-r14.md §四.4.1（plans[] 字段唯一真源，plan.py 同步）。
"""

from __future__ import annotations

import re
from typing import Iterable, List

from . import plan as _plan_module

# 统一正则：可选 买入/卖出 前缀 + 6位代码 + [可选名称] + 价格 + (股数 | 成数)，
# 可选条件单字段（止损/止盈/回落/持有天数）+ 可选「原因是XXX」理由。
# reason 捕获到 下一个代码/终止标点/行尾（防多计划粘连，跨行重置逐计划取）。
_PLAN_RE = re.compile(
    r"(?:(?P<prefix>买入|卖出)\s*)?"
    r"(?P<code>\d{6})\s*(?:(?P<name>[^\s：:，。]{1,12})\s*)?[：:]\s*"
    r"(?P<price_kw>买入价|卖出价)\s*(?P<price>\d+(?:\.\d+)?)\s*元"
    r"[，,，\s]*(?:(?:(?P<qty_kw>买入|卖出)\s*)?(?P<qty>\d+)\s*股|仓位\s*(?P<cheng>\d+)\s*成)"
    r"(?:\s*[，,，\s]*止损\s*(?P<stop_loss>\d+(?:\.\d+)?)\s*元)?"
    r"(?:\s*[，,，\s]*止盈\s*(?P<take_profit>\d+(?:\.\d+)?)\s*元)?"
    r"(?:\s*[，,，\s]*回落\s*(?P<trailing_drawdown>\d+(?:\.\d+)?)\s*(?:%|个百分点))?"
    r"(?:\s*[，,，\s]*持有\s*(?P<max_hold_days>\d+)\s*天)?"
    # 原因（可选）：原因是XXX —— XXX 到 终止标点（，。；,;\n）/下一个计划代码（防多计划粘连）
    r"(?:\s*[，,，\s]*原因是\s*(?P<reason>(?:(?!\d{6}\s*[：:]\s*(?:买入价|卖出价))[^，。；;,\n]){0,60})?)?"
)


def _plan_add(payload: dict) -> dict:
    """调用期属性查找 plan.add —— 不能导入期绑定（`from .plan import add` 会把
    函数对象固定在导入时刻）：测试以 monkeypatch 替换
    `stock_review_crew.accounts.plan.add` 时，本调用必须同步命中替换后的函数。"""
    return _plan_module.add(payload)


def _side_for(match: re.Match) -> str:
    """side 判定：有「买入/卖出」前缀用前缀；无前缀用价格关键字。"""
    prefix = match.group("prefix")
    if prefix:
        return "buy" if prefix == "买入" else "sell"
    price_kw = match.group("price_kw")
    return "buy" if price_kw == "买入价" else "sell"


def _conditional_fields(match: re.Match, side: str) -> dict:
    """买入计划可选条件单字段（止损/止盈/持有天数/移动止盈回落），解析不到保持 None。
    非买入(side == sell)计划一律 None（卖出计划不新增字段）。"""
    if side != "buy":
        return {"stop_loss": None, "take_profit": None, "max_hold_days": None,
                "trailing_drawdown": None}
    sl = match.group("stop_loss")
    tp = match.group("take_profit")
    mh = match.group("max_hold_days")
    tdd = match.group("trailing_drawdown")
    return {
        "stop_loss": float(sl) if sl is not None else None,
        "take_profit": float(tp) if tp is not None else None,
        "max_hold_days": int(mh) if mh is not None else None,
        # 回落 N% / N 个百分点 → 比率小数 N/100（0.05=5 个百分点）
        "trailing_drawdown": (float(tdd) / 100.0) if tdd is not None else None,
    }


def _reason_for(match: re.Match) -> str:
    """提取「原因是XXX」（可选）：捕获不到 → 空串（plan 库按 None 归一）。"""
    text = (match.group("reason") or "").strip()
    return text


def extract_plans(
    text: str,
    rec_date: str,
    skill_id: str,
    skill_name: str,
    held_codes: Iterable[str],
) -> List[dict]:
    """解析分析师文本中的买卖计划，返回 tool_log 条目列表。

    side 判定：有「买入/卖出」前缀用前缀；无前缀用价格关键字（买入价→buy、卖出价→sell）。
    qty/cheng：匹配到「N股」→ qty=N、position_cheng=0、source="regex_qty"；
    匹配到「仓位N成」→ position_cheng=N、qty=None、source="regex_cheng"。
    卖出时 code 不在 held_codes → 跳过并返回 {"type":"sell_skipped_no_position",...}。
    通过 _plan_add 写入 plan registry（复用去重），返回 place_order_auto/place_order_auto_qty 条目。
    """
    tool_log: List[dict] = []
    held = {str(c).zfill(6) for c in (held_codes or ())}
    for m in _PLAN_RE.finditer(text or ""):
        side = _side_for(m)
        code = m.group("code")
        name = m.group("name") or code
        price = m.group("price")
        qty = m.group("qty")
        cheng = m.group("cheng")
        qty_kw = m.group("qty_kw")
        # 与旧 qty 式一致：出现「买入N股/卖出N股」关键字时须与 side 一致，
        # 矛盾输入（如 买入价+卖出N股）不落库
        if qty_kw is not None and side != ("buy" if qty_kw == "买入" else "sell"):
            continue
        if side == "sell" and code.zfill(6) not in held:
            tool_log.append(
                {"type": "sell_skipped_no_position", "code": code, "reason": "无持仓，跳过卖出计划"}
            )
            continue
        try:
            if qty is not None:
                result = _plan_add(
                    {
                        "rec_date": rec_date,
                        "skill_id": skill_id,
                        "skill_name": skill_name,
                        "code": code,
                        "name": name,
                        "side": side,
                        "target_price": float(price),
                        "position_cheng": 0,
                        "qty": int(float(qty)),
                        "source": "regex_qty",
                        "reason": _reason_for(m),
                        **_conditional_fields(m, side),
                    }
                )
                tool_log.append(
                    {"type": "place_order_auto_qty", "code": code, "side": side, "result": str(result)[:200]}
                )
            else:
                result = _plan_add(
                    {
                        "rec_date": rec_date,
                        "skill_id": skill_id,
                        "skill_name": skill_name,
                        "code": code,
                        "name": name,
                        "side": side,
                        "target_price": float(price),
                        "position_cheng": float(cheng),
                        "qty": None,
                        "source": "regex_cheng",
                        "reason": _reason_for(m),
                        **_conditional_fields(m, side),
                    }
                )
                tool_log.append(
                    {"type": "place_order_auto", "code": code, "side": side, "result": str(result)[:200]}
                )
        except BaseException:  # noqa: BLE001 - 单笔失败不阻塞
            continue
    return tool_log
