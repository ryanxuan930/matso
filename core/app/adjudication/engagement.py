"""交戰裁決（SPEC §7.1）——P1 原則的心臟：**AI 永不裁決物理**。

純同步純函數、frozen dataclass 輸入輸出、不碰 DB/Redis/時鐘/RPC（HOW_TO §3、§4.2）。
所有隨機性經注入的 `DeterministicRNG`（stream="adjudication"），確保可重播。

管線（§7.1）：
  [a] 合法性：彈藥>0？射程 ∈ 包絡？LOS 或間瞄可達？
  [b] P_hit = base_ph(weapon, range) × terrain_cover × weather × suppression × posture（夾在 [0,1]）
  [c] roll = rng.random()
  [d] 命中 → damage = damage_table(weapon, target_armor) → 更新 health
  [e] 產生 ENGAGEMENT_RESOLVED 事件（含所有中間係數，供 AAR 溯源）
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG
from app.state.ledger import LedgerEvent


class Resolution(enum.StrEnum):
    HIT = "HIT"
    MISS = "MISS"
    REJECTED = "REJECTED"  # 合法性未過（彈藥/射程/視線）——AI 不介入，物理直接拒絕


@dataclass(frozen=True, slots=True)
class Shooter:
    unit_id: str
    ammo_count: int


@dataclass(frozen=True, slots=True)
class Target:
    unit_id: str
    armor_class: str
    health: float


@dataclass(frozen=True, slots=True)
class EnvSnapshot:
    """Kernel 事先收集的交戰環境（地形/天氣係數 + 幾何事實）。裁決函數不做任何 RPC。

    係數皆為乘法修正，預設 1.0（退化為 base）；O5 天氣模組會填入真實 weather_modifier。
    """

    range_m: float
    los_clear: bool  # LOS 或間瞄彈道可達（由 Kernel 查 terrain 得出）
    terrain_cover_modifier: float = 1.0
    weather_modifier: float = 1.0
    shooter_suppression_modifier: float = 1.0
    target_posture_modifier: float = 1.0


@dataclass(frozen=True, slots=True)
class EngagementResult:
    status: Resolution
    p_hit: float
    roll: float | None  # 合法性未過（REJECTED）時不擲骰 → None
    damage: float
    target_health_after: float
    coefficients: dict[str, float]
    reason: str | None  # REJECTED 原因 code（NO_AMMO / OUT_OF_RANGE / NO_LOS）
    events: list[LedgerEvent] = field(default_factory=list)


def resolve_engagement(
    weapon: WeaponProfile,
    shooter: Shooter,
    target: Target,
    env: EnvSnapshot,
    rng: DeterministicRNG,
    tick: int,
) -> EngagementResult:
    """裁決單次交戰。確定性：相同 (輸入, rng 狀態) → 相同結果。"""
    # [a] 合法性——任一不過即 REJECTED（不擲骰，不消耗 RNG）
    reason = _legality_reason(weapon, shooter, env)
    if reason is not None:
        return _rejected(shooter, target, reason, tick)

    # [b] 命中機率（各係數為乘法修正；夾在 [0,1]）
    base = weapon.base_ph(env.range_m)
    coefficients = {
        "base_ph": base,
        "terrain_cover": env.terrain_cover_modifier,
        "weather": env.weather_modifier,
        "suppression": env.shooter_suppression_modifier,
        "target_posture": env.target_posture_modifier,
    }
    p_hit = _clamp01(
        base
        * env.terrain_cover_modifier
        * env.weather_modifier
        * env.shooter_suppression_modifier
        * env.target_posture_modifier
    )

    # [c] 擲骰
    roll = rng.random()
    hit = roll < p_hit

    # [d] 傷害 / 血量
    damage = weapon.damage_against(target.armor_class) if hit else 0.0
    health_after = max(0.0, target.health - damage)
    status = Resolution.HIT if hit else Resolution.MISS

    # [e] 事件（含所有中間係數，供 AAR）
    event = LedgerEvent(
        event_type="ENGAGEMENT_RESOLVED",
        tick=tick,
        initiator_id=shooter.unit_id,
        target_id=target.unit_id,
        terrain_modifier=env.terrain_cover_modifier,
        damage_calc=damage,
        ai_decision={
            "status": status.value,
            "p_hit": p_hit,
            "roll": roll,
            "hit": hit,
            "coefficients": coefficients,
            "target_health_after": health_after,
        },
    )
    return EngagementResult(
        status=status,
        p_hit=p_hit,
        roll=roll,
        damage=damage,
        target_health_after=health_after,
        coefficients=coefficients,
        reason=None,
        events=[event],
    )


def _legality_reason(weapon: WeaponProfile, shooter: Shooter, env: EnvSnapshot) -> str | None:
    if shooter.ammo_count <= 0:
        return "NO_AMMO"
    if not weapon.in_envelope(env.range_m):
        return "OUT_OF_RANGE"
    if not env.los_clear and not weapon.indirect_fire:
        return "NO_LOS"  # 直瞄需 LOS；間瞄（indirect_fire）不需
    return None


def _rejected(shooter: Shooter, target: Target, reason: str, tick: int) -> EngagementResult:
    event = LedgerEvent(
        event_type="ENGAGEMENT_RESOLVED",
        tick=tick,
        initiator_id=shooter.unit_id,
        target_id=target.unit_id,
        damage_calc=0.0,
        ai_decision={"status": Resolution.REJECTED.value, "reason": reason},
    )
    return EngagementResult(
        status=Resolution.REJECTED,
        p_hit=0.0,
        roll=None,
        damage=0.0,
        target_health_after=target.health,
        coefficients={},
        reason=reason,
        events=[event],
    )


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))
