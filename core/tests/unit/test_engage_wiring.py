"""活執行期交戰接線輔助（新 #1）：WeaponResolver / seed_combat_state / make_engage_env。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.adjudication.adjudicator import EngageCommand
from app.adjudication.effectiveness import effectiveness_pct
from app.engine.engage_wiring import WeaponResolver, make_engage_env, seed_combat_state
from app.models import Base
from app.models.enums import UnitLevel
from app.models.tables import EquipmentInstance, EquipmentTemplate, TacticalUnit, WargameSession
from app.state.hot_state import InMemoryHotState

_RIFLE = {
    "max_range_m": 600,
    "ph_by_range_band": [[100, 0.8], [600, 0.2]],
    "damage_by_armor_class": {"INFANTRY": 35},
    "ammo_types": ["AMMO_556"],
}
_ATGM = {
    "max_range_m": 4000,
    "ph_by_range_band": [[500, 0.9], [4000, 0.5]],
    "damage_by_armor_class": {"ARMOR": 200},
    "ammo_types": ["AMMO_ATGM"],
}


def _db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def _seed(db: Session) -> tuple[str, str, str, str]:
    """session + 一單位（配 RIFLE ammo 100 + ATGM ammo 8）。回 (sid, unit, rifle, atgm)。"""
    s = WargameSession(name="w", master_seed=1, current_weather={})
    db.add(s)
    db.flush()
    u = TacticalUnit(
        session_id=s.id,
        designation="B1",
        unit_level=UnitLevel.PLATOON,
        faction="BLUE",
        current_lat=23.75,
        current_lng=121.25,
        current_strength=88.0,
        authorized_strength=100.0,
        health_status=88.0,
        attributes={"armor_class": "LIGHT_VEHICLE", "platform_count": 4},
    )
    rifle = EquipmentTemplate(name="RIFLE", category="KINETIC", base_stats=_RIFLE)
    atgm = EquipmentTemplate(name="ATGM", category="KINETIC", base_stats=_ATGM)
    db.add_all([u, rifle, atgm])
    db.flush()
    db.add_all(
        [
            EquipmentInstance(template_id=rifle.id, owner_id=u.id, current_state={"ammo": 100}),
            EquipmentInstance(template_id=atgm.id, owner_id=u.id, current_state={"ammo": 8}),
        ]
    )
    db.commit()
    return s.id, u.id, rifle.id, atgm.id


def test_weapon_resolver_primary_is_longest_range() -> None:
    db = _db()
    sid, uid, _rifle_id, _atgm_id = _seed(db)
    r = WeaponResolver(db, sid)
    # 未指定選武器 → 主武器＝射程最遠（ATGM 4km）。
    prof = r.weapon_for(EngageCommand(order_id="o", shooter_id=uid, target_id="t"))
    assert prof.max_range_m == pytest.approx(4000)
    # 主武器彈藥＝ATGM 的 8。
    assert r.primary_ammo(uid) == 8


def test_weapon_resolver_honors_chosen_weapon() -> None:
    db = _db()
    sid, uid, rifle_id, _atgm = _seed(db)
    r = WeaponResolver(db, sid)
    prof = r.weapon_for(
        EngageCommand(order_id="o", shooter_id=uid, target_id="t", weapon_template_id=rifle_id)
    )
    assert prof.max_range_m == pytest.approx(600)  # honor 選定的 RIFLE


def test_weapon_resolver_honors_chosen_weapon_by_instance_id() -> None:
    # COP 下令的 weapon_id 實為 EquipmentInstance.id（非 template id）——須也能 honor。
    db = _db()
    sid, uid, rifle_id, _atgm = _seed(db)
    rifle_inst = db.scalars(
        select(EquipmentInstance).where(EquipmentInstance.template_id == rifle_id)
    ).first()
    assert rifle_inst is not None
    r = WeaponResolver(db, sid)
    prof = r.weapon_for(
        EngageCommand(order_id="o", shooter_id=uid, target_id="t", weapon_template_id=rifle_inst.id)
    )
    assert prof.max_range_m == pytest.approx(600)  # honor 選定的 RIFLE（以實例 id）


def test_weapon_resolver_unknown_unit_returns_no_weapon() -> None:
    db = _db()
    sid, _uid, _r, _a = _seed(db)
    r = WeaponResolver(db, sid)
    prof = r.weapon_for(EngageCommand(order_id="o", shooter_id="ghost", target_id="t"))
    assert prof.max_range_m <= 1.0  # 退化 profile → OUT_OF_RANGE


def test_seed_combat_state_populates_health_armor_ammo() -> None:
    db = _db()
    sid, uid, _r, _a = _seed(db)
    hot = InMemoryHotState()
    r = WeaponResolver(db, sid)
    n = seed_combat_state(db, hot, sid, r)
    assert n == 1
    st = hot.get_unit(uid)
    assert st is not None
    # 真實化交戰：seed 戰力 + 平台數；health 由戰力比 0.88 經效能曲線導出（非直接 88）。
    assert st["strength"] == pytest.approx(88.0)
    assert st["authorized_strength"] == pytest.approx(100.0)
    assert st["platform_count"] == 4
    assert st["health"] == pytest.approx(effectiveness_pct(0.88))
    assert st["armor_class"] == "LIGHT_VEHICLE"
    assert st["ammo"] == 8  # 主武器 ATGM 彈藥
    assert st["lat"] == pytest.approx(23.75)


def test_seed_combat_state_preserves_existing_progress() -> None:
    # 執行期重啟：熱狀態已有扣過的血量/彈藥 → seed 不得重置回 DB 初值（只同步座標）。
    db = _db()
    sid, uid, _r, _a = _seed(db)
    hot = InMemoryHotState()
    hot.put_unit(uid, {"health": 20.0, "ammo": 2, "armor_class": "LIGHT_VEHICLE"})
    seed_combat_state(db, hot, sid, WeaponResolver(db, sid))
    st = hot.get_unit(uid)
    assert st is not None
    assert st["health"] == pytest.approx(20.0)  # 保留戰損
    assert st["ammo"] == 2
    assert st["lat"] == pytest.approx(23.75)  # 座標仍同步


def test_make_engage_env_computes_range() -> None:
    hot = InMemoryHotState()
    hot.put_unit("s", {"lat": 23.75, "lng": 121.25})
    hot.put_unit("t", {"lat": 23.77, "lng": 121.27})
    env = make_engage_env(hot)("s", "t")
    assert env.los_clear is True
    assert 2000 < env.range_m < 4000  # ~2.7km


def test_make_engage_env_missing_coords_is_out_of_range() -> None:
    hot = InMemoryHotState()
    hot.put_unit("s", {"lat": 23.75, "lng": 121.25})
    env = make_engage_env(hot)("s", "ghost")
    assert env.range_m == float("inf")
