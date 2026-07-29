"""MSEL 注入的套用層（WP-B2）——把「觸發了」變成「世界真的改變了」。

`msel_runtime` 決定**要不要**注入（純函數）；本模組決定注入**做什麼**（I/O）。
分開的理由是可測性，也是紅線 2 的形狀：判斷與副作用不要糾纏在一起。

支援的注入型別：

- `MODIFY_UNIT`：白軍軟裁決的機器化出口（[JTLS-F p.1059]）——直接調戰力/位置。
  **雙寫熱狀態與 DB**：只寫熱狀態的話，runner 一重啟 `seed_combat_state` 就用 DB 座標蓋回去
  （這正是 BL-4 那個回滾 bug 的同一個坑）。
- `MESSAGE`：發一封 C2 信文給指定席位（白軍誘導迴圈的「狀況發佈」）。
- `PAUSE`：設暫停旗標讓白軍講評。與控制台的 PAUSE 共用同一個 Redis 鍵。
- `WEATHER_OVERRIDE`：目前只落帳，天氣的 tick 化屬 WP-C4。**明確不假裝有效**——
  靜靜什麼都不做會讓想定作者以為天氣改了。

不認得的型別**不當成錯誤**：注入本來就可以只是「發一則給人看的事件」，
那是 MSEL 最原始的用法（inject 只落 Ledger）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import MessageKind, SeatRole
from app.models.tables import Message, TacticalUnit
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

_LOG = logging.getLogger("app.msel.actions")


def make_applier(
    session_id: str,
    session_factory: sessionmaker[Session],
    hot: HotStateStore,
    pause: Callable[[], None] | None = None,
) -> Callable[[str, dict[str, Any], int], list[LedgerEvent]]:
    """組一個注入套用器。回傳的函式滿足 `msel_runtime.InjectApplier`。"""

    def apply(entry_id: str, inject: dict[str, Any], tick: int) -> list[LedgerEvent]:
        action = str(inject.get("action") or "").upper()
        if action == "MODIFY_UNIT":
            return _modify_unit(session_id, session_factory, hot, entry_id, inject, tick)
        if action == "MESSAGE":
            return _message(session_id, session_factory, entry_id, inject, tick)
        if action == "PAUSE":
            if pause is not None:
                pause()
            return [
                LedgerEvent(
                    event_type="MSEL_PAUSE",
                    tick=tick,
                    ai_decision={"msel_id": entry_id, "reason": inject.get("reason", "")},
                )
            ]
        if action == "WEATHER_OVERRIDE":
            # **明確標記未實作**：靜靜什麼都不做，想定作者會以為天氣改了。
            return [
                LedgerEvent(
                    event_type="MSEL_INJECT_UNSUPPORTED",
                    tick=tick,
                    ai_decision={
                        "msel_id": entry_id,
                        "action": action,
                        "reason": "天氣覆蓋待 WP-C4 天氣 tick 化",
                    },
                )
            ]
        return []  # 無 action ＝ 純事件注入（MSEL 最原始的用法）

    return apply


def _modify_unit(
    session_id: str,
    session_factory: sessionmaker[Session],
    hot: HotStateStore,
    entry_id: str,
    inject: dict[str, Any],
    tick: int,
) -> list[LedgerEvent]:
    """白軍軟裁決：直接調某單位的戰力/位置。

    **一定要雙寫**熱狀態與 DB 列——只寫熱狀態的話，runner 一重啟
    `seed_combat_state` 就用 DB 的舊座標蓋回去（BL-4 那個回滾 bug 的同一個坑）。
    """
    target = str(inject.get("unit_id") or "")
    if not target:
        raise ValueError("MODIFY_UNIT 缺少 unit_id")
    patch: dict[str, Any] = {}
    changed: dict[str, Any] = {}
    with session_factory() as db:
        unit = db.get(TacticalUnit, target)
        if unit is None or unit.session_id != session_id:
            raise ValueError(f"MODIFY_UNIT 的目標單位不存在於本局：{target}")
        if "strength" in inject:
            value = max(0.0, float(inject["strength"]))
            unit.current_strength = value
            patch["strength"] = value
            changed["strength"] = value
            auth = float(unit.authorized_strength) or 100.0
            health = max(0.0, min(100.0, value / auth * 100.0))
            unit.health_status = health
            patch["health"] = health
        if "lat" in inject and "lng" in inject:
            lat, lng = float(inject["lat"]), float(inject["lng"])
            unit.current_lat, unit.current_lng = lat, lng
            patch["lat"], patch["lng"] = lat, lng
            changed["lat"], changed["lng"] = lat, lng
        db.commit()
    if patch:
        hot.update_unit(target, patch)
    return [
        LedgerEvent(
            event_type="MSEL_UNIT_MODIFIED",
            tick=tick,
            initiator_id=target,
            ai_decision={"msel_id": entry_id, "changes": changed},
        )
    ]


def _message(
    session_id: str,
    session_factory: sessionmaker[Session],
    entry_id: str,
    inject: dict[str, Any],
    tick: int,
) -> list[LedgerEvent]:
    """白軍誘導迴圈的「狀況發佈」——把一則狀況送進某陣營/席位的信文匣。

    寄件者填 `msel:{entry_id}`——`from_user_id` 沒有 FK，所以放一個**看得出來源**的字串
    比放某個真實使用者誠實：這是白軍/系統發的狀況，不是某個玩家寫的信。
    """
    faction = str(inject.get("faction") or "")
    if not faction:
        raise ValueError("MESSAGE 注入缺少 faction（要送給誰）")
    seat_raw = inject.get("to_seat")
    seat = SeatRole(str(seat_raw)) if seat_raw else None
    with session_factory() as db:
        db.add(
            Message(
                session_id=session_id,
                kind=MessageKind.REPORT,
                from_user_id=f"msel:{entry_id}",
                from_seat=None,
                to_faction=faction,
                to_seat=seat,
                ref_id=entry_id,
                body=str(inject.get("body") or ""),
                tick=tick,
            )
        )
        db.commit()
    return [
        LedgerEvent(
            event_type="MSEL_MESSAGE",
            tick=tick,
            ai_decision={
                "msel_id": entry_id,
                "observer_faction": faction,  # 受眾：只有收信陣營看得到
                "to_seat": seat.value if seat else None,
            },
        )
    ]


def unit_ids_of(db: Session, session_id: str) -> list[str]:
    """本局所有單位 id（供組 TriggerContext）。"""
    return list(
        db.scalars(select(TacticalUnit.id).where(TacticalUnit.session_id == session_id)).all()
    )


__all__ = ["make_applier", "unit_ids_of"]
