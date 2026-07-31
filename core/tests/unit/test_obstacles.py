"""障礙工事與工兵（WP-C2）：中性預設、型別語意、觸雷、破障/設障令。

[JCATS-A p.5–6] 公正性範例有一半在講障礙（雷區阻機動、斷橋改道、爆破需合理工時）；
p.13 工事構築須符合實際工時。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.adjudication.obstacles import (
    ENGINEER_MINE_STRIKE_MULT,
    NEUTRAL_EFFECT,
    ObstacleType,
    blocks_road,
    breach_ticks,
    effect_of,
    mine_strike_probability,
    obstacle_type_of,
    speed_multiplier,
)
from app.engine.obstacle_wiring import (
    apply_mine_suppression,
    is_engineer,
    roll_mine_strike,
    transit_speed_multiplier,
    typed,
)
from app.engine.rng import DeterministicRNG
from app.engine.suppression_wiring import SUPPRESSION_KEY
from app.movement.attrition import Obstacle, obstacle_from_feature, obstacles_at
from app.state.hot_state import InMemoryHotState

# ---- 中性預設：既有標註行為完全不變 ----
#
# ⚠ 這一組**打在接線層**。純函數的預設參數天生中性，會出事的是「接線怎麼把缺值翻成值」
# ——WP-C3 就是在那一層栽的（`mounted` 缺鍵被 `bool()` 收成 False，命中率無聲掉 20%）。


def _legacy_feature(kind: str = "OBSTACLE") -> dict[str, object]:
    """既有局的標註長這樣：**沒有 `attributes.obstacle_type`**。"""
    return {
        "id": "f1",
        "kind": kind,
        "geometry_type": "POINT",
        "geometry": [121.0, 24.0],
        "label": "舊障礙",
        "influence_radius_m": 300.0,
        "attributes": {},
    }


def test_a_legacy_feature_carries_no_obstacle_type() -> None:
    """**本卡最重要的保護**：既有標註載入後不帶型別、不帶破障旗標、密度 1.0。"""
    obs = obstacle_from_feature(_legacy_feature())
    assert obs is not None
    assert obs.obstacle_type is None and obs.breached is False and obs.density == 1.0


def test_legacy_features_are_filtered_out_before_any_adjudication() -> None:
    """中性是**結構性**的：`typed()` 在入口就把沒宣告型別的整個濾掉。

    濾掉 ⇒ 逐 tick 那條路徑拿到空 list ⇒ 一次幾何判定都不做、一次 RNG 都不抽。
    「係數剛好等於 1.0」是靠算出來的中性，這個是靠**根本沒進去**的中性。
    """
    obs = obstacle_from_feature(_legacy_feature())
    assert obs is not None
    assert typed([obs]) == []


def test_an_existing_session_never_touches_the_rng() -> None:
    """既有局不得多抽一次 RNG——串流有狀態，多抽一次會讓**後面所有**隨機結果位移。"""
    rng = DeterministicRNG(master_seed=20260730, stream_id="movement")
    before = rng.get_state()
    assert roll_mine_strike([], 5.0, rng, engineer=False) is None
    assert rng.get_state() == before


def test_no_obstacles_here_means_full_speed() -> None:
    assert transit_speed_multiplier([], engineer=False) == 1.0


def test_undeclared_type_is_fully_neutral() -> None:
    assert effect_of(None) is NEUTRAL_EFFECT
    assert NEUTRAL_EFFECT.speed_mult == 1.0
    assert NEUTRAL_EFFECT.mine_strike_p_per_km == 0.0
    assert speed_multiplier(None, is_engineer=False, breached=False) == 1.0
    assert mine_strike_probability(None, 10.0, is_engineer=False, breached=False) == 0.0


def test_unknown_type_string_is_undeclared_not_a_default_type() -> None:
    """認不得的字串必須回 None。回 MINEFIELD 之類的話，既有局每一片沒標型別的障礙
    都會突然開始炸人。"""
    for raw in ("TRENCH", "", None, 42, "minefield "):
        assert obstacle_type_of(raw) is None
    assert obstacle_type_of("MINEFIELD") is ObstacleType.MINEFIELD


# ---- 型別語意 ----


def test_wire_and_ditch_actually_block_while_a_minefield_does_not_slow_you() -> None:
    """規格明列非工兵通過鐵絲網/戰車壕 ×0.1。雷區**不減速**——它靠傷亡與心理效果。"""
    assert speed_multiplier(ObstacleType.WIRE, is_engineer=False, breached=False) == 0.1
    assert speed_multiplier(ObstacleType.TANK_DITCH, is_engineer=False, breached=False) == 0.1
    assert speed_multiplier(ObstacleType.MINEFIELD, is_engineer=False, breached=False) == 1.0


def test_engineers_and_breached_obstacles_do_not_slow_anyone() -> None:
    for otype in ObstacleType:
        assert speed_multiplier(otype, is_engineer=True, breached=False) == 1.0
        assert speed_multiplier(otype, is_engineer=False, breached=True) == 1.0


def test_engineers_halve_the_mine_strike_probability() -> None:
    """規格：工兵通過機率減半。"""
    plain = mine_strike_probability(ObstacleType.MINEFIELD, 1.0, is_engineer=False, breached=False)
    eng = mine_strike_probability(ObstacleType.MINEFIELD, 1.0, is_engineer=True, breached=False)
    assert eng == pytest.approx(plain * ENGINEER_MINE_STRIKE_MULT)
    assert 0.0 < eng < plain


def test_mine_probability_scales_with_distance_and_density_and_is_clamped() -> None:
    far = mine_strike_probability(ObstacleType.MINEFIELD, 2.0, is_engineer=False, breached=False)
    near = mine_strike_probability(ObstacleType.MINEFIELD, 1.0, is_engineer=False, breached=False)
    assert far > near
    dense = mine_strike_probability(
        ObstacleType.MINEFIELD, 1.0, is_engineer=False, breached=False, density=2.0
    )
    assert dense > near
    assert (
        mine_strike_probability(
            ObstacleType.MINEFIELD, 999.0, is_engineer=False, breached=False, density=99.0
        )
        == 1.0
    )


def test_a_breached_minefield_no_longer_detonates() -> None:
    assert (
        mine_strike_probability(ObstacleType.MINEFIELD, 10.0, is_engineer=False, breached=True)
        == 0.0
    )


def test_only_minefields_detonate() -> None:
    for otype in ObstacleType:
        p = mine_strike_probability(otype, 5.0, is_engineer=False, breached=False)
        assert (p > 0.0) is (otype is ObstacleType.MINEFIELD)


def test_breaching_costs_real_time_scaled_to_the_obstacle() -> None:
    """[JCATS-A p.13] 工事構築須符合實際工時——破障是要付出時間的決定，不是按鈕。"""
    assert breach_ticks(None) == 0
    assert all(breach_ticks(o) > 0 for o in ObstacleType)
    # 炸橋/修橋是其中最久的一項。
    assert breach_ticks(ObstacleType.BRIDGE_DEMO) == max(breach_ticks(o) for o in ObstacleType)


def test_only_a_demolished_bridge_kills_road_movement() -> None:
    assert blocks_road(ObstacleType.BRIDGE_DEMO, breached=False) is True
    assert blocks_road(ObstacleType.BRIDGE_DEMO, breached=True) is False
    assert blocks_road(ObstacleType.MINEFIELD, breached=False) is False


def test_stacked_obstacles_take_the_worst_not_the_product() -> None:
    """雷區＋鐵絲網不該比鐵絲網難走一個數量級——疊障礙以最難的那道為準。"""
    wire = Obstacle(
        feature_id="w",
        kind="OBSTACLE",
        geometry_type="POINT",
        coords=[(121.0, 24.0)],
        radius_m=300.0,
        obstacle_type="WIRE",
    )
    mines = Obstacle(
        feature_id="m",
        kind="OBSTACLE",
        geometry_type="POINT",
        coords=[(121.0, 24.0)],
        radius_m=300.0,
        obstacle_type="MINEFIELD",
    )
    assert transit_speed_multiplier([wire, mines], engineer=False) == 0.1


# ---- 幾何：站在裡面 vs 路徑穿過 ----


def _minefield(radius_m: float = 300.0, density: float = 1.0) -> Obstacle:
    return Obstacle(
        feature_id="mf",
        kind="OBSTACLE",
        geometry_type="POINT",
        coords=[(121.0, 24.0)],
        label="雷區",
        radius_m=radius_m,
        obstacle_type="MINEFIELD",
        density=density,
    )


def test_obstacles_at_reports_only_what_you_are_standing_in() -> None:
    mf = _minefield()
    assert obstacles_at((121.0, 24.0), [mf]) == [mf]
    assert obstacles_at((121.5, 24.5), [mf]) == []  # 幾十公里外


# ---- 觸雷 ----


def test_walking_through_a_dense_minefield_eventually_detonates() -> None:
    """機率性事件用固定 seed 驗**行為**：走一段夠長的路，遲早會踩到。"""
    rng = DeterministicRNG(master_seed=20260730, stream_id="movement")
    hits = sum(
        1
        for _ in range(200)
        if roll_mine_strike([_minefield()], 1.0, rng, engineer=False) is not None
    )
    assert 0 < hits < 200  # 既不是必炸也不是不炸


def test_engineers_get_hit_less_often_on_the_same_seed() -> None:
    """同 seed 對照——這是驗收條文「工兵通過機率減半」的可觀測形式。"""

    def _hits(engineer: bool) -> int:
        rng = DeterministicRNG(master_seed=20260730, stream_id="movement")
        return sum(
            1
            for _ in range(300)
            if roll_mine_strike([_minefield()], 1.0, rng, engineer=engineer) is not None
        )

    assert _hits(engineer=True) < _hits(engineer=False)


def test_a_mine_strike_suppresses_the_unit() -> None:
    """觸雷真正的價值是把縱隊釘在原地——扣血只是帳面。"""
    hot = InMemoryHotState()
    hot.put_unit("u1", {})
    assert apply_mine_suppression(hot, "u1") > 0.0
    assert (hot.get_unit("u1") or {})[SUPPRESSION_KEY] > 0.0


def test_suppression_from_mines_is_capped() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {})
    for _ in range(10):
        value = apply_mine_suppression(hot, "u1")
    assert value <= 1.0


# ---- 工兵身分 ----


def test_a_unit_without_the_marker_is_not_an_engineer() -> None:
    """缺值＝不是工兵。方向是刻意的：**多算成工兵才會讓雷區失效**。"""
    for attrs in ({}, None, {"platform_count": 12}, "ENGINEER", {"unit_kind": "INFANTRY"}):
        assert is_engineer(attrs) is False
    assert is_engineer({"unit_kind": "ENGINEER"}) is True
    assert is_engineer({"unit_kind": "engineer"}) is True


# ---- ENGINEER 令：預檢 ----


def _make_engineer(factory, world) -> str:  # type: ignore[no-untyped-def]
    """把 BLUE 單位標成工兵並回 id。"""
    from app.models.tables import TacticalUnit

    with factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        unit.attributes = {"unit_kind": "ENGINEER"}
        db.commit()
    return world.blue_unit_id


def _emplace_req(unit_id: str, *, lat: float = 23.75, lng: float = 121.25):  # type: ignore[no-untyped-def]
    from app.orders.schemas import OrderRequest, OrderType

    return OrderRequest(
        unit_id=unit_id,
        order_type=OrderType.ENGINEER,
        payload={"action": "EMPLACE", "obstacle_type": "MINEFIELD", "lat": lat, "lng": lng},
    )


def test_a_non_engineer_cannot_do_obstacle_work(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**在下令時就擋**。工兵令要花數十分鐘，等到收工才發現「這單位不是工兵」，
    那段時間已經回不來了。"""
    from _order_fakes import FakeGateway, seed_world

    from app.errors import PrecheckFailedError
    from app.orders.service import OrderService

    world = seed_world(session_factory)
    db = session_factory()
    with pytest.raises(PrecheckFailedError, match="工兵"):
        OrderService(db, FakeGateway()).submit(
            world.session_id, _emplace_req(world.blue_unit_id), world.blue_issuer_id
        )
    db.close()


