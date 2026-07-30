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

from app.adjudication.weapon import INDIRECT_CATEGORIES, WeaponProfile
from app.factions import FactionRelations
from app.models.enums import RequestKind, RequestStatus
from app.models.tables import (
    EquipmentInstance,
    EquipmentTemplate,
    Request,
    TacticalUnit,
    WargameSession,
)
from app.movement.mobility import resolve_unit_mobility
from app.orders.mission import (
    DefendParams,
    MissionPayload,
    SeizeParams,
)
from app.orders.no_strike import ZoneClass, load_no_strike_cells
from app.orders.roe import load_session_roe
from app.orders.schemas import (
    EngagePayload,
    FireMissionPayload,
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
    # WP-A3 禁射區：NO_STRIKE 一律拒；RESTRICTED_FIRE 未經 override 亦拒（可由使用者確認後放行）。
    "no_strike": "ORDER_NO_STRIKE_ZONE",
    # WP-B6 想定 ROE 禁用武器（明確指名被禁武器的令）。與友軍誤傷共用 ROE 違規碼。
    "roe_weapon": "ORDER_ROE_VIOLATION",
    "fire_approval": "ORDER_FIRE_APPROVAL_REQUIRED",
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


def _no_strike_at(
    db: Session, session_id: str, lat: float, lng: float, acknowledged: bool
) -> list[PrecheckCheck]:
    """WP-A3 禁射區：**某個座標**是否落在保護區。

    與護欄 G4 同一份格集、同一條規則——人與 AI 受同樣約束（差別只在人可對 RESTRICTED_FIRE
    明確 override，AI 則是升白軍確認）。無宣告禁射區的局：一律通過（零行為變更）。

    **以座標為介面而不是以「目標單位」為介面**（WP-C10 收尾時改）：原本只吃 `EngagePayload`，
    於是面目標射擊（打座標）整個沒有禁射區保護——同一個座標，`ENGAGE` 打不了、
    `FIRE_MISSION` 卻可以。禁射區保護的是那塊地，不是站在上面的人。
    """
    zones = load_no_strike_cells(db, session_id)
    if not zones.any_cells:
        return []
    zone = zones.classify_latlng(lat, lng)
    if zone is None:
        return []
    if zone is ZoneClass.NO_STRIKE:
        return [
            PrecheckCheck(
                name="no_strike", passed=False, detail="目標位於禁射區（No-Strike），不得射擊"
            )
        ]
    if acknowledged:  # 限制射擊區：使用者已明確確認 → 放行，但留痕（service 寫 Ledger）
        return [
            PrecheckCheck(
                name="no_strike", passed=True, detail="目標位於限制射擊區——已由下令者明確確認"
            )
        ]
    return [
        PrecheckCheck(
            name="no_strike",
            passed=False,
            detail="目標位於限制射擊區（Restricted-Fire）；確認仍要射擊請重送並勾選確認",
        )
    ]


def _precheck_no_strike(
    db: Session, unit: TacticalUnit, payload: EngagePayload, acknowledged: bool
) -> list[PrecheckCheck]:
    """ENGAGE 的禁射區檢查：以**目標單位當下的座標**判定。"""
    target = db.get(TacticalUnit, payload.target_unit_id)
    if target is None or target.current_lat is None or target.current_lng is None:
        return []  # 目標不存在/無座標 → 交由既有的 target_exists / position 檢查回報
    return _no_strike_at(
        db,
        unit.session_id,
        float(target.current_lat),
        float(target.current_lng),
        acknowledged,
    )


def _precheck_fire_mission_no_strike(
    db: Session, unit: TacticalUnit, payload: FireMissionPayload, acknowledged: bool
) -> list[PrecheckCheck]:
    """FIRE_MISSION 的禁射區檢查：直接判**瞄準點**。

    比 ENGAGE 那條還單純——面射擊本來就是打一個座標，不必先找出目標單位在哪。
    """
    return _no_strike_at(db, unit.session_id, payload.target_lat, payload.target_lng, acknowledged)


def _precheck_roe_weapon(
    db: Session, unit: TacticalUnit, payload: EngagePayload
) -> list[PrecheckCheck]:
    """WP-B6 想定 ROE：**明確指名**的武器是否被本局 ROE 禁用。

    只擋「令面指名了被禁武器」這一種——沒指名武器的令交由裁決層逐武器篩（那裡是權威，
    人與 AI 都走同一條）。在 submit 端另擋一次的價值是**早退與留痕**：使用者當場知道
    「這場不准用飛彈」，而不是下了令、單位卻安靜地不開火。

    無 ROE 宣告的局：一律通過（零行為變更）。
    """
    if not payload.weapon_id:
        return []
    roe = load_session_roe(db, unit.session_id)
    forbidden = roe.forbidden_for(unit.faction)
    if not forbidden:
        return []
    inst = db.get(EquipmentInstance, payload.weapon_id)
    tmpl = db.get(EquipmentTemplate, inst.template_id) if inst is not None else None
    if tmpl is None:
        return []  # 武器不存在 → 交由既有的 weapon 檢查回報
    hit = next((x for x in (str(tmpl.category), str(tmpl.name)) if x in forbidden), None)
    if hit is None:
        return []
    reason = roe.reason_for(unit.faction, hit)
    detail = f"本局交戰規則禁用 {hit}"
    return [
        PrecheckCheck(
            name="roe_weapon", passed=False, detail=f"{detail}（{reason}）" if reason else detail
        )
    ]


def _unit_has_indirect(db: Session, unit: TacticalUnit) -> bool:
    rows = db.scalars(select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id)).all()
    for inst in rows:
        tmpl = db.get(EquipmentTemplate, inst.template_id)
        if tmpl is not None and str(tmpl.category) in INDIRECT_CATEGORIES:
            return True
    return False


