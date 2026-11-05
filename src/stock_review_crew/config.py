"""配置层：环境变量 + LLM 初始化（凭据一律只读 .env，无硬编码 token）"""
import contextlib
import contextvars
import os
from pathlib import Path

# 禁用 posthog 遥测（chroma/langfuse 匿名统计）：避免解释器退出时被
# atexit join 阻塞（服务与测试进程长时间不退出）。
try:
    import posthog

    posthog.disabled = True
except Exception:  # pragma: no cover - 环境无 posthog 时无需处理
    pass

# 禁用 py_mini_racer（akshare 空气/债券接口的内嵌 V8 引擎）：其后台 asyncio
# 线程在 Windows 上会与解释器退出形成 IOCP 死锁（构造即可能永久阻塞）。
# 本项目数据链路不需要 JS 引擎；若上游接口触发，将抛明确错误由数据层降级标注。
try:
    import py_mini_racer

    class _MiniRacerDisabled:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "py_mini_racer 已禁用（Windows IOCP 退出死锁）；"
                "相关 akshare 接口不可用，数据层已降级标注"
            )

    py_mini_racer.MiniRacer = _MiniRacerDisabled
except Exception:  # pragma: no cover - 环境无 py_mini_racer 时无需处理
    pass

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


# 显式定位项目根 .env，并以 utf-8-sig 读取（.env 带 BOM 时普通 utf-8 会污染首个键名）
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_FILE.exists():
    # 2026-08-07：override=True——凭据只以 .env 为唯一真源（需求契约 Q2）：
    # 运行中的守护/旧进程若继承了过期 DEEPSEEK_API_KEY 环境变量，override=False
    # 不会覆盖 → 聊天/复盘持续 401（实测复现）。.env 强制优先避免环境变量残留。
    load_dotenv(dotenv_path=_ENV_FILE, encoding="utf-8-sig", override=True)



def _llm_settings_model() -> str:
    """读取设置页持久化的模型名；未设置时回退 .env DEEPSEEK_MODEL。"""
    try:
        from .accounts.storage import load_llm_model
        return load_llm_model(DEEPSEEK_MODEL) or DEEPSEEK_MODEL
    except Exception:  # noqa: BLE001 - 设置读取失败不影响 LLM 初始化
        return DEEPSEEK_MODEL

# ═══════════════════════════════════════
# 环境变量（数据源凭据只从 .env 读取；缺失时由数据层走降级标注，禁止抛异常）
# ═══════════════════════════════════════
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")       # 缺失 → Tushare 相关数据源降级标注
KAIPANLA_COOKIE = os.getenv("KAIPANLA_COOKIE")   # 缺失 → 竞价数据源降级标注
TDX_PATH = os.getenv("TDX_PATH")                 # 缺失 → 通达信本地数据源降级标注

# R11（2026-08-08，Q5 定案）：博查 Key 仅作可选全网补充，**默认关闭**——
# 即使配置了 Key，BOCHA_SEARCH_ENABLED 不为 true 也不会调用（质量不可用，见 requirements-r11 §七）
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY")                          # 空值不报错，仅影响可选全网补充源
BOCHA_SEARCH_ENABLED = os.getenv("BOCHA_SEARCH_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")

# LLM（保留原配置）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# 设置页可选的 LLM 模型（opencode/DeepSeek 兼容名；用户可自行扩展）
LLM_MODEL_OPTIONS = [
    "deepseek-v4-flash",
    "deepseek-v4",
    "deepseek-r1-0528",
    "deepseek-chat",
    "deepseek-reasoner",
]
# 兼容两种 .env 键名：DEEPSEEK_API_BASE（旧）/ DEEPSEEK_BASE_URL（现用）
DEEPSEEK_BASE_URL = (
    os.getenv("DEEPSEEK_API_BASE")
    or os.getenv("DEEPSEEK_BASE_URL")
    or "https://api.deepseek.com"
)


