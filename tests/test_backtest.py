# -*- coding: utf-8 -*-
"""历史回测编排器测试（backend/backtest.py + routes_backtest.py）。

覆盖（docs/requirements-backtest-ui.md §四/§六/§八）：
- start → running → 逐日 done 的进度/耗时字段（stage/analyst/settle/review）；
- fail-fast：单日重试 3 次仍失败 → 中止、status=error、中文 error，绝不静默跳过；
- cancel：置标志位，当日完成后停，status=cancelled；
- 单 running 互斥：再次 start 返回 409 中文；
- 落盘后 history 可查（clear 内存后从磁盘重建）；
- run_review_day 与 _run_review_job 行为一致（复用 job 链路，结果契约不变）。

关键设计：mock graph/_run_review_day_impl/_settle_impl（不真调 LLM/数据源）。
数据根隔离由 conftest.py 的 autouse _isolated_test_data_dir 提供（每测试全新临时目录）。
"""

from __future__ import annotations

import json
import os
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.backtest as btest
import backend.main as bm
from backend.jobs import run_review_day

client = TestClient(bm.app)

# 分析师 id/name（与 skills/ 目录 + frontend stages 一致）
SKILLS = [("alang", "阿狼"), ("bingchuan", "爱在冰川"), ("tiechui", "铁锤"),
          ("yangjia", "炒股养家"), ("baxiaoxian", "拔小弦")]


# ────────────────────────── LLM / 取数 mock 夹具（对齐 test_backend） ──────────────────────────

def _llm_reply(messages):
    user = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            user = m.get("content", "")
    if "报告要求" in user:
        return "# 2026-08-10 A股收盘复盘报告\n## 一、市场概况\n略\n## 综合标签\n#测试"
    if "实质性分歧" in user:
        return "分歧判断：无\n总结：观点一致，无需辩论。"
    return ("## 1、昨日复盘验证\n无昨日报告，跳过验证\n"
            "## 2、今日判断（今日大盘判断）\n看多\n"
            "## 3、操作计划（明日计划）\n进攻")


class _FakeLLM:
    def __init__(self, factory):
        self._factory = factory

    def bind_tools(self, tools=None):
        return self

    def invoke(self, messages):
        return types.SimpleNamespace(content=self._factory(messages), tool_calls=[])


def _fake_mode_data(mode: str, date: str) -> dict:
    """极简 I1 契约快照（覆盖 close 复盘所需块）。"""
    return {
        "date": date,
        "mode": mode,
        "mode_label": bm.MODE_LABELS.get(mode, mode),
        "blocks": {
            "index_trend": {"indices": {"shanghai": {"close": 3764.0, "pct_change": 0.0052}},
                            "source": "测试源·指数", "degraded": False, "degraded_reason": []},
            "minute": {"symbol": "sh000001", "bars": [{"time": "09:30", "close": 3760.0}],
                       "source": "测试源·分时", "degraded": False},
            "sectors": {"top5": [{"板块": "半导体", "涨跌幅": 0.02}],
                        "bottom5": [], "source": "测试源·板块", "degraded": False},
            "zt_pool": {"limit_up": {"count": 45, "tier": {}},
                        "limit_down": {"count": 3, "tier": {}}, "zhaban_rate": 0.12,
                        "source": "测试源·涨跌停", "degraded": False},
            "breadth": {"up": 2800, "down": 1800, "limit_up": 45, "limit_down": 3,
                        "source": "测试源·涨跌分布"},
            "sentiment": {"up_count": 2800, "down_count": 1800, "red_rate": 0.6,
                          "avg_return": 0.01, "source": "测试源·情绪", "degraded": False},
            "news": {"caixin": [], "cctv": [], "source": "测试源·资讯"},
            "lhb": {"stocks": [], "source": "数据缺失", "degraded": True,
                    "degraded_reason": ["龙虎榜数据缺失（示例）"]},
        },
        "degraded": ["lhb: 龙虎榜数据缺失（示例）"],
        "degraded_flag": True,
    }


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """离线确定性：LLM 用 mock 可用（复盘终止契约要求 LLM 可用），数据取数全 mock。"""
    from stock_review_crew import graph
    from stock_review_crew import chat

    monkeypatch.setattr(graph, "_llm_ready", lambda: True)
    llm = _FakeLLM(_llm_reply)
    strict = _FakeLLM(_llm_reply)
    monkeypatch.setattr(graph, "_llm_pair", lambda: (llm, strict))
    monkeypatch.setattr(graph, "_callbacks", lambda: [])
    monkeypatch.setattr(graph, "_load_analyst_tools", lambda: (None, None))
    monkeypatch.setattr(graph, "_legacy_fetch", lambda state: None)
    monkeypatch.setattr(chat, "_get_default_llm", lambda: None)


