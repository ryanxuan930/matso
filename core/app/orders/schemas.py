"""Order API 的 Pydantic 模型（O3.1）——對映 contracts/core_api.yaml 的 Order schemas。

payload 依 order_type 有不同形狀；request 收 dict，由 validator/precheck 解析為下列 typed 模型。
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import OrderStatus


class OrderType(enum.StrEnum):
    MOVE = "MOVE"
    ENGAGE = "ENGAGE"
    RECON = "RECON"
    RESUPPLY = "RESUPPLY"
    POSTURE = "POSTURE"


class OrderRequest(BaseModel):
    """下令請求。issuer 由認證 token 推導（O4.5，SPEC §12：前端不可信），不由 body 帶入。"""

    unit_id: str = Field(min_length=1)
    order_type: OrderType
    payload: dict[str, Any] = Field(default_factory=dict)


class MovePayload(BaseModel):
    """MOVE 指令載荷：目標 hex + 機動側寫。

    to_lat/to_lng＝精確移動（#2）：預檢仍以 to_h3 做可達/地形判定，但最終落點用精確座標，
    不吸附到六角格心——供 <1km 的近距作戰規劃（校園/大樓等）。
    """

    to_h3: str = Field(min_length=1)
    mobility_profile: str = Field(min_length=1)
    to_lat: float | None = Field(default=None, ge=-90, le=90)
    to_lng: float | None = Field(default=None, ge=-180, le=180)


class EngagePayload(BaseModel):
    """ENGAGE 指令載荷：目標單位（+ 選用武器實例 + 彈種）。"""

    target_unit_id: str = Field(min_length=1)
    weapon_id: str | None = None
    ammo_type: str | None = None


class PrecheckCheck(BaseModel):
    """單一預檢項目結果（供 AAR 溯源與前端顯示）。"""

    name: str
    passed: bool
    detail: str = ""


class PrecheckResult(BaseModel):
    feasible: bool
    checks: list[PrecheckCheck] = Field(default_factory=list)
    reason: str | None = None  # 不可行時的摘要（error code 由例外攜帶）


class OrderResponse(BaseModel):
    id: str
    unit_id: str
    order_type: str
    status: OrderStatus
    precheck: PrecheckResult | None = None
    issued_at_tick: int
    resolved_at_tick: int | None = None
    target_unit_id: str | None = None  # ENGAGE 目標單位（供指令列顯示對象）
    target_h3: str | None = None  # MOVE 目的地 hex（供指令列顯示對象）
