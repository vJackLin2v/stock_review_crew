# -*- coding: utf-8 -*-
"""
R12 分析师模拟账户系统 — 验收测试（测试先行：R12 开发中，全部 skip）
======================================================================

契约来源（唯一依据，编写时未读任何实现代码）：
- docs/requirements.md §五（API 契约）/ §六（数据字典口径铁律）
- docs/requirements-r12.md 全文（含 2026-08-08 已确认口径 Q1/Q2/Q4/Q6/Q9）

「契约探针」声明：
- 本文件顶层不 import 任何实现模块，保证 R12 开发期可正常收集（收集数=用例数）。
- 用例内通过惰性导入访问账户引擎 / 存储 / FastAPI 应用（见 _engine()/_storage()/_client()），
  集成阶段移除 skip 后由探针对齐真实签名；断言仍以本文档契约为准。
- 数量折算口径（用户手算确认）：qty = floor(可用现金×成数 / (目标价×1.0001) / 100) × 100，
  即 10.00 元目标价按除数 10.001 手算；1.0001 为费用预留系数。
- 金额单位元；比率小数；拒绝原因中文；免责声明 "仅供参考，不构成投资建议"。

用例清单（对应任务书 7 项）：
① 账户初始化 ② 买入/卖出校验与费用手算 ③ 盈亏与快照 ④ 规则引擎不下单
⑤ 合规记录 ⑥ API 契约 ⑦ 报告兼容
"""

import json
import math

import pytest


SKIP_REASON = "R12 开发中，集成阶段启用"

INITIAL_CASH = 100_000.0
ANALYSTS = ["alang", "爱在冰川", "拔小弦", "炒股养家", "铁锤"]
FUTURE_SKILL_ID = "future_skill_id_001"
DISCLAIMER = "仅供参考，不构成投资建议"

# 费用口径（Q4 已确认）：佣金 万2.5 双向（最低 5 元）；印花税卖出 0.05%；过户费 0.001% 双向
FEE_COMMISSION_RATE = 0.00025
FEE_COMMISSION_MIN = 5.0
FEE_STAMP_TAX_RATE = 0.0005
FEE_TRANSFER_RATE = 0.00001


# --------------------------------------------------------------------------
# 契约手算助手（需求文档口径的「算盘」，测试内手算期望值）
# --------------------------------------------------------------------------

def calc_qty(cash, price, position):
    """已确认口径：数量 = floor(现金×成数/(价格×1.0001)/100)×100（100 股整数倍折算）。"""
    return math.floor(cash * position / (price * 1.0001) / 100) * 100


def calc_fees(amount, side):
    """费用：佣金 max(金额×万2.5, 5)；印花税仅卖出 0.05%；过户费双向 0.001%。"""
    commission = max(amount * FEE_COMMISSION_RATE, FEE_COMMISSION_MIN)
    stamp_tax = amount * FEE_STAMP_TAX_RATE if side == "sell" else 0.0
    transfer_fee = amount * FEE_TRANSFER_RATE
    return commission, stamp_tax, transfer_fee


def calc_limit_prices(pre_close, code):
    """涨跌停价：主板 ±10%、创业板/科创板(30/68 前缀) ±20%，前收盘×系数四舍五入到 0.01。"""
    rate = 0.20 if code.startswith(("30", "68")) else 0.10
    return round(pre_close * (1 - rate), 2), round(pre_close * (1 + rate), 2)


def _engine():
    """契约探针：惰性导入账户引擎（架构草案 A1：src/stock_review_crew/accounts/engine.py）。"""
    from stock_review_crew.accounts import engine  # 契约探针：集成阶段对齐签名
    return engine


def _storage():
    """契约探针：惰性导入账户存储（架构草案 A1：src/stock_review_crew/accounts/storage.py）。"""
    from stock_review_crew.accounts import storage  # 契约探针
    return storage


def _client():
    """契约探针：惰性导入 FastAPI 应用（AGENTS.md：backend.main:app）。"""
    from fastapi.testclient import TestClient
    from backend.main import app  # 契约探针
    return TestClient(app)


def _buy(eng, acc, *, date, code="600000", name="浦发银行", target_price,
         position=None, qty=None, day_low, day_high, pre_close):
    """契约探针：买入下单封装（目标价+成数 折算数量；qty 直给用于建仓/费用微场景）。"""
    kwargs = dict(date=date, code=code, name=name, side="buy", target_price=target_price,
                  day_low=day_low, day_high=day_high, pre_close=pre_close)
    if position is not None:
        kwargs["position"] = position
    if qty is not None:
        kwargs["qty"] = qty
    return eng.place_order(acc, **kwargs)


def _sell(eng, acc, *, date, code="600000", name="浦发银行", target_price,
          position=None, qty=None, day_low, day_high, pre_close):
    """契约探针：卖出下单封装（成数按持仓折算；qty 直给用于校验场景）。"""
    kwargs = dict(date=date, code=code, name=name, side="sell", target_price=target_price,
                  day_low=day_low, day_high=day_high, pre_close=pre_close)
    if position is not None:
        kwargs["position"] = position
    if qty is not None:
        kwargs["qty"] = qty
    return eng.place_order(acc, **kwargs)


