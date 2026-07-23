"""物理預檢（O3.1，SPEC §2.3 步驟 [2]）——同步 <50ms，呼叫 terrain 判物理可行性。

**紅線**：物理事實（可達/可見/射程）由 terrain（確定性）裁決，AI 永不介入。不可行 → 立即
REJECTED（見 service）。terrain 不可達 → TerrainUnavailableError 冒泡（API 轉 503，硬依賴）。

依賴以 `PhysicsGateway` Protocol 注入，測試可用假 gateway，不需真 gRPC/terrain server。
`TerrainGatewayAdapter` 為真 TerrainClient 的轉接。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.adjudication.trajectory import ArcObstacle

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.weapon import WeaponProfile
from app.factions import FactionRelations
from app.models.tables import EquipmentInstance, EquipmentTemplate, TacticalUnit
from app.orders.schemas import (
    EngagePayload,
    MovePayload,
    PrecheckCheck,
    PrecheckResult,
)
from app.orders.validator import ValidatedOrder

_HEX_RES = 8  # 戰術預設解析度（與 terrain hex grid 一致）
# 交戰觀測高：車載光學/桅杆/前觀 OP 的等效離地高（非單兵 2m 站姿）。避免每個 2m 微起伏都遮斷
# 「地圖上看起來很近」的兩單位，同時真實山脊仍會擋住視線。weapon 專屬高度於 O3.2。
_ENGAGE_OBS_M = 10.0


@dataclass(frozen=True, slots=True)
class LosOutcome:
    """視線查詢結果——含遮蔽點與最小餘隙，供預檢產生可解釋的說明。"""

    visible: bool
    clearance_m: float
    obstruction_lat: float | None = None
    obstruction_lng: float | None = None


def _haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


# 預檢項名稱 → 契約 error code（不可行時取第一個失敗項）
_CHECK_ERROR_CODES = {
    "position": "ORDER_UNIT_NO_POSITION",
    "reachability": "ORDER_UNREACHABLE",
    "target_exists": "ORDER_TARGET_NOT_FOUND",
    "line_of_sight": "ORDER_NO_LOS",
    "trajectory": "ORDER_NO_LOS",  # 彈道飛彈拋物線被地形/障礙阻隔（沿用 NO_LOS 語義：無法命中）
    "roe": "ORDER_ROE_VIOLATION",
    "weapon": "ORDER_INVALID_PAYLOAD",
    "range": "ORDER_OUT_OF_RANGE",
    "ammo": "ORDER_NO_AMMO",
}


class PhysicsGateway(Protocol):
    """物理預檢所需的 terrain 查詢（領域介面，隔離 gRPC 細節）。"""

    def path_reachable(
        self, from_h3: str, to_h3: str, mobility_profile: str
    ) -> tuple[bool, str]: ...

    def has_los(
        self, observer: tuple[float, float, float], target: tuple[float, float, float]
    ) -> LosOutcome: ...

    # 彈道飛彈拋物線淨空所需的地形高程取樣（可選；缺此方法的 gateway 退回僅障礙判定）。
    def elevation(self, lat: float, lng: float) -> float: ...


def run_precheck(
    db: Session,
    validated: ValidatedOrder,
    gateway: PhysicsGateway,
    relations: FactionRelations | None = None,
) -> PrecheckResult:
    """依 order 類型跑對應物理檢查，回 PrecheckResult（feasible + 各項）。

    relations=None 時退回全 HOSTILE 預設（N 方前語義相容；ENGAGE 對非敵陣營→ROE 攔）。
    """
    payload = validated.payload
    rel = relations or FactionRelations()
    if isinstance(payload, MovePayload):
        checks = _precheck_move(validated.unit, payload, gateway)
    elif isinstance(payload, EngagePayload):
        checks = _precheck_engage(db, validated.unit, payload, gateway, rel)
    else:
        checks = []  # 其餘類型（RECON/RESUPPLY/POSTURE）之物理檢查於 O3.x
    feasible = all(c.passed for c in checks)
    reason = None if feasible else next(c.detail for c in checks if not c.passed)
    return PrecheckResult(feasible=feasible, checks=checks, reason=reason)


def precheck_error_code(result: PrecheckResult) -> str:
    """回傳第一個失敗項對應的契約 error code（供 API 422）。"""
    for check in result.checks:
        if not check.passed:
            return _CHECK_ERROR_CODES.get(check.name, "ORDER_PRECHECK_FAILED")
    return "ORDER_PRECHECK_FAILED"


def _precheck_move(
    unit: TacticalUnit, payload: MovePayload, gateway: PhysicsGateway
) -> list[PrecheckCheck]:
    if unit.current_lat is None or unit.current_lng is None:
        return [PrecheckCheck(name="position", passed=False, detail="單位無座標，無法規劃移動")]
    from_h3 = h3.latlng_to_cell(unit.current_lat, unit.current_lng, _HEX_RES)
    reachable, detail = gateway.path_reachable(from_h3, payload.to_h3, payload.mobility_profile)
    return [PrecheckCheck(name="reachability", passed=reachable, detail=detail)]


def _precheck_engage(
    db: Session,
    unit: TacticalUnit,
    payload: EngagePayload,
    gateway: PhysicsGateway,
    relations: FactionRelations,
) -> list[PrecheckCheck]:
    target = db.get(TacticalUnit, payload.target_unit_id)
    if target is None or target.session_id != unit.session_id:
        return [
            PrecheckCheck(name="target_exists", passed=False, detail="目標單位不存在於此 session")
        ]
    # ROE：只能打敵對陣營（§12.1）——打盟軍/中立一律拒（friendly fire / 攻中立）。
    if not relations.is_hostile(unit.faction, target.faction):
        rel = relations.relation(unit.faction, target.faction).value
        detail = f"目標陣營關係為 {rel}，非敵對，禁止交戰"
        return [PrecheckCheck(name="roe", passed=False, detail=detail)]
    if (
        unit.current_lat is None
        or unit.current_lng is None
        or target.current_lat is None
        or target.current_lng is None
    ):
        return [PrecheckCheck(name="position", passed=False, detail="射手或目標無座標")]

    # 解析武器（決定可達性檢查型別：直瞄 LOS / 間瞄免視線 / 飛彈射程或拋物線淨空）。
    profile, tmpl, weapon_fail = _resolve_weapon(db, unit, payload)
    if weapon_fail is not None:
        return [weapon_fail]

    reach = _reachability_check(db, unit, target, profile, gateway)
    if not reach.passed:
        return [reach]
    if profile is None or tmpl is None:
        return [reach]  # 無裝備 → 僅 LOS（維持既有測試綠）
    return [reach, *_range_ammo_checks(unit, target, profile, tmpl, payload)]


def _resolve_weapon(
    db: Session, unit: TacticalUnit, payload: EngagePayload
) -> tuple[WeaponProfile | None, EquipmentTemplate | None, PrecheckCheck | None]:
    """解析單位（或選定）武器 → (profile, tmpl, 失敗檢查)。無裝備 → (None, None, None)。"""
    instances = (
        db.execute(select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id))
        .scalars()
        .all()
    )
    if not instances:
        return None, None, None
    if payload.weapon_id is not None:
        inst = next((i for i in instances if i.id == payload.weapon_id), None)
        if inst is None:
            fail = PrecheckCheck(
                name="weapon", passed=False, detail=f"指定武器不屬於此單位：{payload.weapon_id}"
            )
            return None, None, fail
    else:
        inst = instances[0]
    tmpl = db.get(EquipmentTemplate, inst.template_id)
    if tmpl is None:
        return None, None, PrecheckCheck(name="weapon", passed=False, detail="武器模板遺失")
    try:
        profile = WeaponProfile.from_base_stats(tmpl.base_stats)
    except ValueError as exc:
        return None, None, PrecheckCheck(name="weapon", passed=False, detail=f"武器參數無效：{exc}")
    return profile, tmpl, None


def _reachability_check(
    db: Session,
    unit: TacticalUnit,
    target: TacticalUnit,
    profile: WeaponProfile | None,
    gateway: PhysicsGateway,
) -> PrecheckCheck:
    """依飛行剖面判可達性（#飛彈）：直瞄→LOS；間瞄→免視線；巡弋→僅射程；彈道→拋物線淨空。"""
    assert unit.current_lat is not None and unit.current_lng is not None
    assert target.current_lat is not None and target.current_lng is not None
    dist = _haversine_km(unit.current_lat, unit.current_lng, target.current_lat, target.current_lng)

    if profile is not None and profile.missile:
        if profile.maneuverable:
            return PrecheckCheck(
                name="trajectory",
                passed=True,
                detail=f"可變軌飛彈，末端機動繞過，僅判射程（{dist:.1f} km）",
            )
        return _ballistic_trajectory_check(db, unit, target, profile, gateway, dist)
    if profile is not None and profile.indirect_fire:
        return PrecheckCheck(
            name="line_of_sight", passed=True, detail=f"間瞄彈道越過地形，免視線（{dist:.1f} km）"
        )
    # 直瞄（或無裝備）→ LOS。
    out = gateway.has_los(
        (unit.current_lat, unit.current_lng, _ENGAGE_OBS_M),
        (target.current_lat, target.current_lng, _ENGAGE_OBS_M),
    )
    if out.visible:
        clr = "" if not math.isfinite(out.clearance_m) else f"，最小餘隙 {out.clearance_m:.0f}m"
        detail = f"視線通暢（直線 {dist:.1f} km）{clr}"
    else:
        loc = (
            f"（{out.obstruction_lat:.4f}, {out.obstruction_lng:.4f}）"
            if out.obstruction_lat is not None
            else ""
        )
        deficit = abs(out.clearance_m) if math.isfinite(out.clearance_m) else 0.0
        detail = (
            f"地形遮蔽：{dist:.1f} km 直線視線於{loc}附近被地形擋住，"
            f"最低點高出視線約 {deficit:.0f} m（觀測/目標離地各 {_ENGAGE_OBS_M:.0f} m）"
        )
    return PrecheckCheck(name="line_of_sight", passed=out.visible, detail=detail)


