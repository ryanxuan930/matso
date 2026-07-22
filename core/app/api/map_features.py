"""地圖標註/工事（MapFeature）CRUD（stage ③）——武器據點、障礙、建築、控制措施（點/線/面）。

GET    /api/v1/sessions/{id}/map-features           列出可見標註（fog of war）
POST   /api/v1/sessions/{id}/map-features           新增標註
PATCH  /api/v1/sessions/{id}/map-features/{fid}     編輯
DELETE /api/v1/sessions/{id}/map-features/{fid}     移除

可見性（後端過濾，紅線 #3）：全知見全部；否則見共同（ownerFaction=WHITE_CELL）+ 本軍標注。
編修權：全知編任一；一般指揮官/幕僚僅編本軍標注（ownerFaction=本軍）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError, OrderValidationError, SessionNotFoundError
from app.factions import WHITE_CELL, validate_faction_id
from app.models import MapFeature, WargameSession
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["map-features"])

_GEOMETRY_TYPES = {"POINT", "LINE", "POLYGON"}


class MapFeatureView(BaseModel):
    id: str
    kind: str
    geometry_type: str
    geometry: Any
    owner_faction: str
    label: str | None
    influence_radius_m: float | None
    weapon_template_id: str | None
    attributes: dict[str, Any]


class MapFeatureCreate(BaseModel):
    kind: str
    geometry_type: str
    geometry: Any
    owner_faction: str | None = None  # 全知可指定（含 WHITE_CELL 共同）；否則一律本軍
    label: str | None = None
    influence_radius_m: float | None = None
    weapon_template_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class MapFeatureEdit(BaseModel):
    kind: str | None = None
    geometry_type: str | None = None
    geometry: Any | None = None
    label: str | None = None
    influence_radius_m: float | None = None
    weapon_template_id: str | None = None
    attributes: dict[str, Any] | None = None


def _view(f: MapFeature) -> MapFeatureView:
    return MapFeatureView(
        id=f.id,
        kind=f.kind,
        geometry_type=f.geometry_type,
        geometry=f.geometry,
        owner_faction=f.owner_faction,
        label=f.label,
        influence_radius_m=f.influence_radius_m,
        weapon_template_id=f.weapon_template_id,
        attributes=dict(f.attributes or {}),
    )


def _session_or_404(db: Session, session_id: str) -> WargameSession:
    session = db.get(WargameSession, session_id)
    if session is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")
    return session


def _check_geometry_type(geometry_type: str) -> str:
    gt = geometry_type.upper()
    if gt not in _GEOMETRY_TYPES:
        raise OrderValidationError(
            f"未知幾何型別：{geometry_type}（POINT/LINE/POLYGON）",
            error_code="MAP_FEATURE_BAD_GEOMETRY",
        )
    return gt


@router.get("/{session_id}/map-features", response_model=list[MapFeatureView])
def list_map_features(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MapFeatureView]:
    stmt = select(MapFeature).where(MapFeature.session_id == session_id)
    if not is_omniscient(user.role):
        participant = require_participant(db, user, session_id)
        # fog of war：共同（WHITE_CELL）+ 本軍標注（後端過濾）。
        stmt = stmt.where(MapFeature.owner_faction.in_([WHITE_CELL, participant.faction]))
    return [_view(f) for f in db.execute(stmt).scalars().all()]


@router.post(
    "/{session_id}/map-features",
    response_model=MapFeatureView,
    status_code=status.HTTP_201_CREATED,
)
def create_map_feature(
    session_id: str,
    body: MapFeatureCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MapFeatureView:
    _session_or_404(db, session_id)
    gt = _check_geometry_type(body.geometry_type)
    # ownerFaction：全知可指定（含 WHITE_CELL 共同層）；一般角色一律本軍。
    if is_omniscient(user.role):
        owner = validate_faction_id(body.owner_faction) if body.owner_faction else WHITE_CELL
    else:
        participant = require_participant(db, user, session_id)
        if body.owner_faction and validate_faction_id(body.owner_faction) != participant.faction:
            raise AuthForbiddenError("僅可標注本軍圖層")
        owner = participant.faction
    feat = MapFeature(
        session_id=session_id,
        kind=body.kind,
        geometry_type=gt,
        geometry=body.geometry,
        owner_faction=owner,
        label=body.label,
        influence_radius_m=body.influence_radius_m,
        weapon_template_id=body.weapon_template_id,
        attributes=dict(body.attributes or {}),
    )
    db.add(feat)
    db.commit()
    return _view(feat)


def _feature_for_edit(db: Session, user: CurrentUser, session_id: str, fid: str) -> MapFeature:
    feat = db.get(MapFeature, fid)
    if feat is None or feat.session_id != session_id:
        raise AuthForbiddenError("查無此標註")
    if not is_omniscient(user.role):
        participant = require_participant(db, user, session_id)
        if feat.owner_faction != participant.faction:
            raise AuthForbiddenError("無權編修他方/共同標註")
    return feat


@router.patch("/{session_id}/map-features/{fid}", response_model=MapFeatureView)
def edit_map_feature(
    session_id: str,
    fid: str,
    edit: MapFeatureEdit,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MapFeatureView:
    _session_or_404(db, session_id)
    feat = _feature_for_edit(db, user, session_id, fid)
    if edit.kind is not None:
        feat.kind = edit.kind
    if edit.geometry_type is not None:
        feat.geometry_type = _check_geometry_type(edit.geometry_type)
    if edit.geometry is not None:
        feat.geometry = edit.geometry
    if edit.label is not None:
        feat.label = edit.label
    if edit.influence_radius_m is not None:
        feat.influence_radius_m = edit.influence_radius_m
    if edit.weapon_template_id is not None:
        feat.weapon_template_id = edit.weapon_template_id
    if edit.attributes is not None:
        feat.attributes = {**(feat.attributes or {}), **edit.attributes}
    db.commit()
    return _view(feat)


@router.delete("/{session_id}/map-features/{fid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_map_feature(
    session_id: str,
    fid: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    _session_or_404(db, session_id)
    feat = _feature_for_edit(db, user, session_id, fid)
    db.delete(feat)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