def _seed_1000_shares(eng, acc, date="2026-08-03"):
    """建仓 1000 股 @10.00（卖出类场景的持仓前提）。"""
    acc, rec = _buy(eng, acc, date=date, target_price=10.00, qty=1000,
                    day_low=9.80, day_high=10.00, pre_close=9.10)
    assert rec["status"] == "成交"
    return acc


# --------------------------------------------------------------------------
# ① 账户初始化（R12-1）
# --------------------------------------------------------------------------

@pytest.mark.skip(reason=SKIP_REASON)
@pytest.mark.parametrize("skill_id", ANALYSTS, ids=ANALYSTS)
def test_open_account_initial_state(skill_id):
    """R12-1：每位分析师首次访问自动开户：初始现金 100,000 元、无持仓、无交易。"""
    eng = _engine()
    acc = eng.open_account(skill_id)  # 契约探针：首次访问自动开户
    assert acc["cash"] == pytest.approx(INITIAL_CASH, abs=0.005)
    assert not acc["positions"], "初始账户必须无持仓"
    assert not acc["trades"], "初始账户必须无交易"


@pytest.mark.skip(reason=SKIP_REASON)
def test_auto_open_future_skill_id():
    """R12-1：未来新增分析师按 skill id 动态开户（零改动接入）。"""
    eng = _engine()
    acc = eng.open_account(FUTURE_SKILL_ID)
    assert acc["skill_id"] == FUTURE_SKILL_ID
    assert acc["cash"] == pytest.approx(INITIAL_CASH, abs=0.005)
    assert not acc["positions"] and not acc["trades"]


@pytest.mark.skip(reason=SKIP_REASON)
def test_initial_daily_snapshot():
    """R12-2：开户当日快照 total_assets = cash + 持仓市值 = 100,000，realized_pnl = 0。"""
    eng = _engine()
    acc = eng.open_account("alang")
    snap = eng.snapshot(acc, date="2026-08-03", close_prices={})  # 契约探针
    assert snap["date"] == "2026-08-03"
    assert snap["cash"] == pytest.approx(INITIAL_CASH, abs=0.005)
    assert snap["position_mv"] == pytest.approx(0.0, abs=0.005)
    assert snap["total_assets"] == pytest.approx(INITIAL_CASH, abs=0.005)
    assert snap["realized_pnl"] == pytest.approx(0.0, abs=0.005)


@pytest.mark.skip(reason=SKIP_REASON)
def test_account_storage_layout(tmp_path):
    """R12-1：落盘 data/accounts/{skill_id}/：account.json（现金/持仓/冻结）、trades.json、snapshots.json。
    契约探针：存储根可注入，测试用临时目录，避免向仓库写入测试数据（铁律 3）。"""
    eng = _engine()
    st = _storage()
    root = tmp_path / "accounts"
    acc = eng.open_account("alang", root=root)  # 契约探针
    st.save(acc, root=root)
    skill_dir = root / "alang"
    assert (skill_dir / "account.json").exists()
    assert (skill_dir / "trades.json").exists()
    assert (skill_dir / "snapshots.json").exists()
    account = json.loads((skill_dir / "account.json").read_text(encoding="utf-8"))
    assert {"cash", "positions"} <= set(account.keys())
    # 冻结字段（T+1 相关）存在性：键名未冻结，做宽松校验
    assert any("freeze" in k or "frozen" in k or "lock" in k for k in account.keys()), "account.json 应有冻结字段"
    assert json.loads((skill_dir / "trades.json").read_text(encoding="utf-8")) == []
    assert json.loads((skill_dir / "snapshots.json").read_text(encoding="utf-8")) == []


# --------------------------------------------------------------------------
# ② 买入校验（手算场景）
# --------------------------------------------------------------------------

@pytest.mark.skip(reason=SKIP_REASON)
def test_buy_normal_manual_calculation():
    """手算（用户点名）：现金 10 万、目标价 10.00、仓位 3 成 → 2900 股；
    金额 29,000；佣金 max(29000×0.00025, 5)=7.25、过户费 0.29、印花税 0；
    成交后现金 100000−29000−7.54 = 70,992.46，持仓 2900 股。"""
    eng = _engine()
    price, position = 10.00, 0.3
    qty = calc_qty(INITIAL_CASH, price, position)
    assert qty == 2900, f"手算数量应 2900，实际 {qty}"
    amount = qty * price
    commission, stamp_tax, transfer_fee = calc_fees(amount, "buy")
    assert commission == pytest.approx(7.25, abs=0.005)
    assert transfer_fee == pytest.approx(0.29, abs=0.005)
    assert stamp_tax == pytest.approx(0.0, abs=0.005)

    acc = eng.open_account("alang")
    acc, record = _buy(eng, acc, date="2026-08-03", target_price=price, position=position,
                       day_low=9.80, day_high=10.00, pre_close=9.90)
    assert record["status"] == "成交"
    assert record["qty"] == 2900
    assert record["price"] == pytest.approx(10.00, abs=0.005)
    assert record["amount"] == pytest.approx(29_000.0, abs=0.005)
    assert record["fee"] == pytest.approx(7.25, abs=0.005)             # 佣金（含最低价兜底）
    assert record["stamp_tax"] == pytest.approx(0.0, abs=0.005)        # 买入无印花税
    assert record["transfer_fee"] == pytest.approx(0.29, abs=0.005)    # 过户费双向
    assert acc["cash"] == pytest.approx(70_992.46, abs=0.005)
    pos = acc["positions"]["600000"]
    assert pos["qty"] == 2900
    assert pos["avg_cost"] == pytest.approx(10.00, abs=0.005)  # 盈亏公式口径：avg_cost 不含费用