def _precheck_fire_approval(
    db: Session, unit: TacticalUnit, payload: EngagePayload | FireMissionPayload
) -> list[PrecheckCheck]:
    """WP-B5.3 曲射火協：本局要求時，曲射交戰須掛一張已核准的 FIRE_SUPPORT 申請單。

    **與 ROE 武器禁令的關鍵差異**：ROE 只擋「令面指名了被禁武器」，其餘交裁決層逐武器篩；
    火協若照抄，**不指名武器就能繞過**——那就不是 gate。故本檢查在「未指名武器但單位持有
    任何曲射武器」時同樣要求核准單。

    混合編裝（步槍＋迫砲）未指名武器時會被擋，這是刻意的：要求火協的演習裡，
    要用直射就把武器指出來。訊息會講清楚怎麼做。

    未開啟本局開關 → 一律通過（既有局零行為變更）。

    **FIRE_MISSION 一律視為曲射**（WP-C10.2）：面目標射擊本來就是間瞄火力，
    不掛核准單就能打的話，等於用新令型繞過火協——與「不指名武器就繞過」是同一類洞。
    """
    session = db.get(WargameSession, unit.session_id)
    if session is None or not getattr(session, "indirect_fire_requires_approval", False):
        return []

    if isinstance(payload, FireMissionPayload):
        pass  # 面目標射擊本身即曲射，無論指名什麼武器都要火協
    elif payload.weapon_id:
        inst = db.get(EquipmentInstance, payload.weapon_id)
        tmpl = db.get(EquipmentTemplate, inst.template_id) if inst is not None else None
        if tmpl is None or str(tmpl.category) not in INDIRECT_CATEGORIES:
            return []  # 指名的是直射武器 → 不需火協
    elif not _unit_has_indirect(db, unit):
        return []  # 未指名，且該單位根本沒有曲射武器 → 不需火協

    if not payload.fire_request_id:
        return [
            PrecheckCheck(
                name="fire_approval",
                passed=False,
                detail=(
                    "本局曲射火力需火協核准：請附上已核准的火力支援申請，"
                    "或指名直射武器（weapon_id）"
                ),
            )
        ]
    req = db.get(Request, payload.fire_request_id)
    if (
        req is None
        or req.session_id != unit.session_id
        or req.faction != unit.faction
        or req.kind is not RequestKind.FIRE_SUPPORT
    ):
        return [
            PrecheckCheck(
                name="fire_approval", passed=False, detail="火力支援申請單不存在或不屬於本陣營"
            )
        ]
    if req.status is not RequestStatus.APPROVED:
        return [
            PrecheckCheck(
                name="fire_approval",
                passed=False,
                detail=f"火力支援申請尚未核准（目前 {req.status.value}）",
            )
        ]
    return []


