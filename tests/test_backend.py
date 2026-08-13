"""I5 后端 API 集成测试：复盘全链路 / 窗口判定 / 任务轮询 / TTL / 历史 / 静态托管。

离线确定性（本文件遵守）：
- 函数级 autouse fixture 设置 DEEPSEEK_API_KEY=""（注意：PowerShell 空串赋值会删除环境变量
  导致 .env 真实 Key 生效，必须在 Python 内设置；每个测试结束自动恢复，避免污染
  test_data_config 等依赖 .env Key 的全量套件用例）；
- 引擎取数入口（graph._load_pure_fetcher）monkeypatch 返回极简 I1 契约快照，禁止真实网络；
- LLM 相关入口（_llm_ready/_callbacks/_load_analyst_tools）全部离线化；
- 存储走 STOCK_REVIEW_DATA_DIR / CHATS_DATA_DIR 隔离目录（Path.mkdir 自建并清理，
  不用 pytest tmp_path——沙箱 0o700 ACL 问题，见 PLAYBOOK）。
"""

import os
import re
import shutil
import sys
import time
import types
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import stock_review_crew.chat as chat
import stock_review_crew.graph as graph
from stock_review_crew.storage.reviews import save_review

import backend.main as bm

client = TestClient(bm.app)


_MOCK_REPORT = (
    "# 2026-08-03 A股收盘复盘报告\n\n"
    "## 一、市场概况\n指数收涨，量能温和。\n\n"
    "## 二、盘面特征\n涨停 47 家、跌停 3 家，连板高度 3 板。\n\n"
    "## 三、操作计划\n轻仓观察，等待方向。\n\n"
    "> 仅供参考，不构成投资建议"
)


class _FakeLLM:
    def __init__(self, factory):
        self._factory = factory

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        return types.SimpleNamespace(content=self._factory(messages))


def _llm_reply(messages):
    """按消息内容确定性返回 mock 回复（2026-08-10 契约：LLM 不可用时复盘直接终止，
    因此测全链路的用例必须让 LLM 可用）。"""
    user = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            user = m.get("content", "")
    if "报告要求" in user:
        return _MOCK_REPORT
    if "实质性分歧" in user:
        return "分歧判断：无\n总结：观点一致，无需辩论。"
    return "## 1、昨日复盘验证\n无昨日报告，跳过验证\n## 2、今日判断\n看多\n## 3、操作计划\n进攻"


@pytest.fixture(autouse=True)
def no_llm_key():
    """函数级：无 LLM Key 环境（规则引擎降级），测试结束自动恢复，不污染其他模块。"""
    had_key = "DEEPSEEK_API_KEY" in os.environ
    old_key = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = ""
    yield
    if had_key:
        os.environ["DEEPSEEK_API_KEY"] = old_key or ""
    else:
        os.environ.pop("DEEPSEEK_API_KEY", None)


