"""物理預檢測試（O3.1，步驟 [2]）：MOVE 可達性 / ENGAGE 視線，用假 gateway。"""

from __future__ import annotations

import pytest
from _order_fakes import DownGateway, FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.errors import TerrainUnavailableError
from app.models.tables import TacticalUnit
from app.orders.precheck import precheck_error_code, run_precheck
from app.orders.schemas import EngagePayload, MovePayload, OrderType
from app.orders.validator import ValidatedOrder


def _validated_move(db: Session, unit_id: str, to_h3: str = "8a2a1072b59ffff") -> ValidatedOrder:
    unit = db.get(TacticalUnit, unit_id)
    assert unit is not None
    return ValidatedOrder(
        unit=unit,
        order_type=OrderType.MOVE,
        payload=MovePayload(to_h3=to_h3, mobility_profile="FOOT"),
    )


def _validated_engage(db: Session, unit_id: str, target_id: str) -> ValidatedOrder:
    unit = db.get(TacticalUnit, unit_id)
    assert unit is not None
    return ValidatedOrder(
        unit=unit,
        order_type=OrderType.ENGAGE,
        payload=EngagePayload(target_unit_id=target_id),
    )


def test_move_reachable_feasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    gw = FakeGateway(reachable=True)
    with session_factory() as db:
        result = run_precheck(db, _validated_move(db, world.blue_unit_id), gw)
    assert result.feasible
    assert result.checks[0].name == "reachability"
    assert len(gw.path_calls) == 1  # 從單位座標算出的 from_h3 → to_h3


def test_move_unreachable_infeasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        gw = FakeGateway(reachable=False)
        result = run_precheck(db, _validated_move(db, world.blue_unit_id), gw)
    assert not result.feasible
    assert precheck_error_code(result) == "ORDER_UNREACHABLE"


def test_move_without_position_infeasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        unit.current_lat = None
        unit.current_lng = None
        db.commit()
        result = run_precheck(db, _validated_move(db, world.blue_unit_id), FakeGateway())
    assert not result.feasible
    assert precheck_error_code(result) == "ORDER_UNIT_NO_POSITION"


def test_engage_los_clear_feasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    gw = FakeGateway(visible=True)
    with session_factory() as db:
        result = run_precheck(db, _validated_engage(db, world.blue_unit_id, world.red_unit_id), gw)
    assert result.feasible
    assert result.checks[0].name == "line_of_sight"
    assert len(gw.los_calls) == 1


def test_engage_blocked_los_infeasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        result = run_precheck(
            db,
            _validated_engage(db, world.blue_unit_id, world.red_unit_id),
            FakeGateway(visible=False),
        )
    assert not result.feasible
    assert precheck_error_code(result) == "ORDER_NO_LOS"
    # 可解釋：說明含「地形遮蔽」+ 遮蔽點座標 + 高出視線的公尺數（#2 使用者回報「看不出原因」）。
    detail = result.checks[0].detail or ""
    assert "地形遮蔽" in detail and "23.7000" in detail and "5 m" in detail


def _give_missile(
    db: Session, unit_id: str, *, maneuverable: bool, apex_ratio: float = 0.03
) -> None:
    from app.models.tables import EquipmentInstance, EquipmentTemplate

    tmpl = EquipmentTemplate(
        name="MSL",
        category="MISSILE",
        base_stats={
            "max_range_m": 10000,
            "ph_by_range_band": [[500, 0.9], [10000, 0.85]],
            "damage_by_armor_class": {"ARMOR": 100},
            "ammo_types": ["X"],
            "missile_kind": "CRUISE" if maneuverable else "BALLISTIC",
            "maneuverable": maneuverable,
            "apex_ratio": apex_ratio,
        },
    )
    db.add(tmpl)
    db.flush()
    db.add(EquipmentInstance(template_id=tmpl.id, owner_id=unit_id, current_state={"ammo": 4}))
    db.commit()


