# -*- coding: utf-8 -*-
"""R11 R2/R4 验收用例（T2 自动复盘后端 Agent）：

- R2 status 接口：latest_date 跨周/非交易日/15:00 边界、has_today_review、
  can_auto、pending_job_id；
- R4 每日一份存储：旧多份结构可读（列表/详情兼容）、新结构覆盖+归档成功、
  归档目录落在 output/archive/（环境变量隔离）且不污染仓库 data/；
- POST 并发去重（已有 done 记录 409 / 进行中 job 409）；
- job result.record_id = "{date}"（新契约）。

离线确定性：DEEPSEEK_API_KEY 置空 + 引擎取数/LLM 全 mock；存储与归档走临时目录。
"""

import json
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


class _FakeLLM:
    def __init__(self, factory):
        self._factory = factory

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        return types.SimpleNamespace(content=self._factory(messages))


def _llm_reply(messages):
    """按消息内容确定性返回 mock 回复（2026-08-10 契约：LLM 不可用时复盘直接终止，
    因此测 job 链路的用例必须让 LLM 可用）。"""
    user = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            user = m.get("content", "")
    if "报告要求" in user:
        return "# 2026-08-03 A股收盘复盘报告\n## 一、市场概况\n略\n\n> 仅供参考，不构成投资建议"
    if "实质性分歧" in user:
        return "分歧判断：无\n总结：观点一致，无需辩论。"
    return "## 1、昨日复盘验证\n无昨日报告，跳过验证\n## 2、今日判断\n看多\n## 3、操作计划\n进攻"


@pytest.fixture(autouse=True)
def no_llm_key():
    had_key = "DEEPSEEK_API_KEY" in os.environ
    old_key = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = ""
    yield
    if had_key:
        os.environ["DEEPSEEK_API_KEY"] = old_key or ""
    else:
        os.environ.pop("DEEPSEEK_API_KEY", None)


@pytest.fixture()
def iso_dirs(monkeypatch):
    """隔离存储目录（统一 env=数据根语义）。

    STOCK_REVIEW_DATA_DIR=base（数据根），reviews_root() 拼 /reviews 即 base/reviews，
    正好是 tests 期望的 reviews 产物根（iso_dirs["reviews"]）。
    """
    base = Path(__file__).resolve().parent.parent / ".tmp" / f"r11_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    reviews_dir = base / "reviews"
    archive_dir = base / "archive"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(base))
    monkeypatch.setenv("STOCK_REVIEW_ARCHIVE_DIR", str(archive_dir))
    monkeypatch.setenv("CHATS_DATA_DIR", str(base / "chats"))
    yield {"base": base, "reviews": reviews_dir, "archive": archive_dir}
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(graph, "_llm_ready", lambda: True)
    llm = _FakeLLM(_llm_reply)
    strict = _FakeLLM(_llm_reply)
    monkeypatch.setattr(graph, "_llm_pair", lambda: (llm, strict))
    monkeypatch.setattr(graph, "_callbacks", lambda: [])
    monkeypatch.setattr(graph, "_load_analyst_tools", lambda: (None, None))
    monkeypatch.setattr(graph, "_legacy_fetch", lambda state: None)
    monkeypatch.setattr(chat, "_get_default_llm", lambda: None)
    monkeypatch.setattr(
        chat, "_fetch_target_data",
        lambda target_type, target: {"available": False, "note": "数据暂时不可用（离线测试）", "data": None},
    )
    monkeypatch.setattr(chat, "search_chat_history", lambda q, n_results=5: {"available": False, "data": None})
    monkeypatch.setattr(chat, "save_chat_history", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        chat.ChatEngine, "_search_review",
        lambda self, q: {"available": False, "note": "复盘检索不可用（离线测试）", "data": None},
    )


def _fake_mode_data(mode: str, date: str) -> dict:
    return {
        "date": date,
        "mode": mode,
        "mode_label": bm.MODE_LABELS.get(mode, mode),
        "blocks": {
            "index_trend": {"indices": {"shanghai": {"close": 3764.0}}, "source": "测试", "degraded": False},
            "minute": {"symbol": "sh000001", "bars": [{"time": "09:30", "close": 3760.0}], "source": "测试", "degraded": False},
            "sectors": {"top5": [], "bottom5": [], "source": "测试", "degraded": False},
            "zt_pool": {"limit_up": {"count": 1, "tier": {}}, "limit_down": {"count": 0, "tier": {}}, "source": "测试", "degraded": False},
            "breadth": {"up": 1, "down": 1, "distribution": {}, "source": "测试", "degraded": False},
            "sentiment": {"up_count": 1, "down_count": 1, "source": "测试", "degraded": False},
            "news": {"caixin": [], "cctv": [], "source": "测试"},
            "lhb": {"stocks": [], "source": "数据缺失", "degraded": True, "degraded_reason": ["示例"]},
        },
        "degraded": ["lhb: 示例"],
        "degraded_flag": True,
    }


@pytest.fixture()
def fake_fetch(monkeypatch):
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