def _fake_mode_data(mode: str, date: str) -> dict:
    """极简 I1 契约快照（{date, mode, mode_label, blocks, degraded}），覆盖 6 模式所需块。"""
    return {
        "date": date,
        "mode": mode,
        "mode_label": bm.MODE_LABELS.get(mode, mode),
        "blocks": {
            "index_trend": {
                "indices": {"shanghai": {"close": 3764.0, "pct_change": 0.0052}},
                "source": "测试源·指数",
                "degraded": False,
                "degraded_reason": [],
            },
            "minute": {
                "symbol": "sh000001",
                "bars": [
                    {"time": "09:30", "close": 3760.0},
                    {"time": "10:00", "close": 3764.0},
                ],
                "source": "测试源·分时",
                "degraded": False,
            },
            "sectors": {
                "top5": [
                    {"板块": "半导体", "涨跌幅": 0.02, "领涨股": "示例科技"},
                    {"板块": "军工", "涨跌幅": 0.015, "领涨股": "示例军工"},
                ],
                "bottom5": [
                    {"板块": "保险", "涨跌幅": -0.03, "领涨股": "示例保险"},
                    {"板块": "煤炭", "涨跌幅": -0.02, "领涨股": "示例煤炭"},
                ],
                "source": "测试源·板块",
                "degraded": False,
            },
            "zt_pool": {
                "limit_up": {"count": 45, "tier": {"首板": 40, "2": 5}, "stocks": [{"名称": "示例涨停A"}]},
                "limit_down": {"count": 3, "tier": {}},
                "zhaban_rate": 0.12,
                "source": "测试源·涨跌停",
                "degraded": False,
            },
            "breadth": {
                "up": 2800, "down": 1800, "limit_up": 45, "limit_down": 3, "zhaban_rate_pct": 12.0,
                "distribution": {
                    "涨停": 45, "跌停": 3, "涨5%-10%": 90, "涨2%-5%": 380, "涨0%-2%": 1500,
                    "平盘": 120, "跌0%-2%": 1300, "跌2%-5%": 220, "跌5%以上": 30,
                },
                "source": "测试源·涨跌分布",
                "note": "测试分布说明（降级时透传）",
            },
            "sentiment": {
                "up_count": 2800,
                "down_count": 1800,
                "red_rate": 0.6087,
                "lianban_rate": 0.3,
                "zhaban_rate": 0.12,
                "hean_rate": 0.05,
                "avg_return": 0.012,
                "up_down_ratio": 1.5556,
                "source": "测试源·情绪",
                "degraded": False,
            },
            "news": {"caixin": [], "cctv": [], "source": "测试源·资讯"},
            "lhb": {
                "stocks": [],
                "source": "数据缺失",
                "degraded": True,
                "degraded_reason": ["龙虎榜数据缺失（示例）"],
            },
        },
        "degraded": ["lhb: 龙虎榜数据缺失（示例）"],
        "degraded_flag": True,
    }


@pytest.fixture()
def iso_dirs(monkeypatch):
    """隔离存储目录（自建并清理，沙箱下 pytest tmp_path 不可用）。"""
    base = Path(__file__).resolve().parent.parent / ".tmp" / f"backend_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    reviews_dir = base / "reviews"
    chats_dir = base / "chats"
    archive_dir = base / "archive"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    chats_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(reviews_dir))
    monkeypatch.setenv("CHATS_DATA_DIR", str(chats_dir))
    monkeypatch.setenv("STOCK_REVIEW_ARCHIVE_DIR", str(archive_dir))
    yield {"base": base, "reviews": reviews_dir, "chats": chats_dir, "archive": archive_dir}
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """离线确定性：LLM 用确定性 mock（2026-08-10 契约：LLM 不可用时复盘直接终止，
    因此测全链路的用例必须让 LLM 可用）+ 数据取数/检索全 mock，禁止真实网络。"""
    monkeypatch.setattr(graph, "_llm_ready", lambda: True)
    llm = _FakeLLM(_llm_reply)
    strict = _FakeLLM(_llm_reply)
    monkeypatch.setattr(graph, "_llm_pair", lambda: (llm, strict))
    monkeypatch.setattr(graph, "_callbacks", lambda: [])
    monkeypatch.setattr(graph, "_load_analyst_tools", lambda: (None, None))
    monkeypatch.setattr(graph, "_legacy_fetch", lambda state: None)  # 兜底：禁止旧工具网络路径
    monkeypatch.setattr(chat, "_get_default_llm", lambda: None)
    monkeypatch.setattr(
        chat, "_fetch_target_data",
        lambda target_type, target: {"available": False, "note": "数据暂时不可用（离线测试）", "data": None},
    )
    monkeypatch.setattr(
        chat, "search_chat_history",
        lambda q, n_results=5: {"available": False, "note": "历史对话检索不可用（离线测试）", "data": None},
    )
    monkeypatch.setattr(chat, "save_chat_history", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        chat.ChatEngine, "_search_review",
        lambda self, q: {"available": False, "note": "复盘检索不可用（离线测试）", "data": None},
    )


@pytest.fixture()
def fake_fetch(monkeypatch):
    """引擎取数入口返回极简快照（禁止真实网络/30s 超时进测试）。"""
    monkeypatch.setattr(graph, "_load_pure_fetcher", lambda: _fake_mode_data)


