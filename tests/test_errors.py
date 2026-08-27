"""errors.py 错误分层测试: 码稳定 / 人话无术语 / 技术细节保留。"""
from __future__ import annotations

from src.agent.errors import classify_error


def test_no_region_code_and_plain_language():
    ue = classify_error("缺少区域信息, 无法生成代码")
    assert ue.code == "NO_REGION"
    assert "城市" in ue.suggestion  # 给了可操作的下一步
    assert "白名单" not in ue.user_message
    assert ue.tech_detail == "缺少区域信息, 无法生成代码"  # 原文留给开发者


def test_llm_not_configured():
    ue = classify_error("未配置 LLM 回调 (llm_callbacks['generate']), 无法生成代码。")
    assert ue.code == "LLM_NOT_CONFIGURED"
    assert "AI 模型" in ue.user_message


def test_codegen_failed_hides_jargon():
    raw = "代码生成未通过三层防护 (尝试 3 次): 数据集 ID 不在白名单: FAKE/ID"
    ue = classify_error(raw)
    assert ue.code == "CODEGEN_FAILED"
    assert "白名单" not in ue.user_message
    assert "白名单" in ue.tech_detail  # 术语只留在开发者面


def test_sandbox_timeout():
    ue = classify_error("[沙箱] 试跑超时 (60s), 拒绝全量执行")
    assert ue.code == "SANDBOX_TIMEOUT"


def test_cloud_failed():
    ue = classify_error("云端执行失败: PIE 凭证无效")
    assert ue.code == "CLOUD_FAILED"


def test_retry_exhausted_when_diagnosis_bad():
    ue = classify_error("结果不理想", retries_exhausted=True)
    assert ue.code == "RETRY_EXHAUSTED"


def test_unknown_falls_to_internal():
    ue = classify_error("某个没见过的错误")
    assert ue.code == "INTERNAL"
    assert ue.suggestion  # 内部错误也给出建议动作
