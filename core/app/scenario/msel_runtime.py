"""MSEL 執行器（WP-B2）——把觸發器從「只會記一筆帳」變成「真的會改變世界」。

**在此之前 MSEL 是死碼**：`MselEngine.check()` 只回 `list[LedgerEvent]`，
而活執行期傳給 Kernel 的是 `NoOpTriggerChecker`——想定裡寫的 MSEL 條目從來沒有跑過。
演習系統的心臟缺位（SPEC_V2 §WP-B2 的原話）。

設計上分成兩層，理由是**可測性**：

- `evaluate_msel(...)`：**純函數**。吃條目 + 觸發脈絡 + 記憶，吐「這 tick 該執行哪些注入」
  與更新後的記憶。不碰 DB、不碰熱狀態、不看牆鐘。
- `MselRuntime`：實作 `TriggerChecker`，把上面的結果**套用到世界**（改單位、發信文、暫停）。

一個注入出錯不得讓整局停擺：`kernel.run_tick` 對 trigger 槽**沒有任何防護**，
一個例外會讓 runner 崩潰、3 秒後被 SimManager 重建——在想定資料有問題時變成重啟迴圈。
故每一則注入各自 try/except，失敗落一筆 `MSEL_INJECT_FAILED` 而不是往上拋。

紅線 1：本模組不抽隨機、不看牆鐘；tick 一律由呼叫端傳入。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.scenario.triggers import MselEntry, TriggerContext, TriggerError, evaluate_condition
from app.state.ledger import LedgerEvent

_LOG = logging.getLogger("app.msel")

# 注入型別（SPEC_V2 §WP-B2）。SPAWN_UNITS 屬後續卡（需要決定性 id 與 resolver 失效通知）。
ACTION_MODIFY_UNIT = "MODIFY_UNIT"
ACTION_MESSAGE = "MESSAGE"
ACTION_PAUSE = "PAUSE"
ACTION_WEATHER_OVERRIDE = "WEATHER_OVERRIDE"


@dataclass
class MselMemory:
    """跨 tick 的記憶。**必須進 checkpoint**，否則重啟後所有 once 條目重新武裝。

    `MselEngine._fired` 過去就是一個純記憶體的 `set`——那是已知缺陷
    （見 PROGRESS / live-checkpoint worklog），這裡不重蹈。
    """

    fired_at: dict[str, int] = field(default_factory=dict)
    held_since: dict[str, int] = field(default_factory=dict)
    # 白軍已扣板機的 manual 事件 id。
    manual_fired: set[str] = field(default_factory=set)
    # 白軍決定跳過的事件 id（AAR 要看得出「原定 vs 實際」）。
    skipped: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fired_at": dict(self.fired_at),
            "held_since": dict(self.held_since),
            "manual_fired": sorted(self.manual_fired),
            "skipped": sorted(self.skipped),
        }

    @staticmethod
    def from_dict(raw: Any) -> MselMemory:
        if not isinstance(raw, dict):
            return MselMemory()
        return MselMemory(
            fired_at={str(k): int(v) for k, v in (raw.get("fired_at") or {}).items()},
            held_since={str(k): int(v) for k, v in (raw.get("held_since") or {}).items()},
            manual_fired=set(raw.get("manual_fired") or []),
            skipped=set(raw.get("skipped") or []),
        )


@dataclass(frozen=True, slots=True)
class DueInject:
    """本 tick 該執行的一則注入。"""

    entry_id: str
    inject: dict[str, Any]


def evaluate_msel(
    entries: list[MselEntry], ctx: TriggerContext, memory: MselMemory
) -> list[DueInject]:
    """**純函數**：這 tick 有哪些 MSEL 條目觸發？順帶就地更新 `memory`。

    `held_for` 的記憶在這裡維護：內層條件成立就記下起始 tick，不成立就清掉。
    「成立→中斷→再成立」因此會重新計時——那才是「持續 N tick」的語義，
    而不是「累計成立過 N 次」。

    `once` 條目觸發後記進 `fired_at`；`after_ticks_of` 就是讀這張表。
    """
    due: list[DueInject] = []
    for entry in sorted(entries, key=lambda e: e.id):  # 確定性順序
        if entry.id in memory.skipped:
            continue
        if entry.once and entry.id in memory.fired_at:
            continue
        scoped = _scoped(ctx, entry.id, memory)
        _update_hold(entry, scoped, memory)
        try:
            fired = evaluate_condition(entry.trigger, scoped)
        except TriggerError:
            # 未知 type 應該在載入時就被 validate_condition 擋下。走到這裡代表
            # 資料是繞過 loader 進來的——記一筆就好，別讓它每 tick 炸一次。
            _LOG.warning("MSEL %s 的觸發條件無法評估，已略過", entry.id)
            continue
        if not fired:
            continue
        memory.fired_at[entry.id] = ctx.tick
        due.append(DueInject(entry_id=entry.id, inject=entry.inject))
    return due


def _scoped(ctx: TriggerContext, entry_id: str, memory: MselMemory) -> TriggerContext:
    """把記憶與「目前評估哪一條」綁進脈絡——`manual`/`held_for` 要用它當鍵。"""
    return TriggerContext(
        tick=ctx.tick,
        faction_strength=ctx.faction_strength,
        unit_positions=ctx.unit_positions,
        contacts=ctx.contacts,
        fired_at=memory.fired_at,
        held_since=memory.held_since,
        manual_fired=frozenset(memory.manual_fired),
        entry_id=entry_id,
    )


def _update_hold(entry: MselEntry, ctx: TriggerContext, memory: MselMemory) -> None:
    """維護 `held_for` 的連續計時。非 held_for 條目不動任何東西。"""
    inner = _held_inner(entry.trigger)
    if inner is None:
        return
    try:
        holding = evaluate_condition(inner, ctx)
    except TriggerError:
        return
    if holding:
        memory.held_since.setdefault(entry.id, ctx.tick)
    else:
        memory.held_since.pop(entry.id, None)


def _held_inner(cond: Any) -> dict[str, Any] | None:
    if isinstance(cond, dict) and cond.get("type") == "held_for":
        inner = cond.get("of")
        return inner if isinstance(inner, dict) else None
    return None


# 套用注入的出口（由部署層注入；回傳要落帳的補充事件，通常是空）。
InjectApplier = Callable[[str, dict[str, Any], int], list[LedgerEvent]]


class MselRuntime:
    """`TriggerChecker` 實作——每 tick 評估 MSEL，觸發時套用注入並落帳。

    `context_fn(tick)` 由部署層提供（讀熱狀態組 `TriggerContext`）；
    `applier(entry_id, inject, tick)` 負責實際改變世界。兩者都注入，
    本類別因此可以完全離線單元測試。
    """

    def __init__(
        self,
        entries: list[MselEntry],
        context_fn: Callable[[int], TriggerContext],
        applier: InjectApplier | None = None,
        memory: MselMemory | None = None,
    ) -> None:
        self._entries = entries
        self._context_fn = context_fn
        self._applier = applier
        self.memory = memory or MselMemory()

    def check(self, now: Any) -> list[LedgerEvent]:
        tick = now.tick if hasattr(now, "tick") else int(now)
        if not self._entries:
            return []  # 沒有 MSEL 的局：完全不動作（既有局零行為變更）
        events: list[LedgerEvent] = []
        for item in evaluate_msel(self._entries, self._context_fn(tick), self.memory):
            events.append(_inject_event(item, tick))
            if self._applier is None:
                continue
            try:
                events.extend(self._applier(item.entry_id, item.inject, tick))
            except Exception as err:
                # **一則注入壞掉不得讓整局停擺**：kernel 的 trigger 槽沒有防護，
                # 例外會讓 runner 崩潰後被每 3 秒重建一次。
                _LOG.exception("MSEL %s 的注入套用失敗", item.entry_id)
                events.append(
                    LedgerEvent(
                        event_type="MSEL_INJECT_FAILED",
                        tick=tick,
                        ai_decision={
                            "msel_id": item.entry_id,
                            "reason": type(err).__name__,
                            "reason_detail": str(err)[:200],
                        },
                    )
                )
        return events

    # ---- 白軍的動態取捨（SPEC_V2 §WP-B2「白軍動態取捨」）----

    def fire_manually(self, entry_id: str) -> None:
        """白軍扣板機。`manual` 型條件唯一會成立的方式。"""
        self.memory.manual_fired.add(entry_id)

    def skip(self, entry_id: str) -> None:
        """白軍決定不發這個狀況。**記著而不是刪掉**——AAR 要看得出「原定 vs 實際」。"""
        self.memory.skipped.add(entry_id)

    def pending(self) -> list[str]:
        """尚未觸發也未被跳過的條目（供白軍控制台列「待命注入」）。"""
        return [
            e.id
            for e in sorted(self._entries, key=lambda x: x.id)
            if e.id not in self.memory.fired_at and e.id not in self.memory.skipped
        ]


def _inject_event(item: DueInject, tick: int) -> LedgerEvent:
    inject = item.inject
    decision: dict[str, Any] = {"msel_id": item.entry_id, "source": "MSEL"}
    if inject.get("faction") is not None:
        decision["faction"] = inject["faction"]
    decision.update(inject.get("payload") or {})
    return LedgerEvent(event_type=str(inject["event_type"]), tick=tick, ai_decision=decision)


__all__ = [
    "ACTION_MESSAGE",
    "ACTION_MODIFY_UNIT",
    "ACTION_PAUSE",
    "ACTION_WEATHER_OVERRIDE",
    "DueInject",
    "MselMemory",
    "MselRuntime",
    "evaluate_msel",
]
