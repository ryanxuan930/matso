"""交戰裁決 property tests（O3.2，Hypothesis）——SPEC §7.1 每條公式的性質。

驗收要點（TASKS O3.2）：距離↑→P_hit 單調不增、係數=1 退化為 base、彈藥=0 必 REJECTED。
另含：決定性、命中/未命中傷害、p_hit 夾在 [0,1]、間瞄免 LOS、事件內容。
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.adjudication.engagement import (
    EnvSnapshot,
    Resolution,
    Shooter,
    Target,
    resolve_engagement,
)
from app.adjudication.seed_weapons import SEED_WEAPONS
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG

_RIFLE = WeaponProfile.from_base_stats(SEED_WEAPONS["RIFLE_556"])
_ATGM = WeaponProfile.from_base_stats(SEED_WEAPONS["AUTOCANNON_30"])


def _rng() -> DeterministicRNG:
    return DeterministicRNG(20260720, "adjudication")


def _env(range_m: float, **kw: float | bool) -> EnvSnapshot:
    return EnvSnapshot(range_m=range_m, los_clear=True, **kw)  # type: ignore[arg-type]


def _shooter(ammo: int = 10) -> Shooter:
    return Shooter(unit_id="s", ammo_count=ammo)


def _target(health: float = 100.0, armor: str = "INFANTRY") -> Target:
    return Target(unit_id="t", armor_class=armor, health=health)


def _weapon_const_ph(ph: float, dmg: float = 40.0) -> WeaponProfile:
    return WeaponProfile.from_base_stats(
        {
            "max_range_m": 5000,
            "min_range_m": 0,
            "indirect_fire": False,
            "ph_by_range_band": [[100, ph], [5000, ph]],
            "damage_by_armor_class": {"INFANTRY": dmg},
            "ammo_types": ["X"],
        }
    )


# ---------------- 單調性：距離↑ → P_hit 不增 ----------------


@given(
    r1=st.floats(min_value=1, max_value=3000),
    r2=st.floats(min_value=1, max_value=3000),
)
def test_phit_monotonic_non_increasing_in_range(r1: float, r2: float) -> None:
    lo, hi = sorted((r1, r2))
    p_lo = resolve_engagement(_ATGM, _shooter(), _target(), _env(lo), _rng(), 0).p_hit
    p_hi = resolve_engagement(_ATGM, _shooter(), _target(), _env(hi), _rng(), 0).p_hit
    assert p_lo >= p_hi - 1e-9  # 較遠不得更容易命中


@given(r1=st.floats(min_value=0, max_value=6000), r2=st.floats(min_value=0, max_value=6000))
def test_base_ph_monotonic(r1: float, r2: float) -> None:
    lo, hi = sorted((r1, r2))
    assert _RIFLE.base_ph(lo) >= _RIFLE.base_ph(hi) - 1e-9


# ---------------- 係數=1 退化為 base ----------------


@given(range_m=st.floats(min_value=1, max_value=2999))
def test_unit_coefficients_reduce_to_base_ph(range_m: float) -> None:
    result = resolve_engagement(_ATGM, _shooter(), _target(), _env(range_m), _rng(), 0)
    assert result.p_hit == _ATGM.base_ph(range_m)


# ---------------- 彈藥=0 必 REJECTED ----------------


@given(
    range_m=st.floats(min_value=0, max_value=6000),
    los=st.booleans(),
)
def test_zero_ammo_always_rejected(range_m: float, los: bool) -> None:
    env = EnvSnapshot(range_m=range_m, los_clear=los)
    result = resolve_engagement(_ATGM, _shooter(ammo=0), _target(), env, _rng(), 0)
    assert result.status is Resolution.REJECTED
    assert result.reason == "NO_AMMO"
    assert result.roll is None  # 不擲骰
    assert result.damage == 0.0


# ---------------- 射程包絡 ----------------


@given(range_m=st.floats(min_value=3001, max_value=100_000))
def test_beyond_max_range_rejected(range_m: float) -> None:
    result = resolve_engagement(_ATGM, _shooter(), _target(), _env(range_m), _rng(), 0)
    assert result.status is Resolution.REJECTED
    assert result.reason == "OUT_OF_RANGE"


def test_below_min_range_rejected() -> None:
    atgm = WeaponProfile.from_base_stats(SEED_WEAPONS["ATGM"])  # min_range 200
    result = resolve_engagement(atgm, _shooter(), _target(), _env(100), _rng(), 0)
    assert result.reason == "OUT_OF_RANGE"


# ---------------- 視線 ----------------


def test_direct_fire_without_los_rejected() -> None:
    env = EnvSnapshot(range_m=1000, los_clear=False)
    result = resolve_engagement(_ATGM, _shooter(), _target(), env, _rng(), 0)
    assert result.reason == "NO_LOS"


def test_indirect_fire_ignores_los() -> None:
    indirect = WeaponProfile.from_base_stats(
        {
            "max_range_m": 8000,
            "indirect_fire": True,
            "ph_by_range_band": [[8000, 0.5]],
            "damage_by_armor_class": {"INFANTRY": 40},
            "ammo_types": ["SHELL"],
        }
    )
    env = EnvSnapshot(range_m=4000, los_clear=False)
    result = resolve_engagement(indirect, _shooter(), _target(), env, _rng(), 0)
    assert result.status is not Resolution.REJECTED  # 間瞄不需 LOS


# ---------------- 命中 / 傷害 ----------------


def test_guaranteed_hit_applies_damage() -> None:
    weapon = _weapon_const_ph(1.0, dmg=30.0)  # p_hit=1 → 必中
    result = resolve_engagement(weapon, _shooter(), _target(health=100), _env(500), _rng(), 5)
    assert result.status is Resolution.HIT
    assert result.damage == 30.0
    assert result.target_health_after == 70.0


def test_guaranteed_miss_no_damage() -> None:
    weapon = _weapon_const_ph(0.0)  # p_hit=0 → 必不中
    result = resolve_engagement(weapon, _shooter(), _target(health=100), _env(500), _rng(), 0)
    assert result.status is Resolution.MISS
    assert result.damage == 0.0
    assert result.target_health_after == 100.0


def test_damage_cannot_drive_health_below_zero() -> None:
    weapon = _weapon_const_ph(1.0, dmg=500.0)
    result = resolve_engagement(weapon, _shooter(), _target(health=40), _env(500), _rng(), 0)
    assert result.target_health_after == 0.0


# ---------------- p_hit 夾在 [0,1] ----------------


@given(
    terrain=st.floats(min_value=0, max_value=5),
    weather=st.floats(min_value=0, max_value=5),
    posture=st.floats(min_value=0, max_value=5),
)
def test_phit_clamped_unit_interval(terrain: float, weather: float, posture: float) -> None:
    env = EnvSnapshot(
        range_m=500,
        los_clear=True,
        terrain_cover_modifier=terrain,
        weather_modifier=weather,
        target_posture_modifier=posture,
    )
    result = resolve_engagement(_ATGM, _shooter(), _target(), env, _rng(), 0)
    assert 0.0 <= result.p_hit <= 1.0


# ---------------- 決定性 ----------------


def test_deterministic_same_seed_same_outcome() -> None:
    weapon = _weapon_const_ph(0.5)
    a = resolve_engagement(weapon, _shooter(), _target(), _env(500), _rng(), 0)
    b = resolve_engagement(weapon, _shooter(), _target(), _env(500), _rng(), 0)
    assert a.roll == b.roll
    assert a.status == b.status
    assert a.damage == b.damage


def test_rng_sequence_advances_across_engagements() -> None:
    weapon = _weapon_const_ph(0.5)
    rng = _rng()
    r1 = resolve_engagement(weapon, _shooter(), _target(), _env(500), rng, 0)
    r2 = resolve_engagement(weapon, _shooter(), _target(), _env(500), rng, 1)
    assert r1.roll != r2.roll  # 同一 rng 連續兩次 → 不同骰值


# ---------------- 事件 ----------------


def test_hit_event_carries_coefficients() -> None:
    weapon = _weapon_const_ph(1.0, dmg=30.0)
    result = resolve_engagement(weapon, _shooter(), _target(), _env(500), _rng(), 9)
    assert len(result.events) == 1
    ev = result.events[0]
    assert ev.event_type == "ENGAGEMENT_RESOLVED"
    assert ev.tick == 9
    assert ev.initiator_id == "s" and ev.target_id == "t"
    assert ev.damage_calc == 30.0
    assert ev.ai_decision["coefficients"]["base_ph"] == 1.0


def test_rejected_event_records_reason() -> None:
    result = resolve_engagement(_ATGM, _shooter(ammo=0), _target(), _env(500), _rng(), 0)
    ev = result.events[0]
    assert ev.ai_decision["status"] == "REJECTED"
    assert ev.ai_decision["reason"] == "NO_AMMO"
    assert ev.damage_calc == 0.0