def test_an_engineer_far_from_the_worksite_is_rejected(session_factory) -> None:  # type: ignore[no-untyped-def]
    """半徑外的「遙控破障」是一個按鈕，不是一次工兵作業。"""
    from _order_fakes import FakeGateway, seed_world

    from app.errors import PrecheckFailedError
    from app.orders.service import OrderService

    world = seed_world(session_factory)
    unit_id = _make_engineer(session_factory, world)
    db = session_factory()
    with pytest.raises(PrecheckFailedError, match="距作業點"):
        OrderService(db, FakeGateway()).submit(
            world.session_id, _emplace_req(unit_id, lat=24.5, lng=122.0), world.blue_issuer_id
        )
    db.close()


def test_breach_without_a_feature_id_is_rejected_at_submit() -> None:
    """形狀錯的令若等到 pre_tick 才發現，就只能靜靜作廢，下令者永遠不知道為什麼沒動。"""
    from pydantic import ValidationError

    from app.orders.schemas import EngineerPayload

    with pytest.raises(ValidationError):
        EngineerPayload(action="BREACH")
    with pytest.raises(ValidationError):
        EngineerPayload(action="EMPLACE", obstacle_type="MINEFIELD")  # 缺座標
    assert EngineerPayload(action="BREACH", feature_id="f1").feature_id == "f1"


