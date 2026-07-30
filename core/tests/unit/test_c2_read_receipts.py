"""C2 已讀留痕與跨陣營發信（D-c2）——釘住四個「後端有、實際沒效果」的洞。

這一檔抓的都是同一類病：欄位存在於 DB／契約，但沒有寫入端或沒有閘門，
於是「存得進去、讀得回來、測試全綠、實際沒效果」。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.main import app
from app.models import SessionParticipant, User, UserRole
from app.models.enums import SeatRole


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _client(factory: sessionmaker[Session]) -> TestClient:
    def _db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    return TestClient(app)


def _hdr(user_id: str, role: UserRole = UserRole.COMMANDER) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(user_id, role)}"}


def _cmdr(world: OrderWorld) -> dict[str, str]:
    return _hdr(world.cmdr_user_id)


def _white(world: OrderWorld) -> dict[str, str]:
    return _hdr(world.white_user_id, UserRole.WHITE_CELL_STAFF)


def _add_blue_seat(
    factory: sessionmaker[Session], world: OrderWorld, username: str, seat: SeatRole
) -> str:
    """在藍軍再加一位參與者（自己的 User），指定席位。回 user_id。"""
    with factory() as db:
        u = User(username=username, password_hash="x", role=UserRole.COMMANDER)
        db.add(u)
        db.flush()
        db.add(
            SessionParticipant(
                user_id=u.id,
                session_id=world.session_id,
                faction="BLUE",
                role=UserRole.COMMANDER,
                seat_role=seat,
                unit_scope=[],
            )
        )
        db.commit()
        return u.id


def _inbox(c: TestClient, world: OrderWorld, hdr: dict[str, str]) -> list[dict[str, object]]:
    r = c.get(f"/api/v1/sessions/{world.session_id}/messages", headers=hdr)
    assert r.status_code == 200, r.text
    return list(r.json())


# ---- read_at：欄位在 model 與契約裡躺著，view 沒帶、也沒有任何寫入端 ----


def test_message_view_exposes_read_at(session_factory: sessionmaker[Session]) -> None:
    """**MessageView 少了 read_at 這一欄**。

    DB（Message.readAt）與契約（MessageView.read_at）都有，只有 view 漏掉——
    前端因此連「有沒有已讀這回事」都看不到。未讀時必須是明確的 null，不是欄位不存在。
    """
    world = seed_world(session_factory)
    c = _client(session_factory)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "body": "測試"},
        headers=_cmdr(world),
    )
    assert r.status_code == 201, r.text
    assert "read_at" in r.json(), "送信回應沒有 read_at 欄位"
    assert r.json()["read_at"] is None
    assert "read_at" in _inbox(c, world, _cmdr(world))[0]


def test_recipient_mark_read_makes_sender_see_it(session_factory: sessionmaker[Session]) -> None:
    """**`readAt` 完全沒有寫入端**——沒有任何端點標得了已讀，信文永遠是未讀。

    收件方標示後，寄件者那側必須看得到（那正是已讀的用途：下級到底收到沒有）。
    """
    world = seed_world(session_factory)
    fso = _add_blue_seat(session_factory, world, "fso_r1", SeatRole.FSO_FIRES)
    c = _client(session_factory)
    mid = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "to_seat": "FSO_FIRES", "body": "火力席專用"},
        headers=_cmdr(world),
    ).json()["id"]

    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages/read",
        json={"message_ids": [mid]},
        headers=_hdr(fso),
    )
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == [mid]
    assert r.json()["read_at"] is not None
    assert _inbox(c, world, _cmdr(world))[0]["read_at"] is not None, "寄件者看不到已讀"


def test_mark_read_keeps_first_timestamp(session_factory: sessionmaker[Session]) -> None:
    """**首次已讀為準**：再標一次不得覆寫時戳，也不得重複列入 marked。

    AAR 問的是「第一次被看到是什麼時候」；每次開面板都刷新時戳的話這個問題就答不出來。
    """
    world = seed_world(session_factory)
    fso = _add_blue_seat(session_factory, world, "fso_r2", SeatRole.FSO_FIRES)
    c = _client(session_factory)
    mid = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "to_seat": "FSO_FIRES", "body": "x"},
        headers=_cmdr(world),
    ).json()["id"]
    url = f"/api/v1/sessions/{world.session_id}/messages/read"
    c.post(url, json={"message_ids": [mid]}, headers=_hdr(fso))
    first = _inbox(c, world, _hdr(fso))[0]["read_at"]

    again = c.post(url, json={"message_ids": [mid]}, headers=_hdr(fso))
    assert again.json()["marked"] == [], "已讀過的信被重複標示"
    assert _inbox(c, world, _hdr(fso))[0]["read_at"] == first


def test_sender_cannot_mark_own_message_read(session_factory: sessionmaker[Session]) -> None:
    """**寄件備份不算已讀**——寄件者一定看得到自己的信，若也計入就是「送出即已讀」。"""
    world = seed_world(session_factory)
    c = _client(session_factory)
    mid = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "body": "自己寄的"},
        headers=_cmdr(world),
    ).json()["id"]
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages/read",
        json={"message_ids": [mid]},
        headers=_cmdr(world),
    )
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == []
    assert _inbox(c, world, _cmdr(world))[0]["read_at"] is None


def test_omniscient_view_does_not_mark_read(session_factory: sessionmaker[Session]) -> None:
    """**統裁的全知旁通不得算成已讀。**

    白軍看得到全場信文；若他一開面板（或按「全部標示已讀」）就把各陣營的信標掉，
    已讀留痕當場失真——AAR 會以為下級都讀過了。
    """
    world = seed_world(session_factory)
    c = _client(session_factory)
    c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "body": "藍軍內部通報"},
        headers=_cmdr(world),
    )
    assert len(_inbox(c, world, _white(world))) == 1, "前提：白軍全知，看得到藍軍的信"

    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages/read",
        json={"message_ids": None},  # 全部
        headers=_white(world),
    )
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == [], "白軍替藍軍把信標成已讀了"
    assert _inbox(c, world, _cmdr(world))[0]["read_at"] is None


def test_mark_read_ignores_other_seats_message(session_factory: sessionmaker[Session]) -> None:
    """**指定不是寄給自己的 id 也標不動**——否則同陣營別席可以偽造他席的已讀。"""
    world = seed_world(session_factory)
    fso = _add_blue_seat(session_factory, world, "fso_r3", SeatRole.FSO_FIRES)
    s2 = _add_blue_seat(session_factory, world, "s2_r3", SeatRole.S2_INTEL)
    c = _client(session_factory)
    mid = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "to_seat": "FSO_FIRES", "body": "火力席專用"},
        headers=_cmdr(world),
    ).json()["id"]

    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages/read",
        json={"message_ids": [mid]},
        headers=_hdr(s2),
    )
    assert r.json()["marked"] == []
    assert _inbox(c, world, _hdr(fso))[0]["read_at"] is None


# ---- to_faction：原本無條件採用請求值，等於誰都能往敵軍信文匣投信 ----


def test_commander_cannot_send_to_enemy_faction(session_factory: sessionmaker[Session]) -> None:
    """**跨陣營發信原本沒有任何閘門**：`to_faction=req.to_faction or part.faction`。

    收信匣的可見性只看 to_faction，所以藍軍指揮官帶一個 `to_faction: RED`
    就把信直接投進紅軍的信文匣（還冒充成紅軍內部往來）。
    """
    world = seed_world(session_factory)
    c = _client(session_factory)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "to_faction": "RED", "body": "投給敵軍"},
        headers=_cmdr(world),
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "AUTH_FORBIDDEN"
    assert _inbox(c, world, _white(world)) == [], "被擋下的信不該落庫"


def test_white_cell_may_send_cross_faction(session_factory: sessionmaker[Session]) -> None:
    """白軍（統裁）跨陣營發信要送得出去，且受眾真的換成目標陣營（藍軍看不到）。"""
    world = seed_world(session_factory)
    c = _client(session_factory)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "to_faction": "RED", "body": "統裁通知紅軍"},
        headers=_white(world),
    )
    assert r.status_code == 201, r.text
    assert r.json()["to_faction"] == "RED"
    assert _inbox(c, world, _cmdr(world)) == [], "藍軍看到了發給紅軍的信"


def test_cross_faction_target_must_exist(session_factory: sessionmaker[Session]) -> None:
    """**打錯字的陣營要當場擋下**。

    否則信落在一個沒有任何人屬於的陣營：送出 201、對方永遠收不到，
    正是「存得進去、實際沒效果」那種最難查的病。
    """
    world = seed_world(session_factory)
    c = _client(session_factory)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "to_faction": "REDD", "body": "打錯字"},
        headers=_white(world),
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "FACTION_INVALID"


def test_explicit_own_faction_is_not_cross_faction(session_factory: sessionmaker[Session]) -> None:
    """明示自己的陣營不算跨陣營——閘門不可把一般席位的正常發信一起擋掉。"""
    world = seed_world(session_factory)
    c = _client(session_factory)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "to_faction": "BLUE", "body": "本軍通報"},
        headers=_cmdr(world),
    )
    assert r.status_code == 201, r.text
    assert r.json()["to_faction"] == "BLUE"


# ---- 申請單留痕：欄位早就在 view 裡，釘住不許被拿掉（前端要靠它顯示核覆者） ----


def test_request_view_carries_decision_trail(session_factory: sessionmaker[Session]) -> None:
    """核覆留痕（申請席位／誰核的／第幾 tick）必須出現在 RequestView。

    schema 註解寫明這三欄是給 AAR 重建事件鏈用的；少任何一欄，申請人就只看得到
    一句沒有出處的核覆說明。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        p = db.get(SessionParticipant, world.blue_issuer_id)
        assert p is not None
        p.seat_role = SeatRole.COMMANDER
        db.commit()
    c = _client(session_factory)
    rid = c.post(
        f"/api/v1/sessions/{world.session_id}/requests",
        json={"kind": "AIR_RECON", "note": "請求空偵"},
        headers=_cmdr(world),
    ).json()["id"]
    d = c.post(
        f"/api/v1/sessions/{world.session_id}/requests/{rid}/decide",
        json={"approve": True, "note": "核准"},
        headers=_cmdr(world),
    )
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["requested_seat"] == "COMMANDER"
    assert body["decided_by"] == "cmdr"
    assert body["decided_at_tick"] is not None