@pytest.mark.skip(reason=SKIP_REASON)
@pytest.mark.parametrize("price,position", [(10.00, 0.3), (25.00, 0.5), (8.88, 0.2), (50.00, 1.0), (100.00, 0.1)],
                         ids=["p10_p30", "p25_p50", "p8.88_p20", "p50_p100", "p100_p10"])
def test_buy_qty_always_multiple_of_100(price, position):
    """校验 4：折算数量恒为 100 股整数倍（成交时 qty%100==0，折算为 0 则拒绝）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, record = _buy(eng, acc, date="2026-08-03", target_price=price, position=position,
                       day_low=price - 2, day_high=price, pre_close=round(price / 1.1, 2) + 0.02)
    if record["status"] == "成交":
        assert record["qty"] % 100 == 0
    else:
        assert record["status"] == "拒绝"


@pytest.mark.skip(reason=SKIP_REASON)
def test_buy_reject_insufficient_cash_or_zero_qty():
    """校验 1：仓位 10 成买入高价股 → 折算数量为 0（或金额+费用>现金）→ 拒绝「现金不足」（或数量 0 拒绝）。"""
    eng = _engine()
    price, position = 1000.00, 1.0
    qty = calc_qty(INITIAL_CASH, price, position)
    assert qty == 0, "高价股 10 成折算数量应不足一手"
    acc = eng.open_account("alang")
    acc, record = _buy(eng, acc, date="2026-08-03", target_price=price, position=position,
                       day_low=990.0, day_high=1010.0, pre_close=950.0)
    assert record["status"] == "拒绝"
    reason = record["reject_reason"]
    assert ("现金不足" in reason) or ("数量" in reason), f"拒绝原因应含「现金不足」或「数量」，实际：{reason}"
    assert acc["cash"] == pytest.approx(INITIAL_CASH, abs=0.005)  # 拒绝后现金分文未动


@pytest.mark.skip(reason=SKIP_REASON)
@pytest.mark.parametrize("price,position", [(10.00, 1.0), (10.20, 1.0), (33.33, 0.5), (500.00, 0.6), (1000.00, 1.0)],
                         ids=["p10_full", "p10.2_full", "p33.33_p50", "p500_p60", "p1000_full"])
def test_buy_never_overdraws_cash(price, position):
    """引擎不变量：任何买入成交后 成交金额+费用 ≤ 成交前可用现金（绝不用超额的钱）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    pre_cash = acc["cash"]
    acc, record = _buy(eng, acc, date="2026-08-04", target_price=price, position=position,
                       day_low=price - 2, day_high=price, pre_close=round(price / 1.1, 2) + 0.02)
    if record["status"] == "成交":
        total_cost = (record["amount"] + record["fee"]
                      + record["stamp_tax"] + record["transfer_fee"])
        assert total_cost <= pre_cash + 1e-9, "成交总额不得超过成交前可用现金"
        assert acc["cash"] >= -1e-9
        assert acc["cash"] == pytest.approx(pre_cash - total_cost, abs=0.01)


@pytest.mark.skip(reason=SKIP_REASON)
def test_buy_reject_at_limit_up():
    """校验 3 / Q2：pre_close=10.00（主板）→ 涨停价 11.00；target=11.00 ≥ 涨停价 → 拒绝（封板买不进）。"""
    eng = _engine()
    limit_down, limit_up = calc_limit_prices(10.00, "600000")
    assert (limit_down, limit_up) == (9.00, 11.00)
    acc = eng.open_account("alang")
    acc, record = _buy(eng, acc, date="2026-08-05", target_price=limit_up, position=0.3,
                       day_low=10.50, day_high=limit_up, pre_close=10.00)
    assert record["status"] == "拒绝"
    assert "涨停" in record["reject_reason"], record["reject_reason"]
    assert acc["cash"] == pytest.approx(INITIAL_CASH, abs=0.005)


