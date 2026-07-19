"""共用測試鷹架（O1.7/r14 dedup）。

- session_factory：SQLite in-memory（StaticPool 讓多 session 共用同一連線）。
  整合測試模組以同名 local fixture 覆蓋為 MariaDB 版（pytest 就近優先）。
- build_noop_kernel：全 no-op 依賴的 Kernel 工廠，個別測試以 kwargs 覆蓋要注入的替身。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.engine.clock import SimClock
from app.engine.kernel import Kernel
from app.engine.subsystems import (
    NoOpAdjudicator,
    NoOpBroadcaster,
    NoOpCommsSystem,
    NoOpEventSink,
    NoOpLogisticsSystem,
    NoOpMovementSystem,
    NoOpOrderSource,
    NoOpSensorSystem,
    NoOpTriggerChecker,
    NullMonotonicClock,
)
from app.models import Base
from app.state.hot_state import InMemoryHotState


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


def make_noop_kernel(session_id: str = "s", **overrides: Any) -> Kernel:
    """全 no-op Kernel；以 kwargs 覆蓋任一依賴（如 event_sink=、checkpointer=）。"""
    defaults: dict[str, Any] = {
        "session_id": session_id,
        "clock": SimClock(),
        "order_source": NoOpOrderSource(),
        "adjudicator": NoOpAdjudicator(),
        "movement": NoOpMovementSystem(),
        "sensors": NoOpSensorSystem(),
        "comms": NoOpCommsSystem(),
        "logistics": NoOpLogisticsSystem(),
        "trigger_checker": NoOpTriggerChecker(),
        "broadcaster": NoOpBroadcaster(),
        "event_sink": NoOpEventSink(),
        "hot_state": InMemoryHotState(),
        "wall_clock": NullMonotonicClock(),
    }
    defaults.update(overrides)
    return Kernel(**defaults)


@pytest.fixture
def build_noop_kernel() -> Callable[..., Kernel]:
    return make_noop_kernel
