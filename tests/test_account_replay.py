# -*- coding: utf-8 -*-
"""R14-A4 账户重放验收测试（测试先行，硬门）：
- 确定性：同 plan + 同行情两次全量重放 → 账户产物/replay_meta/replay_report 逐字节一致；
- 违规注入被抓：超资金 / 涨跌停越界 / 非整百 / 裸卖空 / T+1 卖出超可用（引擎漏洞 → 退出码 1）；
- R12 手算样例复现：10 万 × 3 成 target=10.00 → 2900 股 → 次日卖 2900×10.50 → 现金 101,419.318；
- 补单日闭环：D 日计划变更（reset→finalize 整文件覆盖）→ 重放后 D+1 起账户随新计划正确；
- 重放只读 plan.json（不读 pending.json）；离线全命中行情缓存；行情缺失如实标注。

契约（唯一真源：docs/requirements-r14.md 第四节.3/第四节.4/第五节/第八节）：
- run_replay(start=None, end=None, report_dir=None, fixture=False, quote_source=None) -> int；
  结算日序列 = {STOCK_REVIEW_DATA_DIR}/reviews/ 下存在 plan.json 的日期集合（自适应）；
  rec_date=D 的计划在 D 之后最近结算日按 D 日行情撮合（「D 之后最近交易日」），
  未到结算日的保持待结算（meta.unsettled_count/unsettled_plans 计数）；
  行情消费：{STOCK_REVIEW_DATA_DIR}/quotes_cache/{date}.json 缓存优先（离线全命中不联网）；
  产出 {STOCK_REVIEW_DATA_DIR}/accounts/{skill_id}/{account,trades,snapshots}.json +
  report_dir/replay_report.md + replay_meta.json（plan_hashes/quotes_missing/unsettled_*/violations）；
  退出码：0=无违规，1=发现引擎漏洞/断言失败，2=数据不可用（无 plan.json）；
- 撮合口径（R12 手算样例）：买 qty=floor(现金×成数/(target×1.001)/100)×100；
  费用 佣金0.025%(最低5)/印花税卖出0.05%/过户费0.001%双向；
- 引擎调用约定：replay 必须经 `stock_review_crew.accounts.engine` 模块引用调用 place_order
  （违规注入测试 monkeypatch engine.place_order 模拟引擎漏洞）。
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

pytestmark = pytest.mark.skipif(
    replay is None, reason="A2 accounts/replay.py 未实现（R14 开发中，集成阶段启用）"
)


@pytest.fixture()
def data_dir():
    """仓库内 .tmp 隔离目录（沙箱 pytest tmp_path 有 ACL 问题，见 PLAYBOOK），自建自清。"""
    root = Path(__file__).resolve().parent.parent / ".tmp" / "a4_replay"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    for _ in range(3):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            break


# ---------- 测试数据构造（plan.json / quotes_cache 文件格式即对外契约） ----------

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


def _quote(code="600519", close=10.5, pre_close=10.0, high=10.8, low=10.2, name="贵州茅台"):
    return {"name": name, "pre_close": pre_close, "high": high, "low": low, "close": close}


def _write_quotes(data_dir, d, quotes_map, degraded=None):
    p = data_dir / "quotes_cache" / f"{d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"date": d, "fetched_at": "2026-08-11T00:00:00", "source": "test",
         "quotes": quotes_map, "degraded": degraded or []}, ensure_ascii=False), "utf-8")


def _manual_setup(data_dir, position=3):
    """R12 手算样例输入：买入计划 07-15（撮合日 07-16）、卖出计划 07-16（撮合日 07-17）。"""
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=10.00, position=position)])
    _write_plan(data_dir, "2026-07-16",
                [_plan(rec_date="2026-07-16", side="sell", target_price=10.50, position=10)])
    _write_plan(data_dir, "2026-07-17", [])  # 空 plans 也构成结算日
    _write_quotes(data_dir, "2026-07-16",
                  {"600519": _quote(close=10.5, pre_close=10.0, high=10.8, low=10.0)})
    _write_quotes(data_dir, "2026-07-17",
                  {"600519": _quote(close=10.5, pre_close=10.5, high=10.8, low=10.2)})


def _run(data_dir):
    out = data_dir / "out"
    rc = replay.run_replay(report_dir=str(out))
    return rc, out


# ---------- R12 手算样例复现（验收标准 七.3） ----------

def test_manual_example_cash_101419_318(monkeypatch, data_dir):
    """10 万 × 3 成 target=10.00 → 2900 股；次日卖 2900×10.50 → 现金 101,419.318。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _manual_setup(data_dir)
    rc, out = _run(data_dir)
    assert rc == 0
    acc = json.loads((data_dir / "accounts" / "alang" / "account.json").read_text("utf-8"))
    assert acc["cash"] == pytest.approx(101_419.318)
    assert acc["positions"] == {}
    assert acc["realized_pnl"] == pytest.approx(
        (10.50 - 10.00) * 2900 - 7.6125 - 15.225 - 0.3045)
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    assert len(trades) == 2
    buy, sell = trades[0], trades[1]
    assert buy["date"] == "2026-07-16" and buy["side"] == "buy"
    assert buy["qty"] == 2900 and buy["price"] == 10.00
    assert buy["amount"] == pytest.approx(29_000.0)
    assert buy["fee"] == pytest.approx(7.25) and buy["transfer_fee"] == pytest.approx(0.29)
    assert sell["date"] == "2026-07-17" and sell["side"] == "sell"
    assert sell["qty"] == 2900 and sell["price"] == 10.50
    assert sell["fee"] == pytest.approx(7.6125)
    assert sell["stamp_tax"] == pytest.approx(15.225)
    assert sell["transfer_fee"] == pytest.approx(0.3045)
    required = {"date", "code", "name", "side", "target_price", "qty", "price", "amount",
                "fee", "stamp_tax", "transfer_fee", "status", "reject_reason", "cash_after"}
    assert required <= set(buy) and required <= set(sell), "交易记录字段须完整（R12-2 口径）"
    snaps = json.loads((data_dir / "accounts" / "alang" / "snapshots.json").read_text("utf-8"))
    assert [s["date"] for s in snaps] == ["2026-07-15", "2026-07-16", "2026-07-17"]
    assert snaps[0]["total_assets"] == pytest.approx(100_000.0)
    assert snaps[1]["total_assets"] == pytest.approx(70_992.46 + 2900 * 10.5)
    assert snaps[2]["total_assets"] == pytest.approx(101_419.318)
    meta = json.loads((out / "replay_meta.json").read_text("utf-8"))
    assert meta["quotes_missing"] == [] and meta["unsettled_count"] == 0
    assert meta["violations"] == []
    assert isinstance(meta["plan_hashes"], dict) and meta["plan_hashes"]
    md = (out / "replay_report.md").read_text("utf-8")
    assert "仅供参考，不构成投资建议" in md
    assert "未发现未被引擎拦截的违规" in md


