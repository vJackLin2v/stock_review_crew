"""config.py 契约测试：token 只从 .env 读取，无硬编码；缺失不抛异常。"""

import importlib
import sys
from pathlib import Path

import pytest


SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stock_review_crew.config as config  # noqa: E402


def _reload_config(monkeypatch):
    return importlib.reload(config)


def test_no_hardcoded_tushare_token():
    """源文件中不得残留旧硬编码 token。"""
    src = (Path(config.__file__)).read_text(encoding="utf-8")
    assert "377a049becb4289103b6ff73bceaee3c0fe5736f727972e4cfcd1f6c" not in src
    assert "TUSHARE_TOKEN" in src
    assert "os.getenv" in src


def test_env_only_loading(monkeypatch):
    """TUSHARE_TOKEN/KAIPANLA_COOKIE/TDX_PATH 缺失时为 None（数据层走降级标注）。"""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("KAIPANLA_COOKIE", raising=False)
    monkeypatch.delenv("TDX_PATH", raising=False)
    # 屏蔽 .env 自动加载（本机 .env 已含凭据，测试须验证纯环境变量路径）
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    cfg = _reload_config(monkeypatch)
    assert cfg.TUSHARE_TOKEN is None
    assert cfg.KAIPANLA_COOKIE is None
    assert cfg.TDX_PATH is None


def test_env_values_loaded(monkeypatch):
    """2026-08-07 新契约：.env 为凭据唯一真源（load_dotenv override=True）——
    显式环境变量不再覆盖 .env 值（修复守护进程残留旧 DEEPSEEK_API_KEY 导致
    聊天 401 的根因：override=False 时环境变量旧值占位，.env 新值不生效）。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token-abc")
    monkeypatch.setenv("KAIPANLA_COOKIE", "test-cookie")
    monkeypatch.setenv("TDX_PATH", "Z:\\fake_tdx")
    cfg = _reload_config(monkeypatch)
    # .env 优先：env 测试值不得覆盖 .env 既有值（.env 缺失该键时才回退 env）
    assert cfg.TUSHARE_TOKEN != "test-token-abc"
    assert cfg.KAIPANLA_COOKIE != "test-cookie"
    assert cfg.TDX_PATH != "Z:\\fake_tdx"
    # 恢复干净状态，避免污染后续测试（数据源缺失 → 降级标注）
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("KAIPANLA_COOKIE", raising=False)
    monkeypatch.delenv("TDX_PATH", raising=False)
    _reload_config(monkeypatch)


def test_deepseek_config_preserved():
    """DEEPSEEK 配置项保留（键名/默认模型）。"""
    assert hasattr(config, "DEEPSEEK_API_KEY")
    assert hasattr(config, "DEEPSEEK_MODEL")
    assert hasattr(config, "DEEPSEEK_BASE_URL")
    assert config.DEEPSEEK_MODEL  # 非空（.env 或默认值）


def test_env_file_bom_handled():
    """.env 带 BOM 时首个键仍能读取（utf-8-sig 显式读取）。"""
    env_file = SRC.parent / ".env"
    if not env_file.exists():
        pytest.skip("项目 .env 不存在")
    raw = env_file.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf"):
        pytest.skip(".env 无 BOM，无需验证")
    assert config.DEEPSEEK_API_KEY, "带 BOM 的 .env 首个键应被正常加载"
