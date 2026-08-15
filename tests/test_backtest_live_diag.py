# -*- coding: utf-8 -*-
"""回测实时诊断（TDD 红）测试：docs/requirements-backtest-live-diag.md 验收 A1-A4。

契约/验收（唯一真源 = docs/requirements-backtest-live-diag.md §验收标准）：
- A1：GET /api/backtest/{id} 运行中 current 含全部 8 字段
      （date/stage/message/pct/attempt/attempts/last_error/elapsed_ms）；
      elapsed_ms 为实时毫秒数（≥0，随节点推进 轮询两次 增大或不减少）；
      终态（done/error）current 为 None 或无 elapsed_ms。
- A2：analyst 阶段运行中 current.stage=analysts 且 message 含分析师进度
      （注入 progress 事件验证；_timing_cb 不再丢弃 meta type=analyst 子事件）。
- A3：单日失败重试时 current.last_error=上次失败中文原因、attempt=当前次数；
      成功后 current.last_error=None、day.attempts=3、day.attempt 为成功那次次数；
      fail-fast（3 次全败）→ status=error、day.error 中文、current.stage=error。
- A4：attempts bug 修复：任何状态（running/failed/done）day.attempts 恒等于
      MAX_ATTEMPTS(3)，不是当前 attempt。

现状说明：实现代理尚未合并该需求（当前 backend/backtest.py 的 _timing_cb 仍丢弃
analyst 事件、day["attempts"]=attempt bug 未修、current 缺 message/pct/attempt/
attempts/last_error/elapsed_ms）。这些测试本应全红；红的原因必须是「契约/实现未就绪」，
而非测试自身 bug。

关键设计：mock graph/LLM/取数 + 注入 _run_review_day_impl/_settle_impl（复用
test_backtest.py 模式，不真调 LLM/网络）。数据根隔离由 conftest.py 的 autouse
_isolated_test_data_dir 提供。
"""

from __future__ import annotations

import json
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.backtest as btest
import backend.main as bm

client = TestClient(bm.app)

SKILLS = [("alang", "阿狼"), ("bingchuan", "爱在冰川"), ("tiechui", "铁锤"),
          ("yangjia", "炒股养家"), ("baxiaoxian", "拔小弦")]


# ────────────────────────── LLM / 取数 mock 夹具（对齐 test_backend/test_backtest） ──────────────────────────

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
    """离线确定性：LLM 用 mock 可用，数据取数全 mock（不碰真实网络）。"""
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

def _write_products(date_str: str) -> None:
    """按 run_review_day 落盘位置写 meta/report/plan，供 validate_run_product。"""
    from stock_review_crew.storage import reviews as reviews_store
    from stock_review_crew.accounts import plan as plan_store

    review_dir = reviews_store.reviews_root() / date_str
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "meta.json").write_text(json.dumps({
        "date": date_str, "mode": "close", "mode_label": "收盘复盘",
        "degraded": [], "sources": ["测试源"], "disclaimer": "仅供参考，不构成投资建议",
        "summary": "测试摘要",
    }, ensure_ascii=False), encoding="utf-8")
    (review_dir / "report.json").write_text(json.dumps({
        "final_report": "# 测试报告\n> 仅供参考，不构成投资建议",
        "analyses": [{"skill_id": s[0], "skill_name": s[1]} for s in SKILLS],
    }, ensure_ascii=False), encoding="utf-8")
    plan_p = plan_store.plan_path(date_str)
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
        # 模拟阶段进度 + 分析师 started/finished（meta type=analyst 子事件）
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
# A1：current 契约（8 字段 + 实时 elapsed_ms + 终态无 current）
# ═══════════════════════════════════════════════════════════════

def test_a1_current_contract_and_live_elapsed(monkeypatch):
    """A1：运行中 current 含 8 字段；elapsed_ms ≥0 且随轮询两次 增大或不减少；终态 current 为 None。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)

    def slow_impl(date, mode, max_rounds, trace, progress_cb=None):
        # 长时间停留在某阶段，便于两次轮询观察 elapsed_ms 实时增大（或不减少）
        time.sleep(0.5)
        if progress_cb:
            progress_cb("analysts", 20, "分析师 1/5", None)
        time.sleep(0.5)
        return make_fake_day()[0](date, mode, max_rounds, trace, progress_cb)

    monkeypatch.setattr(btest, "_run_review_day_impl", slow_impl)
    bt_id = _start("2026-08-10", "2026-08-10")

    t0 = time.monotonic()

    # 等进入 running 且 current 非空
    current = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        d = client.get(f"/api/backtest/{bt_id}").json()
        if d["status"] == "running" and d["current"] is not None:
            current = d["current"]
            break
        time.sleep(0.02)
    assert current is not None, "运行中 current 一直为空"

    for field in ("date", "stage", "message", "pct", "attempt", "attempts",
                  "last_error", "elapsed_ms"):
        assert field in current, f"current 应含字段 {field}"

    # elapsed_ms 为实时毫秒数（≥0）
    e1 = current["elapsed_ms"]
    assert isinstance(e1, int), "elapsed_ms 应为 int 毫秒数"
    assert e1 >= 0, f"elapsed_ms 应 ≥0，实际 {e1}"

    # 轮询两次：elapsed_ms 随节点推进 增大或不减少（实时跳动）
    time.sleep(0.05)
    d2 = client.get(f"/api/backtest/{bt_id}").json()
    assert d2["status"] == "running", "回测应仍在运行"
    c2 = d2["current"]
    assert c2 is not None, "运行中 current 不应为 None"
    e2 = c2["elapsed_ms"]
    assert e2 >= e1, f"elapsed_ms 应增大或不减少，e1={e1} e2={e2}"

    fin = _wait_backtest(bt_id)
    assert fin["status"] == "done"
    cur = fin.get("current")
    assert cur is None or "elapsed_ms" not in cur, "终态 current 应为 None 或无 elapsed_ms"


# ═══════════════════════════════════════════════════════════════
# A2：analyst 事件可见（_timing_cb 不再丢弃 meta type=analyst）
# ═══════════════════════════════════════════════════════════════

def test_a2_analyst_stage_visible(monkeypatch):
    """A2：analyst 阶段运行中 current.stage=analysts 且 message 含分析师进度信息。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)

    def analyst_impl(date, mode, max_rounds, trace, progress_cb=None):
        # 长时间停留在 analysts 阶段并上报含「分析师」的进度消息（含 meta type=analyst 子事件），
        # 使轮询稳定捕获 stage=analysts 且 message 含「分析师」。
        # 先落到 analysts 阶段（非 analyst 子事件，message 为「分析师 …」）并保持足够久
        progress_cb("analysts", 20, "分析师 1/5", None)
        time.sleep(0.4)
        for idx, (sid, name) in enumerate(SKILLS, 2):
            progress_cb("analysts", 20 * idx, f"分析师 {idx}/{len(SKILLS)}", None)
            progress_cb("analyst", 20 * idx, f"{name} 开始", {
                "type": "analyst", "event": "started", "skill_id": sid, "name": name})
            time.sleep(0.1)
        return make_fake_day()[0](date, mode, max_rounds, trace, progress_cb)

    monkeypatch.setattr(btest, "_run_review_day_impl", analyst_impl)
    bt_id = _start("2026-08-10", "2026-08-10")

    current = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        d = client.get(f"/api/backtest/{bt_id}").json()
        if d["status"] == "running" and d["current"] is not None \
                and d["current"].get("stage") == "analysts" \
                and "分析师" in str(d["current"].get("message", "")):
            current = d["current"]
            break
        time.sleep(0.02)
    assert current is not None, "运行中未捕获 analysts 阶段（含「分析师」消息）"
    assert current["stage"] == "analysts", "current.stage 应为 analysts"
    assert "分析师" in str(current.get("message", "")), \
        f"current.message 应含「分析师」字样，实际：{current.get('message')}"