# ---------- 确定性（验收标准 七.2） ----------

def test_deterministic_byte_identical(monkeypatch, data_dir):
    """同 plan + 同行情两次全量重放 → 全部产出文件逐字节一致。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _manual_setup(data_dir)

    def snapshot_bytes():
        _, out = _run(data_dir)
        files = {}
        for p in (data_dir / "accounts").rglob("*"):
            if p.is_file():
                files[f"accounts/{p.relative_to(data_dir / 'accounts')}"] = p.read_bytes()
        for name in ("replay_report.md", "replay_meta.json"):
            files[name] = (out / name).read_bytes()
        return files

    first = snapshot_bytes()
    second = snapshot_bytes()
    assert first and second
    assert set(first) == set(second)
    for k in first:
        assert first[k] == second[k], f"{k} 两次重放逐字节不一致（确定性）"


# ---------- 补单日闭环（验收标准 七.3） ----------

def test_supplement_day_closed_loop(monkeypatch, data_dir):
    """D 日计划变更（3 成 → 4 成，整文件覆盖）→ 重放后 D+1 起账户随新计划正确。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _manual_setup(data_dir, position=3)
    rc, _ = _run(data_dir)
    assert rc == 0
    assert json.loads((data_dir / "accounts" / "alang" / "account.json")
                      .read_text("utf-8"))["cash"] == pytest.approx(101_419.318)
    # 补单：D 日复盘数据变更（买入 3 成 → 4 成，即 3900 股）
    _manual_setup(data_dir, position=4)
    rc, _ = _run(data_dir)
    assert rc == 0
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    assert [t["qty"] for t in trades] == [3900, 3900], "旧 2900 股买入不得残留"
    acc = json.loads((data_dir / "accounts" / "alang" / "account.json").read_text("utf-8"))
    assert acc["cash"] == pytest.approx(101_908.738)
    assert acc["positions"] == {}
    snaps = json.loads((data_dir / "accounts" / "alang" / "snapshots.json").read_text("utf-8"))
    assert snaps[1]["date"] == "2026-07-16"
    assert snaps[1]["position_mv"] == pytest.approx(3900 * 10.5)
    assert snaps[2]["date"] == "2026-07-17"
    assert snaps[2]["total_assets"] == pytest.approx(101_908.738)