# ═══════════════════════════════════════
# LLM 实例
# ═══════════════════════════════════════
def _proxy_reachable(timeout: float = 0.8) -> bool:
    """代理客户端（127.0.0.1:7890）是否在运行；未运行直接走直连，避免每请求超时惩罚。"""
    import socket

    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", 7890))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _llm_http_client():
    """LLM 传输：优先代理（127.0.0.1:7890），代理不可用时自动切直连。

    2026-08-04 实测：opencode.ai 直连 POST /chat/completions 可用（200）；
    代理客户端未启动时不再让聊天报 Connection error。
    """
    import httpx

    class _FallbackTransport(httpx.BaseTransport):
        def __init__(self):
            self._proxy = httpx.HTTPTransport(proxy="http://127.0.0.1:7890")
            self._direct = httpx.HTTPTransport()
            self._use_proxy = _proxy_reachable()

        def handle_request(self, request):
            if self._use_proxy:
                try:
                    return self._proxy.handle_request(request)
                except (httpx.ConnectError, httpx.ProxyError, httpx.ReadTimeout, httpx.ConnectTimeout):
                    self._use_proxy = False  # 代理不可用：后续全部直连，避免每请求双重超时
            return self._direct.handle_request(request)

        def close(self):
            self._proxy.close()
            self._direct.close()

    return httpx.Client(transport=_FallbackTransport(), timeout=120)


def make_llm(temperature: float = 0.7, timeout: float = 120, max_retries: int = 2):
    """统一 LLM 工厂：代理优先、代理不可用自动直连（opencode.ai 直连实测可用）。

    2026-08-06 修复：DEEPSEEK_API_KEY 缺失/为空时返回 None（而非抛 OpenAIError），
    保证 config 模块可导入——无 Key 时由引擎走规则引擎降级（铁律 7），而不是整个
    服务崩在导入期（此前 health 接口/复盘在无 Key 环境直接报错）。
    """
    if not DEEPSEEK_API_KEY:
        return None
    return ChatOpenAI(
        model=_llm_settings_model(),
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        http_client=_llm_http_client(),
    )


llm = make_llm(temperature=0.7, timeout=120, max_retries=2)

# 低温度版本（做判断、总结时用）
llm_strict = make_llm(temperature=0.1, timeout=120, max_retries=2)


def set_llm_model(model: str) -> dict:
    """设置页持久化 LLM 模型并立即重建 llm/llm_strict 实例。

    - 写入 data/settings/llm.json（后续进程启动也读取该设置）；
    - 更新模块级 DEEPSEEK_MODEL，并重新创建 llm / llm_strict；
    - 无 Key / 创建失败时 llm 可能为 None，由调用方走既有降级逻辑。
    """
    global DEEPSEEK_MODEL, llm, llm_strict
    model = str(model or "").strip()
    if not model:
        raise ValueError("模型名称不能为空")
    try:
        from .accounts.storage import save_llm_model
        save_llm_model(model)
    except Exception as exc:  # noqa: BLE001 - 落盘失败不阻断内存切换
        raise RuntimeError(f"保存模型设置失败：{exc}") from exc
    DEEPSEEK_MODEL = model
    llm = make_llm(temperature=0.7, timeout=120, max_retries=2)
    llm_strict = make_llm(temperature=0.1, timeout=120, max_retries=2)
    return {"ok": True, "model": model}



# ═══════════════════════════════════════
# Langfuse 遥测（凭据齐全即启用；单例复用，避免每次 LLM 调用新建 client/handler）
# ═══════════════════════════════════════
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
# 兼容 LANGFUSE_BASE_URL / LANGFUSE_HOST 两种键名
LANGFUSE_BASE_URL = (
    os.getenv("LANGFUSE_BASE_URL")
    or os.getenv("LANGFUSE_HOST")
    or "https://cloud.langfuse.com"
)
# LANGFUSE_ENABLED=0/false/off/no 可显式关闭；默认 auto（凭据齐全即启用）
LANGFUSE_ENABLED = os.getenv("LANGFUSE_ENABLED", "auto").strip().lower()

_langfuse_callback = None
_langfuse_callback_attempted = False


def langfuse_configured() -> bool:
    """凭据齐全（PUBLIC + SECRET）且未被显式关闭才算可记录。"""
    if LANGFUSE_ENABLED in ("0", "false", "off", "no"):
        return False
    return bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)


# ═══════════════════════════════════════════════════════════════════════
# job 级 LLM 回调开关（2026-08-12）
# 前端页面发起的复盘 → trace=true → LLM 级 Langfuse 回调开启（面板可见全流程）；
# 脚本批量复盘 → trace=false → 关闭（性能 + 不刷屏）。ContextVar 随线程上下文传播，
# 多 job 并发互不干扰；_invoke_llm 在调用方线程求值 _callbacks()，保证读到本开关。
# ═══════════════════════════════════════════════════════════════════════
_langfuse_callbacks_var = contextvars.ContextVar("langfuse_callbacks_on", default=False)


def langfuse_callbacks_enabled() -> bool:
    """当前上下文是否强制开启 LLM 级 Langfuse 回调（默认 False）。"""
    return _langfuse_callbacks_var.get()