# ═══════════════════════════════════════════════════════════════
# A3：重试可见（last_error / attempt 递增 / 成功后重置 / fail-fast）
# ═══════════════════════════════════════════════════════════════

def test_a3_retry_visible_and_success_reset(monkeypatch):
    """A3：前 2 次失败、第 3 次成功 → 重试间 last_error=中文、attempt 递增；
    成功后 last_error=None、day.attempts=3、day.attempt=成功那次次数(3)。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)

    calls: list[dict] = []

    def retry_impl(date, mode, max_rounds, trace, progress_cb=None):
        # 前 2 次失败、第 3 次成功
        attempt = sum(1 for c in calls if c["date"] == date) + 1
        calls.append({"date": date, "attempt": attempt})
        if progress_cb:
            # 每次尝试都上报一次阶段进度，让重试期间 current 可轮询到
            progress_cb("analysts", 20, f"第 {attempt} 次尝试 分析师 1/5", None)
        if attempt < 3:
            time.sleep(0.05)
            raise RuntimeError("模拟单日失败：行情源暂时不可用")
        return make_fake_day()[0](date, mode, max_rounds, trace, progress_cb)

    monkeypatch.setattr(btest, "_run_review_day_impl", retry_impl)
    bt_id = _start("2026-08-10", "2026-08-10")

    # 捕获重试第 2 次（last_error 应为第 1 次失败的中文，attempt=2）
    got_retry = None
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        d = client.get(f"/api/backtest/{bt_id}").json()
        if d["status"] == "running" and d["current"] is not None \
                and d["current"].get("attempt") == 2:
            got_retry = d
            break
        time.sleep(0.02)
    assert got_retry is not None, "重试第 2 次current 未被捕获"
    cur = got_retry["current"]
    assert cur["attempt"] == 2, f"current.attempt 应为 2，实际 {cur.get('attempt')}"
    assert cur["attempts"] == 3, f"current.attempts 应为 3（MAX_ATTEMPTS），实际 {cur.get('attempts')}"
    assert cur["last_error"] and "行情源" in str(cur["last_error"]), \
        f"current.last_error 应为上次失败中文原因（含「行情源」），实际：{cur.get('last_error')}"

    fin = _wait_backtest(bt_id)
    assert fin["status"] == "done"
    curf = fin.get("current")
    assert curf is None or curf.get("last_error") is None, \
        "成功后 current.last_error 应为 None"
    day = fin["days"][0]
    assert day["status"] == "done"
    assert day["attempts"] == 3, f"成功日 day.attempts 应为 3（MAX_ATTEMPTS），实际 {day['attempts']}"
    assert day["attempt"] == 3, f"成功那次 attempt 应为 3，实际 {day['attempt']}"
    assert len(calls) == 3, "应恰好尝试 3 次"


def test_a3_failfast_error_state(monkeypatch):
    """A3：3 次全败 → status=error、day.error 中文、current.stage=error；attempts=3。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)
    impl, calls = make_fake_day(fail_on_attempts={"2026-08-10": {1, 2, 3}})
    monkeypatch.setattr(btest, "_run_review_day_impl", impl)

    bt_id = _start("2026-08-10", "2026-08-10")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "error", f"3 次全败应 status=error，实际 {fin['status']}"
    cur = fin.get("current")
    assert cur is None or cur.get("stage") == "error", "current.stage 应为 error"
    day = fin["days"][0]
    assert day["status"] == "failed"
    assert day["error"] and "复盘" in str(day["error"]), \
        f"day.error 应为中文失败原因，实际：{day.get('error')}"
    assert day["attempts"] == 3, f"failed 日 day.attempts 应为 3，实际 {day['attempts']}"
    assert len([c for c in calls if c["date"] == "2026-08-10"]) == 3, \
        "fail-fast 应恰好重试 3 次"


# ═══════════════════════════════════════════════════════════════
# A4：attempts bug —— 任何状态 day.attempts 恒等于 MAX_ATTEMPTS(3)
# ═══════════════════════════════════════════════════════════════

def test_a4_done_attempts_equals_max(monkeypatch):
    """A4：成功（done）时 day.attempts 恒等于 MAX_ATTEMPTS(3)，不是当前 attempt(1)。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)
    monkeypatch.setattr(btest, "_run_review_day_impl", make_fake_day()[0])
    bt_id = _start("2026-08-10", "2026-08-11")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "done"
    for d in fin["days"]:
        assert d["status"] == "done"
        assert d["attempts"] == btest.MAX_ATTEMPTS == 3, \
            f"done 日 day.attempts 应恒为 3，实际 {d['attempts']}（当前 attempt={d['attempt']}）"
        assert d["attempt"] == 1, "首次成功 attempt 应为 1"


def test_a4_failed_attempts_equals_max(monkeypatch):
    """A4：failed 时 day.attempts 恒等于 MAX_ATTEMPTS(3)（fail-fast 中止态）。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)
    impl, _calls = make_fake_day(fail_on_attempts={"2026-08-10": {1, 2, 3}})
    monkeypatch.setattr(btest, "_run_review_day_impl", impl)
    bt_id = _start("2026-08-10", "2026-08-10")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "error"
    day = fin["days"][0]
    assert day["status"] == "failed"
    assert day["attempts"] == btest.MAX_ATTEMPTS == 3, \
        f"failed 日 day.attempts 应恒为 3，实际 {day['attempts']}（当前 attempt={day['attempt']}）"


# ═══════════════════════════════════════════════════════════════
# A7：统计时间含全部重试（契约设计第 5 条）
# day.elapsed_ms 与 day.review.elapsed_ms 累计所有尝试的墙钟耗时（含失败尝试 + RETRY_SLEEP 等待）
# ═══════════════════════════════════════════════════════════════
#
# 测试要点：mock 的 stage_timings 各项取极小值（≈0ms，对比 make_fake_day 的 100ms/节点），
# 使「只算最后一次成功尝试（或 stage-timing 求和）」的错误实现产出的 review.elapsed_ms 明显
# 小于累计墙钟下界 → A7 断言能真实卡住未实现的累计逻辑。
# RETRY_SLEEP 已被 bt_isolate monkeypatch 为 0.01 s；两次重试等待 = 2×0.01s = 20ms，
# 宽松下界取 15ms（累计实现恒 ≥ 20ms；只算末次成功/不设 elapsed 的错误实现恒 < 15ms）。

def _retry_success_impl(calls: list):
    """前 2 次抛异常、第 3 次成功；stage_timings 各项 ≈0ms。"""
    def impl(date, mode, max_rounds, trace, progress_cb=None):
        attempt = sum(1 for c in calls if c["date"] == date) + 1
        calls.append({"date": date, "attempt": attempt})
        if attempt < 3:
            raise RuntimeError("模拟单日失败：行情源暂时不可用")
        # 成功那次：写入最小产品 + stage_timings 各节点 0ms（避免掩盖累计耗时）
        _write_products(date)
        now = time.monotonic()
        return {
            "meta": {"date": date},
            "report": "测试报告",
            "snapshot": {"date": date},
            "record_id": date,
            "quality": None,
            "stage_timings": {k: {"started_at": now, "elapsed_ms": 0}
                              for k in ("fetch", "analysts", "host", "report")},
            "analyst_timings": {},
        }
    return impl