@pytest.mark.skip(reason=SKIP_REASON)
def test_buy_ok_below_limit_up_when_touched():
    """Q1/Q2：target=10.98 < 涨停 11.00 且当日 high=10.98（区间覆盖目标价）→ 按目标价成交。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, record = _buy(eng, acc, date="2026-08-05", target_price=10.98, position=0.3,
                       day_low=10.60, day_high=10.98, pre_close=10.00)
    assert record["status"] == "成交"
    assert record["price"] == pytest.approx(10.98, abs=0.005)
    assert record["qty"] == calc_qty(INITIAL_CASH, 10.98, 0.3) == 2700


@pytest.mark.skip(reason=SKIP_REASON)
def test_buy_reject_target_not_touched():
    """Q1/Q2：当日 low=10.10 > target=10.00（价格区间未覆盖目标价）→ 拒绝「未触及」。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, record = _buy(eng, acc, date="2026-08-05", target_price=10.00, position=0.3,
                       day_low=10.10, day_high=10.80, pre_close=10.00)
    assert record["status"] == "拒绝"
    assert "未触及" in record["reject_reason"], record["reject_reason"]
    assert acc["cash"] == pytest.approx(INITIAL_CASH, abs=0.005)


@pytest.mark.skip(reason=SKIP_REASON)
def test_limit_board_20pct_gem_star():
    """校验 3：创业板/科创板 ±20%（30/68 前缀）；同一 target=11.50：主板涨停拒买、创业板成交。"""
    eng = _engine()
    gem_limit_down, gem_limit_up = calc_limit_prices(10.00, "300001")
    assert (gem_limit_down, gem_limit_up) == (8.00, 12.00)
    # 主板 600000：target 11.50 ≥ 涨停 11.00 → 拒绝
    acc = eng.open_account("alang")
    acc, rec_main = _buy(eng, acc, date="2026-08-05", code="600000", target_price=11.50,
                         position=0.3, day_low=10.20, day_high=11.50, pre_close=10.00)
    assert rec_main["status"] == "拒绝"
    assert "涨停" in rec_main["reject_reason"]
    # 创业板 300001：target 11.50 < 涨停 12.00 且触及 → 成交
    acc2 = eng.open_account("炒股养家")
    acc2, rec_gem = _buy(eng, acc2, date="2026-08-05", code="300001", name="特锐德",
                         target_price=11.50, position=0.3, day_low=10.20, day_high=11.50,
                         pre_close=10.00)
    assert rec_gem["status"] == "成交"
    assert rec_gem["price"] == pytest.approx(11.50, abs=0.005)


@pytest.mark.skip(reason=SKIP_REASON)
def test_t_plus_1_same_day_sell_rejected():
    """校验 2：当日买入 2900 股后当日卖出同数量 → 拒绝「当日买入不可卖」（可卖=总持仓−当日买入）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, rec_buy = _buy(eng, acc, date="2026-08-03", target_price=10.00, position=0.3,
                        day_low=9.80, day_high=10.00, pre_close=9.10)
    assert rec_buy["status"] == "成交"
    assert acc["positions"]["600000"]["qty"] == 2900
    acc, rec_sell = _sell(eng, acc, date="2026-08-03", target_price=10.20, position=1.0,
                          day_low=10.00, day_high=10.30, pre_close=9.30)
    assert rec_sell["status"] == "拒绝"
    reason = rec_sell["reject_reason"]
    assert any(k in reason for k in ("T+1", "当日买入", "不可卖")), reason
    assert acc["positions"]["600000"]["qty"] == 2900  # 持仓未动


@pytest.mark.skip(reason=SKIP_REASON)
def test_t_plus_1_next_day_sell_allowed():
    """校验 2：次日卖出可用（2900 股全部可卖，按目标价 10.50 成交）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, _ = _buy(eng, acc, date="2026-08-03", target_price=10.00, position=0.3,
                  day_low=9.80, day_high=10.00, pre_close=9.10)
    acc, record = _sell(eng, acc, date="2026-08-04", target_price=10.50, position=1.0,
                        day_low=10.20, day_high=10.60, pre_close=9.30)
    assert record["status"] == "成交"
    assert record["qty"] == 2900
    assert record["amount"] == pytest.approx(30_450.0, abs=0.005)
    assert record["fee"] == pytest.approx(7.6125, abs=0.005)          # 佣金 max(7.6125,5)
    assert record["stamp_tax"] == pytest.approx(15.225, abs=0.005)    # 印花税卖出 0.05%
    assert record["transfer_fee"] == pytest.approx(0.3045, abs=0.005)
    assert acc["cash"] == pytest.approx(101_419.318, abs=0.005)
    assert acc["positions"]["600000"]["qty"] == 0


