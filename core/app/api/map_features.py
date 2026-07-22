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

from app.api.deps import get_current_user, get_db, get_gateway
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError, OrderValidationError, SessionNotFoundError
from app.factions import WHITE_CELL, validate_faction_id
from app.footprint import compute_footprint, haversine_m
from app.models import MapFeature, WargameSession
from app.orders.precheck import PhysicsGateway
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["map-features"])

_GEOMETRY_TYPES = {"POINT", "LINE", "POLYGON"}
# 地形裁切取樣上限：防止單一請求觸發過量 terrain RPC（每方位一次 has_los）。
_MAX_FOOTPRINT_STEPS = 72
_MAX_FOOTPRINT_RANGE_M = 60_000.0


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


class TerrainFootprintRequest(BaseModel):
    """武器射向/雷達扇區的地形裁切請求（#11）——射源、扇形、射程 + 觀測/目標離地高。"""

    origin: list[float]  # [lng, lat]（同 MapFeature POINT 存放格式）
    max_range_m: float = Field(gt=0)
    direction_deg: float | None = None  # 扇形中心方位（北為 0、順時針）；全圓可省
    arc_deg: float | None = None  # 張角；None 或 ≥360 → 全圓（雷達）
    steps: int = 24  # 方位取樣數（伺服端夾至上限）
    observer_height_m: float = 10.0  # 射源/雷達離地高（桅杆/光學）
    target_height_m: float = 2.0  # 目標/障礙離地高（#11 default 2m）


class TerrainFootprintView(BaseModel):
    """地形裁切後的射界多邊形（GeoJSON 環）+ 是否有方位被地形限制。"""

    ring: list[list[float]]  # [[lng, lat], …] 閉合環
    clipped: bool
    max_range_m: float


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


@router.post("/{session_id}/terrain/footprint", response_model=TerrainFootprintView)
def terrain_footprint(
    session_id: str,
    body: TerrainFootprintRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    gateway: PhysicsGateway = Depends(get_gateway),
) -> TerrainFootprintView:
    """武器/雷達射界的地形裁切（viewshed fan，#11）。

    逐方位對 terrain gateway 查 LOS，取地形遮蔽前的最大通視距離 → 裁切後射界多邊形。
    紅線：物理事實（可見/餘隙）由 terrain 裁決，AI 不介入。terrain 不可達 → 503（前端退回幾何）。
    """
    _session_or_404(db, session_id)
    if not is_omniscient(user.role):
        require_participant(db, user, session_id)  # 須為此 session 參與者
    if len(body.origin) < 2:
        raise OrderValidationError("origin 需為 [lng, lat]", error_code="MAP_FEATURE_BAD_GEOMETRY")
    lng, lat = float(body.origin[0]), float(body.origin[1])
    max_range = min(body.max_range_m, _MAX_FOOTPRINT_RANGE_M)
    steps = max(3, min(body.steps, _MAX_FOOTPRINT_STEPS))

    def los_range(
        obs: tuple[float, float, float], tgt: tuple[float, float, float]
    ) -> tuple[bool, float]:
        out = gateway.has_los(obs, tgt)
        if out.visible:
            return True, max_range
        if out.obstruction_lat is not None and out.obstruction_lng is not None:
            return False, haversine_m(obs[0], obs[1], out.obstruction_lat, out.obstruction_lng)
        return False, 0.0

    fp = compute_footprint(
        lng=lng,
        lat=lat,
        max_range_m=max_range,
        direction_deg=body.direction_deg,
        arc_deg=body.arc_deg,
        steps=steps,
        observer_height_m=body.observer_height_m,
        target_height_m=body.target_height_m,
        los_range=los_range,
    )
    return TerrainFootprintView(ring=fp.ring, clipped=fp.clipped, max_range_m=max_range)
