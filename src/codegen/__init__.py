"""codegen 包: 代码生成 + 三层防护 (项目心脏)。"""
from __future__ import annotations

from src.codegen.generator import GenerationResult, generate_and_validate
from src.codegen.reviewer import ReviewReport, review_code
from src.codegen.sandbox import SandboxConfig, sandbox_trial
from src.codegen.validator import ValidationReport, validate_code

__all__ = [
    "generate_and_validate",
    "GenerationResult",
    "validate_code",
    "ValidationReport",
    "review_code",
    "ReviewReport",
    "sandbox_trial",
    "SandboxConfig",
]