@pytest.fixture()
def fake_fetch(monkeypatch):
    """引擎取数入口返回极简快照（禁止真实网络）。"""
    from stock_review_crew import graph

    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: _fake_mode_data)


@pytest.fixture(autouse=True)
def bt_isolate(monkeypatch):
    """重置编排器内存态 + 注入点（防跨测试残留污染）。"""
    with btest._lock:
        btest.BACKTESTS.clear()
    monkeypatch.setattr(btest, "_run_review_day_impl", btest.run_review_day)
    monkeypatch.setattr(btest, "_settle_impl", None)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)  # 加速重试测试
    yield
    with btest._lock:
        btest.BACKTESTS.clear()


def _fake_settle(prev, day):
    return {"filled": 2, "rejected": 1, "quotes_missing": [], "unsettled": [],
            "degraded": []}


# ────────────────────────── mock 产品写盘（让 validate_run_product 通过） ──────────────────────────

def _write_products(date_str: str, llm_degraded: bool = False) -> None:
    """按 run_review_day 落盘位置写 meta/report/plan，供 validate_run_product。"""
    from stock_review_crew.storage import reviews as reviews_store
    from stock_review_crew.accounts import plan as plan_store

    review_dir = reviews_store.reviews_root() / date_str
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "meta.json").write_text(json.dumps({
        "date": date_str, "mode": "close", "mode_label": "收盘复盘",
        "degraded": ["测试降级"] if llm_degraded else [],
        "sources": ["测试源"], "disclaimer": "仅供参考，不构成投资建议",
        "summary": "测试摘要",
    }, ensure_ascii=False), encoding="utf-8")
    (review_dir / "report.json").write_text(json.dumps({
        "final_report": "# 测试报告\n> 仅供参考，不构成投资建议",
        "analyses": [{"skill_id": s[0], "skill_name": s[1]} for s in SKILLS],
    }, ensure_ascii=False), encoding="utf-8")
    plan_p = plan_store.plan_path(date_str)  # {DATA_DIR}/reviews/{date}/plan.json
    plan_p.parent.mkdir(parents=True, exist_ok=True)
    plan_p.write_text(json.dumps({
        "date": date_str, "mode": "close",
        "plans": [{"rec_date": date_str, "skill_id": s[0], "skill_name": s[1],
                   "code": "600001", "side": "buy", "target_price": 10.0,
                   "position_cheng": 1, "qty": None} for s in SKILLS],
    }, ensure_ascii=False), encoding="utf-8")


def _stage_timings():
    now = time.monotonic()
    return {k: {"started_at": now, "elapsed_ms": 100} for k in ("fetch", "analysts", "host", "report")}


def _analyst_timings():
    return {sid: {"skill_id": sid, "name": name, "started_at": time.monotonic(), "elapsed_ms": 50}
            for sid, name in SKILLS}


def make_fake_day(produce=True, fail_on_attempts=None):
    """注入的假单日实现：模拟阶段/分析师进度事件；按「日期 → 失败尝试集合」抛错。

    - fail_on_attempts：{date_str: {attempt}}——该日期第 attempt 次尝试抛错。
    返回 (impl, calls)，calls 元素 {date, attempt, ok}。
    """
    fail_on_attempts = fail_on_attempts or {}
    calls: list[dict] = []

    def impl(date: str, mode: str, max_rounds: int, trace: bool, progress_cb=None):
        # 模拟阶段进度 + 分析师 started/finished（meta 事件）
        if progress_cb:
            for st, pct in (("fetch", 1), ("analysts", 20), ("host", 50), ("report", 85)):
                progress_cb(st, pct, st, None)
            for idx, (sid, name) in enumerate(SKILLS, 1):
                progress_cb("analyst", 20, f"{name} 开始", {
                    "type": "analyst", "event": "started", "skill_id": sid, "name": name})
                progress_cb("analyst", 20, f"{name} 完成", {
                    "type": "analyst", "event": "finished", "skill_id": sid,
                    "name": name, "elapsed_ms": 50})
        attempt = sum(1 for c in calls if c["date"] == date) + 1
        should_fail = attempt in (fail_on_attempts.get(date) or ())
        calls.append({"date": date, "attempt": attempt, "ok": not should_fail})
        if should_fail:
            raise RuntimeError("单日复盘模拟失败")
        if produce:
            _write_products(date)
        return {
            "meta": {"date": date},
            "report": "测试报告",
            "snapshot": {"date": date},
            "record_id": date,
            "quality": None,
            "stage_timings": _stage_timings(),
            "analyst_timings": _analyst_timings(),
        }

    return impl, calls