def _wait_job(job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        data = r.json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError(f"任务超时未完成：{r.json()}")


# ── 健康检查 / 静态托管 ──

def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    # 2026-08-06：health 附带 Langfuse 监控状态（每次启动打开监控的可见性契约）
    assert isinstance(data.get("langfuse"), dict)
    for key in ("enabled", "callback_ok", "host", "mode"):
        assert key in data["langfuse"]
    # 2026-08-12：health 附带资讯开关（前端进度条据此隐藏「资讯」步骤）
    assert isinstance(data.get("news_enabled"), bool)


def test_static_mount():
    if not bm.DIST.is_dir():
        pytest.skip("frontend/dist 不存在，跳过静态托管断言")
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


# ── 复盘全链路：POST → 轮询 → done → 结果契约 → 列表 → 详情 → 删除 → 404 ──

def test_review_full_chain(fake_fetch, iso_dirs):
    # 详情 404 中文（记录不存在）
    r = client.get("/api/reviews/2020-01-02")
    assert r.status_code == 404
    assert "复盘记录不存在" in r.json()["detail"]

    r = client.post("/api/reviews", json={"date": "2020-01-02", "mode": "close", "max_rounds": 2})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert job_id

    job = _wait_job(job_id)
    assert job["status"] == "done", job
    # 轮询契约字段齐备；内部字段（_ 前缀）不外泄
    for key in ("job_id", "status", "stage", "pct", "message", "analysts_done", "analysts_total", "result", "error"):
        assert key in job, f"缺少契约字段：{key}"
    assert not any(k.startswith("_") for k in job)
    assert job["pct"] == 100
    assert job["stage"] == "done"
    assert job["message"] == "复盘完成"
    assert job["error"] is None
    assert job["analysts_done"] == 5 and job["analysts_total"] == 5

    res = job["result"]
    record_id = res["record_id"]
    # R11 R4：record_id = "{date}"（每日一份，不再含 HHMMSS）
    assert record_id == "2020-01-02"
    # result 契约：{record_id, meta, report, snapshot, quality?}（quality 为 R10 Q4 可空字段）
    assert set(res) >= {"record_id", "meta", "report", "snapshot"}
    assert "quality" in res
    assert res["quality"] is None or isinstance(res["quality"], dict)
    meta = res["meta"]
    for key in ("date", "mode", "mode_label", "time", "created_at", "degraded", "sources", "disclaimer", "summary"):
        assert key in meta, f"meta 缺少契约键：{key}"
    assert meta["date"] == "2020-01-02"
    assert meta["mode"] == "close"
    assert meta["mode_label"] == "收盘复盘"
    assert meta["disclaimer"] == "仅供参考，不构成投资建议"
    assert isinstance(meta["degraded"], list) and meta["degraded"]
    assert isinstance(meta["sources"], list) and meta["sources"]
    assert isinstance(meta["summary"], str) and meta["summary"]
    assert re.fullmatch(r"\d{6}", str(meta["time"]))  # 时间点仅作展示
    assert isinstance(res["report"], str) and res["report"]
    assert res["report"].rstrip().endswith("仅供参考，不构成投资建议")


    # ── snapshot 严格对齐前端 mock 结构（normalize.js / mock.js）──
    snap = res["snapshot"]
    # R11-7：快照新增 breadth（涨跌分布）
    assert set(snap) == {
        "index_minute", "limit_ladder", "sectors", "sectors_bottom",
        "sentiment", "breadth", "source", "degraded",
    }
    assert isinstance(snap["breadth"], dict) and "distribution" in snap["breadth"]
    # P0-2：分布键名必须为含 % 规范键（与前端 BREADTH_ORDER 对齐）
    assert set(snap["breadth"]["distribution"]) == {
        "涨停", "跌停", "涨5%-10%", "涨2%-5%", "涨0%-2%",
        "平盘", "跌0%-2%", "跌2%-5%", "跌5%以上",
    }
    assert snap["breadth"]["note"] == "测试分布说明（降级时透传）"  # P1-H：note 透传
    assert snap["index_minute"][0]["name"] == "上证指数"
    assert snap["index_minute"][0]["points"][0] == {"time": "09:30", "value": 3760.0}
    assert set(snap["limit_ladder"]) == {"up", "down"}
    assert snap["limit_ladder"]["up"][0] == {"label": "首板", "count": 40, "stocks": ["示例涨停A"]}
    assert snap["limit_ladder"]["up"][1] == {"label": "2板", "count": 5, "stocks": []}
    assert snap["limit_ladder"]["down"][0] == {"label": "跌停", "count": 3, "stocks": []}
    assert snap["sectors"][0] == {"name": "半导体", "pct_change": 0.02, "leader": "示例科技"}
    assert snap["sectors"][1] == {"name": "军工", "pct_change": 0.015, "leader": "示例军工"}
    # P1-K：板块跌幅榜 bottom5 透传（跌幅最大在前）
    assert snap["sectors_bottom"][0] == {"name": "保险", "pct_change": -0.03, "leader": "示例保险"}
    assert snap["sectors_bottom"][1] == {"name": "煤炭", "pct_change": -0.02, "leader": "示例煤炭"}
    sent = snap["sentiment"]
    assert set(sent) == {
        "up_count", "down_count", "limit_up_count", "limit_down_count", "red_rate",
        "continue_rate", "break_rate", "button_rate", "avg_return", "up_down_ratio",
        "source", "degraded",
    }
    assert sent["up_count"] == 2800 and sent["down_count"] == 1800
    assert sent["limit_up_count"] == 45 and sent["limit_down_count"] == 3
    assert sent["red_rate"] == pytest.approx(0.6087)
    assert sent["continue_rate"] == pytest.approx(0.3)
    assert sent["break_rate"] == pytest.approx(0.12)
    assert sent["button_rate"] == pytest.approx(0.05)
    assert sent["avg_return"] == pytest.approx(0.012)
    assert sent["up_down_ratio"] == pytest.approx(1.5556)
    assert sent["source"] and isinstance(sent["source"], str)
    assert isinstance(sent["degraded"], list)
    assert isinstance(snap["source"], str) and snap["source"]
    assert isinstance(snap["degraded"], list) and snap["degraded"]
    assert any("龙虎榜" in d for d in snap["degraded"])

    # ── 历史分组列表 ──
    r = client.get("/api/reviews")
    assert r.status_code == 200
    groups = r.json()
    assert groups and groups[0]["date"] == "2020-01-02"
    item = groups[0]["items"][0]
    for key in ("record_id", "mode", "mode_label", "time", "created_at", "summary"):
        assert key in item
    assert item["record_id"] == record_id
    assert item["mode"] == "close" and item["mode_label"] == "收盘复盘"

    # ── 详情（R11 R4：单日期路由；report.json 展开 analyses/debate_history）──
    r = client.get("/api/reviews/2020-01-02")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"meta", "report", "analyses", "debate_history", "snapshot"}
    assert body["report"] == res["report"]
    assert isinstance(body["analyses"], list) and len(body["analyses"]) == 5
    for analyst in body["analyses"]:
        assert analyst.get("skill_name") and analyst.get("analysis")
    # P1-C（2026-08-07）：降级（规则引擎）模式下无多空分歧可跳过辩论（0 轮）；
    # 有轮次则每轮结构必须完整。
    assert isinstance(body["debate_history"], list)
    for entry in body["debate_history"]:
        assert entry.get("round") and isinstance(entry.get("responses"), list)
        for resp in entry["responses"]:
            assert resp.get("skill_name") and resp.get("response")
    assert body["snapshot"] == snap

    # ── 删除 204（无 body）→ 再删 404 中文 ──
    r = client.delete("/api/reviews/2020-01-02")
    assert r.status_code == 204
    assert r.content == b""
    r = client.delete("/api/reviews/2020-01-02")
    assert r.status_code == 404
    assert "复盘记录不存在" in r.json()["detail"]
    r = client.get("/api/reviews")
    assert r.json() == []

    # ── R11 R4 并发去重：同一天已有 done 记录 → 409 中文（每日一份）──
    r = client.post("/api/reviews", json={"date": "2020-01-02", "mode": "close", "max_rounds": 2})
    assert r.status_code == 200
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "done", job
    r = client.post("/api/reviews", json={"date": "2020-01-02", "mode": "close"})
    assert r.status_code == 409
    assert "已有复盘记录" in r.json()["detail"]


