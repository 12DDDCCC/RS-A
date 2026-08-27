"""E-2 LLM 弹性层: 错误分类 / 指数退避重试 / 截断防护 / 用量记账。

设计来自 pi/earendil-works 解剖 + 差距分析。项目中 LLM 调用是可注入回调
(Callable[[str], str], 见 nodes.py 的 llm_callbacks / generator.py 的
llm_generate), 本模块提供包装原语, 不直接依赖任何 LLM SDK:

  classify_llm_error   错误 -> ErrorKind (决定重试还是立刻失败)
  with_retry           指数退避重试装饰器 (只重试瞬时错; 配额/鉴权错立即抛)
  guard_truncated      截断防护 (第六道防护雏形: 截断的遥感代码被执行会
                       算出错误结果而非报错, 必须在执行前拦下)
  UsageMeter / Usage / estimate_tokens  用量记账 (估算, 非精确分词)
"""
from __future__ import annotations

import functools
import re
import threading
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Callable, Optional, TypeVar

F = TypeVar("F", bound=Callable)


# ---------------------------------------------------------------------------
# A. 错误分类
# ---------------------------------------------------------------------------


class ErrorKind(Enum):
    """LLM 错误类别。FATAL_* 表示重试无意义应立即失败给用户。"""

    RETRYABLE_TRANSIENT = "retryable_transient"  # 限流/超时/5xx/网络抖动: 等一会再试
    FATAL_QUOTA = "fatal_quota"  # 配额/账单耗尽: 重试只会烧日志, 该让用户去充值
    FATAL_INVALID = "fatal_invalid"  # 鉴权/请求非法: 配置问题, 修好之前重试无意义
    UNKNOWN = "unknown"  # 未识别: 保守起见允许重试 (重试无害, 漏重试才亏)


# 关键词表 (全部小写, 子串匹配)。顺序即优先级: 配额/鉴权错先判,
# 否则 "429 insufficient_quota" 这类复合消息会被 429 误判为可重试限流。
_QUOTA_KEYWORDS = (
    "insufficient_quota", "insufficient quota",
    "quota exceeded", "quota_exceeded", "exceeded your quota",
    "billing",
)
_INVALID_KEYWORDS = (
    "invalid api key", "invalid_api_key",
    "authentication", "unauthorized", "401",
    "invalid request", "invalid_request",
    "model not found", "model_not_found",
)
_TRANSIENT_KEYWORDS = (
    # 限流
    "429", "rate limit", "rate_limit", "overloaded",
    # 超时
    "timeout", "timed out",
    # 5xx 服务端错
    "server error", "server had an error", "internal error", "internal server",
    "bad gateway", "service unavailable", "502", "503", "504",
    # 网络错
    "connection", "reset", "refused",
    # 流早断
    "stream closed prematurely", "stream error",
)


def classify_llm_error(exc_or_msg: Exception | str) -> ErrorKind:
    """把 LLM 异常/错误消息分类为 ErrorKind。

    大小写不敏感子串匹配; 异常对象取 str(exc)。未命中任何关键词返回
    UNKNOWN (调用方可选择保守重试)。
    """
    msg = str(exc_or_msg).lower()
    for kw in _QUOTA_KEYWORDS:
        if kw in msg:
            return ErrorKind.FATAL_QUOTA
    for kw in _INVALID_KEYWORDS:
        if kw in msg:
            return ErrorKind.FATAL_INVALID
    for kw in _TRANSIENT_KEYWORDS:
        if kw in msg:
            return ErrorKind.RETRYABLE_TRANSIENT
    return ErrorKind.UNKNOWN


# ---------------------------------------------------------------------------
# B. 重试装饰器
# ---------------------------------------------------------------------------


