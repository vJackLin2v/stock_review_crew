"""指标计算引擎 v2（自 synalysis_crew 移植，口径冻结——一个字不许改）。

来源：H:\\synalysis_crew\\src\\synalysis_crew\\metrics.py（已验收的冻结契约，
对应 synalysis docs/requirements-v2.md 1.0–1.6 与 docs/requirements.md 第 4 章）。
移植适配：仅精简注释与模块路径说明；FIFO 闭环、逐日 TWR、翻倍/腰斩递进、
is_partial 区间口径、流水模式等全部逻辑与原实现逐行一致。
唯一行为差异：无（行情拉取失败/超时仍按成本兜底标注，符合本项目降级链）。

输入：``list[TradeRecord]``（跨模块契约见 docs/requirements.md 第 4 章，鸭子类型，
不依赖 parser 具体实现；``op_type`` 支持 parser.OpType 枚举或中英文文本）。
输出：``MetricsResult``（固定 JSON Schema，全英文 snake_case，可直接 json.dumps）。

v2 口径（与 requirements-v2.md 对齐）：
- ``amount``：按 parser 落地契约为「成交金额」（恒正）；本模块以「资金余额差额」
  （交易后资金余额 - 上一笔资金余额）作为现金流的权威信号（余额缺失时优先按
  ``cash_amount`` 发生金额、其次按操作类型估计，见下方「流水模式」），
  因此买入费用入成本、卖出费用扣净额、净转入等口径自动成立；
- 费用口径：``fee`` 含「手续费+其他杂费」，``transfer_fee`` 含「过户费+清算费(B股)」，
  总交易成本 = fee + stamp_tax + commission + transfer_fee（佣金与手续费取 max 防重复）；
- 完整交易（trades，1.1）：个股首次买入 → 清仓闭环（FIFO 配对，多笔买入/卖出合并为一个
  闭环）；每闭环输出 code/name/buy_qty（实际买入数量）/buy_amount（含买入费用成本）/
  sell_qty（配对卖出数量）/sell_amount（卖出净额扣费用）/pnl（周期盈亏）/
  holding_days（首买日→清仓日，含首尾）/start_date/end_date/status="closed"；
  分红/红股/利息/逆回购/指定交易/银行转账一律不进 trades（红股仅摊薄持仓成本与数量，
  用于 FIFO 配对与闭环判定，不计入买入数量与买入成本）；期初持仓卖出（无法配对）不进
  trades，单列 ``unmatched_sell_amount``；
- 胜率/盈亏比/平均持仓周期/持仓周期分布（1.2）：均按**完整交易**统计（未清仓不计）；
- 收益率（1.0 v2.1 TWR）：按交易日逐日模拟账户——现金 = 资金余额（权威，缺失时按
  操作类型估计增量续链）；持仓数量按 BUY/SELL/红股增减；估值价 = 该股最近成交价
  （交割单内最后已知价，BUY/SELL 更新，红股不更新）；期初持仓（未配对卖出部分）按
  其变现价值以「合成持仓」计入期初资产（价值 1:1 跟踪，卖出时同步扣减，避免期初
  持仓卖出被误算成收益）。每日 r_d = (当日末资产 − 当日净出入金 − 前一日末资产) /
  前一日末资产（前一日末资产 ≤ 0 的日跳过）；出入金只影响分子扣除；分红/利息/
  逆回购到期利息计为盈亏（不扣出入金）；逆回购本金以「应收款」1:1 跟踪
  （价值中性，仅费用/利息计为盈亏，1 日回购影响本就极小，R-007 等多日回购
  也不会扭曲日收益率）。累计 R = Π(1 + r_d) − 1，``total_return_rate`` 主口径 = 最终 R；
  return_curve = 每月末累计 R [{month, date, return_rate}]（无记录月份沿用上一值），
  equity_curve 保留原始净值供调试；**最大回撤/翻倍/腰斩基于逐日 (1 + R) 序列**；
- 账户级翻倍/腰斩（1.4）：翻倍（v2.1 不变）= 累计 R 达到 +100% 的独立事件
  （R 从 < 100% 升到 ≥ 100% 计 1 次，禁止「从运行低点翻倍」口径——接近归零的
  账户会产生假阳性）；腰斩（v2.2 递进式）= floor 初始 1.0，遍历 v = 1 + R 曲线：
  v > floor → floor = v（新高重置）；v ≤ floor × 0.5 → 计 1 次且 floor = v
  （1→0.5 第 1 次、0.5→0.25 第 2 次、0.25→0.125 第 3 次，连续下跌逐级计数）；
  回升超过当前 floor 才重置基线；事件明细（v2.3）：
  ``double_events / halved_events`` 记录每次触发日的 {date, return_rate}，
  日期为逐日模拟中触发阈值的实际交易日，return_rate 为当日累计 R（小数）；
- M6：首行「证券转银行」按其出金额计入现金流转（net_transfer/gross_withdraw/
  月转账），期初资金 = 余额 + 出金额；
- 区间口径：期初资金 != 0 或期初有持仓（文件内先卖后买）时 ``is_partial=True``。
- 流水模式（无余额文件，如涨乐财富通导出 CSV）：``_collect`` 后所有记录 balance
  均为 None（鸭子类型）或均 ≤ 0 且存在非零 ``cash_amount``（parser 落地：无余额列时
  balance 填 0.0、cash_amount 填「发生金额」）即 ``flow_mode=True``；现金从 0.0 起
  按每条记录 ``cash_amount``（发生金额，带符号：买入为负、卖出为正、转账±、红利/
  利息+；parser 未落地时 getattr 兜底为 None）累加，``_estimate_cash_delta`` 优先
  使用 cash_amount（0.0 视为未提供，回退操作类型估算）。期初资金按 0.0 计
  （期初资金/持仓未知，按文件区间口径），``meta.is_partial=True``，并新增
  ``meta.balance_source``："flow"（无余额文件）/ "balance"（有余额文件）；
  出入金/净转入、FIFO 闭环、胜率、翻倍/腰斩、return_curve 等其余指标逻辑不变，
  仅现金来源切换。

固定 JSON Schema：::

    {
      "meta": {is_partial, balance_source, start_date, end_date, calendar_days,
               active_trading_days, generated_at},
      "account": {initial_balance, ending_balance, net_transfer_in,
                  gross_deposit, gross_withdraw, opening_asset_value,
                  total_return_rate, total_return_rate_net, annualized_return_rate,
                  realized_pnl, total_cost, total_cost_ratio,
                  holding_market_value, holding_cost_value, unrealized_pnl,
                  market_value_source, valuation_date},
      "trading": {total_amount, total_count, buy_count, buy_amount, sell_count, sell_amount,
                  daily_avg_count, daily_avg_amount, distinct_stock_count, current_holding_count,
                  avg_trade_amount, capital_turnover_rate, avg_holding_period_days},
      "pnl": {realized_pnl, win_count, loss_count, win_rate, total_profit, total_loss,
              profit_loss_ratio, max_single_profit, max_single_loss, double_count, halved_count,
              double_events[], halved_events[], unmatched_sell_amount, monthly_pnl[],
              equity_curve[], return_curve[], max_drawdown,
              stock_leaderboard{top_profit[], top_loss[]}},
      "trades": [完整交易：买入→清仓闭环，{code, name, buy_qty, buy_amount, sell_qty,
                sell_amount, pnl, holding_days, start_date, end_date, status}],
      "behavior": {holding_period_distribution{d1,d2_3,d4_5,d6_10,d11_20,d21_30,d31_60,gt60},
                   monthly_activity[], max_position{},
                   top5_concentration, favorite_stocks_top10[], style{}, special_operations{}}
    }

说明：
- 收益率/胜率/集中度等比率以小数存储（如 0.1234 = 12.34%）；最大回撤为正值（相对峰值跌幅）；
- ``total_return_rate`` 主口径 = 逐日 TWR 最终累计收益率 R（1.0），出入金不影响
  收益率本身；无任何有效日收益（如全部起始日资产 ≤ 0）时输出 None。
- ``meta.balance_source``：现金链数据来源——"balance"（资金余额列权威）/ "flow"
  （无余额流水模式：现金从 0 起按 ``cash_amount`` 累加，期初资金/持仓未知，
  ``is_partial`` 恒为 True）。
- ``total_return_rate_net`` 为对照口径（期初资产基准简单收益率）：
  (期末资产 − A0 − 净转入) / A0；A0 = 0（完整历史）时退化为
  (期末资产 − 累计入金) / 累计入金。
- ``win_rate/盈亏比/持仓周期`` 均按**完整交易**统计，每闭环计一笔：
  周期盈亏 = 卖出净额 − 买入成本（含费用）；
  ``holding_period_distribution`` 为 8 档（v2.3）{d1, d2_3, d4_5, d6_10, d11_20,
  d21_30, d31_60, gt60}（1 / 2-3 / 4-5 / 6-10 / 11-20 / 21-30 / 31-60 / >60 天）；
- ``double_count/halved_count`` 为**账户级**：按逐日 v = 1 + R 序列统计
  「R 从 < 100% 升到 ≥ 100% 的翻倍（v2.1）」与「递进式腰斩（v2.2：floor 初始
  1.0，v > floor → floor = v 新高重置；v ≤ floor × 0.5 → 计 1 次且 floor = v，
  连续下跌逐级计数；回升超过当前 floor 才重置基线）」；对应事件明细
  ``double_events / halved_events``（v2.3）= [{date, return_rate}]，日期为逐日
  TWR 模拟触发阈值的实际交易日，``double_count/halved_count`` = 数组长度；
- ``top_loss`` 按 total_pnl **升序**（亏损最多在前），``top_profit`` 降序；
- 金额四舍五入到 2 位、比率到 4 位；非有限数输出 None，保证严格 JSON 序列化；
- 无配对持仓的卖出（期初持仓）记入 ``unmatched_sell_amount``，不计入已实现盈亏与胜率。
"""

