"""F2 LLM 客户端测试 (全程不真调网络: sys.modules 注入假 openai 模块)。

覆盖: PROVIDERS 表完整性 / 探测优先级 (显式指定 + key 存在性 + 全空报错) /
_chat 弹性接线 (限流重试、致命错不重试、重试耗尽、guard_truncated 截断拒收、
usage 精确记账与估算兜底、请求参透传) / real_callbacks 五键与 light-heavy
档位 / env 覆盖模型名 / on_usage 增量用量 / main._make_callbacks 零 key 返回空。
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from src.agent import llm_client as lc
from src.agent.llm_resilience import (
    TruncatedOutputError,
    UsageMeter,
    estimate_tokens,
)

# 参与选型的全部 env (夹具统一清空, 隔离宿主机环境)
_ALL_LLM_ENV = (
    "REMOTE_SENSING_LLM_PROVIDER",
    "DEEPSEEK_API_KEY", "ZHIPU_API_KEY", "DASHSCOPE_API_KEY",
    "MINIMAX_API_KEY", "MINIMAX_MODEL",
    "REMOTE_SENSING_THINKING",
    "CUSTOM_LLM_API_KEY", "CUSTOM_LLM_BASE_URL",
    "CUSTOM_LLM_LIGHT_MODEL", "CUSTOM_LLM_HEAVY_MODEL",
    "DEEPSEEK_LIGHT_MODEL", "DEEPSEEK_HEAVY_MODEL",
    "ZHIPU_LIGHT_MODEL", "ZHIPU_HEAVY_MODEL",
    "MINIMAX_LIGHT_MODEL", "MINIMAX_HEAVY_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    """清空所有 LLM 相关 env, 各测试按需设置。"""
    for name in _ALL_LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


# ---------------------------------------------------------------------------
# 假 openai SDK (够用即可: OpenAI(...) -> chat.completions.create(**kwargs))
# ---------------------------------------------------------------------------


def _resp(
    content: str,
    finish: str = "stop",
    in_tok: Optional[int] = None,
    out_tok: Optional[int] = None,
) -> SimpleNamespace:
    """构造形如 openai SDK 的 chat 响应 (choices[0].message/finish_reason, usage)。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason=finish
        )],
        usage=SimpleNamespace(prompt_tokens=in_tok, completion_tokens=out_tok),
    )


def _install_fake_openai(monkeypatch, script: list) -> SimpleNamespace:
    """把假 openai 模块注入 sys.modules。script 依次回放 (Exception -> 抛出)。

    返回观测句柄: .calls = 每次 create 的 kwargs; .ctor = 每次 OpenAI(...) 的
    kwargs (验证 api_key/base_url 接线)。脚本耗尽再被调用 -> 断言失败 (抓多余出网)。
    """
    state = SimpleNamespace(calls=[], ctor=[])

    def _create(**kwargs: Any) -> Any:
        state.calls.append(kwargs)
        if not script:
            raise AssertionError("假响应脚本耗尽: 出现了预期外的额外调用")
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _openai_ctor(**kwargs: Any) -> SimpleNamespace:
        state.ctor.append(kwargs)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
        )

    fake_mod = types.ModuleType("openai")
    fake_mod.OpenAI = _openai_ctor
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    return state


# ---------------------------------------------------------------------------
# A. PROVIDERS 注册表
# ---------------------------------------------------------------------------


def test_providers_table_complete():
    """五家供应商; 每家 env_var/light/heavy/package 齐全, 固定四家带 https base_url。"""
    assert set(lc.PROVIDERS) == {"deepseek", "zhipu", "qwen", "minimax", "custom"}
    for name, cfg in lc.PROVIDERS.items():
        assert cfg["env_var"] and cfg["light"] and cfg["heavy"], name
        assert cfg["package"] == "openai", name  # 全走 OpenAI 兼容协议
        if name != "custom":
            assert cfg["base_url"].startswith("https://"), name
    assert lc.PROVIDERS["custom"]["base_url_env"] == "CUSTOM_LLM_BASE_URL"
    assert set(lc.PROVIDER_ORDER) == set(lc.PROVIDERS)


def test_providers_default_models():
    """内置默认模型名: deepseek 双档同模型, zhipu/qwen 区分快慢档。"""
    ds = lc.PROVIDERS["deepseek"]
    assert ds["light"] == ds["heavy"] == "deepseek-chat"
    assert (lc.PROVIDERS["zhipu"]["light"], lc.PROVIDERS["zhipu"]["heavy"]) == (
        "glm-4-flash", "glm-4-plus",
    )
    assert (lc.PROVIDERS["qwen"]["light"], lc.PROVIDERS["qwen"]["heavy"]) == (
        "qwen-turbo", "qwen-plus",
    )


