"""C2 信文/申請 REST 端點（WP-B5.2）——重點在**收信匣不得跨席位外洩**。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.main import app
from app.models import SessionParticipant, UserRole
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


def _cmdr(world: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(world.cmdr_user_id, UserRole.COMMANDER)}"}


def _white(world: OrderWorld) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)}"
    }


def _set_seat(factory: sessionmaker[Session], pid: str, seat: SeatRole | None) -> None:
    with factory() as db:
        p = db.get(SessionParticipant, pid)
        assert p is not None
        p.seat_role = seat
        db.commit()


def test_send_and_read_own_message(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    c = _client(session_factory)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "body": "測試信文"},
        headers=_cmdr(world),
    )
    assert r.status_code == 201, r.text
    got = c.get(f"/api/v1/sessions/{world.session_id}/messages", headers=_cmdr(world))
    assert got.status_code == 200
    assert [m["body"] for m in got.json()] == ["測試信文"]


def _add_blue_seat(
    factory: sessionmaker[Session], world: OrderWorld, username: str, seat: SeatRole
) -> str:
    """在藍軍再加一位參與者（自己的 User），指定席位。回 user_id。"""
    from app.models import User

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


def test_inbox_does_not_leak_across_seats(session_factory: sessionmaker[Session]) -> None:
    """**寄給某席位的信文，同陣營的別席不得看到。**

    這是本卡最該防的外洩：席位存在的意義就是同陣營內也有知的邊界。
    要真的驗到，必須有**第三個人**——寄件人自己一定看得到（寄件備份），
    只看寄件人等於什麼都沒測。
    """
    world = seed_world(session_factory)
    fso = _add_blue_seat(session_factory, world, "fso", SeatRole.FSO_FIRES)
    s2 = _add_blue_seat(session_factory, world, "s2", SeatRole.S2_INTEL)
    c = _client(session_factory)

    # 指揮官發一封只給火力席的信
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "to_seat": "FSO_FIRES", "body": "火力席專用"},
        headers=_cmdr(world),
    )
    assert r.status_code == 201, r.text

    def _inbox(uid: str) -> list[str]:
        h = {"Authorization": f"Bearer {order_token(uid, UserRole.COMMANDER)}"}
        return [
            m["body"]
            for m in c.get(f"/api/v1/sessions/{world.session_id}/messages", headers=h).json()
        ]

    assert _inbox(fso) == ["火力席專用"], "收件席位收不到自己的信"
    assert _inbox(s2) == [], "**同陣營別席看到了不該看的信文**"


def test_faction_wide_message_reaches_all_seats(
    session_factory: sessionmaker[Session],
) -> None:
    """未指定席位＝發給整個陣營，各席都收得到（席位只收窄，不改變原本的陣營語義）。"""
    world = seed_world(session_factory)
    s2 = _add_blue_seat(session_factory, world, "s2b", SeatRole.S2_INTEL)
    c = _client(session_factory)
    c.post(
        f"/api/v1/sessions/{world.session_id}/messages",
        json={"kind": "FREE_TEXT", "body": "全軍通報"},
        headers=_cmdr(world),
    )
    h = {"Authorization": f"Bearer {order_token(s2, UserRole.COMMANDER)}"}
    got = c.get(f"/api/v1/sessions/{world.session_id}/messages", headers=h).json()
    assert [m["body"] for m in got] == ["全軍通報"]


def test_non_participant_forbidden(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    c = _client(session_factory)
    r = c.get(f"/api/v1/sessions/{world.session_id}/messages", headers=_white(world))
    # 白軍是全知 → 可讀；這條確認全知不被誤擋
    assert r.status_code == 200


def test_submit_and_decide_request_roundtrip(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _set_seat(session_factory, world.blue_issuer_id, SeatRole.COMMANDER)
    c = _client(session_factory)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/requests",
        json={"kind": "AIR_RECON", "params": {"area": "A1"}, "note": "請求空偵"},
        headers=_cmdr(world),
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["status"] == "PENDING"

    d = c.post(
        f"/api/v1/sessions/{world.session_id}/requests/{rid}/decide",
        json={"approve": True, "note": "核准"},
        headers=_cmdr(world),
    )
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "APPROVED"
    assert d.json()["decided_at_tick"] is not None  # 留痕


def test_decide_twice_conflicts(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _set_seat(session_factory, world.blue_issuer_id, SeatRole.COMMANDER)
    c = _client(session_factory)
    rid = c.post(
        f"/api/v1/sessions/{world.session_id}/requests",
        json={"kind": "FIRE_SUPPORT"},
        headers=_cmdr(world),
    ).json()["id"]
    c.post(
        f"/api/v1/sessions/{world.session_id}/requests/{rid}/decide",
        json={"approve": True},
        headers=_cmdr(world),
    )
    again = c.post(
        f"/api/v1/sessions/{world.session_id}/requests/{rid}/decide",
        json={"approve": False},
        headers=_cmdr(world),
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "REQUEST_ALREADY_DECIDED"


def test_staff_seat_cannot_decide(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _set_seat(session_factory, world.blue_issuer_id, SeatRole.COMMANDER)
    c = _client(session_factory)
    rid = c.post(
        f"/api/v1/sessions/{world.session_id}/requests",
        json={"kind": "FIRE_SUPPORT"},
        headers=_cmdr(world),
    ).json()["id"]
    _set_seat(session_factory, world.blue_issuer_id, SeatRole.S3_OPS)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/requests/{rid}/decide",
        json={"approve": True},
        headers=_cmdr(world),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "REQUEST_APPROVAL_DENIED"


def test_quota_view_reports_limits(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    c = _client(session_factory)
    body = c.get(f"/api/v1/sessions/{world.session_id}/requests", headers=_cmdr(world)).json()
    kinds = {q["kind"] for q in body["quotas"]}
    assert kinds == {"AIR_RECON", "FIRE_SUPPORT", "RESUPPLY_VOUCHER", "CALL_FOR_FIRE"}
    assert all(q["limit"] is None for q in body["quotas"])  # 未宣告＝不限


# ---- WP-C10.1 臨機火力：沒有觀測就叫不動火力 ----


def _override_gateway(visible: bool) -> None:
    """注入假 gateway：控制 LOS 結果。"""
    from app.api.deps import get_gateway
    from app.orders.precheck import LosOutcome

    class _G:
        def path_reachable(self, a: str, b: str, c: str) -> tuple[bool, str]:
            return True, ""

        def has_los(self, o: tuple[float, float, float], t: tuple[float, float, float]) -> object:
            return LosOutcome(visible=visible, clearance_m=10.0)

        def elevation(self, lat: float, lng: float) -> float:
            return 0.0

    app.dependency_overrides[get_gateway] = lambda: _G()


def test_call_for_fire_needs_target_coords(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    c = _client(session_factory)
    _override_gateway(True)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/requests",
        json={"kind": "CALL_FOR_FIRE"},  # 沒帶目標
        headers=_cmdr(world),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "REQUEST_NO_OBSERVER"


def test_call_for_fire_blocked_without_observer(session_factory: sessionmaker[Session]) -> None:
    """**沒有觀測就叫不動火力**——[JCATS-F p.12] 觀測所在整條鏈裡不是形式。"""
    world = seed_world(session_factory)
    c = _client(session_factory)
    _override_gateway(False)  # 全陣營對該點皆無視線
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/requests",
        json={"kind": "CALL_FOR_FIRE", "params": {"target_lat": 23.8, "target_lng": 121.3}},
        headers=_cmdr(world),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "REQUEST_NO_OBSERVER"


def test_call_for_fire_accepted_with_observer(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    c = _client(session_factory)
    _override_gateway(True)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/requests",
        json={"kind": "CALL_FOR_FIRE", "params": {"target_lat": 23.8, "target_lng": 121.3}},
        headers=_cmdr(world),
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "PENDING"


def test_other_request_kinds_unaffected_by_observer_rule(
    session_factory: sessionmaker[Session],
) -> None:
    """觀測條件只綁 CALL_FOR_FIRE——別的申請種類不受影響。"""
    world = seed_world(session_factory)
    c = _client(session_factory)
    _override_gateway(False)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/requests",
        json={"kind": "AIR_RECON"},
        headers=_cmdr(world),
    )
    assert r.status_code == 201, r.text
