"""聯合兵種交戰裁決（SPEC_EXTEND P2）：resolve_combined_engagement 純函數。

驗證：武器組合加總、射程帶/裝甲類自動 weapon-target 匹配、逐武器彈藥、全數不合法 REJECTED、
能量守恆（毀傷夾在目標戰力內）、決定性可重播。
"""

from __future__ import annotations

from app.adjudication.combined import CombinedWeapon, resolve_combined_engagement
from app.adjudication.engagement import EnvSnapshot, Resolution, Target
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG

# 步槍：近距高命中、只殺步兵（pk ARMOR 缺→0）。機槍量大。
_RIFLE = WeaponProfile.from_base_stats(
    {
        "max_range_m": 600,
        "ph_by_range_band": [[100, 0.8], [600, 0.3]],
        "damage_by_armor_class": {"INFANTRY": 35},
        "pk_by_armor_class": {"INFANTRY": 0.5},
        "ammo_types": ["AMMO_556"],
        "rate_per_tick": 1.0,
    }
)
# 反戰車飛彈：長射程、只殺裝甲（pk INFANTRY 缺→0）。kinetic_kind ATGM → 政策視為反裝甲/重火力。
_ATGM = WeaponProfile.from_base_stats(
    {
        "max_range_m": 4000,
        "ph_by_range_band": [[500, 0.9], [4000, 0.6]],
        "damage_by_armor_class": {"ARMOR": 200},
        "pk_by_armor_class": {"ARMOR": 0.8},
        "ammo_types": ["AMMO_ATGM"],
        "rate_per_tick": 1.0,
        "kinetic_kind": "ATGM",
    }
)


def _weapons(rifle_ammo: int = 100, atgm_ammo: int = 8) -> list[CombinedWeapon]:
    return [
        CombinedWeapon("w-rifle", _RIFLE, quantity=7, ammo=rifle_ammo),
        CombinedWeapon("w-atgm", _ATGM, quantity=2, ammo=atgm_ammo),
    ]


def _target(armor: str, strength: float = 100.0, platforms: int = 10) -> Target:
    return Target(
        unit_id="t",
        armor_class=armor,
        health=100.0,
        current_strength=strength,
        authorized_strength=100.0,
        platform_count=platforms,
    )


def _env_at(range_m: float, los: bool = True) -> object:
    def env_for(_profile: WeaponProfile) -> EnvSnapshot:
        return EnvSnapshot(range_m=range_m, los_clear=los)

    return env_for


def _rng() -> DeterministicRNG:
    return DeterministicRNG(42, "adjudication")


def _pw(result: object, weapon_id: str) -> dict:
    per = result.events[0].ai_decision["per_weapon"]  # type: ignore[attr-defined]
    return next(p for p in per if p["weapon_id"] == weapon_id)


def test_combined_vs_infantry_rifle_dominates() -> None:
    # 打步兵：步槍（pk INFANTRY 0.5）造成主要毀傷；ATGM（pk INFANTRY 缺→0）貢獻 0。
    r = resolve_combined_engagement(
        _weapons(), "s", 1.0, _target("INFANTRY"), _env_at(300), _rng(), tick=1
    )
    assert r.status is Resolution.HIT
    assert _pw(r, "w-rifle")["strength_loss"] > 0.0
    assert _pw(r, "w-atgm")["strength_loss"] == 0.0  # 反裝甲打步兵無效
    assert _pw(r, "w-atgm")["status"] == "MISS"


def test_combined_vs_armor_atgm_dominates() -> None:
    # 打裝甲：ATGM（pk ARMOR 0.8）造成毀傷；步槍（pk ARMOR 缺→0）貢獻 0。
    r = resolve_combined_engagement(
        _weapons(), "s", 1.0, _target("ARMOR"), _env_at(300), _rng(), tick=1
    )
    assert r.status is Resolution.HIT
    assert _pw(r, "w-atgm")["strength_loss"] > 0.0
    assert _pw(r, "w-rifle")["strength_loss"] == 0.0  # 步槍打主戰車無效


def test_range_band_filters_short_range_weapon() -> None:
    # 1500m：步槍超射程（max 600）→ REJECTED OUT_OF_RANGE；ATGM（max 4000）照打。
    r = resolve_combined_engagement(
        _weapons(), "s", 1.0, _target("ARMOR"), _env_at(1500), _rng(), tick=1
    )
    assert _pw(r, "w-rifle")["status"] == "REJECTED"
    assert _pw(r, "w-rifle")["reason"] == "OUT_OF_RANGE"
    assert _pw(r, "w-atgm")["strength_loss"] > 0.0
    assert r.status is Resolution.HIT


