"""觸發條件 DSL + MSEL 引擎 + 勝利判定（SPEC_FULL §11.3）。

condition DSL 為 MSEL 觸發器與 victory_conditions 共用（scenario.schema 註）。純函數評估，
狀態經注入的 `TriggerContext` 提供——與 DB/kernel 解耦、可決定性單元測試。

支援的 condition（`type`）：
- `time`         ：`{type: time, at_tick: N}` → tick ≥ N。
- `faction_eliminated` ：`{type: faction_eliminated, faction: X}` → X 戰力 ≤ 0。
- `strength_below`     ：`{type: strength_below, faction: X, value: V}` → X 戰力 < V。
- `unit_in_region`     ：`{type: unit_in_region, faction: X, bbox: [minlng,minlat,maxlng,maxlat]}`。
- `unit_in_polygon`    ：`{type: unit_in_polygon, faction: X, polygon: [[lng,lat], ...]}`（WP-B2）。
- `contact_established`：`{type: contact_established, faction: X, of: Y}`——X 偵測到 Y（WP-B2）。
- `manual`             ：`{type: manual}`——**永遠不自動成立**，等白軍扣板機（WP-B2）。
- `after_ticks_of`     ：`{type: after_ticks_of, event: E, ticks: N}`——E 觸發後 N tick（WP-B2）。
- `held_for`           ：`{type: held_for, of: cond, ticks: N}`——內層**連續**成立 N tick（WP-B2）。
- `all` / `any`        ：`{type: all|any, of: [cond, ...]}` 組合。
- `not`                ：`{type: not, of: cond}`（WP-B2）。

**DSL 是資料不是程式**（SPEC_V2 §WP-B2）——沒有 eval、沒有動態 import，
新增型別一律要同時改 `evaluate_condition` 與 `_CONDITION_FIELDS`，
後者讓想定在**載入時**就報出精確路徑，而不是跑到一半才靜默失效。

`held_for` / `after_ticks_of` 需要**跨 tick 的記憶**，那不是純函數能提供的——
故 `TriggerContext` 帶 `fired_at`（已觸發事件的 tick）與 `held_since`（條件連續成立的起始 tick），
由呼叫端（`MselEngine`）維護並在 checkpoint 中保存。
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
    # WP-B2：`contact_established` 用——(觀測陣營, 被看到的陣營) 的集合。
    contacts: frozenset[tuple[str, str]] = frozenset()
    # WP-B2：`after_ticks_of` 用——已觸發的 MSEL 事件 id → 觸發當時的 tick。
    fired_at: dict[str, int] = field(default_factory=dict)
    # WP-B2：`held_for` 用——條件鍵 → 它**連續**成立至今的起始 tick。由引擎維護。
    held_since: dict[str, int] = field(default_factory=dict)
    # WP-B2：`manual` 用——白軍已扣板機的事件 id。
    manual_fired: frozenset[str] = frozenset()
    # 目前評估中的 MSEL 事件 id（`manual` / `held_for` 要用它當記憶的鍵）。
    entry_id: str = ""


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
    if ctype == "unit_in_polygon":
        return any(
            f == cond["faction"] and _point_in_polygon(lng, lat, cond["polygon"])
            for (f, lat, lng) in ctx.unit_positions
        )
    if ctype == "contact_established":
        return (str(cond["faction"]), str(cond["of"])) in ctx.contacts
    if ctype == "manual":
        # **永遠不自動成立。**白軍扣了板機才算——這正是「動態取捨」的機制：
        # 教官看現場狀況決定要不要發這個狀況，而不是時間到就自動發。
        return ctx.entry_id in ctx.manual_fired
    if ctype == "after_ticks_of":
        base = ctx.fired_at.get(str(cond["event"]))
        return base is not None and ctx.tick >= base + int(cond["ticks"])
    if ctype == "held_for":
        # 內層成立**且**已連續成立夠久。`held_since` 由引擎在每 tick 維護：
        # 內層一旦不成立就清掉，所以「成立→中斷→再成立」會重新計時，那才是「持續」的語義。
        if not evaluate_condition(cond["of"], ctx):
            return False
        since = ctx.held_since.get(ctx.entry_id)
        return since is not None and ctx.tick - since >= int(cond["ticks"])
    if ctype == "all":
        return all(evaluate_condition(c, ctx) for c in cond["of"])
    if ctype == "any":
        return any(evaluate_condition(c, ctx) for c in cond["of"])
    if ctype == "not":
        return not evaluate_condition(cond["of"], ctx)
    raise TriggerError(f"未知的 condition type：{ctype!r}")


def _point_in_polygon(lng: float, lat: float, polygon: list[list[float]]) -> bool:
    """射線法。多邊形為 [[lng,lat], ...]，自動閉合。

    邊界情形不特別處理：一個單位剛好站在邊界線上要不要算，在戰術上本來就沒有標準答案，
    而射線法對它的判定是確定的（同一組輸入永遠同一個答案），決定性成立就夠了。
    """
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


# 每個 condition type 的必填欄位（載入時的結構驗證用；語義評估見 evaluate_condition）。
_CONDITION_FIELDS: dict[str, tuple[str, ...]] = {
    "time": ("at_tick",),
    "faction_eliminated": ("faction",),
    "strength_below": ("faction", "value"),
    "unit_in_region": ("faction", "bbox"),
    "unit_in_polygon": ("faction", "polygon"),
    "contact_established": ("faction", "of"),
    "manual": (),
    "after_ticks_of": ("event", "ticks"),
    "held_for": ("of", "ticks"),
    "all": ("of",),
    "any": ("of",),
    "not": ("of",),
}


def validate_condition(cond: Any, label: str) -> None:
    """**載入時**檢查 condition 的 type 與必填欄位（不評估、不需要 ctx）。

    存在的理由：`evaluate_condition` 對未知 type 丟 `TriggerError`，但那是**執行期**——
    想定會順利載入、開局、跑到白軍以為勝負條件生效時才炸（或整條 MSEL 靜默失效）。
    想定資產的錯誤要在載入時就指出精確路徑（SPEC_FULL §11.1）。
    """
    if not isinstance(cond, dict):
        raise TriggerError(f"{label}: condition 必須是 mapping")
    ctype = cond.get("type")
    fields = _CONDITION_FIELDS.get(str(ctype))
    if fields is None:
        raise TriggerError(
            f"{label}: 未知的 condition type：{ctype!r}"
            f"（支援：{', '.join(sorted(_CONDITION_FIELDS))}）"
        )
    for key in fields:
        if key not in cond:
            raise TriggerError(f"{label}.{ctype}: 缺少必填欄位 '{key}'")
    if ctype in ("all", "any"):
        sub = cond["of"]
        if not isinstance(sub, list) or not sub:
            raise TriggerError(f"{label}.{ctype}.of: 必須是非空陣列")
        for i, child in enumerate(sub):
            validate_condition(child, f"{label}.{ctype}.of[{i}]")
    # `not` / `held_for` 的 `of` 是**單一** condition 而非陣列——型別搞混要在載入時說，
    # 不是等到執行期丟一個看不懂的 TypeError。
    if ctype in ("not", "held_for"):
        validate_condition(cond["of"], f"{label}.{ctype}.of")
    if ctype == "unit_in_polygon":
        poly = cond["polygon"]
        if not isinstance(poly, list) or len(poly) < 3:
            raise TriggerError(f"{label}.unit_in_polygon.polygon: 至少要三個頂點")


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
