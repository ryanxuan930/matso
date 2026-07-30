"""感測器接線（#97）：裝備→SensorProfile 解析、內建基本目視、偵測環境退化紀律。

偵測數學本身由 test_sensor.py / test_sweep.py 覆蓋；本檔只測「接線層」的決策。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.engine.sensor_wiring import INTRINSIC_OPTICAL, SensorResolver, make_detect_env
from app.intel.seed_sensors import SEED_SENSORS
from app.intel.sweep import SensorUnit, TargetUnit
from app.models.base import Base
from app.models.tables import EquipmentInstance, EquipmentTemplate, TacticalUnit, WargameSession


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _session_with_unit(db: Session, *, unit_id: str = "u1", faction: str = "BLUE") -> str:
    sess = WargameSession(id="s1", name="t", master_seed=1, current_weather={})
    db.add(sess)
    db.add(
        TacticalUnit(
            id=unit_id,
            session_id="s1",
            designation="A",
            unit_level="SQUAD",
            faction=faction,
            attributes={},
        )
    )
    db.commit()
    return "s1"


def test_unit_without_sensor_equipment_gets_intrinsic_optical(db: Session) -> None:
    """既有 session 的單位身上沒有 SENSOR 裝備——若不給內建目視，接線後仍是 0 contact。"""
    _session_with_unit(db)
    resolver = SensorResolver(db, "s1")

    assert resolver.sensor_for("u1") == INTRINSIC_OPTICAL
    assert resolver.faction_for("u1") == "BLUE"


def test_equipment_sensor_overrides_intrinsic_and_takes_longest_range(db: Session) -> None:
    """裝備感測器優先，且多件時取射程最遠者（雷達 8km > 內建目視 4km）。"""
    _session_with_unit(db)
    for name in ("ACOUSTIC_ARRAY", "GROUND_RADAR"):
        db.add(
            EquipmentTemplate(id=name, name=name, category="SENSOR", base_stats=SEED_SENSORS[name])
        )
        db.add(EquipmentInstance(id=f"i-{name}", template_id=name, owner_id="u1", quantity=1))
    db.commit()

    profile = SensorResolver(db, "s1").sensor_for("u1")

    assert profile is not None
    assert profile.sensor_kind == "RADAR"
    assert profile.max_range_m == 8000


def test_broken_sensor_base_stats_falls_back_not_crashes(db: Session) -> None:
    """一件壞 baseStats 不該讓整個單位變瞎——略過該件，退回內建目視。"""
    _session_with_unit(db)
    db.add(EquipmentTemplate(id="bad", name="bad", category="SENSOR", base_stats={"nope": 1}))
    db.add(EquipmentInstance(id="i-bad", template_id="bad", owner_id="u1", quantity=1))
    db.commit()

    assert SensorResolver(db, "s1").sensor_for("u1") == INTRINSIC_OPTICAL


def test_non_sensor_equipment_is_ignored(db: Session) -> None:
    """武器類裝備不得被誤認為感測器（category 過濾）。"""
    _session_with_unit(db)
    db.add(
        EquipmentTemplate(
            id="gun", name="gun", category="KINETIC", base_stats=SEED_SENSORS["GROUND_RADAR"]
        )
    )
    db.add(EquipmentInstance(id="i-gun", template_id="gun", owner_id="u1", quantity=1))
    db.commit()

    assert SensorResolver(db, "s1").sensor_for("u1") == INTRINSIC_OPTICAL


def test_unit_spawned_mid_session_is_resolved_lazily(db: Session) -> None:
    """MSEL `SPAWN_UNITS` 的增援：建構時 DB 裡還沒有它。

    沒有惰性補查 → `faction_for` 永遠回空字串 → STATE_DIFF 的每陣營投影（fail-closed）
    把增援整筆剔除，連自己人都看不到自己的援軍。
    """
    _session_with_unit(db)
    resolver = SensorResolver(db, "s1")
    assert resolver.faction_for("late") == ""  # 尚未出生

    db.add(
        TacticalUnit(
            id="late",
            session_id="s1",
            designation="R",
            unit_level="SQUAD",
            faction="RED",
            attributes={},
        )
    )
    db.commit()
    resolver.enable_lazy_lookup(sessionmaker(bind=db.get_bind()))

    assert resolver.faction_for("late") == "RED"
    assert resolver.sensor_for("late") == INTRINSIC_OPTICAL  # 感測器也一併補齊
    assert "RED" in resolver.factions()  # 新陣營要進得了觀測方名單


def test_lazy_lookup_refuses_units_of_other_sessions(db: Session) -> None:
    """補查是「查這一局」，不是「查全表」——別局的單位不得混進本局的投影。"""
    _session_with_unit(db)
    db.add(WargameSession(id="s2", name="other", master_seed=2, current_weather={}))
    db.add(
        TacticalUnit(
            id="foreign",
            session_id="s2",
            designation="X",
            unit_level="SQUAD",
            faction="RED",
            attributes={},
        )
    )
    db.commit()
    resolver = SensorResolver(db, "s1")
    resolver.enable_lazy_lookup(sessionmaker(bind=db.get_bind()))

    assert resolver.faction_for("foreign") == ""


def _pair() -> tuple[SensorUnit, TargetUnit]:
    observer = SensorUnit("o", "BLUE", 23.7, 121.0, INTRINSIC_OPTICAL)
    return observer, TargetUnit("t", "RED", 23.71, 121.0)


def test_detect_env_defaults_to_clear_without_gateway() -> None:
    env = make_detect_env()(*_pair())

    assert env.los_clear is True
    assert env.weather_modifier == 1.0


def test_detect_env_uses_terrain_los() -> None:
    class _Blocked:
        def has_los(self, _a: object, _b: object) -> object:
            return type("O", (), {"visible": False})()

    assert make_detect_env(_Blocked()).__call__(*_pair()).los_clear is False


def test_best_per_target_collapses_same_faction_observers() -> None:
    """同陣營十幾人看到同一敵人 → 只寫一列（取最佳 fidelity），別把 DB 打爆。"""
    from app.intel.sensor_system import _best_per_target
    from app.intel.sweep import Contact
    from app.models.enums import IntelFidelity

    raw = [
        Contact("BLUE", "r1", IntelFidelity.DETECTED, 5, 23.7, 121.0, 500.0),
        Contact("BLUE", "r1", IntelFidelity.IDENTIFIED, 5, 23.7, 121.0, 50.0),
        Contact("BLUE", "r2", IntelFidelity.CLASSIFIED, 5, 23.8, 121.0, 200.0),
        Contact("RED", "b1", IntelFidelity.DETECTED, 5, 23.6, 121.0, 500.0),
    ]

    out = _best_per_target(raw)

    assert len(out) == 3  # (BLUE,r1) 兩筆收斂成一筆
    blue_r1 = next(c for c in out if c.observer_faction == "BLUE" and c.target_unit_id == "r1")
    assert blue_r1.fidelity is IntelFidelity.IDENTIFIED  # 取最佳而非最後一筆
    assert out == sorted(out, key=lambda c: (c.observer_faction, c.target_unit_id))  # 確定性排序


def test_detect_env_survives_terrain_service_failure() -> None:
    """地形服務掛掉 → 退回可見。**不可**退成不可見：那會讓全場忽然集體變瞎。"""

    class _Broken:
        def has_los(self, _a: object, _b: object) -> object:
            raise RuntimeError("terrain down")

    assert make_detect_env(_Broken()).__call__(*_pair()).los_clear is True
