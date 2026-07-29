"""火力計畫服務（WP-C10.3）——預劃目標 → FIRE_MISSION 令。

本卡**不新增物理**，所以這裡釘的全是「令有沒有正確地被送出去/被擋下來」，
以及那些一旦寫錯就會安靜出錯的地方（去重、重複執行、被擋時的狀態）。
"""

from __future__ import annotations

import pytest
from _order_fakes import FakeGateway, OrderWorld, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.fires.service import (
    FirePlanError,
    NewTarget,
    cancel_plan,
    create_plan,
    fire_target,
    issuer_for,
    list_plans,
    targets_of,
)
from app.models.enums import FirePlanTargetStatus, FireSchedule, RequestStatus
from app.models.tables import (
    EquipmentInstance,
    EquipmentTemplate,
    Order,
    Request,
    SessionParticipant,
    WargameSession,
)
from app.orders.service import OrderService

_HOWITZER = {
    "max_range_m": 20000,
    "ph_by_range_band": [[20000, 0.5]],
    "damage_by_armor_class": {"INFANTRY": 60},
    "pk_by_armor_class": {"INFANTRY": 0.6},
    "ammo_types": ["HE"],
    "indirect_fire": True,
    "dispersion_cep_m": 100,
    "lethal_radius_m": 50,
}

# 藍軍單位在 (23.75, 121.25)；預劃目標放在射程內。
_AIM = (23.78, 121.28)


def _svc(gateway: FakeGateway | None = None):  # type: ignore[no-untyped-def]
    gw = gateway or FakeGateway()
    return lambda db: OrderService(db, gw)


def _give_howitzer(factory: sessionmaker[Session], unit_id: str, ammo: int = 40) -> str:
    with factory() as db:
        t = EquipmentTemplate(name="M109", category="ARTILLERY", base_stats=_HOWITZER)
        db.add(t)
        db.flush()
        inst = EquipmentInstance(template_id=t.id, owner_id=unit_id, current_state={"ammo": ammo})
        db.add(inst)
        db.commit()
        return inst.id


def _plan(
    factory: sessionmaker[Session],
    world: OrderWorld,
    *,
    n: int = 1,
    schedule: FireSchedule = FireSchedule.ON_CALL,
    at_tick: int | None = None,
    fire_request_id: str | None = None,
    rounds: int = 4,
) -> str:
    with factory() as db:
        plan = create_plan(
            db,
            world.session_id,
            "BLUE",
            "攻擊準備射擊",
            [
                NewTarget(
                    target_lat=_AIM[0],
                    target_lng=_AIM[1],
                    shooter_unit_id=world.blue_unit_id,
                    label=f"AB100{i}",
                    rounds=rounds,
                    schedule=schedule,
                    at_tick=at_tick,
                    fire_request_id=fire_request_id,
                )
                for i in range(n)
            ],
            participant_id=world.blue_issuer_id,
            tick=5,
        )
        return plan.id


def _fire_first(factory: sessionmaker[Session], world: OrderWorld, plan_id: str, *, tick: int = 10):  # type: ignore[no-untyped-def]
    from app.models.tables import FirePlan

    with factory() as db:
        plan = db.get(FirePlan, plan_id)
        assert plan is not None
        target = targets_of(db, plan_id)[0]
        out = fire_target(
            db,
            plan,
            target,
            issuer_id=world.blue_issuer_id,
            order_service_factory=_svc(),
            tick=tick,
        )
        return out.status, out.order_id, out.failure_reason


def _orders(factory: sessionmaker[Session], world: OrderWorld) -> list[Order]:
    with factory() as db:
        return list(db.query(Order).filter(Order.session_id == world.session_id).all())


# ---- 建立與查詢 ----


def test_targets_keep_their_order(session_factory: sessionmaker[Session]) -> None:
    """seq 由清單順序決定——**排程器依 seq 執行，順序必須確定**才能重播。"""
    world = seed_world(session_factory)
    pid = _plan(session_factory, world, n=3)
    with session_factory() as db:
        assert [t.seq for t in targets_of(db, pid)] == [0, 1, 2]
        assert [t.label for t in targets_of(db, pid)] == ["AB1000", "AB1001", "AB1002"]


def test_other_factions_plans_are_not_listed(session_factory: sessionmaker[Session]) -> None:
    """**火力計畫是陣營私有情報**（紅線 3）——過濾在後端，不是前端。"""
    world = seed_world(session_factory)
    _plan(session_factory, world)
    with session_factory() as db:
        assert len(list_plans(db, world.session_id, "BLUE", omniscient=False)) == 1
        assert list_plans(db, world.session_id, "RED", omniscient=False) == []
        assert len(list_plans(db, world.session_id, "RED", omniscient=True)) == 1


