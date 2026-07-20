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
    OrderValidationError,
    SessionNotFoundError,
)
from app.models.enums import UserRole
from app.models.tables import SessionParticipant, TacticalUnit, WargameSession
from app.orders.schemas import EngagePayload, MovePayload, OrderRequest, OrderType

# 可跨陣營下令的角色（白軍/導演）
_OVERRIDE_ROLES = frozenset({UserRole.WHITE_CELL_STAFF, UserRole.EXERCISE_DIRECTOR})

_PAYLOAD_MODELS: dict[OrderType, type[MovePayload | EngagePayload]] = {
    OrderType.MOVE: MovePayload,
    OrderType.ENGAGE: EngagePayload,
}


@dataclass(frozen=True, slots=True)
class ValidatedOrder:
    unit: TacticalUnit
    order_type: OrderType
    payload: MovePayload | EngagePayload | dict[str, object]


def validate_order(db: Session, session_id: str, req: OrderRequest) -> ValidatedOrder:
    if db.get(WargameSession, session_id) is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")

    unit = db.get(TacticalUnit, req.unit_id)
    if unit is None or unit.session_id != session_id:
        raise OrderValidationError(
            f"單位不存在於此 session：{req.unit_id}",
            error_code="ORDER_UNIT_NOT_FOUND",
            details={"unit_id": req.unit_id},
        )

    _check_permission(db, session_id, req.issuer_id, unit)
    payload = _parse_payload(req)
    return ValidatedOrder(unit=unit, order_type=req.order_type, payload=payload)


def _check_permission(db: Session, session_id: str, issuer_id: str, unit: TacticalUnit) -> None:
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
                "issuer_faction": participant.faction.value,
                "unit_faction": unit.faction.value,
            },
        )


def _parse_payload(req: OrderRequest) -> MovePayload | EngagePayload | dict[str, object]:
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