def _write_legacy(root: Path, date: str, time: str, summary: str, mode: str = "close") -> None:
    """构造旧结构（R11 前同日多份）：{date}/{time}/meta.json 等。"""
    entry = root / date / time
    entry.mkdir(parents=True)
    meta = {
        "date": date,
        "mode": mode,
        "mode_label": bm.MODE_LABELS.get(mode, mode),
        "time": time,
        "created_at": f"{date}T{time}",
        "degraded": [],
        "sources": ["legacy"],
        "disclaimer": "仅供参考，不构成投资建议",
        "summary": summary,
    }
    (entry / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (entry / "report.json").write_text(
        json.dumps({"final_report": f"# {date} {time} 报告", "analysts": [{"name": "阿狼"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (entry / "snapshot.json").write_text(json.dumps({"legacy": True}), encoding="utf-8")


# ── R2：latest_date 判定（单元表驱动，含跨周/非交易日/15:00 边界）──

@pytest.mark.parametrize(
    "date,now,expected",
    [
        # 周一 15:00 前 = 上周五（跨周）
        ("2026-08-03", datetime(2026, 8, 3, 10, 0, 0), "2026-07-31"),
        ("2026-08-03", datetime(2026, 8, 3, 14, 59, 0), "2026-07-31"),
        # 周一 15:00 整/后 = 周一本身（交易日）
        ("2026-08-03", datetime(2026, 8, 3, 15, 0, 0), "2026-08-03"),
        ("2026-08-03", datetime(2026, 8, 3, 16, 0, 0), "2026-08-03"),
        # 周五 15:00 前 → 周四；15:00 后 → 周五
        ("2026-08-07", datetime(2026, 8, 7, 9, 30, 0), "2026-08-06"),
        ("2026-08-07", datetime(2026, 8, 7, 15, 0, 0), "2026-08-07"),
        # 非交易日（周六/周日）→ 最近交易日（周五）
        ("2026-08-08", datetime(2026, 8, 8, 16, 0, 0), "2026-08-07"),
        ("2026-08-09", datetime(2026, 8, 9, 10, 0, 0), "2026-08-07"),
        # date 非当日 → 严格早于 date 的最近交易日
        ("2026-08-03", datetime(2026, 8, 8, 16, 0, 0), "2026-07-31"),
    ],
)
def test_resolve_latest_date(date, now, expected):
    assert bm._resolve_latest_date(date, now) == expected


def test_is_trading_day():
    assert bm._is_trading_day(datetime(2026, 8, 3, 12, 0)) is True   # 周一
    assert bm._is_trading_day(datetime(2026, 8, 7, 12, 0)) is True   # 周五
    assert bm._is_trading_day(datetime(2026, 8, 8, 12, 0)) is False  # 周六
    assert bm._is_trading_day(datetime(2026, 8, 9, 12, 0)) is False  # 周日


# ── R2：status 接口端到端 ──

def test_status_endpoint_contract(iso_dirs, monkeypatch):
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 15, 30, 0))
    r = client.get("/api/reviews/status", params={"date": "2026-08-03"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "date", "is_trading_day", "latest_date", "latest_mode",
        "has_today_review", "can_auto", "pending_job_id",
    }
    assert body == {
        "date": "2026-08-03",
        "is_trading_day": True,
        "latest_date": "2026-08-03",
        "latest_mode": "close",
        "has_today_review": False,
        "can_auto": True,
        "pending_job_id": None,
    }


def test_status_done_job_no_pending(iso_dirs, monkeypatch):
    """已完成的 job 不再算 pending（内存态查找仅 queued/running）。"""
    with bm._lock:
        bm.JOBS["r11_done_job"] = {
            "job_id": "r11_done_job", "status": "done", "stage": "done", "pct": 100,
            "message": "完成", "analysts_done": 6, "analysts_total": 6,
            "result": None, "error": None, "_created": 0.0, "_finished": 1.0, "_date": "2026-08-03",
        }
    try:
        monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 15, 30, 0))
        body = client.get("/api/reviews/status", params={"date": "2026-08-03"}).json()
        assert body["pending_job_id"] is None
        assert body["can_auto"] is True  # done 未落盘（本测试无存储记录）→ 可自动触发
    finally:
        with bm._lock:
            bm.JOBS.pop("r11_done_job", None)


# ── R4：每日一份 + 覆盖前归档（验收路径）──

def test_daily_overwrite_archive_acceptance(iso_dirs, monkeypatch):
    """新结构覆盖+归档：归档目录落在 archive 根（默认 output/archive），
    仓库 data/ 不被污染；API 列表/详情始终每日一条。"""
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 16, 0, 0))
    save_review(
        {"date": "2026-08-03", "time": "150500", "mode": "close", "mode_label": "收盘复盘", "summary": "第一版"},
        "# 第一版报告",
        {"index": {"close": 1}},
    )
    save_review(
        {"date": "2026-08-03", "time": "161000", "mode": "close", "mode_label": "收盘复盘", "summary": "第二版"},
        "# 第二版报告",
        {"index": {"close": 2}},
    )

    # 归档：archive_root/{date}_{HHMMSS}/ 含旧版完整目录（meta/report/snapshot）
    archives = sorted((iso_dirs["archive"]).iterdir())
    assert len(archives) == 1
    assert archives[0].name.startswith("2026-08-03_")
    assert (archives[0] / "meta.json").is_file()
    assert (archives[0] / "report.json").is_file()
    assert (archives[0] / "snapshot.json").is_file()
    assert json.loads((archives[0] / "meta.json").read_text(encoding="utf-8"))["summary"] == "第一版"

    # API：列表每日一条、详情为最新覆盖版本
    groups = client.get("/api/reviews").json()
    assert [g["date"] for g in groups] == ["2026-08-03"]
    assert len(groups[0]["items"]) == 1
    assert groups[0]["items"][0]["record_id"] == "2026-08-03"
    detail = client.get("/api/reviews/2026-08-03").json()
    assert detail["report"] == "# 第二版报告"
    assert detail["meta"]["summary"] == "第二版"
    assert detail["snapshot"] == {"index": {"close": 2}}

    # status：has_today_review=True → can_auto=False
    body = client.get("/api/reviews/status", params={"date": "2026-08-03"}).json()
    assert body["has_today_review"] is True
    assert body["can_auto"] is False


def test_legacy_structure_readable_via_api(iso_dirs, monkeypatch):
    """旧多份结构可读：列表归并为每日一条（取最新时间点），详情兼容展示。"""
    _write_legacy(iso_dirs["reviews"], "2026-08-03", "103813", "上午旧复盘", mode="intraday_am")
    _write_legacy(iso_dirs["reviews"], "2026-08-03", "150500", "收盘旧复盘", mode="close")

    groups = client.get("/api/reviews").json()
    assert len(groups) == 1
    item = groups[0]["items"][0]
    assert item["record_id"] == "2026-08-03"  # 新契约 record_id = date
    assert item["time"] == "150500"  # 取时间点最新一份

    detail = client.get("/api/reviews/2026-08-03").json()
    assert detail["report"] == "# 2026-08-03 150500 报告"
    assert detail["meta"]["summary"] == "收盘旧复盘"
    assert detail["analyses"] == [{"name": "阿狼"}]

    # status 同样识别旧结构
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 16, 0, 0))
    body = client.get("/api/reviews/status", params={"date": "2026-08-03"}).json()
    assert body["has_today_review"] is True
    assert body["can_auto"] is False


