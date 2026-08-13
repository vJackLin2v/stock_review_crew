# -*- coding: utf-8 -*-
"""I4 历史存储单元测试（R11 R4：每日一份 + 覆盖前归档 + 旧结构兼容）。

data/reviews 读写删 / 列表（每日一条）/ 上下文注入 / 路径安全；
全部测试通过 STOCK_REVIEW_DATA_DIR / STOCK_REVIEW_ARCHIVE_DIR 指向临时目录隔离，
绝不触碰仓库 data/ 与 output/。
"""

import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_review_crew.storage.reviews import (  # noqa: E402
    DEFAULT_DISCLAIMER,
    MODE_LABELS,
    archive_root,
    context,
    delete_review,
    get_review,
    has_review,
    list_reviews,
    reviews_root,
    save_review,
)


@pytest.fixture()
def iso_dir():
    """隔离临时目录。

    注意：本环境（沙箱）下以 0o700 权限位创建的目录后续不可再访问
    （pytest 的 tmp_path 正是如此），因此用默认权限位自建并事后清理。
    """
    base = Path(__file__).resolve().parent.parent
    root = base / f".iso_{uuid.uuid4().hex[:12]}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def store(iso_dir, monkeypatch):
    """隔离存储根目录 + 归档根目录。"""
    root = iso_dir / "reviews"
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(root))
    monkeypatch.setenv("STOCK_REVIEW_ARCHIVE_DIR", str(iso_dir / "archive"))
    return root


def _meta(date="2026-07-31", time="110150", mode="intraday_am", **extra):
    meta = {
        "date": date,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "time": time,
        "created_at": "2026-07-31T11:01:50",
        "degraded": [],
        "sources": ["test"],
        "disclaimer": DEFAULT_DISCLAIMER,
        "summary": "测试摘要",
    }
    meta.update(extra)
    return meta


EMPTY_DETAIL = {"meta": {}, "report": "", "analyses": [], "debate_history": [], "snapshot": {}}