# ---- ENGINEER 令：執行（工時） ----


def test_emplacing_an_obstacle_takes_time_and_then_creates_it(session_factory) -> None:  # type: ignore[no-untyped-def]
    """[JCATS-A p.13]：工事構築須符合實際工時。**完工那一刻才改地圖**。"""
    from _order_fakes import FakeGateway, seed_world

    from app.engine.obstacle_wiring import drain_engineer_orders
    from app.models.tables import MapFeature
    from app.orders.service import OrderService

    world = seed_world(session_factory)
    unit_id = _make_engineer(session_factory, world)
    db = session_factory()
    resp = OrderService(db, FakeGateway()).submit(
        world.session_id, _emplace_req(unit_id), world.blue_issuer_id
    )
    assert resp.status.value == "VALIDATED"

    started = drain_engineer_orders(db, world.session_id, tick=0)
    assert [e.event_type for e in started] == ["ENGINEER_WORK_STARTED"]
    eta = started[0].detail["eta_tick"]
    assert eta == breach_ticks(ObstacleType.MINEFIELD)

    # 施工中：地圖上什麼都還沒有。
    assert drain_engineer_orders(db, world.session_id, tick=eta - 1) == []
    assert db.query(MapFeature).count() == 0

    done = drain_engineer_orders(db, world.session_id, tick=eta)
    assert [e.event_type for e in done] == ["OBSTACLE_EMPLACED"]
    feature = db.query(MapFeature).one()
    assert feature.attributes["obstacle_type"] == "MINEFIELD"
    assert feature.owner_faction == "BLUE"
    db.close()


