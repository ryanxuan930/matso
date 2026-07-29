"""曲射火協 gate（WP-B5.3）——本局開關 + 已核准的 FIRE_SUPPORT 申請單。"""

from __future__ import annotations

import pytest
from _order_fakes import OrderWorld, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.errors import PrecheckFailedError
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


# ---- WP-C10.2 FIRE_MISSION 也要走火協 gate ----


def test_fire_mission_always_needs_approval(session_factory: sessionmaker[Session]) -> None:
    """**面目標射擊本身即曲射**——不掛核准單就能打的話，等於用新令型繞過火協。

    與 B5.3 的「不指名武器就繞過」是同一類洞：新增一條路徑時忘了套既有的閘門。
    """
    from app.orders.schemas import FireMissionPayload

    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    _enable_gate(session_factory, world)
    payload = FireMissionPayload(target_lat=23.8, target_lng=121.3, rounds=4)
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        out = _precheck_fire_approval(db, unit, payload)
    assert out and not out[0].passed


def test_fire_mission_with_approval_passes(session_factory: sessionmaker[Session]) -> None:
    from app.orders.schemas import FireMissionPayload

    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    _enable_gate(session_factory, world)
    rid = _make_request(session_factory, world, RequestStatus.APPROVED)
    payload = FireMissionPayload(target_lat=23.8, target_lng=121.3, fire_request_id=rid)
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        assert _precheck_fire_approval(db, unit, payload) == []


def test_fire_mission_gate_off_unaffected(session_factory: sessionmaker[Session]) -> None:
    """未開開關 → 面射擊不需核准單（既有局零行為變更）。"""
    from app.orders.schemas import FireMissionPayload

    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "ARTILLERY")
    payload = FireMissionPayload(target_lat=23.8, target_lng=121.3)
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        assert _precheck_fire_approval(db, unit, payload) == []


# ---- 核准單的兌現（令被收下時扣掉，B5.3；FIRE_MISSION 一併，C10.2）----

_HOWITZER_STATS = {
    "max_range_m": 15000,
    "ph_by_range_band": [[15000, 0.5]],
    "damage_by_armor_class": {"INFANTRY": 60},
    "ammo_types": ["HE"],
    "indirect_fire": True,
    "dispersion_cep_m": 50,
    "lethal_radius_m": 60,
}


def _give_howitzer(factory: sessionmaker[Session], unit_id: str) -> str:
    with factory() as db:
        t = EquipmentTemplate(name="M109", category="ARTILLERY", base_stats=_HOWITZER_STATS)
        db.add(t)
        db.flush()
        inst = EquipmentInstance(template_id=t.id, owner_id=unit_id, current_state={"ammo": 30})
        db.add(inst)
        db.commit()
        return inst.id


def _status_of(factory: sessionmaker[Session], request_id: str) -> RequestStatus:
    with factory() as db:
        req = db.get(Request, request_id)
        assert req is not None
        return req.status


def test_fire_mission_expends_the_approval_it_used(
    session_factory: sessionmaker[Session],
) -> None:
    """**一張核准單掛一道令。**

    `FireMissionPayload` 不是 `EngagePayload` 的子類，兌現那段若只判 EngagePayload，
    同一張核准單可以無限次掛在面射擊令上——預檢擋得住「沒核准單」，
    擋不住「一張單用一百次」。
    """
    from _order_fakes import FakeGateway

    from app.orders.schemas import OrderRequest, OrderType
    from app.orders.service import OrderService

    world = seed_world(session_factory)
    _give_howitzer(session_factory, world.blue_unit_id)
    _enable_gate(session_factory, world)
    rid = _make_request(session_factory, world, RequestStatus.APPROVED)

    req = OrderRequest(
        unit_id=world.blue_unit_id,
        order_type=OrderType.FIRE_MISSION,
        payload={
            "target_lat": 23.76,
            "target_lng": 121.26,
            "rounds": 4,
            "fire_request_id": rid,
        },
    )
    with session_factory() as db:
        resp = OrderService(db, FakeGateway()).submit(world.session_id, req, world.blue_issuer_id)
    assert resp.status == "VALIDATED"
    assert _status_of(session_factory, rid) is RequestStatus.EXPENDED

    # 同一張單再掛第二道令 → 已 EXPENDED，預檢擋下。
    # 目標座標故意換一個：payload 一模一樣會走去重路徑（回既有令、不跑預檢），驗不到這件事。
    again = req.model_copy(update={"payload": {**req.payload, "target_lng": 121.27}})
    with session_factory() as db, pytest.raises(PrecheckFailedError):
        OrderService(db, FakeGateway()).submit(world.session_id, again, world.blue_issuer_id)
