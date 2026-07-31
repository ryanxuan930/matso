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
        # 打了就要耗彈。**過去這條路徑從不扣**——`_apply` 只掛在平台級上。
        assert hot.get_unit(world.blue_unit_id)["ammo"] < 999


async def _aggregate_engage(
    session_factory: sessionmaker[Session],
    shooter_state: dict,  # type: ignore[type-arg]
    *,
    suppress: object = None,
) -> tuple[object, InMemoryHotState, str, str]:
    """把射手升到營級並打一次聚合交戰。回 (events, hot, shooter_id, target_id)。"""
    from app.models.enums import UnitLevel
    from app.models.tables import TacticalUnit

    world = seed_world(session_factory)
    with session_factory() as db:
        blue = db.get(TacticalUnit, world.blue_unit_id)
        assert blue is not None
        blue.unit_level = UnitLevel.BATTALION
        db.commit()
        _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(world.blue_unit_id, shooter_state)
        hot.put_unit(
            world.red_unit_id,
            {"strength": 100.0, "authorized_strength": 100.0, "armor_class": "INFANTRY"},
        )
        adj = _adjudicator(db, hot)
        if suppress is not None:
            adj._suppress = suppress  # type: ignore[attr-defined]
        events = adj.resolve(cmd, SimTime(0, 0))
    return events, hot, world.blue_unit_id, world.red_unit_id


async def test_a_battalion_out_of_ammo_cannot_keep_firing(
    session_factory: sessionmaker[Session],
) -> None:
    """聚合路徑過去用 `Shooter(ammo_count=1)` 這個**捏造的探子**通過合法性檢查。

    於是 NO_AMMO 對營級以上形同虛設：彈藥歸零的旅照樣持續開火造成戰損。
    """
    events, hot, _blue, red = await _aggregate_engage(
        session_factory,
        {"ammo": 0, "strength": 100.0, "authorized_strength": 100.0},
    )

    assert events[0].ai_decision["status"] == "REJECTED"  # type: ignore[index]
    assert events[0].ai_decision["reason"] == "NO_AMMO"  # type: ignore[index]
    assert hot.get_unit(red)["strength"] == 100.0  # 沒有戰損


def _fresh_factory() -> sessionmaker[Session]:
    """自備一套空 DB——`seed_world` 會建固定 username 的使用者，同一個 DB 跑兩次會撞唯一鍵。"""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.models.base import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


async def test_a_cut_off_battalion_fights_worse() -> None:
    """WP-C7.1 斷補只乘在平台級——切斷一個旅的補給線過去對它的戰鬥力毫無影響。"""
    supplied, *_ = await _aggregate_engage(
        _fresh_factory(),
        {"ammo": 999, "strength": 100.0, "authorized_strength": 100.0},
    )
    starving, *_ = await _aggregate_engage(
        _fresh_factory(),
        {
            "ammo": 999,
            "strength": 100.0,
            "authorized_strength": 100.0,
            # 斷糧三天（`supply_effectiveness` 讀 `starved_days`）。
            "starved_days": 3.0,
        },
    )

    assert starving[0].damage_calc < supplied[0].damage_calc  # type: ignore[index]


async def test_being_shelled_by_a_battalion_suppresses_you(
    session_factory: sessionmaker[Session],
) -> None:
    """壓制過去只掛在 `_apply`（平台級）——被一整個營砲擊完全不會被壓制。"""
    calls: list[tuple[str, str]] = []
    await _aggregate_engage(
        session_factory,
        {"ammo": 999, "strength": 100.0, "authorized_strength": 100.0},
        suppress=lambda uid, cat: calls.append((uid, cat)),
    )

    assert len(calls) == 1


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


# 建制數刻意取大。`ammo_spent = ceil(quantity × eff × rate)`，而 `ceil` 會把小編成的
# 斷補效應整個吃掉：7 支步槍 ×0.9 ＝ 6.3 → **仍然 ceil 成 7**，於是拿掉生產程式碼裡的
# `supply_effectiveness` 這個測試照樣綠。70 ×0.9 ＝ 63 就沒有捨入的容身處。
# （這也是活體驗收 C16 用 90 發而不是 9 發的原因——同一個陷阱。）
_RIFLES = 70
_ATGMS = 20


