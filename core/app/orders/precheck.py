"""物理預檢（O3.1，SPEC §2.3 步驟 [2]）——同步 <50ms，呼叫 terrain 判物理可行性。

**紅線**：物理事實（可達/可見/射程）由 terrain（確定性）裁決，AI 永不介入。不可行 → 立即
REJECTED（見 service）。terrain 不可達 → TerrainUnavailableError 冒泡（API 轉 503，硬依賴）。

依賴以 `PhysicsGateway` Protocol 注入，測試可用假 gateway，不需真 gRPC/terrain server。
`TerrainGatewayAdapter` 為真 TerrainClient 的轉接。
"""

from __future__ import annotations

from typing import Protocol

import h3
from sqlalchemy.orm import Session

from app.factions import FactionRelations
from app.models.tables import TacticalUnit
from app.orders.schemas import (
    EngagePayload,
    MovePayload,
    PrecheckCheck,
    PrecheckResult,
)
from app.orders.validator import ValidatedOrder

_HEX_RES = 8  # 戰術預設解析度（與 terrain hex grid 一致）
_ENGAGE_AGL_M = 2.0  # 交戰視線的預設觀測/目標離地高（感測器/人員），weapon 高度於 O3.2

# 預檢項名稱 → 契約 error code（不可行時取第一個失敗項）
_CHECK_ERROR_CODES = {
    "position": "ORDER_UNIT_NO_POSITION",
    "reachability": "ORDER_UNREACHABLE",
    "target_exists": "ORDER_TARGET_NOT_FOUND",
    "line_of_sight": "ORDER_NO_LOS",
    "roe": "ORDER_ROE_VIOLATION",
}


class PhysicsGateway(Protocol):
    """物理預檢所需的 terrain 查詢（領域介面，隔離 gRPC 細節）。"""

    def path_reachable(
        self, from_h3: str, to_h3: str, mobility_profile: str
    ) -> tuple[bool, str]: ...

    def has_los(
        self, observer: tuple[float, float, float], target: tuple[float, float, float]
    ) -> tuple[bool, float]: ...


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
    visible, clearance = gateway.has_los(
        (unit.current_lat, unit.current_lng, _ENGAGE_AGL_M),
        (target.current_lat, target.current_lng, _ENGAGE_AGL_M),
    )
    detail = f"clearance={clearance:.1f}m" if visible else "地形遮蔽，無視線"
    return [PrecheckCheck(name="line_of_sight", passed=visible, detail=detail)]


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
    ) -> tuple[bool, float]:
        resp = self._client.check_los(observer, target)  # type: ignore[attr-defined]
        return resp.visible, resp.fresnel_clearance