# ---------- 重放只读 plan.json（不读 pending.json） ----------

def test_replay_reads_only_plan_json_ignores_pending(monkeypatch, data_dir):
    """reviews/ 下存在 pending.json（含可疑计划）→ 重放输出必须逐字节不变、不受影响。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _manual_setup(data_dir)
    rc, _ = _run(data_dir)
    assert rc == 0
    baseline = {p.name: p.read_bytes() for p in (data_dir / "accounts" / "alang").glob("*")}
    pending = data_dir / "reviews" / "2026-07-15" / "pending.json"
    pending.write_text(json.dumps(
        [_plan(code="600999", target_price=99.99, position=10)], ensure_ascii=False), "utf-8")
    rc, _ = _run(data_dir)
    assert rc == 0
    for p in (data_dir / "accounts" / "alang").glob("*"):
        assert baseline[p.name] == p.read_bytes(), f"{p.name} 不应受 pending.json 影响"
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    assert all(t["code"] != "600999" for t in trades)


# ---------- 行情缺失与离线 ----------

def test_missing_quote_degraded_not_silent(monkeypatch, data_dir):
    """计划所需代码行情抓取失败（000000 恒失败）→ 不得静默成交：quotes_missing + 未结算 + 报告说明。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15",
                [_plan(code="000000", name="无效代码", target_price=10.00, position=3)])
    _write_plan(data_dir, "2026-07-16", [])
    # 不写 07-16 缓存 → ensure_quotes 抓取 000000 必然失败 → 降级
    rc, out = _run(data_dir)
    assert rc == 0, "行情缺失是降级标注，不是违规"
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    assert trades == [], "行情缺失不得静默成交"
    meta = json.loads((out / "replay_meta.json").read_text("utf-8"))
    assert any("000000" in str(x) for x in meta["quotes_missing"]), "缺失行情必须如实标注"
    assert meta["unsettled_count"] >= 1, "无法撮合的计划保持待结算并计数"
    md = (out / "replay_report.md").read_text("utf-8")
    assert ("缺失" in md) or ("未结算" in md), "报告中必须说明行情缺失/待结算"