# ---------------------------------------------------------------------------
# B. 探测优先级
# ---------------------------------------------------------------------------


def test_detect_deepseek_when_key_present(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-1")
    assert lc.detect_provider() == "deepseek"


def test_detect_priority_deepseek_over_zhipu(monkeypatch):
    """同时有 deepseek 与 zhipu 的 key -> deepseek 优先。"""
    monkeypatch.setenv("ZHIPU_API_KEY", "zk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk")
    assert lc.detect_provider() == "deepseek"


def test_detect_zhipu_when_only_zhipu(monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "zk")
    assert lc.detect_provider() == "zhipu"


def test_detect_qwen_when_only_qwen(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qk")
    assert lc.detect_provider() == "qwen"


def test_detect_explicit_overrides_probe(monkeypatch):
    """REMOTE_SENSING_LLM_PROVIDER 显式指定优先于存在性探测。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qk")
    monkeypatch.setenv("REMOTE_SENSING_LLM_PROVIDER", "qwen")
    assert lc.detect_provider() == "qwen"


def test_detect_explicit_unknown_provider_raises(monkeypatch):
    """显式指定不认识的供应商 -> 报错 (不静默猜)。"""
    monkeypatch.setenv("REMOTE_SENSING_LLM_PROVIDER", "gpt4all")
    with pytest.raises(lc.LLMNotConfiguredError, match="不在支持列表"):
        lc.detect_provider()


def test_detect_explicit_missing_key_raises(monkeypatch):
    """显式指定 deepseek 但没 key -> 报错, 不降级到别家。"""
    monkeypatch.setenv("REMOTE_SENSING_LLM_PROVIDER", "deepseek")
    with pytest.raises(lc.LLMNotConfiguredError, match="DEEPSEEK_API_KEY"):
        lc.detect_provider()


def test_detect_none_configured_raises():
    """零 key -> LLMNotConfiguredError (main 捕获后返回空回调的信号)。"""
    with pytest.raises(lc.LLMNotConfiguredError):
        lc.detect_provider()


def test_detect_custom_requires_base_url(monkeypatch):
    """custom: 只有 key 没有 base_url 视为未配置; 两者齐才探测成功。"""
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "ck")
    with pytest.raises(lc.LLMNotConfiguredError):
        lc.detect_provider()
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "http://localhost:9000/v1")
    assert lc.detect_provider() == "custom"


def test_real_callbacks_without_any_key_raises():
    with pytest.raises(lc.LLMNotConfiguredError):
        lc.real_callbacks()


# ---------------------------------------------------------------------------
# C. _chat 弹性接线
# ---------------------------------------------------------------------------


def _chat_deepseek(monkeypatch, script: list) -> SimpleNamespace:
    """deepseek 就绪 + 假 openai 注入, 返回观测句柄。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    return _install_fake_openai(monkeypatch, script)


def test_chat_retries_transient_error(monkeypatch):
    """第一次 429 限流, 第二次成功 -> 重试生效, 恰好两次出网, key/端点接线正确。"""
    state = _chat_deepseek(monkeypatch, [RuntimeError("429 rate limit"), _resp("ok")])
    out = lc._chat(lc.PROVIDERS["deepseek"], "deepseek-chat", "sys", "user")
    assert out == "ok"
    assert len(state.calls) == 2
    assert state.ctor[0]["api_key"] == "sk-test"
    assert state.ctor[0]["base_url"] == "https://api.deepseek.com"


def test_chat_fatal_error_not_retried(monkeypatch):
    """鉴权致命错: 一次都不重试, 立即上抛。"""
    state = _chat_deepseek(monkeypatch, [RuntimeError("invalid api key")])
    with pytest.raises(RuntimeError, match="invalid api key"):
        lc._chat(lc.PROVIDERS["deepseek"], "deepseek-chat", "sys", "user")
    assert len(state.calls) == 1


def test_chat_retries_exhausted(monkeypatch):
    """一直瞬时失败 -> 重试耗尽 (3 次) 后抛最后一次异常。"""
    state = _chat_deepseek(monkeypatch, [RuntimeError("timeout")] * 3)
    with pytest.raises(RuntimeError, match="timeout"):
        lc._chat(lc.PROVIDERS["deepseek"], "deepseek-chat", "sys", "user")
    assert len(state.calls) == 3


def test_chat_truncated_finish_reason_raises(monkeypatch):
    """finish_reason=length -> TruncatedOutputError 上抛 (上层当生成失败),
    且不烧重试 (截断是确定性错)。"""
    state = _chat_deepseek(monkeypatch, [_resp("x = compute(1,", finish="length")])
    with pytest.raises(TruncatedOutputError, match="finish_reason=length"):
        lc._chat(lc.PROVIDERS["deepseek"], "deepseek-chat", "sys", "user")
    assert len(state.calls) == 1


def test_chat_normal_roundtrip(monkeypatch):
    """正常往返: finish_reason=stop 放行, 请求参 (model/messages/温度/长度) 透传。"""
    state = _chat_deepseek(monkeypatch, [_resp('{"ok": true}', finish="stop")])
    assert lc._chat(
        lc.PROVIDERS["deepseek"], "deepseek-chat", "sys", "用户输入"
    ) == '{"ok": true}'
    kw = state.calls[0]
    assert kw["model"] == "deepseek-chat"
    assert kw["messages"][0] == {"role": "system", "content": "sys"}
    assert kw["messages"][1] == {"role": "user", "content": "用户输入"}
    assert kw["temperature"] == 0.1  # 默认参
    assert kw["max_tokens"] == 4096


def test_chat_without_key_raises():
    """cfg 对应的 env key 缺失 -> LLMNotConfiguredError (不裸发空 key 请求)。"""
    with pytest.raises(lc.LLMNotConfiguredError, match="DEEPSEEK_API_KEY"):
        lc._chat(lc.PROVIDERS["deepseek"], "deepseek-chat", "s", "u")


def test_chat_meters_exact_usage(monkeypatch):
    """响应带 usage -> 精确 token 入账。"""
    _chat_deepseek(monkeypatch, [_resp("ok", in_tok=120, out_tok=45)])
    meter = UsageMeter()
    lc._chat(lc.PROVIDERS["deepseek"], "deepseek-chat", "sys", "user", meter=meter)
    assert meter.snapshot() == {"input_tokens": 120, "output_tokens": 45, "calls": 1}


def test_chat_meter_falls_back_to_estimate(monkeypatch):
    """响应缺 usage (None) -> 字符估算兜底, 不记账为 0。"""
    _chat_deepseek(monkeypatch, [_resp("result = compute(42)", in_tok=None, out_tok=None)])
    meter = UsageMeter()
    lc._chat(lc.PROVIDERS["deepseek"], "deepseek-chat", "sys", "用户输入", meter=meter)
    snap = meter.snapshot()
    assert snap["calls"] == 1
    assert snap["input_tokens"] == estimate_tokens("sys用户输入")
    assert snap["output_tokens"] == estimate_tokens("result = compute(42)")
    assert snap["input_tokens"] > 0


# ---------------------------------------------------------------------------
# D. real_callbacks: 五键 / 档位 / 覆盖 / on_usage
# ---------------------------------------------------------------------------


def test_real_callbacks_five_keys_and_tiers(monkeypatch):
    """五键齐全; 单模型模式 (D3): 全回调同一模型, 温度分两档, max_tokens 由思考挡位决定。"""
    monkeypatch.setenv("ZHIPU_API_KEY", "zk")
    state = _install_fake_openai(monkeypatch, [_resp('{"ok":1}')] * 5)
    cbs = lc.real_callbacks()
    assert set(cbs) == {"clarify", "plan", "diagnose", "generate", "review"}

    for i, name in enumerate(("clarify", "plan", "diagnose")):
        cbs[name]("ping")
        assert state.calls[i]["model"] == "glm-4-plus", name  # 单模型模式
        assert state.calls[i]["temperature"] == 0.0, name
        assert state.calls[i]["max_tokens"] == 8192, name  # off 挡预算
    for i, name in enumerate(("generate", "review"), start=3):
        cbs[name]("ping")
        assert state.calls[i]["model"] == "glm-4-plus", name
        assert state.calls[i]["temperature"] == 0.1, name
        assert state.calls[i]["max_tokens"] == 8192, name

    # 每次调用都带共用 system 提示 + 节点 prompt 作为 user 消息
    assert state.calls[0]["messages"][0]["role"] == "system"
    assert state.calls[0]["messages"][1] == {"role": "user", "content": "ping"}


def test_env_overrides_model_names(monkeypatch):
    """单模型模式: ZHIPU_MODEL 覆盖一切档位; 未设时回退 heavy 默认 (D3)。"""
    monkeypatch.setenv("ZHIPU_API_KEY", "zk")
    monkeypatch.setenv("ZHIPU_MODEL", "glm-single")
    state = _install_fake_openai(monkeypatch, [_resp("ok"), _resp("ok")])
    cbs = lc.real_callbacks()
    cbs["plan"]("p")
    assert state.calls[0]["model"] == "glm-single"
    cbs["generate"]("p")
    assert state.calls[1]["model"] == "glm-single"


def test_custom_provider_uses_env_models_and_url(monkeypatch):
    """custom: CUSTOM_LLM_MODEL 单模型 + base_url 从 CUSTOM_LLM_BASE_URL 读。"""
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "ck")
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("CUSTOM_LLM_MODEL", "llama3-70b")
    state = _install_fake_openai(monkeypatch, [_resp("ok"), _resp("ok")])
    cbs = lc.real_callbacks()
    cbs["clarify"]("p")
    cbs["review"]("p")
    assert state.calls[0]["model"] == "llama3-70b"
    assert state.calls[1]["model"] == "llama3-70b"
    assert state.ctor[0]["base_url"] == "http://localhost:8000/v1"
    assert state.ctor[0]["api_key"] == "ck"


def test_custom_provider_default_model_env(monkeypatch):
    """custom 无 CUSTOM_LLM_MODEL 时从 LIGHT/HEAVY 兼容 env 读默认模型名。"""
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "ck")
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "http://x/v1")
    monkeypatch.setenv("CUSTOM_LLM_HEAVY_MODEL", "m-heavy")
    state = _install_fake_openai(monkeypatch, [_resp("ok"), _resp("ok")])
    cbs = lc.real_callbacks()
    cbs["diagnose"]("p")
    cbs["generate"]("p")
    assert state.calls[0]["model"] == "m-heavy"
    assert state.calls[1]["model"] == "m-heavy"


