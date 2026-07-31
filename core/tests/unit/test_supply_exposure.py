"""WP-C7 補給在 `/units` 的曝露與 fog（後勤是個看不見的黑盒，這一檔是那扇窗）。

## 為什麼補給水位是敵情，不只是顯示欄位

`suppression` 洩漏的是「我剛才那一輪打得怎麼樣」；補給水位洩漏的更遠——它是一份
**未來時間表**：對方的 Class I 剩兩成，代表再過一天半他就會掉到 ×0.75 效能。
知道這件事的一方可以什麼都不做，等到那個時刻再打。所以他方單位一律拿中性值
（空清單 + 0 天），與 `suppression`/`posture` 同一條規則，且**過濾在後端**（紅線 3）。

## 中性語義：空清單 ≠ 全部見底

`read_levels()` 對缺 `supply` 鍵回空 dict，本端點照樣回**空陣列**。這一條要有測試釘住，
因為最自然的「順手改進」就是把四個類別都補成 0 —— 那會讓每個既有想定的單位在 COP 上
都顯示「補給 0%」，看起來全軍在挨餓。
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
import yaml
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import app.api.units as units_mod
from app.api.deps import get_db, get_settings
from app.models.enums import UserRole
from app.state.hot_state import unit_key

_CONTRACT = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "core_api.yaml"


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


def _seed_supply(redis: FakeStrictRedis, world: OrderWorld, unit_id: str, **extra: Any) -> None:
    """把補給熱狀態塞進 Redis。

    ⚠ **誠實說明**：播種端（想定/ORBAT → `seed_combat_state`）是另一軌的範圍，本檔驗的是
    「熱狀態裡有這些鍵時，`/units` 有沒有正確地投影出來、有沒有洩漏」。
    塞的是**熱狀態的真實編碼**（`{類別: [存量, 容量]}`，見 `supply_wiring.write_levels`），
    不是 `UnitView` 的形狀——若寫成後者，這條測試就會變成在驗自己餵進去的資料。
    """
    state: dict[str, Any] = {"supply": {"I": [120.0, 400.0], "IX": [5.0, 50.0]}, **extra}
    redis.set(unit_key(world.session_id, unit_id), json.dumps(state))


def _units(
    client: TestClient, w: OrderWorld, headers: dict[str, str], q: str = ""
) -> dict[str, Any]:
    r = client.get(f"/api/v1/sessions/{w.session_id}/units{q}", headers=headers)
    assert r.status_code == 200, r.text
    return {u["id"]: u for u in r.json()}


def _cmdr(w: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(w.cmdr_user_id, UserRole.COMMANDER)}"}


def _white(w: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(w.white_user_id, UserRole.WHITE_CELL_STAFF)}"}


def test_units_response_matches_the_contract(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """**契約說必有的欄位，回應體裡要真的有。**

    本週抓到的 HIGH 就是這個形狀：契約把欄位列為 required、卻沒有任何測試驗回應體，
    於是把那些鍵從回傳 dict 刪掉，全部測試照樣綠——前端照契約宣告成必有，
    畫面就會印 `undefined%`，而沒有任何閘門會發現。
    （寫法沿用 `test_aar_api.test_stats_response_matches_the_contract`。）
    """
    import jsonschema

    spec = yaml.safe_load(_CONTRACT.read_text("utf-8"))
    schema = dict(spec["components"]["schemas"]["UnitView"])
    schema["components"] = spec["components"]  # 供 `supply` 的 $ref 解到 SupplyLevelView

    _seed_supply(fake_redis, world, world.blue_unit_id, starved_days=2.5)
    body = _units(client, world, _cmdr(world))[world.blue_unit_id]

    jsonschema.validate(body, schema)
    missing = set(schema["required"]) - set(body)
    assert not missing, f"回應少了契約宣告為必填的欄位：{sorted(missing)}；實得 {sorted(body)}"
    assert json.dumps(body)  # 可序列化（/state 快照與封存包都要用）

    # ⚠ `supply` 是唯一帶 $ref 的欄位——**巢狀 schema 沒解開的話上面那句會無聲放行任何東西**。
    # 故當場證明它真的在驗：塞一個不合法的類別代號進去，必須被擋。
    bogus = {**body, "supply": [{**body["supply"][0], "supply_class": "II"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bogus, schema)


def test_own_unit_reports_supply_levels_and_starvation(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """己方單位：水位照熱狀態、順序照北約編號（I → III → V → IX，不是字典序）。"""
    _seed_supply(fake_redis, world, world.blue_unit_id, starved_days=2.5)
    unit = _units(client, world, _cmdr(world))[world.blue_unit_id]

    assert [s["supply_class"] for s in unit["supply"]] == ["I", "IX"]
    rations = unit["supply"][0]
    assert (rations["on_hand"], rations["capacity"]) == (120.0, 400.0)
    assert rations["fraction"] == pytest.approx(0.3)
    assert unit["starved_days"] == 2.5


def test_undeclared_classes_are_absent_not_zero(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """`capacity <= 0` ＝**未編制**，不是「空的」——未編制的類別根本不該出現在清單裡。

    補成 0% 的話，一個不帶維修件的步槍排會在卡片上顯示「維修件 0%」，
    看起來像它急需補給；而它從來就沒有那本帳。
    """
    fake_redis.set(
        unit_key(world.session_id, world.blue_unit_id),
        json.dumps({"supply": {"I": [50.0, 100.0], "IX": [0.0, 0.0]}}),
    )
    unit = _units(client, world, _cmdr(world))[world.blue_unit_id]
    assert [s["supply_class"] for s in unit["supply"]] == ["I"]


def test_missing_supply_key_reads_as_no_ledger_not_starving(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """既有想定（沒有 `supply` 鍵）→ 空陣列 + 0 天。

    這條守的是整張卡的中性保證：**空陣列與「全部 0」是完全不同的兩件事**。
    後者會讓每一個既有局的每一支部隊都顯示成正在斷補。
    """
    unit = _units(client, world, _cmdr(world))[world.blue_unit_id]
    assert unit["supply"] == []
    assert unit["starved_days"] == 0.0


def test_enemy_supply_never_leaks_through_the_stub_affordance(
    client: TestClient,
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E 的 `STUB_GATEWAY` 放行全單位給一般角色——**補給不跟著放行**。

    這是本檔最重要的一條：那個 affordance 讓 faction 過濾的整條 SQL where 消失，
    是唯一一條「敵軍單位真的會出現在作戰方回應裡」的路徑（同
    `test_units_suppression_projection` 的理由）。
    """
    stub = TEST_SETTINGS.model_copy(update={"stub_gateway": True})
    client.app.dependency_overrides[get_settings] = lambda: stub  # type: ignore[attr-defined]
    _seed_supply(fake_redis, world, world.red_unit_id, starved_days=4.0)

    view = _units(client, world, _cmdr(world))
    assert world.red_unit_id in view  # affordance 確實放行了敵軍（否則此測試沒在測東西）
    assert view[world.red_unit_id]["supply"] == []
    assert view[world.red_unit_id]["starved_days"] == 0.0


def test_god_view_sees_every_unit_supply(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """白軍 god view 沒有「他方」——統裁本來就看得到真實位置，後勤帳不另設限。

    同時證明上一條不是「資料根本沒寫進去」造成的假綠。
    """
    _seed_supply(fake_redis, world, world.red_unit_id, starved_days=4.0)
    red = _units(client, world, _white(world))[world.red_unit_id]
    assert [s["supply_class"] for s in red["supply"]] == ["I", "IX"]
    assert red["starved_days"] == 4.0