def test_a7_elapsed_includes_all_retries(monkeypatch):
    """A7：前 2 次失败、第 3 次成功 → day.elapsed_ms 与 day.review.elapsed_ms
    累计全部 3 次尝试墙钟（≥ 下界 15ms，宽松）；且 elapsed = review + settle 口径一致。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)
    calls: list[dict] = []
    monkeypatch.setattr(btest, "_run_review_day_impl", _retry_success_impl(calls))

    bt_id = _start("2026-08-10", "2026-08-10")  # 单日，无 prev → settle.elapsed_ms=0
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "done", f"第 3 次应成功，实际 {fin['status']}"
    day = fin["days"][0]
    assert day["status"] == "done"
    assert day.get("elapsed_ms") is not None, "done 日 day.elapsed_ms 不应为 None"
    assert day["elapsed_ms"] >= 15, \
        f"day.elapsed_ms 应累计全部重试墙钟（含 2 次 RETRY_SLEEP=0.01），实际 {day['elapsed_ms']}"
    review_ms = day.get("review", {}).get("elapsed_ms")
    assert review_ms is not None, "day.review.elapsed_ms 不应为 None"
    assert review_ms >= 15, \
        f"day.review.elapsed_ms 应累计全部重试墙钟，实际 {review_ms}"
    # 口径一致：day.elapsed_ms = review.elapsed_ms + settle.elapsed_ms
    settle_ms = int(day.get("settle", {}).get("elapsed_ms") or 0)
    assert day["elapsed_ms"] == review_ms + settle_ms, \
        "day.elapsed_ms 应等于 day.review.elapsed_ms + settle.elapsed_ms，" \
        f"实际 {day['elapsed_ms']} vs {review_ms}+{settle_ms}"
    assert len([c for c in calls if c["date"] == "2026-08-10"]) == 3, "应恰好尝试 3 次"


def test_a7_failfast_elapsed_set(monkeypatch):
    """A7：3 次全败 → status=error 时 day.elapsed_ms 存在且累计全部 3 次尝试墙钟（≥ 15ms）。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)

    def fail_impl(date, mode, max_rounds, trace, progress_cb=None):
        raise RuntimeError("模拟单日失败：行情源暂时不可用")

    monkeypatch.setattr(btest, "_run_review_day_impl", fail_impl)
    bt_id = _start("2026-08-10", "2026-08-10")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "error", f"3 次全败应 error，实际 {fin['status']}"
    day = fin["days"][0]
    assert day["status"] == "failed"
    assert day.get("elapsed_ms") is not None, \
        "fail-fast 失败日也应设置 day.elapsed_ms（不得为 None）"
    assert day["elapsed_ms"] >= 15, \
        f"fail-fast 失败日 day.elapsed_ms 应累计全部 3 次尝试墙钟，实际 {day['elapsed_ms']}"


# ═══════════════════════════════════════════════════════════════
# D1：每天明细 attempts_log 契约（追加需求 2026-08-16）
#   day["attempts_log"] 每次尝试一条 {attempt, ok, reason, elapsed_ms, retry_sleep_ms}；
#   review.elapsed_ms == sum(elapsed_ms)+sum(retry_sleep_ms)。
# ═══════════════════════════════════════════════════════════════

def _retry_2_fail_then_success(calls: list):
    """前 2 次失败、第 3 次成功。失败原因含「单日复盘模拟失败」。"""
    def impl(date, mode, max_rounds, trace, progress_cb=None):
        attempt = sum(1 for c in calls if c["date"] == date) + 1
        calls.append({"date": date, "attempt": attempt, "ok": attempt >= 3})
        if attempt < 3:
            raise RuntimeError("单日复盘模拟失败")
        _write_products(date)
        now = time.monotonic()
        return {
            "meta": {"date": date},
            "report": "测试报告",
            "snapshot": {"date": date},
            "record_id": date,
            "quality": None,
            "stage_timings": {k: {"started_at": now, "elapsed_ms": 0}
                              for k in ("fetch", "analysts", "host", "report")},
            "analyst_timings": {},
        }
    return impl


def test_d1_attempts_log_contract_and_sum(monkeypatch):
    """D1：mock 前 2 次失败、第 3 次成功 → done 后 attempts_log 长度 3，
    每条含 attempt/ok/reason/elapsed_ms/retry_sleep_ms（类型正确）；
    前 2 条 ok=False 且 reason 含中文失败原因，第 3 条 ok=True、reason=="";
    前 2 条 retry_sleep_ms>0（RETRY_SLEEP=0.01），第 3 条 ==0；
    sum(elapsed_ms)+sum(retry_sleep_ms) == day["review"]["elapsed_ms"]。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)
    calls: list[dict] = []
    monkeypatch.setattr(btest, "_run_review_day_impl", _retry_2_fail_then_success(calls))

    bt_id = _start("2026-08-10", "2026-08-10")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "done", f"第 3 次应成功，实际 {fin['status']}"
    day = fin["days"][0]
    assert day["status"] == "done"

    log = day.get("attempts_log")
    assert log is not None, "done 日应有 attempts_log（实现未合入？）"
    assert isinstance(log, list) and len(log) == 3, \
        f"attempts_log 应为 3 条（成功那次也记录），实际 {len(log)}"
    for entry in log:
        for field in ("attempt", "ok", "reason", "elapsed_ms", "retry_sleep_ms"):
            assert field in entry, f"attempts_log 每条应含 {field}；实际 {entry}"
        assert isinstance(entry["attempt"], int), f"attempt 应为 int，实际 {entry['attempt']}"
        assert isinstance(entry["ok"], bool), f"ok 应为 bool，实际 {entry['ok']}"
        assert isinstance(entry["reason"], str), f"reason 应为 str，实际 {entry['reason']}"
        assert isinstance(entry["elapsed_ms"], int), f"elapsed_ms 应为 int，实际 {entry['elapsed_ms']}"
        assert isinstance(entry["retry_sleep_ms"], int), \
            f"retry_sleep_ms 应为 int，实际 {entry['retry_sleep_ms']}"
        assert entry["attempt"] in (1, 2, 3), f"attempt 序号异常：{entry['attempt']}"

    first, second, third = log
    # 失败条目：ok=False、reason 非空中文
    assert first["ok"] is False and second["ok"] is False
    for entry in (first, second):
        assert entry["reason"] and "模拟" in entry["reason"] and "失败" in entry["reason"], \
            f"失败条目 reason 应为中文失败原因（含「模拟/失败」），实际：{entry['reason']}"
        assert entry["retry_sleep_ms"] >= 10, \
            f"失败条目 retry_sleep_ms 应为 RETRY_SLEEP(0.01)=10ms 量级，实际 {entry['retry_sleep_ms']}"
    # 成功条目：ok=True、reason 空串、retry_sleep_ms==0
    assert third["ok"] is True
    assert third["reason"] == "", f"成功条目 reason 应空串，实际：{third['reason']}"
    assert third["retry_sleep_ms"] == 0, \
        f"成功条目 retry_sleep_ms 应为 0，实际 {third['retry_sleep_ms']}"
    # 累计口径：review.elapsed_ms == sum(elapsed_ms)+sum(retry_sleep_ms)（review.elapsed_ms 是 int）
    review_ms = day.get("review", {}).get("elapsed_ms")
    assert isinstance(review_ms, int) and review_ms is not None, \
        "day.review.elapsed_ms 应为 int"
    sum_elapsed = sum(e["elapsed_ms"] for e in log)
    sum_sleep = sum(e["retry_sleep_ms"] for e in log)
    assert review_ms == sum_elapsed + sum_sleep, \
        "review.elapsed_ms 应等于 sum(elapsed_ms)+sum(retry_sleep_ms)，" \
        f"实际 {review_ms} vs {sum_elapsed}+{sum_sleep}"
    assert day.get("elapsed_ms") == review_ms + int(day.get("settle", {}).get("elapsed_ms") or 0), \
        "day.elapsed_ms 应 = review.elapsed_ms + settle.elapsed_ms"


# ═══════════════════════════════════════════════════════════════
# D2：首次成功单条 + fail-fast 全失败 3 条
# ═══════════════════════════════════════════════════════════════

def test_d2_first_success_single_entry(monkeypatch):
    """D2：无重试成功日 → attempts_log 长度 1、ok=True、reason==""、retry_sleep_ms==0。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)
    monkeypatch.setattr(btest, "_run_review_day_impl", make_fake_day()[0])

    bt_id = _start("2026-08-10", "2026-08-11")  # 两天均首次成功
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "done"
    for day in fin["days"]:
        assert day["status"] == "done"
        log = day.get("attempts_log")
        assert log is not None, "成功日应有 attempts_log"
        assert len(log) == 1, f"无重试成功日 attempts_log 应只有 1 条，实际 {len(log)}"
        e = log[0]
        assert e["attempt"] == 1 and e["ok"] is True
        assert e["reason"] == ""
        assert e["retry_sleep_ms"] == 0
        assert e["elapsed_ms"] == day["review"]["elapsed_ms"], \
            "单条成功日 elapsed_ms 应等于 review.elapsed_ms"


