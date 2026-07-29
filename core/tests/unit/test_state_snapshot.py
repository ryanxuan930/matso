"""WP-E3 `/state` 原子快照：**與各 GET 端點逐項一致**（紅線 3 的迷霧過濾只在後端）。

本檔的核心不是「快照回得出東西」，而是「快照看得到的**恰好**等於該身分在各端點看得到的」。
差一個單位就是迷霧漏洞：重連後看到的比正常時多（洩漏），或少（誤殺）。
故每條一致性測試都**同時打快照與對應端點**再比對，而不是寫死期望值——
寫死期望值的話，日後某個端點的過濾改了，快照會安靜地留在舊規則而測試照樣綠。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import app.api.state as state_mod
from app.api.deps import get_db, get_settings
from app.intel import store
from app.intel.sweep import Contact
from app.main import app
from app.models import MapFeature, SessionParticipant, User
from app.models.enums import IntelFidelity, UserRole
from app.state.hot_state import session_tick_key
from app.state.redis_stream import seq_key


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeStrictRedis:
    client = FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(state_mod, "make_redis", lambda *_a, **_k: client)
    return client


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session_factory()
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def world(session_factory: sessionmaker[Session]) -> OrderWorld:
    return seed_world(session_factory)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cmdr(w: OrderWorld) -> dict[str, str]:
    return _hdr(order_token(w.cmdr_user_id, UserRole.COMMANDER))


def _white(w: OrderWorld) -> dict[str, str]:
    return _hdr(order_token(w.white_user_id, UserRole.WHITE_CELL_STAFF))


def _seed_extras(factory: sessionmaker[Session], w: OrderWorld) -> None:
    """給世界加一筆敵情與三個不同 owner 的標註，讓「過濾有作用」不是空集合對空集合。"""
    with factory() as db:
        store.record(
            db,
            w.session_id,
            Contact("RED", w.blue_unit_id, IntelFidelity.DETECTED, 3, 23.75, 121.25, 500.0),
        )
        for owner, label in (("WHITE_CELL", "COMMON"), ("BLUE", "BLUE-OP"), ("RED", "RED-OP")):
            db.add(
                MapFeature(
                    session_id=w.session_id,
                    kind="ANNOTATION",
                    geometry_type="POINT",
                    geometry=[121.2, 23.7],
                    owner_faction=owner,
                    label=label,
                    attributes={},
                )
            )
        db.commit()


def _snapshot(client: TestClient, w: OrderWorld, headers: dict[str, str], q: str = "") -> dict:  # type: ignore[type-arg]
    r = client.get(f"/api/v1/sessions/{w.session_id}/state{q}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _endpoint(client: TestClient, w: OrderWorld, path: str, headers: dict[str, str], q: str = ""):  # type: ignore[no-untyped-def]
    r = client.get(f"/api/v1/sessions/{w.session_id}/{path}{q}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- 一致性：快照 == 各端點（每個身分/視角各驗一次）---


@pytest.mark.parametrize(
    ("who", "query"),
    [("cmdr", ""), ("white", ""), ("white", "?as_faction=RED"), ("white", "?as_faction=BLUE")],
)
def test_snapshot_matches_every_endpoint(
    client: TestClient,
    session_factory: sessionmaker[Session],
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
    who: str,
    query: str,
) -> None:
    _seed_extras(session_factory, world)
    headers = _cmdr(world) if who == "cmdr" else _white(world)

    snap = _snapshot(client, world, headers, query)
    assert snap["units"] == _endpoint(client, world, "units", headers, query)
    assert snap["contacts"] == _endpoint(client, world, "intel", headers, query)
    assert snap["map_features"] == _endpoint(client, world, "map-features", headers, query)
    assert snap["relations"] == _endpoint(client, world, "relations", headers, query)


def test_commander_snapshot_actually_hides_the_enemy(
    client: TestClient,
    session_factory: sessionmaker[Session],
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
) -> None:
    """一致性測試若兩邊都是空的就沒有意義——這條確認過濾真的有東西可濾。"""
    _seed_extras(session_factory, world)
    snap = _snapshot(client, world, _cmdr(world))
    assert [u["id"] for u in snap["units"]] == [world.blue_unit_id]
    assert {f["label"] for f in snap["map_features"]} == {"COMMON", "BLUE-OP"}  # 無 RED-OP
    assert snap["contacts"] == []  # 那筆 contact 是 RED 觀測到的
    assert snap["observer_faction"] == "BLUE"

    god = _snapshot(client, world, _white(world))
    assert {u["id"] for u in god["units"]} == {world.blue_unit_id, world.red_unit_id}
    assert len(god["contacts"]) == 1
    assert god["observer_faction"] is None  # 全知未指定視角＝god view


# --- 權限（與 units/intel/map-features 同紀律）---


def test_non_white_cell_cannot_switch_viewpoint(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/state?as_faction=RED", headers=_cmdr(world)
    )
    assert r.status_code == 403


def test_non_participant_is_rejected(
    client: TestClient,
    session_factory: sessionmaker[Session],
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
) -> None:
    with session_factory() as db:
        outsider = User(username="outsider", password_hash="x", role=UserRole.COMMANDER)
        db.add(outsider)
        db.commit()
        uid = outsider.id
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/state",
        headers=_hdr(order_token(uid, UserRole.COMMANDER)),
    )
    assert r.status_code == 403


def test_omniscient_non_participant_gets_a_snapshot(
    client: TestClient,
    session_factory: sessionmaker[Session],
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
) -> None:
    """未加入該局的白軍觀察員：units/map-features/WS 一直放行，intel 卻 403。

    快照要與各端點逐項一致，就必須先讓各端點彼此一致——WP-E3 把 /intel 對齊了。
    """
    with session_factory() as db:
        db.query(SessionParticipant).filter(
            SessionParticipant.user_id == world.white_user_id
        ).delete()
        db.commit()
    snap = _snapshot(client, world, _white(world))
    assert {u["id"] for u in snap["units"]} == {world.blue_unit_id, world.red_unit_id}
    assert _endpoint(client, world, "intel", _white(world)) == snap["contacts"]


# --- tick / last_seq ---


def test_tick_and_last_seq_come_from_redis(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    fake_redis.set(seq_key(world.session_id), 4321)
    fake_redis.set(session_tick_key(world.session_id), 77)
    snap = _snapshot(client, world, _cmdr(world))
    assert (snap["last_seq"], snap["tick"]) == (4321, 77)


def test_missing_redis_keys_degrade_to_zero(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """尚未跑過任何 tick 的局：快照仍要可用（0 只是讓 client 不去重，不是錯誤）。"""
    snap = _snapshot(client, world, _cmdr(world))
    assert (snap["last_seq"], snap["tick"]) == (0, 0)


def test_redis_outage_does_not_break_the_snapshot(
    client: TestClient, world: OrderWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down:
        def get(self, _key: str) -> str:
            raise ConnectionError("redis down")

    monkeypatch.setattr(state_mod, "make_redis", lambda *_a, **_k: Down())
    snap = _snapshot(client, world, _cmdr(world))
    assert snap["last_seq"] == 0
    assert [u["id"] for u in snap["units"]] == [world.blue_unit_id]  # 狀態照回


def test_last_seq_is_sampled_before_the_state(
    client: TestClient, world: OrderWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取樣順序是正確性的一部分（見 state.py docstring）：先 seq、後狀態。

    反過來的話，介於兩次讀取之間送出的 STATE_DIFF 會既不在快照裡、seq 又 ≤ last_seq，
    client 依約丟棄它 → 遺失更新。這裡以「讀 seq 時狀態尚未被查詢」釘住順序。
    """
    order: list[str] = []

    class Recording:
        def get(self, key: str) -> str | None:
            order.append("redis:" + key.rsplit(":", 1)[-1])
            return None

    monkeypatch.setattr(state_mod, "make_redis", lambda *_a, **_k: Recording())
    real_list_units = state_mod.list_units

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        order.append("state")
        return real_list_units(*args, **kwargs)

    monkeypatch.setattr(state_mod, "list_units", spy)
    _snapshot(client, world, _cmdr(world))
    assert order.index("redis:broadcast_seq") < order.index("state")