from __future__ import annotations

import math
import queue
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

_EPS = 1e-9

# 操作归一化后的分类（内部枚举）
OP_BUY = "buy"
OP_SELL = "sell"
OP_TRANSFER_IN = "transfer_in"
OP_TRANSFER_OUT = "transfer_out"
OP_REVERSE_REPO = "reverse_repo"
OP_DIVIDEND = "dividend"
OP_BONUS_SHARE = "bonus_share"
OP_INTEREST = "interest"
OP_IPO = "ipo"
OP_OTHER = "other"

# parser.OpType 枚举值 / 常见英文值 -> 内部分类
_EN_OP_MAP: Dict[str, str] = {
    "BUY": OP_BUY,
    "SELL": OP_SELL,
    "BANK_TO_SEC": OP_TRANSFER_IN,
    "SEC_TO_BANK": OP_TRANSFER_OUT,
    "REPO": OP_REVERSE_REPO,
    "INTEREST": OP_INTEREST,
    "DIVIDEND": OP_DIVIDEND,
    "BONUS_SHARE": OP_BONUS_SHARE,
    "DIVIDEND_DIFF": OP_DIVIDEND,  # 股息红利差异并入分红统计
    "DESIGNATED_TRADE": OP_OTHER,
    "UNKNOWN": OP_OTHER,
    "buy": OP_BUY,
    "sell": OP_SELL,
    "transfer_in": OP_TRANSFER_IN,
    "transfer_out": OP_TRANSFER_OUT,
    "reverse_repo": OP_REVERSE_REPO,
    "interest": OP_INTEREST,
    "dividend": OP_DIVIDEND,
    "bonus_share": OP_BONUS_SHARE,
    "ipo": OP_IPO,
    "other": OP_OTHER,
}


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        text = str(int(value))
        if len(text) == 8:
            return datetime.strptime(text, "%Y%m%d").date()
        raise ValueError(f"无法解析交易日期: {value!r}")
    text = str(value).strip()
    for fmt in (
        "%Y%m%d",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y年%m月%d日",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法解析交易日期: {value!r}")


def _norm_op(op_type: Any) -> str:
    if isinstance(op_type, Enum):
        op_type = op_type.value
    if op_type is None:
        return OP_OTHER
    text = str(op_type).strip()
    if text in _EN_OP_MAP:
        return _EN_OP_MAP[text]
    low = text.lower()
    if low in _EN_OP_MAP:
        return _EN_OP_MAP[low]
    if "回购" in text:
        return OP_REVERSE_REPO
    if "银行转证券" in text or "银转证" in text:
        return OP_TRANSFER_IN
    if "证券转银行" in text or "证转银" in text:
        return OP_TRANSFER_OUT
    if "红股" in text or "送股" in text:
        return OP_BONUS_SHARE
    if "红利" in text or "股息" in text or "分红" in text:
        return OP_DIVIDEND
    if "利息" in text:
        return OP_INTEREST
    if "申购" in text:
        return OP_IPO
    if "买入" in text or "买" in text:
        return OP_BUY
    if "卖出" in text or "卖" in text:
        return OP_SELL
    return OP_OTHER


def _round2(value: float) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 2)


def _round4(value: float) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    result = round(value, 4)
    # 归一化 -0.0 -> 0.0，保证 JSON 输出与前端展示一致
    return 0.0 if result == 0 else result


def _count_doublings(vs: Sequence[float]) -> int:
    """v2.1 翻倍次数：累计 R 达到 +100% 的独立事件（v = 1+R 从 < 2.0 升到 ≥ 2.0 计 1 次）。

    禁止「从运行低点翻倍」口径——接近归零的账户会产生假阳性。
    """
    count = 0
    above_double = False
    for v in vs:
        if v >= 2.0:
            if not above_double:
                count += 1
                above_double = True
        else:
            above_double = False
    return count


def _count_halvings(vs: Sequence[float]) -> int:
    """v2.2 递进式腰斩次数（1.4，用户确认）：逐日遍历 v = 1 + R 曲线。

    - floor 初始 = 1.0（初始净值）；
    - v > floor → floor = v（新高重置基线）；
    - v ≤ floor × 0.5 → 计 1 次且 floor = v（1→0.5 第 1 次、0.5→0.25 第 2 次、
      0.25→0.125 第 3 次，连续下跌逐级计数）；
    - 回升超过当前 floor 才重置基线（低于历史高点的回升也按新 floor 继续计数）。
    """
    count = 0
    floor = 1.0
    for v in vs:
        if v > floor:
            floor = v
        elif v <= floor * 0.5:
            count += 1
            floor = v
    return count


def _double_events(
    dates: Sequence[date], vs: Sequence[float]
) -> List[Dict[str, Any]]:
    """v2.3 翻倍事件明细：v = 1+R 从 < 2.0 升到 ≥ 2.0 的独立事件。

    返回 [{date: "YYYY-MM-DD", return_rate: 小数}]，日期为逐日 TWR 模拟中
    触发阈值的实际交易日，return_rate 为触发日累计 R（v − 1，4 位舍入）。
    """
    events: List[Dict[str, Any]] = []
    above_double = False
    for d, v in zip(dates, vs):
        if v >= 2.0:
            if not above_double:
                events.append(
                    {"date": d.isoformat(), "return_rate": _round4(v - 1.0)}
                )
                above_double = True
        else:
            above_double = False
    return events


def _halved_events(
    dates: Sequence[date], vs: Sequence[float]
) -> List[Dict[str, Any]]:
    """v2.3 腰斩事件明细（v2.2 递进式 floor 口径）。

    返回 [{date: "YYYY-MM-DD", return_rate: 小数}]，日期为逐日 TWR 模拟中
    触发阈值的实际交易日，return_rate 为触发日累计 R（v − 1，4 位舍入）。
    """
    events: List[Dict[str, Any]] = []
    floor = 1.0
    for d, v in zip(dates, vs):
        if v > floor:
            floor = v
        elif v <= floor * 0.5:
            events.append(
                {"date": d.isoformat(), "return_rate": _round4(v - 1.0)}
            )
            floor = v
    return events


