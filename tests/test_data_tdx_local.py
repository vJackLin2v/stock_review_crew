"""通达信本地解析测试：只读、格式健壮、中文错误。"""

import os
import struct
import sys
import datetime
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_review_crew.tools import tdx_local  # noqa: E402

from test_data_common import fresh_tmp_dir  # noqa: E402


TDX_REAL = Path(r"F:\tdx\vipdoc")
HAVE_REAL_TDX = TDX_REAL.is_dir()


def _write_day_file(root: Path, market: str, code: str, records: list[tuple]) -> Path:
    d = root / "vipdoc" / market / "lday"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{market}{code}.day"
    buf = b"".join(struct.pack("<IIIIIfII", *r) for r in records)
    p.write_bytes(buf)
    return p


def _write_min_file(root: Path, market: str, code: str, period: int,
                    records: list[tuple]) -> Path:
    d = root / "vipdoc" / market / "minline"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{market}{code}.lc{period}"
    buf = b"".join(struct.pack("<HHfffffII", *r) for r in records)
    p.write_bytes(buf)
    return p


def _min_dt(yyyymmdd: int, hhmm: int) -> int:
    y, m, d = yyyymmdd // 10000, yyyymmdd % 10000 // 100, yyyymmdd % 100
    return (y - 2004) * 2048 + m * 100 + d


# ── 市场解析 ──

def test_resolve_market_rules():
    assert tdx_local.resolve_market("sh600519") == ("sh", "600519")
    assert tdx_local.resolve_market("sz000001") == ("sz", "000001")
    assert tdx_local.resolve_market("600519") == ("sh", "600519")
    assert tdx_local.resolve_market("000001") == ("sh", "000001")  # 指数默认沪
    assert tdx_local.resolve_market("399001") == ("sz", "399001")
    assert tdx_local.resolve_market("300750") == ("sz", "300750")
    assert tdx_local.resolve_market("688981") == ("sh", "688981")
    assert tdx_local.resolve_market("830799") == ("bj", "830799")
    with pytest.raises(tdx_local.TdxError, match="无法识别"):
        tdx_local.resolve_market("abc")
    with pytest.raises(tdx_local.TdxError, match="无效"):
        tdx_local.resolve_market("60051")


def test_missing_tdx_path_error():
    """未配置 TDX_PATH 时应报中文错误（与 .env 实际内容无关）。"""
    import stock_review_crew.config as cfg
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cfg, "TDX_PATH", None)
    try:
        with pytest.raises(tdx_local.TdxError, match="未配置通达信路径"):
            tdx_local.read_day("600519", tdx_path=None)
    finally:
        monkeypatch.undo()


# ── 日线解析 ──

def test_read_day_parses_records():
    root = fresh_tmp_dir("tdx_day")
    _write_day_file(root, "sh", "600519", [
        (20260730, 140000, 145000, 139000, 144500, 1.5e9, 1200000, 0),
        (20260731, 144500, 150000, 143000, 149800, 2.0e9, 1500000, 0),
    ])
    out = tdx_local.read_day("sh600519", tdx_path=str(root))
    assert out["record_count"] == 2
    assert out["date_start"] == "2026-07-30"
    assert out["date_end"] == "2026-07-31"
    last = out["records"][-1]
    assert last == {"date": "2026-07-31", "open": 1445.0, "high": 1500.0,
                    "low": 1430.0, "close": 1498.0, "amount": 2.0e9, "volume": 1500000}
    assert out["volume_unit"] == "手"


def test_read_day_tail():
    root = fresh_tmp_dir("tdx_day_tail")
    _write_day_file(root, "sz", "000001", [
        (20260728, 1000, 1100, 900, 1050, 1e8, 100, 0),
        (20260729, 1050, 1150, 950, 1100, 1e8, 120, 0),
        (20260730, 1100, 1200, 1000, 1150, 1e8, 130, 0),
    ])
    tail = tdx_local.read_day_tail("sz000001", n=2, tdx_path=str(root))
    assert [r["date"] for r in tail] == ["2026-07-29", "2026-07-30"]


def test_read_day_missing_file():
    root = fresh_tmp_dir("tdx_day_missing")
    with pytest.raises(tdx_local.TdxError, match="不存在"):
        tdx_local.read_day("sh600519", tdx_path=str(root))