def test_breaching_marks_the_feature_and_disarms_it(session_factory) -> None:  # type: ignore[no-untyped-def]
    """破障的可觀測結果：`attributes.breached` 落庫，且該片雷區從此不再引爆。"""
    from _order_fakes import FakeGateway, seed_world

    from app.engine.obstacle_wiring import drain_engineer_orders
    from app.models.tables import MapFeature
    from app.orders.schemas import OrderRequest, OrderType
    from app.orders.service import OrderService

    world = seed_world(session_factory)
    unit_id = _make_engineer(session_factory, world)
    db = session_factory()
    feature = MapFeature(
        session_id=world.session_id,
        kind="OBSTACLE",
        geometry_type="POINT",
        geometry=[121.25, 23.75],
        owner_faction="RED",
        label="敵雷區",
        influence_radius_m=300.0,
        attributes={"obstacle_type": "MINEFIELD"},
    )
    db.add(feature)
    db.commit()

    resp = OrderService(db, FakeGateway()).submit(
        world.session_id,
        OrderRequest(
            unit_id=unit_id,
            order_type=OrderType.ENGINEER,
            payload={"action": "BREACH", "feature_id": feature.id},
        ),
        world.blue_issuer_id,
    )
    assert resp.status.value == "VALIDATED"
    started = drain_engineer_orders(db, world.session_id, tick=0)
    eta = started[0].detail["eta_tick"]
    assert eta == breach_ticks(ObstacleType.MINEFIELD)  # 工時看**標的**的型別
    done = drain_engineer_orders(db, world.session_id, tick=eta)
    assert [e.event_type for e in done] == ["OBSTACLE_BREACHED"]
    db.refresh(feature)
    assert feature.attributes["breached"] is True

    obs = obstacle_from_feature(
        {
            "id": feature.id,
            "kind": feature.kind,
            "geometry_type": feature.geometry_type,
            "geometry": feature.geometry,
            "label": feature.label,
            "influence_radius_m": feature.influence_radius_m,
            "attributes": feature.attributes,
        }
    )
    assert obs is not None and obs.breached is True
    rng = DeterministicRNG(master_seed=20260730, stream_id="movement")
    assert all(roll_mine_strike([obs], 5.0, rng, engineer=False) is None for _ in range(50))
    db.close()


