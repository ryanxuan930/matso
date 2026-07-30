"""Order API 的 Pydantic 模型（O3.1）——對映 contracts/core_api.yaml 的 Order schemas。

payload 依 order_type 有不同形狀；request 收 dict，由 validator/precheck 解析為下列 typed 模型。
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.enums import OrderStatus


class OrderType(enum.StrEnum):
    MOVE = "MOVE"
    ENGAGE = "ENGAGE"
    RECON = "RECON"
    RESUPPLY = "RESUPPLY"
    POSTURE = "POSTURE"
    # 面目標射擊（WP-C10.2）：打座標而非打單位。ENGAGE 表達不了「攻擊準備射擊」
    # 這種「不管有沒有人在那裡都要打一片」的火力。
    FIRE_MISSION = "FIRE_MISSION"
    # 任務級下令（WP-A2）：下的是任務，展開成低階令的是確定性的符號層分解器。
    # 與 FIRE_MISSION 只是名字相近，兩者無關。
    MISSION = "MISSION"
    # 乘駐車與隊形（WP-C3）。規格寫「MOUNT/DISMOUNT 令」，實作收成**一個令型**：
    # 三個令型會讓席位表、payload 表、預檢分派、前端下拉各多兩個分支，
    # 而它們表達的是同一件事——宣告本單位要以什麼狀態行動（與 POSTURE 同類）。
    FORMATION = "FORMATION"


class OrderRequest(BaseModel):
    """下令請求。issuer 由認證 token 推導（O4.5，SPEC §12：前端不可信），不由 body 帶入。"""

    unit_id: str = Field(min_length=1)
    order_type: OrderType
    payload: dict[str, Any] = Field(default_factory=dict)
    # WP-A3：下令者明確確認「目標位於限制射擊區（Restricted-Fire）仍要射擊」。
    # 只對 RESTRICTED_FIRE 有效——NO_STRIKE 不可 override。true 時 service 會寫一筆
    # ORDER_RESTRICTED_FIRE_OVERRIDE 到 Ledger 供 AAR 追究責任。
    acknowledge_restricted: bool = False


class MovePayload(BaseModel):
    """MOVE 指令載荷：目標 hex + 機動側寫。

    to_lat/to_lng＝精確移動（#2）：預檢仍以 to_h3 做可達/地形判定，但最終落點用精確座標，
    不吸附到六角格心——供 <1km 的近距作戰規劃（校園/大樓等）。
    """

    to_h3: str = Field(min_length=1)
    mobility_profile: str = Field(min_length=1)
    to_lat: float | None = Field(default=None, ge=-90, le=90)
    to_lng: float | None = Field(default=None, ge=-180, le=180)
    tempo: str = "NORMAL"  # #80：行軍節奏 NORMAL / FORCED_MARCH（速度↔耗損取捨）


class EngagePayload(BaseModel):
    """ENGAGE 指令載荷：目標單位（+ 選用武器實例 + 彈種）。"""

    target_unit_id: str = Field(min_length=1)
    weapon_id: str | None = None
    ammo_type: str | None = None
    # WP-B5.3 曲射火協：本局要求時，曲射交戰須掛一張已核准的 FIRE_SUPPORT 申請單。
    fire_request_id: str | None = None


class FireMissionPayload(BaseModel):
    """FIRE_MISSION 指令載荷（WP-C10.2）：目標座標 + 發數（+ 選用武器與火協核准單）。"""

    target_lat: float = Field(ge=-90.0, le=90.0)
    target_lng: float = Field(ge=-180.0, le=180.0)
    rounds: int = Field(default=1, ge=1, le=200)
    weapon_id: str | None = None
    # 與 EngagePayload 同名同義：本局要求火協時，須掛已核准的 FIRE_SUPPORT 申請單。
    fire_request_id: str | None = None


class PosturePayload(BaseModel):
    """POSTURE 指令載荷（WP-C1）：宣告單位要進入的姿態。

    **只是宣告目標**——轉換要時間（HASTY 即時／DEFENSE 30 分／DUG_IN 4 小時），
    期間仍算前一級。宣告掘壕的那一秒就享有掘壕防護會讓工事變成免費按鈕。
    """

    posture: str = Field(pattern="^(MOVING|HASTY|DEFENSE|DUG_IN)$")


class FormationPayload(BaseModel):
    """FORMATION 指令載荷（WP-C3）：宣告隊形與/或乘駐車狀態。

    **兩者皆可省**，但不能都省——至少要宣告一件事。
    只想下車的令不該把隊形一起重設，故 None 代表「不動該欄」。
    """

    formation: str | None = Field(default=None, pattern="^(COLUMN|LINE|WEDGE|VEE|HERRINGBONE)$")
    mounted: bool | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> FormationPayload:
        if self.formation is None and self.mounted is None:
            raise ValueError("formation 與 mounted 至少要指定一項")
        return self


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
    # WP-A2：分解自哪一道 MISSION 令。None＝直接下的令。
    parent_order_id: str | None = None
    mission_type: str | None = None  # 僅 MISSION 令有值
