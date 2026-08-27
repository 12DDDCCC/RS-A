"""全局测试夹具。"""

from __future__ import annotations

import os

import pytest

# 模块级 (早于任何测试模块 import src.main): 禁读本机 .env。
# 单测与本机开发配置彻底隔离——开发者的 .env 真 key 绝不泄入测试环境。
os.environ["REMOTE_SENSING_NO_DOTENV"] = "1"


@pytest.fixture(autouse=True)
def _isolate_failure_store(tmp_path, monkeypatch):
    """全量测试隔离失败库: 任何测试触发的 record_failure 都不污染真实
    cache/failures/ (test_codegen 的拒绝路径用例会真实调用埋点)。"""
    from src.codegen import failure_store

    monkeypatch.setattr(failure_store, "_FAILURES_DIR", tmp_path / "failures")
    yield


@pytest.fixture(autouse=True)
def _mock_platform_env(monkeypatch):
    """测试环境强制 PIE Mock 平台 (GEE 实装后生产默认已切 gee)。

    单测不出网调真实 GEE——241 条基线行为与切换前分毫不变;
    GEE 适配器的真链路由手动实测覆盖 (见 obsidian 15 号存档)。
    """
    monkeypatch.setenv("REMOTE_SENSING_PLATFORM", "pie-engine")
    yield


@pytest.fixture(autouse=True)
def _isolate_runtime_stores(tmp_path, monkeypatch):
    """全量测试隔离 jobs/sessions 存储 (G2 发现)。

    此前测试直用真实 cache/jobs.db: 中断遗留的 running/queued 任务会触发
    per-user 单飞 409, 连环误伤后续测试。改为 tmp 隔离 + 四处引用同步替换
    (两个模块级单例 + main 的 from-import 绑定)。
    """
    import src.main as main_mod
    from src.runtime.jobs import JobStore
    from src.runtime.sessions import SessionStore

    jb = JobStore(db_path=tmp_path / "test_jobs.db")
    ss = SessionStore(db_path=tmp_path / "test_sessions.db")
    monkeypatch.setattr("src.runtime.jobs.store", jb)
    monkeypatch.setattr("src.runtime.sessions.sessions", ss)
    monkeypatch.setattr(main_mod, "store", jb)
    monkeypatch.setattr(main_mod, "sessions", ss)
    # 限流历史同为进程级共享状态: 每测试清空, 防前序测试的提交计数误伤 (G3)
    main_mod._submit_history.clear()
    yield