def test_a_deleted_target_aborts_the_work_instead_of_reporting_success(session_factory) -> None:  # type: ignore[no-untyped-def]
    """標的在施工期間被刪 → CANCELLED + ENGINEER_WORK_ABORTED。

    判 COMPLETED 會讓 AAR 看起來像「破障成功」——那是最糟的一種假訊息。
    """
    from _order_fakes import FakeGateway, seed_world

    from app.engine.obstacle_wiring import drain_engineer_orders
    from app.models.tables import MapFeature, Order
    from app.orders.schemas import OrderRequest, OrderType
    from app.orders.service import OrderService

    world = seed_world(session_factory)
    unit_id = _make_engineer(session_factory, world)
    db = session_factory()
    feature = MapFeature(
        session_id=world.session_id,
        kind="OBSTACLE",
        geometry_type="POINT",
        geometry=[121.25, 23.75],
        owner_faction="RED",
        influence_radius_m=300.0,
        attributes={"obstacle_type": "WIRE"},
    )
    db.add(feature)
    db.commit()
    resp = OrderService(db, FakeGateway()).submit(
        world.session_id,
        OrderRequest(
            unit_id=unit_id,
            order_type=OrderType.ENGINEER,
            payload={"action": "BREACH", "feature_id": feature.id},
        ),
        world.blue_issuer_id,
    )
    eta = drain_engineer_orders(db, world.session_id, tick=0)[0].detail["eta_tick"]
    db.delete(feature)
    db.commit()
    events = drain_engineer_orders(db, world.session_id, tick=eta)
    assert [e.event_type for e in events] == ["ENGINEER_WORK_ABORTED"]
    assert db.get(Order, resp.id).status.value == "CANCELLED"
    db.close()


# ---- 破障工時的時間尺度（WP-C1 那個 bug 的未修兄弟）----


def _breach_req(unit_id: str, feature_id: str):  # type: ignore[no-untyped-def]
    from app.orders.schemas import OrderRequest, OrderType

    return OrderRequest(
        unit_id=unit_id,
        order_type=OrderType.ENGINEER,
        payload={"action": "BREACH", "feature_id": feature_id},
    )


def _seed_minefield_row(db, session_id: str) -> str:  # type: ignore[no-untyped-def]
    """在工兵腳下擺一片敵雷區，回 feature id。"""
    from app.models.tables import MapFeature

    feature = MapFeature(
        session_id=session_id,
        kind="OBSTACLE",
        geometry_type="POINT",
        geometry=[121.25, 23.75],
        owner_faction="RED",
        label="敵雷區",
        influence_radius_m=300.0,
        attributes={"obstacle_type": "MINEFIELD"},
    )
    db.add(feature)
    db.commit()
    return feature.id


