"""MSEL 注入的套用層（WP-B2）——把「觸發了」變成「世界真的改變了」。

`msel_runtime` 決定**要不要**注入（純函數）；本模組決定注入**做什麼**（I/O）。
分開的理由是可測性，也是紅線 2 的形狀：判斷與副作用不要糾纏在一起。

支援的注入型別：

- `SPAWN_UNITS`：增援生成。**單位 id 由 msel event id 決定性派生**（禁 `uuid4()`）——
  同一場重播必須生出同一批 id，否則之後所有指涉那些單位的事件都對不上。
- `MODIFY_UNIT`：白軍軟裁決的機器化出口（[JTLS-F p.1059]）——直接調戰力/位置。
  **雙寫熱狀態與 DB**：只寫熱狀態的話，runner 一重啟 `seed_combat_state` 就用 DB 座標蓋回去
  （這正是 BL-4 那個回滾 bug 的同一個坑）。
- `MESSAGE`：發一封 C2 信文給指定席位（白軍誘導迴圈的「狀況發佈」）。
- `PAUSE`：設暫停旗標讓白軍講評。與控制台的 PAUSE 共用同一個 Redis 鍵。
- `WEATHER_OVERRIDE`：全場天氣覆蓋（WP-B2 × WP-C4b）。**覆蓋優先於插件**——統裁說
  「現在起下暴雨」就不該被下一次刷新蓋回去。`effects` 缺席＝解除覆蓋，
  `duration_ticks` 到期自動解除。

不認得的型別**不當成錯誤**：注入本來就可以只是「發一則給人看的事件」，
那是 MSEL 最原始的用法（inject 只落 Ledger）。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.engine.engage_wiring import WeaponResolver, seed_combat_state
from app.models.enums import MessageKind, SeatRole, UnitLevel
from app.models.tables import EquipmentInstance, EquipmentTemplate, Message, TacticalUnit
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent
from app.weather import WeatherState, effects_from

_LOG = logging.getLogger("app.msel.actions")


def make_applier(
    session_id: str,
    session_factory: sessionmaker[Session],
    hot: HotStateStore,
    pause: Callable[[], None] | None = None,
    set_weather: Callable[[Any, int | None], None] | None = None,
) -> Callable[[str, dict[str, Any], int], list[LedgerEvent]]:
    """組一個注入套用器。回傳的函式滿足 `msel_runtime.InjectApplier`。"""

    def apply(entry_id: str, inject: dict[str, Any], tick: int) -> list[LedgerEvent]:
        action = str(inject.get("action") or "").upper()
        if action == "SPAWN_UNITS":
            return _spawn_units(session_id, session_factory, hot, entry_id, inject, tick)
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
            # WP-C4b 完成後這條終於做得到。**在此之前只落一筆 UNSUPPORTED**，
            # 理由寫「待 WP-C4 天氣 tick 化」——而那張卡早就完成了：
            # 白軍想在演習中注入暴雨仍然做不到。
            if set_weather is None:
                return [
                    LedgerEvent(
                        event_type="MSEL_INJECT_UNSUPPORTED",
                        tick=tick,
                        ai_decision={
                            "msel_id": entry_id,
                            "action": action,
                            "reason": "本執行期未接天氣覆蓋（無 weather cache）",
                        },
                    )
                ]
            raw_effects = inject.get("effects")
            duration = inject.get("duration_ticks")
            until = (
                tick + int(duration)
                if isinstance(duration, (int, float)) and duration > 0
                else None
            )
            # `effects` 缺席 ＝ **解除覆蓋**（回到插件的天氣），不是「套一份晴天」
            # ——後者會讓「取消注入」與「注入晴天」在資料上分不開。
            state = WeatherState.uniform(effects_from(raw_effects)) if raw_effects else None
            set_weather(state, until)
            return [
                LedgerEvent(
                    event_type="MSEL_WEATHER_OVERRIDE",
                    tick=tick,
                    ai_decision={
                        "msel_id": entry_id,
                        "effects": raw_effects if isinstance(raw_effects, dict) else None,
                        "until_tick": until,
                    },
                )
            ]
        return []  # 無 action ＝ 純事件注入（MSEL 最原始的用法）

    return apply


def spawn_unit_id(entry_id: str, index: int) -> str:
    """增援單位的 id——**決定性派生自 MSEL 事件 id**，禁 `uuid4()`。

    重播時必須生出同一批 id：不然重播中「增援 3 號被擊毀」那筆事件會指向一個
    不存在的單位，整段時間軸就對不起來了。取 SHA-256 前 32 個 hex 是為了塞進
    `String(191)` 且長得像既有的 uuid（可讀性）。
    """
    digest = hashlib.sha256(f"msel-spawn:{entry_id}:{index}".encode()).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def _spawn_units(
    session_id: str,
    session_factory: sessionmaker[Session],
    hot: HotStateStore,
    entry_id: str,
    inject: dict[str, Any],
    tick: int,
) -> list[LedgerEvent]:
    """增援生成：ORBAT 片段 → 建 TacticalUnit + 配裝 + 播熱狀態。

    **冪等**：同一個 entry 重跑（重啟後記憶沒還原、或白軍重複扣板機）不會生出兩批單位
    ——id 是決定性的，已存在就跳過。

    生成的單位**要播進熱狀態**，否則它在地圖上、在裁決裡、在 MSEL 脈絡裡都不存在
    （那三處讀的都是熱狀態）。
    """
    specs = inject.get("units")
    if not isinstance(specs, list) or not specs:
        raise ValueError("SPAWN_UNITS 缺少 units 清單")
    faction = str(inject.get("faction") or "")
    if not faction:
        raise ValueError("SPAWN_UNITS 缺少 faction")

    created: list[str] = []
    with session_factory() as db:
        templates = {str(t.name): t.id for t in db.scalars(select(EquipmentTemplate)).all()}
        for index, spec in enumerate(specs):
            if not isinstance(spec, dict):
                continue
            uid = spawn_unit_id(entry_id, index)
            if db.get(TacticalUnit, uid) is not None:
                continue  # 冪等：這批已經生過了
            strength = float(spec.get("strength", 100.0))
            unit = TacticalUnit(
                id=uid,
                session_id=session_id,
                designation=str(spec.get("designation") or f"{faction}-R{index + 1}"),
                unit_level=UnitLevel(str(spec.get("unit_level", "PLATOON"))),
                faction=faction,
                current_lat=float(spec["lat"]),
                current_lng=float(spec["lng"]),
                current_strength=strength,
                authorized_strength=strength,
                health_status=100.0,
                attributes=dict(spec.get("attributes") or {}),
            )
            db.add(unit)
            db.flush()
            for item in spec.get("equipment") or []:
                tmpl_id = templates.get(str(item.get("template")))
                if tmpl_id is None:
                    _LOG.warning("SPAWN_UNITS：查無裝備範本 %r，略過", item.get("template"))
                    continue
                db.add(
                    EquipmentInstance(
                        template_id=tmpl_id,
                        owner_id=uid,
                        quantity=int(item.get("quantity", 1)),
                        current_state={"ammo": int(item.get("ammo", 0))},
                    )
                )
            created.append(uid)
        db.commit()
        # 播熱狀態——不播的話這個單位在地圖、裁決、MSEL 脈絡裡都不存在。
        # **走 `seed_combat_state` 這條與開局同一的路徑**，不自己捲一份鍵集：
        # 手捲的那份漏了 `footprint_m`/`ammo`/`ammo_by_weapon`（增援一發都打不出去），
        # 且 `platform_count` 退回寫死的 1、`armor_class` 只讀 attributes
        # ——正是 `adjudication/establishment.py` 與 `armor.py` 修掉的那兩個病。
        if created:
            seed_combat_state(db, hot, session_id, WeaponResolver(db, session_id), created)
    if not created:
        return []
    return [
        LedgerEvent(
            event_type="MSEL_UNITS_SPAWNED",
            tick=tick,
            ai_decision={"msel_id": entry_id, "faction": faction, "unit_ids": created},
        )
    ]


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