def _ballistic_trajectory_check(
    db: Session,
    unit: TacticalUnit,
    target: TacticalUnit,
    profile: WeaponProfile,
    gateway: PhysicsGateway,
    dist_km: float,
) -> PrecheckCheck:
    """彈道飛彈拋物線淨空：地圖障礙（含高度）+ 地形高程是否阻擋拋物線（#飛彈）。"""
    from app.adjudication.trajectory import obstacle_blocks_arc, terrain_blocks_arc

    assert unit.current_lat is not None and unit.current_lng is not None
    assert target.current_lat is not None and target.current_lng is not None
    shooter = (unit.current_lng, unit.current_lat)
    tgt = (target.current_lng, target.current_lat)
    ground_range_m = dist_km * 1000.0

    # 1) 地圖障礙（地圖編輯器建立、含高度）。
    obstacles = _load_arc_obstacles(db, unit.session_id)
    ob = obstacle_blocks_arc(shooter, tgt, obstacles, apex_ratio=profile.apex_ratio)
    if ob.blocked:
        return PrecheckCheck(
            name="trajectory", passed=False, detail=f"拋物線被障礙阻隔：{ob.detail}"
        )

    # 2) 地形高程（沿弧線取樣；gateway 無 elevation 或失敗則跳過，僅障礙判定）。
    samples = _sample_terrain(gateway, shooter, tgt, steps=10)
    if samples is not None:
        s_elev = _elev(gateway, unit.current_lat, unit.current_lng) or 0.0
        t_elev = _elev(gateway, target.current_lat, target.current_lng) or 0.0
        tb = terrain_blocks_arc(
            samples, s_elev, t_elev, ground_range_m, apex_ratio=profile.apex_ratio
        )
        if tb.blocked:
            return PrecheckCheck(
                name="trajectory", passed=False, detail=f"拋物線被地形阻隔：{tb.detail}"
            )
    return PrecheckCheck(
        name="trajectory", passed=True, detail=f"彈道飛彈拋物線淨空（{dist_km:.1f} km）"
    )


