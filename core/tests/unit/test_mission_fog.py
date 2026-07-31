"""A1 補洞：任務分解**不得**經由 world_view 偷看 ground truth。

## 為什麼這一組打在接線層，而不是打在分解器上

`decomposer` 已經有一條 import 白名單測試（`test_decomposer.py`）釘住「分解器自己不查 DB」。
那條測試**擋不住這張卡要修的洞**：洞在呼叫端——`mission_wiring._world_view` 把
`ground_truth_enemies`（DB 全表）裝進 `known_enemies` 遞給分解器。分解器全程守規矩，
拿到的資料卻是全知的。白名單全綠、陷阱照樣成立。

所以守門要往上移一層：**下一道 SEIZE 令，跑幾個 tick，看 DB 裡冒出來的子令有沒有指向
一個本陣營根本沒偵測到的單位**。這是從外面觀測得到的事實，不是對函式參數的斷言。

## 三條互相撐住的測試

單獨一條「沒有 ENGAGE」是廢的——任務沒跑起來也會沒有 ENGAGE。故：

1. 沒偵測到 → 不得有 ENGAGE，**且**必須有 POSTURE（證明任務確實走到了那個決策點，
   是「看不見所以不打」而不是「什麼都沒發生」）。
2. 偵測到 → 必須有 ENGAGE。contact 由**真的 `SensorSweepSystem` 掃出來**，不是手搭的
   IntelContact——這個 repo 的招牌病就是「測試餵的資料不是引擎會產生的資料」。
3. `ai_ground_truth` 退回開關在這條路徑上要真的有作用（它過去完全管不到這裡）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.engine.clock import SimTime
from app.engine.mission_wiring import LiveMissionPlanner
from app.state.hot_state import InMemoryHotState

# BLUE 站的位置：距 seed_world 的 RED（23.76/121.26）約 70 m，落在目標圈內也在抵達容差內
# → 第 2 個 tick 分解器一定會走到「目標區內有沒有敵人」那個判斷。
_BLUE_LAT, _BLUE_LNG = 23.7595, 121.2595
_OBJECTIVE = {"lat": 23.76, "lng": 121.26}


def _now(tick: int) -> SimTime:
    return SimTime(tick=tick, sim_time_ms=tick * 60_000)


class _FakeRedis:
    """只回一個 ai_config 字串的假 client（planner 只用得到 `.get`）。"""

    def __init__(self, payload: str | None = None, *, boom: bool = False) -> None:
        self._payload = payload
        self._boom = boom

    def get(self, _key: str) -> str | None:
        if self._boom:
            raise ConnectionError("測試：Redis 掛了")
        return self._payload


def _seed(session_factory) -> tuple[Any, Any, InMemoryHotState]:  # type: ignore[no-untyped-def]
    """世界 + 熱狀態（藍紅都要在熱狀態裡——感測掃描讀的是熱狀態不是 DB）。"""
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {"lat": _BLUE_LAT, "lng": _BLUE_LNG, "alive": True})
    hot.put_unit(world.red_unit_id, {"lat": 23.76, "lng": 121.26, "alive": True})
    return world, db, hot


def _issue_seize(db, world) -> None:  # type: ignore[no-untyped-def]
    from _order_fakes import FakeGateway

    from app.orders.schemas import OrderRequest, OrderType
    from app.orders.service import OrderService

    OrderService(db, FakeGateway()).submit(
        world.session_id,
        OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.MISSION,
            payload={
                "mission_type": "SEIZE",
                "params": {"objective": _OBJECTIVE, "axis": [], "objective_radius_m": 400},
            },
        ),
        world.blue_issuer_id,
    )


def _run(db, world, hot, *, redis_client: Any = None, ticks: int = 3):  # type: ignore[no-untyped-def]
    from _order_fakes import FakeGateway

    from app.models.tables import Order

    planner = LiveMissionPlanner(
        db, world.session_id, hot, gateway=FakeGateway(), redis_client=redis_client
    )
    for t in range(1, ticks + 1):
        planner.plan(_now(t))
    return (
        db.query(Order)
        .filter(Order.session_id == world.session_id, Order.parent_order_id.isnot(None))
        .all()
    )


def _sweep_once(db, world, hot) -> int:  # type: ignore[no-untyped-def]
    """跑一次**真的**感測掃描，回落庫的 contact 數。

    走 `SensorSweepSystem` 而不是直接 `store.record(...)`：手搭的 IntelContact 只證明
    「這個形狀的資料進得了分解器」，證明不了「引擎真的會產生這種資料」——而後者才是
    這條測試要撐住的事。感測器給滿機率曲線 → 偵測必定成功且 fidelity 為 IDENTIFIED，
    與 rng 無關（測試不能靠擲骰運氣）。
    """
    from app.engine.rng import DeterministicRNG
    from app.intel.sensor import DetectionEnv, SensorProfile
    from app.intel.sensor_system import SensorSweepSystem

    optics = SensorProfile(
        sensor_kind="OPTICAL", max_range_m=5_000.0, detect_curve=((0.0, 1.0), (5_000.0, 1.0))
    )
    factions = {world.blue_unit_id: "BLUE", world.red_unit_id: "RED"}
    system = SensorSweepSystem(
        db=db,
        session_id=world.session_id,
        hot_state=hot,
        rng=DeterministicRNG(1, "sensors"),
        # 只有藍方有眼睛——紅方看得見藍方與否不影響本測試，少一組 contact 少一個變數。
        sensor_for=lambda uid: optics if uid == world.blue_unit_id else None,
        faction_for=lambda uid: factions.get(uid, ""),
        env_for=lambda _o, _t: DetectionEnv(los_clear=True),
        interval_ticks=1,
    )
    events = asyncio.run(system.sweep(_now(1)))
    return len(events)


def test_an_undetected_enemy_never_reaches_the_sub_orders(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**本卡最重要的一條**：RED 沒被 BLUE 偵測到 → SEIZE 不得產生針對 RED 的子令。

    `_world_view` 過去餵 `ground_truth_enemies`，於是任務分解在目標區「看見」了一個
    從未被偵測過的敵人，直接下 ENGAGE。A1 在任務下令這條路上等於沒做。
    """
    world, db, hot = _seed(session_factory)
    _issue_seize(db, world)
    subs = _run(db, world, hot)

    engages = [s for s in subs if s.order_type == "ENGAGE"]
    assert not engages, "對一個沒偵測到的敵人下了 ENGAGE——分解器又在讀 ground truth"
    assert all(world.red_unit_id not in str(s.payload) for s in subs), (
        f"子令載荷裡出現了未偵測單位 {world.red_unit_id}"
    )
    # ⚠ 沒有這一條，上面兩條就是廢的：任務根本沒跑起來也會「沒有 ENGAGE」。
    # POSTURE 只在分解器走到「目標區內無可見敵蹤 → 鞏固」時才產生。
    assert [s for s in subs if s.order_type == "POSTURE"], (
        "任務沒走到接敵判斷點，這條測試什麼都沒測到"
    )
    db.close()