def test_review_fails_when_llm_unavailable(fake_fetch, iso_dirs, monkeypatch):
    """2026-08-10 用户确认契约：LLM 不可用 → 复盘任务 error（中文消息透传，
    不再降级生成规则报告）。"""
    monkeypatch.setattr(graph, "_llm_ready", lambda: False)
    r = client.post("/api/reviews", json={"date": "2020-01-03", "mode": "close", "max_rounds": 2})
    assert r.status_code == 200, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "error", job
    assert "LLM 不可用" in job["error"]
    assert "本次复盘终止" in job["error"]
    assert job["message"] == job["error"]


# ── 窗口判定 ──

def test_window_auction_ended_400(fake_fetch, iso_dirs, monkeypatch):
    """09:40 选 auction → 400 中文建议（requirements §二.1 示例）。"""
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 9, 40, 0))  # 周一
    r = client.post("/api/reviews", json={"date": "2026-08-03", "mode": "auction"})
    assert r.status_code == 400
    assert "竞价数据已结束，建议切换上午盘中或午间复盘" in r.json()["detail"]


def test_window_buffer_and_generic_hints(monkeypatch):
    """09:25-09:30 缓冲段定向建议；其余窗口外给最近可用模式建议。"""
    now = datetime(2026, 8, 3, 9, 27, 0)
    err = bm._window_error("2026-08-03", "auction", now)
    assert err and "09:25–09:30" in err
    now = datetime(2026, 8, 3, 11, 20, 0)  # 11:20 不在 noon（11:30 起）窗口内
    err = bm._window_error("2026-08-03", "noon", now)
    assert err and "建议切换为「上午盘中」" in err
    now = datetime(2026, 8, 3, 8, 0, 0)
    assert bm._window_error("2026-08-03", "close", now) is not None
    assert "早盘前决策" in bm._window_error("2026-08-03", "close", now)


