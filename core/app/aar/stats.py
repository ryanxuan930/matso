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


def _area_losses(event: AarEvent) -> list[tuple[str, float]]:
    """面射擊事件的逐單位戰損（`AREA_FIRE_RESOLVED.losses_by_unit`）。

    ⚠ **只給 AAR 用**：這是 ground truth，不可經 `/aar` 端點下發給參與者
    （`aar/fog.py` 會在投影時把這個鍵剝掉）。統計是在剝掉之前、
    或以全知身分計算的——參與者看到的數字因此本來就會比較少，那是對的。
    """
    if event.event_type != "AREA_FIRE_RESOLVED":
        return []
    raw = event.ai_decision.get("losses_by_unit")
    if not isinstance(raw, dict):
        return []
    return [(str(k), float(v)) for k, v in raw.items() if isinstance(v, (int, float))]


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
        # 面射擊沒有單一 `target_id`（打的是座標），所以上面那條歸不了帳——
        # 砲兵造成的戰損在「各陣營承受多少」這張表上會整個消失。
        # `losses_by_unit` 早就寫在事件裡，只是從來沒有人讀。
        for unit_id, loss in _area_losses(e):
            owner = faction_of.get(unit_id)
            if owner:
                damage_by_faction[owner] = damage_by_faction.get(owner, 0.0) + loss

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
