"""多陣營模型（SPEC_FULL §12.1、ADR 006）——faction id 與（O6.8 起）關係矩陣的單一權威。

faction 為**想定定義的字串 id**（非封閉 enum）：pattern `^[A-Z][A-Z0-9_]{1,31}$`。
`WHITE_CELL` 為保留字（統裁、非交戰方，不得入 orbat/關係矩陣）。

紅線：任何子系統的敵我判斷 MUST 經本套件（O6.8 關係服務），禁止自行 `faction != mine` 判敵。
"""

from __future__ import annotations

import re

from app.errors import FactionInvalidError
from app.factions.relations import FactionRelations, Relation

# 保留字：統裁視角、非交戰方。
WHITE_CELL = "WHITE_CELL"

FACTION_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")


def is_valid_faction_id(value: str) -> bool:
    """faction id 是否符合格式（不含保留字檢查——WHITE_CELL 本身是合法 id）。"""
    return bool(FACTION_ID_PATTERN.fullmatch(value))


def validate_faction_id(value: str, *, allow_white_cell: bool = True) -> str:
    """驗證並回傳 faction id。非法格式（或不允許的保留字）→ FactionInvalidError。"""
    if not is_valid_faction_id(value):
        raise FactionInvalidError(
            f"非法的 faction id：{value!r}（須符合 ^[A-Z][A-Z0-9_]{{1,31}}$）"
        )
    if not allow_white_cell and value == WHITE_CELL:
        raise FactionInvalidError("WHITE_CELL 為保留字（統裁），不得用於交戰陣營")
    return value


__all__ = [
    "FACTION_ID_PATTERN",
    "WHITE_CELL",
    "FactionRelations",
    "Relation",
    "is_valid_faction_id",
    "validate_faction_id",
]