def with_retry(
    fn: Optional[F] = None,
    *,
    max_attempts: int = 3,
    base_delay_s: float = 0.05,
    max_delay_s: float = 2.0,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> F:
    """指数退避重试装饰器, 兼容 @with_retry 与 @with_retry(max_attempts=2)。

    只重试 RETRYABLE_TRANSIENT 与 UNKNOWN (保守: 漏一次重试的代价高于
    多试一次); FATAL_QUOTA / FATAL_INVALID 立即原样抛出, 不烧重试。

    退避: base_delay_s * 2^(n-1), 上限 max_delay_s。
    on_retry(attempt, error, delay): 观测钩子 (打点/记日志), 第 n 次失败
    后即将睡眠 delay 秒时回调; 最后一次失败不再回调 (不会再重试)。
    """
    if fn is None:
        # 带参用法 @with_retry(...): 返回真正的装饰器
        return functools.partial(  # type: ignore[return-value]
            with_retry,
            max_attempts=max_attempts,
            base_delay_s=base_delay_s,
            max_delay_s=max_delay_s,
            on_retry=on_retry,
        )

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        for attempt in range(1, max_attempts + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 分类后再决定去留
                kind = classify_llm_error(exc)
                if kind in (ErrorKind.FATAL_QUOTA, ErrorKind.FATAL_INVALID):
                    raise  # 致命错: 立即抛出, 不重试
                if attempt >= max_attempts:
                    raise  # 重试耗尽: 抛最后一次异常
                delay = min(base_delay_s * (2 ** (attempt - 1)), max_delay_s)
                if on_retry is not None:
                    on_retry(attempt, exc, delay)
                time.sleep(delay)

    return wrapper  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# C. 截断防护
# ---------------------------------------------------------------------------


class TruncatedOutputError(RuntimeError):
    """LLM 输出被截断。这是第六道防护的雏形: 截断的遥感代码若被执行,
    算出的会是错误结果而非报错——必须在沙箱/云端执行前拦下。"""

    def __init__(self, text: str, hint: str = "启发式判定"):
        self.head = text[:80]
        super().__init__(f"LLM 输出疑似截断 ({hint}): {self.head!r}")


def _looks_truncated(text: str) -> bool:
    """启发式: 代码以冒号/逗号/开括号结尾, 或三类括号计数不平衡。

    不做语法解析 (字符串/注释里的括号会干扰计数), 只是启发式——
    宁可误杀让 LLM 重生成, 不可放过半截代码去执行。
    """
    if text.rstrip().endswith((":", ",", "(", "[", "{")):
        return True
    return any(
        text.count(o) != text.count(c) for o, c in (("(", ")"), ("[", "]"), ("{", "}"))
    )


def guard_truncated(text: str, finish_reason: Optional[str] = None) -> str:
    """截断防护: 拦下疑似被截断的 LLM 代码输出, 正常则原样返回。

    - finish_reason == "length": 模型明确报告因长度截断 -> 必抛
    - finish_reason 缺失: 对长度 >200 的文本做启发式 (结尾字符/括号平衡)
    - finish_reason 为 "stop" 等正常结束信号: 直接放行 (明确信号优先于猜测)
    """
    reason = (finish_reason or "").lower()
    if reason == "length":
        raise TruncatedOutputError(text, hint="finish_reason=length")
    if reason:
        return text  # stop / tool_calls 等: 模型明确正常结束
    if len(text) > 200 and _looks_truncated(text):
        raise TruncatedOutputError(text)
    return text


# ---------------------------------------------------------------------------
# D. 用量记账
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """LLM 用量累计。"""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


_CJK_RE = re.compile(r"[一-鿿]")  # CJK 统一表意文字 (常用汉字)


def estimate_tokens(text: str) -> int:
    """粗估 token 数: len(text)//4, 中文字符按 1.5 倍权重。

    仅用于配额监控的量级估算 (一个汉字信息量约为一个英文字符的 1.5 倍,
    即先 len + 中文字数//2 再 //4), 不是精确分词——精确值以平台返回为准。
    """
    cjk = len(_CJK_RE.findall(text))
    return (len(text) + cjk // 2) // 4


class UsageMeter:
    """线程安全用量记账器。多线程 worker (如后台任务执行器) 共享一个实例。"""

    def __init__(self) -> None:
        self._usage = Usage()
        self._lock = threading.Lock()

    def add(self, *, input: int = 0, output: int = 0) -> None:  # noqa: A002
        """记一次调用: 累加输入/输出 token, calls +1。"""
        with self._lock:
            self._usage.input_tokens += input
            self._usage.output_tokens += output
            self._usage.calls += 1

    def snapshot(self) -> dict:
        """返回当前用量的字典拷贝 (锁内取快照, 不暴露可变内部状态)。"""
        with self._lock:
            return asdict(self._usage)
