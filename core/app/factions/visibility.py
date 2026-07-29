"""「某陣營直接看得到哪些陣營的單位」——單一規則（#91 共享視圖）。

觀測者直接看得到的＝**自己 + 盟軍**。NEUTRAL/HOSTILE 不在此列：那些要靠偵測（`/intel`），
看不看得到由 fog 決定。

WP-C5 起這條規則有兩個消費者：`GET /units`（REST，陣營來源是 DB 查詢）與 STATE_DIFF 的
每陣營投影（活模擬，陣營來源是已載入的 resolver）。**兩份實作＝兩份會漂移的 fog of war**，
故規則本身抽成純函數，資料怎麼來由呼叫端負責。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.factions.relations import FactionRelations


def visible_factions(
    observer: str, factions: Iterable[str], relations: FactionRelations
) -> list[str]:
    """`observer` 直接看得到的陣營清單（含自己），依輸入順序去重。"""
    seen: dict[str, None] = {}
    for f in factions:
        if f == observer or relations.is_allied(observer, f):
            seen.setdefault(f, None)
    return list(seen)
