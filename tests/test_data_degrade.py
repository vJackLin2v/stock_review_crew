"""降级链与本地缓存测试：主源→备用→缓存→缺失；缓存默认只读。"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stock_review_crew.tools.stock_data as sd  # noqa: E402

from test_data_common import (  # noqa: E402
    clear_module_caches, fresh_tmp_dir, seed_cache_block,
    seed_legacy_market_micro, seed_legacy_sectors,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    clear_module_caches(sd)
    monkeypatch.delenv("STOCK_DATA_CACHE_ENABLED", raising=False)


def test_cache_used_when_sources_fail(monkeypatch):
    """主源/备用全失败 → 本地缓存（新格式），degraded 可见。"""
    cache = fresh_tmp_dir("degrade_new")
    seed_cache_block(cache, "2026-07-31", "breadth", {
        "total": 5000, "up": 3000, "down": 1800, "flat": 200,
        "up_down_ratio": 1.6667, "limit_up": 80, "limit_down": 10,
        "zhaban": 20, "touched": 100, "zhaban_rate": 0.2,
        "distribution": {}, "source": "Tushare计算", "degraded": False,
        "degraded_reason": [], "note": "缓存样本",
    })
    monkeypatch.setenv("STOCK_DATA_CACHE_DIR", str(cache))

    def _raise(*a, **k):
        raise RuntimeError("模拟在线失败")

    monkeypatch.setattr(sd, "_ts_daily", _raise)
    monkeypatch.setattr(sd, "_legu_activity", _raise)
    # 2026-08-11：补 mock 盘中实时源（fetch_market_breadth 降级链第二环），
    # 真实网络可用时该源会成功返回导致断言失败（PLAYBOOK 降级测试环境耦合教训）
    monkeypatch.setattr(
        "stock_review_crew.tools.realtime.fetch_breadth_realtime",
        lambda: (_ for _ in ()).throw(RuntimeError("离线")),
    )
    out = sd.fetch_market_breadth("2026-07-31")
    assert out["source"] == "本地缓存"
    assert out["degraded"] is True
    assert any("使用本地缓存" in r for r in out["degraded_reason"])
    assert out["up"] == 3000
    assert out["zhaban_rate"] == pytest.approx(0.2)


def test_legacy_market_micro_conversion(monkeypatch):
    """旧版 market_micro.json：百分数 → 小数换算，标注旧版来源。"""
    cache = fresh_tmp_dir("degrade_legacy")
    seed_legacy_market_micro(cache, "2026-07-31")
    monkeypatch.setenv("STOCK_DATA_CACHE_DIR", str(cache))
    monkeypatch.setattr(sd, "_tx_index_daily",
                        lambda s: (_ for _ in ()).throw(RuntimeError("离线")))
    monkeypatch.setattr(sd, "_records_from_em_kline",
                        lambda s: (_ for _ in ()).throw(RuntimeError("东财日K离线")))
    monkeypatch.setattr(sd, "_records_from_tx_kline",
                        lambda s: (_ for _ in ()).throw(RuntimeError("腾讯日K离线")))
    monkeypatch.setattr(sd.tdx_local, "read_day",
                        lambda *a, **k: (_ for _ in ()).throw(
                            sd.tdx_local.TdxError("文件不存在")))
    out = sd.fetch_index_trend("2026-07-31")
    assert out["source"] == "本地缓存(旧版换算)"
    assert out["degraded"] is True
    sh = out["indices"]["shanghai"]
    assert sh["pct_change"] == pytest.approx(0.0072)   # 0.72% → 0.0072
    assert sh["close"] == pytest.approx(3832.26)


def test_legacy_sectors_cache(monkeypatch):
    """旧版 sectors.json 直接作为板块降级数据。"""
    cache = fresh_tmp_dir("degrade_sectors")
    seed_legacy_sectors(cache, "2026-07-31")
    monkeypatch.setenv("STOCK_DATA_CACHE_DIR", str(cache))
    monkeypatch.setattr(sd, "_ths_sector_summary",
                        lambda: (_ for _ in ()).throw(RuntimeError("离线")))
    # 2026-08-11：历史板块路径实际走 _ths_sector_names_line（v4/v6 line 直连，
    # 2026-08-10 新增），补 mock 使降级链在真实网络下也确定走缓存
    monkeypatch.setattr(sd, "_ths_sector_names_line",
                        lambda: (_ for _ in ()).throw(RuntimeError("离线")))
    monkeypatch.setattr(sd, "_em_sector_names",
                        lambda: (_ for _ in ()).throw(RuntimeError("离线")))
    out = sd.fetch_sectors("2026-07-31")
    assert out["source"] == "本地缓存(旧版)"
    assert out["top5"][0]["板块"] == "软件"
    assert out["degraded"] is True


def test_cache_write_disabled_by_default(monkeypatch):
    """默认不写缓存（生产 data_cache/ 只读降级链）。"""
    cache = fresh_tmp_dir("degrade_nowrite")
    monkeypatch.setenv("STOCK_DATA_CACHE_DIR", str(cache))
    monkeypatch.delenv("STOCK_DATA_CACHE_ENABLED", raising=False)

    def _ok(date):
        return make_zt_df()

    monkeypatch.setattr(sd, "_em_zt_pool", _ok)
    monkeypatch.setattr(sd, "_em_zt_pool_zbgc", lambda date: pd.DataFrame())
    monkeypatch.setattr(sd, "_em_zt_pool_dtgc", lambda date: pd.DataFrame())
    sd.fetch_zt_pool("2026-07-31")
    assert not list((cache / "2026-07-31").glob("block_*.json")), "未开启缓存时不得写文件"


def test_cache_write_enabled_via_env(monkeypatch):
    """显式 STOCK_DATA_CACHE_ENABLED=1 才写缓存。"""
    cache = fresh_tmp_dir("degrade_write")
    monkeypatch.setenv("STOCK_DATA_CACHE_DIR", str(cache))
    monkeypatch.setenv("STOCK_DATA_CACHE_ENABLED", "1")
    monkeypatch.setattr(sd, "_em_zt_pool", lambda date: make_zt_df())
    monkeypatch.setattr(sd, "_em_zt_pool_zbgc", lambda date: pd.DataFrame())
    monkeypatch.setattr(sd, "_em_zt_pool_dtgc", lambda date: pd.DataFrame())
    sd.fetch_zt_pool("2026-07-31")
    files = list((cache / "2026-07-31").glob("block_*.json"))
    assert files, "开启缓存时应写入块文件"


def make_zt_df():
    return pd.DataFrame([{
        "代码": "600001", "名称": "涨停王", "涨跌幅": 10.0, "最新价": 11.0,
        "涨停价": 11.0, "换手率": 5.0, "连板数": 1, "所属行业": "软件",
        "成交额": 1e8, "封板资金": 5e7,
    }])