def test_cancel_skips_pending_but_leaves_fired_alone(
    session_factory: sessionmaker[Session],
) -> None:
    """已執行的目標不因取消計畫而改狀態——那些令已經在帳本上了。"""
    from app.models.tables import FirePlan

    world = seed_world(session_factory)
    _give_howitzer(session_factory, world.blue_unit_id)
    pid = _plan(session_factory, world, n=2)
    _fire_first(session_factory, world, pid)
    with session_factory() as db:
        plan = db.get(FirePlan, pid)
        assert plan is not None
        cancel_plan(db, plan)
        states = [t.status for t in targets_of(db, pid)]
    assert states == [FirePlanTargetStatus.FIRED, FirePlanTargetStatus.SKIPPED]


# ---- 執行 ----


def test_firing_a_target_creates_a_fire_mission_order(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    _give_howitzer(session_factory, world.blue_unit_id)
    pid = _plan(session_factory, world)
    status, order_id, reason = _fire_first(session_factory, world, pid)
    assert status is FirePlanTargetStatus.FIRED, reason
    orders = _orders(session_factory, world)
    assert len(orders) == 1
    assert orders[0].order_type == "FIRE_MISSION"
    assert orders[0].payload["target_lat"] == pytest.approx(_AIM[0])
    assert orders[0].id == order_id


def test_two_identical_targets_produce_two_orders(
    session_factory: sessionmaker[Session],
) -> None:
    """**這條是本卡最容易安靜出錯的地方。**

    `OrderService._find_active_duplicate` 比對的是 payload 原始 dict，同一門砲對同一座標
    同樣發數的兩個預劃目標會被判為重複——**回既有的令、假裝成功、只打一發**。
    payload 帶 `fire_plan_target_id` 才會是兩道令。
    """
    world = seed_world(session_factory)
    _give_howitzer(session_factory, world.blue_unit_id)
    pid = _plan(session_factory, world, n=2)
    from app.models.tables import FirePlan

    with session_factory() as db:
        plan = db.get(FirePlan, pid)
        assert plan is not None
        for t in targets_of(db, pid):
            fire_target(
                db,
                plan,
                t,
                issuer_id=world.blue_issuer_id,
                order_service_factory=_svc(),
                tick=10,
            )
    orders = _orders(session_factory, world)
    assert len(orders) == 2, "兩個預劃目標被去重成一道令＝少打一輪，而且沒有任何錯誤訊息"
    assert {o.payload["fire_plan_target_id"] for o in orders} == set(
        _target_ids(session_factory, pid)
    )


def _target_ids(factory: sessionmaker[Session], plan_id: str) -> list[str]:
    with factory() as db:
        return [t.id for t in targets_of(db, plan_id)]


def test_a_target_only_fires_once(session_factory: sessionmaker[Session]) -> None:
    """重複執行不是「多打幾發」，是排程器出錯——擋在這裡，不要靜靜再打一輪。"""
    from app.models.tables import FirePlan

    world = seed_world(session_factory)
    _give_howitzer(session_factory, world.blue_unit_id)
    pid = _plan(session_factory, world)
    _fire_first(session_factory, world, pid)
    with session_factory() as db:
        plan = db.get(FirePlan, pid)
        assert plan is not None
        target = targets_of(db, pid)[0]
        with pytest.raises(FirePlanError):
            fire_target(
                db,
                plan,
                target,
                issuer_id=world.blue_issuer_id,
                order_service_factory=_svc(),
                tick=11,
            )


def test_a_blocked_order_marks_the_target_failed_with_a_reason(
    session_factory: sessionmaker[Session],
) -> None:
    """打不出去要**看得見為什麼**——沒有曲射武器的單位下火力任務會被預檢擋下。"""
    world = seed_world(session_factory)  # 不配砲
    pid = _plan(session_factory, world)
    status, order_id, reason = _fire_first(session_factory, world, pid)
    assert status is FirePlanTargetStatus.FAILED
    assert order_id is None
    assert reason and "曲射" in reason


def test_blocked_order_does_not_raise(session_factory: sessionmaker[Session]) -> None:
    """一個目標打不出去，不該讓整個排程器停擺——故 `fire_target` 不往上拋。"""
    world = seed_world(session_factory)
    pid = _plan(session_factory, world)
    with session_factory() as db:
        from app.models.tables import FirePlan

        plan = db.get(FirePlan, pid)
        assert plan is not None
        fire_target(  # 不應拋
            db,
            plan,
            targets_of(db, pid)[0],
            issuer_id=world.blue_issuer_id,
            order_service_factory=_svc(),
            tick=10,
        )


# ---- 火協 gate 不得被繞過（紅線 3）----


def _enable_gate(factory: sessionmaker[Session], world: OrderWorld) -> None:
    with factory() as db:
        s = db.get(WargameSession, world.session_id)
        assert s is not None
        s.indirect_fire_requires_approval = True
        db.commit()


def _approved_request(factory: sessionmaker[Session], world: OrderWorld) -> str:
    from app.models.enums import RequestKind

    with factory() as db:
        r = Request(
            session_id=world.session_id,
            faction="BLUE",
            kind=RequestKind.FIRE_SUPPORT,
            status=RequestStatus.APPROVED,
            params={},
            requested_by_id="u",
            requested_at_tick=1,
        )
        db.add(r)
        db.commit()
        return r.id


def test_fire_plan_cannot_bypass_the_fire_approval_gate(
    session_factory: sessionmaker[Session],
) -> None:
    """**預劃火力不是繞過火協的後門。**

    走的是同一個 `OrderService.submit`，所以本局要求火協時，沒掛核准單的預劃目標
    一樣打不出去。這條是紅線 3 的具體形狀。
    """
    world = seed_world(session_factory)
    _give_howitzer(session_factory, world.blue_unit_id)
    _enable_gate(session_factory, world)
    pid = _plan(session_factory, world)
    status, _, reason = _fire_first(session_factory, world, pid)
    assert status is FirePlanTargetStatus.FAILED
    assert reason and "火協" in reason


def test_fire_plan_with_approval_passes_the_gate(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    _give_howitzer(session_factory, world.blue_unit_id)
    _enable_gate(session_factory, world)
    rid = _approved_request(session_factory, world)
    pid = _plan(session_factory, world, fire_request_id=rid)
    status, _, reason = _fire_first(session_factory, world, pid)
    assert status is FirePlanTargetStatus.FIRED, reason


def test_one_approval_cannot_cover_two_targets(
    session_factory: sessionmaker[Session],
) -> None:
    """一張核准單只兌現一次——所以它是**逐目標**欄位，不是整份計畫共用。

    第二個目標拿同一張單會被擋（已 EXPENDED）。這件事違反直覺，故釘住。
    """
    from app.models.tables import FirePlan

    world = seed_world(session_factory)
    _give_howitzer(session_factory, world.blue_unit_id)
    _enable_gate(session_factory, world)
    rid = _approved_request(session_factory, world)
    pid = _plan(session_factory, world, n=2, fire_request_id=rid)
    with session_factory() as db:
        plan = db.get(FirePlan, pid)
        assert plan is not None
        outs = [
            fire_target(
                db,
                plan,
                t,
                issuer_id=world.blue_issuer_id,
                order_service_factory=_svc(),
                tick=10,
            ).status
            for t in targets_of(db, pid)
        ]
    assert outs == [FirePlanTargetStatus.FIRED, FirePlanTargetStatus.FAILED]


# ---- 自動執行的下令者 ----


def test_issuer_is_the_plan_author(session_factory: sessionmaker[Session]) -> None:
    from app.models.tables import FirePlan

    world = seed_world(session_factory)
    pid = _plan(session_factory, world)
    with session_factory() as db:
        plan = db.get(FirePlan, pid)
        assert plan is not None
        assert issuer_for(db, plan) == world.blue_issuer_id


def test_issuer_is_none_when_the_author_left_the_session(
    session_factory: sessionmaker[Session],
) -> None:
    """作者被移出本局 → 沒有下令者。排程器要把該目標判 FAILED，不是拿別人的身分硬送。"""
    from app.models.tables import FirePlan

    world = seed_world(session_factory)
    pid = _plan(session_factory, world)
    with session_factory() as db:
        part = db.get(SessionParticipant, world.blue_issuer_id)
        assert part is not None
        db.delete(part)
        db.commit()
    with session_factory() as db:
        plan = db.get(FirePlan, pid)
        assert plan is not None
        assert issuer_for(db, plan) is None


def test_a_plan_cannot_order_another_factions_unit(
    session_factory: sessionmaker[Session],
) -> None:
    """計畫指定敵方單位當砲兵 → 下令權限檢查擋下（不是靠計畫自己判陣營）。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        plan = create_plan(
            db,
            world.session_id,
            "BLUE",
            "越權",
            [
                NewTarget(
                    target_lat=_AIM[0],
                    target_lng=_AIM[1],
                    shooter_unit_id=world.red_unit_id,  # 紅軍的單位
                )
            ],
            participant_id=world.blue_issuer_id,
            tick=5,
        )
        out = fire_target(
            db,
            plan,
            targets_of(db, plan.id)[0],
            issuer_id=world.blue_issuer_id,
            order_service_factory=_svc(),
            tick=10,
        )
    assert out.status is FirePlanTargetStatus.FAILED
    assert out.failure_reason and "ORDER_PERMISSION_DENIED" in out.failure_reason
