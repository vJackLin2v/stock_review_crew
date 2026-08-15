# -*- coding: utf-8 -*-
"""I4 旧 output 导入脚本测试（R11 R4：每日一份）：模式推断 / 分析师解析 / 幂等 / dry-run / 隔离。

导入目标一律指向 pytest 临时目录（STOCK_REVIEW_DATA_DIR / STOCK_REVIEW_ARCHIVE_DIR），
绝不触碰仓库 data/ 与 output/。
"""

import importlib.util
import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _load_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "import_output_history.py"
    spec = importlib.util.spec_from_file_location("import_output_history", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def imp():
    return _load_script()


@pytest.fixture()
def iso_dir():
    """隔离临时目录（默认权限位自建；本沙箱下 0o700 目录不可再访问）。"""
    base = Path(__file__).resolve().parent.parent
    root = base / f".iso_{uuid.uuid4().hex[:12]}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


SAMPLE_MD = """# 2026-07-31 A股复盘日志
**开始时间**: 2026-07-31 10:38:13
**参与分析师**: 阿狼, 拔小弦, 爱在冰川, 铁锤狂砸盘, 炒股养家

---

### 指数表现

- 上证: 收3832.26

### 阿狼

## 1、昨日复盘验证
内容A

### 爱在冰川

## 1、昨日复盘验证
内容B

### 拔小弦

内容C

### 铁锤狂砸盘

内容D

### 炒股养家

内容E

# 最终报告

## 一、市场概况

2026-07-31 市场概况正文。
"""


@pytest.fixture()
def fixture_output(iso_dir):
    """只读导入源夹具（临时目录，不触碰仓库 output/）。"""
    out = iso_dir / "output"
    (out / "2026-07-31").mkdir(parents=True)
    (out / "2026-07-30").mkdir(parents=True)
    (out / "not-a-date").mkdir(parents=True)
    (out / "2026-07-31" / "复盘_103813.md").write_text(SAMPLE_MD, encoding="utf-8")
    (out / "2026-07-31" / "复盘_092600.md").write_text("空档文件\n### 阿狼\n内容", encoding="utf-8")
    (out / "2026-07-31" / "复盘_150500.md").write_text("收盘文件", encoding="utf-8")
    (out / "2026-07-31" / "复盘_bad.md").write_text("x", encoding="utf-8")
    (out / "2026-07-30" / "复盘_091000.md").write_text("早盘前文件", encoding="utf-8")
    (out / "not-a-date" / "复盘_110000.md").write_text("x", encoding="utf-8")
    return out


@pytest.mark.parametrize(
    "time_str,expected",
    [
        ("000000", "pre_market"),
        ("091459", "pre_market"),
        ("091500", "auction"),
        ("092459", "auction"),
        ("092500", "close"),
        ("092959", "close"),
        ("093000", "intraday_am"),
        ("112959", "intraday_am"),
        ("113000", "noon"),
        ("125959", "noon"),
        ("130000", "intraday_pm"),
        ("145959", "intraday_pm"),
        ("150000", "close"),
        ("235959", "close"),
        ("240000", "close"),
        ("abc123", "close"),
    ],
)
def test_infer_mode(imp, time_str, expected):
    mode, note = imp.infer_mode(time_str)
    assert mode == expected
    if time_str in ("092500", "092959", "240000", "abc123"):
        assert note, "非法时间点必须返回标注"
    else:
        assert note is None


def test_import_basic_structure(imp, fixture_output, iso_dir, monkeypatch):
    root = iso_dir / "reviews"  # reviews 产物根（统一 env=数据根语义下 reviews_root() 返回值）
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(iso_dir))
    monkeypatch.setenv("STOCK_REVIEW_ARCHIVE_DIR", str(iso_dir / "archive"))
    stats = imp.run_import(fixture_output)

    assert stats["scanned"] == 4
    assert stats["imported"] == 2  # 每日一份：2026-07-31 取最新 150500、2026-07-30 取 091000
    assert stats["skipped"] == 2  # 2026-07-31 较早两份（103813/092600）仅保留最新时间点
    assert stats["invalid"] == 2  # 复盘_bad.md + not-a-date 目录

    # R11 R4：目标为每日一份（日期目录直接含 meta/report/snapshot），最新时间点胜出
    entry = root / "2026-07-31"
    meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
    assert meta["time"] == "150500"
    assert meta["mode"] == "close"
    assert meta["mode_label"] == "收盘复盘"
    assert meta["imported_from_legacy"] is True
    assert meta["source_file"].endswith("复盘_150500.md")
    assert meta["summary"]
    assert isinstance(meta["degraded"], list) and meta["degraded"]
    assert not (root / "2026-07-31" / "103813").exists()  # 旧时间点子目录不存在

    report = json.loads((entry / "report.json").read_text(encoding="utf-8"))
    assert report["final_report"] == "收盘文件"
    assert report["analysts"] == []
    assert report["debate_history"] == []

    snapshot = json.loads((entry / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["imported_from_legacy"] is True

    # 2026-07-30 09:10 → pre_market（每日一份）
    meta_am = json.loads((root / "2026-07-30" / "meta.json").read_text(encoding="utf-8"))
    assert meta_am["mode"] == "pre_market"
    assert meta_am["time"] == "091000"

    # 导入源目录保持只读（未被修改）
    assert (fixture_output / "2026-07-31" / "复盘_103813.md").read_text(encoding="utf-8") == SAMPLE_MD


def test_import_idempotent_and_force(imp, fixture_output, iso_dir, monkeypatch):
    root = iso_dir / "reviews"  # reviews 产物根（统一 env=数据根语义下 reviews_root() 返回值）
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(iso_dir))
    monkeypatch.setenv("STOCK_REVIEW_ARCHIVE_DIR", str(iso_dir / "archive"))

    first = imp.run_import(fixture_output)
    assert first["imported"] == 2
    assert first["skipped"] == 2
    second = imp.run_import(fixture_output)
    assert second["imported"] == 0
    assert second["skipped"] == 4
    assert {p.name for p in (root / "2026-07-31").iterdir()} == {"meta.json", "report.json", "snapshot.json"}

    # 目标 meta 去掉 imported_from_legacy → 视为非旧数据，跳过
    entry = root / "2026-07-31"
    meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
    del meta["imported_from_legacy"]
    (entry / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    third = imp.run_import(fixture_output)
    assert third["imported"] == 0
    assert third["skipped"] == 4

    # --force 重导：覆盖写入（先归档旧版到 output/archive），不产生时间点子目录
    (entry / "report.json").unlink()
    fourth = imp.run_import(fixture_output, force=True)
    assert fourth["imported"] == 2
    assert fourth["skipped"] == 2
    assert (entry / "report.json").is_file()
    assert not (root / "2026-07-31" / "150500").exists()
    meta_after = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
    assert meta_after["imported_from_legacy"] is True
    # 归档：force 覆盖前旧版完整目录已复制到归档根（隔离目录）
    archives = sorted((iso_dir / "archive").iterdir())
    assert archives and any(a.name.startswith("2026-07-31_") for a in archives)
    assert (next(a for a in archives if a.name.startswith("2026-07-31_")) / "meta.json").is_file()


def test_import_dry_run_writes_nothing(imp, fixture_output, iso_dir, monkeypatch):
    root = iso_dir / "reviews"  # reviews 产物根（统一 env=数据根语义下 reviews_root() 返回值）
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(iso_dir))
    monkeypatch.setenv("STOCK_REVIEW_ARCHIVE_DIR", str(iso_dir / "archive"))
    stats = imp.run_import(fixture_output, dry_run=True)
    assert stats["imported"] == 0
    assert stats["dry_run"] == 2  # 每日一份：每个日期预览一份
    assert stats["skipped"] == 2  # 较早时间点跳过
    assert not root.exists()


def test_import_date_filter(imp, fixture_output, iso_dir, monkeypatch):
    root = iso_dir / "reviews"  # reviews 产物根（统一 env=数据根语义下 reviews_root() 返回值）
    monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(iso_dir))
    monkeypatch.setenv("STOCK_REVIEW_ARCHIVE_DIR", str(iso_dir / "archive"))
    stats = imp.run_import(fixture_output, date="2026-07-30")
    assert stats["scanned"] == 1
    assert stats["imported"] == 1
    assert (root / "2026-07-30" / "meta.json").is_file()
    assert not (root / "2026-07-31").exists()