def test_a_contact_the_sensors_really_produced_does_reach_the_sub_orders(session_factory) -> None:  # type: ignore[no-untyped-def]
    """反向控制：偵測到了就**必須**打得到。

    這條是上一條的靠山——沒有它，「不得有 ENGAGE」可以因為任何理由（分解器壞了、
    子令送不出去、目標圈算錯）而恆綠。兩條合起來才釘得住「敵情來源換了、鏈路仍通」。
    """
    world, db, hot = _seed(session_factory)
    assert _sweep_once(db, world, hot) == 1, "感測掃描沒掃到 RED，這條測試的前提不成立"
    _issue_seize(db, world)
    subs = _run(db, world, hot)

    engages = [s for s in subs if s.order_type == "ENGAGE"]
    assert engages, "偵測到的敵人沒有被接戰——迷霧投影把敵情整個吃掉了"
    assert {s.payload.get("target_unit_id") for s in engages} == {world.red_unit_id}, (
        "ENGAGE 綁不到真實單位 id（contact→unit 的對應斷了，令橋接不了）"
    )
    db.close()


def test_the_ground_truth_switch_now_reaches_this_path_too(session_factory) -> None:  # type: ignore[no-untyped-def]
    """`ai_ground_truth=true`（SPEC_V2 WP-D1 的有/無迷霧對照實驗）要管得到任務分解。

    改版前這把開關只掛在 `start_ai_workers` 上：同一局裡 LLM 指揮官走迷霧、
    任務分解器走全知，兩條 AI 路徑對世界的認知不一致，而且沒有任何地方看得出來。
    """
    world, db, hot = _seed(session_factory)
    _issue_seize(db, world)
    subs = _run(db, world, hot, redis_client=_FakeRedis('{"ai_ground_truth": true}'))

    engages = [s for s in subs if s.order_type == "ENGAGE"]
    assert engages, "開了全知開關卻仍看不到敵人——這條路徑沒有讀 ai_config"
    assert {s.payload.get("target_unit_id") for s in engages} == {world.red_unit_id}
    db.close()


def test_an_unreadable_ai_config_falls_back_to_fog_not_to_omniscience(session_factory) -> None:  # type: ignore[no-untyped-def]
    """設定讀不到時要退到「看得比較少」那一側。

    反過來寫的話，一次 Redis 逾時就等於整局偷偷取消迷霧——而畫面上完全看不出來。
    """
    world, db, hot = _seed(session_factory)
    _issue_seize(db, world)
    subs = _run(db, world, hot, redis_client=_FakeRedis(boom=True))
    assert not [s for s in subs if s.order_type == "ENGAGE"], (
        "ai_config 讀取失敗時退回了全知敵情——預設方向反了"
    )
    db.close()
