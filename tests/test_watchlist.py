"""R10 观察列表存储层测试（watchlist.py，契约 requirements-r10.md §三/§五）。"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from stock_review_crew.storage import watchlist


@pytest.fixture()
def iso_root(monkeypatch):
    root = Path(__file__).resolve().parent.parent / ".tmp" / "r10_watchlist_test"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(root))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_add_and_list(iso_root):
    rec = watchlist.add_watchlist("600519", note="观察")
    assert rec["code"] == "600519"
    assert rec["note"] == "观察"
    assert rec["pinned"] is False
    assert rec["created_at"]
    rows = watchlist.list_watchlist()
    assert len(rows) == 1
    assert rows[0]["code"] == "600519"
    assert (iso_root / "watchlist.json").exists()


def test_add_duplicate_value_error(iso_root):
    watchlist.add_watchlist("600519")
    with pytest.raises(ValueError) as exc:
        watchlist.add_watchlist("600519")
    assert "已存在" in str(exc.value)


def test_add_invalid_code(iso_root):
    with pytest.raises(ValueError):
        watchlist.add_watchlist("abc")
    with pytest.raises(ValueError):
        watchlist.add_watchlist("12345")


def test_add_with_name(iso_root):
    rec = watchlist.add_watchlist("600519", name="贵州茅台")
    assert rec["name"] == "贵州茅台"
    assert watchlist.list_watchlist()[0]["name"] == "贵州茅台"


def test_delete(iso_root):
    watchlist.add_watchlist("600519")
    assert watchlist.delete_watchlist("600519") is not None
    assert watchlist.list_watchlist() == []
    assert watchlist.delete_watchlist("600519") is None  # 不存在
    assert watchlist.delete_watchlist("abc") is None  # 非法


def test_reorder(iso_root):
    watchlist.add_watchlist("600519")
    watchlist.add_watchlist("000001")
    watchlist.add_watchlist("300750")
    ordered = watchlist.reorder_watchlist(["300750", "600519"])
    assert [x["code"] for x in ordered] == ["300750", "600519", "000001"]
    # 非法/缺失代码忽略
    ordered2 = watchlist.reorder_watchlist(["abc", "000001"])
    assert [x["code"] for x in ordered2] == ["000001", "300750", "600519"]


def test_reorder_bad_type(iso_root):
    with pytest.raises(ValueError):
        watchlist.reorder_watchlist("600519")
    with pytest.raises(ValueError):
        watchlist.reorder_watchlist(None)


def test_corrupt_file_returns_empty(iso_root):
    (iso_root / "watchlist.json").write_text("{not json", encoding="utf-8")
    assert watchlist.list_watchlist() == []
    # 损坏后可正常恢复写入
    rec = watchlist.add_watchlist("600519")
    assert rec["code"] == "600519"


def test_save_failure_returns_false(iso_root, monkeypatch):
    """真实 _save 容错：数据根指向已存在文件 → 目录创建失败 → 返回 False 不抛。"""
    blocker = iso_root / "blocker.txt"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(blocker))
    assert watchlist.add_watchlist("600519") is False
    # 文件读不到（视为空列表）→ delete 找不到 → None
    assert watchlist.delete_watchlist("600519") is None
    assert watchlist.reorder_watchlist(["600519"]) is False


def test_persist_across_reload(iso_root):
    watchlist.add_watchlist("600519", note="持久")
    rows = watchlist.list_watchlist()
    assert rows[0]["note"] == "持久"
    # 文件内容为 JSON 数组（原子写后无 .tmp 残留）
    assert not (iso_root / "watchlist.json.tmp").exists()
    import json

    raw = json.loads((iso_root / "watchlist.json").read_text(encoding="utf-8"))
    assert raw[0]["code"] == "600519"
