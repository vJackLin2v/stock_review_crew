"""统一 LLM 运行时：LLM 可用性探测 / Langfuse 回调 / invoke 硬超时与重试 /
免责声明强制追加 / 主持人输出解析。

从 graph.py 与 trades/analyst.py 抽取的共享适配层（纯重构，行为不变）：
- graph.py 的 _llm_ready/_llm_pair/_callbacks/_invoke_llm/ensure_disclaimer/_parse_host
- trades/analyst.py 的 _llm_ready/_callbacks/_invoke_llm/ensure_disclaimer/parse_host_output
统一为 llm_ready/llm_pair/callbacks/invoke_llm/ensure_disclaimer/parse_host_output；
trades 侧 _invoke_llm 迁移后获得 graph 版 150s 硬超时 + 重试（预期内增强）。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

DISCLAIMER = "仅供参考，不构成投资建议"
_INVOKE_TIMEOUT = 150.0  # R12 修复：LLM 调用硬超时（httpx timeout 对流式/挂起连接不生效）


def llm_ready() -> bool:
    try:
        from .config import DEEPSEEK_API_KEY, llm, llm_strict
        return bool(DEEPSEEK_API_KEY and llm is not None and llm_strict is not None)
    except Exception:
        return False


def llm_pair():
    from .config import llm, llm_strict
    return llm, llm_strict


def callbacks():
    """LLM 调用回调。

    默认关闭——2026-08-10 实测 langfuse CallbackHandler 同步导出
    （cloud.langfuse.com OTLP）把单次 LLM 调用从 ~15s 拖到 180s+（10 倍）。
    2026-08-12 起为 job 级开关：前端页面发起的复盘（trace=true）在
    config.langfuse_callbacks_scope 内强制开启，脚本批量（trace=false）关闭；
    显式环境变量仍可全局覆盖：LANGFUSE_CALLBACKS=1/true/on/yes 强制开，
    =0/false/off/no 强制关。复盘级 trace 由 quality.trace_review 独立上报。"""
    import os

    env = os.getenv("LANGFUSE_CALLBACKS", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return []
    if env not in ("1", "true", "on", "yes"):
        try:
            from .config import langfuse_callbacks_enabled

            if not langfuse_callbacks_enabled():
                return []
        except Exception:
            return []
    try:
        from .config import langfuse_callback

        cb = langfuse_callback()
        return [cb] if cb is not None else []
    except Exception:
        return []


def invoke_llm(llm, messages, timeout: float = _INVOKE_TIMEOUT):
    """LLM 调用：Langfuse 回调故障与 LLM 本体故障隔离 + 硬超时。
    先带 Langfuse 回调调用；若因回调（监控侧网络/配置）抛错，去掉回调重试一次；
    上游偶发失败（含 400 Content Exists Risk 内容审核拦截、限流、5xx）自动重试 2 次
    （间隔 3s/6s，LLM 输出有随机性，重试可越过偶发审核）；重试后仍失败 → 由调用方
    走规则引擎降级或终止复盘（2026-08-10 用户口径：LLM 不可用直接失败，不降级报告）。
    **超时兜底**：httpx 的 timeout 对挂起的连接可能不生效（实测 opencode 端点可挂死），
    这里用守护线程 + result(timeout) 强制放弃，超时抛 TimeoutError 由调用方降级。
    """

    cbs = callbacks()  # 调用方线程求值：job 级 ContextVar 开关在调用方上下文生效

    def _do() -> Any:
        try:
            return llm.invoke(messages, config={"callbacks": cbs})
        except Exception:
            return llm.invoke(messages)

    import threading as _th

    box: list = []
    exc_box: list = []

    def _run() -> None:
        import time as _t

        for _attempt in range(3):  # 1 次原始 + 2 次重试
            try:
                box.append(_do())
                return
            except BaseException as exc:  # noqa: BLE001
                exc_box.append(exc)
                if _attempt < 2:
                    _t.sleep(3 * (_attempt + 1))

    t = _th.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if box:
        return box[0]
    if exc_box:
        raise exc_box[0]
    raise TimeoutError(f"LLM 调用超时（{timeout}s），按降级处理")


def ensure_disclaimer(text, disclaimer: str = DISCLAIMER) -> str:
    """程序级强制免责声明：保证出现在末尾且仅一次。"""
    text = (text or "").strip()
    if not text:
        return f"> {disclaimer}"
    if text.rstrip().endswith(disclaimer):
        return text
    parts = [p.strip() for p in text.split(disclaimer) if p.strip()]
    body = "\n\n".join(parts)
    return f"{body}\n\n---\n\n> {disclaimer}"


def parse_host_output(content) -> tuple[bool, str]:
    """解析主持人两行式输出：分歧判断：有/无 + 讨论议题：<议题>。
    空议题/无实质分歧返回 (False, "")。"""
    has_disagreement = False
    topic = ""
    for line in (content or "").splitlines():
        s = line.strip().lstrip("-*•").strip()
        for sep in ("：", ":"):
            if sep not in s:
                continue
            head, tail = s.split(sep, 1)
            tail = tail.strip()
            if "分歧" in head:
                has_disagreement = "有" in tail
            elif "议题" in head or "话题" in head:
                topic = tail
            break
    if not topic or topic in ("无", "有", "无分歧", "无实质分歧", "暂无"):
        return False, ""
    return has_disagreement, topic
