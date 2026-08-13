"""M3 指数成交额回填测试：主源成功后 amount 缺失 → 备用源（东财 push2his /
通达信协议）回填当日成交额，只写 amount 不丢其他字段；回填失败静默降级。

- 主源 `_tx_index_daily`（akshare 腾讯同源）只有 6 列，无成交额（2026-08-12 实测）；
- 东财 push2his 日K 含 amount（元）；通达信协议 fetch_daily(is_index=True) 含 amount（元）；
- 腾讯日K 备用源 amount 恒 None，不参与回填。
"""

import copy
import datetime
import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stock_review_crew.tools.stock_data as sd  # noqa: E402

from test_data_common import clear_module_caches  # noqa: E402


DATE = "2026-08-11"          # 固定历史交易日（≠当日，避免 realtime 合并）
BACKFILL_EM_AMOUNT = 630617125061.50    # 实测东财 push2his 第 7 列（元）
BACKFILL_TDX_AMOUNT = 630617145344.0    # 实测通达信协议 amount（元）
N_DAYS = 70


def _dates() -> list[str]:
    base = datetime.date.fromisoformat(DATE)
    return [(base - datetime.timedelta(days=N_DAYS - 1 - i)).isoformat()
            for i in range(N_DAYS)]


def _primary_records(amount: object = None, volume: float = 3.0e8) -> list[dict]:
    """主源解析后记录形态：volume=成交量、amount=None（与 _records_from_tx 输出一致）。"""
    rows = []
    for i, d in enumerate(_dates()):
        rows.append({
            "date": d, "open": 3000.0 + i, "high": 3010.0 + i, "low": 2990.0 + i,
            "close": 3005.0 + i, "volume": volume + i,
            "amount": amount,
        })
    return rows


def _primary_df(volume: float = 3.0e8) -> pd.DataFrame:
    """akshare 通达信指数日线 df 形态：列 date/open/close/high/low/amount——
    amount 列实为成交量（手），无 volume 列（2026-08-12 实测列名）。"""
    rows = [{
        "date": d, "open": 3000.0 + i, "high": 3010.0 + i, "low": 2990.0 + i,
        "close": 3005.0 + i, "amount": volume + i,
    } for i, d in enumerate(_dates())]
    return pd.DataFrame(rows)


def _em_records() -> list[dict]:
    """东财 push2his 日K：含 amount（元），末条日期与主源 asof 一致。"""
    rows = []
    for i, d in enumerate(_dates()):
        rows.append({
            "date": d, "open": 3000.0 + i, "high": 3010.0 + i, "low": 2990.0 + i,
            "close": 3005.0 + i, "volume": 3.0e8 + i,
            "amount": BACKFILL_EM_AMOUNT - (N_DAYS - 1 - i) * 1.0e8,
        })
    return rows


def _tdx_records() -> list[dict]:
    rows = []
    for i, d in enumerate(_dates()):
        rows.append({
            "date": d, "open": 3000.0 + i, "high": 3010.0 + i, "low": 2990.0 + i,
            "close": 3005.0 + i, "volume": 3.0e8 + i,
            "amount": BACKFILL_TDX_AMOUNT - (N_DAYS - 1 - i) * 1.0e8,
        })
    return rows


def _raise(*a, **k):
    raise RuntimeError("回填源离线（mock）")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    clear_module_caches(sd)
    monkeypatch.delenv("STOCK_DATA_CACHE_ENABLED", raising=False)
    monkeypatch.delenv("STOCK_DATA_CACHE_DIR", raising=False)
    # 缓存目录指向不存在路径：主源成功路径不写盘、不读盘（与 test_data_modes 同款）
    monkeypatch.setattr(sd, "get_cache_dir", lambda: Path(__file__).parent / "no_such_cache")
    monkeypatch.setattr(sd, "_tx_index_daily", lambda symbol: _primary_df())
    monkeypatch.setattr(sd, "_records_from_em_kline", _raise)
    monkeypatch.setattr(sd, "_records_from_tx_kline", _raise)
    monkeypatch.setattr(sd, "_records_from_tdx_remote", _raise)


def _expected_metrics(records: list[dict]) -> dict:
    """回填前基准：同一 records 独立计算（deepcopy 隔离，防原地变更串扰）。"""
    return sd._compute_index_metrics(copy.deepcopy(records), DATE, 60)


def _assert_only_amount_differs(actual: dict, expected: dict) -> None:
    """逐字段对比：除 amount 外（顶层 + 每条 daily）必须与回填前完全一致。

    忽略两条：① "amount"（本任务唯一允许变更的字段）；② "_" 前缀元数据键
    （如 _fallback_source，降级链既有设计，与回填无关）。"""
    actual_clean = {k: v for k, v in actual.items() if not k.startswith("_")}
    assert set(actual_clean) == set(expected), \
        f"顶层字段集合变化: {set(actual_clean) ^ set(expected)}"
    for key, exp_val in expected.items():
        if key in ("amount", "daily"):
            continue
        assert actual_clean[key] == exp_val, f"字段被改动: {key}: {actual_clean[key]} != {exp_val}"
    assert len(actual["daily"]) == len(expected["daily"])
    for i, (a, e) in enumerate(zip(actual["daily"], expected["daily"])):
        assert set(a) == set(e), f"daily[{i}] 字段集合变化"
        for key, exp_val in e.items():
            if key == "amount":
                continue
            assert a[key] == exp_val, f"daily[{i}].{key} 被改动: {a[key]} != {exp_val}"


