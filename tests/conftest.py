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

# 禁用 py_mini_racer（Windows IOCP 退出死锁），必须在导入 akshare 之前生效
try:
    import py_mini_racer

    class _MiniRacerDisabled:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("py_mini_racer 已禁用（Windows IOCP 退出死锁）")

    py_mini_racer.MiniRacer = _MiniRacerDisabled
except Exception:  # pragma: no cover
    pass
