"""Faction id 驗證與保留字（O6.7，SPEC §12.1 / ADR 006）。"""

from __future__ import annotations

import pytest

from app.errors import FactionInvalidError
from app.factions import WHITE_CELL, is_valid_faction_id, validate_faction_id


@pytest.mark.parametrize("fid", ["BLUE", "RED", "YELLOW", "GREEN", "OPFOR_2", "AB", "WHITE_CELL"])
def test_valid_ids(fid: str) -> None:
    assert is_valid_faction_id(fid)
    assert validate_faction_id(fid) == fid


@pytest.mark.parametrize(
    "fid", ["blue", "1RED", "bad-faction", "HAS SPACE", "", "A", "X" * 40, "紅軍"]
)
def test_invalid_ids_rejected(fid: str) -> None:
    assert not is_valid_faction_id(fid)
    with pytest.raises(FactionInvalidError):
        validate_faction_id(fid)


def test_white_cell_reserved_word_blocked_for_combatants() -> None:
    # 交戰陣營情境（orbat/關係矩陣）不得用 WHITE_CELL
    with pytest.raises(FactionInvalidError, match="保留字"):
        validate_faction_id(WHITE_CELL, allow_white_cell=False)
    # 一般情境（如 intel 查詢視角）WHITE_CELL 合法
    assert validate_faction_id(WHITE_CELL) == WHITE_CELL
