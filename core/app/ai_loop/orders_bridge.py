"""AI 指令橋接 + 護欄 G3 物理可行性 — O11.3（SPEC_AUTONOMY §3.3/§3.4）。

三件事：
1. `tactical_order_to_request`：把 AI 的 tactical_order dict 映成可執行的 `OrderRequest`
   （首版支援 MOVE/ENGAGE；HOLD/RECON/RESUPPLY/POSTURE 回 None＝不落單）。
2. `PrecheckFeasibility`：實作護欄 G3 的 `OrderFeasibilityChecker`——**只**查物理可行性
   （run_precheck：MOVE 可達、ENGAGE LOS/射程/彈藥），不查下令權限。讓 run_faction_turn 的
   G3 逐條剔除「打不到/走不到」的幻想令。
3. `submit_faction_orders`：把（過完護欄的）AI 令經 `OrderService.submit` 落成 VALIDATED，
   issuer＝AI 陣營指揮官 participant；與人類同入口，Kernel 照常 drain 執行。

紅線：AI 不繞過物理（G3 + submit 各跑一次 precheck，雙保險）；AI 不寫熱狀態，只落 Order。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import h3
from pydantic import ValidationError

from app.errors import MatsoError
from app.factions.relations import FactionRelations
from app.models.tables import TacticalUnit
from app.movement.mobility import resolve_unit_mobility
from app.orders.no_strike import NO_STRIKE_H3_RES
from app.orders.precheck import PhysicsGateway, run_precheck
from app.orders.schemas import EngagePayload, MovePayload, OrderRequest, OrderType
from app.orders.service import OrderService
from app.orders.validator import ValidatedOrder

_LOG = logging.getLogger("app.ai_orders_bridge")

# AI MOVE 令的預設機動 profile（與前端 COP 預設一致）；未來可由單位型別導出。
_DEFAULT_MOBILITY = "FOOT"
_MOVE_H3_RES = 8  # AI MOVE 目標經緯 → H3 解析度（與戰術 hex grid 一致）。
# 單一決策週期落單上限（O11.8 防洗版）：LLM 一次吐幾十上百令時只處理前 N。
_MAX_ORDERS_PER_CYCLE = 25

_TypedPayload = MovePayload | EngagePayload | dict[str, Any]


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


_VALID_TEMPO = ("NORMAL", "FORCED_MARCH")


def tactical_order_to_request(
    order: dict[str, Any],
    *,
    mobility_lookup: Callable[[str], str] | None = None,
) -> OrderRequest | None:
    """AI tactical_order dict → OrderRequest。無法映射（HOLD/缺欄位/不支援型別）回 None。

    `mobility_lookup`（#80）：unit_id → 機動 profile（由編裝導出）；None 則退回 FOOT 後備。
    G3 與 submit 兩路徑傳入同一 lookup，確保預檢與落單用一致 profile。
    """
    unit_id = order.get("unit_id")
    otype_raw = order.get("order_type")
    if not isinstance(unit_id, str) or not unit_id or not isinstance(otype_raw, str):
        return None
    try:
        otype = OrderType(otype_raw)
    except ValueError:
        return None  # HOLD 或未知型別 → 不落單（HOLD＝原地待命）

    if otype is OrderType.MOVE:
        # #80：機動 profile 由編裝導出（不再硬寫 FOOT）；缺 lookup 才退回後備。
        profile = mobility_lookup(unit_id) if mobility_lookup is not None else _DEFAULT_MOBILITY
        tempo_raw = order.get("tempo")
        tempo = tempo_raw if tempo_raw in _VALID_TEMPO else "NORMAL"
        # LLM 無法算 H3 hex id，只給得出經緯（敵情/目標/自身皆為 lat/lng）；故 MOVE 優先接受
        # target_lat/target_lng，伺服端換算 to_h3。缺經緯才退回 target_h3（少數已知格）。
        lat, lng = _num(order.get("target_lat")), _num(order.get("target_lng"))
        target_h3 = order.get("target_h3")
        if lat is not None and lng is not None:
            payload: dict[str, Any] = {
                "to_h3": h3.latlng_to_cell(lat, lng, _MOVE_H3_RES),
                "mobility_profile": profile,
                "to_lat": lat,
                "to_lng": lng,
                "tempo": tempo,
            }
        elif isinstance(target_h3, str) and target_h3:
            payload = {"to_h3": target_h3, "mobility_profile": profile, "tempo": tempo}
        else:
            return None
    elif otype is OrderType.ENGAGE:
        target_unit_id = order.get("target_unit_id")
        if not isinstance(target_unit_id, str) or not target_unit_id:
            return None
        payload = {"target_unit_id": target_unit_id}
        fire_policy = order.get("fire_policy")
        if isinstance(fire_policy, str) and fire_policy:
            # fire_policy 走 payload dict → adjudicator（EngagePayload 忽略額外欄，raw 保留）。
            payload["fire_policy"] = fire_policy
        weapon_id = order.get("weapon_template_id") or order.get("weapon_id")
        if isinstance(weapon_id, str) and weapon_id:
            payload["weapon_id"] = weapon_id
    elif otype is OrderType.RESUPPLY:
        # #85：補給令（補給車對同陣營單位加油）；子系統 ResupplySystem 執行。
        target_unit_id = order.get("target_unit_id")
        if not isinstance(target_unit_id, str) or not target_unit_id:
            return None
        payload = {"target_unit_id": target_unit_id}
    else:
        return None  # RECON/POSTURE 首版不橋接（對應子系統 NoOp）

    return OrderRequest(unit_id=unit_id, order_type=otype, payload=payload)


def _parse_typed_payload(req: OrderRequest) -> _TypedPayload | None:
    """把 OrderRequest.payload 解析為 typed 模型（供直接建 ValidatedOrder，不經權限檢查）。"""
    try:
        if req.order_type is OrderType.MOVE:
            return MovePayload.model_validate(req.payload)
        if req.order_type is OrderType.ENGAGE:
            return EngagePayload.model_validate(req.payload)
    except ValidationError:
        return None
    return dict(req.payload)  # 其餘類型（RECON/RESUPPLY/POSTURE）維持 raw dict


class PrecheckFeasibility:
    """護欄 G3：只查物理可行性（run_precheck），不查下令權限（權限在 submit 端）。"""

    def __init__(
        self,
        db: Any,
        session_id: str,
        gateway: PhysicsGateway,
        relations: FactionRelations | None = None,
    ) -> None:
        self._db = db
        self._session_id = session_id
        self._gateway = gateway
        self._relations = relations

    def is_feasible(self, order: dict[str, Any]) -> tuple[bool, str]:
        req = tactical_order_to_request(
            order, mobility_lookup=lambda uid: resolve_unit_mobility(self._db, uid).profile
        )
        if req is None:
            return False, "指令無法轉為可執行令（缺欄位或不支援的型別）"
        unit = self._db.get(TacticalUnit, req.unit_id)
        if unit is None or unit.session_id != self._session_id:
            return False, f"單位不存在於此 session：{req.unit_id}"
        payload = _parse_typed_payload(req)
        if payload is None:
            return False, "指令 payload 格式錯誤"
        validated = ValidatedOrder(unit=unit, order_type=req.order_type, payload=payload)
        try:
            result = run_precheck(self._db, validated, self._gateway, self._relations)
        except Exception as exc:
            # 與交戰 env「terrain 服務中斷不凍結戰鬥」一致：物理閘門暫不可用時暫予放行，
            # 由 submit 端的權威 precheck 再驗（仍不繞過物理，只是不讓 AI 迴圈因 infra 抖動崩潰）。
            _LOG.warning("G3 物理檢查暫時失敗（%s），暫予放行", type(exc).__name__)
            return True, f"物理閘門暫時不可用（{type(exc).__name__}）"
        return result.feasible, result.reason or ""


@dataclass
class BridgeResult:
    """一批 AI 令落單結果。"""

    submitted: list[str] = field(default_factory=list)  # 成 VALIDATED 的 order id
    rejected: list[dict[str, Any]] = field(default_factory=list)  # {order, reason}
    skipped: list[dict[str, Any]] = field(default_factory=list)  # 無法映射（HOLD 等）
    capped: int = 0  # 因速率上限被丟棄的令數（O11.8 防洗版；>0 代表本週期超量）


def submit_faction_orders(
    db: Any,
    session_id: str,
    orders: list[dict[str, Any]],
    *,
    issuer_id: str,
    gateway: PhysicsGateway,
    relations: FactionRelations | None = None,
    tick_source: Callable[[], int] = lambda: 0,
    max_orders: int = _MAX_ORDERS_PER_CYCLE,
) -> BridgeResult:
    """把 AI 令逐筆經 OrderService.submit 落成 VALIDATED（issuer＝AI 陣營 participant）。

    submit 對不可行令會持久化 REJECTED 後拋 PrecheckFailedError；此處捕捉並歸入 rejected，
    不中斷整批。與人類同入口 → 再驗一次 + Kernel 照常 drain 執行。

    速率上限（O11.8）：一週期最多處理 `max_orders` 筆，超量截斷並記 `capped`（防 LLM 洗版）。
    """
    result = BridgeResult()
    if len(orders) > max_orders:
        result.capped = len(orders) - max_orders
        _LOG.warning(
            "AI 落單超速率上限（session %s）：收到 %d 令，僅處理前 %d（防洗版）",
            session_id,
            len(orders),
            max_orders,
        )
        orders = orders[:max_orders]
    service = OrderService(db, gateway, tick_source=tick_source, relations=relations)
    for order in orders:
        req = tactical_order_to_request(
            order, mobility_lookup=lambda uid: resolve_unit_mobility(db, uid).profile
        )
        if req is None:
            result.skipped.append({"order": order})
            continue
        try:
            resp = service.submit(session_id, req, issuer_id)
            result.submitted.append(resp.id)
        except MatsoError as exc:
            result.rejected.append({"order": order, "reason": str(exc)})
        except Exception as exc:
            _LOG.warning("AI 落單非預期失敗（%s），歸為 rejected 續跑", type(exc).__name__)
            result.rejected.append({"order": order, "reason": f"落單失敗：{type(exc).__name__}"})
    return result


class UnitTargetLocator:
    """護欄 G4：把一道打擊令解析成其**目標實際所在的 h3 格**（WP-A3）。

    改版前 G4 只比對令面自帶的 `target_h3`，而 AI 的 ENGAGE 令帶 `target_unit_id`
    → 永遠對不上、G4 從未攔過任何東西。本類補上那一步：unit → 當前座標 → h3。

    座標優先讀**熱狀態**（活模擬中單位會移動，DB 的 current_lat/lng 是上次落盤值）；
    熱狀態缺值才退回 DB。查不到一律回 None（不擋——真正的把關在 submit 端 precheck）。
    """

    def __init__(self, db: Any, session_id: str, hot: Any = None) -> None:
        self._db = db
        self._session_id = session_id
        self._hot = hot

    def _unit_latlng(self, unit_id: str) -> tuple[float, float] | None:
        if self._hot is not None:
            state = self._hot.get(self._session_id, unit_id) or {}
            lat, lng = state.get("lat"), state.get("lng")
            if isinstance(lat, int | float) and isinstance(lng, int | float):
                return float(lat), float(lng)
        unit = self._db.get(TacticalUnit, unit_id)
        if unit is None or unit.session_id != self._session_id:
            return None
        if unit.current_lat is None or unit.current_lng is None:
            return None
        return float(unit.current_lat), float(unit.current_lng)

    def locate(self, order: dict[str, Any]) -> str | None:
        target_id = order.get("target_unit_id")
        if isinstance(target_id, str) and target_id:
            pos = self._unit_latlng(target_id)
            if pos is not None:
                return str(h3.latlng_to_cell(pos[0], pos[1], NO_STRIKE_H3_RES))
        # 直接給座標的令（未來的 MISSION objective / 面射擊）也支援。
        lat, lng = _num(order.get("target_lat")), _num(order.get("target_lng"))
        if lat is not None and lng is not None:
            return str(h3.latlng_to_cell(lat, lng, NO_STRIKE_H3_RES))
        return None
