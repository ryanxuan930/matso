"""squad 齊射（#30）：quantity>1 → 全員射擊火力容量路徑（決定性、彈藥封頂、單發相容）。"""

from __future__ import annotations

from app.adjudication.engagement import (
    EnvSnapshot,
    Shooter,
    Target,
    resolve_engagement,
)
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG

# 步槍：ph=0.5、對 INFANTRY pk=0.4、每 tick 射速 3。
_RIFLE = WeaponProfile.from_base_stats(
    {
        "max_range_m": 500,
        "ph_by_range_band": [[100, 0.5], [500, 0.5]],
        "damage_by_armor_class": {"INFANTRY": 50},
        "pk_by_armor_class": {"INFANTRY": 0.4},
        "ammo_types": ["5.56"],
        "rate_per_tick": 3.0,
    }
)


def _env() -> EnvSnapshot:
    return EnvSnapshot(range_m=200, los_clear=True)


def _squad(strength: float = 100.0) -> Target:
    return Target(
        unit_id="t",
        armor_class="INFANTRY",
        current_strength=strength,
        authorized_strength=100.0,
        platform_count=9,
    )


def test_squad_volley_hits_harder_than_single_rifle() -> None:
    # 同一目標：7 支步槍齊射 vs 1 支步槍單發 → 齊射造成更大戰力損失。
    single = resolve_engagement(
        _RIFLE,
        Shooter("s", 999, quantity=1),
        _squad(),
        _env(),
        DeterministicRNG(3, "adjudication"),
        0,
    )
    volley = resolve_engagement(
        _RIFLE,
        Shooter("s", 999, quantity=7),
        _squad(),
        _env(),
        DeterministicRNG(3, "adjudication"),
        0,
    )
    assert volley.damage > single.damage
    assert volley.coefficients.get("mode", None) is None  # single has no mode
    # 齊射事件標記 mode=VOLLEY。
    assert volley.events[0].ai_decision["mode"] == "VOLLEY"


def test_volley_deterministic_replay() -> None:
    r1 = resolve_engagement(
        _RIFLE,
        Shooter("s", 999, quantity=7),
        _squad(),
        _env(),
        DeterministicRNG(9, "adjudication"),
        0,
    )
    r2 = resolve_engagement(
        _RIFLE,
        Shooter("s", 999, quantity=7),
        _squad(),
        _env(),
        DeterministicRNG(9, "adjudication"),
        0,
    )
    assert r1.damage == r2.damage and r1.target_strength_after == r2.target_strength_after


def test_volley_ammo_capped() -> None:
    # 只剩 2 發彈藥：發射數封頂 2 → ammo_spent<=2，損失遠小於彈藥充足時。
    limited = resolve_engagement(
        _RIFLE,
        Shooter("s", 2, quantity=7),
        _squad(),
        _env(),
        DeterministicRNG(5, "adjudication"),
        0,
    )
    ample = resolve_engagement(
        _RIFLE,
        Shooter("s", 999, quantity=7),
        _squad(),
        _env(),
        DeterministicRNG(5, "adjudication"),
        0,
    )
    assert limited.ammo_spent <= 2
    assert limited.damage < ample.damage


def test_volley_shooter_effectiveness_scales_fire() -> None:
    # 半戰力射手（effectiveness=0.5）齊射 → 損失小於滿編 effectiveness=1.0。
    weak = resolve_engagement(
        _RIFLE,
        Shooter("s", 999, quantity=7, effectiveness=0.5),
        _squad(),
        _env(),
        DeterministicRNG(7, "adjudication"),
        0,
    )
    strong = resolve_engagement(
        _RIFLE,
        Shooter("s", 999, quantity=7, effectiveness=1.0),
        _squad(),
        _env(),
        DeterministicRNG(7, "adjudication"),
        0,
    )
    assert weak.damage < strong.damage


def test_quantity_one_uses_single_shot_path() -> None:
    # quantity=1 → 走既有單發路徑（有 roll、ammo_spent=1），golden replay 不變。
    r = resolve_engagement(
        _RIFLE,
        Shooter("s", 999, quantity=1),
        _squad(),
        _env(),
        DeterministicRNG(1, "adjudication"),
        0,
    )
    assert r.roll is not None and r.ammo_spent == 1
    assert "mode" not in r.events[0].ai_decision


def test_volley_flat_target_falls_back_to_single_shot() -> None:
    # 目標無 strength 三欄（flat health）→ 即使 quantity>1 也退回單發（安全）。
    flat = Target(unit_id="t", armor_class="INFANTRY", health=100.0)
    r = resolve_engagement(
        _RIFLE, Shooter("s", 999, quantity=7), flat, _env(), DeterministicRNG(1, "adjudication"), 0
    )
    assert r.roll is not None  # 單發路徑有 roll
