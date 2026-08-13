"""同花顺板块历史直连（v4/v6 line）测试：列表解析、日K 解析、历史板块聚合。"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stock_review_crew.tools.stock_data as sd  # noqa: E402


_THSHY_HTML = """
<div class="cate_inner">
  <a href="http://q.10jqka.com.cn/thshy/detail/code/881121/" target="_blank">半导体</a>
  <a href="http://q.10jqka.com.cn/thshy/detail/code/881273/" target="_blank">白酒</a>
  <a href="http://q.10jqka.com.cn/thshy/detail/code/881131/" target="_blank">白色家电</a>
  <a href="http://q.10jqka.com.cn/thshy/detail/code/881145/" target="_blank">电力</a>
</div>
"""

_V4_JS = (
    "quotebridge_v6_line_bk_881273_01_last({"
    '"data":"20260714,1832.815,1849.909,1825.399,1848.906,157054900.0,11350330000.000,,,,0;'
    '20260715,1828.649,1959.223,1823.910,1943.762,346914910.0,23247273000.000,,,,0",'
    '"total":"4623","start":"20070801","name":"白酒"})'
)


def _resp(text: str):
    return SimpleNamespace(text=text, status_code=200,
                           raise_for_status=lambda: None)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    sd._ths_code_map.clear()
    monkeypatch.delenv("STOCK_DATA_CACHE_ENABLED", raising=False)


def test_ths_sector_names_line_parses(monkeypatch):
    """列表页解析：名称→代码映射，去重后返回 name/code。"""
    monkeypatch.setattr(sd, "_get", lambda url, **kw: _resp(_THSHY_HTML))
    df = sd._ths_sector_names_line()
    assert list(df.columns) == ["name", "code"]
    assert len(df) == 4
    assert sd._ths_code_map["白酒"] == "881273"
    assert sd._ths_code_map["半导体"] == "881121"


def test_ths_sector_names_line_empty_raises(monkeypatch):
    """列表页解析为空 → 中文 DataSourceError。"""
    monkeypatch.setattr(sd, "_get", lambda url, **kw: _resp("<html></html>"))
    with pytest.raises(sd.DataSourceError, match="列表解析为空"):
        sd._ths_sector_names_line()


def test_ths_sector_hist_line_parses_v4(monkeypatch):
    """v4 按年日K：按 [start, end] 过滤、列名兼容收盘价/成交额。"""
    sd._ths_code_map["白酒"] = "881273"
    monkeypatch.setattr(sd, "_get", lambda url, **kw: _resp(_V4_JS))
    df = sd._ths_sector_hist_line("白酒", "20260714", "20260715")
    assert len(df) == 2
    assert df.iloc[-1]["收盘价"] == pytest.approx(1943.762)
    assert df.iloc[-1]["成交额"] == pytest.approx(23247273000.0)
    # 目标日之外被过滤
    df2 = sd._ths_sector_hist_line("白酒", "20260715", "20260715")
    assert len(df2) == 1
    assert df2.iloc[0]["日期"] == "20260715"


def test_ths_sector_hist_line_falls_back_to_v6(monkeypatch):
    """v4 失败（404/解析异常）→ v6 last 兜底。"""
    sd._ths_code_map["白酒"] = "881273"
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "v4" in url:
            return SimpleNamespace(text="not found", status_code=404,
                                   raise_for_status=lambda: (_ for _ in ()).throw(
                                       RuntimeError("404")))
        return _resp(_V4_JS)

    monkeypatch.setattr(sd, "_get", fake_get)
    df = sd._ths_sector_hist_line("白酒", "20260715", "20260715")
    assert len(df) == 1
    assert any("v6" in c for c in calls)


def test_ths_sector_hist_line_missing_map(monkeypatch):
    """板块映射缺失 → 中文 DataSourceError。"""
    monkeypatch.setattr(sd, "_get", lambda url, **kw: _resp(_THSHY_HTML))
    with pytest.raises(sd.DataSourceError, match="映射缺失"):
        sd._ths_sector_hist_line("不存在的板块", "20260701", "20260731")


def test_sectors_from_hist_concurrent(monkeypatch):
    """历史板块聚合：并发遍历，top5/bottom5 按涨跌幅排序，字段契约正确。"""
    names_df = pd.DataFrame({"name": ["A", "B", "C", "D"],
                             "code": ["881001", "881002", "881003", "881004"]})

    def fake_names():
        return names_df

    def fake_hist(name, start, end):
        pct = {"A": 0.05, "B": -0.03, "C": 0.02, "D": -0.06}[name]
        base = 100.0
        return pd.DataFrame([
            {"日期": "20260710", "收盘价": base, "成交额": 1e9},
            {"日期": "20260715", "收盘价": base * (1 + pct), "成交额": 2e9},
        ])

    monkeypatch.setattr(sd, "_ths_sector_names_line", fake_names)
    monkeypatch.setattr(sd, "_ths_sector_hist_line", fake_hist)
    out = sd._sectors_from_hist("2026-07-15")
    assert out["source"] == "同花顺"
    assert out["degraded"] is False
    assert [r["name"] for r in out["top5"]] == ["A", "C", "B", "D"]
    assert [r["name"] for r in out["bottom5"]] == ["D", "B", "C", "A"]
    assert out["top5"][0]["pct_change"] == pytest.approx(0.05)
    assert out["top5"][0]["amount"] == pytest.approx(2e9)
    assert out["top5"][0]["net_inflow"] is None
    assert out["top5"][0]["leading_stock"] is None