def _load_arc_obstacles(db: Session, session_id: str) -> list[ArcObstacle]:
    """載入本 session 可阻擋拋物線的障礙（障礙/建築/地形，含 attributes.height_m）。"""
    from app.adjudication.trajectory import ArcObstacle
    from app.models.tables import MapFeature

    rows = db.execute(select(MapFeature).where(MapFeature.session_id == session_id)).scalars().all()
    out: list[ArcObstacle] = []
    for f in rows:
        if f.kind not in ("OBSTACLE", "BUILDING", "TERRAIN"):
            continue
        attrs = f.attributes or {}
        h = attrs.get("height_m")
        height = float(h) if isinstance(h, (int, float)) else (2.0 if f.kind != "TERRAIN" else 0.0)
        if height <= 0.0:
            continue
        coords = _coerce_coords(f.geometry_type, f.geometry)
        if not coords:
            continue
        radius = f.influence_radius_m
        out.append(
            ArcObstacle(
                feature_id=f.id,
                kind=f.kind,
                geometry_type=str(f.geometry_type).upper(),
                coords=coords,
                height_m=height,
                radius_m=float(radius) if isinstance(radius, (int, float)) else 0.0,
            )
        )
    return out


def _coerce_coords(gtype: str, geom: Any) -> tuple[tuple[float, float], ...]:
    try:
        gt = str(gtype).upper()
        if gt == "POINT":
            return ((float(geom[0]), float(geom[1])),)
        if gt in ("LINE", "POLYGON"):
            pts = tuple((float(p[0]), float(p[1])) for p in geom if len(p) >= 2)
            return pts if len(pts) >= 2 else ()
    except (TypeError, ValueError, IndexError, KeyError):
        return ()
    return ()


