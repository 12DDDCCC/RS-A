"""失败案例库测试: 落盘回读 / taxonomy 映射 / 检索排序 / generator 埋点入库。

隔离铁律: 所有用例 monkeypatch failure_store._FAILURES_DIR 到 tmp_path,
绝不污染真实 cache/failures/。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from cryptography.fernet import Fernet

from src.codegen import failure_store as fs
from src.codegen.failure_store import (
    classify,
    load_failures,
    make_entry,
    record_failure,
    top_failures,
)
from src.codegen.generator import generate_and_validate

REGION = {"lon_min": 116.0, "lat_min": 39.0, "lon_max": 117.0, "lat_max": 40.0}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """失败库目录隔离到 tmp_path (每个测试独立)。"""
    monkeypatch.setattr(fs, "_FAILURES_DIR", tmp_path / "failures")


# ---------- 落盘与回读 ----------

def test_record_and_load_roundtrip():
    """record 落盘 jsonl 可读回, 字段完整一致。"""
    entry = make_entry(
        "北京植被分析", "img = load('FAKE/ID')", "validator", "数据集 ID 不在白名单: FAKE/ID"
    )
    record_failure(entry)

    entries = load_failures()
    assert len(entries) == 1
    got = entries[0]
    assert got == entry  # dataclass 全字段相等
    assert got.taxonomy == "BAD_DATASET"

    # 落盘文件名是 YYYY-MM.jsonl, 行是合法 JSON 且字段齐全
    files = list(fs._FAILURES_DIR.glob("*.jsonl"))
    assert len(files) == 1
    assert files[0].name == f"{datetime.now():%Y-%m}.jsonl"
    row = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert set(row) == {"ts", "task", "code", "reject_layer", "reason", "taxonomy"}


def test_record_is_append_and_load_empty_dir():
    """多次 record 追加不覆盖; 空目录 load 返回空。"""
    assert load_failures() == []
    for i in range(3):
        record_failure(make_entry(f"任务{i}", f"code{i}", "reviewer", "方向反了"))
    assert len(load_failures()) == 3


# ---------- taxonomy 规则映射 ----------

@pytest.mark.parametrize(
    "layer,reason,expected",
    [
        ("validator", "数据集 ID 不在白名单: FAKE/X -> 可能是幻觉", "BAD_DATASET"),
        ("validator", "波段名不在白名单: B99", "BAD_BAND"),
        ("validator", "语法错误 (line 2): invalid syntax", "SYNTAX_ERROR"),
        ("validator", "其他白名单不通过的问题", "BAD_FORMULA"),
        ("reviewer", "疑似 NDVI 反向: Red 在 NIR 之前", "REVIEW_REJECTED"),
        ("sandbox", "[沙箱] 试跑超时, 拒绝全量执行", "SANDBOX_REJECTED"),
    ],
)
def test_taxonomy_mapping(layer, reason, expected):
    assert classify(layer, reason) == expected


# ---------- top_failures 检索 ----------

def test_top_failures_ranking():
    """字符重叠检索: 重叠多的排前, k 截断生效。"""
    record_failure(make_entry("北京植被覆盖监测", "c1", "validator", "数据集 ID 不在白名单"))
    record_failure(make_entry("上海水体提取", "c2", "reviewer", "疑似公式反向"))

    # "北京植被水体监测" 与北京条目重叠 6 字, 与上海条目重叠 2 字 -> 排序正确
    top = top_failures("北京植被水体监测", k=2)
    assert [e.task for e in top] == ["北京植被覆盖监测", "上海水体提取"]

    # k=1 截断
    assert top_failures("北京植被水体监测", k=1)[0].task == "北京植被覆盖监测"


def test_top_failures_no_overlap_returns_empty():
    """零重叠视为不相关, 不返回。"""
    record_failure(make_entry("北京植被覆盖监测", "c1", "validator", "数据集 ID 不在白名单"))
    # 与记录无公共字符的任务
    assert top_failures("ZZZQQQ", k=2) == []


# ---------- generator 埋点: 三处拒绝点确实入库 ----------

def test_generator_validator_rejection_recorded():
    """validator 拒绝 (幻觉数据集) -> 入库, layer/taxonomy 正确。"""

    def fake_gen(prompt):
        return "img = load('FAKE/ID/HERE')\nresult = img"

    result = generate_and_validate("植被分析", REGION, "u1", fake_gen, max_attempts=1)
    assert not result.ready

    entries = load_failures()
    assert len(entries) == 1
    assert entries[0].reject_layer == "validator"
    assert entries[0].taxonomy == "BAD_DATASET"
    assert "FAKE/ID/HERE" in entries[0].code
    assert "不在白名单" in entries[0].reason


def test_generator_reviewer_rejection_recorded():
    """reviewer 拒绝 (反向 NDVI, 裸波段可过 validator) -> 入库。"""

    def fake_gen(prompt):
        return (
            "ndvi = (B4 - B8) / (B4 + B8)\n"
            "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
            "result = img"
        )

    result = generate_and_validate("植被分析", REGION, "u1", fake_gen, max_attempts=1)
    assert not result.ready

    entries = load_failures()
    assert len(entries) == 1
    assert entries[0].reject_layer == "reviewer"
    assert entries[0].taxonomy == "REVIEW_REJECTED"


def test_generator_sandbox_rejection_recorded(monkeypatch):
    """sandbox 拒绝 (结果指标异常) -> 入库 (需 mock 平台 + 预存凭证)。"""
    from src.codegen import sandbox as sb_mod
    from src.io import credentials as cred_mod
    from src.platform.base import ExecutionResult

    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    cred_mod.store_credentials("u_sb", {"pie_token": "t"})

    class AnomalousPlatform:
        name = "fake"

        def execute(self, code, credentials, region, **kwargs):
            # NDVI 均值全负 -> 沙箱判异常拒全量
            return ExecutionResult(success=True, metrics={"ndvi_mean": -0.9, "valid_ratio": 0.9})

    monkeypatch.setattr(sb_mod, "get_platform", lambda name: AnomalousPlatform())

    def fake_gen(prompt):
        return (
            "ndvi = (B8 - B4) / (B8 + B4)\n"
            "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
            "result = img"
        )

    result = generate_and_validate("植被分析", REGION, "u_sb", fake_gen, max_attempts=1)
    assert not result.ready

    entries = load_failures()
    assert len(entries) == 1
    assert entries[0].reject_layer == "sandbox"
    assert entries[0].taxonomy == "SANDBOX_REJECTED"
