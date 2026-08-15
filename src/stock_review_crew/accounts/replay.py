# -*- coding: utf-8 -*-
"""R14 账户全量重放：只消费 plan.json + 行情缓存（确定性，不调 LLM）。

口径（docs/requirements-r14.md §八 + A4 验收契约，已确认）：
- 结算日序列 = 已存在复盘产物（data/reviews/{date}/plan.json）的日期集合；
- 撮合日 = 结算日序列中 ≥ rec_date 的最近一天：plan.json 当日计划用当日行情撮合
  （A4 验收契约 test_account_replay.py；engine.place_order，
  Q1 口径 code/side/target_price/position_cheng + day）；
  rec_date 晚于最后一个结算日的保持未结算（未到结算日）并在 meta/报告中计数；
- 从零开户 → 逐日撮合 → trades/snapshots 落盘 data/accounts/{skill_id}/
  （结构沿用 storage.save_account，R12 口径不变）；
- 合规断言移植 scripts/backtest_accounts.py 的 AssertionChecker：
  超资金 / T+1 / 涨跌停越界 / 非整百 / 裸卖空；违规 → violations + 退出码 1；
- 输出 .tmp/account_replay/replay_report.md + replay_meta.json
  （plan.json 内容哈希、行情缺失标注、未结算计划数）；
- 确定性：同输入（同 plan.json + 同行情缓存/fixture）同输出，
  不依赖时间/随机；绝不读 pending.json。

退出码：0=无违规，1=发现引擎漏洞/断言失败，2=数据不可用（无复盘产物）。

用法：:

    python -m stock_review_crew.accounts.replay [--start D] [--end D] [--fixture]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Optional

from . import engine as acct_engine
from . import storage as acct_storage
from .quotes import ensure_quotes

INITIAL_CASH = acct_engine.INITIAL_CASH
DISCLAIMER = "仅供参考，不构成投资建议"

# 拒绝原因分布口径（requirements-r14.md §四.4.4 / R12-3）
REJECT_REASONS = [
    "超资金",
    "T+1",
    "涨停拒买",
    "跌停拒卖",
    "未触及目标价",
    "非整百",
    "裸卖空",
]


def _base_root() -> Path:
    """数据根：优先 STOCK_REVIEW_DATA_DIR（测试隔离），否则仓库 data/。"""
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data"


def reviews_root() -> Path:
    """复盘产物根：``data/reviews``。"""
    return _base_root() / "reviews"


def default_out_dir() -> Path:
    """重放报告目录：默认 ``<数据根>/../.tmp/account_replay``（测试隔离时随数据根）。"""
    base = _base_root()
    if os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip():
        return base / ".tmp" / "account_replay"
    return base.parent / ".tmp" / "account_replay"


def plan_path(date_str: str) -> Path:
    return reviews_root() / date_str / "plan.json"


def list_review_dates(start: Optional[str] = None, end: Optional[str] = None) -> list[str]:
    """结算日序列 = 已存在 plan.json 的复盘日期集合（升序；start/end 含边界）。"""
    root = reviews_root()
    dates: list[str] = []
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            if (d / "plan.json").exists():
                dates.append(d.name)
    if start:
        dates = [x for x in dates if x >= str(start)]
    if end:
        dates = [x for x in dates if x <= str(end)]
    return dates


def trading_days(start: Optional[str] = None, end: Optional[str] = None) -> list[_date]:
    """结算日序列（date 对象，升序）：已存在 plan.json 的复盘日期集合
    （周末/节假日不硬编码，自适应；非法日期目录跳过）。"""
    days: list[_date] = []
    for d in list_review_dates(start, end):
        try:
            y, m, dd = d.split("-")
            days.append(_date(int(y), int(m), int(dd)))
        except (ValueError, TypeError):
            continue
    return days


def plan_sha256(date_str: str) -> str:
    """plan.json 内容哈希（sha256，确定性指纹）。文件缺失 → 空串。"""
    p = plan_path(date_str)
    try:
        if p.exists():
            return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _load_plan_file(date_str: str) -> tuple[list[dict], str]:
    """读取单日 plan.json → (plans, error)。缺失/损坏 → ([], 中文标注)，绝不静默。"""
    p = plan_path(date_str)
    try:
        if not p.exists():
            return [], f"{date_str} plan.json 缺失"
        data = json.loads(p.read_text(encoding="utf-8"))
        plans = data.get("plans") if isinstance(data, dict) else None
        if plans is None:
            return [], f"{date_str} plan.json 缺少 plans 数组"
        if not isinstance(plans, list):
            return [], f"{date_str} plan.json plans 非数组"
        return [x for x in plans if isinstance(x, dict)], ""
    except Exception as exc:  # noqa: BLE001
        return [], f"{date_str} plan.json 解析失败：{exc}"


def _limit_pct(code: str) -> Optional[float]:
    """涨跌幅系数（与 engine 口径一致）：科创板/创业板 ±20%，主板 ±10%，其余不支持。"""
    c = str(code or "")
    if c.startswith(("8", "4", "92")):
        return None
    if c.startswith(("688", "689", "300", "301")):
        return 0.20
    if c.startswith(("0", "6")):
        return 0.10
    return None


def _classify_reject(reason: str) -> str:
    """拒绝原因 → 分布口径桶（超资金/T+1/涨停拒买/跌停拒卖/未触及目标价/非整百/裸卖空）。"""
    r = str(reason or "")
    if "T+1" in r or "当日买入不可卖" in r or "冻结" in r or "超过可卖持仓" in r:
        return "T+1"
    if "涨停价" in r:
        return "涨停拒买"
    if "跌停价" in r:
        return "跌停拒卖"
    if "未触及目标价" in r:
        return "未触及目标价"
    if "资金不足" in r or "超资金" in r or "现金" in r:
        return "超资金"
    if "非整百" in r or "整数倍" in r:
        return "非整百"
    if "裸卖空" in r or "无持仓" in r:
        return "裸卖空"
    return "其他"


def plan_hash(dates: list[str]) -> str:
    """全部 plan.json 的组合内容哈希（确定性指纹，同输入同输出）。"""
    h = hashlib.sha256()
    for d in dates:
        h.update(str(d).encode("utf-8"))
        h.update(plan_sha256(d).encode("utf-8"))
    return h.hexdigest()


@dataclass
class Violation:
    """重放断言发现的引擎漏洞（未被引擎拦截的违规）。"""

    date: str
    skill_id: str
    rule: str
    detail: str
    order_desc: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnalystSummary:
    skill_id: str
    name: str
    filled: int = 0
    rejected: int = 0
    reject_reasons: dict = field(default_factory=dict)
    final_assets: float = INITIAL_CASH
    return_rate: float = 0.0
    max_drawdown: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["final_assets"] = round(self.final_assets, 2)
        d["return_rate"] = round(self.return_rate, 6)
        d["max_drawdown"] = round(self.max_drawdown, 6)
        return d


class AssertionChecker:
    """每日断言（移植 scripts/backtest_accounts.py，适配 storage 账户结构）：
    (a) 现金≥0（超资金）；(b) 持仓≥0 且卖出≤可用持仓（T+1）；(c) 成交价∈[跌停,涨停]
    （涨跌停越界）；(d) 买入数量 100 整数倍（非整百）；(e) 裸卖空/费用≥0。
    裸卖空按两层判定：日初持仓（历史成交推算）为负，或当日累计卖出超过
    日初持仓 + 当日买入（卖出了从未持有的份额）。
    """

    @staticmethod
    def check_day(acct: dict, day: str, day_records: list[dict],
                  quotes: dict[str, dict]) -> list[Violation]:
        violations: list[Violation] = []
        eps = 1e-6
        skill_id = str(acct.get("skill_id") or "unknown")

        # (a) 超资金
        cash = float(acct.get("cash") or 0.0)
        if cash < -eps:
            violations.append(Violation(
                date=day, skill_id=skill_id, rule="超资金",
                detail=f"每日结束后现金为 {cash:.2f}（超支）",
                order_desc=f"cash={cash:.2f}"))

        positions = acct.get("positions") or {}
        for code, pos in positions.items():
            if int(pos.get("qty") or 0) < 0:
                violations.append(Violation(
                    date=day, skill_id=skill_id, rule="裸卖空",
                    detail=f"{code} 持仓为 {pos['qty']}（负持仓）",
                    order_desc=code))

        bought_today = Counter()
        sold_today = Counter()
        for rec in day_records:
            if rec.get("status") != "成交":
                continue
            if rec.get("side") == "buy":
                bought_today[str(rec.get("code") or "")] += int(rec.get("qty") or 0)
            elif rec.get("side") == "sell":
                sold_today[str(rec.get("code") or "")] += int(rec.get("qty") or 0)

        # 日初持仓：按 date < day 的历史成交推算（买 +、卖 −）
        start_holdings: Counter = Counter()
        for t in acct.get("trades") or []:
            if t.get("status") != "成交":
                continue
            if str(t.get("date") or "") >= day:
                continue
            tc = str(t.get("code") or "")
            if t.get("side") == "buy":
                start_holdings[tc] += int(t.get("qty") or 0)
            elif t.get("side") == "sell":
                start_holdings[tc] -= int(t.get("qty") or 0)

        for rec in day_records:
            if rec.get("status") != "成交":
                continue
            side = rec.get("side")
            qty = int(rec.get("qty") or 0)
            price = float(rec.get("price") or 0.0)
            code = str(rec.get("code") or "")
            q = quotes.get(code)
            if side == "sell":
                pos_after = int(positions.get(code, {}).get("qty") or 0)
                # 卖出前可用 = 成交后剩余 + 本次卖出 − 当日已买入（T+1）
                avail_before = pos_after + qty - bought_today[code]
                if pos_after < 0 or avail_before < 0:
                    violations.append(Violation(
                        date=day, skill_id=skill_id, rule="T+1",
                        detail=f"{code} 卖出 {qty} 超过可用持仓"
                               f"（卖出前可用 {avail_before}，成交后持仓 {pos_after}）",
                        order_desc=f"{code} sell {qty}"))
                # 裸卖空：日初持仓为负 = 卖出了从未持有的份额
                start_qty = pos_after + sold_today[code] - bought_today[code]
                if start_qty < 0:
                    violations.append(Violation(
                        date=day, skill_id=skill_id, rule="裸卖空",
                        detail=f"{code} 卖出 {qty} 无对应持仓（日初持仓 {start_qty}）",
                        order_desc=f"{code} sell {qty}"))
                # 裸卖空：当日累计卖出 > 日初持仓 + 当日买入（份额凭空出现）
                if sold_today[code] > start_holdings[code] + bought_today[code]:
                    violations.append(Violation(
                        date=day, skill_id=skill_id, rule="裸卖空",
                        detail=f"{code} 当日卖出 {sold_today[code]} 股超过"
                               f"日初持仓 {start_holdings[code]} + 当日买入"
                               f" {bought_today[code]}",
                        order_desc=f"{code} sell {qty}"))
            if side == "buy" and qty % 100 != 0:
                violations.append(Violation(
                    date=day, skill_id=skill_id, rule="非整百",
                    detail=f"{code} 买入 {qty} 非整百（买入数量须为 100 整数倍）",
                    order_desc=f"{code} buy {qty}"))
            if q is not None and q.get("pre_close") is not None:
                pct = _limit_pct(code)
                if pct is not None:
                    limit_up, limit_down = acct_engine.limit_prices(
                        float(q["pre_close"]), pct)
                    if price < limit_down - eps:
                        violations.append(Violation(
                            date=day, skill_id=skill_id, rule="跌停越界",
                            detail=f"{code} 成交价 {price} 低于跌停价 {limit_down}",
                            order_desc=f"{code} @{price}"))
                    if price > limit_up + eps:
                        violations.append(Violation(
                            date=day, skill_id=skill_id, rule="涨停越界",
                            detail=f"{code} 成交价 {price} 高于涨停价 {limit_up}",
                            order_desc=f"{code} @{price}"))
            fee = float(rec.get("fee") or 0.0)
            stamp = float(rec.get("stamp_tax") or 0.0)
            transfer = float(rec.get("transfer_fee") or 0.0)
            if fee < -eps or stamp < -eps or transfer < -eps:
                violations.append(Violation(
                    date=day, skill_id=skill_id, rule="费用为负",
                    detail=f"{code} 费用为负：fee={fee} stamp={stamp} transfer={transfer}",
                    order_desc=f"{code} {side}"))
        return violations


def compute_summary(skill_id: str, acct: dict, name: str = "") -> AnalystSummary:
    """期末资产/收益率（期末/期初-1）/最大回撤（正数）/成交与拒绝统计。"""
    summary = AnalystSummary(skill_id=skill_id, name=name or skill_id)
    for t in acct.get("trades") or []:
        if t.get("status") == "成交":
            summary.filled += 1
        else:
            summary.rejected += 1
            reason = str(t.get("reject_reason") or "其他")
            summary.reject_reasons[_classify_reject(reason)] = \
                summary.reject_reasons.get(_classify_reject(reason), 0) + 1
    snaps = acct.get("snapshots") or []
    if snaps:
        summary.final_assets = float(snaps[-1]["total_assets"])
        summary.return_rate = summary.final_assets / INITIAL_CASH - 1.0
        peak = -1e18
        mdd = 0.0
        for s in snaps:
            peak = max(peak, float(s["total_assets"]))
            if peak > 0:
                mdd = max(mdd, 1.0 - float(s["total_assets"]) / peak)
        summary.max_drawdown = mdd
    return summary


class ComplianceReporter:
    """合规报告（移植 scripts/backtest_accounts.py ComplianceReporter，适配重放）。
    渲染结果完全确定性（同输入同输出）。"""

    def __init__(self, settlement_dates: list[str]):
        self.settlement_dates = list(settlement_dates)
        self.summaries: list[AnalystSummary] = []
        self.violations: list[Violation] = []
        self.degraded_days: list[dict] = []
        self.quotes_missing: list[dict] = []
        self.unsettled: list[dict] = []
        self.notes: list[str] = []

    def add_summary(self, summary: AnalystSummary) -> None:
        self.summaries.append(summary)

    def add_violations(self, violations: list[Violation]) -> None:
        self.violations.extend(violations)

    def add_note(self, note: str) -> None:
        self.notes.append(note)

    def add_degraded_day(self, date: str, notes: list[str]) -> None:
        self.degraded_days.append({"date": date, "notes": list(notes)})

    def render_md(self) -> str:
        start = self.settlement_dates[0] if self.settlement_dates else "—"
        end = self.settlement_dates[-1] if self.settlement_dates else "—"
        lines: list[str] = [
            f"# R14 账户全量重放合规报告（{start} → {end}）",
            "",
            f"> {DISCLAIMER}",
            "",
            "## 一、全局摘要",
            "",
            f"- 结算日数：{len(self.settlement_dates)} 个"
            "（= 已存在复盘产物 plan.json 的日期集合）",
            f"- 分析师数：{len(self.summaries)}",
            f"- 未结算计划数：{len(self.unsettled)}"
            + ("（明细见「四、未结算计划」）" if self.unsettled else ""),
            f"- 行情缺失标注：{len(self.quotes_missing)} 条"
            + ("（明细见 replay_meta.json）" if self.quotes_missing else ""),
            f"- 引擎漏洞（未被引擎拦截的违规）：{len(self.violations)} 条"
            + ("" if self.violations else "（无，引擎校验路径正常）"),
            "",
        ]
        if self.degraded_days:
            lines.append("### 数据降级记录")
            lines.append("")
            lines.append("| 日期 | 降级说明 |")
            lines.append("|---|---|")
            for d in self.degraded_days:
                lines.append(f"| {d['date']} | {'；'.join(d['notes'])[:200]} |")
            lines.append("")
        lines += [
            "## 二、每分析师汇总",
            "",
            "| 分析师 | 成交笔数 | 拒绝笔数 | 超资金 | T+1 | 涨停拒买 | 跌停拒卖 | "
            "未触及目标价 | 非整百 | 裸卖空 | 期末资产 | 收益率 | 最大回撤 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in self.summaries:
            rr = {r: s.reject_reasons.get(r, 0) for r in REJECT_REASONS}
            lines.append(
                f"| {s.name} | {s.filled} | {s.rejected} | {rr['超资金']} | {rr['T+1']} | "
                f"{rr['涨停拒买']} | {rr['跌停拒卖']} | {rr['未触及目标价']} | "
                f"{rr['非整百']} | {rr['裸卖空']} | {s.final_assets:.2f} | "
                f"{s.return_rate:.2%} | {s.max_drawdown:.2%} |")
        lines += [
            "",
            "## 三、全局违规审计（引擎漏洞逐条）",
            "",
        ]
        if not self.violations:
            lines.append("未发现未被引擎拦截的违规（引擎漏洞：无）。")
        else:
            lines.append("| 日期 | 分析师 | 违反规则 | 明细 | 订单 |")
            lines.append("|---|---|---|---|---|")
            for v in self.violations:
                lines.append(f"| {v.date} | {v.skill_id} | {v.rule} | {v.detail} | "
                             f"{v.order_desc} |")
            lines.append("")
            lines.append("> 存在引擎漏洞：重放退出码为 1，需修复引擎后重跑。")
        lines += [
            "",
            "## 四、未结算计划",
            "",
        ]
        if not self.unsettled:
            lines.append("无（全部计划均已撮合或已到结算日）。")
        else:
            lines.append("| 推荐日 | 分析师 | 代码 | 方向 | 原因 |")
            lines.append("|---|---|---|---|---|")
            for u in self.unsettled:
                lines.append(f"| {u['rec_date']} | {u['skill_id']} | {u['code']} | "
                             f"{u['side']} | {u['reason']} |")
        if self.notes:
            lines += ["", "## 五、备注", ""]
            for n in self.notes:
                lines.append(f"- {n}")
        lines += ["", "---", "", f"> {DISCLAIMER}", ""]
        return "\n".join(lines)

    def write(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "replay_report.md"
        target.write_text(self.render_md(), encoding="utf-8")
        return target


def _build_meta(review_dates: list[str], plans_count: dict[str, int],
                quotes_missing: list[dict], unsettled: list[dict],
                violations: list[Violation], exit_code: int) -> dict:
    """重放元数据（完全确定性，无时间戳）。"""
    plan_hashes: dict[str, dict] = {}
    for d in review_dates:
        plan_hashes[d] = {
            "sha256": plan_sha256(d),
            "plans": int(plans_count.get(d, 0)),
        }
    return {
        "start": review_dates[0] if review_dates else "",
        "end": review_dates[-1] if review_dates else "",
        "settlement_dates": list(review_dates),
        "plan_hash": plan_hash(review_dates),
        "plan_hashes": plan_hashes,
        "quotes_missing": list(quotes_missing),
        "unsettled_plans": list(unsettled),
        "unsettled_count": len(unsettled),
        "violations": [v.to_dict() for v in violations],
        "exit_code": exit_code,
    }


def run_replay(start: Optional[str] = None, end: Optional[str] = None,
               report_dir: Optional[Path | str] = None,
               fixture: bool = False,
               quote_source: Optional[Callable[..., dict]] = None) -> int:
    """全量重放主流程：

    1. 结算日序列 = 已存在 plan.json 的日期集合（start/end 过滤）；
    2. 从零开户（engine.open_account，10 万初始资金）；
    3. 逐结算日：撮合日 = 结算日序列中 ≥ rec_date 的最近一天（当日计划当日撮合），
       用当日行情调 engine.place_order（Q1 口径）撮合；
    4. 每日快照（engine.snapshot，持仓按收盘价估值，缺失按成本价并标注）；
    5. trades/snapshots 落盘 data/accounts/{skill_id}/（storage.save_account）；
    6. 合规断言（AssertionChecker），违规 → violations + 退出码 1；
    7. 输出 .tmp/account_replay/replay_report.md + replay_meta.json。

    quote_source 可注入（默认 quotes.ensure_quotes），供测试替换。
    返回退出码：0=无违规，1=违规（引擎漏洞），2=数据不可用（无复盘产物）。
    """
    out = Path(report_dir) if report_dir else default_out_dir()
    review_dates = list_review_dates(start, end)
    if not review_dates:
        return 2

    fetcher: Callable[..., dict] = quote_source or ensure_quotes

    # 计划装载（只消费 plan.json，绝不读 pending.json）
    plans_by_date: dict[str, list[dict]] = {}
    plan_errors: list[str] = []
    plans_count: dict[str, int] = {}
    for d in review_dates:
        plans, err = _load_plan_file(d)
        plans_by_date[d] = plans
        plans_count[d] = len(plans)
        if err:
            plan_errors.append(err)

    all_plans: list[dict] = []
    for fdate in review_dates:
        for idx, plan in enumerate(plans_by_date[fdate]):
            all_plans.append({
                "rec_date": str(plan.get("rec_date") or fdate),
                "file_date": fdate,
                "idx": idx,
                "plan": plan,
            })
    # 确定性顺序：rec_date 升序 → 文件日期升序 → 文件内顺序
    all_plans.sort(key=lambda x: (x["rec_date"], x["file_date"], x["idx"]))

    # 每计划撮合日 = rec_date 之后最近结算日（「D 之后最近交易日」；
    # 无 → 未到结算日，保持未结算并计数）
    for p in all_plans:
        p["match_day"] = next(
            (d for d in review_dates if d > p["rec_date"]), None)

    skill_ids = sorted({str(p["plan"].get("skill_id") or "unknown")
                        for p in all_plans})
    skill_names: dict[str, str] = {}
    for p in all_plans:
        sid = str(p["plan"].get("skill_id") or "unknown")
        skill_names.setdefault(sid, str(p["plan"].get("skill_name") or sid))

    accounts = {sid: acct_engine.open_account(sid) for sid in skill_ids}
    violations: list[Violation] = []
    degraded_days: list[dict] = []
    quotes_missing: list[dict] = []
    unsettled: list[dict] = []

    for day in review_dates:
        day_plans = [p for p in all_plans if p["match_day"] == day]
        codes: set[str] = set()
        for p in day_plans:
            code = str(p["plan"].get("code") or "").zfill(6)
            if code:
                codes.add(code)
        for sid in skill_ids:
            for code in accounts[sid].get("positions", {}):
                codes.add(str(code))

        payload = fetcher(day, sorted(codes), fixture=fixture)
        quotes = payload.get("quotes") or {}
        degraded = list(payload.get("degraded") or [])

        # 每结算日：先推进 T+1（解冻昨日买入）→ 对存量非当日买入持仓跑条件单机械卖出
        # （requirements-trading-v1 §二.5）→ 再匹配新计划（当日买入不受条件单管束）。
        # 条件单卖出现金/持仓变化在先，随后的快照与合规断言可正确反映。
        for sid in skill_ids:
            acct_engine._settle_day(accounts[sid], day)
            acct_engine.check_conditional_orders(accounts[sid], {"date": day}, quotes)

        for p in day_plans:
            plan = p["plan"]
            code = str(plan.get("code") or "").zfill(6)
            side = str(plan.get("side") or "")
            quote = quotes.get(code)
            if not code or quote is None:
                note = f"{code or '（空代码）'} {day} 行情缺失，计划无法撮合"
                quotes_missing.append({
                    "date": day, "code": code or "",
                    "skill_id": str(plan.get("skill_id") or "unknown"),
                    "note": note,
                })
                degraded.append(note)
                unsettled.append({
                    "rec_date": p["rec_date"], "skill_id": str(
                        plan.get("skill_id") or "unknown"),
                    "code": code or "", "side": side, "reason": "行情缺失",
                })
                continue

            name = str(plan.get("name") or quote.get("name") or code)
            target = float(plan.get("target_price") or 0)
            cheng = float(plan.get("position_cheng") or 0)
            day_quote = dict(quote)
            day_quote["date"] = day
            try:
                qty = int(plan.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            acct = accounts[str(plan.get("skill_id") or "unknown")]
            # 买入计划条件单三字段缺省 → 引擎默认（§三）；trailing_drawdown 透传（§六）
            conditional = None
            if side == "buy" and target > 0:
                conditional = acct_engine.apply_conditional_defaults(
                    {
                        "stop_loss": plan.get("stop_loss"),
                        "take_profit": plan.get("take_profit"),
                        "max_hold_days": plan.get("max_hold_days"),
                        "trailing_drawdown": plan.get("trailing_drawdown"),
                    },
                    target,
                )
            if qty > 0:
                # 计划显式给出股数（qty）时优先于仓位成数折算（A4 契约）
                result = acct_engine.place_order(
                    acct, code, name, side, target, cheng, day_quote,
                    qty=qty, conditional=conditional,
                    buy_reason=plan.get("reason") if side == "buy" else None,
                    sell_reason="主动卖出" if side == "sell" else None)
            else:
                result = acct_engine.place_order(
                    acct, code, name, side, target, cheng, day_quote,
                    conditional=conditional,
                    buy_reason=plan.get("reason") if side == "buy" else None,
                    sell_reason="主动卖出" if side == "sell" else None)
            if result.ok:
                # engine 通常已把成交记录写入 acct["trades"]；若调用方引擎未写
                # （如测试注入的假引擎）则兜底追加；并补 balance_after 兼容别名
                # （R12-2 记录口径，engine 原字段为 cash_after）。
                if result.trade is not None:
                    result.trade.setdefault(
                        "balance_after",
                        result.trade.get("cash_after", acct.get("cash")))
                    if result.trade not in acct.setdefault("trades", []):
                        acct["trades"].append(result.trade)
                continue
            acct.setdefault("trades", []).append({
                "date": day, "code": code, "name": name, "side": side,
                "target_price": target, "qty": qty, "price": target,
                "amount": 0.0, "fee": 0.0, "stamp_tax": 0.0, "transfer_fee": 0.0,
                "status": "拒绝", "reject_reason": result.reason or "拒绝",
                "cash_after": acct.get("cash"),
                "balance_after": acct.get("cash"),
            })

        # 每日快照（缺失行情按成本价估值，engine.snapshot 标注 estimated_prices）
        for sid in skill_ids:
            acct = accounts[sid]
            close_prices: dict[str, float] = {}
            for code in acct.get("positions", {}):
                q = quotes.get(str(code))
                if q is not None and q.get("close") is not None:
                    close_prices[str(code)] = float(q["close"])
                else:
                    degraded.append(f"{code} 无 {day} 行情，快照按成本价估值")
            acct.setdefault("snapshots", []).append(
                acct_engine.snapshot(acct, day, close_prices))

        # 合规断言（当日成交记录）
        for sid in skill_ids:
            acct = accounts[sid]
            day_fills = [t for t in acct.get("trades") or []
                         if t.get("date") == day and t.get("status") == "成交"]
            violations.extend(AssertionChecker.check_day(acct, day, day_fills, quotes))

        if degraded:
            degraded_days.append({"date": day, "notes": degraded})

    # 未结算计划：无撮合日（未到结算日）或撮合日行情缺失
    for p in all_plans:
        if p["match_day"] is None:
            plan = p["plan"]
            unsettled.append({
                "rec_date": p["rec_date"],
                "skill_id": str(plan.get("skill_id") or "unknown"),
                "code": str(plan.get("code") or ""),
                "side": str(plan.get("side") or ""),
                "reason": "未到结算日",
            })
    # 去重（同一计划只计一次；顺序保持确定性）
    seen: set[tuple] = set()
    unsettled_dedup: list[dict] = []
    for u in unsettled:
        key = (u["rec_date"], u["skill_id"], u["code"], u["side"], u["reason"])
        if key not in seen:
            seen.add(key)
            unsettled_dedup.append(u)
    unsettled_dedup.sort(key=lambda x: (x["rec_date"], x["skill_id"],
                                        x["code"], x["side"], x["reason"]))

    # 落盘账户（R12 口径：account/trades/snapshots 分文件）
    for sid in skill_ids:
        acct_storage.save_account(sid, accounts[sid])

    summaries = [compute_summary(sid, accounts[sid], skill_names.get(sid, sid))
                 for sid in skill_ids]
    reporter = ComplianceReporter(review_dates)
    for s in summaries:
        reporter.add_summary(s)
    reporter.add_violations(violations)
    for d in degraded_days:
        reporter.add_degraded_day(d["date"], d["notes"])
    reporter.unsettled = list(unsettled_dedup)
    reporter.quotes_missing = list(quotes_missing)
    for err in plan_errors:
        reporter.add_note(err)
    if plan_errors:
        reporter.add_note("plan.json 读取异常已标注，相关日期按无计划处理；"
                          "建议校验后重跑复盘（validate）。")
    reporter.add_note("结算日序列 = 已存在复盘产物 plan.json 的日期集合"
                      "（节假日表不内置）；rec_date=D 的计划用 D 之后最近结算日行情撮合。")
    reporter.add_note("重放确定性：同 plan.json + 同行情（缓存/fixture）"
                      " → 同输出，不依赖时间/随机。")

    exit_code = 1 if violations else 0
    report_path = reporter.write(out)
    meta = _build_meta(review_dates, plans_count, quotes_missing,
                       unsettled_dedup, violations, exit_code)
    (out / "replay_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[replay] 结算日 {len(review_dates)} 个，分析师 {len(skill_ids)} 位，"
          f"成交 {sum(s.filled for s in summaries)} / 拒绝 "
          f"{sum(s.rejected for s in summaries)}，未结算 {len(unsettled_dedup)}，"
          f"违规 {len(violations)} 条")
    print(f"[replay] 报告已写入：{report_path}")
    print(f"[replay] 退出码：{exit_code}"
          "（0=无违规，1=发现引擎漏洞/断言失败）")
    return exit_code


def settle_plans(rec_date: str, settle_date: str,
                 quote_source: Optional[Callable[..., dict]] = None,
                 skill_ids: Optional[list[str]] = None) -> dict:
    """R14 增量撮合（2026-08-12，逐日交错模拟流程用）：

    撮合 ``rec_date`` 的计划，用 ``settle_date`` 的完整行情（收盘后取数），
    **加载已有账户**（无则开户）——分析师在 ``settle_date`` 复盘时即可读到
    get_positions 返回的撮合后持仓（T+1 冻结/现金/成本价）。

    与 run_replay（全量从零重放）不同：本函数只推进一个结算日并落盘，
    由流程脚本逐日调用（撮合前一日计划 → 跑当日复盘 → 撮合当日计划 → ……）。

    2026-08-13（缺陷修复）：即使 ``rec_date`` 无计划，也推进账户状态——
    加载存量账户（复用 load_account/open_account）→ 推进 current_date 到
    settle_date 并解冻 T+1 冻结（复用 engine._settle_day 语义）→ 按
    settle_date 行情为持仓落当日快照（缺失行情按成本价估值并 degraded
    标注，绝不静默）→ 落盘。修复无计划日之后 T+1 冻结不解除、
    收益率曲线缺日、current_date 停滞的问题。

    返回 {filled, rejected, quotes_missing, unsettled, degraded, note?, error?}。
    失败静默：行情缺失的计划进 quotes_missing/unsettled，绝不抛异常。
    """
    from . import engine as acct_engine
    from . import storage as acct_storage
    from .quotes import ensure_quotes

    plans, plan_err = _load_plan_file(rec_date)
    fetcher: Callable[..., dict] = quote_source or ensure_quotes

    codes: set[str] = set()
    for p in plans:
        code = str(p.get("code") or "").zfill(6)
        if code:
            codes.add(code)
    # 分析师集合：显式传入 → 计划涉及 ∪ 存量账户目录。无论 rec_date 有无计划，
    # 都要推进所有存量账户（T+1 解冻 + 当日快照），否则仅部分分析师有新计划时，
    # 其余持仓账户的 current_date 停滞、T+1 冻结不解除，跨日持仓状态失真。
    plan_sids = {str(p.get("skill_id") or "unknown") for p in plans}
    if skill_ids is not None:
        sids = sorted(set(skill_ids))
    else:
        sids = set(plan_sids)
        try:
            sids |= {d.name for d in acct_storage.accounts_root().iterdir()
                     if d.is_dir() and (d / "account.json").exists()}
        except OSError:
            pass
        sids = sorted(sids)
    accounts: dict[str, dict] = {}
    for sid in sids:
        acct = acct_storage.load_account(sid)
        if acct is None:
            acct = acct_engine.open_account(sid)
        acct["trades"] = acct_storage.load_trades(sid)
        # 2026-08-12（用户反馈：收益率曲线只剩「近 1 日」）：增量撮合必须加载历史
        # 快照，否则 save_account 每次把 snapshots.json 覆盖成当天一条，快照链断裂
        acct["snapshots"] = acct_storage.load_snapshots(sid)
        accounts[sid] = acct
        for code in acct.get("positions", {}):
            codes.add(str(code))

    # 推进结算日（T+1 到期解冻）——无论有无计划都要推进（2026-08-13 修复）
    for acct in accounts.values():
        acct_engine._settle_day(acct, settle_date)

    payload = fetcher(settle_date, sorted(codes), fixture=False)
    quotes = payload.get("quotes") or {}
    degraded = list(payload.get("degraded") or [])

    # 条件单机械卖出（requirements-trading-v1 §二.5）：对存量非当日买入持仓，
    # 按止损/止盈/超时顺序执行，生成卖出 trade 并入当日记录（在匹配新计划之前）。
    cond_trades_note: list[str] = []
    for sid, acct in accounts.items():
        new_trades = acct_engine.check_conditional_orders(
            acct, {"date": settle_date}, quotes)
        if new_trades:
            cond_trades_note.append(
                f"{sid} 条件单卖出 {len(new_trades)} 笔："
                + "，".join(f"{t['code']}@{t['price']:.2f}" for t in new_trades))

    filled = rejected = 0
    quotes_missing: list[dict] = []
    unsettled: list[dict] = []

    for p in plans:
        code = str(p.get("code") or "").zfill(6)
        side = str(p.get("side") or "")
        quote = quotes.get(code)
        if not code or quote is None:
            note = f"{code or '（空代码）'} {settle_date} 行情缺失，计划无法撮合"
            quotes_missing.append({"date": settle_date, "code": code or "",
                                   "skill_id": str(p.get("skill_id") or "unknown"), "note": note})
            unsettled.append({"rec_date": rec_date, "skill_id": str(p.get("skill_id") or "unknown"),
                              "code": code or "", "side": side, "reason": "行情缺失"})
            continue
        name = str(p.get("name") or quote.get("name") or code)
        target = float(p.get("target_price") or 0)
        cheng = float(p.get("position_cheng") or 0)
        day_quote = dict(quote)
        day_quote["date"] = settle_date
        try:
            qty = int(p.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        acct = accounts[str(p.get("skill_id") or "unknown")]
        # 买入计划条件单三字段：缺省按引擎默认（买入价×0.95 / ×1.10 / 13）补齐（§三）；
        # trailing_drawdown 透传（§六）
        conditional = None
        if side == "buy" and target > 0:
            conditional = acct_engine.apply_conditional_defaults(
                {
                    "stop_loss": p.get("stop_loss"),
                    "take_profit": p.get("take_profit"),
                    "max_hold_days": p.get("max_hold_days"),
                    "trailing_drawdown": p.get("trailing_drawdown"),
                },
                target,
            )
        result = acct_engine.place_order(
            acct, code, name, side, target, cheng, day_quote,
            qty=qty if qty > 0 else None, conditional=conditional,
            buy_reason=p.get("reason") if side == "buy" else None,
            sell_reason="主动卖出" if side == "sell" else None)
        if result.ok:
            filled += 1
            if result.trade is not None:
                result.trade.setdefault("balance_after",
                                        result.trade.get("cash_after", acct.get("cash")))
                if result.trade not in acct.setdefault("trades", []):
                    acct["trades"].append(result.trade)
        else:
            rejected += 1
            acct.setdefault("trades", []).append({
                "date": settle_date, "code": code, "name": name, "side": side,
                "target_price": target, "qty": qty or None, "price": target,
                "amount": 0.0, "fee": 0.0, "stamp_tax": 0.0, "transfer_fee": 0.0,
                "status": "拒绝", "reject_reason": result.reason or "拒绝",
                "cash_after": acct.get("cash"), "balance_after": acct.get("cash"),
            })

    # 每日快照（缺失行情按成本价估值，engine.snapshot 标注 estimated_prices）
    for sid, acct in accounts.items():
        close_prices: dict[str, float] = {}
        for code in acct.get("positions", {}):
            q = quotes.get(str(code))
            if q is not None and q.get("close") is not None:
                close_prices[str(code)] = float(q["close"])
            else:
                degraded.append(f"{code} 无 {settle_date} 行情，快照按成本价估值")
        acct.setdefault("snapshots", []).append(
            acct_engine.snapshot(acct, settle_date, close_prices))

    for sid, acct in accounts.items():
        acct_storage.save_account(sid, acct)

    out = {"filled": filled, "rejected": rejected,
           "quotes_missing": quotes_missing, "unsettled": unsettled,
           "degraded": degraded}
    if cond_trades_note:
        out["conditional_trades"] = cond_trades_note
    if not plans and not plan_err:
        out["note"] = f"{rec_date} 无计划，已推进账户状态"
    if plan_err:
        out["error"] = plan_err
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="R14 账户全量重放（确定性：plan.json + 行情缓存 → 账户/报告）")
    parser.add_argument("--start", default=None,
                        help="起始结算日 YYYY-MM-DD（含，默认不限）")
    parser.add_argument("--end", default=None,
                        help="结束结算日 YYYY-MM-DD（含，默认不限）")
    parser.add_argument("--fixture", action="store_true",
                        help="确定性样例行情（不读缓存/不取网络），供测试/回归")
    parser.add_argument("--out", default=None,
                        help="报告输出目录（默认 .tmp/account_replay）")
    args = parser.parse_args(argv)
    try:
        return run_replay(start=args.start, end=args.end,
                          fixture=args.fixture, report_dir=args.out)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