@pytest.mark.skip(reason=SKIP_REASON)
def test_sell_normal_half_position_manual():
    """手算：持仓 1000 股、仓位 5 成 → 卖出 500 股；金额 5,250；
    佣金 max(1.3125,5)=5、印花税 2.625、过户费 0.0525。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc = _seed_1000_shares(eng, acc)
    acc, record = _sell(eng, acc, date="2026-08-04", target_price=10.50, position=0.5,
                        day_low=10.20, day_high=10.60, pre_close=9.40)
    assert record["status"] == "成交"
    assert record["qty"] == 500
    assert record["amount"] == pytest.approx(5_250.0, abs=0.005)
    assert record["fee"] == pytest.approx(5.0, abs=0.005)
    assert record["stamp_tax"] == pytest.approx(2.625, abs=0.005)
    assert record["transfer_fee"] == pytest.approx(0.0525, abs=0.005)
    assert acc["positions"]["600000"]["qty"] == 500
    assert acc["cash"] == pytest.approx(95_237.2225, abs=0.005)


@pytest.mark.skip(reason=SKIP_REASON)
def test_sell_reject_at_limit_down():
    """校验 3 / Q2：pre_close=10.00 → 跌停价 9.00；target=9.00 ≤ 跌停价 → 拒绝（跌停卖不出）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc = _seed_1000_shares(eng, acc)
    acc, record = _sell(eng, acc, date="2026-08-04", target_price=9.00, position=1.0,
                        day_low=9.00, day_high=9.50, pre_close=10.00)
    assert record["status"] == "拒绝"
    assert "跌停" in record["reject_reason"], record["reject_reason"]
    assert acc["positions"]["600000"]["qty"] == 1000


@pytest.mark.skip(reason=SKIP_REASON)
def test_sell_ok_above_limit_down_when_touched():
    """Q1/Q2：target=9.01 > 跌停 9.00 且当日 low=9.01（区间覆盖）→ 成交。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc = _seed_1000_shares(eng, acc)
    acc, record = _sell(eng, acc, date="2026-08-04", target_price=9.01, position=1.0,
                        day_low=9.01, day_high=9.40, pre_close=10.00)
    assert record["status"] == "成交"
    assert record["qty"] == 1000
    assert record["price"] == pytest.approx(9.01, abs=0.005)


@pytest.mark.skip(reason=SKIP_REASON)
def test_sell_reject_bare_short():
    """校验 4：卖出数量 > 可卖持仓 → 拒绝（不得裸卖空）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc = _seed_1000_shares(eng, acc)
    acc, record = _sell(eng, acc, date="2026-08-04", target_price=10.20, qty=1500,
                        day_low=10.00, day_high=10.30, pre_close=9.30)
    assert record["status"] == "拒绝"
    reason = record["reject_reason"]
    assert any(k in reason for k in ("可卖", "持仓", "裸卖")), reason
    assert acc["positions"]["600000"]["qty"] == 1000


@pytest.mark.skip(reason=SKIP_REASON)
@pytest.mark.parametrize("qty", [50, 150, 2950], ids=["lt100", "not_100", "2950"])
def test_qty_not_multiple_of_100_rejected(qty):
    """校验 4：卖出数量非 100 整数倍（含 <100）→ 拒绝（折算结果恒整百；直接指定则逐笔校验）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, _ = _buy(eng, acc, date="2026-08-03", target_price=10.00, qty=3000,
                  day_low=9.80, day_high=10.00, pre_close=9.10)
    acc, record = _sell(eng, acc, date="2026-08-04", target_price=10.20, qty=qty,
                        day_low=10.00, day_high=10.30, pre_close=9.30)
    assert record["status"] == "拒绝"
    reason = record["reject_reason"]
    assert any(k in reason for k in ("100", "整百", "整数倍")), reason
    assert acc["positions"]["600000"]["qty"] == 3000


@pytest.mark.skip(reason=SKIP_REASON)
def test_fee_commission_minimum_5():
    """校验 5：佣金 万2.5 双向、最低 5 元（小额交易：100 股×10 元=1000 元 → 佣金 5 元）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, rec_buy = _buy(eng, acc, date="2026-08-03", target_price=10.00, qty=100,
                        day_low=9.80, day_high=10.00, pre_close=9.10)
    assert rec_buy["status"] == "成交"
    assert rec_buy["fee"] == pytest.approx(5.0, abs=0.005)  # max(1000×0.00025, 5)
    acc, rec_sell = _sell(eng, acc, date="2026-08-04", target_price=10.00, qty=100,
                          day_low=9.90, day_high=10.10, pre_close=9.10)
    assert rec_sell["status"] == "成交"
    assert rec_sell["fee"] == pytest.approx(5.0, abs=0.005)  # 卖出小额佣金同样最低 5 元


@pytest.mark.skip(reason=SKIP_REASON)
def test_fee_stamp_tax_sell_only():
    """校验 5：印花税仅卖出 0.05%，买入为 0。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, rec_buy = _buy(eng, acc, date="2026-08-03", target_price=10.00, qty=1000,
                        day_low=9.80, day_high=10.00, pre_close=9.10)
    assert rec_buy["status"] == "成交"
    assert rec_buy["stamp_tax"] == pytest.approx(0.0, abs=0.005)
    acc, rec_sell = _sell(eng, acc, date="2026-08-04", target_price=10.00, qty=1000,
                          day_low=9.90, day_high=10.10, pre_close=9.10)
    assert rec_sell["status"] == "成交"
    assert rec_sell["stamp_tax"] == pytest.approx(10_000.0 * 0.0005, abs=0.005)  # =5.0


@pytest.mark.skip(reason=SKIP_REASON)
def test_fee_transfer_fee_both_sides():
    """校验 5：过户费 0.001% 双向（买 2900×10=29000 → 0.29；卖 500×10.5=5250 → 0.0525）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, rec_buy = _buy(eng, acc, date="2026-08-03", target_price=10.00, position=0.3,
                        day_low=9.80, day_high=10.00, pre_close=9.10)
    assert rec_buy["status"] == "成交"
    assert rec_buy["transfer_fee"] == pytest.approx(0.29, abs=0.005)
    acc, rec_sell = _sell(eng, acc, date="2026-08-04", target_price=10.50, qty=500,
                          day_low=10.20, day_high=10.60, pre_close=9.40)
    assert rec_sell["status"] == "成交"
    assert rec_sell["transfer_fee"] == pytest.approx(0.0525, abs=0.005)