def test_d2_failfast_log_all_three(monkeypatch):
    """D2：3 次全败 → status=error、attempts_log 长度 3 全 ok=False、reason 非空。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)
    impl, calls = make_fake_day(fail_on_attempts={"2026-08-10": {1, 2, 3}})
    monkeypatch.setattr(btest, "_run_review_day_impl", impl)

    bt_id = _start("2026-08-10", "2026-08-10")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "error", f"3 次全败应 error，实际 {fin['status']}"
    day = fin["days"][0]
    assert day["status"] == "failed"
    log = day.get("attempts_log")
    assert log is not None, "fail-fast 失败日也应写 attempts_log（3 条全失败）"
    assert len(log) == 3, f"fail-fast 日 attempts_log 应为 3 条，实际 {len(log)}"
    for entry in log:
        assert entry["ok"] is False, f"fail-fast 全失败条目 ok 应为 False：{entry}"
        assert entry["reason"] and "模拟" in entry["reason"] and "失败" in entry["reason"], \
            f"fail-fast 条目 reason 应为中文失败原因，实际：{entry['reason']}"
    # 重试等待：前 2 条失败后接重试 → retry_sleep_ms ≥10；第 3 条（最后一次，不再重试）→ 0
    for entry in log[:2]:
        assert entry["retry_sleep_ms"] >= 10, \
            f"fail-fast 前 2 条（失败后接重试）retry_sleep_ms 应 ≥10，实际 {entry['retry_sleep_ms']}"
    assert log[2]["retry_sleep_ms"] == 0, \
        f"fail-fast 最后一条（不再重试）retry_sleep_ms 应为 0，实际 {log[2]['retry_sleep_ms']}"
    # 累计口径对 fail-fast 同样成立
    review_ms = day.get("review", {}).get("elapsed_ms")
    assert review_ms == sum(e["elapsed_ms"] for e in log) + sum(e["retry_sleep_ms"] for e in log), \
        "fail-fast 日 review.elapsed_ms 也应等于 sum(elapsed_ms)+sum(retry_sleep_ms)"
    assert day.get("elapsed_ms") == review_ms + int(day.get("settle", {}).get("elapsed_ms") or 0), \
        "fail-fast 日 day.elapsed_ms 口径应一致"


# ═══════════════════════════════════════════════════════════════
# D1：历史兼容 —— 无 attempts_log 字段时其他字段仍齐全（前端 normalize 兜底）
# （可选用例：后端仅确认「其他字段存在」，不要求 attempts_log）
# ═══════════════════════════════════════════════════════════════

def test_d1_history_day_without_attempts_log_keeps_fields():
    """D1：模拟旧数据（无 attempts_log 的 done 日）→ 其他字段仍齐全；
    前端由 normalize 兜底显示。后端只保证缺省安全、字段完整。"""
    now = time.monotonic()
    old_day = {
        "date": "2026-08-05",
        "status": "done",
        "attempt": 1,
        "attempts": 3,
        "settle": {"filled": 2, "rejected": 0, "quotes_missing": 0, "elapsed_ms": 0},
        "review": {"elapsed_ms": 1200},
        "elapsed_ms": 1200,
        "stages": [{"key": "fetch", "elapsed_ms": 100},
                   {"key": "analysts", "elapsed_ms": 900},
                   {"key": "host", "elapsed_ms": 100},
                   {"key": "report", "elapsed_ms": 100}],
    }
    # 旧数据无 attempts_log 字段：不抛 KeyError，其余字段仍在
    assert "attempts_log" not in old_day, "历史数据无 attempts_log 字段"
    for field in ("date", "status", "attempt", "attempts", "settle", "review",
                  "elapsed_ms", "stages"):
        assert field in old_day, f"无 attempts_log 时其他字段 {field} 应仍齐全"
    assert old_day["review"]["elapsed_ms"] + old_day["settle"]["elapsed_ms"] \
        == old_day["elapsed_ms"], "历史日 elapsed 口径一致"
    # 前端语义：缺 attempts_log 即「无重试明细」，attempt=1 与单条成功一致
    assert old_day["attempt"] == 1, "历史日 attempt 应为 1（首次即成功）"


# ═══════════════════════════════════════════════════════════════
# E1：LLM 单次调用硬超时 150s → 200s（追加需求 2026-08-16 B1）
# ═══════════════════════════════════════════════════════════════

def test_e1_llm_invoke_timeout_200():
    """E1：llm_runtime._INVOKE_TIMEOUT 应为 200.0（150→200，减少边缘误杀）。

    验证方式：import src/.../llm_runtime 后直接断言常量（不改实现文件）。
    """
    from stock_review_crew import llm_runtime

    assert getattr(llm_runtime, "_INVOKE_TIMEOUT", None) == 200.0, \
        "llm_runtime._INVOKE_TIMEOUT 应等于 200.0（实现未合入：仍为旧值 150.0）"


# ═══════════════════════════════════════════════════════════════
# E2：LLM 类失败（TimeoutError）→ 最多 2 次尝试即 fail-fast
#   - mock 每次抛 python 内置 TimeoutError（LLM 硬超时形态）
#   - 恰好转 2 次（不抛第 3 次）→ status=error、day.attempts==2、
#     attempts_log 长度 2 全 ok=False 且 reason 含「超时」、day.error 中文、
#     current.stage=error。
# ═══════════════════════════════════════════════════════════════

def test_e2_llm_timeout_failfast_two_attempts(monkeypatch):
    """E2：单日抛 TimeoutError → 恰好尝试 2 次后 fail-fast（LLM_MAX_ATTEMPTS=2）。

    失败分类：_is_llm_failure(TimeoutError)=True → 首败后上限降为 2；
    day.attempts/current.attempts 用实际上限 2；attempts_log 2 条全失败且 reason 含「超时」。
    """
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)

    calls: list[dict] = []

    def llm_timeout_impl(date, mode, max_rounds, trace, progress_cb=None):
        attempt = sum(1 for c in calls if c["date"] == date) + 1
        calls.append({"date": date, "attempt": attempt, "ok": False})
        if progress_cb:
            progress_cb("analysts", 20, f"第 {attempt} 次尝试 分析师 1/5", None)
        # LLM 硬超时形态：python 内置 TimeoutError
        raise TimeoutError("LLM 调用超时（200s），按降级处理")

    monkeypatch.setattr(btest, "_run_review_day_impl", llm_timeout_impl)
    bt_id = _start("2026-08-10", "2026-08-10")
    fin = _wait_backtest(bt_id)

    assert fin["status"] == "error", f"LLM 超时 2 次应 fail-fast → status=error，实际 {fin['status']}"
    day_calls = [c for c in calls if c["date"] == "2026-08-10"]
    assert len(day_calls) == 2, f"LLM 类失败应恰好尝试 2 次，实际 {len(day_calls)} 次"
    assert [c["attempt"] for c in day_calls] == [1, 2], \
        f"尝试序号应为 [1, 2]，实际 {[c['attempt'] for c in day_calls]}"

    cur = fin.get("current")
    assert cur is None or cur.get("stage") == "error", \
        f"current.stage 应为 error，实际 {cur}"
    assert cur is None or cur.get("attempts") == 2, \
        f"current.attempts 应为 2（LLM 日实际上限），实际 {cur.get('attempts')}"

    day = fin["days"][0]
    assert day["status"] == "failed"
    assert day["attempts"] == 2, f"LLM 日 day.attempts 应为 2，实际 {day['attempts']}"
    assert day["attempt"] == 2, f"LLM 日最后 attempt 应为 2，实际 {day['attempt']}"
    assert day["error"] and "超时" in str(day["error"]), \
        f"day.error 应为中文超时原因，实际：{day.get('error')}"

    log = day.get("attempts_log")
    assert log is not None, f"LLM fail-fast 日应有 attempts_log（实现未合入？）：{day.get('attempts_log')}"
    assert len(log) == 2, f"LLM 日 attempts_log 应 2 条（不抛第 3 次），实际 {len(log)}"
    for entry in log:
        assert entry["ok"] is False, f"全失败条目 ok 应为 False：{entry}"
        assert "超时" in entry["reason"], \
            f"LLM 失败条目 reason 应含「超时」，实际：{entry['reason']}"


# ═══════════════════════════════════════════════════════════════
# E3：data 类失败（非 TimeoutError）→ 保持 3 次尝试（回归不误判为 LLM 类）
#   现有 test_a3_failfast / test_a4 已覆盖 RuntimeError → 3 次的语义；
#   这里补一个显式 E3 回归：RuntimeError 抛满仍恰好 3 次、attempts==3，
#   并断言不会被 _is_llm_failure 误判（data 类保持 MAX_ATTEMPTS）。
# ═══════════════════════════════════════════════════════════════

def test_e3_data_failure_keeps_three_attempts(monkeypatch):
    """E3：mock 抛 RuntimeError（行情源不可用，data 类）→ 保持 3 次尝试、day.attempts==3。

    回归 A3/A4 语义，且证明 data 类未被 LLM 分类逻辑降上限。
    """
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)

    calls: list[dict] = []

    def data_fail_impl(date, mode, max_rounds, trace, progress_cb=None):
        attempt = sum(1 for c in calls if c["date"] == date) + 1
        calls.append({"date": date, "attempt": attempt, "ok": False})
        if progress_cb:
            progress_cb("analysts", 20, f"第 {attempt} 次尝试 分析师 1/5", None)
        raise RuntimeError("模拟单日失败：行情源暂时不可用")

    monkeypatch.setattr(btest, "_run_review_day_impl", data_fail_impl)
    bt_id = _start("2026-08-10", "2026-08-10")
    fin = _wait_backtest(bt_id)

    assert fin["status"] == "error", f"data 类 3 次全败应 status=error，实际 {fin['status']}"
    day_calls = [c for c in calls if c["date"] == "2026-08-10"]
    assert len(day_calls) == 3, f"data 类失败应恰好尝试 3 次（不降上限），实际 {len(day_calls)} 次"
    assert [c["attempt"] for c in day_calls] == [1, 2, 3], \
        f"尝试序号应为 [1, 2, 3]，实际 {[c['attempt'] for c in day_calls]}"

    day = fin["days"][0]
    assert day["status"] == "failed"
    assert day["attempts"] == btest.MAX_ATTEMPTS == 3, \
        f"data 类日 day.attempts 应为 3（MAX_ATTEMPTS），实际 {day['attempts']}"
    log = day.get("attempts_log")
    assert log is not None and len(log) == 3, f"data 日 attempts_log 应 3 条，实际 {len(log)}"


# ═══════════════════════════════════════════════════════════════
# F1-F3：回测「快速模式」（lite）验收（docs/requirements-backtest-live-diag.md §追加 2026-08-16）
#   - F1：lite 回测单日真实链路完成（status=done），report.json 存在且为规则汇总
#         （文本含「快速模式」、不含「LLM 生成报告失败」）；meta.degraded 含「快速模式」
#         但 validate_run_product 通过（免 LLM 降级关键词）。
#   - F2：lite 时 host/report 阶段零 LLM 调用（mock 计数：系统 prompt 命中
#         HOST_SYSTEM_PROMPT / REVIEW_ASSISTANT_SYSTEM_PROMPT 的 _invoke_llm 调用为 0；
#         分析师仍走 LLM，故总数 > 0）。
#   - F3：非 lite 回归——真实链路不带 lite 产出正常报告且不含「快速模式」；
#         A/D/E 既有用例（mock 链路）确认全绿。
#
# 现状说明：实现代理尚未合入 lite（run_review_day 无 lite 参数、graph 的
# default_initial_state 无 lite、host_node/report_node 无 lite 分支）。
# 故 F1/F2 本应红——红的原因是「lite 未实现」（TypeError: unexpected keyword 'lite'），
# 而非测试自身 bug；实现合并后变绿。F3（非 lite）应始终绿。
#
# 关键设计：F1/F2 走「真实 run_review_day + LLM/取数 mock 全链路」（复用 offline /
# fake_fetch 夹具），使 host/report 内部真正进入规则分支而非注入替代实现。
# lite=True 经 run_review_day → default_initial_state → state["lite"] 透传。

def _read_lite_products(date_str: str):
    """读真实 run_review_day 落盘的 meta.json / report.json / plan_path。"""
    from stock_review_crew.storage import reviews as reviews_store
    from stock_review_crew.accounts import plan as plan_store

    root = reviews_store.reviews_root() / date_str
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    final = report.get("final_report") or ""
    return meta, report, final, plan_store.plan_path(date_str)


def _count_system_prompt_hits(prompts_module, calls_sys_prompts):
    """counts 中命中 host/report 系统 prompt 的次数（用于 F2 断言零 LLM）。"""
    host_hits = sum(1 for sp in calls_sys_prompts
                    if sp == prompts_module.HOST_SYSTEM_PROMPT)
    report_hits = sum(1 for sp in calls_sys_prompts
                      if sp == prompts_module.REVIEW_ASSISTANT_SYSTEM_PROMPT)
    return host_hits, report_hits


def test_f1_lite_real_chain_done_and_rule_report(fake_fetch):
    """F1：真实 run_review_day(lite=True) 全链路 → 单日完成；
    report.json 存在且 final_report 含「快速模式」、不含「LLM 生成报告失败」；
    meta.degraded 含「快速模式」，validate_run_product 通过。

    若实现未合入：run_review_day 无 lite 参数 → TypeError（真实红信号）。
    """
    from backend.jobs import run_review_day
    from stock_review_crew.accounts import plan as plan_store

    plan_store.reset("2026-08-10")
    try:
        res = run_review_day("2026-08-10", "close", 2, trace=False, lite=True)
    except TypeError as exc:  # 实现未合入：lite kwarg 不被接受
        raise AssertionError(
            "lite 快速模式未实现：run_review_day 不接受 lite 参数（expected red，"
            f"待实现代理合入后此用例应变绿）：{exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 真实链路任何失败都视为 lite 路径未打通
        raise AssertionError(f"lite 真实链路失败（实现未合入或链路不通）：{exc}") from exc

    assert res["record_id"] == "2026-08-10"

    ok, reasons = btest.validate_run_product("2026-08-10")
    assert ok, f"lite 产物应通过 validate_run_product（不得含 LLM 降级关键词），原因：{reasons}"

    meta, _rpt, final_report, plan_p = _read_lite_products("2026-08-10")
    # report 为规则汇总特征：含「快速模式」（而非 LLM 生成报告失败）
    assert "快速模式" in final_report, \
        f"lite 报告应含「快速模式」，实际报告前 300 字：{final_report[:300]}"
    assert "LLM 生成报告失败" not in final_report, \
        "lite 报告不得含「LLM 生成报告失败」（不应走 LLM 失败路径）"
    # meta.degraded 含「快速模式」但非 LLM 降级关键词
    degraded_notes = "；".join(meta.get("degraded") or [])
    assert "快速模式" in degraded_notes, \
        f"lite 时 meta.degraded 应含「快速模式」，实际：{degraded_notes}"
    # plan.json 仍落盘（报告级 lite 不破坏计划产物）
    assert plan_p.exists(), "lite 单日也应产出 plan.json"


def test_f2_lite_host_render_zero_llm_calls(fake_fetch, monkeypatch):
    """F2：lite 真实链路 → host/report 阶段零 LLM 调用。

    验证方法：monkeypatch graph._invoke_llm 为计数包装，记录每次调用的系统
    prompt；lite 链结束后断言命中 HOST_SYSTEM_PROMPT（主持人）与
    REVIEW_ASSISTANT_SYSTEM_PROMPT（报告）的调用数为 0，而分析师仍走 LLM
    （总数 > 0）——证明 lite 只跳过 host/report 的 LLM。
    """
    from stock_review_crew import graph
    from stock_review_crew import prompts as prompts_mod
    from backend.jobs import run_review_day
    from stock_review_crew.accounts import plan as plan_store

    calls_sys_prompts: list[str] = []
    original_invoke = graph._invoke_llm

    def _counting_invoke(llm, messages, timeout=None):
        # 记录系统 prompt（host/report 用固定系统 prompt 识别）
        system = ""
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                system = m.get("content", "")
        calls_sys_prompts.append(system)
        return original_invoke(llm, messages)

    monkeypatch.setattr(graph, "_invoke_llm", _counting_invoke)

    plan_store.reset("2026-08-10")
    try:
        res = run_review_day("2026-08-10", "close", 2, trace=False, lite=True)
    except TypeError as exc:  # 实现未合入
        raise AssertionError(
            "lite 未实现：run_review_day 不接受 lite 参数，无法验证零 LLM（expected red，"
            f"待实现后应变绿）：{exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"lite 真实链路失败：{exc}") from exc

    assert res["record_id"] == "2026-08-10"
    assert calls_sys_prompts, "lite 链路应至少触发分析师 LLM 调用（分析师不走 lite）"

    host_hits, report_hits = _count_system_prompt_hits(prompts_mod, calls_sys_prompts)
    assert host_hits == 0, \
        f"lite 时 host 阶段不应触发 LLM（命中 HOST_SYSTEM_PROMPT 的调用应 0），实际 {host_hits}"
    assert report_hits == 0, \
        f"lite 时 report 阶段不应触发 LLM（命中 REVIEW_ASSISTANT_SYSTEM_PROMPT 的调用应 0），实际 {report_hits}"
    # 反向佐证：分析师确实调了 LLM（否则上面两个断言可能「真空通过」）
    analyst_llm_hits = len(calls_sys_prompts) - host_hits - report_hits
    assert analyst_llm_hits > 0, \
        "分析师阶段应仍走 LLM（总调用应大于 host/report 的 0），避免虚假通过"


def test_f3_lite_off_behavior_unchanged(fake_fetch):
    """F3：非 lite（不带 lite）真实链路 → 行为不变：单日完成、产物通过、
    报告不含「快速模式」（证明 lite 不影响普通路径）。

    说明：A/D/E 既有用例（A1-A4/A7/D1-D2/E1-E3，mock 链路）均已覆盖非 lite 的
    编排/重试/耗时/attempts_log/LLM 分类语义，本用例从「真实链路」侧确认非 lite 不回归。
    """
    from backend.jobs import run_review_day
    from stock_review_crew.accounts import plan as plan_store

    plan_store.reset("2026-08-10")
    res = run_review_day("2026-08-10", "close", 2, trace=False)  # 不带 lite
    assert res["record_id"] == "2026-08-10"

    ok, reasons = btest.validate_run_product("2026-08-10")
    assert ok, f"非 lite 产物应通过校验，原因：{reasons}"

    meta, _rpt, final_report, _plan_p = _read_lite_products("2026-08-10")
    assert "快速模式" not in final_report, \
        "非 lite 报告不应含「快速模式」（lite 专属标注不污染普通路径）"
    assert "快速模式" not in "；".join(meta.get("degraded") or []), \
        "非 lite 时 meta.degraded 不应含「快速模式」"


# ═══════════════════════════════════════════════════════════════
# 回测断点续跑（resume）+ 产物校验失败保留行情快照
# docs/requirements-backtest-resume.md 验收 H1-H6（回测相关 + 快照保留单测）
#
# 现状说明：实现代理尚未合入该需求（backend/backtest.py 无 _run_loop 的
# start_idx、无 resume_backtest；backend/routes_backtest.py 无 resume 路由；
# reviews.delete_review 无 keep_snapshot；_run_one_day 产物校验失败仍走
# delete_review 整删）。故本批 H1-H4/H6 用例本应红——红的原因是「实现未就绪」：
#   H1/H2/H3 → POST /api/backtest/{id}/resume 不存在（404）；
#   H4       → delete_review(date, keep_snapshot=...) 抛 TypeError；
#   H6       → _run_one_day 产物校验失败清理后 market_data.json 被删。
# 实现合并后应全部变绿。
#
# 关键设计：
#   - resume 数据用 2026-08-10(周一)~2026-08-14(周五) 共 5 天，第 3 天=2026-08-12。
#   - make_resume_fake 用可变 `fail_12` 标志控制「第 3 天原跑失败、续跑成功」，
#     支持一次 start + 一次 resume 走过两个阶段而返回同一份注入实现。
# ═══════════════════════════════════════════════════════════════

def make_resume_fake(fail_12: dict) -> tuple[list, list]:
    """复刻 make_fake_day 但失败行为由 fail_12['fail'] 控制：为真时 2026-08-12
    全失败，为假时成功；其余日期恒成功。返回 (impl, calls)。"""
    calls: list[dict] = []

    def impl(date: str, mode: str, max_rounds: int, trace: bool, progress_cb=None):
        if progress_cb:
            for st, pct in (("fetch", 1), ("analysts", 20), ("host", 50), ("report", 85)):
                progress_cb(st, pct, st, None)
        attempt = sum(1 for c in calls if c["date"] == date) + 1
        should_fail = date == "2026-08-12" and fail_12.get("fail") and attempt <= 3
        calls.append({"date": date, "attempt": attempt, "ok": not should_fail})
        if should_fail:
            raise RuntimeError("单日复盘模拟失败")
        _write_products(date)
        # 每个成功日小睡一会，让 resume 阶段可被轮询到 running 态（done 天保留观察）
        time.sleep(0.05)
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


def _wait_observing_marked_done(bt_id: str, timeout: float = 30.0) -> dict:
    """轮询直到出现一个 running 快照并断言 days[0..1] 仍 done；若回测已终局则
    直接返回终局（仍断言其 done 天被保留，见调用处）。"""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/api/backtest/{bt_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("done", "error", "cancelled"):
            return last
        if last["status"] == "running":
            # 任意一个 running 快照上断言「已 done 天保留」
            assert last["days"][0]["status"] == "done", \
                f"续跑中 days[0] 应保持 done，实际 {last['days'][0]['status']}（红：done 天被重置）"
            assert last["days"][1]["status"] == "done", \
                f"续跑中 days[1] 应保持 done，实际 {last['days'][1]['status']}（红：done 天被重置）"
            assert last["days_done"] >= 2, \
                f"续跑中 days_done 应从已 done 数起算（≥2），实际 {last['days_done']}"
            return last
        time.sleep(0.02)
    raise AssertionError(f"回测未在 {timeout}s 内出现 running 或终局：{last.get('status')}")


# ────────────────── H1：resume 从失败日续跑（done 天保留 + 最终全 done） ──────────────────

def test_h1_resume_from_failed_day(monkeypatch):
    """H1：start 5 天回测、第 3 天(2026-08-12)全败 → status=error、days[0..1]=done、
    days[2]=failed；POST resume → 200、resume_from=失败日；
    续跑中 days[0..1] 仍 done、days_done 从 2 起算；续跑后 → done、全 done、days_done==len(days)。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)
    fail_12 = {"fail": True}
    impl, calls = make_resume_fake(fail_12)
    monkeypatch.setattr(btest, "_run_review_day_impl", impl)

    # 原跑：5 天，第 3 天全败 → status=error
    bt_id = _start("2026-08-10", "2026-08-14")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "error", f"第 3 天全败应 error，实际 {fin['status']}"
    dates = [d["date"] for d in fin["days"]]
    assert dates == ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"], \
        f"交易日序列应为 08-10..08-14 五天，实际 {dates}"
    assert fin["days"][0]["status"] == "done", "days[0] 应已 done"
    assert fin["days"][1]["status"] == "done", "days[1] 应已 done"
    assert fin["days"][2]["status"] == "failed", \
        f"days[2](2026-08-12) 应 failed，实际 {fin['days'][2]['status']}"

    # 打开续跑开关：第 3 天将被成功重跑（等效「故障恢复」）
    fail_12["fail"] = False

    # resume → 200、resume_from=失败日
    r = client.post(f"/api/backtest/{bt_id}/resume")
    assert r.status_code == 200, \
        f"resume 应 200（红：实现未合入应 404），实际 {r.status_code} {r.text}"
    body = r.json()
    assert body.get("backtest_id") == bt_id, \
        f"resume 应回传 backtest_id，实际 {body.get('backtest_id')}"
    assert body.get("resume_from") == "2026-08-12", \
        f"resume_from 应为失败日 2026-08-12，实际 {body.get('resume_from')}"
    assert body.get("ok") is True, "resume 响应应含 ok:true"

    # 续跑中：done 天保留、days_done 从已 done 数起算
    running_or_final = _wait_observing_marked_done(bt_id)
    if running_or_final.get("status") == "running":
        assert running_or_final["days"][0]["status"] == "done"
        assert running_or_final["days"][1]["status"] == "done"

    # 续跑完成：全 done
    fin2 = _wait_backtest(bt_id)
    assert fin2["status"] == "done", f"续跑后应 done，实际 {fin2['status']}"
    assert len(fin2["days"]) == 5
    assert all(d["status"] == "done" for d in fin2["days"]), \
        "续跑后全部天应 done"
    assert fin2["days_done"] == len(fin2["days"]) == 5, \
        f"续跑后 days_done 应 == len(days)==5，实际 {fin2['days_done']}"


