"""O11.3 AI 指令橋接 + 護欄 G3 feasibility：映射、物理可行性、落單 VALIDATED。"""

from __future__ import annotations

from _order_fakes import FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.ai_loop.orders_bridge import (
    PrecheckFeasibility,
    submit_faction_orders,
    tactical_order_to_request,
)
from app.models.enums import OrderStatus
from app.models.tables import Order
from app.orders.schemas import OrderType

# ---- tactical_order_to_request（純映射）----


def test_map_move() -> None:
    req = tactical_order_to_request({"unit_id": "u1", "order_type": "MOVE", "target_h3": "8a2a"})
    assert req is not None
    assert req.order_type is OrderType.MOVE
    assert req.payload["to_h3"] == "8a2a"
    assert req.payload["mobility_profile"] == "FOOT"


def test_map_engage_with_policy_and_weapon() -> None:
    req = tactical_order_to_request(
        {
            "unit_id": "u1",
            "order_type": "ENGAGE",
            "target_unit_id": "r1",
            "fire_policy": "ANTI_ARMOR_HOLD",
            "weapon_template_id": "w9",
        }
    )
    assert req is not None
    assert req.payload["target_unit_id"] == "r1"
    assert req.payload["fire_policy"] == "ANTI_ARMOR_HOLD"
    assert req.payload["weapon_id"] == "w9"


def test_map_move_by_latlng_derives_h3() -> None:
    # AI 給經緯（LLM 算不出 H3）→ 伺服端換算 to_h3 + 保留精確落點。
    import h3

    req = tactical_order_to_request(
        {"unit_id": "u1", "order_type": "MOVE", "target_lat": 24.15, "target_lng": 120.84}
    )
    assert req is not None
    assert req.order_type is OrderType.MOVE
    assert req.payload["to_h3"] == h3.latlng_to_cell(24.15, 120.84, 8)
    assert req.payload["to_lat"] == 24.15 and req.payload["to_lng"] == 120.84


def test_map_move_missing_target_returns_none() -> None:
    # 無 target_h3 也無 target_lat/lng → 不落單。
    assert tactical_order_to_request({"unit_id": "u1", "order_type": "MOVE"}) is None


def test_map_engage_missing_target_returns_none() -> None:
    assert tactical_order_to_request({"unit_id": "u1", "order_type": "ENGAGE"}) is None


def test_map_hold_returns_none() -> None:
    # HOLD 不在 OrderType（原地待命）→ 不落單。
    assert tactical_order_to_request({"unit_id": "u1", "order_type": "HOLD"}) is None


def test_map_missing_unit_id_returns_none() -> None:
    assert tactical_order_to_request({"order_type": "MOVE", "target_h3": "8a2a"}) is None


# ---- PrecheckFeasibility（G3，物理可行性）----


def test_feasibility_move_reachable(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        checker = PrecheckFeasibility(db, world.session_id, FakeGateway(reachable=True))
        ok, reason = checker.is_feasible(
            {"unit_id": world.blue_unit_id, "order_type": "MOVE", "target_h3": "8a2a1072b59ffff"}
        )
    assert ok is True
    assert reason == ""


def test_feasibility_move_unreachable(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        checker = PrecheckFeasibility(db, world.session_id, FakeGateway(reachable=False))
        ok, reason = checker.is_feasible(
            {"unit_id": world.blue_unit_id, "order_type": "MOVE", "target_h3": "8a2a1072b59ffff"}
        )
    assert ok is False
    assert reason  # 帶不可達原因


def test_feasibility_unknown_unit(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        checker = PrecheckFeasibility(db, world.session_id, FakeGateway())
        ok, _ = checker.is_feasible({"unit_id": "ghost", "order_type": "MOVE", "target_h3": "8a2a"})
    assert ok is False


def test_feasibility_hold_not_feasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        checker = PrecheckFeasibility(db, world.session_id, FakeGateway())
        ok, _ = checker.is_feasible({"unit_id": world.blue_unit_id, "order_type": "HOLD"})
    assert ok is False  # 無法映射 → G3 視為不可行（該令不入合格集）


# ---- submit_faction_orders（落 VALIDATED）----


def test_submit_move_becomes_validated(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        res = submit_faction_orders(
            db,
            world.session_id,
            [{"unit_id": world.blue_unit_id, "order_type": "MOVE", "target_h3": "8a2a1072b59ffff"}],
            issuer_id=world.blue_issuer_id,
            gateway=FakeGateway(reachable=True),
        )
        assert len(res.submitted) == 1
        order = db.get(Order, res.submitted[0])
        assert order is not None
        assert order.status == OrderStatus.VALIDATED


def test_submit_unreachable_rejected(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        res = submit_faction_orders(
            db,
            world.session_id,
            [{"unit_id": world.blue_unit_id, "order_type": "MOVE", "target_h3": "8a2a1072b59ffff"}],
            issuer_id=world.blue_issuer_id,
            gateway=FakeGateway(reachable=False),
        )
    assert not res.submitted
    assert len(res.rejected) == 1


def test_submit_non_participant_issuer_rejected(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        res = submit_faction_orders(
            db,
            world.session_id,
            [{"unit_id": world.blue_unit_id, "order_type": "MOVE", "target_h3": "8a2a1072b59ffff"}],
            issuer_id="not-a-participant",
            gateway=FakeGateway(reachable=True),
        )
    assert not res.submitted
    assert len(res.rejected) == 1  # 權限（非參與者）→ 歸 rejected，不中斷


def test_submit_hold_skipped(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        res = submit_faction_orders(
            db,
            world.session_id,
            [{"unit_id": world.blue_unit_id, "order_type": "HOLD"}],
            issuer_id=world.blue_issuer_id,
            gateway=FakeGateway(),
        )
    assert not res.submitted and not res.rejected
    assert len(res.skipped) == 1


def test_submit_rate_cap(session_factory: sessionmaker[Session]) -> None:
    # O11.8 防洗版：LLM 一次吐 10 令、上限 3 → 只處理前 3，其餘記 capped。
    world = seed_world(session_factory)
    orders = [
        {"unit_id": world.blue_unit_id, "order_type": "MOVE", "target_h3": f"8a2a1072b59{i}fff"}
        for i in range(10)
    ]
    with session_factory() as db:
        res = submit_faction_orders(
            db,
            world.session_id,
            orders,
            issuer_id=world.blue_issuer_id,
            gateway=FakeGateway(reachable=True),
            max_orders=3,
        )
    assert res.capped == 7
    assert len(res.submitted) + len(res.rejected) + len(res.skipped) == 3


def test_map_posture() -> None:
    """**過去這裡回 None**——註解寫「對應子系統 NoOp」，但 WP-C1 完成後
    `drain_posture_orders` 每 tick 都在跑。

    後果：LLM 決定掘壕、令被靜靜丟掉、AI 以為部隊進入防禦而實際上還站著。
    """
    req = tactical_order_to_request({"unit_id": "u1", "order_type": "POSTURE", "posture": "dug_in"})

    assert req is not None
    assert req.order_type is OrderType.POSTURE
    assert req.payload == {"posture": "DUG_IN"}


def test_posture_without_a_posture_is_dropped() -> None:
    assert tactical_order_to_request({"unit_id": "u1", "order_type": "POSTURE"}) is None


def test_recon_is_not_bridged_because_nothing_executes_it() -> None:
    """RECON 有 OrderType、有席位表位置…就是沒有任何執行端。落單只會永遠停在 VALIDATED。"""
    assert tactical_order_to_request({"unit_id": "u1", "order_type": "RECON"}) is None
