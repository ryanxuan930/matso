"""移動執行 DB 整合（O3.4 驗收）：下 MOVE 令（O3.1）→ movement 逐 tick 推進 → 位置=終點、
order 落庫 COMPLETED。並單測 DbOrderStore 的狀態轉移。
"""

from __future__ import annotations

import h3
from _order_fakes import FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimTime
from app.models.enums import OrderStatus
from app.models.tables import Order, TacticalUnit
from app.movement.db_store import DbOrderStore
from app.movement.system import MovementSystem
from app.orders.schemas import OrderRequest, OrderType
from app.orders.service import OrderService
from app.state.hot_state import InMemoryHotState

_START = h3.latlng_to_cell(23.75, 121.25, 8)  # seed_world 藍軍座標
_END = h3.latlng_to_cell(23.78, 121.28, 8)
_PATH = h3.grid_path_cells(_START, _END)


class FixedPlanner:
    def plan(self, from_h3: str, to_h3: str, mobility_profile: str) -> list[str]:
        return list(_PATH)


def _time(tick: int) -> SimTime:
    return SimTime(tick=tick, sim_time_ms=tick * 1000)


async def test_move_order_to_completion_integration(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        # O3.1：下 MOVE 令 → VALIDATED（用可達的假 gateway）
        resp = OrderService(db, FakeGateway(reachable=True)).submit(
            world.session_id,
            OrderRequest(
                unit_id=world.blue_unit_id,
                order_type=OrderType.MOVE,
                payload={"to_h3": _END, "mobility_profile": "FOOT"},
                issuer_id=world.blue_issuer_id,
            ),
        )
        assert resp.status is OrderStatus.VALIDATED

        # O3.4：movement 子系統逐 tick 推進
        hot = InMemoryHotState()
        hot.put_unit(world.blue_unit_id, {"h3": _START})
        system = MovementSystem(
            world.session_id, hot, DbOrderStore(db), FixedPlanner(), speed_hexes=1
        )
        for t in range(len(_PATH) + 2):
            await system.step(_time(t))

        # 位置 = 路徑終點
        assert hot.get_unit(world.blue_unit_id)["h3"] == _PATH[-1]
        # order 落庫為 COMPLETED + resolved_at_tick
        order = db.get(Order, resp.id)
        assert order is not None
        assert order.status is OrderStatus.COMPLETED
        assert order.resolved_at_tick is not None


def test_db_store_pending_and_transitions(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        created = OrderService(db, FakeGateway(reachable=True)).submit(
            world.session_id,
            OrderRequest(
                unit_id=world.blue_unit_id,
                order_type=OrderType.MOVE,
                payload={"to_h3": _END, "mobility_profile": "FOOT"},
                issuer_id=world.blue_issuer_id,
            ),
        )
        store = DbOrderStore(db)
        pending = store.pending_moves(world.session_id)
        assert len(pending) == 1
        assert pending[0].from_h3 == _START  # 由單位座標推導（權威來自 DB）

        store.mark_executing(created.id)
        assert db.get(Order, created.id).status is OrderStatus.EXECUTING  # type: ignore[union-attr]
        assert store.pending_moves(world.session_id) == []  # 已非 VALIDATED

        store.mark_completed(created.id, 42)
        done = db.get(Order, created.id)
        assert done.status is OrderStatus.COMPLETED  # type: ignore[union-attr]
        assert done.resolved_at_tick == 42  # type: ignore[union-attr]


def test_db_store_skips_unit_without_position(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        OrderService(db, FakeGateway(reachable=True)).submit(
            world.session_id,
            OrderRequest(
                unit_id=world.blue_unit_id,
                order_type=OrderType.MOVE,
                payload={"to_h3": _END, "mobility_profile": "FOOT"},
                issuer_id=world.blue_issuer_id,
            ),
        )
        unit = db.get(TacticalUnit, world.blue_unit_id)
        unit.current_lat = None  # type: ignore[union-attr]
        db.commit()
        assert DbOrderStore(db).pending_moves(world.session_id) == []  # 無座標 → 跳過


def test_db_store_transitions_ignore_missing_order(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        store = DbOrderStore(db)
        store.mark_executing("nonexistent")  # 不存在 → 靜默略過，不拋
        store.mark_completed("nonexistent", 1)