@contextlib.contextmanager
def langfuse_callbacks_scope(flag: bool):
    """with 块内强制开启/关闭 LLM 级 Langfuse 回调；退出自动恢复上一状态。"""
    token = _langfuse_callbacks_var.set(bool(flag))
    try:
        yield
    finally:
        _langfuse_callbacks_var.reset(token)


def langfuse_callback():
    """Langfuse LangChain 回调单例；任何失败返回 None（绝不抛错、绝不阻塞 LLM 调用）。

    2026-08-06 修复：此前 graph._callbacks() 每次 LLM 调用都新建 CallbackHandler，
    监控故障（网络/配置）可能把 LLM 调用拖入失败 → 复盘「一直降级」；现改为进程级
    单例，构造失败即缓存 None，后续调用直接跳过监控，与 LLM 本体故障完全隔离。
    """
    global _langfuse_callback, _langfuse_callback_attempted
    if _langfuse_callback_attempted:
        return _langfuse_callback
    _langfuse_callback_attempted = True
    if not langfuse_configured():
        return None
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        # langfuse 4.x：CallbackHandler 仅接受 public_key/trace_context，
        # secret_key 与 host 从环境变量读取（config 已 load_dotenv 注入 LANGFUSE_*）
        # 2026-08-06 修复（Langfuse 捕获不到分析师 trace 的根因）：
        # langfuse.get_client(public_key) 是「先查已注册实例」的单例模式——若进程里
        # 从未用同 public_key 显式构造过 Langfuse 客户端，则返回 disabled client
        # （tracing_enabled=False，日志提示 "No Langfuse client with public key ... has
        # been initialized. Skipping tracing..."），所有 LLM span 静默丢弃。
        # 因此必须先显式构造 Langfuse(...) 注册实例，再建 CallbackHandler。
        # 2026-08-06 修复：Langfuse OTLP 导出（opentelemetry OTLPSpanExporter）每次
        # 请求都读环境代理（HTTP_PROXY/ALL_PROXY 等），沙箱假代理 127.0.0.1:9 与用户
        # 7890 代理都会导致导出静默失败——Langfuse 面板捕获不到复盘/聊天 LLM trace。
        # 方案：构造期间清理全部代理变量（exporter 创建时不带代理），并**持久化**追加
        # NO_PROXY=cloud.langfuse.com（导出线程异步运行，须保证其请求时仍绕过代理）；
        # cloud.langfuse.com 直连实测 200，与数据层/LLM 的代理使用互不影响。
        _proxy_env = {k: os.environ.pop(k, None) for k in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
        )}
        try:
            Langfuse(
                public_key=LANGFUSE_PUBLIC_KEY,
                secret_key=LANGFUSE_SECRET_KEY,
                host=LANGFUSE_BASE_URL,
            )
            _langfuse_callback = CallbackHandler(public_key=LANGFUSE_PUBLIC_KEY)
        finally:
            for _k, _v in _proxy_env.items():
                if _v is not None:
                    os.environ[_k] = _v
        # 持久化 NO_PROXY：langfuse 域强制直连（导出线程后续请求仍生效）
        for _k in ("NO_PROXY", "no_proxy"):
            _cur = os.environ.get(_k, "")
            if "cloud.langfuse.com" not in _cur:
                os.environ[_k] = (_cur + "," if _cur else "") + "cloud.langfuse.com"
    except Exception:
        _langfuse_callback = None
    return _langfuse_callback


def langfuse_status() -> dict:
    """Langfuse 状态（供启动日志 / /api/health 展示，不泄漏凭据）。"""
    return {
        "enabled": langfuse_configured(),
        "callback_ok": langfuse_callback() is not None,
        "host": LANGFUSE_BASE_URL if langfuse_configured() else None,
        "mode": LANGFUSE_ENABLED,
    }


# ═══════════════════════════════════════════════════════════════════════
# 资讯开关（2026-08-12）
# NEWS_ENABLED=1/true/on/yes 开启资讯（财新/央视/新浪快讯等）；
# 默认关闭（屏蔽资讯）——复盘/聊天/实时快讯均不取数、报告不含资讯内容。
# 动态读取 env：测试可随时 monkeypatch，服务重启后读取 .env 生效。
# ═══════════════════════════════════════════════════════════════════════
_NEWS_ON_VALUES = ("1", "true", "on", "yes")


def news_enabled() -> bool:
    """资讯开关是否开启（默认关闭=屏蔽资讯）。"""
    return os.getenv("NEWS_ENABLED", "0").strip().lower() in _NEWS_ON_VALUES