def test_per_weapon_ammo_spent_and_capped() -> None:
    # 逐武器彈藥消耗；受各自 ammo 封頂（步槍 7 射手×rate 1 = 7 發，但只有 3 發彈 → 封頂 3）。
    r = resolve_combined_engagement(
        _weapons(rifle_ammo=3, atgm_ammo=8), "s", 1.0, _target("INFANTRY"), _env_at(300), _rng(), 1
    )
    assert r.ammo_spent_by_weapon is not None
    assert r.ammo_spent_by_weapon["w-rifle"] == 3  # 封頂在彈量
    assert r.ammo_spent == sum(r.ammo_spent_by_weapon.values())


def test_all_weapons_out_of_range_is_rejected() -> None:
    # 5km：兩武器皆超射程 → 合格集空 → REJECTED，不消耗彈藥。
    r = resolve_combined_engagement(
        _weapons(), "s", 1.0, _target("ARMOR"), _env_at(5000), _rng(), tick=1
    )
    assert r.status is Resolution.REJECTED
    assert r.reason == "OUT_OF_RANGE"
    assert r.ammo_spent == 0
    assert r.ammo_spent_by_weapon is None


def test_reject_reason_prefers_actionable() -> None:
    # 步槍在射程但無 LOS（NO_LOS）、ATGM 超射程（OUT_OF_RANGE）→ 取較可行動的 NO_LOS。
    def env_for(profile: WeaponProfile) -> EnvSnapshot:
        if profile.max_range_m <= 600:  # rifle：在射程、無視線
            return EnvSnapshot(range_m=400, los_clear=False)
        return EnvSnapshot(range_m=400, los_clear=True)  # atgm：也在射程

    # 讓 ATGM 超射程：改用只有步槍在射程、且無 LOS 的情境 → 全數不合格。
    weapons = [
        CombinedWeapon("w-rifle", _RIFLE, quantity=7, ammo=100),
        CombinedWeapon("w-atgm", _ATGM, quantity=2, ammo=8),
    ]

    def env2(profile: WeaponProfile) -> EnvSnapshot:
        if profile.max_range_m <= 600:
            return EnvSnapshot(range_m=400, los_clear=False)  # rifle: NO_LOS
        return EnvSnapshot(range_m=9000, los_clear=True)  # atgm: OUT_OF_RANGE

    r = resolve_combined_engagement(weapons, "s", 1.0, _target("ARMOR"), env2, _rng(), tick=1)
    assert r.status is Resolution.REJECTED
    assert r.reason == "NO_LOS"  # 優先序：NO_LOS > OUT_OF_RANGE


def test_energy_conservation_loss_capped_at_strength() -> None:
    # 目標僅剩 5 戰力、平台數 1（每平台戰力大）→ 大量火力不得把戰力扣成負值。
    tgt = _target("INFANTRY", strength=5.0, platforms=1)
    r = resolve_combined_engagement(_weapons(), "s", 1.0, tgt, _env_at(150), _rng(), tick=1)
    assert r.target_strength_after is not None
    assert 0.0 <= r.target_strength_after <= 5.0
    assert r.damage <= 5.0 + 1e-9


def test_deterministic_replay() -> None:
    # 相同 (輸入, rng seed) → 相同結果（決定性可重播）。
    a = resolve_combined_engagement(
        _weapons(), "s", 1.0, _target("INFANTRY"), _env_at(300), _rng(), tick=1
    )
    b = resolve_combined_engagement(
        _weapons(), "s", 1.0, _target("INFANTRY"), _env_at(300), _rng(), tick=1
    )
    assert a.damage == b.damage
    assert a.target_strength_after == b.target_strength_after
    assert a.ammo_spent_by_weapon == b.ammo_spent_by_weapon


def _atgm_only() -> list[CombinedWeapon]:
    return [CombinedWeapon("w-atgm", _ATGM, quantity=2, ammo=8)]


# ── SPEC_EXTEND P3：火力政策（fire_policy）─────────────────────────────────


def test_policy_small_arms_only_holds_atgm() -> None:
    # SMALL_ARMS_ONLY：反裝甲（ATGM）保留不發射、不耗彈；步槍照打。
    r = resolve_combined_engagement(
        _weapons(),
        "s",
        1.0,
        _target("INFANTRY"),
        _env_at(300),
        _rng(),
        1,
        fire_policy="SMALL_ARMS_ONLY",
    )
    assert _pw(r, "w-atgm")["status"] == "HELD"
    assert r.ammo_spent_by_weapon is not None
    assert "w-atgm" not in r.ammo_spent_by_weapon  # 未耗彈
    assert _pw(r, "w-rifle")["strength_loss"] > 0.0