# --------------------------------------------------------------------------
# ③ 盈亏与快照（R12-2）
# --------------------------------------------------------------------------

@pytest.mark.skip(reason=SKIP_REASON)
def test_realized_pnl_after_sell():
    """盈亏：卖出后 realized_pnl = (卖出价−avg_cost)×数量 − 卖出费用。
    建仓 1000 股@10.00；卖 500 股@10.50 → 费用 5+2.625+0.0525=7.6775 →
    (10.50−10.00)×500 − 7.6775 = 242.3225。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc = _seed_1000_shares(eng, acc)
    acc, record = _sell(eng, acc, date="2026-08-04", target_price=10.50, qty=500,
                        day_low=10.20, day_high=10.60, pre_close=9.40)
    assert record["status"] == "成交"
    expected = (10.50 - 10.00) * 500 - (5.0 + 2.625 + 0.0525)
    assert expected == pytest.approx(242.3225, abs=0.005)
    assert acc["realized_pnl"] == pytest.approx(expected, abs=0.005)


@pytest.mark.skip(reason=SKIP_REASON)
def test_daily_snapshot_total_assets():
    """快照：total_assets = cash + 持仓市值（按收盘价）；position_mv = qty×close。
    D1 买 2900@10.00 收盘 10.50 → cash 70,992.46、mv 30,450、total 101,442.46、realized 0；
    D2 卖 500@11.00 后收盘 11.20 → cash 76,484.655、mv 26,880、total 103,364.655、realized 492.195。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, _ = _buy(eng, acc, date="2026-08-03", target_price=10.00, position=0.3,
                  day_low=9.80, day_high=10.00, pre_close=9.10)
    snap1 = eng.snapshot(acc, date="2026-08-03", close_prices={"600000": 10.50})
    assert snap1["cash"] == pytest.approx(70_992.46, abs=0.005)
    assert snap1["position_mv"] == pytest.approx(2900 * 10.50, abs=0.005)      # 30,450
    assert snap1["total_assets"] == pytest.approx(101_442.46, abs=0.005)
    assert snap1["realized_pnl"] == pytest.approx(0.0, abs=0.005)

    acc, _ = _sell(eng, acc, date="2026-08-04", target_price=11.00, qty=500,
                   day_low=10.90, day_high=11.00, pre_close=10.00)
    snap2 = eng.snapshot(acc, date="2026-08-04", close_prices={"600000": 11.20})
    assert snap2["position_mv"] == pytest.approx(2400 * 11.20, abs=0.005)      # 26,880
    assert snap2["realized_pnl"] == pytest.approx(492.195, abs=0.005)
    assert snap2["cash"] == pytest.approx(76_484.655, abs=0.005)
    assert snap2["total_assets"] == pytest.approx(103_364.655, abs=0.005)


@pytest.mark.skip(reason=SKIP_REASON)
def test_account_continues_across_same_day_reviews():
    """§五 风险：账户按日期推进，不随同日多份复盘覆盖回滚；当日买入仍受 T+1 约束。"""
    eng = _engine()
    acc = eng.open_account("alang")
    acc, _ = _buy(eng, acc, date="2026-08-03", target_price=10.00, position=0.3,
                  day_low=9.80, day_high=10.00, pre_close=9.10)
    # 同日另一份复盘（覆盖场景）：重新开户/加载不得重置账户
    acc2 = eng.open_account("alang", date="2026-08-03")
    assert acc2["cash"] == pytest.approx(70_992.46, abs=0.005)
    assert acc2["positions"]["600000"]["qty"] == 2900
    # 当日买入仍不可卖（T+1 冻结按日期推进）
    acc2, rec = _sell(eng, acc2, date="2026-08-03", target_price=10.20, position=1.0,
                      day_low=10.00, day_high=10.30, pre_close=9.30)
    assert rec["status"] == "拒绝"


# --------------------------------------------------------------------------
# ④ 规则引擎不下单（R12-2 校验 6）
# --------------------------------------------------------------------------

