"""Lobby REST 端點（O4.1）：GET/POST /sessions，認證 + 角色過濾。"""

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


def test_list_requires_auth(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    assert client.get("/api/v1/sessions").status_code == 401


def test_list_empty_for_new_user(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    tokens = login(client)
    r = client.get("/api/v1/sessions", headers=auth_header(tokens["access_token"]))
    assert r.status_code == 200
    assert r.json() == []


def test_create_then_appears_in_list(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    tokens = login(client)
    h = auth_header(tokens["access_token"])
    created = client.post("/api/v1/sessions", json={"name": "演習一號"}, headers=h)
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "演習一號"
    assert body["status"] == "ACTIVE"
    assert body["my_faction"] == "WHITE_CELL"  # 建立者為統裁
    # 出現在列表
    listing = client.get("/api/v1/sessions", headers=h).json()
    assert [s["id"] for s in listing] == [body["id"]]


def test_create_requires_auth(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    assert client.post("/api/v1/sessions", json={"name": "x"}).status_code == 401


def test_non_participant_commander_sees_only_own(session_factory: sessionmaker[Session]) -> None:
    # 指揮官甲建局 → 指揮官乙（非參與者）列表看不到
    seed_user(session_factory, username="alice", role=UserRole.COMMANDER)
    seed_user(session_factory, username="bob", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    a = auth_header(login(client, "alice")["access_token"])
    client.post("/api/v1/sessions", json={"name": "alice 的局"}, headers=a)
    b = auth_header(login(client, "bob")["access_token"])
    assert client.get("/api/v1/sessions", headers=b).json() == []


def test_director_sees_all_sessions(session_factory: sessionmaker[Session]) -> None:
    # 統裁看得到別人建的局（即使非參與者）
    seed_user(session_factory, username="alice", role=UserRole.COMMANDER)
    seed_user(session_factory, username="chief", role=UserRole.EXERCISE_DIRECTOR)
    client = make_client(session_factory)
    a = auth_header(login(client, "alice")["access_token"])
    made = client.post("/api/v1/sessions", json={"name": "alice 的局"}, headers=a).json()
    chief = auth_header(login(client, "chief")["access_token"])
    listing = client.get("/api/v1/sessions", headers=chief).json()
    ids = [s["id"] for s in listing]
    assert made["id"] in ids
    assert listing[0]["my_faction"] is None  # chief 非參與者


def test_edit_session_name_and_world_time(session_factory: sessionmaker[Session]) -> None:
    """建立者（本 session 的統裁參與者）可編輯名稱 + 想定世界初始時間（#16）。"""
    seed_user(session_factory)
    client = make_client(session_factory)
    h = auth_header(login(client)["access_token"])
    sid = client.post("/api/v1/sessions", json={"name": "原名"}, headers=h).json()["id"]
    r = client.patch(
        f"/api/v1/sessions/{sid}",
        json={"name": "新名", "world_start_time": "2030-06-01T06:00"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "新名"
    assert body["world_start_time"] and body["world_start_time"].startswith("2030-06-01T06:00")
    # 清除 world_start_time（空字串）
    r2 = client.patch(f"/api/v1/sessions/{sid}", json={"world_start_time": ""}, headers=h)
    assert r2.status_code == 200 and r2.json()["world_start_time"] is None


def test_archive_unarchive_and_delete(session_factory: sessionmaker[Session]) -> None:
    """#31：建立者可封存/還原/刪除本局（統裁參與者權限）。"""
    seed_user(session_factory)
    client = make_client(session_factory)
    h = auth_header(login(client)["access_token"])
    sid = client.post("/api/v1/sessions", json={"name": "待封存"}, headers=h).json()["id"]

    # 封存 → status ARCHIVED、archived_at 有值。
    r = client.post(f"/api/v1/sessions/{sid}/archive", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ARCHIVED" and r.json()["archived_at"]

    # 還原 → 回 ACTIVE。
    r = client.post(f"/api/v1/sessions/{sid}/unarchive", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
    assert r.json()["archived_at"] is None

    # 刪除 → 204，列表不再出現。
    assert client.delete(f"/api/v1/sessions/{sid}", headers=h).status_code == 204
    assert all(s["id"] != sid for s in client.get("/api/v1/sessions", headers=h).json())


def test_delete_session_with_children(session_factory: sessionmaker[Session]) -> None:
    """#31：刪除有子表（單位/裝備/事件）的推演應成功（清子表，不因 FK 違反 500）。"""
    from app.models import EquipmentInstance, EquipmentTemplate, TacticalUnit, UnitLevel

    seed_user(session_factory)
    client = make_client(session_factory)
    h = auth_header(login(client)["access_token"])
    sid = client.post("/api/v1/sessions", json={"name": "有子表"}, headers=h).json()["id"]
    # 加單位（真 FK，無 cascade——舊實作刪 session 會 500）+ 裝備（owner cascade）。
    with session_factory() as db:
        u = TacticalUnit(
            session_id=sid, designation="U1", unit_level=UnitLevel.PLATOON, faction="BLUE"
        )
        db.add(u)
        db.flush()
        tmpl = EquipmentTemplate(name="R", category="KINETIC", base_stats={})
        db.add(tmpl)
        db.flush()
        db.add(EquipmentInstance(template_id=tmpl.id, owner_id=u.id, current_state={}))
        db.commit()
    # 刪除應 204（先清子表再刪 session）。
    assert client.delete(f"/api/v1/sessions/{sid}", headers=h).status_code == 204
    assert all(s["id"] != sid for s in client.get("/api/v1/sessions", headers=h).json())
    # 子表已清（單位、事件不再存在）。
    with session_factory() as db:
        from sqlalchemy import select as _select

        assert not db.execute(_select(TacticalUnit).where(TacticalUnit.session_id == sid)).first()


def test_non_director_cannot_delete(session_factory: sessionmaker[Session]) -> None:
    """非本局統裁/管理者不可封存或刪除（#31）。"""
    seed_user(session_factory, username="alice", role=UserRole.COMMANDER)
    seed_user(session_factory, username="mallory", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    a = auth_header(login(client, "alice")["access_token"])
    sid = client.post("/api/v1/sessions", json={"name": "alice 的局"}, headers=a).json()["id"]
    m = auth_header(login(client, "mallory")["access_token"])
    assert client.post(f"/api/v1/sessions/{sid}/archive", headers=m).status_code == 403
    assert client.delete(f"/api/v1/sessions/{sid}", headers=m).status_code == 403