def _elev(gateway: PhysicsGateway, lat: float, lng: float) -> float | None:
    fn = getattr(gateway, "elevation", None)
    if not callable(fn):
        return None
    try:
        return float(fn(lat, lng))
    except Exception:
        return None


def _sample_terrain(
    gateway: PhysicsGateway, s: tuple[float, float], t: tuple[float, float], *, steps: int
) -> list[tuple[float, float]] | None:
    """沿 s→t 取 steps 個中間點的地形高程；gateway 無 elevation → None（跳過地形判定）。"""
    if not callable(getattr(gateway, "elevation", None)):
        return None
    samples: list[tuple[float, float]] = []
    for i in range(1, steps):
        frac = i / steps
        lng = s[0] + (t[0] - s[0]) * frac
        lat = s[1] + (t[1] - s[1]) * frac
        e = _elev(gateway, lat, lng)
        if e is None:
            return None
        samples.append((frac, e))
    return samples


def _range_ammo_checks(
    unit: TacticalUnit,
    target: TacticalUnit,
    profile: WeaponProfile,
    tmpl: EquipmentTemplate,
    payload: EngagePayload,
) -> list[PrecheckCheck]:
    """射程 + 彈種檢查（資料驅動 baseStats）。"""
    assert unit.current_lat is not None and unit.current_lng is not None
    assert target.current_lat is not None and target.current_lng is not None
    dist_m = (
        _haversine_km(unit.current_lat, unit.current_lng, target.current_lat, target.current_lng)
        * 1000.0
    )
    envelope = f"[{profile.min_range_m:.0f}, {profile.max_range_m:.0f}] m"
    in_range = profile.in_envelope(dist_m)
    range_check = PrecheckCheck(
        name="range",
        passed=in_range,
        detail=(
            f"距離 {dist_m:.0f} m 位於 {tmpl.name} 射程包絡 {envelope} 內"
            if in_range
            else f"距離 {dist_m:.0f} m 超出 {tmpl.name} 射程包絡 {envelope}"
        ),
    )
    if not in_range:
        return [range_check]

    available = ", ".join(profile.ammo_types)
    ammo_ok = payload.ammo_type is None or payload.ammo_type in profile.ammo_types
    if not ammo_ok:
        ammo_detail = f"{tmpl.name} 不支援彈種 {payload.ammo_type}（可用：{available}）"
    elif payload.ammo_type is None:
        ammo_detail = f"未指定彈種，使用 {tmpl.name} 預設（可用：{available}）"
    else:
        ammo_detail = f"彈種 {payload.ammo_type} 可用（{tmpl.name}）"
    ammo_check = PrecheckCheck(name="ammo", passed=ammo_ok, detail=ammo_detail)
    return [range_check, ammo_check]


class TerrainGatewayAdapter:
    """真 PhysicsGateway：轉接 app.plugins.TerrainClient 的 gRPC 回應為領域結果。"""

    def __init__(self, client: object) -> None:
        self._client = client  # app.plugins.TerrainClient（避免 import 環：鴨子型別）

    def path_reachable(self, from_h3: str, to_h3: str, mobility_profile: str) -> tuple[bool, str]:
        resp = self._client.get_path(from_h3, to_h3, mobility_profile)  # type: ignore[attr-defined]
        detail = f"cost={resp.total_cost:.1f}, eta={resp.eta_ticks}" if resp.reachable else "不可達"
        return resp.reachable, detail

    def has_los(
        self, observer: tuple[float, float, float], target: tuple[float, float, float]
    ) -> LosOutcome:
        resp = self._client.check_los(observer, target)  # type: ignore[attr-defined]
        if resp.visible:
            return LosOutcome(True, resp.fresnel_clearance)
        op = resp.obstruction_point
        return LosOutcome(False, resp.fresnel_clearance, op.lat, op.lng)

    def elevation(self, lat: float, lng: float) -> float:
        """地形高程（公尺）——供彈道飛彈拋物線淨空取樣（#飛彈）。"""
        resp = self._client.get_elevation(lat, lng)  # type: ignore[attr-defined]
        return float(resp.elevation)
