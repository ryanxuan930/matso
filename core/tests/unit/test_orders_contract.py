"""Order 端點契約 fuzz（O3.1 驗收）——schemathesis 對 FastAPI 產生的 OpenAPI 隨機打，
斷言已實作端點在任意輸入下**不產生 5xx server error**（db/gateway 以測試替身注入）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import schemathesis
from _order_fakes import FakeGateway
from hypothesis import HealthCheck, settings
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db, get_gateway
from app.main import app
from app.models import Base

# 僅針對已實作的 order 端點打（其餘尚未實作）
schema = schemathesis.openapi.from_asgi("/openapi.json", app).include(path_regex="/orders")

_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Base.metadata.create_all(_engine)
_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


@pytest.fixture(autouse=True)
def _overrides() -> Iterator[None]:
    def _db() -> Iterator[Session]:
        db = _factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_gateway] = lambda: FakeGateway()
    yield
    app.dependency_overrides.clear()


@schema.parametrize()
@settings(max_examples=20, deadline=None, suppress_health_check=list(HealthCheck))
def test_orders_no_server_error(case: schemathesis.Case) -> None:
    response = case.call()
    assert response.status_code < 500, (
        f"{case.method} {case.path} → {response.status_code}: {response.text[:200]}"
    )
