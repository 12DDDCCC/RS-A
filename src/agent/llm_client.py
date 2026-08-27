"""F2 LLM 客户端: OpenAI 兼容协议打通国内供应商 (deepseek/zhipu/qwen/custom)。

GeoAgent llm.py 的声明式 PROVIDERS 模式 + 本项目 E-2 弹性层接线:

  - 四家供应商全部走 openai>=1.0 SDK (换 base_url 即切端点, 不引各家专有 SDK)
  - _chat 是全项目唯一真实出网口: 网络调用过 with_retry (瞬时错指数退避,
    配额/鉴权致命错立即抛), 返回内容过 guard_truncated (截断防护)
  - UsageMeter 记账: 响应带 usage 用精确值, 缺失退回字符估算
  - 档位: light (clarify/plan/diagnose, 快而便宜) / heavy (generate/review)
  - 探测不到任何 key -> LLMNotConfiguredError (main._make_callbacks 捕获后
    返回空 dict, 零 key 现状完全不变)

选型: env REMOTE_SENSING_LLM_PROVIDER 显式指定, 否则按 key 存在性探测
deepseek > zhipu > qwen > custom。
模型名可被 env {PROVIDER}_LIGHT_MODEL / {PROVIDER}_HEAVY_MODEL 覆盖。
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional

from src.agent.llm_resilience import (
    UsageMeter,
    estimate_tokens,
    guard_truncated,
    with_retry,
)

# 推理模型思维链标签: MiniMax-M3 / DeepSeek-R1 等会输出 <think>...</think>。
# 我们的回调要纯内容 (JSON/代码), think 段必须剥掉, 否则 clarify 的 JSON
# 解析和 generate 的代码白名单都会被污染。
_REASONING_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL)


def strip_reasoning(text: str) -> str:
    """剥离推理模型的 <think>...</think> 段 (无标签则原样返回)。"""
    return _REASONING_RE.sub("", text).strip()

# 选型 env (显式指定供应商, 免探测)
PROVIDER_ENV_VAR = "REMOTE_SENSING_LLM_PROVIDER"

# 声明式供应商注册表: 全部走 OpenAI 兼容协议 (package 都是 openai)。
# custom 档: base_url 与 light/heavy 模型名都从环境读 (自建/中转端点),
# 其 light/heavy 存的是 env 变量名而非默认模型名。
PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {
        "env_var": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "light": "deepseek-chat",
        "heavy": "deepseek-chat",
        "package": "openai",
    },
    "zhipu": {
        "env_var": "ZHIPU_API_KEY",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "light": "glm-4-flash",
        "heavy": "glm-4-plus",
        "package": "openai",
    },
    "qwen": {
        "env_var": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "light": "qwen-turbo",
        "heavy": "qwen-plus",
        "package": "openai",
    },
    "minimax": {
        # MiniMax 开放平台 (platform.minimaxi.com) OpenAI 兼容端点 (国内)。
        # MiniMax-M3: 456B MoE, 1M 输入上下文 —— 长知识库注入友好。
        # supports_thinking_off: M3 原生思考开关 (extra_body={"thinking":{"type":"disabled"}}),
        # 思考与正文共享 max_tokens 配额 -> light 档关闭思考 (快/省/正文独占 4096),
        # heavy 档开启 (写遥感代码需深度推理, 16384 预算)。仅 M3 支持, M2.x 无效。
        "env_var": "MINIMAX_API_KEY",
        "base_url": "https://api.minimaxi.com/v1",
        "light": "MiniMax-Text-01",
        "heavy": "MiniMax-Text-01",
        "package": "openai",
        "supports_thinking_off": "true",
    },
    "custom": {
        "env_var": "CUSTOM_LLM_API_KEY",
        "base_url_env": "CUSTOM_LLM_BASE_URL",
        "light": "CUSTOM_LLM_LIGHT_MODEL",  # env 变量名 (custom 无内置默认模型)
        "heavy": "CUSTOM_LLM_HEAVY_MODEL",
        "package": "openai",
    },
}

# 探测优先级 (key 存在即选中, 先到先得)
PROVIDER_ORDER = ("deepseek", "zhipu", "qwen", "minimax", "custom")

# 五回调共用的 system 提示: 节点 prompt 里已有完整格式要求 (JSON/代码),
# 这里只约束输出纪律, 不重复业务内容。
_SYSTEM_PROMPT = (
    "你是遥感分析助手的组成部分。严格按提示词中的格式要求作答: "
    "要求 JSON 就只输出 JSON, 要求代码就只输出代码; 不寒暄、不解释你做了什么。"
)


class LLMNotConfiguredError(RuntimeError):
    """未配置任何 LLM 凭证。零 key 环境的显式信号 (main 捕获后返回空回调)。"""


# ---------------------------------------------------------------------------
# 供应商选型与配置解析
# ---------------------------------------------------------------------------


def _env(name: str) -> str:
    """读 env 并去空白 (空串视同未设置)。"""
    return os.environ.get(name, "").strip()


def _provider_ready(name: str) -> bool:
    """供应商是否配置齐: key 必须有; custom 型还必须有 base_url。"""
    cfg = PROVIDERS[name]
    if not _env(cfg["env_var"]):
        return False
    url_env = cfg.get("base_url_env")
    return not url_env or bool(_env(url_env))


def detect_provider() -> str:
    """选型: REMOTE_SENSING_LLM_PROVIDER 显式指定 > 按 key 存在性探测。

    显式指定但 key 缺失 -> 报错 (用户意图明确, 不静默降级到别家);
    探测不到任何一家 -> LLMNotConfiguredError。
    """
    explicit = _env(PROVIDER_ENV_VAR).lower()
    if explicit:
        if explicit not in PROVIDERS:
            raise LLMNotConfiguredError(
                f"{PROVIDER_ENV_VAR}={explicit} 不在支持列表: {sorted(PROVIDERS)}"
            )
        if not _provider_ready(explicit):
            raise LLMNotConfiguredError(
                f"已指定供应商 {explicit}, 但 {PROVIDERS[explicit]['env_var']} 未设置"
            )
        return explicit
    for name in PROVIDER_ORDER:
        if _provider_ready(name):
            return name
    raise LLMNotConfiguredError(
        "未配置任何 LLM 凭证: 请设置 DEEPSEEK_API_KEY / ZHIPU_API_KEY / "
        "DASHSCOPE_API_KEY / MINIMAX_API_KEY 之一 "
        "(或 CUSTOM_LLM_API_KEY + CUSTOM_LLM_BASE_URL)"
    )


def _resolve_base_url(cfg: dict[str, str]) -> str:
    """端点解析: custom 型从 env 读, 其余用内置。"""
    url_env = cfg.get("base_url_env")
    return _env(url_env) if url_env else cfg["base_url"]


def _resolve_model(provider: str, tier: str) -> str:
    """档位模型名: 单模型模式 ({PROVIDER}_MODEL, D3 决议) > 档位覆盖 > 内置默认。

    custom 型的内置默认值本身是 env 变量名, 从环境读 (可能为空,
    由 real_callbacks 统一报 LLMNotConfiguredError)。
    """
    # D3 单模型模式: {PROVIDER}_MODEL; custom 兼容 CUSTOM_LLM_MODEL 命名
    candidates = [f"{provider.upper()}_MODEL"]
    if provider == "custom":
        candidates.append("CUSTOM_LLM_MODEL")
    for name in candidates:
        single = _env(name)
        if single:
            return single
    override = _env(f"{provider.upper()}_{tier.upper()}_MODEL")
    if override:
        return override
    default = PROVIDERS[provider][tier]
    if "base_url_env" in PROVIDERS[provider]:
        return _env(default)
    return default


# ---------------------------------------------------------------------------
# 核心调用
# ---------------------------------------------------------------------------


def _make_client(provider_cfg: dict[str, str], api_key: str) -> Any:
    """构造 OpenAI 兼容客户端。延迟 import: 未装包给精确安装提示而非裸 ImportError。"""
    try:
        import openai
    except ImportError as exc:
        package = provider_cfg["package"]
        raise ImportError(
            f"LLM 客户端依赖 {package}>=1.0 (OpenAI 兼容协议): "
            f"请执行 pip install {package}"
        ) from exc
    return openai.OpenAI(api_key=api_key, base_url=_resolve_base_url(provider_cfg))


def _chat(
    provider_cfg: dict[str, str],
    model: str,
    system: str,
    user: str,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    meter: Optional[UsageMeter] = None,
    thinking_enabled: bool = True,
) -> str:
    """单次对话补全 (OpenAI 兼容), 弹性层接线点。

    - with_retry 只包网络调用: 瞬时错退避重试, 配额/鉴权致命错立即抛 (E-2)
    - guard_truncated 在重试之外: 截断多为确定性错 (max_tokens 不够),
      同参数重试只会再烧一次 token -> 立即上抛 TruncatedOutputError,
      由上层 (生成器反馈环/任务) 当生成失败处理
    - meter 记账: 响应带 usage 用精确 token (0/None 视为缺失), 缺失退回估算
    - thinking_enabled=False: 支持思考开关的供应商 (MiniMax-M3) 关闭思考 ——
      思考与正文共享 max_tokens 配额, 轻任务关思考让正文独占预算 (快且省)
    """
    api_key = _env(provider_cfg["env_var"])
    if not api_key:
        raise LLMNotConfiguredError(
            f"{provider_cfg['env_var']} 未设置, 无法调用模型 {model}"
        )
    client = _make_client(provider_cfg, api_key)

    @with_retry()
    def _call() -> tuple[str, Optional[str]]:
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        # 思考开关仅对声明支持的供应商透传 (MiniMax-M3): 其他供应商的
        # OpenAI 兼容端点可能对未知字段严格, 不冒险
        if provider_cfg.get("supports_thinking_off") and not thinking_enabled:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        # 记账在重试环内: 即使最终截断被拒, 已烧的 token 也如实入账
        if meter is not None:
            usage = getattr(resp, "usage", None)
            meter.add(
                input=getattr(usage, "prompt_tokens", None) or estimate_tokens(system + user),
                output=getattr(usage, "completion_tokens", None) or estimate_tokens(
                    choice.message.content or ""
                ),
            )
        return choice.message.content or "", getattr(choice, "finish_reason", None)

    content, finish_reason = _call()
    guarded = guard_truncated(content, finish_reason)
    # 截断防护之后再剥思维链: M3 完整响应的 think 必闭合 (截断已被上面拒绝)
    return strip_reasoning(guarded)


# ---------------------------------------------------------------------------
# 生产回调工厂
# ---------------------------------------------------------------------------


# ---------- 思考挡位 (两挡 OFF/ON, 小孟同学决议) ----------
# M3 特性: 开思考即与 max_tokens 共享且无节制 -> ON 挡预算封顶 24k
# (1-3 分钟级); OFF 正文独占 8k 秒级响应。运行时可经 /thinking API 切换。
THINKING_LEVELS: dict[str, dict] = {
    "OFF": {"enabled": False, "max_tokens": 8192},   # 默认: 秒级响应
    "ON":  {"enabled": True,  "max_tokens": 24576},  # 疑难任务开思考
}
_THINKING_ENV = "REMOTE_SENSING_THINKING"
# 进程级运行时覆盖 (POST /thinking 切换, 免重启); None=按 env 初始值
_thinking_mode_override: str | None = None

# 旧三挡 env 值兼容映射: off->OFF; low/high/on 一律视作 ON
_LEGACY_ENV_MAP = {"off": "OFF", "low": "ON", "high": "ON", "on": "ON"}


def set_thinking_mode(mode: str) -> dict:
    """运行时切换思考挡位 (OFF/ON, 大小写不敏感), 立即对后续任务生效。

    返回切换后配置 {mode, enabled, max_tokens}; 非法挡位抛 ValueError。
    """
    global _thinking_mode_override
    m = str(mode).strip().upper()
    if m not in THINKING_LEVELS:
        raise ValueError(f"思考挡位仅支持 OFF/ON, 收到: {mode!r}")
    _thinking_mode_override = m
    return {"mode": m, **THINKING_LEVELS[m]}


def get_thinking_mode() -> dict:
    """当前生效挡位 (含运行时覆盖)。"""
    lvl, _ = _get_thinking_level()
    return {"mode": lvl, **THINKING_LEVELS[lvl]}


def _get_thinking_level() -> tuple[str, dict]:
    """挡位优先级: 运行时覆盖 > env 初始值 (旧三挡值兼容映射)。"""
    if _thinking_mode_override:
        lvl = _thinking_mode_override
    else:
        lvl = _LEGACY_ENV_MAP.get(_env(_THINKING_ENV).lower(), "OFF")
    return lvl, THINKING_LEVELS[lvl]


def real_callbacks(
    on_usage: Optional[Callable[[str, dict], None]] = None,
) -> dict[str, Callable[[str], str]]:
    """构造 agent 五回调 (单模型模式, D3 决议)。

    全部回调使用同一模型 ({PROVIDER}_MODEL 或 PROVIDERS 内置默认);
    思考挡位由 REMOTE_SENSING_THINKING 统一控制 (off 默认)。
    on_usage(name, usage): 每次调用后收到 (回调名, 本次用量 dict,
    input_tokens/output_tokens/calls 均为本 call 增量); None 则跳过。
    探测不到供应商或 custom 未配模型名 -> LLMNotConfiguredError。
    """
    provider = detect_provider()
    cfg = PROVIDERS[provider]
    model = _resolve_model(provider, "heavy")
    if not model:
        raise LLMNotConfiguredError(
            f"供应商 {provider} 未配置模型名: 请设置 "
            f"{cfg['heavy']} 或 {provider.upper()}_MODEL"
        )

    think_lvl, think_cfg = _get_thinking_level()
    max_tokens = think_cfg["max_tokens"]
    thinking_enabled = think_cfg["enabled"]

    meter = UsageMeter()  # 五回调共享一本账 (闭包持有)

    def _make(name: str, temperature: float) -> Callable[[str], str]:
        def cb(prompt: str) -> str:
            before = meter.snapshot()
            text = _chat(
                cfg, model, _SYSTEM_PROMPT, prompt,
                temperature=temperature, meter=meter,
                max_tokens=max_tokens,
                thinking_enabled=thinking_enabled,
            )
            if on_usage is not None:
                after = meter.snapshot()
                on_usage(name, {k: after[k] - before[k] for k in after})
            return text

        return cb

    return {
        # 单模型 + 思考挡位 (D3): 全回调同一模型;
        # off=思考关(默认, 正文独占预算) / low|high=思考开+大预算(疑难场景)
        "clarify": _make("clarify", 0.0),
        "plan": _make("plan", 0.0),
        "diagnose": _make("diagnose", 0.0),
        "generate": _make("generate", 0.1),
        "review": _make("review", 0.1),
    }