def test_custom_provider_missing_models_raises(monkeypatch):
    """custom 有 key+base_url 但没配模型名 -> LLMNotConfiguredError (人话指出 env)。"""
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "ck")
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "http://x/v1")
    with pytest.raises(lc.LLMNotConfiguredError, match="模型名"):
        lc.real_callbacks()


def test_on_usage_receives_per_call_delta(monkeypatch):
    """on_usage: 每回调独立收到本次调用的增量用量 (非累计值)。"""
    monkeypatch.setenv("ZHIPU_API_KEY", "zk")
    _install_fake_openai(monkeypatch, [
        _resp("ok", in_tok=10, out_tok=6),
        _resp("ok", in_tok=20, out_tok=8),
    ])
    seen: List[tuple] = []
    cbs = lc.real_callbacks(on_usage=lambda name, u: seen.append((name, u)))
    cbs["clarify"]("p")
    cbs["generate"]("p")
    assert seen[0] == ("clarify", {"input_tokens": 10, "output_tokens": 6, "calls": 1})
    assert seen[1] == ("generate", {"input_tokens": 20, "output_tokens": 8, "calls": 1})


def test_on_usage_none_is_default():
    """on_usage 缺省为 None: 不注入也照常工作 (探测失败先行报错属预期)。"""
    assert lc.real_callbacks.__defaults__[0] is None


