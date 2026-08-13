# -*- coding: utf-8 -*-
"""Langfuse LLM 级回调开关测试（2026-08-12）。

覆盖（需求：前端页面复盘在 Langfuse 可见全流程，脚本跑不上报）：
- config.langfuse_callbacks_scope：job 级 ContextVar 开关（scope 内开、退出关、并发隔离）；
- graph._callbacks()：scope 内返回回调、scope 外 []、LANGFUSE_CALLBACKS=0/1 强制覆盖；
- graph._invoke_llm：在调用方线程求值回调（job 开关不丢给内部超时线程）。

注意：不能放 test_graph.py——其 autouse offline fixture 会把 graph._callbacks
替换为恒返回 []，这里需要跑真实函数。
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import stock_review_crew.graph as graph


class _FakeCb:
    """假 Langfuse 回调对象（仅验证开关是否命中）。"""


def test_callbacks_job_scope_and_env_override(monkeypatch):
    """2026-08-12：LLM 回调 job 级开关——scope 内开启、scope 外关闭；env 可强制覆盖。"""
    monkeypatch.delenv("LANGFUSE_CALLBACKS", raising=False)
    from stock_review_crew import config as cfg

    monkeypatch.setattr(cfg, "langfuse_callback", lambda: _FakeCb())
    assert graph._callbacks() == []
    with cfg.langfuse_callbacks_scope(True):
        cbs = graph._callbacks()
        assert len(cbs) == 1 and isinstance(cbs[0], _FakeCb)
    assert graph._callbacks() == []  # scope 退出后恢复关闭
    monkeypatch.setenv("LANGFUSE_CALLBACKS", "0")
    with cfg.langfuse_callbacks_scope(True):
        assert graph._callbacks() == []  # env 强制关 > job 开关
    monkeypatch.setenv("LANGFUSE_CALLBACKS", "1")
    assert len(graph._callbacks()) == 1  # env 强制开 > scope 外默认关


def test_invoke_llm_uses_job_scope_callbacks(monkeypatch):
    """2026-08-12：_invoke_llm 在调用方线程求值回调（job 级开关生效，不丢给内部线程）。"""
    monkeypatch.delenv("LANGFUSE_CALLBACKS", raising=False)
    from stock_review_crew import config as cfg

    seen: list[list] = []

    class _FakeLLM:
        def invoke(self, messages, config=None):
            seen.append((config or {}).get("callbacks"))
            return types.SimpleNamespace(content="ok")

    monkeypatch.setattr(cfg, "langfuse_callback", lambda: _FakeCb())
    llm = _FakeLLM()
    with cfg.langfuse_callbacks_scope(True):
        out = graph._invoke_llm(llm, [{"role": "user", "content": "hi"}])
    assert out.content == "ok"
    assert len(seen) == 1
    assert len(seen[0]) == 1 and isinstance(seen[0][0], _FakeCb)