def _write_legacy_entry(root: Path, date: str, time: str, meta: dict, report="旧版报告") -> None:
    """手工构造旧结构（R11 前同日多份）条目：{date}/{time}/meta.json 等。"""
    entry = root / date / time
    entry.mkdir(parents=True)
    (entry / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (entry / "report.json").write_text(
        json.dumps({"final_report": report, "analysts": [{"name": "阿狼"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (entry / "snapshot.json").write_text(json.dumps({"index": {"close": 3832.26}}, ensure_ascii=False), encoding="utf-8")


def test_save_and_get_roundtrip(store):
    meta = _meta()
    report = {"final_report": "# 测试报告", "analysts": [{"name": "阿狼"}], "debate_history": [{"round": 1}]}
    snapshot = {"index": {"close": 3832.26}}
    record_id = save_review(meta, report, snapshot)

    # R11 R4：record_id = "{date}"（每日一份，不再含 HHMMSS）
    assert record_id == "2026-07-31"
    entry = store / "2026-07-31"
    assert (entry / "meta.json").is_file()
    assert (entry / "report.json").is_file()
    assert (entry / "snapshot.json").is_file()

    detail = get_review("2026-07-31")
    assert detail["meta"]["date"] == "2026-07-31"
    assert detail["meta"]["summary"] == "测试摘要"
    assert detail["report"] == "# 测试报告"
    assert detail["analyses"] == [{"name": "阿狼"}]
    assert detail["debate_history"] == [{"round": 1}]
    assert detail["snapshot"] == {"index": {"close": 3832.26}}

    stored_meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
    for key in ("date", "mode", "mode_label", "time", "created_at", "degraded", "sources", "disclaimer", "summary"):
        assert key in stored_meta, f"meta 缺少契约键：{key}"


def test_save_string_report_and_defaults(store):
    record_id = save_review({"date": "2026-07-31", "time": "093000"}, "# 纯文本报告")
    assert record_id == "2026-07-31"
    detail = get_review("2026-07-31")
    assert detail["report"] == "# 纯文本报告"
    assert detail["analyses"] == []
    assert detail["meta"]["mode"] == "close"
    assert detail["meta"]["mode_label"] == "收盘复盘"
    assert detail["meta"]["disclaimer"] == DEFAULT_DISCLAIMER
    assert detail["meta"]["degraded"] == []
    assert detail["meta"]["sources"] == []


def test_save_none_snapshot_writes_empty(store):
    save_review(_meta(time="100000"), "报告", None)
    snapshot = json.loads((store / "2026-07-31" / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot == {}


def test_daily_overwrite_archives_previous(store):
    """R11 R4：同日期再次保存 = 覆盖写，覆盖前旧版完整目录归档到 output/archive。"""
    save_review(_meta(summary="第一版", time="150500"), {"final_report": "# 第一版"}, {"index": 1})
    save_review(_meta(summary="第二版", time="160000"), {"final_report": "# 第二版"}, {"index": 2})

    # 当日目录只剩一份（第二版）
    detail = get_review("2026-07-31")
    assert detail["meta"]["summary"] == "第二版"
    assert detail["report"] == "# 第二版"
    assert detail["snapshot"] == {"index": 2}
    assert not (store / "2026-07-31" / "150500").exists()  # 旧时间点子目录不存在

    # 归档目录：output/archive/{date}_{HHMMSS}/ 含旧版完整目录（meta/report/snapshot）
    archive_root_dir = store.parent / "archive"
    archives = sorted(archive_root_dir.iterdir())
    assert len(archives) == 1
    assert archives[0].name.startswith("2026-07-31_") and archives[0].name.split("_", 1)[1]
    assert (archives[0] / "meta.json").is_file()
    assert (archives[0] / "report.json").is_file()
    assert (archives[0] / "snapshot.json").is_file()
    old_meta = json.loads((archives[0] / "meta.json").read_text(encoding="utf-8"))
    assert old_meta["summary"] == "第一版"
    old_report = json.loads((archives[0] / "report.json").read_text(encoding="utf-8"))
    assert old_report["final_report"] == "# 第一版"
    # 新 meta 记录归档去向
    assert json.loads((store / "2026-07-31" / "meta.json").read_text(encoding="utf-8"))["archived_to"]


def test_overwrite_archives_legacy_structure(store):
    """R11 R4：旧结构（多份子目录）日期被覆盖时，整日目录归档到 output/archive。"""
    _write_legacy_entry(store, "2026-07-30", "103813", _meta(date="2026-07-30", time="103813", summary="旧上午"))
    _write_legacy_entry(store, "2026-07-30", "150500", _meta(date="2026-07-30", time="150500", summary="旧收盘"))
    save_review(_meta(date="2026-07-30", time="160000", summary="新每日一份"))

    assert get_review("2026-07-30")["meta"]["summary"] == "新每日一份"
    assert not (store / "2026-07-30" / "103813").exists()  # 旧子目录已清理
    archives = sorted((store.parent / "archive").iterdir())
    assert len(archives) == 1
    assert (archives[0] / "103813" / "meta.json").is_file()  # 旧结构整目录归档
    assert (archives[0] / "150500" / "meta.json").is_file()


def test_overwrite_archive_failure_records_degraded_note(store, monkeypatch):
    """R4：归档失败不阻塞主流程，meta.degraded 记录 degraded_note（中文）。"""
    save_review(_meta(summary="旧版"), "报告A")

    def boom(*args, **kwargs):
        raise OSError("磁盘只读（测试模拟）")

    monkeypatch.setattr("stock_review_crew.storage.reviews.shutil.copytree", boom)
    record_id = save_review(_meta(summary="新版"), "报告B")
    assert record_id == "2026-07-31"
    meta = json.loads((store / "2026-07-31" / "meta.json").read_text(encoding="utf-8"))
    assert meta["summary"] == "新版"
    assert any("覆盖前归档失败" in d for d in meta["degraded"])
    assert get_review("2026-07-31")["report"] == "报告B"
    # 归档目录为空（失败未产生归档）
    assert not (store.parent / "archive").exists() or not any((store.parent / "archive").iterdir())


def test_save_defaults_date_time_now(store):
    record_id = save_review({})
    assert record_id == datetime_now_date()
    assert (store / record_id).is_dir()
    meta = json.loads((store / record_id / "meta.json").read_text(encoding="utf-8"))
    assert len(meta["time"]) == 6 and meta["time"].isdigit()


def datetime_now_date() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")


def test_save_invalid_meta_raises_chinese(store):
    with pytest.raises(ValueError, match="日期格式"):
        save_review({"date": "2026/07/31", "time": "110150"})
    with pytest.raises(ValueError, match="时间格式"):
        save_review({"date": "2026-07-31", "time": "11:01:50"})


def test_list_grouping_and_order(store):
    save_review(_meta(date="2026-07-31", time="180502", mode="close", summary="收盘"))
    save_review(_meta(date="2026-07-30", time="170431", mode="close", summary="昨日"))

    groups = list_reviews()
    assert [g["date"] for g in groups] == ["2026-07-31", "2026-07-30"]
    # R11 R4：每日一条（record_id = "{date}"，不含时间点）
    assert len(groups[0]["items"]) == 1
    item = groups[0]["items"][0]
    assert set(item) == {"record_id", "mode", "mode_label", "time", "created_at", "summary"}
    assert item["record_id"] == "2026-07-31"
    assert item["time"] == "180502"
    assert item["mode"] == "close"
    assert item["mode_label"] == "收盘复盘"
    assert item["summary"] == "收盘"


def test_list_empty_when_no_root(store):
    assert list_reviews() == []


def test_list_corrupt_meta_fallback(store):
    entry = store / "2026-07-31"
    entry.mkdir(parents=True)
    (entry / "meta.json").write_text("{broken", encoding="utf-8")
    groups = list_reviews()
    assert len(groups) == 1
    item = groups[0]["items"][0]
    assert item["record_id"] == "2026-07-31"
    assert item["time"] == "000000"
    assert item["mode"] == "close"
    assert item["mode_label"] == "收盘复盘"


def test_get_missing_and_corrupt(store):
    assert get_review("2026-07-31") == EMPTY_DETAIL

    entry = store / "2026-07-31"
    entry.mkdir(parents=True)
    (entry / "meta.json").write_text("{bad", encoding="utf-8")
    (entry / "report.json").write_text("[1,2", encoding="utf-8")
    (entry / "snapshot.json").write_text("null", encoding="utf-8")
    detail = get_review("2026-07-31")
    assert detail["meta"] == {}
    assert detail["report"] == ""
    assert detail["snapshot"] == {}
    assert detail["analyses"] == []
    assert detail["debate_history"] == []

    # report.json 为裸字符串时按 final_report 返回
    (entry / "report.json").write_text(json.dumps("裸字符串报告", ensure_ascii=False), encoding="utf-8")
    assert get_review("2026-07-31")["report"] == "裸字符串报告"


def test_legacy_structure_read_compat(store):
    """R11 R4 历史兼容：新结构不存在时兼容读旧结构（取时间点最新一份）展示。"""
    _write_legacy_entry(store, "2026-07-31", "103813", _meta(time="103813", summary="上午"))
    _write_legacy_entry(store, "2026-07-31", "150500", _meta(time="150500", summary="收盘"))

    # 列表：每日一条，取时间点最新一份（record_id 归并为 date）
    groups = list_reviews()
    assert len(groups) == 1
    item = groups[0]["items"][0]
    assert item["record_id"] == "2026-07-31"
    assert item["time"] == "150500"
    assert item["summary"] == "收盘"

    # 详情：无 time 参数 → 最新一份；指定 time → 对应旧条目
    detail = get_review("2026-07-31")
    assert detail["report"] == "旧版报告"
    assert detail["meta"]["summary"] == "收盘"
    older = get_review("2026-07-31", "103813")
    assert older["meta"]["summary"] == "上午"

    # has_review 兼容旧结构
    assert has_review("2026-07-31") is True


def test_delete_review(store):
    save_review(_meta(time="110150"))
    assert (store / "2026-07-31").is_dir()
    assert delete_review("2026-07-31") is True
    assert not (store / "2026-07-31").exists()
    assert delete_review("2026-07-31") is False
    assert list_reviews() == []


def test_delete_review_legacy_entry(store):
    _write_legacy_entry(store, "2026-07-31", "103813", _meta(time="103813"))
    _write_legacy_entry(store, "2026-07-31", "150500", _meta(time="150500"))
    assert delete_review("2026-07-31", "103813") is True
    assert not (store / "2026-07-31" / "103813").exists()
    assert (store / "2026-07-31" / "150500").is_dir()
    assert delete_review("2026-07-31") is True  # 整日删除
    assert not (store / "2026-07-31").exists()


def test_has_review(store):
    assert has_review("2026-07-31") is False
    assert has_review("bad-date") is False
    save_review(_meta())
    assert has_review("2026-07-31") is True
    delete_review("2026-07-31")
    assert has_review("2026-07-31") is False


@pytest.mark.parametrize(
    "date,time",
    [
        ("../../etc", "110150"),
        ("2026-07-31", ".."),
        ("2026-07-31", "../secret"),
        ("2026-07-31", "110150/../x"),
        ("2026-13-99", "110150"),
        ("2026-02-30", "110150"),
        ("2026-07-31", "999999"),
        ("2026-07-31", "123456-0"),
        ("2026-07-31", "12345"),
        ("2026-07-31", "123456-"),
        ("2026-07-31", "123456-abc"),
    ],
)
def test_invalid_ids_rejected(date, time, store):
    assert get_review(date, time) == EMPTY_DETAIL
    assert delete_review(date, time) is False


def test_context_empty(store):
    assert context("2026-07-31") == {"yesterday": None, "earlier_today": []}
    assert context("bad-date") == {"yesterday": None, "earlier_today": []}
    assert context("2026-13-01") == {"yesterday": None, "earlier_today": []}


def test_context_yesterday_close_preferred(store):
    save_review(_meta(date="2026-07-30", time="143000", mode="intraday_pm", summary="午后"))
    save_review(_meta(date="2026-07-30", time="150500", mode="close", summary="收盘"))
    save_review(_meta(date="2026-07-29", time="160000", mode="close", summary="更早收盘"))
    save_review(_meta(date="2026-07-31", time="103813", mode="intraday_am", summary="当日上午"))

    ctx = context("2026-07-31")
    assert ctx["yesterday"]["date"] == "2026-07-30"
    assert ctx["yesterday"]["time"] == "150500"
    assert ctx["yesterday"]["mode"] == "close"
    assert ctx["yesterday"]["summary"] == "收盘"
    assert [i["time"] for i in ctx["earlier_today"]] == ["103813"]
    assert all(i["date"] == "2026-07-31" for i in ctx["earlier_today"])
    assert ctx["earlier_today"][0]["record_id"] == "2026-07-31"


def test_context_legacy_earlier_today_multiple(store):
    """旧结构同日期多份：earlier_today 保留全部时间点（倒序），yesterday 每日一条。"""
    _write_legacy_entry(store, "2026-07-31", "103813", _meta(time="103813", summary="旧上午"))
    _write_legacy_entry(store, "2026-07-31", "150500", _meta(time="150500", summary="旧收盘"))
    ctx = context("2026-07-31")
    assert [i["time"] for i in ctx["earlier_today"]] == ["150500", "103813"]
    assert ctx["yesterday"] is None


def test_context_yesterday_fallback_any_mode(store):
    save_review(_meta(date="2026-07-30", time="143000", mode="intraday_pm", summary="午后"))
    save_review(_meta(date="2026-07-28", time="160000", mode="close", summary="更早的收盘"))
    ctx = context("2026-07-31")
    # close 全局优先：最近日期无 close 时，取更早日期上的最近 close
    assert ctx["yesterday"]["mode"] == "close"
    assert ctx["yesterday"]["date"] == "2026-07-28"
    assert ctx["yesterday"]["time"] == "160000"

    # 早于该日期全部无 close → 任意模式兜底
    delete_review("2026-07-28")
    ctx2 = context("2026-07-31")
    assert ctx2["yesterday"]["mode"] == "intraday_pm"
    assert ctx2["yesterday"]["time"] == "143000"


def test_context_skips_same_date_and_corrupt(store):
    save_review(_meta(date="2026-07-31", time="110150", mode="close", summary="当日"))
    ctx = context("2026-07-31")
    assert ctx["yesterday"] is None
    assert len(ctx["earlier_today"]) == 1

    # 昨日 meta 损坏：兜底仍返回该条目（mode 取 close）
    entry = store / "2026-07-30"
    entry.mkdir(parents=True)
    (entry / "meta.json").write_text("x", encoding="utf-8")
    ctx = context("2026-07-31")
    assert ctx["yesterday"]["time"] == "000000"
    assert ctx["yesterday"]["mode"] == "close"


def test_roots_default_and_isolation(store):
    """隔离验证：仓库 data/reviews 与 output/archive 均不被测试写入。"""
    repo = Path(__file__).resolve().parent.parent
    repo_reviews = repo / "data" / "reviews"
    repo_archive = repo / "output" / "archive"
    before_r = {p.name for p in repo_reviews.iterdir()} if repo_reviews.is_dir() else None
    before_a = {p.name for p in repo_archive.iterdir()} if repo_archive.is_dir() else None

    save_review(_meta())
    save_review(_meta(summary="覆盖"))  # 触发归档
    assert reviews_root() == store
    assert archive_root() == store.parent / "archive"
    assert (store.parent / "archive").is_dir() and any((store.parent / "archive").iterdir())

    after_r = {p.name for p in repo_reviews.iterdir()} if repo_reviews.is_dir() else None
    after_a = {p.name for p in repo_archive.iterdir()} if repo_archive.is_dir() else None
    assert after_r == before_r, "隔离失败：仓库 data/reviews 不应被本测试写入"
    assert after_a == before_a, "隔离失败：仓库 output/archive 不应被本测试写入"

    # 默认根目录契约（环境变量未设置时）：output/archive 为归档保留区
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("STOCK_REVIEW_ARCHIVE_DIR", raising=False)
    try:
        assert archive_root() == repo / "output" / "archive"
    finally:
        monkeypatch.undo()
