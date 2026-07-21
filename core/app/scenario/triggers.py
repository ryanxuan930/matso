"""觸發條件 DSL + MSEL 引擎 + 勝利判定（SPEC_FULL §11.3）。

condition DSL 為 MSEL 觸發器與 victory_conditions 共用（scenario.schema 註）。純函數評估，
狀態經注入的 `TriggerContext` 提供——與 DB/kernel 解耦、可決定性單元測試。

支援的 condition（`type`）：
- `time`         ：`{type: time, at_tick: N}` → tick ≥ N。
- `faction_eliminated` ：`{type: faction_eliminated, faction: X}` → X 戰力 ≤ 0。
- `strength_below`     ：`{type: strength_below, faction: X, value: V}` → X 戰力 < V。
- `unit_in_region`     ：`{type: unit_in_region, faction: X, bbox: [minlng,minlat,maxlng,maxlat]}`。
- `all` / `any`        ：`{type: all|any, of: [cond, ...]}` 組合。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.state.ledger import LedgerEvent


@dataclass(frozen=True, slots=True)
class TriggerContext:
    """觸發評估所需的狀態視圖（由呼叫端從 session 熱狀態組出）。"""

    tick: int
    faction_strength: dict[str, float] = field(default_factory=dict)
    # (faction, lat, lng)：
    unit_positions: list[tuple[str, float, float]] = field(default_factory=list)


class TriggerError(ValueError):
    """未知或格式錯誤的 condition。"""


def evaluate_condition(cond: dict[str, Any], ctx: TriggerContext) -> bool:
    """評估單一 condition 對當前 ctx 是否成立。未知 type → TriggerError。"""
    ctype = cond.get("type")
    if ctype == "time":
        return ctx.tick >= int(cond["at_tick"])
    if ctype == "faction_eliminated":
        return ctx.faction_strength.get(cond["faction"], 0.0) <= 0.0
    if ctype == "strength_below":
        return ctx.faction_strength.get(cond["faction"], 0.0) < float(cond["value"])
    if ctype == "unit_in_region":
        min_lng, min_lat, max_lng, max_lat = cond["bbox"]
        return any(
            f == cond["faction"] and min_lat <= lat <= max_lat and min_lng <= lng <= max_lng
            for (f, lat, lng) in ctx.unit_positions
        )
    if ctype == "all":
        return all(evaluate_condition(c, ctx) for c in cond["of"])
    if ctype == "any":
        return any(evaluate_condition(c, ctx) for c in cond["of"])
    raise TriggerError(f"未知的 condition type：{ctype!r}")


def check_victory(victory_conditions: list[dict[str, Any]], ctx: TriggerContext) -> list[str]:
    """回傳達成勝利條件的陣營清單（可能多方/空）。"""
    return [vc["faction"] for vc in victory_conditions if evaluate_condition(vc["condition"], ctx)]


@dataclass(frozen=True, slots=True)
class MselEntry:
    id: str
    trigger: dict[str, Any]  # condition DSL
    inject: dict[str, Any]  # {event_type, payload?, faction?}
    once: bool = True  # 邊緣觸發（成立時觸一次）；False 則每個成立的 tick 都觸


class MselEngine:
    """MSEL 觸發器（實作 TriggerChecker）。每 tick 評估條件，成立即注入事件。

    context_fn(tick) 由部署層注入（讀 session 熱狀態組 TriggerContext）；once 條目觸發後不再觸。
    """

    def __init__(
        self, entries: list[MselEntry], context_fn: Callable[[int], TriggerContext]
    ) -> None:
        self._entries = entries
        self._context_fn = context_fn
        self._fired: set[str] = set()

    def check(self, now: Any) -> list[LedgerEvent]:
        """SimTime → 本 tick 觸發的注入事件（滿足 TriggerChecker 協定）。"""
        tick = now.tick if hasattr(now, "tick") else int(now)
        ctx = self._context_fn(tick)
        events: list[LedgerEvent] = []
        for entry in self._entries:
            if entry.once and entry.id in self._fired:
                continue
            if evaluate_condition(entry.trigger, ctx):
                self._fired.add(entry.id)
                events.append(_inject_event(entry, tick))
        return events


def _inject_event(entry: MselEntry, tick: int) -> LedgerEvent:
    inject = entry.inject
    decision: dict[str, Any] = {"msel_id": entry.id, "source": "MSEL"}
    if inject.get("faction") is not None:
        decision["faction"] = inject["faction"]
    decision.update(inject.get("payload") or {})
    return LedgerEvent(event_type=inject["event_type"], tick=tick, ai_decision=decision)
