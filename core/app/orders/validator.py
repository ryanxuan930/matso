"""Order 驗證（O3.1，SPEC §2.3 步驟 [1]）——語法 / 單位存在性 / 下令權限。

純檢查，不改狀態。失敗拋領域例外（API 層轉 error code）；成功回 ValidatedOrder（帶已載入
的單位與解析後的 typed payload，供物理預檢重用，避免重複查詢/解析）。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import (
    OrderPermissionError,
    OrderSeatDeniedError,
    OrderValidationError,
    SessionNotFoundError,
)
from app.models.enums import UserRole
from app.models.tables import SessionParticipant, TacticalUnit, WargameSession
from app.orders.schemas import (
    EngagePayload,
    FireMissionPayload,
    MovePayload,
    OrderRequest,
    OrderType,
)
from app.seats import SEAT_LABELS, seat_may_order

# 可跨陣營下令的角色（白軍/導演）
_OVERRIDE_ROLES = frozenset({UserRole.WHITE_CELL_STAFF, UserRole.EXERCISE_DIRECTOR})

_PAYLOAD_MODELS: dict[OrderType, type[MovePayload | EngagePayload | FireMissionPayload]] = {
    OrderType.MOVE: MovePayload,
    OrderType.ENGAGE: EngagePayload,
    OrderType.FIRE_MISSION: FireMissionPayload,
}


@dataclass(frozen=True, slots=True)
class ValidatedOrder:
    unit: TacticalUnit
    order_type: OrderType
    payload: MovePayload | EngagePayload | FireMissionPayload | dict[str, object]


def validate_order(
    db: Session, session_id: str, req: OrderRequest, issuer_id: str
) -> ValidatedOrder:
    if db.get(WargameSession, session_id) is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")

    unit = db.get(TacticalUnit, req.unit_id)
    if unit is None or unit.session_id != session_id:
        raise OrderValidationError(
            f"單位不存在於此 session：{req.unit_id}",
            error_code="ORDER_UNIT_NOT_FOUND",
            details={"unit_id": req.unit_id},
        )

    _check_permission(db, session_id, issuer_id, unit, req.order_type)
    # 固定單位（指揮部/後勤/陣地）：不接受 MOVE 令——不論下令者是 AI 或白軍/導演。這是想定層的
    # 編成約束（非物理裁決），故在驗證層擋下；ENGAGE/其他令不受限（原地自衛仍可）。座標「布局」
    # 走 reposition 端點（White Cell god setup），不經此路徑，故固定單位仍可於地圖狀態編輯中擺放。
    if req.order_type is OrderType.MOVE and unit.is_fixed:
        raise OrderValidationError(
            f"固定單位不可移動：{unit.designation}（指揮部等固定編成，於劇本設定）",
            error_code="ORDER_UNIT_FIXED",
            details={"unit_id": unit.id, "designation": unit.designation},
        )
    payload = _parse_payload(req)
    return ValidatedOrder(unit=unit, order_type=req.order_type, payload=payload)


def _check_permission(
    db: Session,
    session_id: str,
    issuer_id: str,
    unit: TacticalUnit,
    order_type: OrderType,
) -> None:
    participant = db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.id == issuer_id,
            SessionParticipant.session_id == session_id,
        )
    )
    if participant is None:
        raise OrderPermissionError(
            f"下令者非此 session 參與者：{issuer_id}",
            details={"issuer_id": issuer_id},
        )
    if participant.role in _OVERRIDE_ROLES:
        return  # 白軍/導演可對任一單位下令
    if participant.faction != unit.faction:
        raise OrderPermissionError(
            "無權對他方單位下令",
            details={
                "issuer_faction": participant.faction,
                "unit_faction": unit.faction,
            },
        )
    # unit_scope：若名冊限縮此帳號只指揮特定單位子集（非空），則只能對子集內單位下令。
    scope = participant.unit_scope if isinstance(participant.unit_scope, list) else []
    if scope and unit.id not in scope:
        raise OrderPermissionError(
            "此帳號僅獲授權指揮部分單位，不含此單位",
            details={"unit_id": unit.id, "unit_scope": [str(x) for x in scope]},
        )
    # 席位職掌（WP-B5.1）。放在陣營/unit_scope 之後：「這不是你的單位」比
    # 「這不是你的職掌」更根本，先報前者比較好懂。
    # **未指派席位（None）不在此設限**——見 app.seats 的說明。
    seat = participant.seat_role
    if seat is not None and not seat_may_order(seat, order_type):
        raise OrderSeatDeniedError(
            f"{SEAT_LABELS.get(seat, seat.value)} 不得下「{order_type.value}」令",
            details={"seat_role": seat.value, "order_type": order_type.value},
        )


def _parse_payload(
    req: OrderRequest,
) -> MovePayload | EngagePayload | FireMissionPayload | dict[str, object]:
    model = _PAYLOAD_MODELS.get(req.order_type)
    if model is None:
        return dict(req.payload)  # 其餘類型（RECON/RESUPPLY/POSTURE）O3.x 再細化
    try:
        return model.model_validate(req.payload)
    except ValidationError as exc:
        raise OrderValidationError(
            f"{req.order_type} 載荷格式錯誤",
            error_code="ORDER_INVALID_PAYLOAD",
            details={"errors": exc.errors(include_url=False)},
        ) from exc