def test_window_non_trading_day_400(fake_fetch, iso_dirs, monkeypatch):
    """非交易日（周日）选盘中模式 → 400 中文提示；非盘中模式与历史日期放行。"""
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 2, 10, 0, 0))  # 周日
    r = client.post("/api/reviews", json={"date": "2026-08-02", "mode": "intraday_am"})
    assert r.status_code == 400
    assert "非交易日" in r.json()["detail"]
    # 单元级：非盘中模式在其窗口内（如周日 16:00 close / 8:00 pre_market）放行；
    # 历史日期盘中模式放行
    assert bm._window_error("2026-08-02", "close", datetime(2026, 8, 2, 16, 0, 0)) is None
    assert bm._window_error("2026-08-02", "pre_market", datetime(2026, 8, 2, 8, 0, 0)) is None
    assert bm._window_error("2026-07-31", "intraday_am", datetime(2026, 8, 2, 10, 0, 0)) is None


def test_historical_intraday_allowed(fake_fetch, iso_dirs, monkeypatch):
    """历史日期补做任意模式：盘中模式也放行并完成任务。"""
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 9, 40, 0))
    r = client.post("/api/reviews", json={"date": "2020-01-02", "mode": "intraday_am", "max_rounds": 1})
    assert r.status_code == 200, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["meta"]["mode"] == "intraday_am"


# ── R11 R2：status 接口（自动复盘状态）──

def test_status_monday_morning(iso_dirs, monkeypatch):
    """周一 15:00 前 → latest_date = 上周五；未过 15:00 不可自动触发。"""
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 10, 0, 0))  # 周一
    r = client.get("/api/reviews/status", params={"date": "2026-08-03"})
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-08-03"
    assert body["is_trading_day"] is True
    assert body["latest_date"] == "2026-07-31"  # 上周五（跨周）
    assert body["latest_mode"] == "intraday_am"
    assert body["has_today_review"] is False
    assert body["pending_job_id"] is None
    assert body["can_auto"] is False