def test_offline_replay_all_quotes_from_cache(monkeypatch, data_dir):
    """行情全部命中缓存（不联网）→ 计划按 R12 手算口径成交，meta 无缺失标注。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=10.00, position=3)])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16",
                  {"600519": _quote(close=10.5, pre_close=10.0, high=10.8, low=10.0)})
    rc, out = _run(data_dir)
    assert rc == 0
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    assert trades[0]["qty"] == 2900, "行情全部命中缓存时计划应正常成交"
    meta = json.loads((out / "replay_meta.json").read_text("utf-8"))
    assert meta["quotes_missing"] == []


# ---------- 违规注入被抓（引擎漏洞 → 退出码 1） ----------

def _fake_engine(behavior):
    """模拟引擎漏洞：买入委托真实引擎；指定行为绕过合规返回『成交』（含违规记录）。"""
    import stock_review_crew.accounts.engine as engine_mod
    orig = engine_mod.place_order

    def fake(account, code, name, side, target_price, position, day):
        if side == "buy":
            if behavior == "overdraft":
                qty, price = 3500, target_price
                amount = 999_999_999.0
                account["cash"] -= amount
                pos = account["positions"].setdefault(
                    code, {"qty": 0, "frozen_qty": 0, "avg_cost": price, "name": name})
                pos["qty"] += qty
                pos["frozen_qty"] += qty
                trade = {"date": day["date"], "code": code, "name": name, "side": "buy",
                         "target_price": target_price, "qty": qty, "price": price,
                         "amount": amount, "fee": 0.0, "stamp_tax": 0.0,
                         "transfer_fee": 0.0, "status": "成交", "reject_reason": "",
                         "cash_after": account["cash"]}
                return SimpleNamespace(ok=True, reason="", trade=trade)
            if behavior in ("price_out_of_limit", "price_below_limit_down", "non_lot"):
                qty = 150 if behavior == "non_lot" else 3500
                price = {"price_out_of_limit": 9.50,
                         "price_below_limit_down": 7.00}.get(behavior, target_price)
                amount = round(qty * price, 2)
                fee = round(max(5.0, amount * 0.00025), 2)
                transfer = round(amount * 0.00001, 2)
                account["cash"] -= amount + fee + transfer
                pos = account["positions"].setdefault(
                    code, {"qty": 0, "frozen_qty": 0, "avg_cost": price, "name": name})
                pos["qty"] += qty
                pos["frozen_qty"] += qty
                trade = {"date": day["date"], "code": code, "name": name, "side": "buy",
                         "target_price": target_price, "qty": qty, "price": price,
                         "amount": amount, "fee": fee, "stamp_tax": 0.0,
                         "transfer_fee": transfer, "status": "成交", "reject_reason": "",
                         "cash_after": account["cash"]}
                return SimpleNamespace(ok=True, reason="", trade=trade)
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
        return orig(account, code, name, side, target_price, position, day)

    return fake


@pytest.mark.parametrize("behavior,plan_side,keyword", [
    ("overdraft", "buy", "超资金"),
    ("price_out_of_limit", "buy", "涨停"),
    ("price_below_limit_down", "buy", "跌停"),
    ("non_lot", "buy", "非整百"),
    ("bare_short", "sell", "裸卖空"),
])
def test_replay_flags_engine_leak_violations(monkeypatch, data_dir, behavior, plan_side, keyword):
    """违规注入必须被断言层抓到：退出码 1，报告含引擎漏洞与对应规则。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    import stock_review_crew.accounts.engine as engine_mod
    monkeypatch.setattr(engine_mod, "place_order", _fake_engine(behavior))
    if plan_side == "sell":
        # 卖出注入需先有真实买入建立持仓（买入委托真实引擎）
        _write_plan(data_dir, "2026-07-15",
                    [_plan(side="buy", target_price=10.00, position=3)])
        _write_plan(data_dir, "2026-07-16",
                    [_plan(rec_date="2026-07-16", side="sell",
                           target_price=10.50, position=10)])
        _write_plan(data_dir, "2026-07-17", [])
        _write_quotes(data_dir, "2026-07-16",
                      {"600519": _quote(close=10.5, pre_close=10.0, high=10.8, low=10.0)})
        _write_quotes(data_dir, "2026-07-17",
                      {"600519": _quote(close=10.5, pre_close=10.5, high=10.8, low=10.2)})
    else:
        _write_plan(data_dir, "2026-07-15",
                    [_plan(side="buy", target_price=10.00, position=3)])
        _write_plan(data_dir, "2026-07-16", [])
        _write_plan(data_dir, "2026-07-17", [])
        if behavior == "price_out_of_limit":
            _write_quotes(data_dir, "2026-07-16",
                          {"600519": _quote(pre_close=8.3, close=8.5, high=8.6, low=8.2)})
        else:
            _write_quotes(data_dir, "2026-07-16",
                          {"600519": _quote(close=10.5, pre_close=10.0, high=10.8, low=10.0)})
    rc, out = _run(data_dir)
    assert rc == 1, f"{behavior} 违规注入必须被断言层抓到（退出码 1）"
    md = (out / "replay_report.md").read_text("utf-8")
    assert "引擎漏洞" in md
    assert keyword in md, f"报告应包含违规规则关键词：{keyword}"
    meta = json.loads((out / "replay_meta.json").read_text("utf-8"))
    assert meta["violations"], "违规必须进入 replay_meta.json"
    if behavior == "bare_short":
        acc = json.loads((data_dir / "accounts" / "alang" / "account.json").read_text("utf-8"))
        assert acc["positions"]["600519"]["qty"] < 0, "裸卖空应保留负持仓供审计"


# ---------- 退出码与引擎拒绝 ----------

