"""重播服務（O8.1，SPEC_FULL §14.2）——Ledger → 時間軸 frames + 書籤 + 任一 tick 狀態重建。

純函數（輸入 AarEvent 清單）。狀態重建讀事件記錄的**權威後態**（target_health_after 等），
故與 checkpoint 熱狀態一致（同一份事實來源）；無後態欄時退回以 damage_calc 遞減。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.aar.events import AarEvent
from app.adjudication.effectiveness import effectiveness_pct

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
    """重播出來的單位狀態。

    **`health` 與 `strength` 是兩個量綱，不可互相賦值**——這是本模組修過的一個實錯：
    個體交戰記的 `target_health_after` 是效能%（0–100），聚合交戰記的 `*_strength_after`
    是戰力點（人數/平台數量級），過去兩者都被寫進 `health`，於是一個滿編 500 的營打完剩 420
    會顯示成「health 420」。活模擬那條路徑（`adjudicator._apply_agg_force`）從一開始就分開，
    重播是唯一混用的地方。
    """

    health: float = 100.0  # 效能%（0–100）。帳本無傷即滿——append-only 下「沒有戰損事件」＝沒受損。
    strength: float | None = None  # 當前戰力點；僅聚合交戰會記錄，個體交戰為 None。
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


def reconstruct_states(
    events: Sequence[AarEvent],
    up_to_tick: int,
    authorized: Mapping[str, float] | None = None,
) -> dict[str, UnitState]:
    """重建 up_to_tick（含）當下的單位狀態——與 checkpoint 一致（同一事實來源）。

    `authorized` 是各單位的滿編戰力（`TacticalUnit.authorized_strength`），
    **聚合交戰的效能% 要靠它導出**（戰力點 ÷ 滿編 → 效能曲線），與活模擬同一個
    `effectiveness_pct`。事件流裡沒有滿編戰力（那是 DB 靜態資料），故由呼叫端注入。
    缺該單位的滿編值時：戰力點照記，但**不猜效能%**（維持既有值），
    寧可少一個數字也不要給一個錯刻度的數字。
    """
    states: dict[str, UnitState] = {}
    auth = authorized or {}

    def _st(uid: str) -> UnitState:
        return states.setdefault(uid, UnitState())

    def _set_strength(uid: str, points: float) -> None:
        s = _st(uid)
        s.strength = points
        a = auth.get(uid)
        if a is not None and a > 0:
            s.health = effectiveness_pct(points / a)

    for e in events:
        if e.tick > up_to_tick:
            break
        dec = e.ai_decision
        # 聚合交戰另有權威後態（戰力點），不能讓 damage_calc 的 fallback 先動到血量：
        # 聚合事件的 damage_calc 是**雙方損失相加**（aggregate.py），拿它扣單側等於
        # 把攻擊方的傷亡也記到守方頭上（§4 差距總表第 23 列「聚合戰損歸帳單側」）。
        has_after = "target_health_after" in dec or "target_strength_after" in dec
        # 個體交戰：權威後態（engagement.py 記 target_health_after，已是效能%）。
        if e.target_id and "target_health_after" in dec:
            _st(e.target_id).health = float(dec["target_health_after"])
        elif e.target_id and e.damage_calc is not None and not has_after:
            s = _st(e.target_id)
            s.health = max(0.0, s.health - float(e.damage_calc))
        # 聚合交戰：雙方後態皆為戰力點。
        if "initiator_strength_after" in dec and e.initiator_id:
            _set_strength(e.initiator_id, float(dec["initiator_strength_after"]))
        if "target_strength_after" in dec and e.target_id:
            _set_strength(e.target_id, float(dec["target_strength_after"]))
        # 位置更新。**移動類事件把 lat/lng 記在 `detail` 而非 `ai_decision`**
        # （movement.py 全部走 detail=）——本分支原本只看 ai_decision，於是自建立以來
        # 從未對任何真實移動生效過，地圖重播會是「所有單位都不動」。
        # 兩處都看：detail 優先（移動事件的真實來源），ai_decision 保留給有記位置的裁決事件。
        pos = e.detail if ("lat" in e.detail and "lng" in e.detail) else dec
        if e.initiator_id and "lat" in pos and "lng" in pos:
            s = _st(e.initiator_id)
            s.lat, s.lng = float(pos["lat"]), float(pos["lng"])
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
