"""陣營關係矩陣（SPEC_FULL §12.1、ADR 006）——敵我判斷的單一權威。

三值 `ALLIED/NEUTRAL/HOSTILE`，**對稱**；未宣告配對預設 **HOSTILE**（兵推常態；既有 BLUE/RED
零遷移）。同一陣營對自己視為 ALLIED。White Cell 局中調整（宣戰/停火）→ FACTION_RELATION_CHANGED
Ledger 事件（證據性、可重播）。

紅線：任何子系統 MUST 經本服務判敵（`is_hostile/is_allied/is_neutral`），禁止自行 `!= faction`。
"""

from __future__ import annotations

import enum

from app.state.ledger import LedgerEvent


class Relation(enum.StrEnum):
    ALLIED = "ALLIED"
    NEUTRAL = "NEUTRAL"
    HOSTILE = "HOSTILE"


def _key(a: str, b: str) -> frozenset[str]:
    return frozenset({a, b})


class FactionRelations:
    """對稱關係矩陣。未宣告配對回 `default`（預設 HOSTILE）。"""

    def __init__(
        self,
        declarations: list[tuple[str, str, Relation]] | None = None,
        *,
        default: Relation = Relation.HOSTILE,
    ) -> None:
        self._default = default
        self._pairs: dict[frozenset[str], Relation] = {}
        for a, b, rel in declarations or []:
            if a != b:
                self._pairs[_key(a, b)] = rel

    def relation(self, a: str, b: str) -> Relation:
        if a == b:
            return Relation.ALLIED  # 己方對己方
        return self._pairs.get(_key(a, b), self._default)

    def is_hostile(self, a: str, b: str) -> bool:
        return self.relation(a, b) is Relation.HOSTILE

    def is_allied(self, a: str, b: str) -> bool:
        return self.relation(a, b) is Relation.ALLIED

    def is_neutral(self, a: str, b: str) -> bool:
        return self.relation(a, b) is Relation.NEUTRAL

    def declarations(self) -> list[tuple[str, str, Relation]]:
        """明確宣告的（非預設）配對，供想定匯出（O7.3）。回傳確定性排序。"""
        out = [(min(p), max(p), rel) for p, rel in self._pairs.items()]
        return sorted(out, key=lambda t: (t[0], t[1]))

    def set_relation(self, a: str, b: str, rel: Relation, *, tick: int) -> LedgerEvent:
        """局中調整關係（宣戰/停火）→ 回 FACTION_RELATION_CHANGED 事件供寫入 Ledger。"""
        if a == b:
            raise ValueError("不可設定陣營對自己的關係")
        self._pairs[_key(a, b)] = rel
        return LedgerEvent(
            event_type="FACTION_RELATION_CHANGED",
            tick=tick,
            ai_decision={"factions": sorted([a, b]), "relation": rel.value},
        )