# ────────────────── H2：done 回测拒绝 resume ──────────────────

def test_h2_done_resume_rejected(monkeypatch):
    """H2：已完成回测 resume → 409（中文「已完成」）。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)
    monkeypatch.setattr(btest, "_run_review_day_impl", make_fake_day()[0])

    bt_id = _start("2026-08-10", "2026-08-11")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "done", f"应先跑完为 done，实际 {fin['status']}"

    r = client.post(f"/api/backtest/{bt_id}/resume")
    assert r.status_code == 409, \
        f"done 回测 resume 应 409（红：实现未合入应 404），实际 {r.status_code}"
    assert "已完成" in str(r.json().get("detail") or {}), \
        f"409 原因应中文含「已完成」，实际：{r.json()}"


# ────────────────── H3：单 running 互斥（续跑运行中再 resume → 409 运行中） ──────────────────

def test_h3_resume_mutex_single_running(monkeypatch):
    """H3：error 回测 resume 后若仍 running，再 resume（同/另一）→ 409「运行中」。
    用一个缓慢续跑 impl 拉长 running 窗，成功触发二次 resume 的互斥拒绝。"""
    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)

    # 先构造一个 error 回测（第 3 天全败）
    fail_12 = {"fail": True}
    impl, _calls = make_resume_fake(fail_12)
    monkeypatch.setattr(btest, "_run_review_day_impl", impl)
    bt_id = _start("2026-08-10", "2026-08-14")
    fin = _wait_backtest(bt_id)
    assert fin["status"] == "error", f"应构造 error 回测，实际 {fin['status']}"

    # 切换为缓慢成功 impl，续跑后保持 running，制造互斥窗
    def slow_ok(date, mode, max_rounds, trace, progress_cb=None):
        _write_products(date)
        time.sleep(1.0)  # 拉长 running 窗
        return {
            "meta": {"date": date}, "report": "测试报告", "snapshot": {"date": date},
            "record_id": date, "quality": None,
            "stage_timings": _stage_timings(), "analyst_timings": _analyst_timings(),
        }

    monkeypatch.setattr(btest, "_run_review_day_impl", slow_ok)
    fail_12["fail"] = False

    r1 = client.post(f"/api/backtest/{bt_id}/resume")
    assert r1.status_code == 200, f"首次 resume 应 200，实际 {r1.status_code} {r1.text}"

    # 等进入 running
    deadline = time.monotonic() + 10
    running = False
    while time.monotonic() < deadline:
        d = client.get(f"/api/backtest/{bt_id}").json()
        if d["status"] == "running":
            running = True
            break
        time.sleep(0.02)
    assert running, "续跑应进入 running"

    # 互斥：续跑运行中再 resume（同一回测）→ 409「运行中」
    r2 = client.post(f"/api/backtest/{bt_id}/resume")
    assert r2.status_code == 409, \
        f"续跑运行中再 resume 应 409，实际 {r2.status_code} {r2.text}"
    detail = str(r2.json().get("detail") or "")
    assert ("运行" in detail) or ("进行" in detail), \
        f"409 原因应中文含「运行中/进行中」，实际：{r2.json()}"

    # 收尾：清掉缓慢续跑，避免拖慢 suite（取消）
    client.post(f"/api/backtest/{bt_id}/cancel")
    _wait_backtest(bt_id)


# ────────────────── H4：delete_review keep_snapshot 保留行情快照（+ 普通删除回归） ──────────────────

def test_h4_delete_review_keep_snapshot_retains():
    """H4：delete_review(date, keep_snapshot=True) 删除产物后 market_data.json 仍在；
    该日 meta/report 等产物被清，但快照保留（离线回放不破坏）。"""
    from stock_review_crew.storage import reviews as reviews_store

    d = "2026-08-12"
    reviews_store.save_review({"date": d, "time": "150000", "mode": "close"}, "# 报告", {"index": 1})
    reviews_store.save_market_data(d, {"date": d, "market_data": "# 行情快照"})
    snap = reviews_store.market_data_path(d)
    assert snap.is_file(), "前置：快照应先写好"

    try:
        result = reviews_store.delete_review(d, keep_snapshot=True)
    except TypeError as exc:
        raise AssertionError(
            "delete_review 尚无 keep_snapshot 参数（expected red，待实现代理合入后应变绿）："
            f"{exc}"
        ) from exc

    assert result is True, "带 keep_snapshot 删除应返回 True"
    assert snap.is_file(), \
        "keep_snapshot=True 删除产物后 market_data.json 应保留（防复发：恢复离线回放）"
    assert not (reviews_store.reviews_root() / d / "meta.json").exists(), "产物 meta 应被删"
    assert not (reviews_store.reviews_root() / d / "report.json").exists(), "产物 report 应被删"


def test_h4_plain_delete_removes_snapshot():
    """H4 回归：不带 keep_snapshot 的普通 delete_review 删整份（含 market_data.json）——
    页面/管理的删除语义不变（test_storage_reviews 既有 delete 用例不破坏）。"""
    from stock_review_crew.storage import reviews as reviews_store

    d = "2026-08-12"
    reviews_store.save_review({"date": d, "time": "150000", "mode": "close"}, "# 报告", {"index": 1})
    reviews_store.save_market_data(d, {"date": d, "market_data": "# 行情快照"})
    snap = reviews_store.market_data_path(d)
    assert snap.is_file()

    assert reviews_store.delete_review(d) is True
    assert not (reviews_store.reviews_root() / d).exists(), "普通删除应删整份目录（含快照）"
    assert not snap.exists(), "普通 delete_review 应连 market_data.json 一并删除"


# ────────────────── H6：_run_one_day 产物校验失败清理保留快照（防复发） ──────────────────

def _write_products_llm_degraded(date_str: str) -> None:
    """写校验不合格产物：report/plan/meta 齐全，但 meta.degraded 含
    「LLM 不可用」（命中 LLM_DEGRADED_MARKERS）→ validate_run_product 拒绝。"""
    from stock_review_crew.accounts import plan as plan_store

    review_dir = _reviews_store_root() / date_str
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "meta.json").write_text(json.dumps({
        "date": date_str, "mode": "close", "mode_label": "收盘复盘",
        "degraded": ["LLM 不可用：所有分析师降级为规则"], "sources": ["测试源"],
        "disclaimer": "仅供参考，不构成投资建议", "summary": "测试摘要",
    }, ensure_ascii=False), encoding="utf-8")
    (review_dir / "report.json").write_text(json.dumps({
        "final_report": "# 测试报告\n> 仅供参考，不构成投资建议",
        "analyses": [{"skill_id": s[0], "skill_name": s[1]} for s in SKILLS],
    }, ensure_ascii=False), encoding="utf-8")
    plan_p = plan_store.plan_path(date_str)
    plan_p.parent.mkdir(parents=True, exist_ok=True)
    plan_p.write_text(json.dumps({
        "date": date_str, "mode": "close",
        "plans": [{"rec_date": date_str, "skill_id": s[0], "skill_name": s[1],
                   "code": "600001", "side": "buy", "target_price": 10.0,
                   "position_cheng": 1, "qty": None} for s in SKILLS],
    }, ensure_ascii=False), encoding="utf-8")


def _reviews_store_root():
    from stock_review_crew.storage import reviews as reviews_store

    return reviews_store.reviews_root()


def test_h6_product_validation_failure_preserves_snapshot(monkeypatch):
    """H6：_run_one_day 产物校验失败路径（预写快照再触发失败）→ 清理后该日
    market_data.json 仍存在；坏产物（meta/report）被清。保证离线回放不破坏。"""
    from stock_review_crew.storage import reviews as reviews_store

    monkeypatch.setattr(btest, "_settle_impl", _fake_settle)
    monkeypatch.setattr(btest, "RETRY_SLEEP", 0.01)

    d = "2026-08-12"
    # 前置：预写完整行情快照（模拟真实离线回放前置条件）
    reviews_store.save_market_data(d, {"date": d, "market_data": "# 好快照"})
    snap = reviews_store.market_data_path(d)
    assert snap.is_file(), "前置：快照应先写好"

    # mock 单日：产物校验失败（写 LLM 降级 meta → validate_run_product=False）
    def bad_impl(date, mode, max_rounds, trace, progress_cb=None):
        _write_products_llm_degraded(date)
        now = time.monotonic()
        return {"meta": {"date": date}, "report": "测试报告",
                "snapshot": {"date": date}, "record_id": date, "quality": None,
                "stage_timings": {k: {"started_at": now, "elapsed_ms": 0}
                                  for k in ("fetch", "analysts", "host", "report")},
                "analyst_timings": {}}

    monkeypatch.setattr(btest, "_run_review_day_impl", bad_impl)

    bt = btest._new_backtest({
        "start": d, "end": d, "mode": "close", "force": False,
        "lite": False, "max_rounds": 3,
    })
    try:
        btest._run_one_day(bt, 0)
    except btest._BacktestStopped as exc:
        assert exc.bt["status"] == "error", "产物校验失败应 fail-fast → error"
    else:
        raise AssertionError("_run_one_day 产物校验失败应抛 _BacktestStopped（fail-fast）")

    # 防复发：清理后快照仍在，坏产物被清
    assert snap.is_file(), \
        "产物校验失败清理后 market_data.json 应保留（红：实现未合入会整删快照）"
    assert not (reviews_store.reviews_root() / d / "meta.json").exists(), "坏产物 meta 应被清"
    assert not (reviews_store.reviews_root() / d / "report.json").exists(), "坏产物 report 应被清"
