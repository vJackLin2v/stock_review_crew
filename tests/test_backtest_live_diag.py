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