def _precheck_fire_mission(
    db: Session, unit: TacticalUnit, payload: FireMissionPayload
) -> list[PrecheckCheck]:
    """面目標射擊的物理預檢（WP-C10.2）：單位須有曲射武器、目標須在射程內。

    **刻意不檢查 LOS**——間瞄火力打的就是看不見的地方，那正是它存在的理由。
    （直射的 ENGAGE 仍照舊檢查視線。）
    """
    checks: list[PrecheckCheck] = []
    if unit.current_lat is None or unit.current_lng is None:
        return [PrecheckCheck(name="position", passed=False, detail="射擊單位無座標")]

    rows = db.scalars(select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id)).all()
    best_range = 0.0
    for inst in rows:
        tmpl = db.get(EquipmentTemplate, inst.template_id)
        if tmpl is None or str(tmpl.category) not in INDIRECT_CATEGORIES:
            continue
        try:
            profile = WeaponProfile.from_base_stats(dict(tmpl.base_stats))
            best_range = max(best_range, profile.max_range_m)
        except (KeyError, ValueError):
            continue
    if best_range <= 0:
        return [
            PrecheckCheck(name="indirect_weapon", passed=False, detail="此單位無可用的曲射武器")
        ]

    dist_m = (
        _haversine_km(
            float(unit.current_lat), float(unit.current_lng), payload.target_lat, payload.target_lng
        )
        * 1000.0
    )
    checks.append(
        PrecheckCheck(
            name="range",
            passed=dist_m <= best_range,
            detail=f"距離 {dist_m / 1000:.1f} km / 最大射程 {best_range / 1000:.1f} km",
        )
    )
    return checks


def run_precheck(
    db: Session,
    validated: ValidatedOrder,
    gateway: PhysicsGateway,
    relations: FactionRelations | None = None,
    *,
    acknowledge_restricted: bool = False,
) -> PrecheckResult:
    """依 order 類型跑對應物理檢查，回 PrecheckResult（feasible + 各項）。

    relations=None 時退回全 HOSTILE 預設（N 方前語義相容；ENGAGE 對非敵陣營→ROE 攔）。
    `acknowledge_restricted`：下令者已明確確認「限制射擊區仍要射擊」（WP-A3）。
    """
    payload = validated.payload
    rel = relations or FactionRelations()
    if isinstance(payload, MovePayload):
        checks = _precheck_move(validated.unit, payload, gateway)
    elif isinstance(payload, FireMissionPayload):
        checks = _precheck_fire_mission(db, validated.unit, payload)
        # WP-C10 收尾：面射擊一樣要過禁射區。少了這行的話，同一個座標
        # `ENGAGE` 打不了、`FIRE_MISSION` 卻可以——那不是保護，是繞道。
        checks.extend(
            _precheck_fire_mission_no_strike(db, validated.unit, payload, acknowledge_restricted)
        )
        checks.extend(_precheck_fire_approval(db, validated.unit, payload))
    elif isinstance(payload, EngagePayload):
        checks = _precheck_engage(db, validated.unit, payload, gateway, rel)
        checks.extend(_precheck_no_strike(db, validated.unit, payload, acknowledge_restricted))
        checks.extend(_precheck_roe_weapon(db, validated.unit, payload))
        checks.extend(_precheck_fire_approval(db, validated.unit, payload))
    elif isinstance(payload, MissionPayload):
        checks = _precheck_mission(db, validated.unit, payload, gateway)
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


