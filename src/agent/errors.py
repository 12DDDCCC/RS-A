"""错误解释分层: 错误码 + 人话 + 技术细节三分离。

设计蓝图 P0-3: 普通人收到的错误禁止出现英文异常名/Collection ID/
"白名单""沙箱"等术语; tech_detail 留给开发者调试。

用法: node_output / main.py 出口调 classify_error(raw) 得 UserError,
用户面用 user_message + suggestion, 开发者面用 tech_detail。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class UserError:
    """一次失败的三个面向: 稳定码(机器)/人话(用户)/技术细节(开发者)。"""

    code: str
    user_message: str
    tech_detail: str
    suggestion: str = ""


# 错误码 -> (人话模板, 建议动作)。人话禁术语是铁律。
_MESSAGES: dict[str, tuple[str, str]] = {
    "NO_CREDS": (
        "还没有绑定卫星数据平台的账号",
        "请先绑定你的 PIE-Engine 账号后再开始分析",
    ),
    "NO_REGION": (
        "还不知道要分析哪个地方",
        "请告诉我一个城市名, 比如: 上海",
    ),
    "LLM_NOT_CONFIGURED": (
        "服务端还未接入 AI 模型, 暂时无法编写分析代码",
        "请联系管理员完成模型配置",
    ),
    "CODEGEN_FAILED": (
        "自动编写的分析代码没有通过安全检查, 重试几次也没成功",
        "建议换种说法再试一次, 比如加上具体城市名和年份",
    ),
    "PLAN_FAILED": (
        "分析方案没有通过安全检查",
        "建议换个时间范围 (如 2020 年至今) 或更具体的描述再试",
    ),
    "CLOUD_FAILED": (
        "卫星数据平台的计算没有成功",
        "请稍后再试; 若反复失败, 建议检查绑定的平台账号是否可用",
    ),
    "SANDBOX_TIMEOUT": (
        "试算耗时过长, 已自动停止",
        "建议缩小分析范围(比如只分析一个城市)或缩短时间范围后重试",
    ),
    "GEE_NETWORK": (
        "连不上卫星数据平台 (Google Earth Engine)",
        "请检查网络后重试: 国内网络需要先开启代理/VPN 才能访问 Google 服务",
    ),
    "SANDBOX_REJECTED": (
        "试算结果明显异常, 已停止正式计算",
        "建议换个时间范围重试(云太多时结果会异常)",
    ),
    "RETRY_EXHAUSTED": (
        "自动重试后结果仍不理想",
        "可以换个说法再问一次, 或换一个时间段(如避开雨季)试试",
    ),
    "INTENT_UNPARSE": (
        "没太听懂这句话",
        "可以换个说法吗? 比如: 看看上海 2020 到 2023 年的植被变化",
    ),
    "INTERNAL": (
        "处理过程中出现了意外问题",
        "请稍后再试; 若反复出现请保留下方技术信息反馈",
    ),
}

# 技术错误文本 -> 错误码 的匹配规则 (按顺序取首个命中)。
# NO_CREDS 在沙箱/云失败之前: "沙箱拒绝: 凭证未配置"的根因是凭证, 不是沙箱。
_RULES: list[tuple[str, re.Pattern]] = [
    ("NO_REGION", re.compile(r"缺少区域信息|未提供.*区域|无法确定.*区域")),
    ("LLM_NOT_CONFIGURED", re.compile(r"未配置 LLM|llm_callbacks")),
    ("NO_CREDS", re.compile(r"凭证无效或未配置|无已存储凭证|凭证未配置|凭证服务不可用")),
    # 网络故障优先于沙箱规则: 网络错的文本带 [沙箱] 前缀, 但根因是网络不是代码
    ("GEE_NETWORK", re.compile(r"GEE_NETWORK")),
    ("SANDBOX_TIMEOUT", re.compile(r"试跑超时")),
    ("SANDBOX_REJECTED", re.compile(r"\[沙箱\]")),
    ("CODEGEN_FAILED", re.compile(r"代码生成未通过三层防护")),
    ("PLAN_FAILED", re.compile(r"分析计划未通过校验")),
    ("CLOUD_FAILED", re.compile(r"云端执行失败|云端平台")),
    ("INTENT_UNPARSE", re.compile(r"意图解析失败|没太听懂")),
]


def classify_error(raw: str, *, retries_exhausted: bool = False) -> UserError:
    """把内部错误文本转成面向用户的 UserError。

    Args:
        raw: 内部错误文本 (node/generator/platform 抛出的原文)。
        retries_exhausted: 诊断重试已达上限 (此时语义是"结果不理想"而非"跑挂")。
    """
    raw = raw or ""
    code = "INTERNAL"
    for candidate, pattern in _RULES:
        if pattern.search(raw):
            code = candidate
            break
    if retries_exhausted and code == "INTERNAL":
        code = "RETRY_EXHAUSTED"

    user_message, suggestion = _MESSAGES[code]
    return UserError(
        code=code,
        user_message=user_message,
        tech_detail=raw,
        suggestion=suggestion,
    )