def test_run_replay_exit_2_when_no_plan(monkeypatch, data_dir):
    """无任何 plan.json（数据不可用）→ 退出码 2。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    (data_dir / "reviews").mkdir(parents=True, exist_ok=True)
    rc, _ = _run(data_dir)
    assert rc == 2


def test_run_replay_tracks_engine_rejections(monkeypatch, data_dir):
    """引擎拒绝不算引擎漏洞：退出码 0，拒绝记录完整落盘并进入报告。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    import stock_review_crew.accounts.engine as engine_mod

    def fake(account, code, name, side, target_price, position, day):
        return SimpleNamespace(ok=False, reason="涨停拒买：目标价达到涨停价", trade=None)

    monkeypatch.setattr(engine_mod, "place_order", fake)
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=10.00, position=3)])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16",
                  {"600519": _quote(close=10.5, pre_close=10.0, high=10.8, low=10.0)})
    rc, out = _run(data_dir)
    assert rc == 0, "引擎拒绝不算引擎漏洞"
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    assert len(trades) == 1 and trades[0]["status"] == "拒绝"
    assert "涨停拒买" in trades[0]["reject_reason"]
    md = (out / "replay_report.md").read_text("utf-8")
    assert "涨停拒买" in md and "拒绝笔数" in md


def test_replay_rejects_same_day_sell_t1(monkeypatch, data_dir):
    """同日买入+卖出计划 → 引擎 T+1 拒绝并记录（正常拒绝，非引擎漏洞）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15", [
        _plan(side="buy", target_price=10.00, position=3),
        _plan(side="sell", target_price=10.50, position=10),
    ])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16",
                  {"600519": _quote(close=10.5, pre_close=10.0, high=10.8, low=10.0)})
    rc, _ = _run(data_dir)
    assert rc == 0
    trades = json.loads((data_dir / "accounts" / "alang" / "trades.json").read_text("utf-8"))
    assert [t["status"] for t in trades] == ["成交", "拒绝"]
    assert "T+1" in trades[1]["reject_reason"]


def test_replay_opens_accounts_per_skill_in_plans(monkeypatch, data_dir):
    """按计划中的 skill_id 动态开户：只开户出现在计划里的分析师。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15", [
        _plan(skill_id="alang", code="600519", target_price=10.00),
        _plan(skill_id="tiechui", code="600010", target_price=12.50, name="包钢股份"),
    ])
    _write_plan(data_dir, "2026-07-16", [])
    _write_quotes(data_dir, "2026-07-16", {
        "600519": _quote(close=10.5, pre_close=10.0, high=10.8, low=10.0),
        "600010": _quote(code="600010", close=12.5, pre_close=12.0, high=12.6, low=12.2),
    })
    rc, _ = _run(data_dir)
    assert rc == 0
    for sid in ("alang", "tiechui"):
        assert (data_dir / "accounts" / sid / "account.json").exists(), f"{sid} 应开户"
    assert not (data_dir / "accounts" / "bingchuan").exists(), "未出现在计划中的分析师不开户"