def _precheck_mission(
    db: Session, unit: TacticalUnit, payload: MissionPayload, gateway: PhysicsGateway
) -> list[PrecheckCheck]:
    """MISSION 令的可達性檢查（WP-A2）——**任務目標走得到嗎**。

    ⚠ 沒有這個分支的話，`run_precheck` 會掉進最後的 `else: checks = []`，
    而 `all([]) is True`——MISSION 令會**無條件通過預檢**。那是 fail-open，
    而且不會有任何測試自然發現（它不報錯，只是靜靜放行）。

    重用 `_precheck_move` 而非另寫一套：任務目標的可達性與移動目標的可達性是同一個問題，
    兩份實作必然漂移。`objective` 的取法逐任務型不同，取不到就不擋
    （SCREEN 的線、MOVE_MARCH 的航路點都有多個點，逐點檢查會讓一次下令打 N 次地形服務）。
    """
    objective = _mission_objective(payload)
    if objective is None:
        return [
            PrecheckCheck(
                name="mission_params",
                passed=True,
                detail="此任務型無單一目標點；可達性於分解時逐段檢查",
            )
        ]
    lat, lng = objective
    profile = resolve_unit_mobility(db, unit.id).profile
    move = MovePayload(
        to_h3=h3.latlng_to_cell(lat, lng, _HEX_RES),
        mobility_profile=profile,
        to_lat=lat,
        to_lng=lng,
    )
    return _precheck_move(unit, move, gateway)


def _mission_objective(payload: MissionPayload) -> tuple[float, float] | None:
    """任務的**主**目標點。SCREEN/MOVE_MARCH 是多點任務，回 None。"""
    params = payload.typed_params()
    if isinstance(params, SeizeParams):
        return params.objective.lat, params.objective.lng
    if isinstance(params, DefendParams):
        return params.area.lat, params.area.lng
    return None


def _precheck_move(
    unit: TacticalUnit, payload: MovePayload, gateway: PhysicsGateway
) -> list[PrecheckCheck]:
    if unit.current_lat is None or unit.current_lng is None:
        return [PrecheckCheck(name="position", passed=False, detail="單位無座標，無法規劃移動")]
    from_h3 = h3.latlng_to_cell(unit.current_lat, unit.current_lng, _HEX_RES)
    reachable, detail = gateway.path_reachable(from_h3, payload.to_h3, payload.mobility_profile)
    if not reachable:
        # 不可達最常見主因：起訖不在已建置地形快取範圍（terrain A* 於預算 hex grid 外不規劃），
        # 或沿途地形不可通行。給使用者可行動的原因，而非只回「不可達」（與前端直線預覽落差來源）。
        detail = (
            f"{detail}——目標或路徑可能超出已建置地形範圍，或沿途地形不可通行；"
            "請縮短距離或改選已涵蓋區域內的目標（長距離道路/地形路由為後續強化）"
        )
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

    # 聯合兵種（SPEC_EXTEND P4.5）：未指定單一武器且持 ≥2 武器 → 對武器組合逐件判可達，
    # **任一武器可打即 feasible**（例：直瞄被稜線擋，但頂攻飛彈免視線仍可交戰）。避免主武器
    # 無 LOS 就把整張聯合 ENGAGE 令擋死。指定 weapon_id 或單武器 → 下方既有單武器路徑。
    if payload.weapon_id is None:
        combined = _weapon_profiles(db, unit)
        if len(combined) >= 2:
            return _precheck_engage_any(db, unit, target, gateway, payload, combined)

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


def _inst_ammo(inst: EquipmentInstance) -> int:
    """裝備實例的 DB 彈藥數（current_state.ammo）；缺/非數值 → 0。"""
    raw = inst.current_state.get("ammo") if isinstance(inst.current_state, dict) else None
    return int(raw) if isinstance(raw, (int, float)) else 0


