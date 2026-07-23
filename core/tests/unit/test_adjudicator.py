"""Kernel 裁決接線（O3.6）：EngageOrderSource drain + EngagementAdjudicator 落地。

真實流程：drain（VALIDATED→EXECUTING）→ resolve（EXECUTING→COMPLETED）。
"""

from __future__ import annotations

import pytest
from _order_fakes import FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.adjudication.adjudicator import EngagementAdjudicator, EngageOrderSource
from app.adjudication.effectiveness import effectiveness_pct
from app.adjudication.engagement import EnvSnapshot
from app.adjudication.weapon import WeaponProfile
from app.engine.clock import SimTime
from app.engine.rng import DeterministicRNG
from app.models.enums import OrderStatus
from app.models.tables import Order
from app.orders.schemas import OrderRequest, OrderType
from app.orders.service import OrderService
from app.state.hot_state import InMemoryHotState

_WEAPON = WeaponProfile.from_base_stats(
    {
        "max_range_m": 5000,
        "ph_by_range_band": [[100, 1.0], [5000, 1.0]],
        "damage_by_armor_class": {"INFANTRY": 40},
        "ammo_types": ["X"],
    }
)


def _submit_engage(db: Session, world) -> str:  # type: ignore[no-untyped-def]
    return (
        OrderService(db, FakeGateway(visible=True))
        .submit(
            world.session_id,
            OrderRequest(
                unit_id=world.blue_unit_id,
                order_type=OrderType.ENGAGE,
                payload={"target_unit_id": world.red_unit_id},
            ),
            world.blue_issuer_id,
        )
        .id
    )


def _adjudicator(db: Session, hot: InMemoryHotState) -> EngagementAdjudicator:
    return EngagementAdjudicator(
        db,
        hot,
        DeterministicRNG(1, "adjudication"),
        lambda _cmd: _WEAPON,
        lambda _s, _t, _indirect=False: EnvSnapshot(range_m=500.0, los_clear=True),
    )


async def test_engage_source_drains_and_transitions(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        oid = _submit_engage(db, world)
        cmds = await EngageOrderSource(db, world.session_id).drain()
        assert len(cmds) == 1
        assert cmds[0].shooter_id == world.blue_unit_id
        assert cmds[0].target_id == world.red_unit_id
        assert db.get(Order, oid).status is OrderStatus.EXECUTING  # type: ignore[union-attr]
        # 已非 VALIDATED → 再次 drain 為空
        assert await EngageOrderSource(db, world.session_id).drain() == []


async def test_hit_applies_damage_and_completes(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        oid = _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(world.blue_unit_id, {"ammo": 5})
        hot.put_unit(world.red_unit_id, {"health": 100.0, "armor_class": "INFANTRY"})
        events = _adjudicator(db, hot).resolve(cmd, SimTime(0, 0))
        # 真實化交戰：strength 為權威量（無 pk → 期望傷亡 40/100=0.4，單體 cp=100 → loss 40）；
        # health 改為由戰力比 0.60 經效能曲線導出（非 flat 100−40）。
        assert hot.get_unit(world.red_unit_id)["strength"] == pytest.approx(60.0)
        assert hot.get_unit(world.red_unit_id)["health"] == pytest.approx(effectiveness_pct(0.60))
        assert hot.get_unit(world.blue_unit_id)["ammo"] == 4  # 彈藥 −1
        assert events[0].event_type == "ENGAGEMENT_RESOLVED"
        assert db.get(Order, oid).status is OrderStatus.COMPLETED  # type: ignore[union-attr]


async def test_battalion_uses_aggregate_lanchester(
    session_factory: sessionmaker[Session],
) -> None:
    # #33a：射手為營級 → 走聚合 Lanchester，雙方同時消耗（AGGREGATE_ENGAGEMENT_RESOLVED）。
    from app.models.enums import UnitLevel
    from app.models.tables import TacticalUnit

    world = seed_world(session_factory)
    with session_factory() as db:
        blue = db.get(TacticalUnit, world.blue_unit_id)
        assert blue is not None
        blue.unit_level = UnitLevel.BATTALION  # 提升到聚合門檻
        db.commit()
        oid = _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(
            world.blue_unit_id, {"ammo": 999, "strength": 100.0, "authorized_strength": 100.0}
        )
        hot.put_unit(
            world.red_unit_id,
            {"strength": 100.0, "authorized_strength": 100.0, "armor_class": "INFANTRY"},
        )
        events = _adjudicator(db, hot).resolve(cmd, SimTime(0, 0))
        assert events and events[0].event_type == "AGGREGATE_ENGAGEMENT_RESOLVED"
        # Lanchester 雙方同時消耗：目標與射手戰力皆下降。
        assert hot.get_unit(world.red_unit_id)["strength"] < 100.0
        assert hot.get_unit(world.blue_unit_id)["strength"] < 100.0
        assert db.get(Order, oid).status is OrderStatus.COMPLETED  # type: ignore[union-attr]


async def test_rejected_no_ammo_no_damage(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        oid = _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(world.blue_unit_id, {"ammo": 0})  # 無彈藥
        hot.put_unit(world.red_unit_id, {"health": 100.0, "armor_class": "INFANTRY"})
        events = _adjudicator(db, hot).resolve(cmd, SimTime(0, 0))
        assert hot.get_unit(world.red_unit_id)["health"] == 100.0  # 無戰損
        assert hot.get_unit(world.blue_unit_id)["ammo"] == 0  # 未消耗
        assert events[0].ai_decision["status"] == "REJECTED"
        assert db.get(Order, oid).status is OrderStatus.COMPLETED  # type: ignore[union-attr]


async def test_missing_unit_completes_without_event(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        oid = _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()  # 熱狀態空 → 找不到 shooter/target
        events = _adjudicator(db, hot).resolve(cmd, SimTime(0, 0))
        assert events == []
        assert db.get(Order, oid).status is OrderStatus.COMPLETED  # type: ignore[union-attr]