def test_status_after_close_can_auto(iso_dirs, monkeypatch):
    """交易日 15:00 后且无当日复盘/进行中任务 → latest_date=date 本身，can_auto=True。"""
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 16, 0, 0))  # 周一 16:00
    r = client.get("/api/reviews/status", params={"date": "2026-08-03"})
    body = r.json()
    assert body["latest_date"] == "2026-08-03"
    assert body["latest_mode"] == "close"
    assert body["has_today_review"] is False
    assert body["can_auto"] is True


def test_status_weekend(iso_dirs, monkeypatch):
    """非交易日（周六）→ 最近交易日（周五），can_auto=False。"""
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 8, 16, 0, 0))  # 周六
    r = client.get("/api/reviews/status", params={"date": "2026-08-08"})
    body = r.json()
    assert body["is_trading_day"] is False
    assert body["latest_date"] == "2026-08-07"  # 周五
    assert body["can_auto"] is False


def test_status_has_review_blocks_auto(iso_dirs, monkeypatch):
    """已有当日复盘 → has_today_review=True，can_auto=False。"""
    save_review(
        {
            "date": "2026-08-03",
            "time": "150500",
            "mode": "close",
            "mode_label": "收盘复盘",
            "summary": "当日收盘",
            "disclaimer": "仅供参考，不构成投资建议",
        },
        "# 当日报告",
    )
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 16, 0, 0))
    r = client.get("/api/reviews/status", params={"date": "2026-08-03"})
    body = r.json()
    assert body["has_today_review"] is True
    assert body["can_auto"] is False


def test_status_pending_job(iso_dirs, monkeypatch):
    """当天有进行中 job → pending_job_id 返回任务 id，can_auto=False。"""
    with bm._lock:
        bm.JOBS["r11_job_running"] = {
            "job_id": "r11_job_running",
            "status": "running",
            "stage": "fetch",
            "pct": 10,
            "message": "取数中",
            "analysts_done": 0,
            "analysts_total": 5,
            "result": None,
            "error": None,
            "_created": 0.0,
            "_finished": None,
            "_date": "2026-08-03",
        }
    try:
        monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 16, 0, 0))
        r = client.get("/api/reviews/status", params={"date": "2026-08-03"})
        body = r.json()
        assert body["pending_job_id"] == "r11_job_running"
        assert body["can_auto"] is False
        # 轮询契约不外泄内部字段（_date 不下发）
        job = client.get("/api/jobs/r11_job_running").json()
        assert not any(k.startswith("_") for k in job)
    finally:
        with bm._lock:
            bm.JOBS.pop("r11_job_running", None)


def test_status_validation():
    r = client.get("/api/reviews/status")
    assert r.status_code == 400 and "date" in r.json()["detail"]
    r = client.get("/api/reviews/status", params={"date": "2026/08/03"})
    assert r.status_code == 400 and "日期" in r.json()["detail"]
    r = client.get("/api/reviews/status", params={"date": "2026-13-99"})
    assert r.status_code == 400


def test_post_dedup_in_progress_job(fake_fetch, iso_dirs, monkeypatch):
    """R11 R4：进行中 job 存在时不重复创建（409 中文）。"""
    with bm._lock:
        bm.JOBS["r11_job_pending"] = {
            "job_id": "r11_job_pending",
            "status": "running",
            "stage": "fetch",
            "pct": 10,
            "message": "取数中",
            "analysts_done": 0,
            "analysts_total": 5,
            "result": None,
            "error": None,
            "_created": 0.0,
            "_finished": None,
            "_date": "2026-08-03",
        }
    try:
        monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 16, 0, 0))
        r = client.post("/api/reviews", json={"date": "2026-08-03", "mode": "close"})
        assert r.status_code == 409
        assert "进行中" in r.json()["detail"]
    finally:
        with bm._lock:
            bm.JOBS.pop("r11_job_pending", None)


# ── Langfuse trace 开关（2026-08-12：脚本批量跑 trace=false 跳过，页面默认 true）──

