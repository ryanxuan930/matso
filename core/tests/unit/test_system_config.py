"""系統設定端點（#54）：AI/LLM 可編輯 + 唯讀系統資訊 + 權限。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.models import UserRole


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _admin_header(factory: sessionmaker[Session], client) -> dict[str, str]:  # type: ignore[no-untyped-def]
    seed_user(factory, username="chief", role=UserRole.WHITE_CELL_STAFF)
    return auth_header(login(client, "chief")["access_token"])


def test_non_admin_forbidden(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory, username="joe", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    h = auth_header(login(client, "joe")["access_token"])
    assert client.get("/api/v1/system/config", headers=h).status_code == 403


def test_get_returns_editable_and_readonly(session_factory: sessionmaker[Session]) -> None:
    client = make_client(session_factory)
    h = _admin_header(session_factory, client)
    body = client.get("/api/v1/system/config", headers=h).json()
    assert set(body["ai"]) >= {"ai_mode", "llm_base_url", "llm_model", "ai_modes"}
    assert body["ai"]["ai_modes"] == ["AI_OFF", "AI_BARE", "AI_FULL"]
    assert set(body["readonly"]) >= {"env", "terrain_grpc_target", "ai_loop_wired"}
    # WP-F3：自主迴路自 O11 起就接入活執行期（sim_runtime 起 per-faction worker），
    # F3 再把 RoleManager 與 AIInvocationLog 接上。這個旗標過去一直是過時的 False。
    assert body["readonly"]["ai_loop_wired"] is True


def test_put_persists_ai_and_llm(session_factory: sessionmaker[Session]) -> None:
    client = make_client(session_factory)
    h = _admin_header(session_factory, client)
    r = client.put(
        "/api/v1/system/config",
        json={
            "ai_mode": "AI_FULL",
            "llm_base_url": "http://host.docker.internal:11434/",  # 尾斜線應被去除
            "llm_model": "gemma4:12b-mlx",
            "llm_api_key": "sk-test",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    ai = r.json()["ai"]
    assert ai["ai_mode"] == "AI_FULL"
    assert ai["llm_base_url"] == "http://host.docker.internal:11434"  # 已 rstrip
    assert ai["llm_model"] == "gemma4:12b-mlx"
    assert ai["llm_api_key_set"] is True  # 有存 key（值不外洩）
    # 重新 GET 反映已存值
    got = client.get("/api/v1/system/config", headers=h).json()["ai"]
    assert got["ai_mode"] == "AI_FULL" and got["llm_model"] == "gemma4:12b-mlx"


def test_put_rejects_unknown_ai_mode(session_factory: sessionmaker[Session]) -> None:
    client = make_client(session_factory)
    h = _admin_header(session_factory, client)
    r = client.put("/api/v1/system/config", json={"ai_mode": "AI_TURBO"}, headers=h)
    assert r.status_code >= 400
    assert "AI_TURBO" in r.text or "SYSTEM_INVALID_AI_MODE" in r.text


def test_put_omitting_api_key_keeps_it(session_factory: sessionmaker[Session]) -> None:
    # 先設 key，再送不含 llm_api_key 的更新 → key 應保留（None＝不變）。
    client = make_client(session_factory)
    h = _admin_header(session_factory, client)
    client.put(
        "/api/v1/system/config",
        json={
            "ai_mode": "AI_BARE",
            "llm_base_url": "http://x",
            "llm_model": "m",
            "llm_api_key": "k",
        },
        headers=h,
    )
    r = client.put(
        "/api/v1/system/config",
        json={"ai_mode": "AI_BARE", "llm_base_url": "http://x", "llm_model": "m2"},
        headers=h,
    )
    assert r.json()["ai"]["llm_api_key_set"] is True  # 未送 key → 保留


def test_test_llm_missing_base_url_returns_not_ok(session_factory: sessionmaker[Session]) -> None:
    # 無 base_url → 直接回 ok:false，不觸網。
    client = make_client(session_factory)
    h = _admin_header(session_factory, client)
    r = client.post(
        "/api/v1/system/config/test-llm", json={"base_url": "", "model": "m"}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
