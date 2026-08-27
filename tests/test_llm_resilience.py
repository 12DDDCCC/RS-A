"""E-2 LLM 弹性层测试: 错误分类 / 重试退避 / 截断防护 / 用量记账。

覆盖: 分类三类各多样例 + 优先级 + 异常对象输入 / 重试 (第二次成功、耗尽
抛出、FATAL 不重试、退避延迟序列与上限、装饰器两种写法、UNKNOWN 可重试) /
截断 (finish_reason=length、括号不平衡、尾逗号、正常代码、短文本不误判) /
Usage 并发 10 线程各 100 次 / estimate_tokens 中英文与空串。
"""
from __future__ import annotations

import threading

import pytest

from src.agent.llm_resilience import (
    ErrorKind,
    TruncatedOutputError,
    UsageMeter,
    classify_llm_error,
    estimate_tokens,
    guard_truncated,
    with_retry,
)


# ---------------------------------------------------------------------------
# A. 错误分类
# ---------------------------------------------------------------------------


def test_transient_rate_limit_variants():
    """限流类: 429 / rate limit / overloaded 都是可重试瞬时错。"""
    for msg in ("Error code: 429", "rate limit exceeded", "The engine is currently overloaded"):
        assert classify_llm_error(msg) is ErrorKind.RETRYABLE_TRANSIENT, msg


def test_transient_timeout_and_network():
    """超时与网络错: timeout / timed out / connection reset 都是瞬时错。"""
    for msg in ("Request timeout after 30s", "operation timed out", "connection refused"):
        assert classify_llm_error(msg) is ErrorKind.RETRYABLE_TRANSIENT, msg


def test_transient_server_5xx_and_stream():
    """5xx 服务端错与流早断: server error / internal error / 流中断。"""
    for msg in (
        "500 Internal Server Error",
        "The server had an error while processing",
        "stream closed prematurely",
    ):
        assert classify_llm_error(msg) is ErrorKind.RETRYABLE_TRANSIENT, msg


def test_fatal_quota():
    """配额类: insufficient_quota / quota exceeded / billing 是致命错。"""
    for msg in ("insufficient_quota", "You exceeded your quota", "billing hard limit reached"):
        assert classify_llm_error(msg) is ErrorKind.FATAL_QUOTA, msg


def test_fatal_invalid():
    """鉴权/请求非法类: invalid api key / 401 / model not found 是致命错。"""
    for msg in (
        "invalid api key provided",
        "401 Unauthorized",
        "authentication failed",
        "The model does not exist: invalid request",
        "model not found",
    ):
        assert classify_llm_error(msg) is ErrorKind.FATAL_INVALID, msg


def test_quota_beats_429():
    """复合消息优先级: 429 + insufficient_quota 应判配额错而非限流。"""
    assert classify_llm_error("429 insufficient_quota") is ErrorKind.FATAL_QUOTA


def test_unknown_for_unmatched():
    """未命中关键词 -> UNKNOWN (调用方可保守重试)。"""
    assert classify_llm_error("完全无关的奇怪错误") is ErrorKind.UNKNOWN


def test_accepts_exception_objects():
    """异常对象取 str(exc) 参与分类。"""
    assert classify_llm_error(ConnectionError("connection reset by peer")) is (
        ErrorKind.RETRYABLE_TRANSIENT
    )
    assert classify_llm_error(RuntimeError("invalid request body")) is ErrorKind.FATAL_INVALID


def test_classification_case_insensitive():
    """大小写不敏感: RATE LIMIT / Insufficient_Quota 同样命中。"""
    assert classify_llm_error("RATE LIMIT") is ErrorKind.RETRYABLE_TRANSIENT
    assert classify_llm_error("Insufficient_Quota") is ErrorKind.FATAL_QUOTA


# ---------------------------------------------------------------------------
# B. 重试装饰器
# ---------------------------------------------------------------------------


def test_retry_succeeds_second_attempt():
    """第一次限流失败, 第二次成功 -> 返回成功值, on_retry 观测到 1 次。"""
    calls = {"n": 0}
    retries: list[tuple[int, float]] = []

    @with_retry(max_attempts=3, on_retry=lambda a, e, d: retries.append((a, d)))
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 rate limit")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2
    assert len(retries) == 1  # 只重试那一次触发了 on_retry
    assert retries[0][0] == 1  # 第 1 次失败后回调


def test_retry_exhausted_raises():
    """一直瞬时失败 -> 重试耗尽后抛出最后一次异常。"""
    calls = {"n": 0}

    @with_retry(max_attempts=3, base_delay_s=0.0)
    def always_fail() -> None:
        calls["n"] += 1
        raise RuntimeError("timeout")

    with pytest.raises(RuntimeError, match="timeout"):
        always_fail()
    assert calls["n"] == 3  # 恰好尝试 max_attempts 次


def test_fatal_not_retried_immediately():
    """致命错 (配额/鉴权) 一次都不重试, 立即抛出。"""
    for fatal_msg in ("insufficient_quota", "invalid api key"):
        calls = {"n": 0}

        @with_retry(max_attempts=3)
        def bad() -> None:
            calls["n"] += 1
            raise RuntimeError(fatal_msg)

        with pytest.raises(RuntimeError):
            bad()
        assert calls["n"] == 1, fatal_msg


