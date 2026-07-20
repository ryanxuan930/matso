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
    blue_strength_after: float
    red_strength_after: float
    blue_loss: float
    red_loss: float
    coefficients: dict[str, float]
    events: list[LedgerEvent]


def resolve_aggregate_tick(
    blue: AggregateForce,
    red: AggregateForce,
    env: AggregateEnv,
    rng: DeterministicRNG,
    tick: int,
) -> AggregateResult:
    """一個 tick 的 Lanchester 消耗。雙方同時以 tick 前戰力互算（對稱）。"""
    roll_b = rng.random()  # 藍方承受的隨機化
    roll_r = rng.random()  # 紅方承受的隨機化

    blue_loss = min(blue.strength, _incoming_loss(red, blue, env, roll_b))
    red_loss = min(red.strength, _incoming_loss(blue, red, env, roll_r))
    blue_after = blue.strength - blue_loss
    red_after = red.strength - red_loss

    coefficients = {
        "aimed_fraction": env.aimed_fraction,
        "terrain": env.terrain_modifier,
        "weather": env.weather_modifier,
        "variance": env.variance,
        "blue_lethality": blue.lethality,
        "red_lethality": red.lethality,
    }
    event = LedgerEvent(
        event_type="AGGREGATE_ENGAGEMENT_RESOLVED",
        tick=tick,
        initiator_id=blue.unit_id,
        target_id=red.unit_id,
        terrain_modifier=env.terrain_modifier,
        damage_calc=blue_loss + red_loss,
        ai_decision={
            "blue_loss": blue_loss,
            "red_loss": red_loss,
            "blue_strength_after": blue_after,
            "red_strength_after": red_after,
            "coefficients": coefficients,
        },
    )
    return AggregateResult(
        blue_strength_after=blue_after,
        red_strength_after=red_after,
        blue_loss=blue_loss,
        red_loss=red_loss,
        coefficients=coefficients,
        events=[event],
    )


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
