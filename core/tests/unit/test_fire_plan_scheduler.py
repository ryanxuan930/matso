"""at_tick 火力排程（WP-C10.3）——時間到了自動下令。

排程器本身很薄，所以這裡釘的是**時機**：早了不打、到了要打、錯過了要補、打過不再打。
"""

from __future__ import annotations

from _order_fakes import FakeGateway, OrderWorld, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.fires.scheduler import due_targets, run_due_fire_missions
from app.fires.service import NewTarget, cancel_plan, create_plan, targets_of
from app.models.enums import FirePlanTargetStatus, FireSchedule
from app.models.tables import (
    EquipmentInstance,
    EquipmentTemplate,
    FirePlan,
    Order,
    SessionParticipant,
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
_AIM = (23.78, 121.28)


def _svc(db: Session) -> OrderService:
    return OrderService(db, FakeGateway())


def _arm(factory: sessionmaker[Session], unit_id: str) -> None:
    with factory() as db:
        t = EquipmentTemplate(name="M109", category="ARTILLERY", base_stats=_HOWITZER)
        db.add(t)
        db.flush()
        db.add(EquipmentInstance(template_id=t.id, owner_id=unit_id, current_state={"ammo": 60}))
        db.commit()


def _plan_at(
    factory: sessionmaker[Session],
    world: OrderWorld,
    at_ticks: list[int],
    *,
    schedule: FireSchedule = FireSchedule.AT_TICK,
) -> str:
    with factory() as db:
        plan = create_plan(
            db,
            world.session_id,
            "BLUE",
            "H-20 攻擊準備射擊",
            [
                NewTarget(
                    target_lat=_AIM[0],
                    target_lng=_AIM[1] + i * 0.001,  # 錯開座標，避免與去重無關的干擾
                    shooter_unit_id=world.blue_unit_id,
                    label=f"AB200{i}",
                    schedule=schedule,
                    at_tick=t,
                )
                for i, t in enumerate(at_ticks)
            ],
            participant_id=world.blue_issuer_id,
            tick=0,
        )
        return plan.id


def _run(factory: sessionmaker[Session], world: OrderWorld, tick: int) -> int:
    with factory() as db:
        return run_due_fire_missions(db, world.session_id, tick, _svc)


def _statuses(factory: sessionmaker[Session], plan_id: str) -> list[FirePlanTargetStatus]:
    with factory() as db:
        return [t.status for t in targets_of(db, plan_id)]


def _order_count(factory: sessionmaker[Session], world: OrderWorld) -> int:
    with factory() as db:
        return db.query(Order).filter(Order.session_id == world.session_id).count()


def test_nothing_fires_before_its_tick(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    pid = _plan_at(session_factory, world, [100])
    assert _run(session_factory, world, 99) == 0
    assert _statuses(session_factory, pid) == [FirePlanTargetStatus.PENDING]


def test_it_fires_on_its_tick(session_factory: sessionmaker[Session]) -> None:
    """驗收條件：預劃的攻擊準備射擊到時**自動執行**，沒有人按任何按鈕。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    pid = _plan_at(session_factory, world, [100])
    assert _run(session_factory, world, 100) == 1
    assert _statuses(session_factory, pid) == [FirePlanTargetStatus.FIRED]
    assert _order_count(session_factory, world) == 1


def test_a_missed_tick_still_fires_late(session_factory: sessionmaker[Session]) -> None:
    """**這條是刻意的行為，不是寬鬆。**

    runner 會暫停、崩潰重啟、回滾。只認 `at_tick == tick` 的話，錯過的那一刻就永遠不補打，
    而且不會有任何徵兆——預劃火力靜靜地不見。寧可遲到（遲多久看 fired_at_tick 就知道）。
    """
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    pid = _plan_at(session_factory, world, [100])
    assert _run(session_factory, world, 250) == 1, "錯過的預劃火力靜靜地消失了"
    assert _statuses(session_factory, pid) == [FirePlanTargetStatus.FIRED]
    with session_factory() as db:
        assert targets_of(db, pid)[0].fired_at_tick == 250  # 遲到看得見


def test_a_target_does_not_fire_twice(session_factory: sessionmaker[Session]) -> None:
    """狀態落在 DB（不是行程內的 set）——重跑同一 tick 不該再打一輪。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    _plan_at(session_factory, world, [100])
    assert _run(session_factory, world, 100) == 1
    assert _run(session_factory, world, 100) == 0
    assert _run(session_factory, world, 101) == 0
    assert _order_count(session_factory, world) == 1


def test_on_call_targets_are_never_auto_fired(session_factory: sessionmaker[Session]) -> None:
    """待命目標就是待命——排程器不該替 FSO 決定什麼時候呼叫。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    pid = _plan_at(session_factory, world, [1], schedule=FireSchedule.ON_CALL)
    assert _run(session_factory, world, 999) == 0
    assert _statuses(session_factory, pid) == [FirePlanTargetStatus.PENDING]


def test_cancelled_plans_are_not_scheduled(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    pid = _plan_at(session_factory, world, [100])
    with session_factory() as db:
        plan = db.get(FirePlan, pid)
        assert plan is not None
        cancel_plan(db, plan)
    assert _run(session_factory, world, 100) == 0


def test_execution_order_is_deterministic(session_factory: sessionmaker[Session]) -> None:
    """同一 tick 到期的多個目標依 seq 送出——**沒有 ORDER BY 就不可重播**。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    _plan_at(session_factory, world, [50, 50, 50])
    with session_factory() as db:
        pairs = due_targets(db, world.session_id, 50)
    assert [t.seq for _, t in pairs] == [0, 1, 2]


def test_all_due_targets_fire_in_one_tick(session_factory: sessionmaker[Session]) -> None:
    """「攻擊準備射擊」是一整組目標同時落地，不是一個 tick 打一個。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    pid = _plan_at(session_factory, world, [50, 50, 50])
    assert _run(session_factory, world, 50) == 3
    assert _statuses(session_factory, pid) == [FirePlanTargetStatus.FIRED] * 3
    assert _order_count(session_factory, world) == 3


def test_author_gone_marks_failed_and_keeps_going(
    session_factory: sessionmaker[Session],
) -> None:
    """作者已離局 → 沒有下令者。判 FAILED 並記原因，**不是拿別人的身分硬送**。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    pid = _plan_at(session_factory, world, [10])
    with session_factory() as db:
        part = db.get(SessionParticipant, world.blue_issuer_id)
        assert part is not None
        db.delete(part)
        db.commit()
    assert _run(session_factory, world, 10) == 0
    with session_factory() as db:
        t = targets_of(db, pid)[0]
    assert t.status is FirePlanTargetStatus.FAILED
    assert t.failure_reason and "建立者" in t.failure_reason


def test_one_bad_target_does_not_stop_the_rest(session_factory: sessionmaker[Session]) -> None:
    """一個目標打不出去不該讓整份計畫停擺——第二個目標仍要打出去。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    with session_factory() as db:
        plan = create_plan(
            db,
            world.session_id,
            "BLUE",
            "混合",
            [
                # 第一個指定敵方單位 → 權限被擋
                NewTarget(
                    target_lat=_AIM[0],
                    target_lng=_AIM[1],
                    shooter_unit_id=world.red_unit_id,
                    schedule=FireSchedule.AT_TICK,
                    at_tick=10,
                ),
                NewTarget(
                    target_lat=_AIM[0],
                    target_lng=_AIM[1] + 0.002,
                    shooter_unit_id=world.blue_unit_id,
                    schedule=FireSchedule.AT_TICK,
                    at_tick=10,
                ),
            ],
            participant_id=world.blue_issuer_id,
            tick=0,
        )
        pid = plan.id
    assert _run(session_factory, world, 10) == 1
    assert _statuses(session_factory, pid) == [
        FirePlanTargetStatus.FAILED,
        FirePlanTargetStatus.FIRED,
    ]
