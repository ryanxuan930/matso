"""真實化交戰（Phase 1）：以單位戰力/平台數做漸進傷亡，取代 flat 100->50->0。"""

from __future__ import annotations

import pytest

from app.adjudication.engagement import (
    EnvSnapshot,
    Resolution,
    Shooter,
    Target,
    resolve_engagement,
)
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG

# 必中的 ATGM（ph=1.0），對 ARMOR pk=0.8（每發期望擊殺 0.8 個平台份量）。
_ATGM = WeaponProfile.from_base_stats(
    {
        "max_range_m": 5000,
        "ph_by_range_band": [[100, 1.0], [5000, 1.0]],
        "damage_by_armor_class": {"ARMOR": 80},
        "pk_by_armor_class": {"ARMOR": 0.8},
        "ammo_types": ["X"],
    }
)


def _env() -> EnvSnapshot:
    return EnvSnapshot(range_m=1000, los_clear=True)


def _company(strength: float) -> Target:
    return Target(
        unit_id="t",
        armor_class="ARMOR",
        current_strength=strength,
        authorized_strength=100.0,
        platform_count=14,
    )


def _singleton(strength: float) -> Target:
    return Target(
        unit_id="t",
        armor_class="ARMOR",
        current_strength=strength,
        authorized_strength=100.0,
        platform_count=1,
    )


def test_company_survives_many_hits_gradual_attrition() -> None:
    # 14 平台連：每發 cp_per_platform=100/14≈7.14，loss≈5.7 → 三發後仍 >80（漸進消耗）。
    rng = DeterministicRNG(1, "adjudication")
    s = 100.0
    for _ in range(3):
        r = resolve_engagement(_ATGM, Shooter("s", 10), _company(s), _env(), rng, 0)
        assert r.status is Resolution.HIT
        assert r.target_strength_after is not None
        s = r.target_strength_after
    assert s > 80.0  # 兩發不會死；漸進


def test_singleton_dies_in_two_hits() -> None:
    # 單體：cp_per_platform=100，pk 0.8 → 每發扣 80 → 兩發歸零。
    rng = DeterministicRNG(1, "adjudication")
    s, hits = 100.0, 0
    while s > 0.0 and hits < 6:
        r = resolve_engagement(_ATGM, Shooter("s", 10), _singleton(s), _env(), rng, 0)
        s = r.target_strength_after or 0.0
        hits += 1
    assert hits == 2


def test_strength_monotonic_nonincreasing_and_health_in_range() -> None:
    rng = DeterministicRNG(3, "adjudication")
    s, prev = 100.0, 101.0
    for _ in range(20):
        r = resolve_engagement(_ATGM, Shooter("s", 100), _company(s), _env(), rng, 0)
        s = r.target_strength_after if r.target_strength_after is not None else s
        assert s <= prev + 1e-9  # 非遞增
        assert 0.0 <= r.target_health_after <= 100.0
        prev = s


def test_health_derived_from_strength_not_flat() -> None:
    # 命中後 health 是效能%（由戰力比導出），非「100 減 damage」。
    rng = DeterministicRNG(1, "adjudication")
    r = resolve_engagement(_ATGM, Shooter("s", 10), _company(100.0), _env(), rng, 0)
    # strength 100->94.3；ratio 0.943 -> 效能 ~0.96 -> health ~96（遠高於 flat 的低值）。
    assert r.target_health_after > 90.0
    assert r.target_strength_after == pytest.approx(100.0 - 0.8 * (100.0 / 14), abs=1e-6)


def test_flat_fallback_when_no_strength_fields() -> None:
    # 未給 strength 三欄 → 退回舊 flat 傷害（相容既有種子/測試）。
    flat = WeaponProfile.from_base_stats(
        {
            "max_range_m": 5000,
            "ph_by_range_band": [[100, 1.0], [5000, 1.0]],
            "damage_by_armor_class": {"ARMOR": 40},
            "ammo_types": ["X"],
        }
    )
    rng = DeterministicRNG(1, "adjudication")
    r = resolve_engagement(flat, Shooter("s", 10), Target("t", "ARMOR", 100.0), _env(), rng, 0)
    assert r.target_strength_after is None
    assert r.target_health_after == pytest.approx(60.0)  # 100 - 40 flat


def test_miss_leaves_strength_unchanged() -> None:
    # p_hit<1 且 roll>=p_hit → MISS：strength 不變。
    weak = WeaponProfile.from_base_stats(
        {
            "max_range_m": 5000,
            "ph_by_range_band": [[100, 0.0], [5000, 0.0]],  # 必不中
            "damage_by_armor_class": {"ARMOR": 80},
            "pk_by_armor_class": {"ARMOR": 0.8},
            "ammo_types": ["X"],
        }
    )
    rng = DeterministicRNG(1, "adjudication")
    r = resolve_engagement(weak, Shooter("s", 10), _company(88.0), _env(), rng, 0)
    assert r.status is Resolution.MISS
    assert r.target_strength_after == pytest.approx(88.0)