def test_review_trace_false_skips_langfuse(fake_fetch, iso_dirs, monkeypatch):
    """trace=false：复盘正常完成，但不调用 quality.trace_review/score_review。"""
    calls: list[str] = []
    import stock_review_crew.quality as quality_mod

    monkeypatch.setattr(
        quality_mod, "trace_review",
        lambda *a, **k: calls.append("trace") or "tr-off",
    )
    monkeypatch.setattr(
        quality_mod, "score_review",
        lambda *a, **k: calls.append("score") or {"score": 4, "passed": True},
    )
    r = client.post(
        "/api/reviews", json={"date": "2020-01-02", "mode": "close", "trace": False}
    )
    assert r.status_code == 200, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "done", job
    assert calls == [], f"trace=false 不应上报 Langfuse：{calls}"
    assert job["result"]["quality"] is None


def test_review_trace_default_true(fake_fetch, iso_dirs, monkeypatch):
    """不传 trace 字段（前端页面行为）：默认上报 Langfuse trace + score。"""
    calls: list[str] = []
    import stock_review_crew.quality as quality_mod

    monkeypatch.setattr(
        quality_mod, "trace_review",
        lambda *a, **k: calls.append("trace") or "tr-on",
    )
    monkeypatch.setattr(
        quality_mod, "score_review",
        lambda *a, **k: calls.append("score") or {"score": 4, "passed": True},
    )
    r = client.post("/api/reviews", json={"date": "2020-01-02", "mode": "close"})
    assert r.status_code == 200, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "done", job
    assert calls == ["trace", "score"], f"默认应上报 Langfuse：{calls}"
    assert job["result"]["quality"] == {"score": 4, "passed": True}


def test_review_job_langfuse_scope_flag(fake_fetch, iso_dirs, monkeypatch):
    """2026-08-12：前端复盘（默认 trace=true）job 内 LLM 回调开关为 True；
    脚本 trace=false 为 False；job 结束（主线程）复位为 False。"""
    import stock_review_crew.graph as graph_mod
    from stock_review_crew import config as cfg

    seen: list[bool] = []
    original_build = graph_mod.build_graph  # fake_fetch fixture 已替换为假图

    def wrapped(**kwargs):
        compiled = original_build(**kwargs)
        orig_invoke = compiled.invoke

        def invoke(initial):
            seen.append(cfg.langfuse_callbacks_enabled())
            return orig_invoke(initial)

        compiled.invoke = invoke
        return compiled

    monkeypatch.setattr(graph_mod, "build_graph", wrapped)

    # 前端页面：不传 trace → 默认 true → LLM 回调开启
    r = client.post("/api/reviews", json={"date": "2020-01-02", "mode": "close"})
    assert r.status_code == 200, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "done", job
    assert seen == [True], f"前端复盘应开启 LLM 回调：{seen}"

    # 脚本：trace=false → 回调关闭
    r = client.post("/api/reviews", json={"date": "2020-01-03", "mode": "close", "trace": False})
    assert r.status_code == 200, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "done", job
    assert seen == [True, False], f"trace=false 应关闭 LLM 回调：{seen}"

    # job 结束后开关复位（主线程上下文未被污染）
    assert cfg.langfuse_callbacks_enabled() is False


# ── 参数校验 400 ──

def test_invalid_date_mode_400():
    r = client.post("/api/reviews", json={"date": "2026-13-99", "mode": "close"})
    assert r.status_code == 400 and "日期" in r.json()["detail"]
    r = client.post("/api/reviews", json={"date": "2026/08/01", "mode": "close"})
    assert r.status_code == 400 and "日期格式" in r.json()["detail"]
    r = client.post("/api/reviews", json={"date": "2026-08-01", "mode": "bad_mode"})
    assert r.status_code == 400 and "无效模式" in r.json()["detail"]
    r = client.post("/api/reviews", json={"date": "2026-08-01"})
    assert r.status_code == 400 and "mode" in r.json()["detail"]
    r = client.post("/api/reviews", json={"date": "2026-08-01", "mode": "close", "max_rounds": "abc"})
    assert r.status_code == 400 and "max_rounds" in r.json()["detail"]
    r = client.post("/api/reviews", json={"date": "2026-08-01", "mode": "close", "trace": "false"})
    assert r.status_code == 400 and "trace" in r.json()["detail"]
    r = client.post("/api/reviews", json=[1, 2])
    assert r.status_code == 400 and "JSON 对象" in r.json()["detail"]