def _months_between(start: date, end: date) -> List[str]:
    months: List[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def _month_end_date(year: int, month: int) -> date:
    """某年某月的最后一天。"""
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


@dataclass
class _Rec:
    op: str
    code: str
    name: str
    qty: float
    price: float
    amount: float
    balance: Optional[float]
    fee: float
    stamp_tax: float
    commission: float
    transfer_fee: float
    date: date
    idx: int
    cash_amount: Optional[float] = None  # 「发生金额」带符号；无余额流水模式现金链来源

    @property
    def gross(self) -> float:
        """成交金额（名义金额）。"""
        return self.qty * self.price

    @property
    def cost_fields(self) -> float:
        """单笔交易成本：印花税 + 过户费 + max(手续费, 佣金)。

        parser 已把「其他杂费」并入 fee、「清算费(B股)」并入 transfer_fee，
        因此这里即为需求 2.3 第 7 项「手续费+印花税+佣金+过户费+杂费」口径。
        max(fee, commission) 用于规避同花顺交割单中两列同值导致的重复计算。
        """
        return self.stamp_tax + self.transfer_fee + max(self.fee, self.commission)


@dataclass
class _Lot:
    qty: float
    unit_cost: float
    buy_date: date


@dataclass
class _Cycle:
    """个股完整持仓周期（首次买入→清仓闭环）。

    - ``bought_qty``：实际买入股数（红股不计入，trades 的 buy_qty 口径）；
    - ``acquired_qty``：买入 + 红股入账的总数量（用于闭环判定，防止卖出含红股时
      尚未清仓却提前闭环）；
    - ``buy_cost``：实际买入成本（含买入费用），红股零成本摊薄；
    - ``proceeds``：已配对卖出的净额（扣卖出费用）。
    """

    bought_qty: float = 0.0
    acquired_qty: float = 0.0
    sold_qty: float = 0.0
    buy_cost: float = 0.0
    proceeds: float = 0.0
    start_date: Optional[date] = None
    end_date: Optional[date] = None


@dataclass
class _StockState:
    lots: deque = field(default_factory=deque)
    cycle: Optional[_Cycle] = None
    pre_qty: float = 0.0  # 期初持仓被卖出的股数（无法配对，pnl 中性）

    def holding_qty(self) -> float:
        return sum(lot.qty for lot in self.lots)

    def holding_cost(self) -> float:
        return sum(lot.qty * lot.unit_cost for lot in self.lots)


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """兼容 dict 与对象两种输入（R12：后端传 to_dict() 的 dict）。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _collect(trades: Sequence[Any]) -> List[_Rec]:
    recs: List[_Rec] = []
    for index, trade in enumerate(trades):
        try:
            # R12 修复：后端传入 to_dict() 的 dict（此前仅支持对象属性 → 全 0）
            balance_raw = _field(trade, "balance", None)
            cash_raw = _field(trade, "cash_amount", None)
            recs.append(
                _Rec(
                    op=_norm_op(_field(trade, "op_type", None)),
                    code=str(_field(trade, "code", "") or "").strip(),
                    name=str(_field(trade, "name", "") or "").strip(),
                    qty=_num(_field(trade, "qty", 0.0)),
                    price=_num(_field(trade, "price", 0.0)),
                    amount=_num(_field(trade, "amount", 0.0)),
                    balance=None if balance_raw is None else _num(balance_raw),
                    cash_amount=None if cash_raw is None else _num(cash_raw),
                    fee=_num(_field(trade, "fee", 0.0)),
                    stamp_tax=_num(_field(trade, "stamp_tax", 0.0)),
                    commission=_num(_field(trade, "commission", 0.0)),
                    transfer_fee=_num(_field(trade, "transfer_fee", 0.0)),
                    date=_parse_date(_field(trade, "trade_date", None)),
                    idx=index,
                )
            )
        except Exception:
            # 单条脏数据跳过（脏数据兜底主要职责在 parser）
            continue
    recs.sort(key=lambda rec: (rec.date, rec.idx))
    return recs


def _estimate_cash_delta(rec: _Rec) -> float:
    """余额缺失时估计现金增量：有 ``cash_amount``（发生金额，带符号）时优先——
    流水模式权威信号；否则按操作类型估算（买入为负、卖出为正、转账±、红利/利息+）。
    cash_amount == 0.0 视为未提供（parser 对同花顺版式留 0.0，金额口径按余额推算），
    继续按操作类型估算，避免把默认值误当权威信号。"""
    if rec.cash_amount is not None and abs(rec.cash_amount) > _EPS:
        return rec.cash_amount
    if rec.op == OP_BUY:
        return -(rec.gross + rec.cost_fields)
    if rec.op == OP_SELL:
        return rec.gross - rec.cost_fields
    if rec.op == OP_TRANSFER_IN:
        return rec.amount if rec.amount > 0 else 0.0
    if rec.op == OP_TRANSFER_OUT:
        return -(rec.amount if rec.amount > 0 else 0.0)
    if rec.op == OP_REVERSE_REPO:
        return -(rec.gross + rec.cost_fields)
    return rec.amount


def _prescan_unmatched(
    recs: Sequence[_Rec], initial_balance: float, flow_mode: bool = False
) -> Tuple[Dict[str, float], float]:
    """v2.1 轻量 FIFO 预扫描：逐股计算「期初持仓未配对卖出额」（变现价值）。

    与主循环 FIFO 语义一致（红股仅在有持仓时增加可用数量），结果用于：
    - ``opening_asset_value`` 期初资产中的「合成持仓」（价值 1:1）；
    - 逐日 TWR 模拟开始时计入期初资产，未配对卖出时同步扣减，
      保证期初持仓卖出不产生虚假收益。

    返回 (逐股未配对卖出额, 合计)。
    """
    avail: Dict[str, float] = defaultdict(float)
    unmatched: Dict[str, float] = defaultdict(float)
    prev_balance = initial_balance
    for rec in recs:
        if not flow_mode and rec.balance is not None:
            cash_delta = rec.balance - prev_balance
            prev_balance = rec.balance
        else:
            cash_delta = _estimate_cash_delta(rec)
        op = rec.op
        if op == OP_BUY and rec.qty > _EPS:
            avail[rec.code] += rec.qty
        elif (
            op == OP_BONUS_SHARE
            and rec.qty > _EPS
            and avail.get(rec.code, 0.0) > _EPS
        ):
            avail[rec.code] += rec.qty
        elif op == OP_SELL and rec.qty > _EPS:
            take = min(rec.qty, avail.get(rec.code, 0.0))
            remaining = rec.qty - take
            if remaining > _EPS:
                proceeds = (
                    cash_delta
                    if cash_delta > _EPS
                    else rec.gross - rec.cost_fields
                )
                unmatched[rec.code] += proceeds * remaining / rec.qty
            avail[rec.code] = max(0.0, avail[rec.code] - take)
    return dict(unmatched), sum(unmatched.values())


def _parse_spot_frame(frame: Any) -> Optional[Dict[str, float]]:
    """把 akshare 全市场行情表（列：代码/最新价）解析为 代码 -> 最新价。"""
    try:
        import pandas as pd

        if not isinstance(frame, pd.DataFrame) or frame is None or frame.empty:
            return None
        code_col: Any = None
        price_col: Any = None
        for col in frame.columns:
            text = str(col)
            if code_col is None and "代码" in text:
                code_col = col
            elif price_col is None and "最新价" in text:
                price_col = col
        if code_col is None or price_col is None:
            return None
        prices: Dict[str, float] = {}
        for _, row in frame.iterrows():
            code_text = str(row[code_col]).strip()
            if len(code_text) >= 6 and code_text[:2].lower() in ("sh", "sz", "bj"):
                code_text = code_text[2:]
            if code_text.isdigit() and len(code_text) > 6:
                code_text = code_text[-6:]
            try:
                price = float(row[price_col])
            except (TypeError, ValueError):
                continue
            if code_text and price > 0:
                prices[code_text] = price
        return prices or None
    except Exception:
        return None


def _fetch_spot_em_table(ak_module: Any) -> Any:
    """调用 akshare 全市场行情；失败返回 None。"""
    try:
        frame = ak_module.stock_zh_a_spot_em()
        if frame is None or getattr(frame, "empty", True):
            return None
        return frame
    except Exception:
        return None


def _market_prefix(code: str) -> str:
    """沪深京市场前缀（新浪/腾讯行情接口用）。"""
    if code[:1] in ("6", "9"):
        return "sh"
    if code[:1] in ("4", "8"):
        return "bj"
    return "sz"


def _get_price_once(
    requests_module: Any, url: str, params: Optional[Dict[str, str]]
) -> Optional[float]:
    """单次拉价尝试：先走环境代理，再直连（trust_env=False），任一成功即返回。"""
    headers = {"Referer": "https://finance.sina.com.cn"} if "sinajs" in url else {}
    for direct in (False, True):
        try:
            if direct:
                session = requests_module.Session()
                session.trust_env = False
                resp = session.get(url, params=params, headers=headers, timeout=8)
            else:
                resp = requests_module.get(url, params=params, headers=headers, timeout=8)
            if resp.status_code != 200:
                continue
            if "eastmoney" in url:
                data = resp.json().get("data") or {}
                price = data.get("f43")
                if isinstance(price, (int, float)) and float(price) > 0:
                    return float(price)
            else:
                text = resp.text
                if '=""' in text:
                    continue
                parts = text.split('"')[1].split("," if "sinajs" in url else "~")
                if len(parts) > 3:
                    try:
                        price = float(parts[3])  # 新浪/腾讯的第 4 个字段均为最新价
                        if price > 0:
                            return price
                    except (TypeError, ValueError):
                        continue
        except Exception:
            continue
    return None


def _fetch_single_em_price(code: str) -> Optional[float]:
    """逐只兜底拉最新价：eastmoney 实时 -> 新浪 -> 腾讯。

    单股请求体小，且每个源都同时尝试代理与直连，兼容代理对大响应体不稳定、
    个别行情域名被阻断等网络环境；全部失败返回 None（按成本兜底）。
    """
    try:
        import requests
    except Exception:
        return None
    prefix = _market_prefix(code)
    em_market = "1" if prefix == "sh" else "0"
    attempts = [
        (
            "https://push2.eastmoney.com/api/qt/stock/get",
            {"fltt": "2", "invt": "2", "fields": "f43,f58", "secid": f"{em_market}.{code}"},
            prefix,
        ),
        (f"https://hq.sinajs.cn/list={prefix}{code}", None, prefix),
        (f"https://qt.gtimg.cn/q={prefix}{code}", None, prefix),
    ]
    for url, params, _ in attempts:
        price = _get_price_once(requests, url, params)
        if price is not None:
            return price
    return None


def _fetch_latest_prices(
    codes: Sequence[str], timeout: float = 15.0
) -> Optional[Dict[str, float]]:
    """拉取 A 股最新价（代码 -> 最新价）。

    优先 akshare ``stock_zh_a_spot_em`` 全市场表；失败时逐只直连 eastmoney 单股行情；
    全部失败 / 超时 / akshare 未安装时返回 None，由调用方按成本兜底
    （``market_value_source="cost"``）。
    拉取放在守护线程中执行并限制等待时间，超时立即返回且不阻塞进程退出。
    """
    try:
        import akshare as ak  # type: ignore
    except Exception:
        return None

    result_queue: "queue.Queue[Any]" = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            try:
                frame = _fetch_spot_em_table(ak)
            except Exception:  # noqa: BLE001 - 东财 502 时 akshare 表抛异常，不应短路后续 realtime 链
                frame = None
            if frame is not None:
                result_queue.put(("frame", frame))
                return
            prices: Dict[str, float] = {}
            # R12 修复：优先复用 realtime 完整降级链（东财 → 腾讯 → 通达信协议），
            # 东财 502 时腾讯/通达信仍可用，避免持仓估值全部落到成本（未实现盈亏恒 0）。
            try:
                from ..tools.realtime import fetch_stock_quote

                for code in codes:
                    try:
                        q = fetch_stock_quote(code)
                        price = (q or {}).get("price")
                        if price is not None and price > 0:
                            prices[code] = float(price)
                    except Exception:  # noqa: BLE001 - 单只失败跳过，保留已成功价格
                        continue
            except Exception:  # noqa: BLE001 - realtime 模块级不可用时回退单股东财直连
                pass
            if not prices:
                for code in codes:
                    price = _fetch_single_em_price(code)
                    if price is not None and price > 0:
                        prices[code] = price
            result_queue.put(("prices", prices))
        except Exception as exc:  # noqa: BLE001
            result_queue.put(("error", exc))

    thread = threading.Thread(
        target=_worker, daemon=True, name="metrics-akshare-price-fetch"
    )
    thread.start()
    try:
        kind, payload = result_queue.get(timeout=timeout)
    except queue.Empty:
        return None
    if kind == "error":
        return None
    if kind == "prices":
        return payload or None
    return _parse_spot_frame(payload)


class MetricsResult(dict):
    """固定 JSON Schema 的结果容器：可直接 ``json.dumps``，支持属性访问与 ``to_dict()``。"""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in self.items()}


def _empty_result() -> MetricsResult:
    now = datetime.now().isoformat(timespec="seconds")
    return MetricsResult(
        {
            "meta": {
                "is_partial": False,
                "balance_source": "balance",
                "start_date": None,
                "end_date": None,
                "calendar_days": 0,
                "active_trading_days": 0,
                "generated_at": now,
            },
            "account": {
                "initial_balance": 0.0,
                "ending_balance": 0.0,
                "net_transfer_in": 0.0,
                "gross_deposit": 0.0,
                "gross_withdraw": 0.0,
                "opening_asset_value": 0.0,
                "total_return_rate": None,
                "total_return_rate_net": None,
                "annualized_return_rate": None,
                "realized_pnl": 0.0,
                "total_cost": 0.0,
                "total_cost_ratio": 0.0,
                "holding_market_value": 0.0,
                "holding_cost_value": 0.0,
                "unrealized_pnl": 0.0,
                "market_value_source": "cost",
                "valuation_date": None,
            },
            "trading": {
                "total_amount": 0.0,
                "total_count": 0,
                "buy_count": 0,
                "buy_amount": 0.0,
                "sell_count": 0,
                "sell_amount": 0.0,
                "daily_avg_count": 0.0,
                "daily_avg_amount": 0.0,
                "distinct_stock_count": 0,
                "current_holding_count": 0,
                "avg_trade_amount": 0.0,
                "capital_turnover_rate": None,
                "avg_holding_period_days": None,
            },
            "pnl": {
                "realized_pnl": 0.0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": None,
                "total_profit": 0.0,
                "total_loss": 0.0,
                "profit_loss_ratio": None,
                "max_single_profit": 0.0,
                "max_single_loss": 0.0,
                "double_count": 0,
                "halved_count": 0,
                "double_events": [],
                "halved_events": [],
                "unmatched_sell_amount": 0.0,
                "monthly_pnl": [],
                "equity_curve": [],
                "return_curve": [],
                "max_drawdown": 0.0,
                "stock_leaderboard": {"top_profit": [], "top_loss": []},
            },
            "behavior": {
                "holding_period_distribution": {
                    "d1": 0,
                    "d2_3": 0,
                    "d4_5": 0,
                    "d6_10": 0,
                    "d11_20": 0,
                    "d21_30": 0,
                    "d31_60": 0,
                    "gt60": 0,
                },
                "monthly_activity": [],
                "max_position": {"ratio": 0.0, "code": None, "name": None, "date": None},
                "top5_concentration": 0.0,
                "favorite_stocks_top10": [],
                "style": {
                    "holding_style": "波段",
                    "concentration": "分散",
                    "risk_style": "稳健",
                    "label": "波段·分散·稳健",
                },
                "special_operations": {
                    "reverse_repo": {"count": 0, "amount": 0.0},
                    "dividend": {"count": 0, "amount": 0.0},
                    "bonus_share": {"count": 0, "qty": 0.0},
                    "interest": {"count": 0, "amount": 0.0},
                    "ipo": {"count": 0, "amount": 0.0},
                    "other": {"count": 0, "amount": 0.0},
                },
            },
            "stocks": [],
            "trades": [],
        }
    )


def compute_metrics(
    trades: Sequence[Any], price_timeout: float = 15.0
) -> MetricsResult:
    """按需求 2.3 计算 A/B/C/D 全部指标，返回固定 JSON Schema 的 MetricsResult。"""
    recs = _collect(trades)
    if not recs:
        return _empty_result()

    # ---- 流水模式检测（无余额文件，如涨乐财富通导出 CSV）----
    # 1) 契约/鸭子类型：balance 字段缺失（None）；
    # 2) parser 实际落地：无余额列时 balance 填 0.0、cash_amount 填发生金额（非零），
    #    同花顺版式 balance 为真实余额、cash_amount 恒 0.0（视为未提供）。
    flow_mode = all(rec.balance is None for rec in recs) or (
        all(rec.balance is not None and abs(rec.balance) <= _EPS for rec in recs)
        and any(
            rec.cash_amount is not None and abs(rec.cash_amount) > _EPS
            for rec in recs
        )
    )

    # ---- 期初资金：余额口径为首笔交易前余额；流水模式从 0 起（期初资金未知）----
    if flow_mode:
        initial_balance = 0.0
    else:
        first = recs[0]
        if first.balance is None:
            initial_balance = 0.0
        elif first.op == OP_TRANSFER_IN:
            # 首行银行转证券：完整历史，视为从 0 开始
            initial_balance = 0.0
        elif first.op == OP_TRANSFER_OUT:
            # M6：首行证券转银行——该笔出金必须计入现金流转（net_transfer/
            # gross_withdraw/月转账），期初资金 = 余额 + 出金额。出金额以记录
            # 的成交金额字段为代理（parser 未暴露「发生金额」列；同花顺导出若
            # 成交金额为空则无法从契约字段重建，保持按余额反推）。
            out_amount = first.amount if first.amount > _EPS else 0.0
            initial_balance = first.balance + out_amount
        else:
            initial_balance = first.balance - _estimate_cash_delta(first)

    start_date = recs[0].date
    end_date = recs[-1].date

    # ---- v2.1 期初持仓预扫描：逐股未配对卖出额 = 期初持仓变现价值（合成持仓） ----
    unmatched_by_stock, unmatched_sell_amount = _prescan_unmatched(
        recs, initial_balance, flow_mode
    )
    opening_asset_value = initial_balance + unmatched_sell_amount

    stocks: Dict[str, _StockState] = {}
    code_name: Dict[str, str] = {}
    special: Dict[str, Dict[str, float]] = {
        kind: {"count": 0.0, "amount": 0.0}
        for kind in (
            OP_REVERSE_REPO,
            OP_DIVIDEND,
            OP_BONUS_SHARE,
            OP_INTEREST,
            OP_IPO,
            OP_OTHER,
        )
    }
    net_transfer = 0.0
    gross_deposit = 0.0
    gross_withdraw = 0.0
    month_transfer: Dict[str, float] = defaultdict(float)
    month_deposit: Dict[str, float] = defaultdict(float)
    total_cost = 0.0
    balances: List[float] = []
    prev_balance = initial_balance
    last_balance = initial_balance
    last_date = start_date
    prev_month: Optional[str] = None
    snapshots: List[Tuple[str, date, float, float]] = []

    realized_by_month: Dict[str, float] = defaultdict(float)
    activity: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "buy": 0, "sell": 0}
    )
    equity_dates = set()
    counts = {"total": 0, "buy": 0, "sell": 0}
    turnover = {"total": 0.0, "buy": 0.0, "sell": 0.0}
    code_turnover: Dict[str, float] = defaultdict(float)
    code_count: Dict[str, int] = defaultdict(int)
    code_realized: Dict[str, float] = defaultdict(float)
    code_buy_count: Dict[str, int] = defaultdict(int)
    code_sell_count: Dict[str, int] = defaultdict(int)
    code_buy_qty: Dict[str, float] = defaultdict(float)
    code_sell_qty: Dict[str, float] = defaultdict(float)
    code_buy_amount: Dict[str, float] = defaultdict(float)
    code_sell_amount: Dict[str, float] = defaultdict(float)
    code_first_date: Dict[str, date] = {}
    code_last_date: Dict[str, date] = {}

    realized_total = 0.0
    win_count = 0
    loss_count = 0
    total_profit = 0.0
    total_loss = 0.0
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None
    unmatched_sell_amount = 0.0

    dist = {
        "d1": 0,
        "d2_3": 0,
        "d4_5": 0,
        "d6_10": 0,
        "d11_20": 0,
        "d21_30": 0,
        "d31_60": 0,
        "gt60": 0,
    }
    completed_trades: List[Dict[str, Any]] = []
    cycle_win_count = 0
    cycle_loss_count = 0
    cycle_total_profit = 0.0
    cycle_total_loss = 0.0
    cycle_max_profit: Optional[float] = None
    cycle_max_loss: Optional[float] = None
    cycle_hold_days_sum = 0.0
    cycle_hold_count = 0

    max_pos_ratio = 0.0
    max_pos_code: Optional[str] = None
    max_pos_name: Optional[str] = None
    max_pos_date: Optional[str] = None

    def holding_cost_now() -> float:
        return sum(state.holding_cost() for state in stocks.values())

    # ---- v2.1 逐日 TWR 模拟状态 ----
    # 现金以资金余额为权威（缺失时按估计增量续链；流水模式从 0 起按发生金额累加）；
    # 持仓估值价 = 最近成交价；
    # 合成持仓（期初持仓变现价值）从模拟开始计入期初资产，未配对卖出时同步扣减。
    synthetic_remaining: Dict[str, float] = dict(unmatched_by_stock)
    last_price: Dict[str, float] = {}
    repo_receivable = 0.0  # v2.1：逆回购本金按 1:1「应收款」跟踪（价值中性）
    cash = initial_balance
    current_day: Optional[date] = None
    day_flow = 0.0
    prev_day_end = opening_asset_value
    cum_v = 1.0
    daily_v: List[float] = []  # 每个有效交易日结算后的累计 (1 + R)
    daily_dates: List[date] = []  # 与 daily_v 对齐：每个有效交易日的实际日期（v2.3 事件明细）
    day_R: Dict[date, Optional[float]] = {}  # 每个记录日结算后的累计 R（跳过日为 None）

    def finalize_twr_day() -> None:
        """结算当前交易日的 r_d（v2.1 1.0）。

        r_d = (当日末资产 − 当日净出入金 − 前一日末资产) / 前一日末资产；
        前一日末资产 ≤ 0 的日跳过；出入金只影响分子扣除；分红/利息/逆回购到期
        利息计为盈亏（不扣出入金）；逆回购本金以「应收款」1:1 跟踪（价值中性，
        仅费用/利息计盈亏），1 日回购影响本就极小，R-007 等多日回购也不会扭曲
        日收益率。
        """
        nonlocal cum_v, prev_day_end
        assets_end = (
            cash
            + sum(
                state.holding_qty() * last_price.get(code, 0.0)
                for code, state in stocks.items()
            )
            + sum(synthetic_remaining.values())
            + repo_receivable
        )
        r_d: Optional[float] = None
        if prev_day_end > _EPS:
            r_d = (assets_end - day_flow - prev_day_end) / prev_day_end
            cum_v *= 1.0 + r_d
        prev_day_end = assets_end
        day_R[current_day] = None if r_d is None else cum_v - 1.0
        if r_d is not None:
            daily_v.append(cum_v)
            daily_dates.append(current_day)

    def update_max_pos(code: str, name: str, trade_date: date) -> None:
        nonlocal max_pos_ratio, max_pos_code, max_pos_name, max_pos_date
        assets = last_balance + holding_cost_now()
        state = stocks.get(code)
        if state is None or assets <= _EPS:
            return
        cost = state.holding_cost()
        if cost <= _EPS:
            return
        ratio = cost / assets
        if ratio > max_pos_ratio:
            max_pos_ratio = ratio
            max_pos_code = code
            max_pos_name = name
            max_pos_date = trade_date.isoformat()

    for rec in recs:
        month = f"{rec.date.year:04d}-{rec.date.month:02d}"
        if prev_month is None:
            prev_month = month
        elif month != prev_month:
            snapshots.append(
                (prev_month, last_date, last_balance, holding_cost_now())
            )
            prev_month = month
        last_date = rec.date

        # v2.1：交易日切换时先结算上一交易日的 r_d
        if current_day is None:
            current_day = rec.date
        elif rec.date != current_day:
            finalize_twr_day()
            current_day = rec.date
            day_flow = 0.0

        cash_delta = (
            _estimate_cash_delta(rec)
            if flow_mode or rec.balance is None
            else rec.balance - prev_balance
        )
        # 现金：资金余额为权威；缺失或流水模式时按估计增量续链（流水模式 = cash_amount 从 0 起累加）
        if flow_mode or rec.balance is None:
            cash += cash_delta
            last_balance = cash
            balances.append(cash)
        else:
            cash = rec.balance
            last_balance = rec.balance
            balances.append(rec.balance)
        total_cost += rec.cost_fields

        if rec.code:
            code_name[rec.code] = rec.name or code_name.get(rec.code, "")

        op = rec.op
        if op in (OP_TRANSFER_IN, OP_TRANSFER_OUT):
            net_transfer += cash_delta
            month_transfer[month] += cash_delta
            day_flow += cash_delta  # v2.1：当日净出入金（只影响 r_d 分子）
            if cash_delta > 0:
                gross_deposit += cash_delta
                month_deposit[month] += cash_delta
            else:
                gross_withdraw += -cash_delta
        elif op in (OP_REVERSE_REPO, OP_DIVIDEND, OP_INTEREST, OP_IPO, OP_OTHER):
            sp = special[op]
            sp["count"] += 1
            sp["amount"] += abs(cash_delta) if op == OP_REVERSE_REPO else cash_delta
            if op == OP_REVERSE_REPO:
                # v2.1：逆回购本金 1:1 应收款——融出腿记入、到期腿按成交金额回补扣减，
                # 仅费用/利息计为当日盈亏。1 日回购影响本已极小；R-007 等多日回购
                # 若按纯盈亏处理会使单日 r_d 击穿 -100%，(1+R) 失真（见 1.0 注释）。
                if cash_delta < -_EPS:
                    repo_receivable += (
                        rec.amount if rec.amount > _EPS else -cash_delta
                    )
                elif cash_delta > _EPS:
                    principal = rec.amount if rec.amount > _EPS else cash_delta
                    repo_receivable = max(0.0, repo_receivable - principal)
        elif op == OP_BONUS_SHARE:
            sp = special[op]
            sp["count"] += 1
            sp["amount"] += rec.qty
            state = stocks.get(rec.code)
            if state is not None and rec.qty > 0:
                total_qty = state.holding_qty()
                if total_qty > _EPS:
                    factor = 1.0 + rec.qty / total_qty
                    for lot in state.lots:
                        lot.qty *= factor
                        lot.unit_cost /= factor  # 红股摊薄单位成本，成本基础总额不变
                    if state.cycle is not None:
                        state.cycle.acquired_qty += rec.qty
        elif op in (OP_BUY, OP_SELL) and rec.qty > _EPS:
            code = rec.code
            if rec.price > _EPS:
                last_price[code] = rec.price  # v2.1：估值价 = 最近成交价
            state = stocks.setdefault(code, _StockState())
            equity_dates.add(rec.date)
            counts["total"] += 1
            code_count[code] += 1
            code_first_date[code] = min(
                code_first_date.get(code, rec.date), rec.date
            )
            code_last_date[code] = max(code_last_date.get(code, rec.date), rec.date)
            notional = rec.amount if rec.amount > _EPS else rec.gross
            turnover["total"] += notional
            code_turnover[code] += notional
            activity[month]["total"] += 1
            if op == OP_BUY:
                counts["buy"] += 1
                turnover["buy"] += notional
                activity[month]["buy"] += 1
                code_buy_count[code] += 1
                code_buy_qty[code] += rec.qty
                code_buy_amount[code] += notional
                cost = -cash_delta if cash_delta < -_EPS else rec.gross + rec.cost_fields
                state.lots.append(_Lot(rec.qty, cost / rec.qty, rec.date))
                if state.cycle is None:
                    state.cycle = _Cycle(start_date=rec.date)
                state.cycle.bought_qty += rec.qty
                state.cycle.acquired_qty += rec.qty
                state.cycle.buy_cost += cost
                update_max_pos(code, rec.name, rec.date)
            else:
                counts["sell"] += 1
                turnover["sell"] += notional
                activity[month]["sell"] += 1
                code_sell_count[code] += 1
                code_sell_qty[code] += rec.qty
                code_sell_amount[code] += notional
                proceeds = cash_delta if cash_delta > _EPS else rec.gross - rec.cost_fields
                remaining = rec.qty
                pnl = 0.0
                matched_qty = 0.0
                days_wsum = 0.0
                while remaining > _EPS and state.lots:
                    lot = state.lots[0]
                    take = min(lot.qty, remaining)
                    cost_taken = take * lot.unit_cost
                    allocated = proceeds * take / rec.qty
                    pnl += allocated - cost_taken
                    matched_qty += take
                    days_wsum += take * (rec.date - lot.buy_date).days
                    if state.cycle is not None:
                        state.cycle.sold_qty += take
                        state.cycle.proceeds += allocated
                    lot.qty -= take
                    if lot.qty <= _EPS:
                        state.lots.popleft()
                    remaining -= take
                if remaining > _EPS:
                    state.pre_qty += remaining
                    unmatched_amount = proceeds * remaining / rec.qty
                    unmatched_sell_amount += unmatched_amount
                    # v2.1：未配对卖出同步扣减合成持仓（价值 1:1），
                    # 期初持仓变现不产生虚假收益
                    synthetic_remaining[code] = max(
                        0.0, synthetic_remaining.get(code, 0.0) - unmatched_amount
                    )
                if matched_qty > _EPS:
                    realized_total += pnl
                    realized_by_month[month] += pnl
                    code_realized[code] += pnl
                if (
                    state.cycle is not None
                    and state.cycle.acquired_qty > _EPS
                    and state.cycle.sold_qty >= state.cycle.acquired_qty - _EPS
                ):
                    state.cycle.end_date = rec.date
                    cycle_pnl = state.cycle.proceeds - state.cycle.buy_cost
                    hold_days = (rec.date - state.cycle.start_date).days + 1
                    completed_trades.append(
                        {
                            "code": code,
                            "name": code_name.get(code, rec.name or ""),
                            "buy_qty": _round2(state.cycle.bought_qty),
                            "buy_amount": _round2(state.cycle.buy_cost),
                            "sell_qty": _round2(state.cycle.sold_qty),
                            "sell_amount": _round2(state.cycle.proceeds),
                            "pnl": _round2(cycle_pnl),
                            "holding_days": hold_days,
                            "start_date": state.cycle.start_date.isoformat(),
                            "end_date": state.cycle.end_date.isoformat(),
                            "status": "closed",
                        }
                    )
                    cycle_hold_days_sum += hold_days
                    cycle_hold_count += 1
                    if hold_days <= 1:
                        dist["d1"] += 1
                    elif hold_days <= 3:
                        dist["d2_3"] += 1
                    elif hold_days <= 5:
                        dist["d4_5"] += 1
                    elif hold_days <= 10:
                        dist["d6_10"] += 1
                    elif hold_days <= 20:
                        dist["d11_20"] += 1
                    elif hold_days <= 30:
                        dist["d21_30"] += 1
                    elif hold_days <= 60:
                        dist["d31_60"] += 1
                    else:
                        dist["gt60"] += 1
                    if cycle_pnl > _EPS:
                        cycle_win_count += 1
                        cycle_total_profit += cycle_pnl
                        cycle_max_profit = (
                            cycle_pnl
                            if cycle_max_profit is None
                            else max(cycle_max_profit, cycle_pnl)
                        )
                    elif cycle_pnl < -_EPS:
                        cycle_loss_count += 1
                        cycle_total_loss += -cycle_pnl
                        cycle_max_loss = (
                            cycle_pnl
                            if cycle_max_loss is None
                            else min(cycle_max_loss, cycle_pnl)
                        )
                    state.cycle = None
                update_max_pos(code, rec.name, rec.date)

        if rec.balance is not None:
            prev_balance = rec.balance

    if current_day is not None:
        finalize_twr_day()  # 结算最后一个交易日
    ending_balance = last_balance
    if prev_month is not None:
        snapshots.append((prev_month, last_date, ending_balance, holding_cost_now()))

    # ---- 期末持仓估值（akshare 最新价，失败按成本兜底） ----
    held_codes = sorted(
        code for code, state in stocks.items() if state.holding_qty() > _EPS
    )
    prices: Dict[str, float] = {}
    market_value_source = "cost"
    valuation_date: Optional[str] = None
    if held_codes:
        fetched = _fetch_latest_prices(held_codes, timeout=price_timeout)
        if fetched:
            market_value_source = "akshare"
            valuation_date = date.today().isoformat()
            prices = fetched

    holding_market_value = 0.0
    holding_cost_value = holding_cost_now()
    code_unrealized: Dict[str, float] = {}
    for code in held_codes:
        state = stocks[code]
        cost = state.holding_cost()
        qty = state.holding_qty()
        price = prices.get(code)
        if price is None or price <= 0:
            price = cost / qty if qty > _EPS else 0.0
        market_value = qty * price
        holding_market_value += market_value
        code_unrealized[code] = market_value - cost

    # 期初资产 A0 = 期初资金 + 期初持仓变现估值（已在预扫描后确定，
    # 供 v2.1 逐日模拟与对照口径共用）

    # ---- 月末资产近似净值（equity_curve 保留原始净值供调试） ----
    # 月度近似资产 equity_m = 月末资金余额 + 月末持仓成本；
    # 无记录月份沿用上一月末余额/持仓成本（月末最后一天为快照日）。
    months = _months_between(start_date, end_date)
    snap_by_month = {month: (d, bal, cost) for month, d, bal, cost in snapshots}
    expanded: List[Tuple[str, date, float, float]] = []
    carried: Optional[Tuple[float, float]] = None
    for month in months:
        if month in snap_by_month:
            snapshot_date, balance, holding_cost = snap_by_month[month]
            carried = (balance, holding_cost)
        else:
            snapshot_date = _month_end_date(
                int(month[:4]), int(month[5:7])
            )
            balance, holding_cost = carried if carried is not None else (0.0, 0.0)
        expanded.append((month, snapshot_date, balance, holding_cost))

    equity_curve: List[Dict[str, Any]] = []
    base_equity: Optional[float] = None
    peak_equity: Optional[float] = None
    for month, snapshot_date, balance, holding_cost in expanded:
        equity = balance + holding_cost
        if base_equity is None:
            base_equity = equity
        peak_equity = equity if peak_equity is None else max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > _EPS else 0.0
        equity_curve.append(
            {
                "month": month,
                "date": snapshot_date.isoformat(),
                "balance": _round2(balance),
                "holding_cost": _round2(holding_cost),
                "equity": _round2(equity),
                "net_value": _round4(equity / base_equity)
                if base_equity and base_equity > _EPS
                else 0.0,
                "drawdown": _round4(drawdown),
            }
        )

    # ---- v2.1 收益率曲线：每月末累计 R（无记录月份沿用上一值补齐） ----
    last_record_day: Dict[str, date] = {}
    for rec in recs:
        last_record_day[f"{rec.date.year:04d}-{rec.date.month:02d}"] = rec.date
    return_curve: List[Dict[str, Any]] = []
    carried_r: Optional[float] = None
    for month in months:
        record_day = last_record_day.get(month)
        if record_day is None:
            r_val = carried_r
            snapshot_date = _month_end_date(int(month[:4]), int(month[5:7]))
        else:
            r_val = day_R.get(record_day)
            if r_val is None:
                r_val = carried_r
            snapshot_date = record_day
        if r_val is not None:
            carried_r = r_val
        return_curve.append(
            {
                "month": month,
                "date": snapshot_date.isoformat(),
                "return_rate": _round4(r_val) if r_val is not None else None,
            }
        )

    # ---- v2.1 最大回撤：基于逐日 (1 + R) 序列（正值，相对峰值跌幅） ----
    # 峰值起点为 v0 = 1 + R0 = 1.0（首日即下跌也按起点计回撤）。
    max_drawdown = 0.0
    peak_v = 1.0
    for v in daily_v:
        if v > peak_v:
            peak_v = v
        max_drawdown = max(max_drawdown, (peak_v - v) / peak_v)

    # ---- 账户级翻倍 / 腰斩（1.4 v2.2/v2.3）：逐日 v = 1 + R 序列 ----
    # 翻倍（v2.1 不变）：累计 R 从 < 100% 升到 ≥ 100% 计 1 次独立事件
    #（禁止「从运行低点翻倍」——接近归零的账户会产生假阳性）；
    # 腰斩（v2.2 递进式）：floor 初始 1.0，v > floor → floor = v（新高重置），
    # v ≤ floor × 0.5 → 计 1 次且 floor = v（连续下跌逐级计数），
    # 回升超过当前 floor 才重置基线；
    # 事件明细（v2.3）：double_events/halved_events 记录触发日的实际交易日
    # 与当日累计 R，double_count/halved_count = 数组长度。
    double_events = _double_events(daily_dates, daily_v)
    halved_events = _halved_events(daily_dates, daily_v)
    double_count = len(double_events)
    halved_count = len(halved_events)

    # ---- v2.1 收益率：主口径 = 逐日 TWR 最终 R ----
    total_return_rate = None if not daily_v else cum_v - 1.0

    # ---- 辅口径（对照）：期初资产基准简单收益率；A0=0 退化累计入金基准 ----
    end_assets = ending_balance + holding_market_value
    total_return_rate_net = None
    if opening_asset_value > _EPS:
        total_return_rate_net = (
            end_assets - opening_asset_value - net_transfer
        ) / opening_asset_value
    elif gross_deposit > _EPS:
        total_return_rate_net = (end_assets - gross_deposit) / gross_deposit

    span_days = (end_date - start_date).days
    annualized_return_rate = None
    if (
        total_return_rate is not None
        and total_return_rate > -0.5
        and span_days > 0
    ):
        annualized_return_rate = (1.0 + total_return_rate) ** (365.0 / span_days) - 1.0

    # ---- 交易统计 ----
    total_count = counts["total"]
    active_days = len(equity_dates)
    avg_balance = sum(balances) / len(balances) if balances else 0.0
    avg_trade_amount = turnover["total"] / total_count if total_count else 0.0
    daily_avg_count = total_count / active_days if active_days else 0.0
    daily_avg_amount = turnover["total"] / active_days if active_days else 0.0
    capital_turnover_rate = (
        turnover["total"] / avg_balance if avg_balance > _EPS else None
    )
    avg_holding_days = (
        cycle_hold_days_sum / cycle_hold_count if cycle_hold_count else None
    )
    win_count = cycle_win_count
    loss_count = cycle_loss_count
    total_profit = cycle_total_profit
    total_loss = cycle_total_loss
    max_profit = cycle_max_profit
    max_loss = cycle_max_loss
    win_rate = (
        win_count / (win_count + loss_count) if (win_count + loss_count) else None
    )
    profit_loss_ratio = (
        total_profit / total_loss if total_loss > _EPS else None
    )
    total_cost_ratio = (
        total_cost / turnover["total"] if turnover["total"] > _EPS else 0.0
    )

    monthly_pnl = [
        {"month": month, "pnl": _round2(realized_by_month.get(month, 0.0))}
        for month in months
    ]
    monthly_activity = [
        {
            "month": month,
            "total_count": activity[month]["total"],
            "buy_count": activity[month]["buy"],
            "sell_count": activity[month]["sell"],
        }
        for month in months
    ]

    # ---- 个股维度 ----
    top5_codes = sorted(code_turnover.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top5_amount = sum(value for _, value in top5_codes)
    top5_concentration = (
        top5_amount / turnover["total"] if turnover["total"] > _EPS else 0.0
    )
    favorite_sorted = sorted(
        code_count.items(),
        key=lambda kv: (-kv[1], -code_turnover[kv[0]], kv[0]),
    )[:10]
    favorite_stocks_top10 = [
        {
            "code": code,
            "name": code_name.get(code, ""),
            "count": count,
            "amount": _round2(code_turnover[code]),
        }
        for code, count in favorite_sorted
    ]
    leaderboard_entries: List[Dict[str, Any]] = []
    for code in set(code_realized) | set(code_unrealized):
        realized = code_realized.get(code, 0.0)
        unrealized = code_unrealized.get(code, 0.0)
        total = realized + unrealized
        leaderboard_entries.append(
            {
                "code": code,
                "name": code_name.get(code, ""),
                "realized_pnl": _round2(realized),
                "unrealized_pnl": _round2(unrealized),
                "total_pnl": _round2(total),
                "trade_count": code_count.get(code, 0),
            }
        )
    stock_stats: List[Dict[str, Any]] = []
    for code in sorted(code_count, key=lambda c: (-code_turnover[c], c)):
        first = code_first_date.get(code)
        last = code_last_date.get(code)
        realized = code_realized.get(code, 0.0)
        unrealized = code_unrealized.get(code, 0.0)
        stock_stats.append(
            {
                "code": code,
                "name": code_name.get(code, ""),
                "buy_count": code_buy_count.get(code, 0),
                "buy_qty": _round2(code_buy_qty.get(code, 0.0)),
                "buy_amount": _round2(code_buy_amount.get(code, 0.0)),
                "sell_count": code_sell_count.get(code, 0),
                "sell_qty": _round2(code_sell_qty.get(code, 0.0)),
                "sell_amount": _round2(code_sell_amount.get(code, 0.0)),
                "turnover": _round2(code_turnover[code]),
                "realized_pnl": _round2(realized),
                "unrealized_pnl": _round2(unrealized),
                "total_pnl": _round2(realized + unrealized),
                "first_date": first.isoformat() if first else None,
                "last_date": last.isoformat() if last else None,
                "holding_days": (last - first).days + 1 if first and last else 0,
                "status": "held" if code in held_codes else "closed",
            }
        )
    top_profit = sorted(
        (entry for entry in leaderboard_entries if (entry["total_pnl"] or 0) > 0),
        key=lambda entry: entry["total_pnl"],
        reverse=True,
    )[:10]
    top_loss = sorted(
        (entry for entry in leaderboard_entries if (entry["total_pnl"] or 0) < 0),
        key=lambda entry: entry["total_pnl"],
    )[:10]

    # ---- 风格初判（规则引擎） ----
    holding_style = (
        "短线"
        if avg_holding_days is not None and avg_holding_days <= 7
        else "波段"
        if avg_holding_days is None or avg_holding_days <= 30
        else "长线"
    )
    concentration_style = "集中" if top5_concentration >= 0.5 else "分散"
    aggressive = (
        max_pos_ratio >= 0.5
        or (capital_turnover_rate is not None and capital_turnover_rate >= 15)
        or (avg_holding_days is not None and avg_holding_days <= 3)
    )
    conservative = (
        max_pos_ratio <= 0.25
        and (avg_holding_days is None or avg_holding_days >= 15)
        and (capital_turnover_rate is None or capital_turnover_rate <= 5)
    )
    risk_style = "激进" if aggressive else ("稳健" if conservative else "均衡")
    style_label = f"{holding_style}·{concentration_style}·{risk_style}"

    return MetricsResult(
        {
            "meta": {
                "is_partial": True
                if flow_mode
                else (
                    abs(initial_balance) > _EPS
                    or any(state.pre_qty > _EPS for state in stocks.values())
                ),
                "balance_source": "flow" if flow_mode else "balance",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "calendar_days": span_days + 1,
                "active_trading_days": active_days,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "account": {
                "initial_balance": _round2(initial_balance),
                "ending_balance": _round2(ending_balance),
                "net_transfer_in": _round2(net_transfer),
                "gross_deposit": _round2(gross_deposit),
                "gross_withdraw": _round2(gross_withdraw),
                "opening_asset_value": _round2(opening_asset_value),
                "total_return_rate": _round4(total_return_rate)
                if total_return_rate is not None
                else None,
                "total_return_rate_net": _round4(total_return_rate_net)
                if total_return_rate_net is not None
                else None,
                "annualized_return_rate": _round4(annualized_return_rate)
                if annualized_return_rate is not None
                else None,
                "realized_pnl": _round2(realized_total),
                "total_cost": _round2(total_cost),
                "total_cost_ratio": _round4(total_cost_ratio),
                "holding_market_value": _round2(holding_market_value),
                "holding_cost_value": _round2(holding_cost_value),
                "unrealized_pnl": _round2(holding_market_value - holding_cost_value),
                "market_value_source": market_value_source,
                "valuation_date": valuation_date,
            },
            "trading": {
                "total_amount": _round2(turnover["total"]),
                "total_count": total_count,
                "buy_count": counts["buy"],
                "buy_amount": _round2(turnover["buy"]),
                "sell_count": counts["sell"],
                "sell_amount": _round2(turnover["sell"]),
                "daily_avg_count": _round4(daily_avg_count),
                "daily_avg_amount": _round2(daily_avg_amount),
                "distinct_stock_count": len(
                    {code for code in code_count if code_count[code] > 0}
                ),
                "current_holding_count": len(held_codes),
                "avg_trade_amount": _round2(avg_trade_amount),
                "capital_turnover_rate": _round2(capital_turnover_rate)
                if capital_turnover_rate is not None
                else None,
                "avg_holding_period_days": _round2(avg_holding_days)
                if avg_holding_days is not None
                else None,
            },
            "pnl": {
                "realized_pnl": _round2(realized_total),
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate": _round4(win_rate) if win_rate is not None else None,
                "total_profit": _round2(total_profit),
                "total_loss": _round2(total_loss),
                "profit_loss_ratio": _round4(profit_loss_ratio)
                if profit_loss_ratio is not None
                else None,
                "max_single_profit": _round2(max_profit) if max_profit is not None else 0.0,
                "max_single_loss": _round2(max_loss) if max_loss is not None else 0.0,
                "double_count": double_count,
                "halved_count": halved_count,
                "double_events": double_events,
                "halved_events": halved_events,
                "unmatched_sell_amount": _round2(unmatched_sell_amount),
                "monthly_pnl": monthly_pnl,
                "equity_curve": equity_curve,
                "return_curve": return_curve,
                "max_drawdown": _round4(max_drawdown),
                "stock_leaderboard": {
                    "top_profit": top_profit,
                    "top_loss": top_loss,
                },
            },
            "behavior": {
                "holding_period_distribution": dist,
                "monthly_activity": monthly_activity,
                "max_position": {
                    "ratio": _round4(max_pos_ratio),
                    "code": max_pos_code,
                    "name": max_pos_name,
                    "date": max_pos_date,
                },
                "top5_concentration": _round4(top5_concentration),
                "favorite_stocks_top10": favorite_stocks_top10,
                "style": {
                    "holding_style": holding_style,
                    "concentration": concentration_style,
                    "risk_style": risk_style,
                    "label": style_label,
                },
                "special_operations": {
                    "reverse_repo": {
                        "count": int(special[OP_REVERSE_REPO]["count"]),
                        "amount": _round2(special[OP_REVERSE_REPO]["amount"]),
                    },
                    "dividend": {
                        "count": int(special[OP_DIVIDEND]["count"]),
                        "amount": _round2(special[OP_DIVIDEND]["amount"]),
                    },
                    "bonus_share": {
                        "count": int(special[OP_BONUS_SHARE]["count"]),
                        "qty": _round2(special[OP_BONUS_SHARE]["amount"]),
                    },
                    "interest": {
                        "count": int(special[OP_INTEREST]["count"]),
                        "amount": _round2(special[OP_INTEREST]["amount"]),
                    },
                    "ipo": {
                        "count": int(special[OP_IPO]["count"]),
                        "amount": _round2(special[OP_IPO]["amount"]),
                    },
                    "other": {
                        "count": int(special[OP_OTHER]["count"]),
                        "amount": _round2(special[OP_OTHER]["amount"]),
                    },
                },
            },
            "stocks": stock_stats,
            "trades": completed_trades,
        }
    )


__all__ = ["MetricsResult", "compute_metrics"]
