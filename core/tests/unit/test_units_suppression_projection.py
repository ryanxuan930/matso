"""WP-C1 在 REST 投影層的 fog 規則：壓制度與姿態只給友軍。

**為什麼這是安全問題而不只是顯示問題**：看得到敵軍被壓制多少，等於一份免費的即時戰果評估
——「我打過去了，他被壓到 0.7」直接告訴你火力效果。WP-C10.4 花了整張卡讓戰果只能靠
觀測員估算並帶 ±30% 誤差；`/units` 直接吐真值就把那張卡整個繞過去。

姿態同理：對方掘壕到什麼程度是要靠偵察才知道的事。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import app.api.units as units_mod
from app.api.deps import get_db, get_settings
from app.models.enums import UserRole
from app.state.hot_state import unit_key


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeStrictRedis:
    client = FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(units_mod, "make_redis", lambda *_a, **_k: client)
    return client


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    from app.main import app

    app.dependency_overrides[get_db] = lambda: session_factory()
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def world(session_factory: sessionmaker[Session]) -> OrderWorld:
    return seed_world(session_factory)


def _suppress(redis: FakeStrictRedis, world: OrderWorld, unit_id: str) -> None:
    redis.set(
        unit_key(world.session_id, unit_id),
        json.dumps({"suppression": 0.72, "posture": "DUG_IN", "posture_target": "DUG_IN"}),
    )


def _units(client: TestClient, w: OrderWorld, headers: dict[str, str], q: str = ""):  # type: ignore[no-untyped-def]
    r = client.get(f"/api/v1/sessions/{w.session_id}/units{q}", headers=headers)
    assert r.status_code == 200, r.text
    return {u["id"]: u for u in r.json()}


def _cmdr(w: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(w.cmdr_user_id, UserRole.COMMANDER)}"}


def _white(w: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(w.white_user_id, UserRole.WHITE_CELL_STAFF)}"}


def test_own_unit_reports_its_suppression_and_posture(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    _suppress(fake_redis, world, world.blue_unit_id)
    unit = _units(client, world, _cmdr(world))[world.blue_unit_id]
    assert unit["suppression"] == 0.72
    assert unit["posture"] == "DUG_IN"


def test_missing_hot_state_reads_as_neutral(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """既有局（那些鍵根本不存在）→ 0 / MOVING，即中性預設。"""
    unit = _units(client, world, _cmdr(world))[world.blue_unit_id]
    assert (unit["suppression"], unit["posture"]) == (0.0, "MOVING")


def test_enemy_unit_never_leaks_suppression(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """白軍指定 `as_faction=BLUE` ＝問「藍軍看得到什麼」→ 紅軍的壓制度必須是中性值。

    走白軍視角切換而不是藍軍 token，是因為一般角色的敵軍單位早被 SQL 濾掉了根本看不到；
    要驗的是**萬一它出現在回應裡**（視角切換、STUB_GATEWAY affordance）也不能帶壓制度。
    """
    _suppress(fake_redis, world, world.red_unit_id)
    # as_faction=RED：確認資料真的寫進去了（否則下面的斷言會因為「根本沒資料」而假綠）。
    assert _units(client, world, _white(world), "?as_faction=RED")[world.red_unit_id][
        "suppression"
    ] == pytest.approx(0.72)

    blue_view = _units(client, world, _white(world), "?as_faction=BLUE")
    assert world.red_unit_id not in blue_view  # 敵軍本來就不該在藍軍視角裡


def test_god_view_sees_every_unit_suppression(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """白軍 god view 沒有「他方」——全知者本來就看得到真實位置，壓制度不另設限。"""
    _suppress(fake_redis, world, world.red_unit_id)
    red = _units(client, world, _white(world))[world.red_unit_id]
    assert red["suppression"] == 0.72
    assert red["posture"] == "DUG_IN"


def test_stub_gateway_affordance_does_not_leak_suppression(
    client: TestClient,
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E 的 STUB_GATEWAY 放行全單位給一般角色——**壓制度不跟著放行**。

    這是本檔最重要的一條：那個 affordance 讓 faction 過濾整條 SQL where 消失，
    是唯一一條「敵軍單位真的會出現在作戰方回應裡」的路徑。
    """
    stub = TEST_SETTINGS.model_copy(update={"stub_gateway": True})
    client.app.dependency_overrides[get_settings] = lambda: stub  # type: ignore[attr-defined]
    _suppress(fake_redis, world, world.red_unit_id)
    view = _units(client, world, _cmdr(world))
    assert world.red_unit_id in view  # affordance 確實放行了敵軍（否則此測試沒在測東西）
    assert view[world.red_unit_id]["suppression"] == 0.0
    assert view[world.red_unit_id]["posture"] == "MOVING"