# ---------------------------------------------------------------------------
# E. main._make_callbacks 接线 (spec D)
# ---------------------------------------------------------------------------


def test_make_callbacks_empty_without_keys():
    """零 key -> _make_callbacks 返回空 dict (205 基线行为完全不变)。"""
    from src.main import _make_callbacks

    assert _make_callbacks() == {}


def test_make_callbacks_real_with_key(monkeypatch):
    """配了 key -> _make_callbacks 产出五键真实回调。"""
    monkeypatch.setenv("ZHIPU_API_KEY", "zk")
    from src.main import _make_callbacks

    cbs = _make_callbacks()
    assert set(cbs) == {"clarify", "plan", "diagnose", "generate", "review"}


# ---------------------------------------------------------------------------
# F. MiniMax 供应商 (2026-08-24 G2 批次: 小孟选定 MiniMax-M3)
# ---------------------------------------------------------------------------


def test_minimax_provider_registered():
    """minimax 在注册表: OpenAI 兼容国内端点 + 1M 上下文模型。"""
    assert "minimax" in lc.PROVIDERS
    cfg = lc.PROVIDERS["minimax"]
    assert cfg["env_var"] == "MINIMAX_API_KEY"
    assert cfg["base_url"] == "https://api.minimaxi.com/v1"
    assert cfg["package"] == "openai"


