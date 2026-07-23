"""Kernel 裁決接線（O3.6）：EngageOrderSource drain + EngagementAdjudicator 落地。

真實流程：drain（VALIDATED→EXECUTING）→ resolve（EXECUTING→COMPLETED）。
"""

from __future__ import annotations

import pytest
from _order_fakes import FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.adjudication.adjudicator import EngagementAdjudicator, EngageOrderSource
from app.adjudication.combined import CombinedWeapon
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


# ── SPEC_EXTEND P2：聯合兵種加總 gating ─────────────────────────────────────

_RIFLE_C = WeaponProfile.from_base_stats(
    {
        "max_range_m": 600,
        "ph_by_range_band": [[100, 0.8], [600, 0.3]],
        "damage_by_armor_class": {"INFANTRY": 35},
        "pk_by_armor_class": {"INFANTRY": 0.5},
        "ammo_types": ["A556"],
    }
)
_ATGM_C = WeaponProfile.from_base_stats(
    {
        "max_range_m": 4000,
        "ph_by_range_band": [[500, 0.9], [4000, 0.6]],
        "damage_by_armor_class": {"ARMOR": 200},
        "pk_by_armor_class": {"ARMOR": 0.8},
        "ammo_types": ["ATGM"],
    }
)


def _adjudicator_combined(db: Session, hot: InMemoryHotState, combined) -> EngagementAdjudicator:  # type: ignore[no-untyped-def]
    return EngagementAdjudicator(
        db,
        hot,
        DeterministicRNG(1, "adjudication"),
        lambda _cmd: _RIFLE_C,
        lambda _s, _t, _indirect=False: EnvSnapshot(range_m=400.0, los_clear=True),
        combined_weapons_for=combined,
    )


async def test_combined_path_engages_with_weapon_mix(
    session_factory: sessionmaker[Session],
) -> None:
    # ≥2 武器系統 → 聯合兵種加總（mode=COMBINED）：逐武器扣熱狀態 ammo_by_weapon，目標戰力下降。
    world = seed_world(session_factory)
    with session_factory() as db:
        oid = _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(
            world.blue_unit_id,
            {
                "ammo": 108,
                "ammo_by_weapon": {"w-rifle": 100, "w-atgm": 8},
                "strength": 100.0,
                "authorized_strength": 100.0,
            },
        )
        hot.put_unit(
            world.red_unit_id,
            {
                "health": 100.0,
                "armor_class": "INFANTRY",
                "strength": 100.0,
                "authorized_strength": 100.0,
                "platform_count": 10,
            },
        )
        weapons = [
            CombinedWeapon("w-rifle", _RIFLE_C, quantity=7, ammo=100),
            CombinedWeapon("w-atgm", _ATGM_C, quantity=2, ammo=8),
        ]
        events = _adjudicator_combined(db, hot, lambda _sid: weapons).resolve(cmd, SimTime(0, 0))
        assert events[0].ai_decision["mode"] == "COMBINED"
        abw = hot.get_unit(world.blue_unit_id)["ammo_by_weapon"]
        assert abw["w-rifle"] < 100  # 步槍消耗（打步兵有效）
        assert hot.get_unit(world.red_unit_id)["strength"] < 100.0  # 步槍造成戰損
        assert db.get(Order, oid).status is OrderStatus.COMPLETED  # type: ignore[union-attr]


async def test_drain_parses_fire_policy(session_factory: sessionmaker[Session]) -> None:
    # P3：ENGAGE payload.fire_policy → EngageCommand.fire_policy。
    world = seed_world(session_factory)
    with session_factory() as db:
        OrderService(db, FakeGateway(visible=True)).submit(
            world.session_id,
            OrderRequest(
                unit_id=world.blue_unit_id,
                order_type=OrderType.ENGAGE,
                payload={
                    "target_unit_id": world.red_unit_id,
                    "fire_policy": "SMALL_ARMS_ONLY",
                },
            ),
            world.blue_issuer_id,
        )
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        assert cmd.fire_policy == "SMALL_ARMS_ONLY"
        assert cmd.weapon_template_id is None


async def test_explicit_weapon_skips_combined_path(
    session_factory: sessionmaker[Session],
) -> None:
    # P3：指定 weapon_id（操作員選單一武器）→ 即使 ≥2 武器也走既有單武器路徑（非 COMBINED）。
    world = seed_world(session_factory)
    with session_factory() as db:
        OrderService(db, FakeGateway(visible=True)).submit(
            world.session_id,
            OrderRequest(
                unit_id=world.blue_unit_id,
                order_type=OrderType.ENGAGE,
                payload={"target_unit_id": world.red_unit_id, "weapon_id": "w-rifle"},
            ),
            world.blue_issuer_id,
        )
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(world.blue_unit_id, {"ammo": 5, "ammo_by_weapon": {"w-rifle": 5, "w-atgm": 8}})
        hot.put_unit(world.red_unit_id, {"health": 100.0, "armor_class": "INFANTRY"})
        weapons = [
            CombinedWeapon("w-rifle", _RIFLE_C, quantity=7, ammo=5),
            CombinedWeapon("w-atgm", _ATGM_C, quantity=2, ammo=8),
        ]
        events = _adjudicator_combined(db, hot, lambda _sid: weapons).resolve(cmd, SimTime(0, 0))
        assert events[0].ai_decision.get("mode") != "COMBINED"  # 指定武器 → 走單武器路徑


async def test_single_weapon_unit_skips_combined_path(
    session_factory: sessionmaker[Session],
) -> None:
    # 單武器單位（清單長度 1）→ gating 不觸發 combined，落回既有單/齊射路徑（golden 不變）。
    world = seed_world(session_factory)
    with session_factory() as db:
        oid = _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(world.blue_unit_id, {"ammo": 5})
        hot.put_unit(world.red_unit_id, {"health": 100.0, "armor_class": "INFANTRY"})
        one = [CombinedWeapon("w-rifle", _RIFLE_C, quantity=1, ammo=5)]
        events = _adjudicator_combined(db, hot, lambda _sid: one).resolve(cmd, SimTime(0, 0))
        assert events[0].ai_decision.get("mode") != "COMBINED"  # 走既有單發路徑
        assert hot.get_unit(world.blue_unit_id)["ammo"] == 4  # 純量 ammo −1（單發）
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
