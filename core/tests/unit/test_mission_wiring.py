"""WP-A2 收尾：任務令在**活執行期**真的會展開成子令。

在補這個檔案之前，`sim_runtime` 從來沒有把 planner 傳進 `Kernel`——MISSION 令收得下、
狀態變 VALIDATED、指令列看得到，**然後什麼都不會發生**。golden `mission_seize_60`
抓不到，因為它自帶一個純記憶體的測試用 planner：釘住的是分解邏輯，不是生產接線。

所以這一組全部打在**接線層**（DB 的令進、DB 的子令出），不重測分解器。
"""

from __future__ import annotations

import pytest

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
    # 行軍第一步是「展開縱隊 + 前往第 1 航路點」兩道令（WP-A2 的 spacing_km 接線）。
    assert {s.order_type for s in subs} == {"FORMATION", "MOVE"}
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
    # **只有需要過物理閘門的令會被打回**：FORMATION 是狀態宣告，不查可達性。
    moves = [s for s in _sub_orders(db, world) if s.order_type == "MOVE"]
    assert moves and all(s.status.value == "REJECTED" for s in moves)
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


# ---- 四種任務型都要走得完整條接線（不只 MOVE_MARCH）----
#
# 只測 MOVE_MARCH 的話，另外三型的 payload 形狀（objective/area/line）到底進不進得了
# `MissionPayload.model_validate`、以及它們產的子令是不是每一種都送得出去，全都沒人驗過。


def _run(db, world, hot, ticks: int, *, planner=None):  # type: ignore[no-untyped-def]
    planner = planner or _planner(db, world, hot)
    events = []
    for t in range(1, ticks + 1):
        events.extend(planner.plan(_now(t)))
    return planner, events


def test_seize_moves_along_the_axis(session_factory) -> None:  # type: ignore[no-untyped-def]
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    _issue_mission(
        db,
        world,
        mission_type="SEIZE",
        params={
            "objective": {"lat": 23.80, "lng": 121.30},
            "axis": [{"lat": 23.77, "lng": 121.27}],
            "objective_radius_m": 400,
        },
    )
    _run(db, world, hot, 2)
    moves = [s for s in _sub_orders(db, world) if s.order_type == "MOVE"]
    assert moves, "SEIZE 沒有派生任何 MOVE 子令"
    assert moves[0].payload["to_h3"]
    db.close()


def test_defend_issues_a_posture_order_once_in_the_area(session_factory) -> None:  # type: ignore[no-untyped-def]
    """DEFEND 的第二階段產的是 POSTURE 令——**不是 MOVE**。

    `_hydrate` 若無條件補 `to_h3`，POSTURE 會直接驗證失敗（它沒有那個欄位）。
    """
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    # 一開始就站在防區裡 → 下一個 tick 就進 CONSOLIDATING 並下 POSTURE。
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    _issue_mission(
        db,
        world,
        mission_type="DEFEND",
        params={"area": {"lat": 23.75, "lng": 121.25}, "area_radius_m": 800},
    )
    _run(db, world, hot, 3)
    types = {s.order_type for s in _sub_orders(db, world)}
    assert "POSTURE" in types, f"DEFEND 沒有下出姿態令，只有 {types}"
    db.close()


def test_screen_deliberately_never_engages(session_factory) -> None:  # type: ignore[no-untyped-def]
    """掩護幕的任務是偵測回報，不是接戰——就位後**不下任何 ENGAGE**。

    這條是釘語義的：若哪天有人「順手」讓 SCREEN 也接戰，掩護幕就變成前進攻擊。
    """
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    _issue_mission(
        db,
        world,
        mission_type="SCREEN",
        params={"line": [{"lat": 23.75, "lng": 121.25}, {"lat": 23.76, "lng": 121.26}]},
    )
    _run(db, world, hot, 4)
    assert not [s for s in _sub_orders(db, world) if s.order_type == "ENGAGE"]
    db.close()


