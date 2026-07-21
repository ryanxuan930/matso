"""重播服務（O8.1，SPEC_FULL §14.2）——Ledger → 時間軸 frames + 書籤 + 任一 tick 狀態重建。

純函數（輸入 AarEvent 清單）。狀態重建讀事件記錄的**權威後態**（target_health_after 等），
故與 checkpoint 熱狀態一致（同一份事實來源）；無後態欄時退回以 damage_calc 遞減。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.aar.events import AarEvent

# 值得書籤的關鍵事件（§14.2「可跳至書籤事件」）。
BOOKMARK_TYPES = frozenset(
    {
        "ENGAGEMENT_RESOLVED",
        "AGGREGATE_ENGAGEMENT_RESOLVED",
        "GUARDRAIL_INTERVENTION",
        "FACTION_RELATION_CHANGED",
        "ROLLBACK",
        "REINFORCEMENT",
        "FORCE_COLLAPSE",
    }
)


@dataclass(frozen=True, slots=True)
class Frame:
    tick: int
    seqs: list[int]
    event_types: list[str]


@dataclass(frozen=True, slots=True)
class Bookmark:
    seq: int
    tick: int
    event_type: str
    label: str


@dataclass(slots=True)
class UnitState:
    health: float = 100.0
    lat: float | None = None
    lng: float | None = None


def build_timeline(events: Sequence[AarEvent]) -> list[Frame]:
    """把事件依 tick 聚成 frames（供前端逐 tick 播放/倒帶）。"""
    by_tick: dict[int, Frame] = {}
    for e in events:
        f = by_tick.get(e.tick)
        if f is None:
            f = Frame(tick=e.tick, seqs=[], event_types=[])
            by_tick[e.tick] = f
        f.seqs.append(e.seq)
        f.event_types.append(e.event_type)
    return [by_tick[t] for t in sorted(by_tick)]


def bookmarks(events: Sequence[AarEvent]) -> list[Bookmark]:
    """關鍵事件書籤（§14.2）。"""
    out: list[Bookmark] = []
    for e in events:
        if e.event_type in BOOKMARK_TYPES:
            label = e.event_type
            if e.target_id:
                label = f"{e.event_type} → {e.target_id}"
            out.append(Bookmark(seq=e.seq, tick=e.tick, event_type=e.event_type, label=label))
    return out


def reconstruct_states(events: Sequence[AarEvent], up_to_tick: int) -> dict[str, UnitState]:
    """重建 up_to_tick（含）當下的單位狀態（health/位置）——與 checkpoint 一致（同一事實來源）。"""
    states: dict[str, UnitState] = {}

    def _st(uid: str) -> UnitState:
        return states.setdefault(uid, UnitState())

    for e in events:
        if e.tick > up_to_tick:
            break
        dec = e.ai_decision
        # 個體交戰：權威後態（engagement.py 記 target_health_after）。
        if e.target_id and "target_health_after" in dec:
            _st(e.target_id).health = float(dec["target_health_after"])
        elif e.target_id and e.damage_calc is not None:
            s = _st(e.target_id)
            s.health = max(0.0, s.health - float(e.damage_calc))
        # 聚合交戰：雙方後態。
        if "initiator_strength_after" in dec and e.initiator_id:
            _st(e.initiator_id).health = float(dec["initiator_strength_after"])
        if "target_strength_after" in dec and e.target_id:
            _st(e.target_id).health = float(dec["target_strength_after"])
        # 位置更新（MOVE 完成等記 lat/lng）。
        if e.initiator_id and "lat" in dec and "lng" in dec:
            s = _st(e.initiator_id)
            s.lat, s.lng = float(dec["lat"]), float(dec["lng"])
    return states


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    frames: list[Frame] = field(default_factory=list)
    bookmarks: list[Bookmark] = field(default_factory=list)
    total_events: int = 0
    max_tick: int = 0


def replay_summary(events: Sequence[AarEvent]) -> ReplaySummary:
    frames = build_timeline(events)
    return ReplaySummary(
        frames=frames,
        bookmarks=bookmarks(events),
        total_events=len(events),
        max_tick=frames[-1].tick if frames else 0,
    )