def test_minimax_detected_by_key(monkeypatch):
    """只有 MINIMAX_API_KEY 时, 探测选中 minimax。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "mk")
    assert lc.detect_provider() == "minimax"


def test_minimax_explicit_and_priority(monkeypatch):
    """显式指定 minimax 可用; 有 deepseek key 时 minimax 仍可显式抢占。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk")
    monkeypatch.setenv("MINIMAX_API_KEY", "mk")
    assert lc.detect_provider() == "deepseek"  # 探测优先级: deepseek 在前
    monkeypatch.setenv("REMOTE_SENSING_LLM_PROVIDER", "minimax")
    assert lc.detect_provider() == "minimax"  # 显式指定压过探测


def test_strip_reasoning_removes_think_block():
    """推理模型思维链剥离: 闭合 think 段删除, 无标签原样。"""
    assert lc.strip_reasoning("<think>推理过程...</think>纯内容") == "纯内容"
    assert lc.strip_reasoning("前置<think>a</think>中<think>b</think>后") == "前置中后"
    assert lc.strip_reasoning("无标签原样返回") == "无标签原样返回"


def test_chat_strips_reasoning(monkeypatch):
    """_chat 返回前剥掉 <think>: clarify/generate 拿到的是纯内容。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "mk")
    handle = _install_fake_openai(monkeypatch, [_resp("<think>思考</think>{\"ok\": true}")])
    out = lc._chat(lc.PROVIDERS["minimax"], "MiniMax-M3", "sys", "user")
    assert out == '{"ok": true}'
    assert handle.calls[0]["model"] == "MiniMax-M3"


def test_thinking_off_only_for_supporting_providers(monkeypatch):
    """思考开关: minimax 透传 extra_body, 其他供应商不带 (防严格端点报错)。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "mk")
    h = _install_fake_openai(monkeypatch, [_resp("纯正文")])
    lc._chat(lc.PROVIDERS["minimax"], "MiniMax-M3", "s", "u", thinking_enabled=False)
    assert h.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    # 默认开思考: 不带 extra_body
    _install_fake_openai  # noqa
    h2 = _install_fake_openai(monkeypatch, [_resp("纯正文")])
    lc._chat(lc.PROVIDERS["minimax"], "MiniMax-M3", "s", "u")
    assert "extra_body" not in h2.calls[0]
    # deepseek (未声明支持): 关思考也不透传
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk")
    h3 = _install_fake_openai(monkeypatch, [_resp("纯正文")])
    lc._chat(lc.PROVIDERS["deepseek"], "deepseek-chat", "s", "u", thinking_enabled=False)
    assert "extra_body" not in h3.calls[0]


def test_thinking_levels_table(monkeypatch):
    """两挡思考表 (OFF/ON, 小孟同学决议): 每挡预算封顶, 无不限制挡。"""
    assert set(lc.THINKING_LEVELS) == {"OFF", "ON"}
    assert lc.THINKING_LEVELS["OFF"]["enabled"] is False
    assert lc.THINKING_LEVELS["ON"]["enabled"] is True
    assert lc.THINKING_LEVELS["ON"]["max_tokens"] > lc.THINKING_LEVELS["OFF"]["max_tokens"]


def test_thinking_level_env_switch(monkeypatch):
    """REMOTE_SENSING_THINKING 初始挡位: 默认 OFF; 旧三挡值兼容映射为 ON。"""
    from src.agent.llm_client import _get_thinking_level
    monkeypatch.delenv("REMOTE_SENSING_THINKING", raising=False)
    lvl, cfg = _get_thinking_level()
    assert lvl == "OFF" and cfg["enabled"] is False
    monkeypatch.setenv("REMOTE_SENSING_THINKING", "high")   # 旧三挡值 -> ON
    lvl, cfg = _get_thinking_level()
    assert lvl == "ON" and cfg["enabled"] is True
    monkeypatch.setenv("REMOTE_SENSING_THINKING", "garbage")
    lvl, cfg = _get_thinking_level()
    assert lvl == "OFF"


def test_thinking_runtime_switch(monkeypatch):
    """set_thinking_mode 运行时切换 (免重启), 大小写不敏感, 非法值拒绝。"""
    monkeypatch.setattr(lc, "_thinking_mode_override", None)
    monkeypatch.delenv("REMOTE_SENSING_THINKING", raising=False)
    assert lc.get_thinking_mode()["mode"] == "OFF"
    cfg = lc.set_thinking_mode("on")            # 小写 -> ON
    assert cfg["mode"] == "ON" and cfg["enabled"] is True
    assert lc.get_thinking_mode()["mode"] == "ON"
    lc.set_thinking_mode("OFF")
    assert lc.get_thinking_mode()["mode"] == "OFF"
    with pytest.raises(ValueError):
        lc.set_thinking_mode("high")            # 三挡旧值不再接受
