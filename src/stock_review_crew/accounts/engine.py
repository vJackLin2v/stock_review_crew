"""R12 账户引擎：开户 / 下单校验撮合 / 每日快照（口径见 requirements-r12.md，已确认）。

手算基准（验收用例 test_r12_acceptance.py，2026-08-08 冻结）：
- 10 万 × 3 成买入 target=10.00 → 数量 2900 股（折算：floor(cash×成数/10/(target×1.001)/100)×100）；
  佣金 max(29000×0.00025, 5)=7.25、过户费 0.29 → 成交后现金 70,992.46；
- 次日卖出 2900 × 10.50 → 佣金 7.6125、印花税 15.225、过户费 0.3045 → 现金 101,419.318；
- realized_pnl = (卖出价−成本)×数量 − 费用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

# 初始资金（用户确认：每位分析师 10 万元）
INITIAL_CASH = 100_000.0

# 涨跌停系数：前缀 → ±比例（北证/ B 股不支持交易）
_LIMIT_PCT: dict[tuple[str, ...], float] = {
    ("688", "689"): 0.20,   # 科创板
    ("300", "301"): 0.20,   # 创业板
}
_REJECT_PREFIX = ("8", "4", "92")  # 北证 / 三板 / B 股


def _limit_pct(code: str) -> Optional[float]:
    """按代码前缀返回涨跌幅（小数）；北证/B 股返回 None（不支持交易）。"""
    c = str(code or "")
    if c.startswith(_REJECT_PREFIX):
        return None
    for prefixes, pct in _LIMIT_PCT.items():
        if c.startswith(prefixes):
            return pct
    if c.startswith(("0", "6")):
        return 0.10
    return None


def min_buy_unit(code: str) -> int:
    """A 股最小买入单位（股）：
    - 科创板（688/689）：最低 200 股（2 手）起，之后按 100 股递增；
    - 其余主板/创业板：100 股整数倍（1 手）。
    北证/B 股由 _limit_pct 拒绝，不会走到这里。
    """
    c = str(code or "")
    if c.startswith(("688", "689")):
        return 200
    return 100


def round_lot(qty: int, code: str, side: str = "buy") -> int:
    """按 A 股规则取整股数：
    - 买入：科创板最低 200 股起、100 股递增；其余 100 股整数倍；
    - 卖出：100 股整数倍；若持仓含零股（不足 100 股部分）允许一次性卖出。
    """
    qty = max(0, int(qty))
    if qty == 0:
        return 0
    if side == "sell":
        # 卖出：100 股整数倍；不足 100 股的零股允许整仓一次性卖出
        lots = qty // 100
        return lots * 100 if lots > 0 else qty
    # 买入：按 100 股递增
    lots = qty // 100
    qty2 = lots * 100
    unit = min_buy_unit(code)
    if qty2 < unit:
        return 0  # 不足最小单位（科创板 200 股/2 手起）
    return qty2


def _round_half_up(value: float, digits: int = 2) -> float:
    """四舍五入到指定小数位（Decimal ROUND_HALF_UP，避免银行家舍入）。"""
    q = Decimal("0." + "0" * digits) if digits else Decimal("1")
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def limit_prices(pre_close: float, pct: float) -> tuple[float, float]:
    """涨停/跌停价（四舍五入到 0.01）。"""
    return (
        _round_half_up(pre_close * (1.0 + pct)),
        _round_half_up(pre_close * (1.0 - pct)),
    )


def _commission(amount: float) -> float:
    """佣金：万 2.5，最低 5 元（买卖双向）。"""
    return round(max(amount * 0.00025, 5.0), 6)


def _transfer_fee(amount: float) -> float:
    """过户费：0.001%（双向）。"""
    return round(amount * 0.00001, 6)


def _stamp_tax(amount: float) -> float:
    """印花税：卖出 0.05%（2023-08-28 减半后口径）。"""
    return round(amount * 0.0005, 6)


@dataclass
class TradeResult:
    """下单结果：成交或拒绝（拒绝也完整记录，供合规审计）。"""

    ok: bool
    reason: str = ""
    trade: Optional[dict] = None


def open_account(skill_id: str) -> dict:
    """开户：初始资金 10 万、无持仓、无交易。已存在则原样返回。"""
    return {
        "skill_id": skill_id,
        "cash": INITIAL_CASH,
        "positions": {},  # code -> {name, qty, frozen_qty, avg_cost}
        "current_date": None,  # 最近交易日（用于 T+1 冻结结算）
        "realized_pnl": 0.0,
    }


def _settle_day(acct: dict, date: str) -> None:
    """跨交易日结算：当日买入冻结（frozen_qty）转为可用（T+1 到期）。"""
    if acct.get("current_date") == date:
        return
    for pos in acct.get("positions", {}).values():
        pos["frozen_qty"] = 0  # 新交易日，昨日及更早买入全部解冻
    acct["current_date"] = date


def place_order(
    acct: dict,
    code: str,
    name: str,
    side: str,
    target_price: float,
    position_cheng: float,
    day: dict,
    qty: Optional[int] = None,
    *,
    conditional: Optional[dict] = None,
) -> TradeResult:
    """下单（口径用户已确认）：
    - 目标价 ≥ 涨停价 → 拒买（涨停买不进）；目标价 ≤ 跌停价 → 拒卖；
    - 当日价格区间未覆盖目标价（最低 > 目标价 或 最高 < 目标价）→ 拒；
    - T+1：卖出数量 ≤ 可用持仓（总持仓 − 当日买入冻结）；
    - 资金：买入数量 = floor(可用现金×成数/10/(目标价×1.001)/100)×100；
      若显式传 qty（具体股数）则优先使用，并按 A 股最小单位取整；
    - 费用：佣金 万2.5 最低 5 元、过户费 0.001% 双向、印花税 卖出 0.05%；
    - 拒绝也完整记录（合规审计）。绝不抛异常。
    day = {date, pre_close, high, low, close}（当日行情；缺失 → 拒「行情数据不足」）。
    conditional：买入可选条件单字段 {stop_loss, take_profit, max_hold_days}，
    成交时挂到持仓（供 check_conditional_orders 每日机械执行）；
    缺省/为 None → 引擎不挂（调用方负责按默认 0.95/1.10/13 传入）。
    """
    try:
        date = str(day.get("date") or "")
        pre_close = day.get("pre_close")
        high = day.get("high")
        low = day.get("low")
        if pre_close is None or high is None or low is None or not date:
            return TradeResult(False, "行情数据不足（缺少前收/最高/最低）")
        pre_close = float(pre_close)
        high = float(high)
        low = float(low)
        target = float(target_price)
        cheng = float(position_cheng or 0)
        if target <= 0:
            return TradeResult(False, "目标价必须大于 0")
        # 2026-08-12（适配兜底）：显式 qty（如「卖出 000595：卖出价6.26元，卖出3000股」，
        # 文本无成数信息 → position_cheng=0）时，股数已确定、成数不参与计算 → 放宽校验；
        # 无显式 qty 时仍必须提供有效成数（1-10），否则拒绝。
        if not (0 < cheng <= 10) and qty is None:
            return TradeResult(False, f"仓位成数无效：{position_cheng}（应为 1-10 成）")

        pct = _limit_pct(code)
        if pct is None:
            return TradeResult(False, "北证/B 股不支持交易")
        limit_up, limit_down = limit_prices(pre_close, pct)

        # 涨跌停约束
        if side == "buy":
            if target >= limit_up:
                return TradeResult(False, f"目标价 {target:.2f} 不低于涨停价 {limit_up:.2f}，无法买入")
        elif side == "sell":
            if target <= limit_down:
                return TradeResult(False, f"目标价 {target:.2f} 不高于跌停价 {limit_down:.2f}，无法卖出")
        else:
            return TradeResult(False, f"未知方向：{side}")

        # 当日价格区间覆盖目标价才成交
        if target < low or target > high:
            return TradeResult(
                False,
                f"当日未触及目标价 {target:.2f}（区间 {low:.2f}–{high:.2f}）",
            )

        _settle_day(acct, date)
        positions = acct.get("positions", {})
        pos = positions.get(code)

        if side == "buy":
            if qty is not None:
                qty = round_lot(int(qty), code, side="buy")
            else:
                raw_qty = int((acct["cash"] * cheng / 10.0) / (target * 1.001))
                qty = round_lot(raw_qty, code, side="buy")
            if qty <= 0:
                return TradeResult(
                    False,
                    f"可用资金不足或不足最小买入单位（{min_buy_unit(code)} 股起）"
                )
            amount = round(target * qty, 6)
            fee = _commission(amount)
            tf = _transfer_fee(amount)
            total = amount + fee + tf
            # 边界保护（2026-08-10）：金额+费用不得超过可用现金，
            # 超过则逐手（100 股）递减，但不得低于最小买入单位
            # （科创板 688/689 最低 200 股起，2026-08-13 修复：不再递减到 100 股成交）
            _step = 100
            _min_qty = min_buy_unit(code)
            while total > acct["cash"] + 1e-6 and qty - _step >= _min_qty:
                qty -= _step
                amount = round(target * qty, 6)
                fee = _commission(amount)
                tf = _transfer_fee(amount)
                total = amount + fee + tf
            if total > acct["cash"] + 1e-6:
                return TradeResult(
                    False,
                    f"资金不足：需 {total:.2f}，可用 {acct['cash']:.2f}"
                    f"（最小单位 {min_buy_unit(code)} 股起，无法再递减）",
                )
            acct["cash"] = round(acct["cash"] - total, 6)
            cond = conditional or {}
            cond_fields = {
                "stop_loss": cond.get("stop_loss"),
                "take_profit": cond.get("take_profit"),
                "max_hold_days": cond.get("max_hold_days"),
                "buy_date": date,
                "hold_days": 1,  # 买入日=第 1 天；后续结算日由 check_conditional_orders 递增
                "hold_check_date": date,
            }
            if pos is None:
                positions[code] = {
                    "name": str(name or code),
                    "qty": qty,
                    "frozen_qty": qty,  # T+1：当日买入冻结
                    "avg_cost": round(amount / qty, 6),
                    **cond_fields,
                }
            else:
                old_cost = pos["avg_cost"] * pos["qty"]
                pos["qty"] += qty
                pos["frozen_qty"] = pos.get("frozen_qty", 0) + qty
                pos["avg_cost"] = round((old_cost + amount) / pos["qty"], 6)
                # 条件单字段以本次买入计划为准（追加/覆盖绑定）
                for k in ("stop_loss", "take_profit", "max_hold_days"):
                    pos[k] = cond_fields.get(k)
            trade = {
                "date": date, "code": code, "name": positions[code]["name"],
                "side": "buy", "target_price": target, "qty": qty,
                "price": target, "amount": amount, "fee": fee,
                "stamp_tax": 0.0, "transfer_fee": tf,
                "status": "成交", "reject_reason": "", "cash_after": acct["cash"],
            }
            acct.setdefault("trades", []).append(trade)
            return TradeResult(True, "", trade)

        # sell
        if pos is None:
            return TradeResult(False, "无持仓，无法卖出（禁止裸卖空）")
        available = pos["qty"] - pos.get("frozen_qty", 0)
        if qty is not None:
            qty = round_lot(int(qty), code, side="sell")
            if qty > available:
                return TradeResult(
                    False,
                    f"卖出数量 {qty} 超过可卖持仓 {available}（T+1 冻结 {pos.get('frozen_qty', 0)}）",
                )
        else:
            raw_qty = int(available * cheng / 10.0)
            qty = round_lot(raw_qty, code, side="sell")
            # 边界保护：成数折算结果不得超过可卖持仓
            while qty > available and qty > 100:
                qty -= 100
        if qty <= 0:
            return TradeResult(
                False,
                "可卖持仓不足（T+1：当日买入不可卖）" if available <= 0
                else "可卖持仓不足（折算数量为 0）",
            )
        amount = round(target * qty, 6)
        fee = _commission(amount)
        stamp = _stamp_tax(amount)
        tf = _transfer_fee(amount)
        realized = round((target - pos["avg_cost"]) * qty - fee - stamp - tf, 6)
        acct["cash"] = round(acct["cash"] + amount - fee - stamp - tf, 6)
        pos["qty"] -= qty
        pos["frozen_qty"] = max(0, pos.get("frozen_qty", 0))  # 卖出只减 qty，不动 frozen_qty
        acct["realized_pnl"] = round(acct["realized_pnl"] + realized, 6)
        if pos["qty"] <= 0:
            del positions[code]
        trade = {
            "date": date, "code": code, "name": str(name or code),
            "side": "sell", "target_price": target, "qty": qty,
            "price": target, "amount": amount, "fee": fee,
            "stamp_tax": stamp, "transfer_fee": tf,
            "status": "成交", "reject_reason": "", "cash_after": acct["cash"],
            "realized": realized,  # 单笔已实现盈亏（含费用），供胜率/盈亏比统计
        }
        acct.setdefault("trades", []).append(trade)
        return TradeResult(True, "", trade)
    except Exception as exc:  # noqa: BLE001 - 引擎绝不抛异常
        return TradeResult(False, f"下单处理异常：{exc}")


DEFAULT_STOP_LOSS_RATE = 0.95
DEFAULT_TAKE_PROFIT_RATE = 1.10
DEFAULT_MAX_HOLD_DAYS = 13


def apply_conditional_defaults(conditional: Optional[dict], buy_price: float) -> dict:
    """买入计划三字段缺省（requirements-trading-v1 §三.1）：None 字段按
    买入价×0.95 / 买入价×1.10 / 13 补齐。返回 {stop_loss, take_profit, max_hold_days}。"""
    c = conditional or {}
    return {
        "stop_loss": float(c.get("stop_loss")) if c.get("stop_loss") is not None
        else round(buy_price * DEFAULT_STOP_LOSS_RATE, 6),
        "take_profit": float(c.get("take_profit")) if c.get("take_profit") is not None
        else round(buy_price * DEFAULT_TAKE_PROFIT_RATE, 6),
        "max_hold_days": int(c.get("max_hold_days")) if c.get("max_hold_days") is not None
        else DEFAULT_MAX_HOLD_DAYS,
    }


def _one_word_limit_down(quote: dict, code: str) -> bool:
    """一字跌停判定：当日开盘=最低=最高=收盘=跌停价（开板前无法成交）。
    open 缺失（源拿不到）时退化为 低=高=收盘=跌停价（一字封死签名；
    跳空判断按不跳空处理，但封死仍能识别）；绝不抛异常。"""
    try:
        high = float(quote.get("high"))
        low = float(quote.get("low"))
        close = float(quote.get("close"))
        pre_close = quote.get("pre_close")
        if pre_close is None:
            return False
        pct = _limit_pct(code)
        if pct is None:
            return False
        limit_up, limit_down = limit_prices(float(pre_close), pct)
        equal_sealed = (abs(low - close) < 1e-6 and abs(close - high) < 1e-6
                        and abs(close - limit_down) < 1e-6)
        if not equal_sealed:
            return False
        open_ = quote.get("open")
        if open_ is not None:
            return abs(float(open_) - close) < 1e-6
        return True  # open 缺失：低=高=收盘=跌停价已是封死签名
    except Exception:  # noqa: BLE001 - 绝不抛异常
        return False


def _conditional_sell_record(acct: dict, pos: dict, code: str, price: float,
                             qty: int, day: dict, *, reason: str = "") -> dict:
    """条件单卖出落账（与 place_order 卖出相同费用/realized 口径，R12 一致）：
    佣金 万2.5 最低 5、过户费 0.001% 双向、印花税 卖出 0.05%；
    realized = (成交价−avg_cost)×数量−费用；更新 cash/positions/realized_pnl；
    写入 acct["trades"] 并返回 trade 记录。"""
    amount = round(price * qty, 6)
    fee = _commission(amount)
    stamp = _stamp_tax(amount)
    tf = _transfer_fee(amount)
    realized = round((price - pos["avg_cost"]) * qty - fee - stamp - tf, 6)
    acct["cash"] = round(acct["cash"] + amount - fee - stamp - tf, 6)
    pos["qty"] -= qty
    pos["frozen_qty"] = max(0, pos.get("frozen_qty", 0))
    acct["realized_pnl"] = round(acct.get("realized_pnl", 0.0) + realized, 6)
    if pos.get("qty", 0) <= 0:
        acct["positions"].pop(code, None)
    trade = {
        "date": str(day.get("date") or ""), "code": str(code),
        "name": str(pos.get("name") or code), "side": "sell",
        "target_price": float(price), "qty": qty, "price": float(price),
        "amount": amount, "fee": fee, "stamp_tax": stamp, "transfer_fee": tf,
        "status": "成交", "reject_reason": "",
        "cash_after": round(acct["cash"], 6),
        "realized": realized,
        "balance_after": round(acct["cash"], 6),
        "condition": str(reason) if reason else "conditional_order",
    }
    acct.setdefault("trades", []).append(trade)
    return trade


def check_conditional_orders(acct: dict, day: dict, quotes: dict) -> list[dict]:
    """每日条件单机械卖出（requirements-trading-v1 §二.5，B 层引擎核心）：

    对 ``acct["positions"]`` 中**非当日买入**（buy_date < day）的每只持仓，
    按顺序执行：①一字跌停顺延 → ②止损 → ③止盈 → ④超时卖出（收盘价）。

    - ① 一字跌停（开板前无法成交）→ 不卖，顺延到下个交易日；
    - ② 止损：day.low < stop_loss → 卖，成交价 = min(open, stop_loss)
      （open 缺失 → stop_loss）；跳空低开打穿按开盘价成交；
    - ③ 止盈：day.high >= take_profit → 卖，成交价 = max(open, take_profit)
      （open 缺失 → take_profit）；跳空高开按开盘价成交；
    - ④ 超时：持有天数（买入日=第 1 天，沿结算日序列累计）>= max_hold_days
      且当日未止损止盈 → 收盘价卖出。

    卖出走 place_order 卖出同样的费用/realized 口径，生成 status=「成交」trade
    记录（含 date/price/qty/amount/fee/stamp_tax/transfer_fee/cash_after/realized），
    并更新 acct["cash"]/positions/realized_pnl。流程调用方需保证已对 day 执行
    _settle_day（T+1 解冻），否则当日买入持仓被误判。绝不抛异常。

    quotes = {code: {pre_close, open, high, low, close}}。返回当日新增 trade 列表。
    """
    trades: list[dict] = []
    try:
        day_date = str(day.get("date") or "")
        # 确定性顺序：code 排序，保证同输入同输出；遍历键快照，卖出后跳过
        for code in sorted(acct.get("positions") or {}):
            pos_cur = acct["positions"].get(code)
            if pos_cur is None:
                continue
            buy_date = str(pos_cur.get("buy_date") or "")
            if not buy_date or not (buy_date < day_date):
                continue  # 仅对非当日买入持仓检查
            qty = int(pos_cur.get("qty") or 0)
            if qty <= 0:
                continue
            stop_loss = pos_cur.get("stop_loss")
            take_profit = pos_cur.get("take_profit")
            max_hold_days = int(pos_cur.get("max_hold_days") or 0)
            if stop_loss is None or take_profit is None or max_hold_days <= 0:
                continue  # 老持仓无条件单字段（未绑定）→ 不机械卖出
            quote = quotes.get(code)
            if quote is None:
                continue  # 行情缺失 → 无法检查，顺延（绝不静默卖出）
            open_ = quote.get("open")

            # ① 一字跌停 → 顺延到下个交易日（不推进持有天数，仍算持有）
            if _one_word_limit_down(quote, code):
                continue

            # 持有天数累计（买入日=第 1 天，沿结算日序列逐日递增，一天只 +1）：
            # 依赖 pos.hold_days 持久计数器 + hold_check_date 防止同日重复递增
            if str(pos_cur.get("hold_check_date") or "") != day_date:
                pos_cur["hold_check_date"] = day_date
                pos_cur["hold_days"] = int(pos_cur.get("hold_days") or 1) + 1
            hold_days = int(pos_cur.get("hold_days") or 1)

            # ② 止损：盘中最低 < 止损价
            if float(quote["low"]) < float(stop_loss):
                price = min(open_, float(stop_loss)) if open_ is not None else float(stop_loss)
                trades.append(_conditional_sell_record(
                    acct, acct["positions"][code], code, price, qty, day, reason="stop_loss"))
            # ③ 止盈：盘中最高 >= 止盈价（未止损时）
            elif float(quote["high"]) >= float(take_profit):
                price = max(open_, float(take_profit)) if open_ is not None else float(take_profit)
                trades.append(_conditional_sell_record(
                    acct, acct["positions"][code], code, price, qty, day, reason="take_profit"))
            # ④ 超时：持有天数 >= max_hold_days → 收盘价卖出
            elif max_hold_days <= hold_days:
                price = float(quote["close"])
                trades.append(_conditional_sell_record(
                    acct, acct["positions"][code], code, price, qty, day, reason="timeout"))
    except Exception:  # noqa: BLE001 - 引擎绝不抛异常
        pass
    return trades


def snapshot(acct: dict, date: str, close_prices: dict) -> dict:
    """每日快照：现金 + 持仓市值（按 close_prices 估值）→ 总资产。
    close_prices = {code: close}；缺失代码按成本价估值并标注。
    2026-08-13 修复：返回值新增 positions = {code: {"close", "market_value", "qty", "estimated"}}，
    每只持仓记录结算日收盘价与市值，供展示层按历史收盘价估值（不再回拉实时价）。"""
    position_mv = 0.0
    estimate = []
    positions: dict[str, dict] = {}
    for code, pos in (acct.get("positions") or {}).items():
        close = close_prices.get(code)
        estimated = close is None
        if close is None:
            close = pos.get("avg_cost", 0.0) or 0.0
            estimate.append(code)
        market_value = round(float(close) * pos["qty"], 6)
        position_mv = round(position_mv + market_value, 6)
        positions[str(code)] = {
            "close": round(float(close), 6),
            "market_value": market_value,
            "qty": int(pos["qty"]),
            "estimated": estimated,
        }
    total = round(acct["cash"] + position_mv, 6)
    return {
        "date": date,
        "cash": round(acct["cash"], 6),
        "position_mv": position_mv,
        "total_assets": total,
        "realized_pnl": round(acct.get("realized_pnl", 0.0), 6),
        "estimated_prices": estimate,
        "positions": positions,
    }


def trade_stats(acct: dict) -> dict:
    """账户交易统计（参考同花顺交割单口径）：
    - 胜率：盈利卖出笔数 / 总卖出笔数
    - 盈亏比：平均单笔盈利 / 平均单笔亏损绝对值
    - 平均持仓天数：按股票 FIFO 配对买入→卖出，取平均自然日
    无卖出记录时返回 None（前端显示「—」）。
    """
    trades = acct.get("trades") or []
    sells = [t for t in trades if t.get("side") == "sell" and t.get("status") == "成交"]
    if not sells:
        return {"win_rate": None, "profit_loss_ratio": None, "avg_holding_days": None}

    realized = [float(t.get("realized") or 0.0) for t in sells]
    wins = [r for r in realized if r > 0]
    losses = [r for r in realized if r < 0]
    win_rate = len(wins) / len(realized) if realized else None
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    pl_ratio = (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss) else None

    # 平均持仓天数：按代码 FIFO 配对（当日买入→后续卖出）
    from datetime import datetime

    from collections import deque
    buy_queue: dict[str, deque] = {}
    hold_days: list[float] = []
    for t in trades:
        if t.get("status") != "成交":
            continue
        code = str(t.get("code") or "")
        try:
            d = datetime.strptime(str(t.get("date") or ""), "%Y-%m-%d")
        except ValueError:
            continue
        if t.get("side") == "buy":
            buy_queue.setdefault(code, deque()).append((d, int(t.get("qty") or 0)))
        elif t.get("side") == "sell":
            q = buy_queue.get(code)
            if not q:
                continue
            sell_qty = int(t.get("qty") or 0)
            while sell_qty > 0 and q:
                bd, bq = q[0]
                take = min(bq, sell_qty)
                hold_days.append((d - bd).days)
                sell_qty -= take
                if take >= bq:
                    q.popleft()
                else:
                    q[0] = (bd, bq - take)
    avg_days = round(sum(hold_days) / len(hold_days), 1) if hold_days else None
    return {
        "win_rate": round(win_rate, 6) if win_rate is not None else None,
        "profit_loss_ratio": round(pl_ratio, 4) if pl_ratio is not None else None,
        "avg_holding_days": avg_days,
    }


def closed_trades(acct: dict) -> list[dict]:
    """历史持仓（已平仓）：已全部卖出（buy_qty == sell_qty）且当前无持仓的股票，
    返回完整买卖周期与盈亏（字段契约见 requirements-r12 补充，前端按此消费）：
    {code, name, buy_qty, sell_qty, avg_buy_price, avg_sell_price, realized_pnl,
     first_buy_date, last_sell_date, holding_days}
    - 只统计 trades 中 status == 「成交」的记录；realized 缺失按 0；
    - 已平仓判定：buy_qty > 0 且 buy_qty == sell_qty，且该 code 不在 positions
      （或 positions[code].qty == 0）；
    - avg_buy_price = buy_amount/buy_qty、avg_sell_price = sell_amount/sell_qty（round 6）；
      realized_pnl = Σ sell.realized（round 6）；holding_days = (末卖日 − 首买日).days 自然日；
    - 按 last_sell_date 倒序（最近卖出在前）；日期缺失/解析失败跳过该字段不报错；
    - 绝不抛异常（异常/无数据 → []）。
    """
    try:
        from datetime import datetime

        trades = [t for t in (acct.get("trades") or []) if t.get("status") == "成交"]
        agg: dict[str, dict] = {}
        for t in trades:
            code = str(t.get("code") or "")
            if not code:
                continue
            try:
                qty = int(t.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            try:
                amount = float(t.get("amount") or 0.0)
            except (TypeError, ValueError):
                amount = 0.0
            row = agg.setdefault(code, {
                "buy_qty": 0,
                "buy_amount": 0.0,
                "sell_qty": 0,
                "sell_amount": 0.0,
                "sell_realized": 0.0,
                "first_buy_date": None,
                "last_sell_date": None,
                "name": str(t.get("name") or code),
            })
            date = str(t.get("date") or "")
            if t.get("side") == "buy":
                row["buy_qty"] += qty
                row["buy_amount"] += amount
                if date and (row["first_buy_date"] is None or date < row["first_buy_date"]):
                    row["first_buy_date"] = date
            elif t.get("side") == "sell":
                row["sell_qty"] += qty
                row["sell_amount"] += amount
                try:
                    realized = float(t.get("realized") or 0.0)
                except (TypeError, ValueError):
                    realized = 0.0
                row["sell_realized"] += realized
                if date and (row["last_sell_date"] is None or date > row["last_sell_date"]):
                    row["last_sell_date"] = date

        positions = acct.get("positions") or {}
        result: list[dict] = []
        for code, row in agg.items():
            if row["buy_qty"] <= 0 or row["buy_qty"] != row["sell_qty"]:
                continue  # 未平仓（含只买不卖 / 部分卖出）
            pos = positions.get(code)
            if pos is not None and int(pos.get("qty") or 0) != 0:
                continue  # 仍有持仓，不算历史持仓
            holding_days = None
            if row["first_buy_date"] and row["last_sell_date"]:
                try:
                    d1 = datetime.strptime(row["first_buy_date"], "%Y-%m-%d")
                    d2 = datetime.strptime(row["last_sell_date"], "%Y-%m-%d")
                    holding_days = (d2 - d1).days
                except ValueError:
                    holding_days = None  # 日期异常跳过该字段，不报错
            result.append({
                "code": code,
                "name": row["name"] or code,
                "buy_qty": row["buy_qty"],
                "sell_qty": row["sell_qty"],
                "avg_buy_price": round(row["buy_amount"] / row["buy_qty"], 6),
                "avg_sell_price": round(row["sell_amount"] / row["sell_qty"], 6)
                if row["sell_qty"] else 0.0,
                "realized_pnl": round(row["sell_realized"], 6),
                "first_buy_date": row["first_buy_date"],
                "last_sell_date": row["last_sell_date"],
                "holding_days": holding_days,
            })
        result.sort(key=lambda r: r["last_sell_date"] or "", reverse=True)
        return result
    except Exception:  # noqa: BLE001 - 绝不抛异常
        return []
