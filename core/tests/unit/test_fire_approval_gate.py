"""曲射火協 gate（WP-B5.3）——本局開關 + 已核准的 FIRE_SUPPORT 申請單。"""

from __future__ import annotations

from _order_fakes import OrderWorld, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import RequestKind, RequestStatus
from app.models.tables import (
    EquipmentInstance,
    EquipmentTemplate,
    Request,
    TacticalUnit,
    WargameSession,
)
from app.orders.precheck import _precheck_fire_approval
from app.orders.schemas import EngagePayload


def _enable_gate(factory: sessionmaker[Session], world: OrderWorld, on: bool = True) -> None:
    with factory() as db:
        s = db.get(WargameSession, world.session_id)
        assert s is not None
        s.indirect_fire_requires_approval = on
        db.commit()


def _give_weapon(factory: sessionmaker[Session], unit_id: str, category: str) -> str:
    """配一件指定類別的武器給單位，回 EquipmentInstance id。"""
    with factory() as db:
        t = EquipmentTemplate(name=f"t-{category}", category=category, base_stats={})
        db.add(t)
        db.flush()
        inst = EquipmentInstance(template_id=t.id, owner_id=unit_id, current_state={})
        db.add(inst)
        db.commit()
        return inst.id


def _make_request(
    factory: sessionmaker[Session],
    world: OrderWorld,
    status: RequestStatus,
    kind: RequestKind = RequestKind.FIRE_SUPPORT,
    faction: str = "BLUE",
) -> str:
    with factory() as db:
        r = Request(
            session_id=world.session_id,
            faction=faction,
            kind=kind,
            status=status,
            params={},
            requested_by_id="u",
            requested_at_tick=1,
        )
        db.add(r)
        db.commit()
        return r.id


def _check(factory: sessionmaker[Session], world: OrderWorld, payload: EngagePayload) -> list:  # type: ignore[type-arg]
    with factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        return _precheck_fire_approval(db, unit, payload)


def _p(**kw: object) -> EngagePayload:
    base: dict[str, object] = {"target_unit_id": "R1"}
    base.update(kw)
    return EngagePayload(**base)  # type: ignore[arg-type]


def test_gate_off_means_zero_behaviour_change(session_factory: sessionmaker[Session]) -> None:
    """**未開啟本局開關 → 一律通過**（既有局零行為變更）。"""
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    assert _check(session_factory, world, _p()) == []


def test_indirect_without_approval_is_blocked(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    wid = _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    _enable_gate(session_factory, world)
    out = _check(session_factory, world, _p(weapon_id=wid))
    assert out and not out[0].passed
    assert "火協" in out[0].detail


def test_unnamed_weapon_cannot_bypass_the_gate(session_factory: sessionmaker[Session]) -> None:
    """**這條是本卡的重點。**

    ROE 只擋「令面指名了被禁武器」。火協若照抄，不指名武器就能繞過——那就不是 gate。
    持有曲射武器的單位，即使沒指名武器，也要有核准單。
    """
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    _enable_gate(session_factory, world)
    out = _check(session_factory, world, _p())  # 完全沒指名武器
    assert out and not out[0].passed, "不指名武器就繞過了火協 gate"


def test_named_direct_fire_weapon_needs_no_approval(
    session_factory: sessionmaker[Session],
) -> None:
    """指名直射武器＝不是曲射任務，不需火協。"""
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    kid = _give_weapon(session_factory, world.blue_unit_id, "KINETIC")
    _enable_gate(session_factory, world)
    assert _check(session_factory, world, _p(weapon_id=kid)) == []


def test_unit_without_indirect_weapons_unaffected(
    session_factory: sessionmaker[Session],
) -> None:
    """沒有曲射武器的單位不受影響——不該被無關的開關綁住。"""
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "KINETIC")
    _enable_gate(session_factory, world)
    assert _check(session_factory, world, _p()) == []


def test_approved_request_passes(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    wid = _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    _enable_gate(session_factory, world)
    rid = _make_request(session_factory, world, RequestStatus.APPROVED)
    assert _check(session_factory, world, _p(weapon_id=wid, fire_request_id=rid)) == []


def test_pending_request_rejected(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    wid = _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    _enable_gate(session_factory, world)
    rid = _make_request(session_factory, world, RequestStatus.PENDING)
    out = _check(session_factory, world, _p(weapon_id=wid, fire_request_id=rid))
    assert out and "尚未核准" in out[0].detail


def test_expended_request_cannot_be_reused(session_factory: sessionmaker[Session]) -> None:
    """**一張核准單只能兌現一次**——EXPENDED 不得再掛在第二道令上。"""
    world = seed_world(session_factory)
    wid = _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    _enable_gate(session_factory, world)
    rid = _make_request(session_factory, world, RequestStatus.EXPENDED)
    out = _check(session_factory, world, _p(weapon_id=wid, fire_request_id=rid))
    assert out and not out[0].passed


def test_other_factions_request_rejected(session_factory: sessionmaker[Session]) -> None:
    """不得拿別的陣營的核准單來用。"""
    world = seed_world(session_factory)
    wid = _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    _enable_gate(session_factory, world)
    rid = _make_request(session_factory, world, RequestStatus.APPROVED, faction="RED")
    out = _check(session_factory, world, _p(weapon_id=wid, fire_request_id=rid))
    assert out and "不屬於本陣營" in out[0].detail


def test_wrong_kind_request_rejected(session_factory: sessionmaker[Session]) -> None:
    """空偵核准單不能拿來當火協用。"""
    world = seed_world(session_factory)
    wid = _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    _enable_gate(session_factory, world)
    rid = _make_request(session_factory, world, RequestStatus.APPROVED, kind=RequestKind.AIR_RECON)
    out = _check(session_factory, world, _p(weapon_id=wid, fire_request_id=rid))
    assert out and not out[0].passed