def _weapon_profiles(
    db: Session, unit: TacticalUnit
) -> list[tuple[EquipmentInstance, WeaponProfile, EquipmentTemplate]]:
    """單位所有可產生 WeaponProfile 的裝備（能解析 baseStats 者＝武器）；非武器略過。"""
    instances = (
        db.execute(select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id))
        .scalars()
        .all()
    )
    out: list[tuple[EquipmentInstance, WeaponProfile, EquipmentTemplate]] = []
    for inst in instances:
        tmpl = db.get(EquipmentTemplate, inst.template_id)
        if tmpl is None:
            continue
        try:
            profile = WeaponProfile.from_base_stats(tmpl.base_stats)
        except (ValueError, KeyError, TypeError):
            continue  # 非武器或 baseStats 壞 → 略過
        out.append((inst, profile, tmpl))
    return out


def _precheck_engage_any(
    db: Session,
    unit: TacticalUnit,
    target: TacticalUnit,
    gateway: PhysicsGateway,
    payload: EngagePayload,
    weapons: list[tuple[EquipmentInstance, WeaponProfile, EquipmentTemplate]],
) -> list[PrecheckCheck]:
    """聯合兵種可達性：任一武器可打（可達 + 射程 + 彈藥）即 feasible。

    先評估**免 terrain**的武器（可變軌飛彈/間瞄），能命中即短路回傳——省 LOS 呼叫、對 terrain
    延遲更穩健。全數不可打 → **逐武器**列出各自失敗原因（有彈的武器排前決定錯誤碼）——不讓「單一
    武器沒彈」誤導成整組不能打（其餘有彈武器其實是被地形/射程擋，才是真正原因）。
    """

    def _cheap_first(item: tuple[EquipmentInstance, WeaponProfile, EquipmentTemplate]) -> int:
        p = item[1]
        return 0 if (p.missile and p.maneuverable) or p.indirect_fire else 1

    # 每件武器評估後留一筆 (has_ammo, passed_count, 帶武器名的失敗 check)。
    evals: list[tuple[bool, int, PrecheckCheck]] = []
    for inst, profile, tmpl in sorted(weapons, key=_cheap_first):
        has_ammo = _inst_ammo(inst) > 0
        # 彈藥數為 0 的武器不算可打（precheck 只看 DB current_state；活彈藥由裁決把關）。
        if not has_ammo:
            checks = [PrecheckCheck(name="ammo", passed=False, detail=f"{tmpl.name} 無彈藥")]
        else:
            reach = _reachability_check(db, unit, target, profile, gateway)
            checks = [reach]
            if reach.passed:
                checks += _range_ammo_checks(unit, target, profile, tmpl, payload)
        if all(c.passed for c in checks):
            return [
                PrecheckCheck(
                    name="combined_fires",
                    passed=True,
                    detail=f"聯合火力：可由 {tmpl.name} 交戰（其餘武器於裁決時逐件判定）",
                )
            ]
        # 該武器的代表失敗原因（第一個未過的 check），命名沿用失敗類型 → precheck_error_code 可取到
        # 具體物理碼（NO_LOS/OUT_OF_RANGE/NO_AMMO）；detail 前綴武器名，供面板逐列顯示全貌。
        fail = next((c for c in checks if not c.passed), checks[-1])
        line = PrecheckCheck(name=fail.name, passed=False, detail=f"{tmpl.name}：{fail.detail}")
        evals.append((has_ammo, sum(c.passed for c in checks), line))
    # 排序：**有彈**優先、通過項多優先 → 代表（第一列）決定錯誤碼與標題原因。這樣「某武器沒彈」不會
    # 蓋過「其餘有彈武器被地形/射程擋」這個真正原因。全部列出讓使用者看到各武器各自狀態。
    evals.sort(key=lambda e: (e[0], e[1]), reverse=True)
    return [e[2] for e in evals]


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
