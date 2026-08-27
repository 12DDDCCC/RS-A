"""FastAPI 端点测试 (P0-4 异步任务制 + T-1 鉴权)。

验证: 首次绑定签发令牌; Bearer 鉴权与任务归属; 202 入队 -> 轮询 -> JPEG;
澄清挂起 -> answer 续跑; 地名解析接线; 未接 LLM 友好失败。
TestClient 下 BackgroundTasks 在响应后同步完成, 无需真异步等待。
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.io import credentials as cred_mod
from src.io import auth as auth_mod


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    # 清理测试用户的历史绑定 (凭证 + 令牌), 保证每个测试首次绑定语义
    for uid in ("u1", "u2"):
        cred_mod.delete_credentials(uid)
        auth_mod.delete_access_token(uid)
    # 延迟导入, 让环境变量先生效
    from src.main import app

    return TestClient(app)


@pytest.fixture
def with_creds(client):
    """u1 首次绑定 -> 返回 (client, headers)。"""
    r = client.post("/credentials", json={"user_id": "u1", "credentials": {"pie_token": "t"}})
    assert r.status_code == 200
    token = r.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_credentials_status_and_replace(client, monkeypatch):
    """状态查询(只回email) + 换绑(须Bearer, 不轮换令牌)。"""
    from cryptography.fernet import Fernet as _F

    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", _F.generate_key().decode())
    r = client.post("/credentials", json={"user_id": "u9",
                                          "credentials": {"service_account_email": "a@b.iam",
                                                          "key_json": "{}"}})
    assert r.status_code == 200
    tok = r.json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    s = client.get("/credentials/status?user_id=u9", headers=H).json()
    assert s == {"bound": True, "email": "a@b.iam"}  # 只回 email, 不回 key

    # 无令牌换绑 -> 401 (防抢注覆盖)
    r2 = client.post("/credentials/replace", json={"user_id": "u9",
                                                   "credentials": {"service_account_email": "c@d.iam",
                                                                   "key_json": "{}"}})
    assert r2.status_code == 401

    # 带令牌换绑成功; 旧令牌仍有效 (不轮换, dsh 侧零中断)
    r3 = client.post("/credentials/replace", headers=H,
                     json={"user_id": "u9",
                           "credentials": {"service_account_email": "c@d.iam",
                                           "key_json": "{}"}})
    assert r3.status_code == 200 and r3.json()["replaced"] is True
    s2 = client.get("/credentials/status?user_id=u9", headers=H).json()
    assert s2["email"] == "c@d.iam"
    assert client.get("/credentials/status?user_id=u9", headers=H).status_code == 200

    from src.io import credentials as _c, auth as _a

    _c.delete_credentials("u9")
    _a.delete_access_token("u9")


def test_local_user_ensure(client, monkeypatch):
    """/users/local: 新用户建号首签令牌; 已存在须 Bearer (防抢注)。"""
    from cryptography.fernet import Fernet as _F

    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", _F.generate_key().decode())
    from src.io import credentials as _c0, auth as _a0

    _c0.delete_credentials("nu1")
    _a0.delete_access_token("nu1")  # 清历史运行残留 (enc+tok 任一残留即 401)
    creds = {"service_account_email": "n@x.iam", "key_json": "{}"}

    # 新用户 + 凭证 -> created=True
    r = client.post("/users/local", json={"user_id": "nu1", "credentials": creds})
    assert r.status_code == 200 and r.json()["created"] is True
    tok = r.json()["access_token"]

    # 已存在无 Bearer -> 401
    r2 = client.post("/users/local", json={"user_id": "nu1", "credentials": creds})
    assert r2.status_code == 401

    # 带 Bearer -> 取回令牌 (created=False)
    H = {"Authorization": f"Bearer {tok}"}
    r3 = client.post("/users/local", headers=H,
                     json={"user_id": "nu1", "credentials": creds})
    assert r3.status_code == 200 and r3.json()["created"] is False

    # 令牌真实可用
    st = client.get("/credentials/status?user_id=nu1", headers=H)
    assert st.json()["bound"] is True

    from src.io import credentials as _c, auth as _a

    _c.delete_credentials("nu1")
    _a.delete_access_token("nu1")


def _mock_callbacks(task_id=None) -> dict:
    return {
        "clarify": lambda p: '{"task_type":"vegetation","clarified":"北京植被变化","need_clarify":false,"clarify_question":"","suggested_methods":[]}',
        "generate": lambda p: "ndvi = (B8 - B4) / (B8 + B4)\nimg = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img",
        "review": lambda p: "APPROVED\nok",
        "diagnose": lambda p: '{"diagnosis":"ok","reason":"正常","should_retry":false,"retry_hint":""}',
    }


# ---------- 鉴权 (T-1) ----------

def test_health_no_auth_needed(client):
    assert client.get("/health").status_code == 200


def test_credentials_first_bind_issues_token(client):
    """首次绑定 -> 返回一次性访问令牌。"""
    r = client.post("/credentials", json={"user_id": "u1", "credentials": {"pie_token": "t"}})
    assert r.status_code == 200
    assert r.json()["stored"] is True
    assert r.json()["access_token"]  # 令牌仅此一次返回


def test_credentials_rebind_without_token_rejected(with_creds):
    """已绑定 -> 无令牌不可覆盖 (防他人用 user_id 抢绑/覆盖凭证)。"""
    client, _ = with_creds
    r = client.post("/credentials", json={"user_id": "u1", "credentials": {"pie_token": "evil"}})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ALREADY_BOUND"
    # 凭证未被覆盖
    assert cred_mod.load_credentials("u1")["pie_token"] == "t"


def test_analyze_requires_token(client, with_creds):
    """同 user_id 但令牌缺失/错误 -> 401 (不再裸奔)。"""
    client, headers = with_creds
    body = {"user_input": "北京植被", "user_id": "u1", "place": "北京"}
    assert client.post("/analyze", json=body).status_code == 401
    bad = {"Authorization": "Bearer wrong_token"}
    assert client.post("/analyze", json=body, headers=bad).status_code == 401


def test_task_access_requires_owner_token(client, with_creds, monkeypatch):
    """任务归属校验: 他人令牌查别人的任务 -> 401。"""
    import src.main as main_mod

    client, headers = with_creds
    monkeypatch.setattr(main_mod, "_make_callbacks", _mock_callbacks)
    r = client.post("/analyze", json={
        "user_input": "北京植被", "user_id": "u1", "place": "北京",
    }, headers=headers)
    task_id = r.json()["task_id"]

    # 造一个"别人"的令牌: u2 绑定
    r2 = client.post("/credentials", json={"user_id": "u2", "credentials": {"pie_token": "x"}})
    u2_headers = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    assert client.get(f"/tasks/{task_id}", headers=u2_headers).status_code == 401
    # 主人自己的令牌能查
    assert client.get(f"/tasks/{task_id}", headers=headers).status_code == 200


# ---------- 业务流 ----------

def test_analyze_missing_credentials_after_unbind(client, with_creds, monkeypatch):
    """令牌有效但凭证文件被删 (如管理员清理) -> 400 NO_CREDS (人话)。"""
    client, headers = with_creds
    cred_mod.delete_credentials("u1")  # 保留令牌, 只删凭证
    r = client.post("/analyze", json={
        "user_input": "植被", "user_id": "u1", "place": "北京",
    }, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "NO_CREDS"


def test_analyze_missing_region(client, with_creds):
    """无 region/place 且 user_input 里也解析不出地名 -> 400 NO_REGION。"""
    client, headers = with_creds
    r = client.post("/analyze", json={"user_input": "植被变化", "user_id": "u1"}, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "NO_REGION"


def test_analyze_unknown_place(client, with_creds):
    """地名表外的地方 -> 400, 不猜坐标。"""
    client, headers = with_creds
    r = client.post("/analyze", json={
        "user_input": "植被", "user_id": "u1", "place": "霍格沃茨",
    }, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "NO_REGION"


def test_analyze_end_to_end_returns_jpeg(client, with_creds, monkeypatch):
    """202 入队 -> done -> JPEG (端到端, 产物必须过魔数)。"""
    import src.main as main_mod

    client, headers = with_creds
    monkeypatch.setattr(main_mod, "_make_callbacks", _mock_callbacks)

    r = client.post("/analyze", json={
        "user_input": "北京近十年植被覆盖变化", "user_id": "u1", "place": "北京",
    }, headers=headers)
    assert r.status_code == 202
    task_id = r.json()["task_id"]

    # BackgroundTasks 在 TestClient 下响应后同步完成
    s = client.get(f"/tasks/{task_id}", headers=headers)
    assert s.status_code == 200
    assert s.json()["status"] == "done"
    assert s.json()["caption"].splitlines()[0] == "分析区域：北京"  # place 已接线

    img = client.get(f"/tasks/{task_id}/result", headers=headers)
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/jpeg"
    # 端到端产物必须是合法 JPEG (魔数), 不是占位字节
    assert img.content.startswith(b"\xff\xd8\xff")


def test_analyze_place_in_text(client, with_creds, monkeypatch):
    """不给 place, 城市名写在 user_input 里也能解析 (普通人自然表达)。"""
    import src.main as main_mod

    client, headers = with_creds
    monkeypatch.setattr(main_mod, "_make_callbacks", _mock_callbacks)

    r = client.post("/analyze", json={
        "user_input": "北京近十年植被覆盖变化", "user_id": "u1",
    }, headers=headers)
    assert r.status_code == 202
    task_id = r.json()["task_id"]
    s = client.get(f"/tasks/{task_id}", headers=headers)
    assert s.json()["status"] == "done"
    assert s.json()["caption"].splitlines()[0] == "分析区域：北京"


def test_analyze_without_llm_friendly_failure(client, with_creds, monkeypatch):
    """生产未接 LLM -> 任务 failed, 错误是人话+码 (不裸 500)。"""
    import src.main as main_mod

    client, headers = with_creds
    monkeypatch.setattr(main_mod, "_make_callbacks", lambda task_id=None: {})

    r = client.post("/analyze", json={
        "user_input": "北京植被变化", "user_id": "u1", "place": "北京",
    }, headers=headers)
    assert r.status_code == 202
    task_id = r.json()["task_id"]

    s = client.get(f"/tasks/{task_id}", headers=headers)
    assert s.json()["status"] == "failed"
    err = s.json()["error"]
    assert err["code"] == "LLM_NOT_CONFIGURED"
    assert "白名单" not in err["message"]  # 人话无术语
    assert err["suggestion"]


def test_clarify_then_answer_flow(client, with_creds, monkeypatch):
    """澄清挂起 -> POST /answer 续跑 -> done (决策5 落地)。"""
    import src.main as main_mod

    client, headers = with_creds
    cb = _mock_callbacks()
    cb["clarify"] = lambda p: '{"task_type":"vegetation","clarified":"","need_clarify":true,"clarify_question":"要看哪个时间段?"}'

    # 第一次: 反问挂起
    monkeypatch.setattr(main_mod, "_make_callbacks", lambda task_id=None: cb)
    r = client.post("/analyze", json={
        "user_input": "看看变化", "user_id": "u1", "place": "北京",
    }, headers=headers)
    task_id = r.json()["task_id"]
    s = client.get(f"/tasks/{task_id}", headers=headers)
    assert s.json()["status"] == "need_clarify"
    assert "时间段" in s.json()["question"]

    # 回答后续跑 (不再反问)
    monkeypatch.setattr(main_mod, "_make_callbacks", _mock_callbacks)
    a = client.post(f"/tasks/{task_id}/answer", json={"answer": "近三年的植被变化"}, headers=headers)
    assert a.status_code == 202
    assert client.get(f"/tasks/{task_id}", headers=headers).json()["status"] == "done"


def test_answer_wrong_status_rejected(client, with_creds, monkeypatch):
    """非 need_clarify 状态 answer -> 409。"""
    import src.main as main_mod

    client, headers = with_creds
    monkeypatch.setattr(main_mod, "_make_callbacks", _mock_callbacks)
    r = client.post("/analyze", json={
        "user_input": "北京植被", "user_id": "u1", "place": "北京",
    }, headers=headers)
    task_id = r.json()["task_id"]
    a = client.post(f"/tasks/{task_id}/answer", json={"answer": "x"}, headers=headers)
    assert a.status_code == 409


def test_task_not_found(client):
    assert client.get("/tasks/nonexistent").status_code == 404
