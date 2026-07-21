"""Order REST 端點（O3.1 + O4.5，SPEC §16.1/§13.4）。

GET    /api/v1/sessions/{id}/orders            列出指令（pending + 歷史，faction-scoped）
POST   /api/v1/sessions/{id}/orders            下令（回 precheck；不可行 422 REJECTED）
DELETE /api/v1/sessions/{id}/orders/{oid}      取消未執行指令

issuer 由認證 token 推導（SPEC §12：前端不可信）——非該 session 參與者 → 403。
"""

from __future__ import annotations

import logging

import redis
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_order_service, get_settings
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.config import Settings
from app.orders.schemas import OrderRequest, OrderResponse
from app.orders.service import OrderService
from app.stream.faction_filter import is_omniscient
from app.stream.publish import publish_event

_LOG = logging.getLogger("app.orders")

router = APIRouter(prefix="/api/v1/sessions", tags=["orders"])


def _emit_adjudication_event(
    settings: Settings, session_id: str, faction: str, resp: OrderResponse
) -> None:
    """E2E stub 模式：下令成功後發一則裁決事件到 WS stream（真裁決由 kernel 產出）。"""
    if not settings.stub_gateway:
        return
    event_type = "ENGAGEMENT_RESOLVED" if resp.order_type == "ENGAGE" else "ORDER_VALIDATED"
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        publish_event(
            client,
            session_id,
            event_type,
            {"order_id": resp.id, "unit_id": resp.unit_id, "order_type": resp.order_type},
            faction=faction,
        )
        client.close()
    except redis.RedisError as exc:  # 失敗不阻斷下令，但要留痕（CODE_REVIEW C11）
        _LOG.warning("session %s: 裁決事件發佈失敗：%s", session_id, exc)


@router.get("/{session_id}/orders", response_model=list[OrderResponse])
def list_orders(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> list[OrderResponse]:
    p = require_participant(db, user, session_id)
    return service.list_orders(session_id, p.faction.value, is_omniscient(p.role))


@router.post("/{session_id}/orders", status_code=201, response_model=OrderResponse)
def issue_order(
    session_id: str,
    req: OrderRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
    settings: Settings = Depends(get_settings),
) -> OrderResponse:
    p = require_participant(db, user, session_id)
    resp = service.submit(session_id, req, p.id)
    _emit_adjudication_event(settings, session_id, p.faction.value, resp)
    return resp


@router.delete("/{session_id}/orders/{order_id}", response_model=OrderResponse)
def cancel_order(
    session_id: str,
    order_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> OrderResponse:
    p = require_participant(db, user, session_id)  # 須為參與者
    return service.cancel(session_id, order_id, p.faction.value, is_omniscient(p.role))