def test_breaching_costs_the_same_simulated_time_at_any_tick_rate(session_factory) -> None:  # type: ignore[no-untyped-def]
    """同一片雷區，1 分鐘/tick 與 1 秒/tick 兩張想定下，破障花掉的**模擬時間相同**。

    這是 commit d67fe61（壓制/掘壕）那個 bug 的未修兄弟：工時表寫的是 45，
    註解宣稱「1 tick = 1 分鐘」，但 `tick_rate_ms` 是想定可調的，而官方 demo 與
    使用者的想定都寫 1000。於是 1 秒/tick 的局裡破一片雷區只要 45 **秒**——
    「要付出時間的決定」退化成一個按鈕。

    ⚠ 刻意**跑完整條令的管線**（submit → drain 起工 → drain 完工）而不是只比
    `breach_ticks()` 的回傳值：本 repo 的招牌病正是「純函數對了、接線沒接上」，
    而這裡真正漏掉參數的就是接線（`drain_engineer_orders` 的簽名裡根本沒有 tick_rate_ms）。
    """
    from _order_fakes import FakeGateway, seed_world

    from app.adjudication.obstacles import breach_minutes
    from app.engine.obstacle_wiring import drain_engineer_orders
    from app.models.tables import MapFeature
    from app.orders.service import OrderService

    world = seed_world(session_factory)
    unit_id = _make_engineer(session_factory, world)
    db = session_factory()
    service = OrderService(db, FakeGateway())

    def breach_once(start_tick: int, tick_rate_ms: int) -> int:
        """下一道 BREACH 令並跑到完工。回實際花掉的 tick 數。"""
        feature_id = _seed_minefield_row(db, world.session_id)
        assert (
            service.submit(
                world.session_id, _breach_req(unit_id, feature_id), world.blue_issuer_id
            ).status.value
            == "VALIDATED"
        )
        started = drain_engineer_orders(db, world.session_id, start_tick, tick_rate_ms)
        assert [e.event_type for e in started] == ["ENGINEER_WORK_STARTED"]
        eta = int(started[0].detail["eta_tick"])
        # 前一個 tick 還在施工——地圖上那片雷區仍然是雷區。
        assert drain_engineer_orders(db, world.session_id, eta - 1, tick_rate_ms) == []
        assert db.get(MapFeature, feature_id).attributes.get("breached") is not True
        done = drain_engineer_orders(db, world.session_id, eta, tick_rate_ms)
        assert [e.event_type for e in done] == ["OBSTACLE_BREACHED"]
        return eta - start_tick

    slow = breach_once(start_tick=0, tick_rate_ms=60_000)  # 1 分鐘/tick
    fast = breach_once(start_tick=slow, tick_rate_ms=1_000)  # 1 秒/tick（demo 與使用者想定）

    minutes = breach_minutes(ObstacleType.MINEFIELD)
    assert slow == minutes  # 舊行為：1 tick = 1 分鐘 → 位元不變
    assert slow * 60_000 == fast * 1_000 == minutes * 60_000  # 模擬時間相同
    assert fast > slow  # tick 變短 → tick 數必然變多（沒換算的話兩者會相等）
    db.close()


def test_work_already_in_progress_keeps_its_original_eta(session_factory) -> None:  # type: ignore[no-untyped-def]
    """升級當下正在施工的令**沿用舊的完工 tick**，不因單位換算而突然變長。

    真實情境：一局以舊碼（工時＝表上數字，等同 1 分鐘/tick）跑著，工兵已經動工，
    `_work_until_tick` 早就寫進 payload；runner 重啟後開始傳 `tick_rate_ms=1000`。
    若這時重算，那支挖到一半的工兵會突然多出 60 倍的工時。
    """
    from _order_fakes import FakeGateway, seed_world

    from app.adjudication.obstacles import breach_minutes
    from app.engine.obstacle_wiring import drain_engineer_orders
    from app.models.tables import Order
    from app.orders.service import OrderService

    world = seed_world(session_factory)
    unit_id = _make_engineer(session_factory, world)
    db = session_factory()
    feature_id = _seed_minefield_row(db, world.session_id)
    resp = OrderService(db, FakeGateway()).submit(
        world.session_id, _breach_req(unit_id, feature_id), world.blue_issuer_id
    )
    # 舊碼起的工：等同 tick_rate_ms=60000。
    started = drain_engineer_orders(db, world.session_id, tick=0)
    old_eta = int(started[0].detail["eta_tick"])
    assert old_eta == breach_minutes(ObstacleType.MINEFIELD)
    assert db.get(Order, resp.id).payload["_work_until_tick"] == old_eta

    # 新碼接手（1 秒/tick）：完工時刻不動。重算的話會變成 45 × 60 = 2700。
    assert drain_engineer_orders(db, world.session_id, old_eta - 1, 1_000) == []
    done = drain_engineer_orders(db, world.session_id, old_eta, 1_000)
    assert [e.event_type for e in done] == ["OBSTACLE_BREACHED"]
    db.close()


