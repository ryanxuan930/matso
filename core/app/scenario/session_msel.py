"""把持久化的 MSEL 接回執行期（WP-B2）。

兩件事：從 session 讀出腳本事件、以及每 tick 組出 `TriggerContext`。

**脈絡從熱狀態組，不從 DB**：活模擬只寫熱狀態，DB 的 `current_lat/lng` 停在開局位置。
用 DB 組脈絡的話，「紅軍推進到北岸」這種條件永遠不會成立——而且不會有任何徵兆。
（同 BL-3 `has_observer_on` 踩過的那個坑。）
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.tables import IntelContact, TacticalUnit, WargameSession
from app.scenario.triggers import MselEntry, TriggerContext
from app.state.hot_state import HotStateStore

_LOG = logging.getLogger("app.msel")


def load_session_msel(db: Session, session_id: str) -> list[MselEntry]:
    """讀本局的 MSEL 腳本事件（開局快照）。未宣告 → 空清單 → 引擎完全不動作。"""
    session = db.get(WargameSession, session_id)
    raw = getattr(session, "msel", None) if session is not None else None
    if not isinstance(raw, list):
        return []
    out: list[MselEntry] = []
    for item in raw:
        if not isinstance(item, dict) or "id" not in item or "trigger" not in item:
            _LOG.warning("session %s 的 MSEL 有一筆格式不對，已略過：%r", session_id, item)
            continue
        out.append(
            MselEntry(
                id=str(item["id"]),
                trigger=dict(item["trigger"]),
                inject=dict(item.get("inject") or {}),
                once=bool(item.get("once", True)),
            )
        )
    return out


def make_context_fn(
    session_factory: sessionmaker[Session], session_id: str, hot: HotStateStore
) -> Callable[[int], TriggerContext]:
    """回 `tick → TriggerContext`。

    **每 tick 都會被呼叫**，所以刻意做得便宜：單位陣營表只查一次 DB 並快取
    （陣營不會在局中改變），位置與戰力每次從熱狀態的 mirror cache 取（零 Redis 往返）。
    敵情接觸則查 DB——`IntelContact` 是 sensor sweep 寫的，本來就在那裡。
    """
    faction_of: dict[str, str] = {}

    def build(tick: int) -> TriggerContext:
        nonlocal faction_of
        if not faction_of:
            with session_factory() as db:
                faction_of = dict(
                    db.execute(
                        select(TacticalUnit.id, TacticalUnit.faction).where(
                            TacticalUnit.session_id == session_id
                        )
                    ).tuples()
                )
        positions: list[tuple[str, float, float]] = []
        strength: dict[str, float] = {}
        for unit_id, state in hot.get_all().items():
            faction = faction_of.get(unit_id)
            if not faction:
                continue
            lat, lng = state.get("lat"), state.get("lng")
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                positions.append((faction, float(lat), float(lng)))
            raw = state.get("strength", state.get("health"))
            if isinstance(raw, (int, float)):
                strength[faction] = strength.get(faction, 0.0) + float(raw)
        with session_factory() as db:
            contacts = frozenset(
                (str(f), str(faction_of.get(str(t), "")))
                for f, t in db.execute(
                    select(IntelContact.faction, IntelContact.target_unit_id).where(
                        IntelContact.session_id == session_id
                    )
                ).tuples()
                if faction_of.get(str(t))
            )
        return TriggerContext(
            tick=tick,
            faction_strength=strength,
            unit_positions=positions,
            contacts=contacts,
        )

    return build


def context_snapshot(ctx: TriggerContext) -> dict[str, Any]:
    """脈絡摘要（除錯/測試用；不進帳本）。"""
    return {
        "tick": ctx.tick,
        "factions": sorted(ctx.faction_strength),
        "units": len(ctx.unit_positions),
        "contacts": len(ctx.contacts),
    }


__all__ = ["context_snapshot", "load_session_msel", "make_context_fn"]
