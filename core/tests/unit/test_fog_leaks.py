"""兩個紅線 3 洩漏——**主幹守得住，旁支繞過去**。

紅線 3：「fog of war 的 faction 過濾只能在後端」。`/units`、`/intel`、`/state`、WS
這四條主幹都守得住，但兩個旁支端點把它整片繞過：

1. `GET /aar/replay/states`：`_visible_events` 把**事件**霧化了，但單位**名冊**沒有
   ——任一參與者 poll 就拿到全陣營的番號、編制與 tick 0 即時座標。
   該端點的 docstring 自己都寫了「參與者在演習進行中就能 poll AAR，
   不投影的話等於一個沒有上鎖的敵情窗口」。
2. `POST /movement/preview`：完全不檢查目標單位陣營。`require_participant` 只為了
   「阻礙可見性」而呼叫，單位本身沒閘門——拿別人的 unit_id 就能得到**即時座標**、
   機動能力與剩餘油料，而且不留痕跡。

這兩條都是**演習進行中**就能打的，不是事後才開放。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.models import SessionParticipant, TacticalUnit, UnitLevel, WargameSession
from app.models.enums import UserRole


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _two_faction_session(factory: sessionmaker[Session], user_id: str) -> str:
    """一局兩軍：呼叫者是 RED 指揮官；BLUE 是他不該看到的那一邊。"""
    with factory() as db:
        s = WargameSession(name="fog", master_seed=1, current_weather={})
        db.add(s)
        db.flush()
        for uid, desig, faction, lat in (
            ("red-1", "R1", "RED", 24.0),
            ("blue-1", "B1", "BLUE", 25.0),
        ):
            db.add(
                TacticalUnit(
                    id=uid,
                    session_id=s.id,
                    designation=desig,
                    unit_level=UnitLevel.COMPANY,
                    faction=faction,
                    current_lat=lat,
                    current_lng=121.0,
                )
            )
        db.add(
            SessionParticipant(
                session_id=s.id,
                user_id=user_id,
                faction="RED",
                role=UserRole.COMMANDER,
                unit_scope={},
            )
        )
        db.commit()
        return str(s.id)


def _red_client(factory: sessionmaker[Session]) -> tuple[object, dict[str, str], str]:
    user_id = seed_user(factory, role=UserRole.COMMANDER)
    client = make_client(factory)
    h = auth_header(login(client)["access_token"])
    return client, h, _two_faction_session(factory, user_id)


def test_aar_replay_states_does_not_hand_over_the_enemy_roster(
    session_factory: sessionmaker[Session],
) -> None:
    """RED 指揮官不可以在 `/aar/replay/states` 拿到 BLUE 的番號與座標。"""
    client, h, sid = _red_client(session_factory)
    r = client.get(f"/api/v1/sessions/{sid}/aar/replay/states", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200, r.text
    units = r.json()["units"]
    factions = {u["faction"] for u in units}
    assert factions == {"RED"}, f"名冊洩漏了他軍：{factions}"
    assert all(u["designation"] != "B1" for u in units)


def test_movement_preview_refuses_someone_elses_unit(
    session_factory: sessionmaker[Session],
) -> None:
    """RED 指揮官不可以預覽 BLUE 單位的移動——那會回傳它的即時座標與機動諸元。

    也斷言**錯誤訊息不可以分辨「不存在」與「存在但不是你的」**：
    能分辨就等於確認了敵軍編成，錯誤訊息本身變成偵察工具。
    """
    client, h, sid = _red_client(session_factory)
    body = {"unit_id": "blue-1", "to_lat": 25.01, "to_lng": 121.01}
    r = client.post(f"/api/v1/sessions/{sid}/movement/preview", json=body, headers=h)  # type: ignore[attr-defined]
    assert r.status_code >= 400, f"竟然讓 RED 預覽了 BLUE 的單位：{r.text}"

    ghost = {"unit_id": "does-not-exist", "to_lat": 25.01, "to_lng": 121.01}
    r2 = client.post(f"/api/v1/sessions/{sid}/movement/preview", json=ghost, headers=h)  # type: ignore[attr-defined]
    assert r2.status_code == r.status_code
    assert r2.json() == r.json(), "「他軍單位」與「不存在」的回應不同 → 可用來探測敵軍編成"


def test_movement_preview_still_works_on_your_own_unit(
    session_factory: sessionmaker[Session],
) -> None:
    """閘門不可以把自己人也擋掉——這是最容易在修洩漏時做過頭的地方。"""
    client, h, sid = _red_client(session_factory)
    body = {"unit_id": "red-1", "to_lat": 24.01, "to_lng": 121.01}
    r = client.post(f"/api/v1/sessions/{sid}/movement/preview", json=body, headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200, r.text