def _wait_backtest(bt_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/backtest/{bt_id}")
        assert r.status_code == 200, r.text
        data = r.json()
        if data["status"] in ("done", "error", "cancelled"):
            return data
        time.sleep(0.02)
    raise AssertionError(f"回测未在 {timeout}s 内结束：{r.text}")


def _start(start="2026-08-10", end="2026-08-12", **kw):
    payload = {"start": start, "end": end}
    payload.update(kw)
    r = client.post("/api/backtest/start", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["backtest_id"]


# ═══════════════════════════════════════════════════════════════
# 一、start → running → done 逐日进度/耗时字段
# ═══════════════════════════════════════════════════════════════

def test_backtest_full_done(monkeypatch):
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    impl, calls = make_fake_day()
    monkeypatch.setattr(btest, "_run_review_day_impl", impl)

    bt_id = _start("2026-08-10", "2026-08-12", mode="close", max_rounds=2)

    # 立即查：queued/running + days_total
    r0 = client.get(f"/api/backtest/{bt_id}")
    assert r0.status_code == 200
    data0 = r0.json()
    assert data0["status"] in ("queued", "running")
    assert data0["days_total"] == 3  # 8/10 周一, 8/11 周二, 8/12 周三
    assert [d["date"] for d in data0["days"]] == ["2026-08-10", "2026-08-11", "2026-08-12"]

    fin = _wait_backtest(bt_id)
    assert fin["status"] == "done"
    assert fin["days_done"] == 3
    assert fin["current"] is None
    assert fin["finished_at"]
    assert isinstance(fin["total_elapsed_ms"], int)

    for d in fin["days"]:
        assert d["status"] == "done"
        assert d["attempts"] == 1 and d["attempt"] == 1
        assert isinstance(d["elapsed_ms"], int) and d["elapsed_ms"] >= 0
        # review 阶段耗时
        assert d["review"]["elapsed_ms"] == 400
        # 4 个节点
        assert [s["key"] for s in d["stages"]] == ["fetch", "analysts", "host", "report"]
        for s in d["stages"]:
            assert s["elapsed_ms"] == 100
        # 分析师细分
        ana = next(s for s in d["stages"] if s["key"] == "analysts")["analysts"]
        assert len(ana) == 5
        assert {a["skill_id"] for a in ana} == {s[0] for s in SKILLS}
        for a in ana:
            assert a["elapsed_ms"] == 50
        assert d["error"] is None
    # 首日无 prev → settle 为 0；后续日才有 settl
    assert fin["days"][0]["settle"] == {"elapsed_ms": 0, "filled": 0, "rejected": 0,
                                        "quotes_missing": 0}
    for d in fin["days"][1:]:
        assert d["settle"]["filled"] == 2 and d["settle"]["rejected"] == 1
        assert d["settle"]["quotes_missing"] == 0
        assert d["settle"]["elapsed_ms"] >= 0
    assert len(calls) == 3
    # 每天 elapsed = review(实际耗时) + settle
    for d in fin["days"]:
        assert d["elapsed_ms"] >= d["settle"]["elapsed_ms"]


# ═══════════════════════════════════════════════════════════════
# 二、fail-fast：单日重试 3 次仍失败 → error + 中文，绝不静默跳过
# ═══════════════════════════════════════════════════════════════

def test_failfast_aborts_after_3_attempts(monkeypatch):
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    impl, calls = make_fake_day(fail_on_attempts={"2026-08-10": {1, 2, 3}})
    monkeypatch.setattr(btest, "_run_review_day_impl", impl)

    bt_id = _start("2026-08-10", "2026-08-12")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "error"
    day0 = next(d for d in fin["days"] if d["date"] == "2026-08-10")
    assert day0["status"] == "failed"
    assert day0["attempts"] == 3
    assert day0["error"] and "复盘" in day0["error"]
    # 首日即 fail-fast → 后续日期绝不执行
    assert all(d["status"] == "pending" for d in fin["days"][1:])
    assert len([c for c in calls if c["date"] == "2026-08-10"]) == 3
    assert all(not c["ok"] for c in calls)


def test_failfast_second_day_aborts(monkeypatch):
    """第 1 天成功、第 2 天失败 → 第 2 天后中止，第 3 天不跑。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    impl, calls = make_fake_day(fail_on_attempts={"2026-08-11": {1, 2, 3}})
    monkeypatch.setattr(btest, "_run_review_day_impl", impl)

    bt_id = _start("2026-08-10", "2026-08-12")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "error"
    assert fin["days"][0]["status"] == "done"
    assert fin["days"][1]["status"] == "failed"
    assert fin["days"][1]["attempts"] == 3
    assert fin["days"][2]["status"] == "pending"
    assert {c["date"] for c in calls} == {"2026-08-10", "2026-08-11"}


# ═══════════════════════════════════════════════════════════════
# 三、cancel：置标志位，当日完成后停，status=cancelled
# ═══════════════════════════════════════════════════════════════

def test_cancel_stops_after_current_day(monkeypatch):
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    base_impl, _calls = make_fake_day()

    def blocking_impl(date, mode, max_rounds, trace, progress_cb=None):
        time.sleep(0.4)  # 首日较长，期间可 cancel
        return base_impl(date, mode, max_rounds, trace, progress_cb)

    monkeypatch.setattr(btest, "_run_review_day_impl", blocking_impl)
    bt_id = _start("2026-08-10", "2026-08-14")

    # 等首日进入 running 后 cancel
    deadline = time.monotonic() + 5
    cancelled = False
    while time.monotonic() < deadline:
        d = client.get(f"/api/backtest/{bt_id}").json()
        if d["status"] == "running" and d["days"][0]["status"] == "running":
            rc = client.post(f"/api/backtest/{bt_id}/cancel")
            assert rc.status_code == 200
            assert rc.json() == {"ok": True}
            cancelled = True
            break
        time.sleep(0.02)
    assert cancelled, "未能捕获 running 态以 cancel"

    fin = _wait_backtest(bt_id)
    assert fin["status"] == "cancelled"
    assert fin["days"][0]["status"] == "done"  # cancel 前当日已完成
    assert all(d["status"] == "pending" for d in fin["days"][1:])


def test_cancel_terminal_409(monkeypatch):
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "_run_review_day_impl", make_fake_day()[0])
    bt_id = _start("2026-08-10", "2026-08-10")
    _wait_backtest(bt_id)  # done
    rc = client.post(f"/api/backtest/{bt_id}/cancel")
    assert rc.status_code == 409
    assert "回测已结束" in rc.json()["detail"]


# ═══════════════════════════════════════════════════════════════
# 四、单 running 互斥 + 非法参数
# ═══════════════════════════════════════════════════════════════

def test_single_running_mutex(monkeypatch):
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)

    def slow_impl(date, mode, max_rounds, trace, progress_cb=None):
        time.sleep(2.0)  # 保持 running 足够久
        return make_fake_day()[0](date, mode, max_rounds, trace, progress_cb)

    monkeypatch.setattr(btest, "_run_review_day_impl", slow_impl)
    bt_id = _start("2026-08-10", "2026-08-11")
    r2 = client.post("/api/backtest/start", json={"start": "2026-08-10", "end": "2026-08-11"})
    assert r2.status_code == 409
    assert "进行中" in r2.json()["detail"]


def test_start_invalid_params(monkeypatch):
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    # start > end
    r = client.post("/api/backtest/start", json={"start": "2026-08-12", "end": "2026-08-10"})
    assert r.status_code == 400
    # 非法日期
    r = client.post("/api/backtest/start", json={"start": "2026/08/10", "end": "2026-08-10"})
    assert r.status_code == 400
    # 无交易日（纯周末）
    r = client.post("/api/backtest/start", json={"start": "2026-08-15", "end": "2026-08-16"})
    assert r.status_code == 400
    assert "交易日" in r.json()["detail"]
    # 无效模式
    r = client.post("/api/backtest/start", json={"start": "2026-08-10", "end": "2026-08-10",
                                                 "mode": "nope"})
    assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════
# 五、落盘 + history 可查（内存清空后从磁盘重建）
# ═══════════════════════════════════════════════════════════════

def test_persistence_and_history(monkeypatch):
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "_run_review_day_impl", make_fake_day()[0])
    bt_id = _start("2026-08-10", "2026-08-10")
    _wait_backtest(bt_id)

    rdir = Path(os.environ["STOCK_REVIEW_DATA_DIR"]) / "backtests" / bt_id
    assert (rdir / "manifest.json").exists()
    assert (rdir / "progress.json").exists()

    # 清空内存模拟重启 → history 仍可查 + GET 完整对象从磁盘重建
    with btest._lock:
        btest.BACKTESTS.clear()
    rh = client.get("/api/backtest/history")
    assert rh.status_code == 200
    runs = rh.json()["runs"]
    assert any(x["id"] == bt_id for x in runs)
    hit = next(x for x in runs if x["id"] == bt_id)
    assert hit["status"] == "done"
    assert hit["days_total"] == 1
    detail = client.get(f"/api/backtest/{bt_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "done"
    # 磁盘重建也保留每天明细
    assert len(detail.json()["days"]) == 1
    assert detail.json()["days"][0]["status"] == "done"


# ═══════════════════════════════════════════════════════════════
# 六、run_review_day 与 _run_review_job 行为一致（result 契约）
# ═══════════════════════════════════════════════════════════════

def test_run_review_day_matches_job_result(fake_fetch):
    """跑真实 _run_review_job 复盘（复用后端单日链路），断言 run_review_day 产物契约不回归。"""
    r = client.post("/api/reviews", json={"date": "2026-08-10", "mode": "close", "max_rounds": 2})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    deadline = time.monotonic() + 30
    job = None
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert job and job["status"] == "done", (job or {}).get("error")
    res = job["result"]
    assert res["record_id"] == "2026-08-10"
    meta = res["meta"]
    for k in ("date", "mode", "mode_label", "time", "created_at", "degraded", "sources",
              "disclaimer", "summary"):
        assert k in meta
    assert meta["mode_label"] == "收盘复盘"
    assert res["quality"] is not None or "quality" in res
    from stock_review_crew.accounts import plan as plan_store

    assert plan_store.plan_path("2026-08-10").exists()


def test_run_review_day_direct_returns_timing(fake_fetch):
    """直接调用 run_review_day 返回 stage_timings / analyst_timings（引擎真实 _emit meta 事件）。"""
    from stock_review_crew.accounts import plan as plan_store

    plan_store.reset("2026-08-10")
    res = run_review_day("2026-08-10", "close", 2, trace=False)
    assert res["record_id"] == "2026-08-10"
    # 阶段耗时 4 节点全有、elapsed_ms >= 0
    assert set(res["stage_timings"].keys()) >= {"fetch", "analysts", "host", "report"}
    for v in res["stage_timings"].values():
        assert v.get("elapsed_ms") is not None and v["elapsed_ms"] >= 0
    # 分析师粒度耗时（引擎分析师线程 copy_context 后经 _emit meta 事件上报）
    at = res["analyst_timings"]
    assert set(at.keys()) == {s[0] for s in SKILLS}
    for sid, v in at.items():
        assert v.get("elapsed_ms") is not None and v["elapsed_ms"] >= 0
        assert v.get("name")
        assert v.get("skill_id") == sid  # 值内嵌 skill_id，供 backtest _fill_analyst_gauge 排序


# ═══════════════════════════════════════════════════════════════
# 七、账户重置（方案 A）：回测前清空 data/accounts/（回到 10 万）
# ═══════════════════════════════════════════════════════════════

def test_start_backtest_resets_accounts(monkeypatch):
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "_run_review_day_impl", make_fake_day()[0])

    from stock_review_crew.accounts.storage import accounts_root

    root = accounts_root()
    (root / "alang").mkdir(parents=True, exist_ok=True)
    (root / "alang" / "account.json").write_text("{}", encoding="utf-8")
    assert (root / "alang").is_dir()

    bt_id = _start("2026-08-10", "2026-08-10")
    _wait_backtest(bt_id)

    # 回测启动已清空 accounts/（重置到 10 万初始资金）
    assert not (root / "alang").exists()
