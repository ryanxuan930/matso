"""該局關係矩陣的讀取（#98）——執行期取得 `WargameSession.factionRelations`。

與 `relations.py` 分開的原因：那支是**純**模組（敵我判斷的數學與語義，不碰 DB），
本支才碰 DB。任何需要「這一局的敵我關係」的子系統（偵測 sweep、AI worker、API）都走這裡，
避免各自到處 query 或各自退回不同的預設。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.factions.relations import FactionRelations, relations_from_triples
from app.models.tables import WargameSession


def load_session_relations(db: Session, session_id: str) -> FactionRelations:
    """讀該局的關係矩陣。查無 session 或欄位為 NULL → 全 HOSTILE 預設（既有局的語義）。"""
    row = db.get(WargameSession, session_id)
    return relations_from_triples(row.faction_relations if row is not None else None)
