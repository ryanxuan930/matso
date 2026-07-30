"""WP-A2 收尾：任務令在**活執行期**真的會展開成子令。

在補這個檔案之前，`sim_runtime` 從來沒有把 planner 傳進 `Kernel`——MISSION 令收得下、
狀態變 VALIDATED、指令列看得到，**然後什麼都不會發生**。golden `mission_seize_60`
抓不到，因為它自帶一個純記憶體的測試用 planner：釘住的是分解邏輯，不是生產接線。

所以這一組全部打在**接線層**（DB 的令進、DB 的子令出），不重測分解器。
"""

from __future__ import annotations

from app.engine.clock import SimTime
from app.engine.mission_wiring import STATE_KEY, LiveMissionPlanner
from app.state.hot_state import InMemoryHotState


def _now(tick: int) -> SimTime:
    return SimTime(tick=tick, sim_time_ms=tick * 60_000)


def _issue_mission(db, world, *, mission_type: str = "MOVE_MARCH", params=None):  # type: ignore[no-untyped-def]
    from _order_fakes import FakeGateway

    from app.orders.schemas import OrderRequest, OrderType
    from app.orders.service import OrderService

    return OrderService(db, FakeGateway()).submit(
        world.session_id,
        OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.MISSION,
            payload={
                "mission_type": mission_type,
                "params": params
                if params is not None
                else {"route": [{"lat": 23.80, "lng": 121.30}]},
            },
        ),
        world.blue_issuer_id,
    )


def _planner(db, world, hot):  # type: ignore[no-untyped-def]
    from _order_fakes import FakeGateway

    return LiveMissionPlanner(db, world.session_id, hot, gateway=FakeGateway())


def _sub_orders(db, world):  # type: ignore[no-untyped-def]
    from app.models.tables import Order

    return (
        db.query(Order)
        .filter(Order.session_id == world.session_id, Order.parent_order_id.isnot(None))
        .all()
    )


