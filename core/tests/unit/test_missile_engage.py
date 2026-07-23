"""飛彈接戰可行性（#飛彈）：可變軌僅判射程；不可變軌（彈道）須拋物線淨空。"""

from __future__ import annotations

from app.adjudication.engagement import (
    EnvSnapshot,
    Resolution,
    Shooter,
    Target,
    resolve_engagement,
)
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG

_BASE = {
    "max_range_m": 5000,
    "ph_by_range_band": [[500, 0.9], [5000, 0.85]],
    "damage_by_armor_class": {"ARMOR": 100},
    "pk_by_armor_class": {"ARMOR": 0.8},
    "ammo_types": ["X"],
    "missile_kind": "CRUISE",
}


def _cruise() -> WeaponProfile:
    return WeaponProfile.from_base_stats({**_BASE, "missile_kind": "CRUISE", "maneuverable": True})


def _ballistic() -> WeaponProfile:
    return WeaponProfile.from_base_stats(
        {**_BASE, "missile_kind": "BALLISTIC", "maneuverable": False}
    )


def _tgt() -> Target:
    return Target("t", "ARMOR", current_strength=100.0, authorized_strength=100.0, platform_count=1)


def _rng() -> DeterministicRNG:
    return DeterministicRNG(1, "adjudication")


def test_profile_flags() -> None:
    assert _cruise().missile and _cruise().maneuverable and not _cruise().ballistic
    assert _ballistic().missile and not _ballistic().maneuverable and _ballistic().ballistic


def test_cruise_ignores_los_and_trajectory() -> None:
    # 巡弋：即使無 LOS、軌跡被擋，仍只判射程 → 在射程內即可接戰。
    env = EnvSnapshot(range_m=3000, los_clear=False, trajectory_clear=False)
    r = resolve_engagement(_cruise(), Shooter("s", 5), _tgt(), env, _rng(), 0)
    assert r.status is not Resolution.REJECTED


def test_ballistic_blocked_when_trajectory_obstructed() -> None:
    env = EnvSnapshot(range_m=3000, los_clear=False, trajectory_clear=False)
    r = resolve_engagement(_ballistic(), Shooter("s", 5), _tgt(), env, _rng(), 0)
    assert r.status is Resolution.REJECTED and r.reason == "TRAJECTORY_BLOCKED"


def test_ballistic_ok_when_trajectory_clear() -> None:
    env = EnvSnapshot(range_m=3000, los_clear=False, trajectory_clear=True)
    r = resolve_engagement(_ballistic(), Shooter("s", 5), _tgt(), env, _rng(), 0)
    assert r.status is not Resolution.REJECTED


def test_out_of_range_rejected_regardless() -> None:
    env = EnvSnapshot(range_m=9000, los_clear=True, trajectory_clear=True)
    assert (
        resolve_engagement(_cruise(), Shooter("s", 5), _tgt(), env, _rng(), 0).reason
        == "OUT_OF_RANGE"
    )
