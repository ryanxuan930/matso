"""統計儀表板指標（O8.2，SPEC_FULL §14.2）——由 Ledger 事件推導。

純函數。faction 級指標需 unit→faction 對照（由呼叫端提供；缺則只算全域指標）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.aar.events import AarEvent

_ENGAGE_TYPES = frozenset({"ENGAGEMENT_RESOLVED", "AGGREGATE_ENGAGEMENT_RESOLVED"})


@dataclass(frozen=True, slots=True)
class AarMetrics:
    total_events: int
    event_counts: dict[str, int]
    engagements: int
    hits: int
    hit_rate: float  # hits / 個體交戰數
    total_damage: float
    guardrail_blocks: int
    damage_by_faction: dict[str, float]  # 各陣營「承受」的總戰損
    max_tick: int


def compute_metrics(
    events: Sequence[AarEvent], unit_faction: dict[str, str] | None = None
) -> AarMetrics:
    faction_of = unit_faction or {}
    counts: dict[str, int] = {}
    engagements = hits = 0
    total_damage = 0.0
    guardrail_blocks = 0
    damage_by_faction: dict[str, float] = {}
    max_tick = 0

    for e in events:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1
        max_tick = max(max_tick, e.tick)
        if e.event_type in _ENGAGE_TYPES:
            engagements += 1
        if e.event_type == "ENGAGEMENT_RESOLVED" and e.ai_decision.get("hit"):
            hits += 1
        if e.event_type == "GUARDRAIL_INTERVENTION":
            guardrail_blocks += 1
        dmg = e.damage_calc or 0.0
        total_damage += dmg
        if dmg and e.target_id and e.target_id in faction_of:
            f = faction_of[e.target_id]
            damage_by_faction[f] = damage_by_faction.get(f, 0.0) + dmg

    individual = counts.get("ENGAGEMENT_RESOLVED", 0)
    return AarMetrics(
        total_events=len(events),
        event_counts=counts,
        engagements=engagements,
        hits=hits,
        hit_rate=(hits / individual) if individual else 0.0,
        total_damage=round(total_damage, 3),
        guardrail_blocks=guardrail_blocks,
        damage_by_faction={k: round(v, 3) for k, v in damage_by_faction.items()},
        max_tick=max_tick,
    )