def test_job_not_found_404():
    r = client.get("/api/jobs/doesnotexist")
    assert r.status_code == 404
    assert r.json()["detail"] == "任务不存在"


# ── 健壮性：异常 → status=error 中文（绝不 500 崩线程）──

def test_job_error_chinese(fake_fetch, iso_dirs, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("模拟引擎故障")

    monkeypatch.setattr(graph, "build_graph", boom)
    r = client.post("/api/reviews", json={"date": "2020-01-02", "mode": "close"})
    assert r.status_code == 200
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "error"
    assert job["message"].startswith("复盘失败")
    assert "模拟引擎故障" in job["error"]
    assert job["result"] is None


# ── JOBS TTL / 上限 ──

def _make_job(jid: str, status: str, finished: float) -> dict:
    return {
        "job_id": jid,
        "status": status,
        "stage": "done" if status == "done" else "trend",
        "pct": 100.0 if status == "done" else 20.0,
        "message": "完成" if status == "done" else "进行中",
        "analysts_done": 5,
        "analysts_total": 5,
        "result": None,
        "error": None,
        "_created": 0.0,
        "_finished": finished,
    }


def test_jobs_ttl_and_cap(monkeypatch):
    monkeypatch.setattr(bm, "JOB_TTL_SECONDS", 3600)
    monkeypatch.setattr(bm, "MAX_JOBS", 50)
    with bm._lock:
        bm.JOBS.clear()
        for i in range(55):
            bm.JOBS[f"t{i:02d}"] = _make_job(f"t{i:02d}", "done", float(i))
        bm.JOBS["running1"] = _make_job("running1", "running", None)
    bm._prune_jobs(now=1000.0)
    with bm._lock:
        ids = set(bm.JOBS)
    assert "running1" in ids  # 进行中不删
    assert len(ids) == 51  # 保留最近 50 条终态 + 1 条进行中
    assert "t00" not in ids and "t04" not in ids  # 最旧 5 条被淘汰
    assert "t54" in ids

    # TTL：终态超时移除，进行中保留
    monkeypatch.setattr(bm, "JOB_TTL_SECONDS", 1)
    bm._prune_jobs(now=1000.0)
    with bm._lock:
        assert set(bm.JOBS) == {"running1"}
    r = client.get("/api/jobs/running1")
    assert r.status_code == 200
    r = client.get("/api/jobs/t54")
    assert r.status_code == 404
    assert r.json()["detail"] == "任务不存在"


# ── 上下文注入端点 ──

def test_review_context_endpoint(iso_dirs):
    save_review(
        {
            "date": "2026-07-31",
            "time": "150500",
            "mode": "close",
            "mode_label": "收盘复盘",
            "summary": "昨日收盘摘要",
            "disclaimer": "仅供参考，不构成投资建议",
        },
        "# 昨日报告",
    )
    save_review(
        {
            "date": "2026-08-03",
            "time": "103000",
            "mode": "intraday_am",
            "mode_label": "上午盘中",
            "summary": "今日上午摘要",
            "disclaimer": "仅供参考，不构成投资建议",
        },
        "# 上午复盘",
    )
    r = client.get("/api/reviews/context", params={"date": "2026-08-03", "mode": "intraday_am"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"yesterday", "earlier_today"}
    assert body["yesterday"]["summary"] == "昨日收盘摘要"
    assert body["earlier_today"][0]["summary"] == "今日上午摘要"
    assert body["earlier_today"][0]["time"] == "103000"

    r = client.get("/api/reviews/context", params={"date": "not-a-date"})
    assert r.status_code == 400 and "日期" in r.json()["detail"]
    r = client.get("/api/reviews/context", params={"mode": "close"})
    assert r.status_code == 400 and "date" in r.json()["detail"]
    r = client.get("/api/reviews/context", params={"date": "2026-08-03", "mode": "bad"})
    assert r.status_code == 400 and "无效模式" in r.json()["detail"]
