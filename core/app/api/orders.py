"""Order REST 端點（O3.1，SPEC §16.1）。

POST   /api/v1/sessions/{session_id}/orders            下令（回 precheck；不可行 422 REJECTED）
DELETE /api/v1/sessions/{session_id}/orders/{order_id} 取消未執行指令
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_order_service
from app.orders.schemas import OrderRequest, OrderResponse
from app.orders.service import OrderService

router = APIRouter(prefix="/api/v1/sessions", tags=["orders"])


@router.post("/{session_id}/orders", status_code=201, response_model=OrderResponse)
def issue_order(
    session_id: str,
    req: OrderRequest,
    service: OrderService = Depends(get_order_service),
) -> OrderResponse:
    return service.submit(session_id, req)


@router.delete("/{session_id}/orders/{order_id}", response_model=OrderResponse)
def cancel_order(
    session_id: str,
    order_id: str,
    service: OrderService = Depends(get_order_service),
) -> OrderResponse:
    return service.cancel(session_id, order_id)