def test_read_day_empty_file():
    root = fresh_tmp_dir("tdx_day_empty")
    p = root / "vipdoc" / "sh" / "lday"
    p.mkdir(parents=True)
    (p / "sh600519.day").write_bytes(b"")
    with pytest.raises(tdx_local.TdxError, match="为空"):
        tdx_local.read_day("600519", tdx_path=str(root))


def test_read_day_corrupt_size():
    root = fresh_tmp_dir("tdx_day_corrupt")
    p = root / "vipdoc" / "sh" / "lday"
    p.mkdir(parents=True)
    (p / "sh600519.day").write_bytes(b"\x00" * 33)
    with pytest.raises(tdx_local.TdxError, match="损坏"):
        tdx_local.read_day("600519", tdx_path=str(root))


def test_read_day_corrupt_date():
    root = fresh_tmp_dir("tdx_day_baddate")
    _write_day_file(root, "sh", "600519", [(99999999, 100, 100, 100, 100, 1.0, 1, 0)])
    with pytest.raises(tdx_local.TdxError, match="损坏"):
        tdx_local.read_day("600519", tdx_path=str(root))


# ── 分钟线解析 ──

def test_read_minline_parses_records():
    root = fresh_tmp_dir("tdx_min")
    _write_min_file(root, "sh", "000001", 1, [
        (_min_dt(20260731, 931), 9 * 60 + 31, 3000.0, 3010.0, 2990.0, 3005.0, 1.5e8, 100000, 0),
        (_min_dt(20260731, 932), 9 * 60 + 32, 3005.0, 3020.0, 3000.0, 3015.0, 1.8e8, 120000, 0),
    ])
    out = tdx_local.read_minline("sh000001", 1, tdx_path=str(root))
    assert out["record_count"] == 2
    assert out["records"][0]["datetime"] == "2026-07-31 09:31"
    assert out["records"][0]["close"] == 3005.0
    assert out["records"][-1]["volume"] == 120000
    assert out["period"] == 1


def test_read_minline_period5_and_validation():
    root = fresh_tmp_dir("tdx_min5")
    _write_min_file(root, "sz", "000001", 5, [
        (_min_dt(20260731, 935), 9 * 60 + 35, 100.0, 101.0, 99.0, 100.5, 1e7, 500, 0),
    ])
    out = tdx_local.read_minline("sz000001", 5, tdx_path=str(root))
    assert out["records"][0]["time"] == "09:35"
    with pytest.raises(tdx_local.TdxError, match="周期"):
        tdx_local.read_minline("sz000001", 15, tdx_path=str(root))


def test_read_minline_missing():
    root = fresh_tmp_dir("tdx_min_missing")
    with pytest.raises(tdx_local.TdxError, match="不存在"):
        tdx_local.read_minline("600519", 1, tdx_path=str(root))


# ── 只读保证 ──

def test_tdx_readonly_no_write():
    root = fresh_tmp_dir("tdx_ro")
    p = _write_day_file(root, "sh", "600519", [
        (20260730, 140000, 145000, 139000, 144500, 1.5e9, 1200000, 0),
    ])
    before = (p.stat().st_size, p.stat().st_mtime_ns)
    tdx_local.read_day("600519", tdx_path=str(root))
    tdx_local.read_day_tail("600519", n=1, tdx_path=str(root))
    after = (p.stat().st_size, p.stat().st_mtime_ns)
    assert before == after, "读取操作不得修改通达信文件"


# ── 真实 F:\\tdx 只读测试（离线夹具；缺失则跳过）──

@pytest.mark.skipif(not HAVE_REAL_TDX, reason="F:\\tdx\\vipdoc 不存在，跳过真实文件测试")
def test_real_tdx_read_day():
    out = tdx_local.read_day("sh000001", tdx_path=r"F:\tdx")
    assert out["record_count"] > 100
    # 本地通达信数据会随行情更新（2026-08-10 实测已到 08-10），
    # 断言改为「覆盖近期且不晚于今天」，避免写死日期过时
    assert "2026-07-01" <= out["date_end"] <= datetime.date.today().isoformat()
    assert out["records"][0]["close"] > 0


@pytest.mark.skipif(not HAVE_REAL_TDX, reason="F:\\tdx\\vipdoc 不存在，跳过真实文件测试")
def test_real_tdx_iter_day_codes():
    codes = list(tdx_local.iter_day_codes(tdx_path=r"F:\tdx"))
    assert len(codes) > 1000
    markets = {m for m, _, _ in codes}
    assert markets == {"sh", "sz"}
    for market, code, path in codes[:50]:
        assert path.exists()
        assert path.stat().st_size % 32 == 0
