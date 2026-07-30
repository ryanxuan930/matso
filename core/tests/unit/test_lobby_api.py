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


def test_delete_retries_when_the_running_sim_holds_the_rows(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """刪一局**進行中**的推演會和它自己的 runner 搶列——撞到就重試，不要吐 500。

    偵測 sweep 每 tick 都在改寫 `IntelContact`，MariaDB 於是丟
    1020「Record has changed since last read」。使用者看到的症狀是
    「刪除失敗、再按一次就成功」——那是最糟的一種錯誤：看起來像隨機故障，
    實際上有明確成因（活體全系統檢查在清理測試局時真的踩到）。
    """
    from sqlalchemy.exc import OperationalError

    from app.lobby import service as svc

    monkeypatch.setattr(svc, "_DELETE_BACKOFF_S", 0.0)  # 測試不要真的睡
    seed_user(session_factory)
    client = make_client(session_factory)
    h = auth_header(login(client)["access_token"])
    sid = client.post("/api/v1/sessions", json={"name": "進行中"}, headers=h).json()["id"]

    calls = {"n": 0}
    real = svc.purge_session_rows

    def flaky(db: Session, session_id: str):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("purge", {}, Exception("1020 Record has changed"))
        return real(db, session_id)

    monkeypatch.setattr(svc, "purge_session_rows", flaky)
    assert client.delete(f"/api/v1/sessions/{sid}", headers=h).status_code == 204
    assert calls["n"] == 2, "第一次撞鎖後應重試一次就成功"


def test_delete_gives_up_after_the_retry_budget(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一直撞就要真的失敗——無限重試會讓一個壞掉的刪除永遠掛在那裡轉圈。"""
    from sqlalchemy.exc import OperationalError

    from app.lobby import service as svc

    monkeypatch.setattr(svc, "_DELETE_BACKOFF_S", 0.0)
    seed_user(session_factory)
    client = make_client(session_factory)
    h = auth_header(login(client)["access_token"])
    sid = client.post("/api/v1/sessions", json={"name": "永遠鎖著"}, headers=h).json()["id"]

    calls = {"n": 0}

    def always_locked(db: Session, session_id: str):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise OperationalError("purge", {}, Exception("1020 Record has changed"))

    monkeypatch.setattr(svc, "purge_session_rows", always_locked)
    with pytest.raises(OperationalError):
        client.delete(f"/api/v1/sessions/{sid}", headers=h)
    assert calls["n"] == svc._DELETE_ATTEMPTS


def test_clone_session_copies_units_equipment_and_new_seed(
    session_factory: sessionmaker[Session],
) -> None:
    """#79：複製一局 → 新局帶單位/裝備/部署 verbatim，且 master_seed 互異（新 RNG）。"""
    from sqlalchemy import select as _select

    from app.models import (
        EquipmentInstance,
        EquipmentTemplate,
        TacticalUnit,
        UnitLevel,
        WargameSession,
    )

    seed_user(session_factory)
    client = make_client(session_factory)
    h = auth_header(login(client)["access_token"])
    sid = client.post("/api/v1/sessions", json={"name": "母局"}, headers=h).json()["id"]
    with session_factory() as db:
        hq = TacticalUnit(
            session_id=sid,
            designation="HQ",
            unit_level=UnitLevel.COMPANY,
            faction="BLUE",
            is_fixed=True,
            current_lat=24.1,
            current_lng=120.8,
            current_strength=63.0,  # 已交戰減損：verbatim 應原樣帶過
        )
        db.add(hq)
        db.flush()
        sub = TacticalUnit(
            session_id=sid,
            designation="P1",
            unit_level=UnitLevel.PLATOON,
            faction="BLUE",
            parent_id=hq.id,
        )
        db.add(sub)
        db.flush()
        tmpl = EquipmentTemplate(name="R", category="KINETIC", base_stats={})
        db.add(tmpl)
        db.flush()
        db.add(
            EquipmentInstance(
                template_id=tmpl.id, owner_id=sub.id, current_state={"ammo": 40}, quantity=7
            )
        )
        db.commit()
        src_seed = db.get(WargameSession, sid).master_seed  # type: ignore[union-attr]

    r = client.post(f"/api/v1/sessions/{sid}/clone", json={"name": "第二輪"}, headers=h)
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    assert new_id != sid and r.json()["name"] == "第二輪"

    with session_factory() as db:
        new_units = list(
            db.execute(_select(TacticalUnit).where(TacticalUnit.session_id == new_id)).scalars()
        )
        assert {u.designation for u in new_units} == {"HQ", "P1"}
        new_hq = next(u for u in new_units if u.designation == "HQ")
        new_p1 = next(u for u in new_units if u.designation == "P1")
        # 部署/固定/戰力 verbatim（含已減損戰力）。
        assert new_hq.is_fixed is True
        assert new_hq.current_lat == 24.1 and new_hq.current_lng == 120.8
        assert new_hq.current_strength == 63.0
        # parent 於新局內重新連結（指向新 HQ，非舊）。
        assert new_p1.parent_id == new_hq.id
        # 裝備 verbatim（含數量與彈藥）。
        eq = list(
            db.execute(
                _select(EquipmentInstance).where(EquipmentInstance.owner_id == new_p1.id)
            ).scalars()
        )
        assert len(eq) == 1 and eq[0].quantity == 7 and eq[0].current_state == {"ammo": 40}
        # 新 master_seed（新一輪獨立 RNG）。
        assert db.get(WargameSession, new_id).master_seed != src_seed  # type: ignore[union-attr]


def test_clone_requires_director(session_factory: sessionmaker[Session]) -> None:
    """#79：非本局統裁/管理者不可複製。"""
    seed_user(session_factory, username="alice", role=UserRole.COMMANDER)
    seed_user(session_factory, username="mallory", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    a = auth_header(login(client, "alice")["access_token"])
    sid = client.post("/api/v1/sessions", json={"name": "alice 的局"}, headers=a).json()["id"]
    m = auth_header(login(client, "mallory")["access_token"])
    assert client.post(f"/api/v1/sessions/{sid}/clone", headers=m).status_code == 403


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


def test_allow_fratricide_reaches_the_api(session_factory: sessionmaker[Session]) -> None:
    """WP-C9：`allowFratricide` 要**從 DB 一路回到 `/sessions`**。

    這條在補一個具體的洞：後端早就有這個旗標（想定 → `WargameSession.allowFratricide` →
    `precheck` 放行友軍目標），但**沒有任何 API 回它**，於是 COP 只能永遠把盟軍濾出
    ENGAGE 下拉——後端放行了，操作員還是點不到。

    兩邊都斷言：預設要是 `False`（既有局行為不變），設成 True 後要真的變 True
    （只測預設值的話，欄位寫死回 False 也會綠）。
    """
    from app.models.tables import WargameSession

    seed_user(session_factory)
    client = make_client(session_factory)
    h = auth_header(login(client)["access_token"])
    sid = client.post("/api/v1/sessions", json={"name": "誤傷局"}, headers=h).json()["id"]

    # 用 id 撈而不是 `[0]`：這個帳號是統裁（看得到全部 session），本檔日後多開一局
    # 就會讓位置索引指到別人的局。
    def mine() -> bool:
        rows = client.get("/api/v1/sessions", headers=h).json()
        return next(s["allow_fratricide"] for s in rows if s["id"] == sid)

    assert mine() is False

    with session_factory() as db:
        db.get(WargameSession, sid).allow_fratricide = True  # type: ignore[union-attr]
        db.commit()
    assert mine() is True