def _add_tall_building(db: Session, session_id: str) -> None:
    from app.models.tables import MapFeature

    # 藍(23.75,121.25)→紅(23.76,121.26) 中點附近的 400m 高建築。
    db.add(
        MapFeature(
            session_id=session_id,
            kind="BUILDING",
            geometry_type="POLYGON",
            geometry=[[121.253, 23.753], [121.257, 23.753], [121.257, 23.757], [121.253, 23.757]],
            owner_faction="WHITE_CELL",
            attributes={"height_m": 400.0},
        )
    )
    db.commit()


def test_ballistic_missile_blocked_by_tall_obstacle(session_factory: sessionmaker[Session]) -> None:
    # 彈道飛彈（低伸拋物線）+ 高建築擋在路徑上 → 拋物線被障礙阻隔（即使無 LOS 也走軌跡判定）。
    world = seed_world(session_factory)
    with session_factory() as db:
        _give_missile(db, world.blue_unit_id, maneuverable=False, apex_ratio=0.02)
        _add_tall_building(db, world.session_id)
        result = run_precheck(
            db,
            _validated_engage(db, world.blue_unit_id, world.red_unit_id),
            FakeGateway(visible=False),
        )
    assert not result.feasible
    assert result.checks[0].name == "trajectory"
    assert "障礙" in (result.checks[0].detail or "")


def test_cruise_missile_ignores_obstacle_and_los(session_factory: sessionmaker[Session]) -> None:
    # 巡弋飛彈（可變軌）：即使無 LOS + 有高建築，仍只判射程 → 可行。
    world = seed_world(session_factory)
    with session_factory() as db:
        _give_missile(db, world.blue_unit_id, maneuverable=True)
        _add_tall_building(db, world.session_id)
        result = run_precheck(
            db,
            _validated_engage(db, world.blue_unit_id, world.red_unit_id),
            FakeGateway(visible=False),
        )
    assert result.feasible
    assert result.checks[0].name == "trajectory" and result.checks[0].passed


def test_engage_unknown_target_infeasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        result = run_precheck(db, _validated_engage(db, world.blue_unit_id, "ghost"), FakeGateway())
    assert not result.feasible
    assert precheck_error_code(result) == "ORDER_TARGET_NOT_FOUND"


def test_terrain_down_propagates(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(TerrainUnavailableError):
        run_precheck(db, _validated_move(db, world.blue_unit_id), DownGateway())


def test_non_physical_order_feasible_no_checks(session_factory: sessionmaker[Session]) -> None:
    # RECON 等目前無物理檢查 → feasible=True、checks 空（O3.x 再補）
    world = seed_world(session_factory)
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        validated = ValidatedOrder(unit=unit, order_type=OrderType.RECON, payload={"area": "n"})
        result = run_precheck(db, validated, FakeGateway())
    assert result.feasible
    assert result.checks == []


def test_engage_own_faction_rejected_by_roe(session_factory: sessionmaker[Session]) -> None:
    """O6.8：ENGAGE 只能打敵對陣營——BLUE 打 BLUE（己方＝ALLIED）→ ROE 攔（§12.1）。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        # blue_unit 打自己（同陣營），LOS gateway 恆通——應被 ROE 先擋下
        result = run_precheck(
            db, _validated_engage(db, world.blue_unit_id, world.blue_unit_id), FakeGateway()
        )
        assert not result.feasible
        assert precheck_error_code(result) == "ORDER_ROE_VIOLATION"


def test_engage_hostile_default_passes_roe(session_factory: sessionmaker[Session]) -> None:
    """預設全 HOSTILE：BLUE 打 RED 通過 ROE（沿用既有行為）。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        result = run_precheck(
            db, _validated_engage(db, world.blue_unit_id, world.red_unit_id), FakeGateway()
        )
        assert result.feasible  # ROE + LOS 皆過