def test_trading_days_from_existing_plan_dates(monkeypatch, data_dir):
    """结算日序列 = 已存在 plan.json 的日期集合（排序；周末不硬编码，自适应）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    for d in ("2026-07-16", "2026-07-15", "2026-07-18"):  # 07-18 周六（--dates 显式指定）
        _write_plan(data_dir, d, [_plan(rec_date=d)])
    assert [str(d) for d in replay.list_review_dates()] == \
        ["2026-07-15", "2026-07-16", "2026-07-18"]


def test_trading_days_skips_dir_without_plan(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15", [_plan(rec_date="2026-07-15")])
    (data_dir / "reviews" / "2026-07-16").mkdir(parents=True, exist_ok=True)  # 有目录无 plan.json
    assert [str(d) for d in replay.list_review_dates()] == ["2026-07-15"]


def test_trading_days_empty_when_no_plans(monkeypatch, data_dir):
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    assert replay.list_review_dates() == []


# ---------- 无计划结算日仍推进账户状态（2026-08-13 缺陷修复） ----------

def test_settle_plans_no_plan_still_advances_and_unfreezes(monkeypatch, data_dir):
    """无计划结算日：仍推进 current_date、解冻 T+1 frozen_qty、为持仓落当日快照。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    _write_plan(data_dir, "2026-07-15",
                [_plan(side="buy", target_price=10.00, position=3)])
    _write_plan(data_dir, "2026-07-16", [])  # 07-16 无计划（空 plans 也构成结算日）
    _write_quotes(data_dir, "2026-07-16",
                  {"600519": _quote(close=10.5, pre_close=10.0, high=10.8, low=10.0)})
    _write_quotes(data_dir, "2026-07-17",
                  {"600519": _quote(close=10.5, pre_close=10.5, high=10.8, low=10.2)})

    # 第一步：撮合 07-15 买入计划（用 07-16 行情）→ 2900 股 T+1 冻结
    r1 = replay.settle_plans("2026-07-15", "2026-07-16")
    assert r1["filled"] == 1 and r1["rejected"] == 0
    acc = json.loads((data_dir / "accounts" / "alang" / "account.json").read_text("utf-8"))
    assert acc["current_date"] == "2026-07-16"
    pos = acc["positions"]["600519"]
    assert pos["qty"] == 2900 and pos["frozen_qty"] == 2900, "当日买入应 T+1 冻结"

    # 第二步：07-16 无计划 → 仍推进 current_date、解冻、落当日快照
    r2 = replay.settle_plans("2026-07-16", "2026-07-17")
    assert r2["filled"] == 0 and r2["rejected"] == 0
    assert r2["quotes_missing"] == [] and r2["unsettled"] == []
    assert "无计划" in r2.get("note", "") and "已推进账户状态" in r2.get("note", "")
    acc = json.loads((data_dir / "accounts" / "alang" / "account.json").read_text("utf-8"))
    assert acc["current_date"] == "2026-07-17", "无计划日也必须推进 current_date"
    pos = acc["positions"]["600519"]
    assert pos["qty"] == 2900 and pos["frozen_qty"] == 0, "T+1 到期应解冻"
    snaps = json.loads((data_dir / "accounts" / "alang" / "snapshots.json").read_text("utf-8"))
    assert [s["date"] for s in snaps] == ["2026-07-16", "2026-07-17"], "无计划日也必须落快照"
    assert snaps[-1]["total_assets"] == pytest.approx(acc["cash"] + 2900 * 10.5)


def test_settle_plans_no_plan_missing_quote_snapshot_at_cost(monkeypatch, data_dir):
    """无计划且持仓行情缺失：快照按成本价估值 + degraded 标注（绝不静默）。"""
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(data_dir))
    import stock_review_crew.accounts.engine as engine_mod
    import stock_review_crew.accounts.storage as storage_mod
    import stock_review_crew.tools.stock_data as stock_data_mod

    # 2026-08-14：get_day_quotes 缓存部分命中会补抓缺失代码，故改为 mock 主源失败
    # 来模拟「行情缺失」（而非依赖空缓存阻断抓取的旧语义）。
    def boom(code, date_str):
        raise RuntimeError("主源不可用（模拟）")

    monkeypatch.setattr(stock_data_mod, "fetch_stock_info", boom)

    # 手工构造已有账户：前一交易日买入 100 股 @10.00 冻结（T+1 未到期）
    acct = engine_mod.open_account("alang")
    acct["cash"] = 99_000.0
    acct["current_date"] = "2026-07-16"
    acct["positions"]["600519"] = {
        "name": "贵州茅台", "qty": 100, "frozen_qty": 100, "avg_cost": 10.0}
    storage_mod.save_account("alang", acct)

    _write_plan(data_dir, "2026-07-16", [])  # 无计划
    _write_quotes(data_dir, "2026-07-17", {})  # 缓存不含持仓代码 + 主源 mock 失败 → 行情缺失

    r = replay.settle_plans("2026-07-16", "2026-07-17")
    assert "无计划" in r.get("note", "") and "已推进账户状态" in r.get("note", "")
    assert any("600519" in d and "快照按成本价估值" in d for d in r["degraded"]), \
        "持仓行情缺失必须 degraded 标注（快照按成本价估值）"
    acc = json.loads((data_dir / "accounts" / "alang" / "account.json").read_text("utf-8"))
    assert acc["current_date"] == "2026-07-17"
    assert acc["positions"]["600519"]["frozen_qty"] == 0
    snaps = json.loads((data_dir / "accounts" / "alang" / "snapshots.json").read_text("utf-8"))
    assert len(snaps) == 1 and snaps[0]["date"] == "2026-07-17"
    assert snaps[0]["estimated_prices"] == ["600519"]
    assert snaps[0]["total_assets"] == pytest.approx(99_000.0 + 100 * 10.0)
