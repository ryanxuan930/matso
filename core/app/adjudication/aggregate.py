"""聚合裁決（Lanchester；SPEC §7.1 末段）——營級以上的大部隊戰鬥。

避免逐一單兵計算的效能爆炸：以隨機化 Lanchester 方程逐 tick 遞減雙方戰力
（aimed-fire square law / area-fire linear law 混合，係數由單位屬性推導）。

同 engagement 的紀律：**純同步純函數、frozen dataclass、不碰 DB/Redis/時鐘/RPC**，
隨機性經注入的 DeterministicRNG（stream="adjudication"）。**能量守恆**：每 tick 每側戰損
夾在 [0, 當前戰力] → 戰力恆 ≥ 0、單調不增 → 累計總戰損 ≤ 初始總戰力。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.rng import DeterministicRNG
from app.factions import FactionRelations
from app.models.enums import UnitLevel
from app.state.ledger import LedgerEvent

# linear-law（area fire）正規化尺度（v0 佔位；由校準/想定調整）
_AREA_SCALE = 100.0

# 單位規模排名：THEATER 最大（0）… INDIVIDUAL 最小。數字越小＝單位越大。
_SIZE_RANK = {level: rank for rank, level in enumerate(UnitLevel)}


def should_aggregate(level: UnitLevel, threshold: UnitLevel = UnitLevel.BATTALION) -> bool:
    """此單位是否走聚合裁決：規模 ≥ threshold（含）。閾值來自 scenario 的
    aggregate_adjudication_level（預設 BATTALION）。"""
    return _SIZE_RANK[level] <= _SIZE_RANK[threshold]


@dataclass(frozen=True, slots=True)
class AggregateForce:
    unit_id: str
    faction: str
    strength: float  # 當前戰力（≥0）
    lethality: float  # 攻擊係數（每單位戰力對敵的殺傷率）


@dataclass(frozen=True, slots=True)
class AggregateEnv:
    """混合律比例與環境係數。aimed_fraction=1 純 square law、0 純 linear law。"""

    aimed_fraction: float = 1.0
    terrain_modifier: float = 1.0
    weather_modifier: float = 1.0
    variance: float = 0.0  # 隨機化幅度（0=確定；每 tick 係數 ×[1-variance, 1+variance)）


@dataclass(frozen=True, slots=True)
class AggregateResult:
    """兩力交戰結果（中性 a/b；SPEC §12.1/ADR 006：不綁 blue/red）。"""

    a_strength_after: float
    b_strength_after: float
    a_loss: float
    b_loss: float
    coefficients: dict[str, float]
    events: list[LedgerEvent]


def _pair_event(
    force_a: AggregateForce,
    force_b: AggregateForce,
    env: AggregateEnv,
    a_loss: float,
    b_loss: float,
    coefficients: dict[str, float],
    tick: int,
) -> LedgerEvent:
    return LedgerEvent(
        event_type="AGGREGATE_ENGAGEMENT_RESOLVED",
        tick=tick,
        initiator_id=force_a.unit_id,
        target_id=force_b.unit_id,
        terrain_modifier=env.terrain_modifier,
        damage_calc=a_loss + b_loss,
        ai_decision={
            "initiator_loss": a_loss,
            "target_loss": b_loss,
            "initiator_strength_after": force_a.strength - a_loss,
            "target_strength_after": force_b.strength - b_loss,
            "coefficients": coefficients,
        },
    )


def resolve_aggregate_tick(
    force_a: AggregateForce,
    force_b: AggregateForce,
    env: AggregateEnv,
    rng: DeterministicRNG,
    tick: int,
) -> AggregateResult:
    """一個 tick 的 Lanchester 消耗。雙方同時以 tick 前戰力互算（對稱）。"""
    roll_a = rng.random()  # force_a 承受的隨機化
    roll_b = rng.random()  # force_b 承受的隨機化

    a_loss = min(force_a.strength, _incoming_loss(force_b, force_a, env, roll_a))
    b_loss = min(force_b.strength, _incoming_loss(force_a, force_b, env, roll_b))

    coefficients = {
        "aimed_fraction": env.aimed_fraction,
        "terrain": env.terrain_modifier,
        "weather": env.weather_modifier,
        "variance": env.variance,
        "initiator_lethality": force_a.lethality,
        "target_lethality": force_b.lethality,
    }
    event = _pair_event(force_a, force_b, env, a_loss, b_loss, coefficients, tick)
    return AggregateResult(
        a_strength_after=force_a.strength - a_loss,
        b_strength_after=force_b.strength - b_loss,
        a_loss=a_loss,
        b_loss=b_loss,
        coefficients=coefficients,
        events=[event],
    )


@dataclass(frozen=True, slots=True)
class MultiwayResult:
    """多方混戰一個 tick 的結果：各 force 的 tick 後戰力 + 每個敵對配對一則事件。"""

    strength_after: dict[str, float]
    events: list[LedgerEvent]


def resolve_multiway_tick(
    forces: list[AggregateForce],
    relations: FactionRelations,
    env: AggregateEnv,
    rng: DeterministicRNG,
    tick: int,
) -> MultiwayResult:
    """N 方混戰（§12.1/ADR 006）：對每一 HOSTILE 配對逐一裁決（配對確定性排序）。

    每 force 承受**所有敵對配對**的戰損（以 tick 前戰力互算），最後一次夾至 [0, 戰力]——
    同時對多敵作戰者可被合圍殲滅，能量守恆（總戰損 ≤ 總戰力）仍成立。
    """
    ordered = sorted(forces, key=lambda f: f.unit_id)
    raw_loss: dict[str, float] = {f.unit_id: 0.0 for f in ordered}
    events: list[LedgerEvent] = []

    for i, force_a in enumerate(ordered):
        for force_b in ordered[i + 1 :]:
            if not relations.is_hostile(force_a.faction, force_b.faction):
                continue
            roll_a = rng.random()
            roll_b = rng.random()
            la = _incoming_loss(force_b, force_a, env, roll_a)
            lb = _incoming_loss(force_a, force_b, env, roll_b)
            raw_loss[force_a.unit_id] += la
            raw_loss[force_b.unit_id] += lb
            coefficients = {
                "aimed_fraction": env.aimed_fraction,
                "terrain": env.terrain_modifier,
                "weather": env.weather_modifier,
                "variance": env.variance,
                "initiator_lethality": force_a.lethality,
                "target_lethality": force_b.lethality,
            }
            events.append(_pair_event(force_a, force_b, env, la, lb, coefficients, tick))

    strength_after = {f.unit_id: f.strength - min(f.strength, raw_loss[f.unit_id]) for f in ordered}
    return MultiwayResult(strength_after=strength_after, events=events)


def _incoming_loss(
    attacker: AggregateForce, defender: AggregateForce, env: AggregateEnv, roll: float
) -> float:
    """defender 本 tick 承受的戰損（尚未夾上限）。square/linear 混合 × 環境 × 隨機化。"""
    factor = 1.0 + env.variance * (2.0 * roll - 1.0)  # roll∈[0,1) → [1-variance, 1+variance)
    aimed = attacker.strength  # square law：殺傷率正比於攻方戰力
    area = attacker.strength * defender.strength / _AREA_SCALE  # linear law：正比於雙方
    base = env.aimed_fraction * aimed + (1.0 - env.aimed_fraction) * area
    loss = attacker.lethality * base * env.terrain_modifier * env.weather_modifier * factor
    return max(0.0, loss)
