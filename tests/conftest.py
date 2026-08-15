# 退出挂起修复：chroma/langfuse 的 posthog 遥测线程会在解释器退出时被
# atexit join 阻塞（最长约 2 分钟）。在导入任何库之前全局禁用。
try:
    import posthog

    posthog.disabled = True
except Exception:  # pragma: no cover - 环境无 posthog 时无需处理
    pass

# 2026-08-06：测试环境全局移除 Langfuse 凭据，避免 graph._callbacks() 真实初始化
# Langfuse 客户端导致网络导出/退出挂起；需要测 Langfuse 配置的用例自行 monkeypatch。
import os  # noqa: E402

for _langfuse_key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_BASE_URL"):
    os.environ.pop(_langfuse_key, None)

# 2026-08-12：资讯开关生产默认关闭（NEWS_ENABLED 未设置=屏蔽）；测试环境显式开启，
# 保持既有资讯链路用例语义（测「关闭时屏蔽」的用例自行 monkeypatch 置 0）。
os.environ.setdefault("NEWS_ENABLED", "1")

# ---------- 数据根隔离（2026-08-15） ----------
# 默认将所有测试的数据根隔离到临时目录，避免复盘落盘类改动（如 fetch_data_node
# 自动保存 market_data.json）污染仓库真实 data/（PLAYBOOK 铁律：测试隔离
# STOCK_REVIEW_DATA_DIR 指向临时目录）。
# 设计：函数级 autouse —— 每个测试各得一个全新空目录，杜绝「一个测试落盘的文件
# 被后续测试离线回放复用」的顺序污染；需要真实隔离语义或持久化的测试可自行
# monkeypatch.setenv 覆盖（autouse 之后、测试体内的 setenv 优先生效）。
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

import pytest  # noqa: E402

_TEST_DATA_BASE = _Path(tempfile.mkdtemp(prefix="stock_rew_tests_"))
_STASH: list[str] = []


@pytest.fixture(autouse=True)
def _isolated_test_data_dir():
    """每个测试独立的数据根：新建空目录并清空旧残留。"""
    _cleanup_test_data_base()
    import os as _os
    root = _Path(tempfile.mkdtemp(prefix="test_", dir=str(_TEST_DATA_BASE)))
    _STASH.append(str(root))
    _os.environ["STOCK_REVIEW_DATA_DIR"] = str(root)
    _os.environ["STOCK_REVIEW_ARCHIVE_DIR"] = str(root / "archive")
    yield
    try:
        shutil.rmtree(str(root), ignore_errors=True)
    except Exception:  # pragma: no cover
        pass


def _cleanup_test_data_base() -> None:
    """仅在前一个测试的临时根残留时清理，避免 dir 选项目录堆积（惰性、容忍异常）。"""
    for child in _TEST_DATA_BASE.iterdir():
        if child.is_dir():
            try:
                shutil.rmtree(child, ignore_errors=True)
            except Exception:  # pragma: no cover
                pass


@atexit.register
def _cleanup_test_data_dir() -> None:  # pragma: no cover - 仅清理
    shutil.rmtree(str(_TEST_DATA_BASE), ignore_errors=True)

# 禁用 py_mini_racer（Windows IOCP 退出死锁），必须在导入 akshare 之前生效
try:
    import py_mini_racer

    class _MiniRacerDisabled:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("py_mini_racer 已禁用（Windows IOCP 退出死锁）")

    py_mini_racer.MiniRacer = _MiniRacerDisabled
except Exception:  # pragma: no cover
    pass
