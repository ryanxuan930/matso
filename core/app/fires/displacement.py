"""陣地變換排程器（WP-C10.5）——掃出該換位置的砲，替它下一道 MOVE 令。

與 `fires/scheduler.py`（at_tick 火力排程）同一條路徑：跑在 `run_paced(pre_tick=…)`，
用自己的 DB session，經 `OrderService.submit`。理由見 `fires/survivability.py` 的模組說明。

**位移是有代價的**：走的是正規移動路徑，所以會吃油、會有行軍耗損、會被地形擋。
那是刻意的——shoot-and-scoot 本來就要付出東西，不然它就是免費的無敵技。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.rng import DeterministicRNG
from app.fires.survivability import (
    MISSION_COUNT_KEY,
    SELF_MOVING_PROFILES,
    SurvivabilityConfig,
    pick_displacement_point,
)
from app.models.enums import OrderStatus, UserRole
from app.models.tables import Order, SessionParticipant, TacticalUnit, User
from app.movement.mobility import resolve_unit_mobility
from app.orders.schemas import MovePayload, OrderRequest, OrderType
from app.orders.service import OrderService
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

_LOG = logging.getLogger("app.fires.displacement")

_HEX_RES = 8
# 一個單位一次最多試幾個方位。抽到的點可能不可達（地形/超出已建置範圍）——
# 換個方位再試比放棄合理，但不能無上限地打 gateway。
_MAX_ATTEMPTS = 3


def ensure_system_participant(db: Session, session_id: str, faction: str) -> str:
    """陣地變換這種**系統自發**的令要掛在誰身上。

    沿用 AI 迴路的做法（`ai_loop/orchestrator.ensure_ai_participant`）：一個不可登入的
    `system-{faction}` 帳號。**不是為了繞過檢查**——role=COMMANDER 仍受陣營檢查，
    而且 AAR 上看得出「這道令不是人下的」。

    與火力計畫刻意不同：那裡的當責者是寫計畫的人（人的意圖），這裡沒有人的意圖，
    是想定開關造成的自動反應，掛在誰頭上都是假的。
    """
    username = f"system-{faction}"
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username, password_hash="!system-no-login", role=UserRole.COMMANDER)
        db.add(user)
        db.flush()
    part = db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.user_id == user.id,
            SessionParticipant.session_id == session_id,
        )
    )
    if part is None:
        part = SessionParticipant(
            user_id=user.id,
            session_id=session_id,
            faction=faction,
            role=UserRole.COMMANDER,
            unit_scope=[],
        )
        db.add(part)
    db.commit()
    return part.id


def _has_open_move(db: Session, unit_id: str) -> bool:
    """該單位已有未完成的 MOVE 令 → 它本來就在移動，不要再塞一個相衝的目的地。"""
    return (
        db.scalar(
            select(Order.id).where(
                Order.unit_id == unit_id,
                Order.order_type == OrderType.MOVE.value,
                Order.status.in_(
                    (OrderStatus.PENDING, OrderStatus.VALIDATED, OrderStatus.EXECUTING)
                ),
            )
        )
        is not None
    )


def due_units(
    db: Session, hot: HotStateStore, session_id: str, cfg: SurvivabilityConfig
) -> list[str]:
    """該換陣地的單位（確定性排序）。

    三道過濾，每一道都有理由：

    - **打夠次數了**（熱狀態計數 ≥ 門檻）。
    - **不是固定單位**：想定作者標了 `is_fixed` 就是說這門砲不動；想定開關不該蓋過
      想定作者的明確宣告。而且送出去也一定會被 `ORDER_UNIT_FIXED` 打回。
    - **能自走**：牽引砲要牽引車。用 FOOT 側寫「走」1.5 km 不是模型化人力搬砲，
      那只是機動解析的 fallback 在替我們亂編。
    """
    if not cfg.enabled:
        return []
    units = db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == session_id)).all()
    out: list[str] = []
    for unit in sorted(units, key=lambda u: u.id):
        state = hot.get_unit(unit.id) or {}
        raw = state.get(MISSION_COUNT_KEY, 0)
        count = int(raw) if isinstance(raw, (int, float)) else 0
        if count < cfg.missions_before_move:
            continue
        if unit.is_fixed:
            continue
        if resolve_unit_mobility(db, unit.id).profile not in SELF_MOVING_PROFILES:
            continue
        out.append(unit.id)
    return out


def run_due_displacements(
    db: Session,
    hot: HotStateStore,
    session_id: str,
    tick: int,
    cfg: SurvivabilityConfig,
    rng: DeterministicRNG,
    order_service_factory: Callable[[Session], OrderService],
) -> list[LedgerEvent]:
    """替該換陣地的砲下 MOVE 令。回要落帳的事件。

    **不論成敗都把計數歸零**：不歸零的話，一門位移不出去的砲會在每個 tick 重試一次，
    每次一趟 `path_reachable` gRPC——那是永久的負載，而且畫面上什麼都不會發生。
    失敗要留痕（`SURVIVABILITY_MOVE_BLOCKED`），不是靜靜吞掉。
    """
    events: list[LedgerEvent] = []
    for unit_id in due_units(db, hot, session_id, cfg):
        unit = db.get(TacticalUnit, unit_id)
        if unit is None:
            continue
        state = hot.get_unit(unit_id) or {}
        lat, lng = state.get("lat"), state.get("lng")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        if _has_open_move(db, unit_id):
            continue  # 已在移動中——不要塞相衝的目的地（而且 payload 相同還會被去重吃掉）
        hot.update_unit(unit_id, {MISSION_COUNT_KEY: 0})
        event = _displace(db, unit, float(lat), float(lng), tick, cfg, rng, order_service_factory)
        events.append(event)
    return events


def _displace(
    db: Session,
    unit: TacticalUnit,
    lat: float,
    lng: float,
    tick: int,
    cfg: SurvivabilityConfig,
    rng: DeterministicRNG,
    order_service_factory: Callable[[Session], OrderService],
) -> LedgerEvent:
    issuer = ensure_system_participant(db, unit.session_id, unit.faction)
    service = order_service_factory(db)
    profile = resolve_unit_mobility(db, unit.id).profile
    last_error = ""
    for _ in range(_MAX_ATTEMPTS):
        # **每次重試都重抽**——抽樣次數因此隨地形變動，這正是它要用獨立 stream 的理由。
        to_lat, to_lng = pick_displacement_point(lat, lng, rng, cfg)
        payload = MovePayload(
            to_h3=h3.latlng_to_cell(to_lat, to_lng, _HEX_RES),
            mobility_profile=profile,
            to_lat=to_lat,
            to_lng=to_lng,
        )
        try:
            resp = service.submit(
                unit.session_id,
                OrderRequest(
                    unit_id=unit.id,
                    order_type=OrderType.MOVE,
                    payload=payload.model_dump(),
                ),
                issuer,
            )
        except Exception as err:  # 預檢不可行／權限／地形——換個方位再試
            last_error = str(err)
            continue
        return LedgerEvent(
            event_type="SURVIVABILITY_MOVE",
            tick=tick,
            initiator_id=unit.id,
            ai_decision={
                "reason": "COUNTER_BATTERY",
                "order_id": resp.id,
                "from_lat": lat,
                "from_lng": lng,
                "to_lat": to_lat,
                "to_lng": to_lng,
            },
        )
    _LOG.info("session %s 單位 %s 陣地變換受阻：%s", unit.session_id, unit.id, last_error)
    return LedgerEvent(
        event_type="SURVIVABILITY_MOVE_BLOCKED",
        tick=tick,
        initiator_id=unit.id,
        ai_decision={
            "reason": "NO_REACHABLE_POSITION",
            "reason_detail": last_error[:200],
            "attempts": _MAX_ATTEMPTS,
        },
    )
