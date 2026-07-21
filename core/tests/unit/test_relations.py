"""陣營關係矩陣 + 整合（O6.8，SPEC §12.1 / ADR 006）。"""

from __future__ import annotations

import pytest

from app.engine.rng import DeterministicRNG
from app.factions import FactionRelations, Relation
from app.intel.seed_sensors import SEED_SENSORS
from app.intel.sensor import DetectionEnv, SensorProfile
from app.intel.sweep import SensorUnit, TargetUnit, sweep


def test_default_hostile_and_self_allied() -> None:
    r = FactionRelations()
    assert r.is_hostile("BLUE", "RED")  # 未宣告 → HOSTILE
    assert r.is_allied("BLUE", "BLUE")  # 己方
    assert not r.is_hostile("BLUE", "BLUE")


def test_symmetric() -> None:
    r = FactionRelations([("BLUE", "YELLOW", Relation.ALLIED)])
    assert r.is_allied("BLUE", "YELLOW") and r.is_allied("YELLOW", "BLUE")


def test_neutral_declared() -> None:
    r = FactionRelations([("RED", "YELLOW", Relation.NEUTRAL)])
    assert r.is_neutral("RED", "YELLOW")
    assert not r.is_hostile("RED", "YELLOW")


def test_wartime_change_emits_event() -> None:
    r = FactionRelations([("BLUE", "YELLOW", Relation.ALLIED)])
    ev = r.set_relation("BLUE", "YELLOW", Relation.HOSTILE, tick=42)  # 盟友反目
    assert r.is_hostile("BLUE", "YELLOW")
    assert ev.event_type == "FACTION_RELATION_CHANGED" and ev.tick == 42
    assert ev.ai_decision == {"factions": ["BLUE", "YELLOW"], "relation": "HOSTILE"}


def test_cannot_relate_self() -> None:
    with pytest.raises(ValueError, match="自己"):
        FactionRelations().set_relation("BLUE", "BLUE", Relation.HOSTILE, tick=1)


# ---- 整合：三方偵測 ----

_RADAR = SensorProfile.from_base_stats(SEED_SENSORS["GROUND_RADAR"])  # 8km


def _obs(uid: str, faction: str) -> SensorUnit:
    return SensorUnit(unit_id=uid, faction=faction, lat=23.75, lng=121.25, sensor=_RADAR)


def _tgt(uid: str, faction: str) -> TargetUnit:
    return TargetUnit(unit_id=uid, faction=faction, lat=23.75, lng=121.251)  # ~100m


def _env_clear(_o: SensorUnit, _t: TargetUnit) -> DetectionEnv:
    return DetectionEnv(los_clear=True)


def test_yellow_detects_both_blue_and_red() -> None:
    """三方混戰（全 HOSTILE 預設）：黃軍觀測者同時偵測藍與紅、不偵測己方。"""
    contacts = sweep(
        observers=[_obs("Y1", "YELLOW")],
        candidates=[_tgt("B1", "BLUE"), _tgt("R1", "RED"), _tgt("Y2", "YELLOW")],
        env_for=_env_clear,
        rng=DeterministicRNG(1, "sweep"),
        tick=1,
    )
    seen = {c.target_unit_id for c in contacts}
    assert {"B1", "R1"} <= seen  # 兩個敵對陣營都偵測到
    assert "Y2" not in seen  # 己方不偵測


def test_allies_do_not_detect_each_other() -> None:
    rel = FactionRelations([("BLUE", "YELLOW", Relation.ALLIED)])
    contacts = sweep(
        observers=[_obs("B1", "BLUE")],
        candidates=[_tgt("Y1", "YELLOW"), _tgt("R1", "RED")],
        env_for=_env_clear,
        rng=DeterministicRNG(1, "sweep"),
        tick=1,
        relations=rel,
    )
    seen = {c.target_unit_id for c in contacts}
    assert "R1" in seen  # 敵對 → 偵測
    assert "Y1" not in seen  # 盟軍 → 不成 contact
