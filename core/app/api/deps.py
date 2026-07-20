"""API 依賴注入（O3.1）。測試以 app.dependency_overrides 覆寫 get_db / get_gateway。"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

import grpc
from fastapi import Depends
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import default_session_factory
from app.orders.precheck import PhysicsGateway, TerrainGatewayAdapter
from app.orders.service import OrderService
from app.plugins import TerrainClient


def get_db() -> Iterator[Session]:
    db = default_session_factory()()
    try:
        yield db
    finally:
        db.close()


@lru_cache(maxsize=1)
def _default_channel() -> grpc.Channel:
    return grpc.insecure_channel(Settings().terrain_grpc_target)


def get_gateway() -> PhysicsGateway:
    """真物理 gateway（轉接 terrain gRPC）。terrain 未起時呼叫會拋 → API 503。"""
    return TerrainGatewayAdapter(TerrainClient(_default_channel()))


def get_order_service(
    db: Session = Depends(get_db),
    gateway: PhysicsGateway = Depends(get_gateway),
) -> OrderService:
    return OrderService(db, gateway)