def test_extract_analysts(imp):
    md = "### 指数表现\nx\n### 阿狼\n内容\n### 涨停板情况\ny\n### 炒股养家\nz\n"
    assert imp.extract_analysts(md) == ["阿狼", "炒股养家"]
    assert imp.extract_analysts("### 指数表现\nx") == []
    assert imp.extract_analysts("") == []

    # 参与行与段落交集为空 → 兜底采用参与行名单
    md2 = "**参与分析师**: 阿狼, 拔小弦\n### 指数表现\nx\n"
    assert imp.extract_analysts(md2) == ["阿狼", "拔小弦"]


def test_run_import_missing_output_raises_chinese(imp, iso_dir):
    with pytest.raises(FileNotFoundError, match="输出目录不存在"):
        imp.run_import(iso_dir / "nope")
    with pytest.raises(ValueError, match="日期参数"):
        imp.run_import(iso_dir, date="2026/07/31")


def test_cli_dry_run(imp, fixture_output, iso_dir, capsys):
    data_dir = iso_dir / "cli_reviews"
    code = imp.main(["--dry-run", "--output-dir", str(fixture_output), "--data-dir", str(data_dir)])
    assert code == 0
    out = capsys.readouterr().out
    assert "将导入" in out
    assert "汇总" in out
    assert not data_dir.exists()