def test_policy_anti_armor_hold_vs_infantry_holds_atgm() -> None:
    # ANTI_ARMOR_HOLD 對步兵：ATGM 對步兵無效（pk 0）→ 保留（不浪費反裝甲）；步槍照打。
    r = resolve_combined_engagement(
        _weapons(),
        "s",
        1.0,
        _target("INFANTRY"),
        _env_at(300),
        _rng(),
        1,
        fire_policy="ANTI_ARMOR_HOLD",
    )
    assert _pw(r, "w-atgm")["status"] == "HELD"
    assert _pw(r, "w-rifle")["strength_loss"] > 0.0


def test_policy_anti_armor_hold_vs_armor_fires_atgm() -> None:
    # ANTI_ARMOR_HOLD 對裝甲：ATGM 有效（pk 0.8）→ 發射；步槍（非反裝甲）照打（但 pk 0 → 0 毀傷）。
    r = resolve_combined_engagement(
        _weapons(),
        "s",
        1.0,
        _target("ARMOR"),
        _env_at(300),
        _rng(),
        1,
        fire_policy="ANTI_ARMOR_HOLD",
    )
    assert _pw(r, "w-atgm")["status"] != "HELD"
    assert _pw(r, "w-atgm")["strength_loss"] > 0.0
    assert r.status is Resolution.HIT


def test_policy_holds_all_weapons_is_rejected_hold_fire() -> None:
    # 只有 ATGM 的單位 + SMALL_ARMS_ONLY → 全被保留 → REJECTED HOLD_FIRE，不耗彈。
    r = resolve_combined_engagement(
        _atgm_only(),
        "s",
        1.0,
        _target("ARMOR"),
        _env_at(300),
        _rng(),
        1,
        fire_policy="SMALL_ARMS_ONLY",
    )
    assert r.status is Resolution.REJECTED
    assert r.reason == "HOLD_FIRE"
    assert r.ammo_spent == 0


def test_policy_free_is_default_all_fire() -> None:
    # 預設 FREE：反裝甲對步兵仍發射耗彈（0 毀傷）——驗證 P3 未破壞 P2 預設行為。
    r = resolve_combined_engagement(
        _weapons(), "s", 1.0, _target("INFANTRY"), _env_at(300), _rng(), 1
    )
    assert r.ammo_spent_by_weapon is not None
    assert "w-atgm" in r.ammo_spent_by_weapon  # FREE 下 ATGM 仍發射耗彈


def test_rejected_event_has_per_weapon_reason_detail() -> None:
    # 全數不可打 → 事件帶 reason_detail 逐武器原因彙總（供戰況 feed 顯示為何整組不能打）。
    weapons = [
        CombinedWeapon("w-rifle", _RIFLE, quantity=7, ammo=100),  # 在射程但無 LOS → 無視線
        CombinedWeapon("w-atgm", _ATGM, quantity=2, ammo=8),  # 超射程
        CombinedWeapon("w-empty", _RIFLE, quantity=1, ammo=0),  # 無彈藥
    ]

    def env(profile: WeaponProfile) -> EnvSnapshot:
        if profile.max_range_m <= 600:  # rifle：在射程、無視線
            return EnvSnapshot(range_m=400, los_clear=False)
        return EnvSnapshot(range_m=9000, los_clear=True)  # atgm：超射程（max 4000）

    r = resolve_combined_engagement(weapons, "s", 1.0, _target("ARMOR"), env, _rng(), tick=1)
    assert r.status is Resolution.REJECTED
    detail = r.events[0].ai_decision["reason_detail"]
    assert "無視線" in detail and "超射程" in detail and "無彈藥" in detail


def test_weapon_order_is_deterministic_dispersion() -> None:
    # 每合格武器恰一次 dispersion 抽樣（順序＝清單序）：per_weapon 明細帶各自 dispersion。
    r = resolve_combined_engagement(
        _weapons(), "s", 1.0, _target("INFANTRY"), _env_at(300), _rng(), tick=1
    )
    disps = [p.get("dispersion") for p in r.events[0].ai_decision["per_weapon"]]
    assert all(d is not None for d in disps)
    assert all(0.8 <= d <= 1.2 for d in disps)