def test_api_delete_legacy_date_whole(iso_dirs):
    """DELETE /api/reviews/{date}：旧多份结构整日删除；再删 404 中文。"""
    _write_legacy(iso_dirs["reviews"], "2026-08-03", "103813", "上午")
    _write_legacy(iso_dirs["reviews"], "2026-08-03", "150500", "收盘")
    r = client.delete("/api/reviews/2026-08-03")
    assert r.status_code == 204
    assert r.content == b""
    assert not (iso_dirs["reviews"] / "2026-08-03").exists()
    r = client.delete("/api/reviews/2026-08-03")
    assert r.status_code == 404
    assert "复盘记录不存在" in r.json()["detail"]


def test_repo_data_and_output_not_polluted(iso_dirs):
    """验收：归档/存储全部落在隔离目录，仓库 data/reviews 与 output/archive 不变。"""
    repo = Path(__file__).resolve().parent.parent
    before_r = {p.name for p in (repo / "data" / "reviews").iterdir()} if (repo / "data" / "reviews").is_dir() else None
    before_o = {p.name for p in (repo / "output" / "archive").iterdir()} if (repo / "output" / "archive").is_dir() else None

    save_review({"date": "2026-08-03", "time": "150500", "mode": "close", "summary": "A"}, "报告A")
    save_review({"date": "2026-08-03", "time": "161000", "mode": "close", "summary": "B"}, "报告B")  # 覆盖+归档

    after_r = {p.name for p in (repo / "data" / "reviews").iterdir()} if (repo / "data" / "reviews").is_dir() else None
    after_o = {p.name for p in (repo / "output" / "archive").iterdir()} if (repo / "output" / "archive").is_dir() else None
    assert after_r == before_r
    assert after_o == before_o


def test_job_result_record_id_is_date(fake_fetch, iso_dirs, monkeypatch):
    """job 完成：result.record_id = "{date}"（新契约，前端按此适配）。"""
    monkeypatch.setattr(bm, "_now", lambda: datetime(2026, 8, 3, 16, 0, 0))
    r = client.post("/api/reviews", json={"date": "2020-01-02", "mode": "close", "max_rounds": 1})
    assert r.status_code == 200, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["record_id"] == "2020-01-02"
    assert job["result"]["meta"]["date"] == "2020-01-02"
    assert re.fullmatch(r"\d{6}", str(job["result"]["meta"]["time"]))
    # 落盘位置：每日一份
    assert (iso_dirs["reviews"] / "2020-01-02" / "meta.json").is_file()