@pytest.mark.skip(reason=SKIP_REASON)
def test_rule_engine_mode_no_trades():
    """校验 6：规则引擎（LLM 降级）模式不产生新交易，账户只读；快照仍输出（无交易）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    before = (acc["cash"], dict(acc["positions"]), len(acc["trades"]))
    acc, record = _buy(eng, acc, date="2026-08-03", target_price=10.00, position=0.3,
                       day_low=9.80, day_high=10.00, pre_close=9.10,
                       **{"mode": "rule_engine"})  # 降级模式：不产生新交易
    after = (acc["cash"], acc["positions"], len(acc["trades"]))
    assert after == before, "规则引擎模式账户必须只读"
    if record is not None:  # 无效调用可记录但不得成交
        assert record["status"] == "拒绝"
    snap = eng.snapshot(acc, date="2026-08-03", close_prices={"600000": 10.00})
    assert snap["total_assets"] == pytest.approx(INITIAL_CASH, abs=0.005)  # 快照仍输出


# --------------------------------------------------------------------------
# ⑤ 合规记录（R12-2 每笔记录 + 拒绝原因中文）
# --------------------------------------------------------------------------

REJECT_CASES = [
    pytest.param(
        {"label": "超资金-折算数量0", "seed": False,
         "order": dict(date="2026-08-03", code="600519", name="贵州茅台", side="buy",
                       target_price=1000.00, position=1.0, day_low=990.0, day_high=1010.0,
                       pre_close=950.0)},
        ["现金不足", "数量"], id="over_cash"),
    pytest.param(
        {"label": "涨停拒买", "seed": False,
         "order": dict(date="2026-08-03", code="600000", name="浦发银行", side="buy",
                       target_price=11.00, position=0.3, day_low=10.50, day_high=11.00,
                       pre_close=10.00)},
        ["涨停"], id="limit_up"),
    pytest.param(
        {"label": "未触及目标价", "seed": False,
         "order": dict(date="2026-08-03", code="600000", name="浦发银行", side="buy",
                       target_price=10.00, position=0.3, day_low=10.10, day_high=10.80,
                       pre_close=10.00)},
        ["未触及"], id="not_touched"),
    pytest.param(
        {"label": "T+1当日卖出", "seed": True,
         "order": dict(date="2026-08-03", code="600000", name="浦发银行", side="sell",
                       target_price=10.20, position=1.0, day_low=10.00, day_high=10.30,
                       pre_close=9.30)},
        ["t+1", "当日买入", "不可卖"], id="t_plus_1"),
    pytest.param(
        {"label": "非100整数倍", "seed": True,
         "order": dict(date="2026-08-04", code="600000", name="浦发银行", side="sell",
                       target_price=10.20, qty=150, day_low=10.00, day_high=10.30,
                       pre_close=9.30)},
        ["100", "整百", "整数倍"], id="not_multiple_100"),
    pytest.param(
        {"label": "跌停拒卖", "seed": True,
         "order": dict(date="2026-08-04", code="600000", name="浦发银行", side="sell",
                       target_price=9.00, position=1.0, day_low=9.00, day_high=9.50,
                       pre_close=10.00)},
        ["跌停"], id="limit_down"),
    pytest.param(
        {"label": "裸卖空", "seed": True,
         "order": dict(date="2026-08-04", code="600000", name="浦发银行", side="sell",
                       target_price=10.20, qty=1500, day_low=10.00, day_high=10.30,
                       pre_close=9.30)},
        ["可卖", "持仓", "裸卖"], id="bare_short"),
]


@pytest.mark.skip(reason=SKIP_REASON)
@pytest.mark.parametrize("case,reason_keywords", REJECT_CASES)
def test_rejected_order_records_complete(case, reason_keywords):
    """R12-2：每笔拒绝都有完整记录（date/code/name/side/qty/price/amount/fee/stamp_tax/
    transfer_fee/status/reject_reason/balance_after），reject_reason 为中文且命中规则原因；
    拒绝不动现金（balance_after=成交前现金）。"""
    eng = _engine()
    acc = eng.open_account("alang")
    cash_before = acc["cash"]
    if case["seed"]:
        acc = _seed_1000_shares(eng, acc)
        cash_before = acc["cash"]
    order = dict(case["order"])
    order.setdefault("code", "600000")
    order.setdefault("name", "浦发银行")
    acc, record = eng.place_order(acc, **order)
    required = {"date", "code", "name", "side", "qty", "price", "amount", "fee",
                "stamp_tax", "transfer_fee", "status", "reject_reason", "balance_after"}
    missing = required - set(record.keys())
    assert not missing, f"拒绝记录缺字段: {sorted(missing)}"
    assert record["status"] == "拒绝"
    reason = record["reject_reason"]
    assert isinstance(reason, str) and reason.strip(), "reject_reason 必须非空"
    assert any("\u4e00" <= ch <= "\u9fff" for ch in reason), "reject_reason 必须为中文"
    assert any(k.lower() in reason.lower() for k in reason_keywords), \
        f"拒绝原因[{reason}]未命中关键字 {reason_keywords}"
    assert record["balance_after"] == pytest.approx(cash_before, abs=0.005)
    assert acc["cash"] == pytest.approx(cash_before, abs=0.005)


# --------------------------------------------------------------------------
# ⑥ API 契约（requirements-r12 §四 增量接口 + requirements.md §五）
# --------------------------------------------------------------------------

@pytest.mark.skip(reason=SKIP_REASON)
def test_api_get_accounts_list():
    """GET /api/accounts：5 位分析师账户齐全（skill_id/cash/positions/realized_pnl/total_assets），
    响应恒含 disclaimer。"""
    client = _client()
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("disclaimer") == DISCLAIMER  # §六.7：响应恒含免责声明
    if "accounts" in data:
        accounts = data["accounts"]
    elif isinstance(data, list):
        accounts = data
    else:
        pytest.fail("GET /api/accounts 顶层结构未冻结，集成阶段对齐契约")
    by_id = {a["skill_id"]: a for a in accounts}
    assert set(by_id) >= set(ANALYSTS), f"缺少分析师账户: {set(ANALYSTS) - set(by_id)}"
    for skill_id in ANALYSTS:
        a = by_id[skill_id]
        for field in ("skill_id", "cash", "positions", "realized_pnl", "total_assets"):
            assert field in a, f"{skill_id} 账户缺字段 {field}"
        assert a["cash"] == pytest.approx(INITIAL_CASH, abs=0.005)


@pytest.mark.skip(reason=SKIP_REASON)
def test_api_get_account_detail():
    """GET /api/accounts/{skill_id}：现金/持仓/交易/快照/盈亏字段齐全，disclaimer 恒在。"""
    client = _client()
    resp = client.get("/api/accounts/alang")
    assert resp.status_code == 200
    a = resp.json()
    assert a["skill_id"] == "alang"
    assert a.get("disclaimer") == DISCLAIMER
    for field in ("cash", "positions", "trades", "snapshots", "realized_pnl", "total_assets"):
        assert field in a, f"账户详情缺字段 {field}"
    assert a["cash"] == pytest.approx(INITIAL_CASH, abs=0.005)
    assert not a["positions"], "新账户无持仓"
    assert a["trades"] == []


@pytest.mark.skip(reason=SKIP_REASON)
def test_api_unknown_skill_auto_opens():
    """R12-1：GET /api/accounts/{新 skill_id} → 首次访问自动开户（10 万、无持仓）。"""
    client = _client()
    resp = client.get(f"/api/accounts/{FUTURE_SKILL_ID}")
    assert resp.status_code == 200
    a = resp.json()
    assert a["skill_id"] == FUTURE_SKILL_ID
    assert a["cash"] == pytest.approx(INITIAL_CASH, abs=0.005)
    assert not a["positions"]


def _any_review_meta(client):
    """契约探针：取一份既有复盘报告 meta（无则 POST 创建并轮询 job）。"""
    resp = client.get("/api/reviews")
    assert resp.status_code == 200
    reviews = resp.json()
    for day in reviews:
        for item in day.get("items", []):
            detail = client.get(f"/api/reviews/{day['date']}/{item['time']}")
            if detail.status_code == 200:
                return detail.json()["meta"]
    # 无既有报告：创建一份（契约探针：集成阶段可换固定 fixture 报告）
    job = client.post("/api/reviews", json={"date": "2026-08-03", "mode": "close"}).json()
    job_id = job["job_id"]
    for _ in range(60):
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("done", "error"):
            break
    assert status["status"] == "done", "测试前置：需有一份可用复盘报告"
    return status["result"]["meta"]


@pytest.mark.skip(reason=SKIP_REASON)
def test_report_meta_accounts_is_extra_field():
    """Q7/§四.2：meta.accounts 为附加字段；冻结 meta 字段（date/mode/mode_label/time/
    created_at/degraded/disclaimer/summary）必须仍在。"""
    client = _client()
    meta = _any_review_meta(client)
    frozen = {"date", "mode", "mode_label", "time", "created_at", "degraded",
              "disclaimer", "summary"}
    assert frozen <= set(meta.keys()), f"meta 缺失冻结字段: {frozen - set(meta.keys())}"
    assert meta["disclaimer"] == DISCLAIMER
    assert "accounts" in meta, "meta.accounts 必须作为附加字段存在"
    accounts = meta["accounts"]
    ids = list(accounts.keys()) if isinstance(accounts, dict) else \
        [a["skill_id"] for a in accounts] if isinstance(accounts, list) else None
    assert ids is not None and set(ids) >= set(ANALYSTS), "meta.accounts 应含全部分析师"


@pytest.mark.skip(reason=SKIP_REASON)
def test_report_body_not_changed_by_accounts():
    """Q7/§四.2：报告 Markdown 正文不因账户系统改变（正文不含「模拟账户」区块，免责声明仍在）。"""
    client = _client()
    meta = _any_review_meta(client)
    detail = client.get(f"/api/reviews/{meta['date']}/{meta['time']}")
    assert detail.status_code == 200
    body = detail.json()["report"]
    assert isinstance(body, str) and body.strip()
    assert "模拟账户" not in body, "账户数据不得写入报告正文（R7 冻结）"
    assert (DISCLAIMER in body) or ("免责声明" in body), "报告正文必须保留免责声明"