def test_a_mission_order_actually_produces_sub_orders(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**本卡最重要的一條**：下一道任務令，跑一個 tick，DB 裡要真的多出一道子令。

    這正是活執行期一直缺的那一段——分解器、評估、UI 全都做好了，
    就是沒有人把 planner 傳進 Kernel。
    """
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})

    assert _issue_mission(db, world).status.value == "VALIDATED"
    assert _sub_orders(db, world) == []

    _planner(db, world, hot).plan(_now(1))

    subs = _sub_orders(db, world)
    assert subs, "任務令跑了一個 tick 卻沒有產生任何子令——planner 又沒接上"
    assert subs[0].order_type == "MOVE"
    db.close()


def test_progress_lives_on_the_order_so_a_restart_does_not_rewind(session_factory) -> None:  # type: ignore[no-untyped-def]
    """進度寫回 `Order.payload._mission_state`。只放在 planner 實例裡的話，
    runner 一重啟（SimManager 每 3 秒掃描重建）任務就從 PLANNED 重跑一遍。"""
    from _order_fakes import seed_world

    from app.models.tables import Order

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    resp = _issue_mission(db, world)
    _planner(db, world, hot).plan(_now(1))

    saved = db.get(Order, resp.id).payload[STATE_KEY]
    assert saved["phase"] != "PLANNED"

    # 換一個 planner 實例（＝重啟）→ 讀回進度，不從頭來。
    fresh = _planner(db, world, hot)
    fresh.plan(_now(2))
    assert db.get(Order, resp.id).payload[STATE_KEY]["phase"] == saved["phase"]
    db.close()


def test_the_mission_order_leaves_validated_so_it_is_not_re_admitted(session_factory) -> None:  # type: ignore[no-untyped-def]
    """任務令首見即轉 EXECUTING——留在 VALIDATED 會讓它每 tick 都被當成新令。"""
    from _order_fakes import seed_world

    from app.models.tables import Order

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    resp = _issue_mission(db, world)
    _planner(db, world, hot).plan(_now(1))
    assert db.get(Order, resp.id).status.value == "EXECUTING"
    db.close()


def test_a_session_with_no_missions_does_nothing_at_all(session_factory) -> None:  # type: ignore[no-untyped-def]
    """既有局（沒有任何 MISSION 令）→ 空事件、零副作用。"""
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    assert _planner(db, world, InMemoryHotState()).plan(_now(1)) == []
    db.close()


def test_planner_never_raises_into_the_kernel(session_factory) -> None:  # type: ignore[no-untyped-def]
    """`run_tick` 對子系統沒有任何防護——一個 raise 會讓 runner 崩潰後被
    SimManager 每 3 秒重建成無限重啟迴圈。"""
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    planner = _planner(db, world, InMemoryHotState())
    db.close()  # 讓底下的查詢一定會炸
    assert planner.plan(_now(1)) == []


def test_sub_orders_are_attributed_to_the_mission(session_factory) -> None:  # type: ignore[no-untyped-def]
    """子令要掛 `parent_order_id`——AAR 的任務時間軸（`aar/missions.py`）靠它串。"""
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    resp = _issue_mission(db, world)
    _planner(db, world, hot).plan(_now(1))
    assert all(s.parent_order_id == resp.id for s in _sub_orders(db, world))
    db.close()


def test_a_rejected_sub_order_leaves_a_trace_instead_of_vanishing(session_factory) -> None:  # type: ignore[no-untyped-def]
    """子令被打回**不可以只記 log**。

    「任務看起來在跑、實際上一步都不動」正是這張卡要修的病——第一版的 `_submit`
    把預檢例外記成 INFO 就吞了，畫面上什麼都看不到。
    """
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    _issue_mission(db, world)

    class _RefusingGateway:
        def path_reachable(self, *_a, **_k):  # type: ignore[no-untyped-def]
            return False, "測試：一律不可達"

        def has_los(self, *_a, **_k):  # type: ignore[no-untyped-def]
            return True, ""

    planner = LiveMissionPlanner(db, world.session_id, hot, gateway=_RefusingGateway())
    events = planner.plan(_now(1))
    assert [e.event_type for e in events if e.event_type == "MISSION_SUBORDER_REJECTED"]
    # `OrderService.submit` 打回時**仍會落一列 REJECTED**（既有語義），故指令列也看得到
    # ——兩條痕跡都要在：帳本供 AAR 追究，令列供操作員當場看見。
    assert all(s.status.value == "REJECTED" for s in _sub_orders(db, world))
    db.close()


def test_move_sub_orders_get_the_hex_the_decomposer_cannot_compute(session_factory) -> None:  # type: ignore[no-untyped-def]
    """`MovePayload.to_h3` 必填，而分解器只給 `to_lat/to_lng`——那不是疏漏：
    它的 import 被白名單鎖住，**不能** import h3。latlng→hex 的正確位置是接線層。

    少了這一步，每一道 MOVE 子令都會在驗證層被打成「MOVE 載荷格式錯誤」。
    """
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    _issue_mission(db, world)
    _planner(db, world, hot).plan(_now(1))
    move = next(s for s in _sub_orders(db, world) if s.order_type == "MOVE")
    assert move.payload["to_h3"]
    # 精確落點仍在（hex 只是預檢用的，最終座標不吸附到格心）。
    assert move.payload["to_lat"] and move.payload["to_lng"]
    db.close()


def test_non_move_sub_orders_are_passed_through_untouched(session_factory) -> None:  # type: ignore[no-untyped-def]
    """`_hydrate` 只碰 MOVE。POSTURE/ENGAGE 被塞一個 `to_h3` 會直接驗證失敗。"""
    from app.engine.mission_wiring import _hydrate
    from app.orders.schemas import OrderType

    payload = {"posture": "DEFENSE"}
    assert _hydrate(OrderType.POSTURE, payload) is payload
    assert _hydrate(OrderType.ENGAGE, {"target_unit_id": "x"}) == {"target_unit_id": "x"}