@pytest.mark.parametrize(
    ("mission_type", "params"),
    [
        (
            "SEIZE",
            {"objective": {"lat": 23.80, "lng": 121.30}, "axis": [], "objective_radius_m": 400},
        ),
        ("DEFEND", {"area": {"lat": 23.75, "lng": 121.25}, "area_radius_m": 800}),
        ("SCREEN", {"line": [{"lat": 23.75, "lng": 121.25}]}),
        ("MOVE_MARCH", {"route": [{"lat": 23.80, "lng": 121.30}]}),
    ],
)
def test_every_mission_type_survives_the_round_trip_through_the_order_row(  # type: ignore[no-untyped-def]
    session_factory, mission_type, params
) -> None:
    """四型的 params 都要能從 DB 的 payload 重新 parse 回 `MissionPayload`，**連續多個 tick**。

    planner 每 tick 都重 parse 一次（它不快取令），而 payload 裡混著 `_mission_state`
    這種底線前綴的執行期欄位。

    ⚠ `_load` 的底線過濾**目前不是必要的**——pydantic 預設 `extra="ignore"`，我用突變測試
    驗過：拿掉過濾這一組仍然全綠。留著是為了讓「哪天有人把 `MissionPayload` 改成
    `extra="forbid"`」不會炸掉每一道進行中的任務令。不要在註解裡宣稱它現在擋住了什麼。
    """
    from _order_fakes import seed_world

    from app.models.tables import Order

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    _issue_mission(db, world, mission_type=mission_type, params=params)
    _run(db, world, hot, 3)
    missions = (
        db.query(Order)
        .filter(Order.session_id == world.session_id, Order.order_type == "MISSION")
        .all()
    )
    assert all(m.status.value != "REJECTED" for m in missions), f"{mission_type} 被判 REJECTED"
    db.close()


# ---- 任務終局：子令不可以留在天上飛 ----


def test_finishing_a_mission_cancels_its_still_flying_sub_orders(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**這條抓的是我在 A2 收尾漏掉的一段**。

    `_cancel_children` 只掛在使用者按取消那一條路上；planner 走到終局時是直接把母令寫成
    COMPLETED 就結束。於是任務完成（或失敗）之後，最後一道 MOVE 子令仍是 EXECUTING
    ——**部隊照著一個已經結束的任務繼續走**。失敗的任務更糟。
    """
    from _order_fakes import seed_world

    from app.models.tables import Order

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    # 一開始就站在唯一的航路點上 → 下一個 tick 就走完並進終局。
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    resp = _issue_mission(
        db, world, mission_type="MOVE_MARCH", params={"route": [{"lat": 23.75, "lng": 121.25}]}
    )
    _, events = _run(db, world, hot, 6)

    parent = db.get(Order, resp.id)
    assert parent.status.value == "COMPLETED", "任務沒有走到終局，這條測試沒測到東西"
    assert "MISSION_ENDED" in [e.event_type for e in events]
    leftovers = [s for s in _sub_orders(db, world) if s.status.value in ("VALIDATED", "EXECUTING")]
    assert not leftovers, f"任務結束了還有 {len(leftovers)} 道子令在執行"
    db.close()


def test_a_cancel_landing_inside_the_tick_is_not_overwritten(session_factory) -> None:  # type: ignore[no-untyped-def]
    """使用者在 API 行程取消任務，planner **不可以**把它靜靜改回 COMPLETED。

    ## 為什麼要直接呼叫 `_terminate`

    這個競態的窗口在**一次 `plan()` 之內**：`_load` 的 SELECT 撈到令（那時還是 EXECUTING），
    取消在那之後、`_terminate` 之前落庫。從外面連跑兩次 `plan()` 碰不到它——
    第二次的 `_load` WHERE 就把 CANCELLED 濾掉了，終局那段根本不會執行。
    **突變測試證明了這件事**：我第一版從外面跑的測試，把重讀和 `next_status` 都拿掉照樣全綠。

    ## 陷阱本身

    不是 `next_status` 不夠——是 `expire_on_commit=False` + runner 整局共用一條 Session：
    `db.get` 直接命中 identity map 回傳舊狀態，一句 SQL 都不發。於是被別的連線取消掉的令，
    在 planner 眼裡仍是 EXECUTING，`next_status` 順利通過。故重讀要帶 `populate_existing=True`。
    """
    from _order_fakes import seed_world

    from app.models.enums import OrderStatus
    from app.models.tables import Order
    from app.orders.mission import MissionPhase, MissionState

    world = seed_world(session_factory)
    db = session_factory()  # planner 的長生命期 Session
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "alive": True})
    resp = _issue_mission(db, world)
    planner = _planner(db, world, hot)
    planner.plan(_now(1))
    order = db.get(Order, resp.id)  # planner 手上那個物件（狀態 EXECUTING）

    # 另一條連線（＝API 行程）在 tick 之中把它取消掉。
    other = session_factory()
    other.get(Order, resp.id).status = OrderStatus.CANCELLED
    other.commit()
    other.close()
    assert order.status is OrderStatus.EXECUTING, (
        "identity map 應該還握著舊狀態——沒有的話這條測試就沒有在測那個陷阱"
    )

    assert planner._terminate(order, MissionState(phase=MissionPhase.COMPLETE), _now(2)) == []
    db.commit()

    check = session_factory()
    assert check.get(Order, resp.id).status.value == "CANCELLED", (
        "planner 把使用者的取消覆寫成 COMPLETED 了——identity map 的舊狀態沒有重讀"
    )
    check.close()
    db.close()
