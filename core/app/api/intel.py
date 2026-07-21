"""Faction-scoped intel 查詢端點（O3.3，SPEC §16.1）。

GET /api/v1/sessions/{session_id}/intel?faction=RED → 該 faction 的敵情視圖（去識別化）。

⚠ faction 目前由 query 參數帶入（**O7.5 RBAC 落地後改由認證主體推導，不信任 client**）。
後端 fog-of-war 強制在 IntelService/store（一律以 faction 過濾，永不回 ground truth）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.factions import validate_faction_id
from app.intel.schemas import ContactView
from app.intel.service import IntelService

router = APIRouter(prefix="/api/v1/sessions", tags=["intel"])


@router.get("/{session_id}/intel", response_model=list[ContactView])
def get_intel(
    session_id: str,
    faction: str,
    db: Session = Depends(get_db),
) -> list[ContactView]:
    return IntelService(db).visible_contacts(session_id, validate_faction_id(faction))