def test_unknown_error_is_retried():
    """UNKNOWN 保守可重试: 第二次成功即通过。"""
    calls = {"n": 0}

    @with_retry(max_attempts=3, base_delay_s=0.0)
    def weird() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("某种没见过的错误")
        return "ok"

    assert weird() == "ok"
    assert calls["n"] == 2


def test_backoff_delays_exponential_with_cap():
    """退避序列 base*2^(n-1) 且封顶 max_delay_s: 0.01 -> 0.02 -> 0.02(cap)。"""
    delays: list[float] = []

    @with_retry(max_attempts=4, base_delay_s=0.01, max_delay_s=0.02,
                on_retry=lambda a, e, d: delays.append(d))
    def three_fails() -> str:
        if len(delays) < 3:
            raise RuntimeError("timeout")
        return "ok"

    assert three_fails() == "ok"
    assert delays == [0.01, 0.02, 0.02]


def test_decorator_bare_form():
    """裸装饰器 @with_retry (不带括号) 可用且保留函数名。"""

    @with_retry
    def stable() -> int:
        return 42

    assert stable() == 42
    assert stable.__name__ == "stable"


def test_decorator_parameterized_form_preserves_args():
    """带参装饰器: 透传位置/关键字参数给被包装函数。"""

    @with_retry(max_attempts=2, base_delay_s=0.0)
    def add(a: int, b: int = 0) -> int:
        return a + b

    assert add(1, b=2) == 3


# ---------------------------------------------------------------------------
# C. 截断防护
# ---------------------------------------------------------------------------


def test_finish_reason_length_raises():
    """模型明确报告 finish_reason=length -> 必抛, 即使文本看似完整。"""
    with pytest.raises(TruncatedOutputError, match="finish_reason=length"):
        guard_truncated("print('看起来完整')", finish_reason="length")


def test_unbalanced_brackets_long_text_raises():
    """无 finish_reason 的长文本括号不平衡 -> 判截断抛出。"""
    unbalanced = "x = (a + b) * c\n" * 40 + "y = compute_value(\n"  # 多一个 (
    assert len(unbalanced) > 200
    with pytest.raises(TruncatedOutputError):
        guard_truncated(unbalanced)


def test_trailing_comma_or_colon_raises():
    """长文本以逗号/冒号/开括号结尾 -> 中途断句, 判截断。"""
    for tail in ("values = (1, 2,", "if region:", "call_me("):
        text = "do_something()\n" * 40 + tail
        with pytest.raises(TruncatedOutputError):
            guard_truncated(text)


def test_normal_code_passes():
    """正常完整代码: 括号平衡且正常结尾 -> 原样返回 (无 finish_reason)。"""
    code = (
        "import pie\n"
        "# NDVI = (NIR - Red) / (NIR + Red)\n"
        "img = pie.ImageCollection('S2')\n"
        "ndvi = img.normalizedDifference(['B8', 'B4'])\n"
        "ndvi = ndvi.clip(region)\n"
        "task = ndvi.export(name='ndvi')\n"
        "task.start()\n"
    ) * 5  # 拉长到 >200 字符, 证明长而平衡不误判
    assert guard_truncated(code) == code


def test_finish_reason_stop_passes():
    """finish_reason=stop: 模型明确正常结束, 直接放行 (即使不平衡的散文)。"""
    text = "这是分析结论, 无需再续写"
    assert guard_truncated(text, finish_reason="stop") == text


def test_short_text_not_flagged():
    """短文本 (<=200) 即使不平衡也不误判 (启发式仅对长代码生效)。"""
    short = "x = f(1, 2"  # 不平衡, 但太短
    assert guard_truncated(short) == short


def test_error_contains_head_80_chars():
    """异常消息含截断文本前 80 字符, 便于日志定位断点。"""
    text = "断" * 300
    with pytest.raises(TruncatedOutputError) as ei:
        guard_truncated(text + "(", finish_reason="length")
    assert text[:80] in str(ei.value)


# ---------------------------------------------------------------------------
# D. 用量记账
# ---------------------------------------------------------------------------


def test_usage_meter_concurrent():
    """并发正确性: 10 线程各 add 100 次 -> calls=1000, token 无丢失。"""
    meter = UsageMeter()

    def worker() -> None:
        for _ in range(100):
            meter.add(input=3, output=5)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert meter.snapshot() == {"input_tokens": 3000, "output_tokens": 5000, "calls": 1000}


def test_usage_meter_snapshot_defaults():
    """新 meter 快照全 0; snapshot 返回拷贝不暴露内部状态。"""
    meter = UsageMeter()
    snap = meter.snapshot()
    assert snap == {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    snap["calls"] = 999  # 改拷贝不影响内部
    assert meter.snapshot()["calls"] == 0


def test_estimate_tokens_english():
    """英文: len//4 简单估算 (空格也计入字符数)。"""
    assert estimate_tokens("a" * 100) == 25
    assert estimate_tokens("hello world example") == len("hello world example") // 4


def test_estimate_tokens_chinese():
    """中文: 1.5 倍权重 -> (len + 中文字数//2) // 4。"""
    zh = "北京植被覆盖变化监测"  # 10 个汉字
    assert estimate_tokens(zh) == (10 + 10 // 2) // 4  # 15//4 = 3


def test_estimate_tokens_mixed_and_empty():
    """中英混合与空串: 空串为 0, 混合按各自字符计入。"""
    assert estimate_tokens("") == 0
    mixed = "a" * 8 + "植被"  # len=10, cjk=2 -> (10+1)//4
    assert estimate_tokens(mixed) == 2
