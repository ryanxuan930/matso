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

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.supply import SupplyClass
from app.api.deps import get_current_user, get_db, get_gateway
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.engine.supply_points import SUPPLY_POINT_KIND
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
    # 變更歸屬（WHITE_CELL＝共同層）。**僅全知可用**——見 edit_map_feature 的檢查。
    owner_faction: str | None = None


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


def _check_supply_point(
    kind: str, geometry_type: str, geometry: Any, owner: str, attributes: dict[str, Any]
) -> None:
    """補給點（WP-C7.2）的三道前置檢查。**其他 kind 完全不受影響**。

    這三件事在此之前都是「存得進去、讀得回來、實際沒效果」——
    `engine/supply_points.read_point()` 對它們一律**靜默回 None**（略過該筆，不毀掉整局），
    於是白軍在 COP 上圈了一個補給點、清單裡看得到、地圖上畫得出來，
    而撥交端 `load_points()` 根本不認得它。畫的人沒有任何線索。
    靜默是那個設計對的選擇（一筆髒資料不該讓整局的補給停擺）；**把話講在建立當下**才是這裡的事。

    1. **只認 POINT，且幾何要真的是 `[lng, lat]`**：`read_point` 解不開就整筆略過。
    2. **不可落在 WHITE_CELL 共同層**：`nearest_usable()` 只找**同陣營**的補給點
       （盟軍共用是後勤協定問題，預設不共用），共同層的補給點沒有任何單位撥交得到。
    3. **`stock` 必須宣告且類別要認得**：`read_point` 對認不得的類別 `continue`
       ——打錯一個字母，那一格庫存就人間蒸發。空倉庫要明寫 `{"I": 0}`，
       那與「忘了填」是不同的意思。
    """
    if kind != SUPPLY_POINT_KIND:
        return
    coords = geometry if isinstance(geometry, list) else []
    if geometry_type != "POINT" or len(coords) < 2 or not _all_numbers(coords[:2]):
        raise OrderValidationError(
            "補給點只認 POINT 幾何（[lng, lat]）——存成線/面的補給點，撥交端讀不到它。",
            error_code="MAP_FEATURE_SUPPLY_POINT_GEOMETRY",
        )
    if owner == WHITE_CELL:
        raise OrderValidationError(
            "補給點必須歸屬某一作戰陣營：補給只撥交給同陣營單位，"
            "掛在共同層（WHITE_CELL）的補給點沒有任何單位拉得到。",
            error_code="MAP_FEATURE_SUPPLY_POINT_FACTION",
        )
    stock = attributes.get("stock")
    if not isinstance(stock, dict) or not stock:
        raise OrderValidationError(
            '補給點必須宣告庫存 attributes.stock，例：{"I": 500, "IX": 80}'
            "（類別 I 口糧水、III 油料、V 彈藥、IX 維修件；空倉庫請明寫 0）。",
            error_code="MAP_FEATURE_SUPPLY_POINT_STOCK",
        )
    known = {c.value for c in SupplyClass}
    for key, value in stock.items():
        if str(key) not in known:
            raise OrderValidationError(
                f"未知補給類別：{key}（限 {'/'.join(sorted(known))}）——"
                "撥交端會靜默略過認不得的類別，那一格庫存等於不存在。",
                error_code="MAP_FEATURE_SUPPLY_POINT_STOCK",
            )
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise OrderValidationError(
                f"補給類別 {key} 的庫存須為非負數，實得：{value!r}",
                error_code="MAP_FEATURE_SUPPLY_POINT_STOCK",
            )


def _all_numbers(values: list[Any]) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)


@router.get("/{session_id}/map-features", response_model=list[MapFeatureView])
def list_map_features(
    session_id: str,
    as_faction: str | None = Query(None, description="White Cell 視角：以某陣營視角看標註（#92）"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MapFeatureView]:
    stmt = select(MapFeature).where(MapFeature.session_id == session_id)
    omniscient = is_omniscient(user.role)
    if as_faction is not None:
        # 視角切換（#92）：僅全知可指定；與 units/intel 同紀律（不信任 client 帶的陣營）。
        if not omniscient:
            raise AuthForbiddenError("僅 White Cell 可切換視角")
        stmt = stmt.where(
            MapFeature.owner_faction.in_([WHITE_CELL, validate_faction_id(as_faction)])
        )
    elif not omniscient:
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
    _check_supply_point(body.kind, gt, body.geometry, owner, dict(body.attributes or {}))
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
    if edit.owner_faction is not None:
        # 轉移歸屬僅全知可為：一般角色若能改，等同可把標註轉給他軍、或逕自發布到共同層
        # （WHITE_CELL）讓全體看見——那會繞過 fog of war。
        if not is_omniscient(user.role):
            raise AuthForbiddenError("僅 White Cell 可變更標註歸屬")
        feat.owner_faction = validate_faction_id(edit.owner_faction)
    # 補給點的三道檢查跑在**合併之後的最終狀態**上：PATCH 對 attributes 是 merge，
    # 只看這次送來的欄位會漏掉「原本就壞」與「這次把它改壞」兩種情形
    # （例如把一個既有標註的 kind 改成 SUPPLY_POINT，庫存從來就沒有過）。
    try:
        _check_supply_point(
            feat.kind,
            feat.geometry_type,
            feat.geometry,
            feat.owner_faction,
            dict(feat.attributes or {}),
        )
    except OrderValidationError:
        db.rollback()  # 檢查跑在套用之後（要看合併結果）→ 失敗就把這次的變更整批丟掉
        raise
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
