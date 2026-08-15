# -*- coding: utf-8 -*-
"""R12-A4 回测脚本单测迁移（R14）：scripts/backtest_accounts.py 将被删除，
等价断言迁移到 stock_review_crew.accounts.replay（吸收 AssertionChecker/ComplianceReporter；
结算日序列自适应为「已存在 plan.json 的日期集合」；规则引擎不再作为生产路径）。

覆盖（requirements-r12.md R12-3 口径 + requirements-r14.md 第四节/第五节/第八节）：
- 结算日序列：= 已存在 plan.json 的日期集合（排序、去重，周末/节假日不硬编码——规避节假日表问题）；
- 计划执行：replay 只消费 plan.json，不生成规则计划；空 plans → 无成交、不开户；
- 引擎适配：plan.qty 非空时按股数执行（优先于 position_cheng 折算）；
- AssertionChecker 违规断言（等价迁移）：超资金 / 裸卖空 / 涨跌停越界 / 非整百+负费用；
- ComplianceReporter 合规报告结构（吸收自 backtest_accounts，R14 新格式）；
- run_replay 退出码：0=无违规、1=引擎漏洞/断言失败、2=数据不可用（无 plan.json）；
- 账户落盘结构：account.json / trades.json / snapshots.json（R12 数据口径不变）；
- 按计划中的 skill_id 动态开户（未来新增分析师零改动）。
CLI（dry-run / --llm-days）随 scripts/backtest_accounts.py 删除，由 A3 work/account_replay.py 承接。
模块未实现时整文件 skip；集成阶段启用必须全绿。
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

try:
    from stock_review_crew.accounts import replay
except ImportError:  # pragma: no cover - A2 未实现
    replay = None

from stock_review_crew.accounts.engine import open_account  # noqa: E402

pytestmark = pytest.mark.skipif(
    replay is None, reason="A2 accounts/replay.py 未实现（R14 开发中，集成阶段启用）"
)


@pytest.fixture()
def data_dir():
    """仓库内 .tmp 隔离目录（沙箱 pytest tmp_path 有 ACL 问题，见 PLAYBOOK），自建自清。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / "a4_r12_replay"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    for _ in range(3):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            break


def _plan(rec_date="2026-07-15", skill_id="alang", code="600519", side="buy",
          target_price=10.00, position=3, qty=None, source="tool", name="贵州茅台"):
    return {
        "rec_date": rec_date, "skill_id": skill_id, "skill_name": "阿郎",
        "code": code, "name": name, "side": side, "target_price": target_price,
        "position_cheng": position, "qty": qty, "source": source,
    }


def _write_plan(data_dir, d, plans):
    p = data_dir / "reviews" / d / "plan.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"date": d, "mode": "close", "generated_at": "2026-08-11T00:00:00", "plans": plans},
        ensure_ascii=False), "utf-8")


def _quote(code="600519", close=8.5, pre_close=8.3, high=8.6, low=8.2, name="包钢股份"):
    return {"name": name, "pre_close": pre_close, "high": high, "low": low, "close": close}


def _write_quotes(data_dir, d, quotes_map):
    p = data_dir / "quotes_cache" / f"{d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"date": d, "fetched_at": "2026-08-11T00:00:00", "source": "test",
         "quotes": quotes_map, "degraded": []}, ensure_ascii=False), "utf-8")


def _mk_record(status="成交", side="buy", code="600519", qty=3500, price=8.5,
               amount=None, fee=None, reason="", date="2026-07-15") -> dict:
    amount = amount if amount is not None else round(qty * price, 2)
    fee = fee if fee is not None else round(max(5.0, amount * 0.00025), 2)
    return {"date": date, "code": code, "name": code, "side": side, "qty": qty,
            "price": price, "amount": amount, "fee": fee, "stamp_tax": 0.0,
            "transfer_fee": 0.0, "status": status, "reject_reason": reason,
            "cash_after": None}