def test_a_tick_longer_than_the_job_still_costs_one_tick(session_factory) -> None:  # type: ignore[no-untyped-def]
    """tick 比工時還長 → 仍要**至少一個 tick**。

    破障是「工作」不是「宣告」：一張 1 小時/tick 的想定裡，鐵絲網（20 分鐘）
    若換算成 0 就會在下令的同一個 tick 完工——那正好又變回一個按鈕。
    """
    from app.adjudication.obstacles import breach_ticks

    assert breach_ticks(ObstacleType.WIRE, 3_600_000) == 1
    assert breach_ticks(None, 3_600_000) == 0  # 沒東西可破仍是 0


def test_road_is_cut_only_for_an_unbreached_bridge_demo() -> None:
    """`road_is_cut` 是 `blocks_road` 的**消費者**——它在本卡之前完全沒有呼叫端。

    斷橋刻意不是「減速倍率」：炸斷的橋不會讓你走得慢，它讓你**不能再沿路走**，
    得繞路或涉水。所以它的效果只能接在 movement 的道路加速分支，
    不在障礙通過倍率那一段——這正是它寫好卻一直沒接上的原因。
    """
    from app.engine.obstacle_wiring import road_is_cut
    from app.movement.attrition import Obstacle

    def ob(kind: str, breached: bool = False) -> Obstacle:
        return Obstacle(
            feature_id="f",
            kind="OBSTACLE",
            geometry_type="LINE",
            coords=((121.0, 24.0), (121.001, 24.0)),
            obstacle_type=kind,
            breached=breached,
        )

    assert road_is_cut([ob("BRIDGE_DEMO")]) is True
    # 工兵架好便橋 → 道路恢復
    assert road_is_cut([ob("BRIDGE_DEMO", breached=True)]) is False
    # 其他障礙**不**阻斷道路（它們走 speed_multiplier 那條路）
    assert road_is_cut([ob("MINEFIELD"), ob("WIRE"), ob("TANK_DITCH")]) is False
    # 沒有障礙 / 未宣告型別 → 中性
    assert road_is_cut([]) is False


def test_the_production_wiring_really_passes_the_session_tick_rate(
    session_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**守的是最外層那道接線，不是純函數。**

    這張卡在修的 bug 就是「純函數對了、接線沒把 tick_rate_ms 傳下去」。
    而其他測試都是自己呼叫 `drain_engineer_orders(db, sid, tick, tick_rate_ms)`
    ——測試自己把參數傳進去，於是**把第 4 個引數從 `sim_runtime._engineer_tick`
    拿掉，全套 2197 條測試一條都不紅**。同一個形狀的洞在同一張卡裡又開了一次。

    這條打在 `sim_runtime._engineer_tick`：攔住 `drain_engineer_orders`，
    斷言它收到的 tick_rate_ms 就是呼叫端給的那一個。
    """
    from app import sim_runtime

    seen: dict[str, object] = {}

    def spy(db: object, session_id: str, tick: int, tick_rate_ms: int) -> list[object]:
        seen.update(session_id=session_id, tick=tick, tick_rate_ms=tick_rate_ms)
        return []

    monkeypatch.setattr(sim_runtime, "drain_engineer_orders", spy)
    sim_runtime._engineer_tick(session_factory, "sid-x", 7, 1_000)

    assert seen == {"session_id": "sid-x", "tick": 7, "tick_rate_ms": 1_000}, (
        f"接線沒把該局的 tick_rate_ms 傳下去，實得 {seen}——破障工時會退回寫死的 1 分鐘/tick"
    )