def test_backfill_em_when_primary_amount_missing(monkeypatch):
    """手算抽查：主源 records 全部 amount=None + 东财回填 amount=630617125061.50
    → daily[-1].amount 与顶层 amount 被回填，其余字段（close/open/high/low/volume/
    ma5/ma10/ma20/pct_5d 等）逐字段与原值完全一致。"""
    records = _primary_records()
    expected = _expected_metrics(records)
    assert expected["amount"] is None
    assert expected["daily"][-1]["amount"] is None

    monkeypatch.setattr(sd, "_records_from_em_kline", lambda symbol: _em_records())
    monkeypatch.setattr(sd, "_records_from_tdx_remote", _raise)  # 东财成功即止，不降级

    out = sd.fetch_index_trend(DATE)
    assert out["source"] == "通达信akshare"
    assert out["degraded"] is False
    sh = out["indices"]["shanghai"]
    assert sh["daily"][-1]["amount"] == pytest.approx(BACKFILL_EM_AMOUNT)
    assert sh["amount"] == pytest.approx(BACKFILL_EM_AMOUNT)
    # 只补当日：前一日 amount 仍为 None
    assert sh["daily"][-2]["amount"] is None
    _assert_only_amount_differs(sh, expected)
    # 三指数口径：全部被回填
    for name in ("shanghai", "chuangye", "kechuang"):
        ind = out["indices"][name]
        assert ind["amount"] == pytest.approx(BACKFILL_EM_AMOUNT)
        assert ind["daily"][-1]["amount"] == pytest.approx(BACKFILL_EM_AMOUNT)


def test_backfill_falls_back_to_tdx_remote(monkeypatch):
    """东财回填失败 → 通达信协议回填（amount=630617145344.0），同样只写 amount。"""
    records = _primary_records()
    expected = _expected_metrics(records)
    monkeypatch.setattr(sd, "_records_from_em_kline", _raise)
    monkeypatch.setattr(sd, "_records_from_tdx_remote", lambda symbol: _tdx_records())

    out = sd.fetch_index_trend(DATE)
    sh = out["indices"]["shanghai"]
    assert sh["amount"] == pytest.approx(BACKFILL_TDX_AMOUNT)
    assert sh["daily"][-1]["amount"] == pytest.approx(BACKFILL_TDX_AMOUNT)
    _assert_only_amount_differs(sh, expected)


def test_backfill_silent_when_all_sources_fail(monkeypatch):
    """回填源全失败 → 静默降级：amount 保持 None，不抛异常、不阻塞、不标注降级。"""
    out = sd.fetch_index_trend(DATE)
    assert out["source"] == "通达信akshare"
    assert out["degraded"] is False
    assert out["degraded_reason"] == []
    sh = out["indices"]["shanghai"]
    assert sh["amount"] is None
    assert sh["daily"][-1]["amount"] is None
    # 其余字段不受影响
    assert sh["close"] == pytest.approx(3005.0 + N_DAYS - 1)
    assert sh["ma5"] is not None and sh["ma20"] is not None


def test_records_from_tx_maps_akshare_amount_to_volume(monkeypatch):
    """2026-08-12 根因修正：akshare 通达信指数日线 amount 列实为成交量（手），
    _records_from_tx 必须映射到 volume、amount 置 None（触发回填）——
    绝不把成交量当成交额直达报告。"""
    df = _primary_df(volume=3.0e8)
    rows = sd._records_from_tx(df)
    assert rows[0]["volume"] == pytest.approx(3.0e8)
    assert rows[0]["amount"] is None
    # 全链路：主源 df.amount 有值（成交量）→ metrics amount 仍 None → 回填触发真实成交额
    monkeypatch.setattr(sd, "_records_from_em_kline", lambda symbol: _em_records())
    out = sd.fetch_index_trend(DATE)
    sh = out["indices"]["shanghai"]
    assert sh["amount"] == pytest.approx(BACKFILL_EM_AMOUNT)
    assert sh["volume"] == pytest.approx(3.0e8 + N_DAYS - 1)


def test_backfill_on_tx_backup_path(monkeypatch):
    """主源失败 → 备用链落到腾讯日K（amount 恒 None）→ 回填照常生效；
    证明回填逻辑在备用源 return 前统一执行。"""
    records = _primary_records()
    expected = _expected_metrics(records)
    monkeypatch.setattr(sd, "_tx_index_daily", _raise)  # 主源离线
    monkeypatch.setattr(sd, "_records_from_em_kline", _raise)  # 东财备用也离线
    monkeypatch.setattr(sd, "_records_from_tx_kline",
                        lambda symbol: copy.deepcopy(records))  # 腾讯日K 6 列
    monkeypatch.setattr(sd, "_records_from_tdx_remote", lambda symbol: _tdx_records())

    out = sd.fetch_index_trend(DATE)
    assert out["source"] == "东财/腾讯日K备用"
    assert out["degraded"] is True
    sh = out["indices"]["shanghai"]
    assert sh["amount"] == pytest.approx(BACKFILL_TDX_AMOUNT)
    assert sh["daily"][-1]["amount"] == pytest.approx(BACKFILL_TDX_AMOUNT)
    # 回填前基准来自腾讯 6 列 records（amount=None），逐字段对比证明不丢数据
    _assert_only_amount_differs(sh, expected)


def test_backfill_no_match_date_keeps_none(monkeypatch):
    """回填源有数据但无目标日期（asof 对不上）→ 不误填其他日期，保持 None。"""
    rows = _em_records()
    # 日期整体改为 2026-05-01 起（2026-05-01..2026-07-09），与目标日 2026-08-11 无交集
    for i, r in enumerate(rows):
        r["date"] = (datetime.date(2026, 5, 1) + datetime.timedelta(days=i)).isoformat()
    monkeypatch.setattr(sd, "_records_from_em_kline", lambda symbol: rows)

    out = sd.fetch_index_trend(DATE)
    sh = out["indices"]["shanghai"]
    assert sh["amount"] is None
    assert sh["daily"][-1]["amount"] is None