def _apply(account: dict, rec: dict) -> None:
    """将一条「成交」记录应用到引擎账户 dict（等价旧 AccountBook.apply，供 check_day 单测构造状态）。"""
    if rec.get("status") != "成交":
        return
    code, side, qty, price = rec["code"], rec["side"], rec["qty"], rec["price"]
    amount = rec.get("amount") if rec.get("amount") is not None else round(qty * price, 2)
    fee = rec.get("fee") or 0.0
    stamp = rec.get("stamp_tax") or 0.0
    transfer = rec.get("transfer_fee") or 0.0
    account["cash"] += (-amount - fee - transfer - stamp) if side == "buy" \
        else (amount - fee - transfer - stamp)
    pos = account["positions"].setdefault(
        code, {"qty": 0, "frozen_qty": 0, "avg_cost": price, "name": rec.get("name") or code})
    if side == "buy":
        pos["qty"] += qty
        pos["frozen_qty"] += qty
        pos["avg_cost"] = price
    else:
        pos["qty"] -= qty
        pos["frozen_qty"] = max(0, pos["frozen_qty"] - qty)


# ---------- 结算日序列（自适应，替代 build_trading_days 固定区间） ----------

def test_trading_days_from_existing_plan_dates(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    for d in ("2026-07-16", "2026-07-15", "2026-07-17"):
        _write_plan(data_dir, d, [_plan(rec_date=d)])
    assert [str(x) for x in replay.list_review_dates()] == \
        ["2026-07-15", "2026-07-16", "2026-07-17"]


def test_trading_days_skips_dir_without_plan(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15", [_plan(rec_date="2026-07-15")])
    (data_dir / "reviews" / "2026-07-16").mkdir(parents=True, exist_ok=True)  # 有目录无 plan.json
    assert [str(x) for x in replay.list_review_dates()] == ["2026-07-15"]


def test_trading_days_keeps_explicit_weekend(monkeypatch, data_dir):
    """--dates 显式指定周末复盘时保留该日期（不做硬编码周末过滤，规避节假日表问题）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-18", [_plan(rec_date="2026-07-18")])  # 周六
    assert [str(x) for x in replay.list_review_dates()] == ["2026-07-18"]


def test_trading_days_empty_when_no_plans(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    assert replay.list_review_dates() == []


# ---------- 计划执行（替代 RuleEngine：只消费 plan.json，不生成规则计划） ----------

def test_replay_executes_exactly_plan_json(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=8.5, code="600010", name="包钢股份")])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16", {"600010": _quote(code="600010")})
    rc = replay.run_replay(report_dir=str(data_dir / "out"))
    assert rc == 0
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    assert len(trades) == 1
    assert trades[0]["code"] == "600010" and trades[0]["status"] == "成交"


def test_replay_no_rule_generation_on_empty_plans(monkeypatch, data_dir):
    """plan.json 为空 → 即使行情/账户状态允许也不产生任何交易，不开户。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15", [])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16", {"600519": _quote(code="600519", close=10.5,
                                                            pre_close=10.0, high=10.8, low=10.0)})
    rc = replay.run_replay(report_dir=str(data_dir / "out"))
    assert rc == 0
    assert not (data_dir / "accounts" / "alang").exists(), "空计划不应开户（无规则生成）"


def test_replay_plan_qty_priority_over_position(monkeypatch, data_dir):
    """plan.qty 非空时按股数执行：qty=2900（而非 10 成折算的 9900）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15", [_plan(side="buy", target_price=10.00,
                                               position=10, qty=2900)])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16", {
        "600519": {"name": "贵州茅台", "pre_close": 10.0, "high": 10.8, "low": 10.0, "close": 10.5}})
    rc = replay.run_replay(report_dir=str(data_dir / "out"))
    assert rc == 0
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    assert trades[0]["qty"] == 2900, "qty 必须优先于 position_cheng 折算"


# ---------- AssertionChecker 违规断言（等价迁移） ----------

def test_assertion_catches_overdraft():
    account = open_account("alang")
    rec = _mk_record(amount=999_999_999.0, fee=0.0)  # 超支成交
    _apply(account, rec)
    violations = replay.AssertionChecker.check_day(account, "2026-07-15", [rec], {})
    assert any("超资金" in v.rule for v in violations)
    assert account["cash"] < 0


def test_assertion_catches_bare_short_sell():
    account = open_account("alang")
    rec = _mk_record(side="sell", code="600999", qty=500, price=5.0)  # 无持仓裸卖空
    _apply(account, rec)
    violations = replay.AssertionChecker.check_day(account, "2026-07-15", [rec], {})
    assert any("裸卖空" in v.rule for v in violations)
    assert account["positions"]["600999"]["qty"] < 0, "裸卖空应保留负持仓"


def test_assertion_catches_t1_sell_violation():
    """R12 口径：当日买入 300 冻结、总持仓 1000、可用 700，卖出 1100（持仓 -100）
    与卖出 800（持仓 200）均应被断言层拦截（T+1 卖出超过可用）。"""
    account = open_account("alang")
    account["positions"]["600000"] = {"qty": 1000, "frozen_qty": 300,
                                      "avg_cost": 5.0, "name": "浦发银行"}
    rec = _mk_record(side="sell", code="600000", qty=800, price=5.0)  # 可用 700，卖 800
    _apply(account, rec)
    violations = replay.AssertionChecker.check_day(account, "2026-07-15", [rec], {})
    assert any("T+1" in v.rule or "裸卖空" in v.rule for v in violations), \
        "卖出超过可用持仓必须被断言层抓到"


def test_assertion_catches_price_out_of_limit():
    account = open_account("alang")
    quotes = {"600519": _quote(code="600519", pre_close=8.3, close=8.5, high=8.6, low=8.2)}
    rec = _mk_record(price=9.50)  # 高于涨停 9.13
    _apply(account, rec)
    violations = replay.AssertionChecker.check_day(account, "2026-07-15", [rec], quotes)
    assert any("涨停" in v.rule for v in violations)
    rec_low = _mk_record(price=7.00)  # 低于跌停 7.47
    _apply(account, rec_low)
    violations2 = replay.AssertionChecker.check_day(account, "2026-07-15", [rec_low], quotes)
    assert any("跌停" in v.rule for v in violations2)


def test_assertion_catches_non_lot100_and_negative_fee():
    account = open_account("alang")
    rec = _mk_record(qty=150, fee=-5.0)  # 非整百 + 负费用
    _apply(account, rec)
    violations = replay.AssertionChecker.check_day(account, "2026-07-15", [rec], {})
    rules = {v.rule for v in violations}
    assert any("非整百" in r for r in rules)
    assert any("费用" in r for r in rules)


def test_assertion_passes_clean_day():
    account = open_account("alang")
    rec = _mk_record()  # 3500×8.5，费用正常
    _apply(account, rec)
    quotes = {"600519": _quote(code="600519", pre_close=8.3, close=8.5, high=8.6, low=8.2)}
    assert replay.AssertionChecker.check_day(account, "2026-07-15", [rec], quotes) == []


# ---------- ComplianceReporter 合规报告结构（吸收自 backtest_accounts，R14 新格式） ----------

def test_compliance_report_structure():
    reporter = replay.ComplianceReporter(["2026-07-15", "2026-07-16"])
    reporter.add_summary(replay.AnalystSummary(
        skill_id="alang", name="阿郎", filled=3, rejected=2,
        reject_reasons={"超资金": 1, "T+1": 1, "涨停拒买": 1, "跌停拒卖": 0,
                        "未触及目标价": 0, "非整百": 0, "裸卖空": 0},
        final_assets=100_500.0, return_rate=0.005, max_drawdown=0.01))
    reporter.add_summary(replay.AnalystSummary(
        skill_id="tiechui", name="铁锤", filled=0, rejected=1,
        reject_reasons={"裸卖空": 1}, final_assets=99_000.0,
        return_rate=-0.01, max_drawdown=0.02))
    reporter.add_violations([replay.Violation(
        date="2026-07-15", skill_id="alang", rule="超资金",
        detail="每日结束后现金为 -1.00（超支）", order_desc="cash=-1.0")])
    reporter.add_note("未内置节假日表：周一至周五均按交易日处理。")
    md = reporter.render_md()
    assert "仅供参考，不构成投资建议" in md
    assert "阿郎" in md and "铁锤" in md
    assert "超资金" in md and "裸卖空" in md
    assert "未触及目标价" in md and "非整百" in md
    assert "期末资产" in md and "收益率" in md and "最大回撤" in md
    assert "引擎漏洞" in md
    assert "| 阿郎 | 3 | 2 | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 100500.00 | 0.50% | 1.00% |" in md, \
        "成交/拒绝/超资金/T+1/涨停拒买 列与期末资产/收益率/回撤应正确"
    reporter2 = replay.ComplianceReporter(["2026-07-15", "2026-07-16"])
    reporter2.add_summary(replay.AnalystSummary(skill_id="alang", name="阿郎"))
    md2 = reporter2.render_md()
    assert "未发现未被引擎拦截的违规" in md2


def test_reject_reason_distribution():
    account = open_account("alang")
    account["trades"] = []
    reasons = ["超资金：用了过多的钱", "T+1：当日买入不可卖出", "涨停拒买：目标价达到涨停价",
               "跌停拒卖：目标价低于跌停价", "未触及目标价：价格区间不覆盖目标价",
               "非整百：数量必须为100整数倍", "裸卖空：卖出超过可用持仓"]
    for r in reasons:
        account["trades"].append(_mk_record(status="拒绝", reason=r))
    account["trades"].append(_mk_record())
    account["trades"].append(_mk_record(side="sell", code="600000", qty=100, price=8.5))
    summary = replay.compute_summary("alang", account)
    assert summary.filled == 2
    assert summary.rejected == 7
    for reason in replay.REJECT_REASONS:
        assert summary.reject_reasons.get(reason) == 1, f"{reason} 应计 1 次"


# ---------- 账户落盘结构（R12 数据口径不变） ----------

def test_replay_account_save_structure(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=8.5, code="600010", name="包钢股份")])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16", {"600010": _quote(code="600010")})
    rc = replay.run_replay(report_dir=str(data_dir / "out"))
    assert rc == 0
    acc_dir = data_dir / "accounts" / "alang"
    for name in ("account.json", "trades.json", "snapshots.json"):
        assert (acc_dir / name).exists()
    acc = json.loads((acc_dir / "account.json").read_text("utf-8"))
    assert acc["skill_id"] == "alang"
    amount = 3500 * 8.5
    fee = round(max(5.0, amount * 0.00025), 2)
    transfer = round(amount * 0.00001, 2)
    assert acc["cash"] == pytest.approx(100_000.0 - amount - fee - transfer)
    assert "600010" in acc["positions"]
    snaps = json.loads((acc_dir / "snapshots.json").read_text("utf-8"))
    assert snaps[0]["total_assets"] > 0


# ---------- run_replay 退出码 ----------

def _fake_place_order(behavior):
    """模拟引擎漏洞：买入委托真实引擎；指定行为绕过合规返回『成交』，供退出码 1 断言。"""
    import stock_review_crew.accounts.engine as engine_mod
    orig = engine_mod.place_order

    def fake(account, code, name, side, target_price, position, day, **kwargs):
        if behavior == "bare_short" and side == "sell":
            qty = 999_999
            price = target_price
            amount = round(qty * price, 2)
            account["cash"] += amount
            pos = account["positions"].setdefault(
                code, {"qty": 0, "frozen_qty": 0, "avg_cost": price, "name": name})
            pos["qty"] -= qty
            trade = {"date": day["date"], "code": code, "name": name, "side": "sell",
                     "target_price": target_price, "qty": qty, "price": price,
                     "amount": amount, "fee": 5.0, "stamp_tax": round(amount * 0.0005, 2),
                     "transfer_fee": round(amount * 0.00001, 2),
                     "status": "成交", "reject_reason": "", "cash_after": account["cash"]}
            return SimpleNamespace(ok=True, reason="", trade=trade)
        if behavior == "overdraft" and side == "buy":
            qty, price = 3500, target_price
            account["cash"] -= 999_999_999.0
            pos = account["positions"].setdefault(
                code, {"qty": 0, "frozen_qty": 0, "avg_cost": price, "name": name})
            pos["qty"] += qty
            pos["frozen_qty"] += qty
            trade = {"date": day["date"], "code": code, "name": name, "side": "buy",
                     "target_price": target_price, "qty": qty, "price": price,
                     "amount": 999_999_999.0, "fee": 0.0, "stamp_tax": 0.0,
                     "transfer_fee": 0.0, "status": "成交", "reject_reason": "",
                     "cash_after": account["cash"]}
            return SimpleNamespace(ok=True, reason="", trade=trade)
        return orig(account, code, name, side, target_price, position, day, **kwargs)

    return fake


def test_run_replay_exit_0_clean(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=8.5, code="600010", name="包钢股份")])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16", {"600010": _quote(code="600010")})
    rc = replay.run_replay(report_dir=str(data_dir / "out"))
    assert rc == 0, "无违规引擎漏洞应退出 0"
    md = (data_dir / "out" / "replay_report.md").read_text("utf-8")
    assert "未发现未被引擎拦截的违规" in md
    assert "仅供参考，不构成投资建议" in md
    assert (data_dir / "accounts" / "alang" / "snapshots.json").exists()


def test_run_replay_exit_1_on_overdraft_leak(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    import stock_review_crew.accounts.engine as engine_mod
    monkeypatch.setattr(engine_mod, "place_order", _fake_place_order("overdraft"))
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=10.00, position=3)])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16", {
        "600519": {"name": "贵州茅台", "pre_close": 10.0, "high": 10.8, "low": 10.0, "close": 10.5}})
    rc = replay.run_replay(report_dir=str(data_dir / "out"))
    assert rc == 1, "超支（引擎漏洞）应退出 1"
    md = (data_dir / "out" / "replay_report.md").read_text("utf-8")
    assert "引擎漏洞" in md and "超资金" in md


def test_run_replay_exit_1_on_bare_short_leak(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    import stock_review_crew.accounts.engine as engine_mod
    monkeypatch.setattr(engine_mod, "place_order", _fake_place_order("bare_short"))
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=10.00, position=3)])
    _write_plan(data_dir, "2026-07-16",
                [_plan(rec_date="2026-07-16", side="sell", target_price=10.50, position=10)])
    _write_plan(data_dir, "2026-07-17", [])
    _write_quotes(data_dir, "2026-07-16", {
        "600519": {"name": "贵州茅台", "pre_close": 10.0, "high": 10.8, "low": 10.0, "close": 10.5}})
    _write_quotes(data_dir, "2026-07-17", {
        "600519": {"name": "贵州茅台", "pre_close": 10.5, "high": 10.8, "low": 10.2, "close": 10.5}})
    rc = replay.run_replay(report_dir=str(data_dir / "out"))
    assert rc == 1, "裸卖空（引擎漏洞）应退出 1"
    md = (data_dir / "out" / "replay_report.md").read_text("utf-8")
    assert "裸卖空" in md


def test_run_replay_exit_2_when_no_plan(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    (data_dir / "reviews").mkdir(parents=True, exist_ok=True)
    assert replay.run_replay(report_dir=str(data_dir / "out")) == 2, "数据不可用应退出 2"


def test_run_replay_tracks_rejections(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    import stock_review_crew.accounts.engine as engine_mod

    def fake(account, code, name, side, target_price, position, day, **kwargs):
        return SimpleNamespace(ok=False, reason="涨停拒买：目标价达到涨停价", trade=None)

    monkeypatch.setattr(engine_mod, "place_order", fake)
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=10.00, position=3)])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16", {
        "600519": {"name": "贵州茅台", "pre_close": 10.0, "high": 10.8, "low": 10.0, "close": 10.5}})
    rc = replay.run_replay(report_dir=str(data_dir / "out"))
    assert rc == 0, "引擎拒绝不算引擎漏洞"
    md = (data_dir / "out" / "replay_report.md").read_text("utf-8")
    assert "涨停拒买" in md and "拒绝笔数" in md


def test_replay_opens_accounts_per_skill_in_plans(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15", [
        _plan(skill_id="alang", code="600519", target_price=10.00),
        _plan(skill_id="tiechui", code="600010", target_price=8.5, name="包钢股份"),
    ])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16", {
        "600519": {"name": "贵州茅台", "pre_close": 10.0, "high": 10.8, "low": 10.0, "close": 10.5},
        "600010": {"name": "包钢股份", "pre_close": 8.3, "high": 8.6, "low": 8.2, "close": 8.5},
    })
    rc = replay.run_replay(report_dir=str(data_dir / "out"))
    assert rc == 0
    for sid in ("alang", "tiechui"):
        assert (data_dir / "accounts" / sid / "account.json").exists(), f"{sid} 应开户"
    assert not (data_dir / "accounts" / "bingchuan").exists(), "未出现在计划中的分析师不开户"