async def _combined_engage(
    factory: sessionmaker[Session], extra: dict[str, object]
) -> tuple[list[object], InMemoryHotState, str]:
    """跑一次**真的**聯合兵種交戰（下令 → drain → resolve），回事件與熱狀態。

    刻意不叫 `resolve_combined_engagement`：這個洞就長在 `_resolve_combined` 組
    `effectiveness` 的那幾行，純函數本身一直是對的（它照著收到的 eff 算）。
    直接測純函數會全綠而洞還在——本 repo 這一週已經有八次同樣的教訓。
    """
    world = seed_world(factory)
    with factory() as db:
        _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(
            world.blue_unit_id,
            {
                "ammo": _RIFLES + _ATGMS,
                "ammo_by_weapon": {"w-rifle": _RIFLES, "w-atgm": _ATGMS},
                "strength": 100.0,
                "authorized_strength": 100.0,
                **extra,
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
            CombinedWeapon("w-rifle", _RIFLE_C, quantity=_RIFLES, ammo=_RIFLES),
            CombinedWeapon("w-atgm", _ATGM_C, quantity=_ATGMS, ammo=_ATGMS),
        ]
        events = _adjudicator_combined(db, hot, lambda _sid: weapons).resolve(cmd, SimTime(0, 0))
        return events, hot, world.blue_unit_id


async def test_a_starving_combined_arms_unit_fires_fewer_rounds() -> None:
    """WP-C7.1：斷補要套在**三條**裁決路徑上，聯合兵種曾是唯一漏掉的一條。

    後果不是「少扣一點戰損」而是**同一支部隊有兩種物理**：操作員在下令時點了武器下拉
    就走 `_resolve_single`（會餓），不點就走這裡（不會餓）。而規格的驗收條文
    「斷補的裝甲連 3 模擬日後效能階梯下降」指的裝甲連，正是典型的多武器單位——
    那句條文要適用的對象，剛好落在唯一不生效的路徑上。

    斷言擊發彈數而不是戰損：`ammo_spent = ceil(quantity × eff × rate)` **不擲骰**，
    所以一發齊射就能下結論；戰損還要乘 U(0.8, 1.2)，×0.9 那一階的區間會重疊，
    拿一次抽樣去說「戰損降了」是在講一個自己都不相信的話。
    （活體驗收 C16 觀測到的也正是彈數：`LOG_MIXED 98→[98]`，一發都沒少。）
    """
    fed, fed_hot, fed_blue = await _combined_engage(_fresh_factory(), {})
    # 斷糧一天 → `starvation_modifier` 的第一階 ×0.9（`supply_effectiveness` 讀 `starved_days`）。
    starved, starved_hot, starved_blue = await _combined_engage(
        _fresh_factory(), {"starved_days": 1.0}
    )

    assert fed[0].ai_decision["mode"] == "COMBINED"  # type: ignore[attr-defined,index]
    assert starved[0].ai_decision["mode"] == "COMBINED"  # type: ignore[attr-defined,index]

    def _spent(hot: InMemoryHotState, uid: str) -> int:
        after = hot.get_unit(uid)["ammo_by_weapon"]
        return (100 - int(after["w-rifle"])) + (8 - int(after["w-atgm"]))

    assert _spent(fed_hot, fed_blue) > 0, "吃飽的對照組一發都沒打，這條斷言沒有意義"
    assert _spent(starved_hot, starved_blue) < _spent(fed_hot, fed_blue)


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


async def test_combined_persists_spent_ammo_to_db(
    session_factory: sessionmaker[Session],
) -> None:
    # #53：聯合交戰消耗的彈藥持久化到 DB EquipmentInstance（供 GET /weapons 顯示 + 重啟續戰）。
    from app.models.tables import EquipmentInstance, EquipmentTemplate

    world = seed_world(session_factory)
    with session_factory() as db:
        # DB 武器射程夠長 → submit precheck 過（單武器路徑）；resolve 走注入的聯合武器組合。
        tmpl = EquipmentTemplate(
            name="RIFLE",
            category="KINETIC",
            base_stats={
                "max_range_m": 8000,
                "ph_by_range_band": [[100, 0.8], [8000, 0.3]],
                "damage_by_armor_class": {"INFANTRY": 35},
                "pk_by_armor_class": {"INFANTRY": 0.5},
                "ammo_types": ["A556"],
            },
        )
        db.add(tmpl)
        db.flush()
        inst = EquipmentInstance(
            template_id=tmpl.id, owner_id=world.blue_unit_id, current_state={"ammo": 100}
        )
        db.add(inst)
        db.commit()
        oid = _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(
            world.blue_unit_id,
            {
                "ammo": 108,
                "ammo_by_weapon": {inst.id: 100, "w-atgm": 8},
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
            CombinedWeapon(inst.id, _RIFLE_C, quantity=7, ammo=100),
            CombinedWeapon("w-atgm", _ATGM_C, quantity=2, ammo=8),
        ]
        _adjudicator_combined(db, hot, lambda _sid: weapons).resolve(cmd, SimTime(0, 0))
        db.refresh(inst)
        assert inst.current_state["ammo"] < 100  # 消耗已持久化到 DB
    assert oid  # 令已建立


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


# ── WP-B6 ROE：`roe.py` 說明寫「沒有繞過的路徑」，實際上有三條 ──────────────


def _adjudicator_roe(
    db: Session,
    hot: InMemoryHotState,
    forbidden: frozenset[str],
    *,
    category: str = "MISSILE",
    name: str = "MLRS",
) -> EngagementAdjudicator:
    return EngagementAdjudicator(
        db,
        hot,
        DeterministicRNG(1, "adjudication"),
        lambda _cmd: _WEAPON,
        lambda _s, _t, _indirect=False: EnvSnapshot(range_m=500.0, los_clear=True),
        roe_for=lambda _uid: (None, forbidden),
        weapon_category_for=lambda _cmd: category,
        weapon_name_for=lambda _cmd: name,
    )


async def test_a_single_weapon_unit_obeys_the_scenario_roe(
    session_factory: sessionmaker[Session],
) -> None:
    """裁決層唯一的 ROE 套用點在 `_resolve_combined`，其進入條件是「持 ≥2 武器且未指名」。

    於是**單武器單位的 ENGAGE 完全不過 ROE**：想定宣告禁用飛彈，它照打不誤。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(world.blue_unit_id, {"ammo": 10})
        hot.put_unit(world.red_unit_id, {"health": 100.0, "armor_class": "INFANTRY"})
        events = _adjudicator_roe(db, hot, frozenset({"MISSILE"})).resolve(cmd, SimTime(0, 0))

    assert events[0].ai_decision["reason"] == "ROE"  # type: ignore[index]
    assert hot.get_unit(world.red_unit_id)["health"] == 100.0
    assert hot.get_unit(world.blue_unit_id)["ammo"] == 10  # 沒發射就不耗彈


async def test_roe_can_ban_a_specific_template_not_just_a_category(
    session_factory: sessionmaker[Session],
) -> None:
    """想定寫得出 `forbid_templates: [T-90]`——比對範本名這條路過去在裁決層不存在。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(world.blue_unit_id, {"ammo": 10})
        hot.put_unit(world.red_unit_id, {"health": 100.0, "armor_class": "INFANTRY"})
        events = _adjudicator_roe(db, hot, frozenset({"MLRS"})).resolve(cmd, SimTime(0, 0))

    assert events[0].ai_decision["reason"] == "ROE"  # type: ignore[index]


async def test_an_allowed_weapon_still_fires(session_factory: sessionmaker[Session]) -> None:
    """守門不可過寬：沒被禁的武器照常交戰（否則這條修正會把整局的火力鎖死）。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(world.blue_unit_id, {"ammo": 10})
        hot.put_unit(world.red_unit_id, {"health": 100.0, "armor_class": "INFANTRY"})
        events = _adjudicator_roe(db, hot, frozenset({"LASER"})).resolve(cmd, SimTime(0, 0))

    assert events[0].ai_decision.get("reason") != "ROE"  # type: ignore[union-attr]


async def test_a_battalion_obeys_the_scenario_roe(
    session_factory: sessionmaker[Session],
) -> None:
    """`_resolve_aggregate` 從不碰 `self._roe_for`——禁用 MLRS 的想定裡，一個旅照樣拿它打。"""
    from app.models.enums import UnitLevel
    from app.models.tables import TacticalUnit

    world = seed_world(session_factory)
    with session_factory() as db:
        blue = db.get(TacticalUnit, world.blue_unit_id)
        assert blue is not None
        blue.unit_level = UnitLevel.BATTALION
        db.commit()
        _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(
            world.blue_unit_id, {"ammo": 999, "strength": 100.0, "authorized_strength": 100.0}
        )
        hot.put_unit(
            world.red_unit_id,
            {"strength": 100.0, "authorized_strength": 100.0, "armor_class": "INFANTRY"},
        )
        events = _adjudicator_roe(db, hot, frozenset({"MISSILE"})).resolve(cmd, SimTime(0, 0))

    assert events[0].ai_decision["reason"] == "ROE"  # type: ignore[index]
    assert hot.get_unit(world.red_unit_id)["strength"] == 100.0


async def test_single_weapon_engagement_persists_spent_ammo_to_db(
    session_factory: sessionmaker[Session],
) -> None:
    """**最常見的那條路徑**：一個單位一種武器，開火後 DB 的彈藥也要跟著扣。

    聯合兵種（≥2 武器）與火力任務兩條路徑早就都寫回 `EquipmentInstance` 了，
    唯獨「單武器齊射」只扣熱狀態的純量 `ammo`。後果不是裁決算錯——是
    `GET /units/{id}/weapons`（COP 單位卡與選彈畫面讀的就是它）**永遠顯示滿彈**：
    一個排打光了子彈，畫面上還是 900 發，指揮官據此決定不用補給。
    sim 一重啟熱狀態重新播種，彈匣還會自己補滿。

    而一個步槍排正是這套系統裡最多的單位。
    """
    from app.models.tables import EquipmentInstance, EquipmentTemplate

    world = seed_world(session_factory)
    with session_factory() as db:
        tmpl = EquipmentTemplate(
            name="RIFLE_SOLO",
            category="KINETIC",
            base_stats={
                "max_range_m": 8000,
                "ph_by_range_band": [[100, 1.0], [8000, 1.0]],
                "damage_by_armor_class": {"INFANTRY": 35},
                "pk_by_armor_class": {"INFANTRY": 0.5},
                "ammo_types": ["A556"],
            },
        )
        db.add(tmpl)
        db.flush()
        inst = EquipmentInstance(
            template_id=tmpl.id,
            owner_id=world.blue_unit_id,
            quantity=30,
            current_state={"ammo": 900},
        )
        db.add(inst)
        db.commit()
        _submit_engage(db, world)
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()

        hot = InMemoryHotState()
        hot.put_unit(
            world.blue_unit_id,
            {"ammo": 900, "strength": 100.0, "authorized_strength": 100.0},
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
        # 只有**一件**武器 → 走單武器/齊射路徑（len(cweapons) < 2）。
        weapons = [CombinedWeapon(inst.id, _RIFLE_C, quantity=30, ammo=900)]
        _adjudicator_combined(db, hot, lambda _sid: weapons).resolve(cmd, SimTime(0, 0))

        spent_hot = 900 - int(hot.get_unit(world.blue_unit_id)["ammo"])
        assert spent_hot > 0, "齊射應該要消耗彈藥"

        db.refresh(inst)
        assert inst.current_state["ammo"] == 900 - spent_hot, (
            f"DB 的彈藥要與熱狀態一致：熱狀態扣了 {spent_hot}，DB 卻是 {inst.current_state['ammo']}"
        )
        # 逐武器帳也要記上，否則之後這個單位拿到第二件武器就會「回滿」。
        assert hot.get_unit(world.blue_unit_id)["ammo_by_weapon"][inst.id] == 900 - spent_hot
