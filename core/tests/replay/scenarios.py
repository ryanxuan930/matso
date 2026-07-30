"""Golden replay 想定註冊表。

現階段（O1.6）子系統多為 no-op；為讓 golden 對邏輯敏感，加入一個確定性示範子系統
RngWalkMovement（整數格點亂步，用 DeterministicRNG）。真實子系統（O3.x）就緒後，
可在此加入「讀 Ledger order 序列」的想定——harness 的 build_kernel 工廠可注入 scripted OrderSource。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from harness import ReplayScenario

from app.engine.clock import SimClock, SimTime
from app.engine.kernel import Kernel
from app.engine.rng import DeterministicRNG
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
from app.state.hot_state import InMemoryHotState
from app.state.ledger import LedgerEvent

MASTER_SEED = 20260718
N_UNITS = 5
# 整數格點四方向；PCG64 整數串跨平台穩定 → hash 不受浮點格式化影響
_MOVES: tuple[tuple[int, int], ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))


class RngWalkMovement:
    """確定性示範：每 tick 用 DeterministicRNG 讓每個單位在整數格點上走一步。

    僅供 golden replay 驗證（相同 seed→相同最終 stateHash）；真實 movement 為 O3.4。
    """

    def __init__(
        self,
        hot_state: InMemoryHotState,
        unit_ids: Sequence[str],
        master_seed: int,
        stream_id: str = "movement",
    ) -> None:
        self._hot = hot_state
        self._ids = list(unit_ids)
        self._rng = DeterministicRNG(master_seed, stream_id)

    async def step(self, now: SimTime) -> list[LedgerEvent]:
        for uid in self._ids:
            cur: dict[str, Any] = self._hot.get_unit(uid) or {"x": 0, "y": 0}
            dx, dy = self._rng.choice(_MOVES)
            self._hot.update_unit(uid, {"x": cur["x"] + dx, "y": cur["y"] + dy})
        return []


def _base_kernel(hot: InMemoryHotState, movement: Any | None = None) -> Kernel:
    return Kernel(
        session_id="replay",
        clock=SimClock(),
        order_source=NoOpOrderSource(),
        adjudicator=NoOpAdjudicator(),
        movement=movement or NoOpMovementSystem(),
        sensors=NoOpSensorSystem(),
        comms=NoOpCommsSystem(),
        logistics=NoOpLogisticsSystem(),
        trigger_checker=NoOpTriggerChecker(),
        broadcaster=NoOpBroadcaster(),
        event_sink=NoOpEventSink(),
        hot_state=hot,
        wall_clock=NullMonotonicClock(),
    )


def _build_empty() -> Kernel:
    return _base_kernel(InMemoryHotState())


def build_rng_walk_kernel(stream_id: str = "movement") -> Kernel:
    hot = InMemoryHotState()
    ids = [f"u{i}" for i in range(N_UNITS)]
    for uid in ids:
        hot.put_unit(uid, {"x": 0, "y": 0})
    return _base_kernel(hot, movement=RngWalkMovement(hot, ids, MASTER_SEED, stream_id))


# --------------------------------------------------------------------------
# Order 指令序列重播（O3.1 / O1.7 R10）：SPEC §3.2「replay 讀 Ledger 指令序列重新執行 →
# 最終 stateHash 一致」的 Phase-1 接入。以固定 ScriptedOrder 序列（代表 Ledger 中記錄的 order
# 序列）驅動 Kernel：LedgerOrderSource 依 tick drain、OrderApplyingAdjudicator 確定性套用。
# 完整「讀 DB Ledger 重播」屬 O8.1；此處證明同一指令序列 → 同一最終狀態的決定性保證。
# --------------------------------------------------------------------------

_ORDER_UNITS = [f"u{i}" for i in range(N_UNITS)]
_ORDER_TICKS = 60


@dataclass(frozen=True, slots=True)
class ScriptedOrder:
    tick: int
    unit_id: str
    dx: int
    dy: int


class LedgerOrderSource:
    """依當前 tick drain 固定指令序列（順序確定）。讀 SimClock 得知 tick。"""

    def __init__(self, clock: SimClock, orders: Sequence[ScriptedOrder]) -> None:
        self._clock = clock
        by_tick: dict[int, list[ScriptedOrder]] = {}
        for order in orders:
            by_tick.setdefault(order.tick, []).append(order)
        self._by_tick = by_tick

    async def drain(self) -> list[ScriptedOrder]:
        return list(self._by_tick.get(self._clock.now().tick, []))


class OrderApplyingAdjudicator:
    """把 MOVE 指令確定性套到 hot_state（位移）並產 ORDER_EXECUTED 事件。純同步。"""

    def __init__(self, hot: InMemoryHotState) -> None:
        self._hot = hot

    def resolve(self, order: ScriptedOrder, now: SimTime) -> list[LedgerEvent]:
        cur: dict[str, Any] = self._hot.get_unit(order.unit_id) or {"x": 0, "y": 0}
        self._hot.update_unit(order.unit_id, {"x": cur["x"] + order.dx, "y": cur["y"] + order.dy})
        return [
            LedgerEvent(
                event_type="ORDER_EXECUTED",
                tick=now.tick,
                initiator_id=order.unit_id,
                ai_decision={"dx": order.dx, "dy": order.dy},
            )
        ]


def _scripted_orders() -> list[ScriptedOrder]:
    """固定（無 RNG、無牆鐘）指令序列：每 tick 每單位一條確定性位移。"""
    orders: list[ScriptedOrder] = []
    for tick in range(_ORDER_TICKS):
        for i, uid in enumerate(_ORDER_UNITS):
            dx = 1 if (tick + i) % 2 == 0 else 0
            dy = 1 if (tick + i) % 3 == 0 else -1
            orders.append(ScriptedOrder(tick=tick, unit_id=uid, dx=dx, dy=dy))
    return orders


def build_order_replay_kernel() -> Kernel:
    hot = InMemoryHotState()
    for uid in _ORDER_UNITS:
        hot.put_unit(uid, {"x": 0, "y": 0})
    clock = SimClock()
    return Kernel(
        session_id="replay",
        clock=clock,
        order_source=LedgerOrderSource(clock, _scripted_orders()),
        adjudicator=OrderApplyingAdjudicator(hot),
        movement=NoOpMovementSystem(),
        sensors=NoOpSensorSystem(),
        comms=NoOpCommsSystem(),
        logistics=NoOpLogisticsSystem(),
        trigger_checker=NoOpTriggerChecker(),
        broadcaster=NoOpBroadcaster(),
        event_sink=NoOpEventSink(),
        hot_state=hot,
        wall_clock=NullMonotonicClock(),
    )


# --------------------------------------------------------------------------
# 壓制與姿態（WP-C1，SPEC_V2 §6 明列須有 golden 案例）。
#
# 這個想定把 C1 的四件事一次跑進 stateHash：面射擊落點抽樣、姿態的**轉換要時間**、
# 壓制在半徑內的累積、以及每 tick 的衰減。全在記憶體（無 DB/Redis/牆鐘）。
#
# 姿態刻意用 DEFENSE（30 tick）而不是 DUG_IN（240 tick）：60 tick 的視窗內收斂得完，
# 於是「未就位仍算前一級」與「就位後才享有防護」兩種行為都進了同一個 hash。
# --------------------------------------------------------------------------

_C1_TICKS = 60
_C1_AIM = (24.0, 121.0)
_C1_ROUNDS = 4
# 前三輪落在姿態就位（tick 30）之前、後三輪之後——轉換那一刻的行為差異才進得了 hash。
_C1_FIRE_TICKS = (5, 10, 15, 35, 40, 45)


def _c1_weapon() -> Any:
    from app.adjudication.weapon import WeaponProfile

    return WeaponProfile.from_base_stats(
        {
            "max_range_m": 20000,
            "ph_by_range_band": [[20000, 0.5]],
            "damage_by_armor_class": {"SOFT": 60.0},
            "pk_by_armor_class": {"SOFT": 0.6},
            "ammo_types": ["HE"],
            "dispersion_cep_m": 100.0,
            "lethal_radius_m": 50.0,
        }
    )


class _C1FireOrders:
    """固定 tick 開火（無 RNG、無牆鐘）。"""

    def __init__(self, clock: SimClock) -> None:
        self._clock = clock

    async def drain(self) -> list[int]:
        tick = self._clock.now().tick
        return [tick] if tick in _C1_FIRE_TICKS else []


class _C1FireAdjudicator:
    """一次面射擊：`resolve_area_fire` → 扣戰力 → `apply_area_suppression`。

    走的是與活執行期 `AreaFireAdjudicator` 相同的純函數與接線函式，只是不碰 DB。
    """

    def __init__(self, hot: InMemoryHotState, rng: DeterministicRNG) -> None:
        self._hot = hot
        self._rng = rng

    def resolve(self, _order: int, now: SimTime) -> list[LedgerEvent]:
        from app.adjudication.area_fire import AreaTarget, resolve_area_fire
        from app.engine.suppression_wiring import POSTURE_KEY, apply_area_suppression

        state = self._hot.get_unit("INF") or {}
        target = AreaTarget(
            unit_id="INF",
            faction="RED",
            lat=float(state["lat"]),
            lng=float(state["lng"]),
            armor_class="SOFT",
            current_strength=float(state["strength"]),
            authorized_strength=120.0,
            platform_count=120,
            posture=str(state.get(POSTURE_KEY) or "MOVING"),
        )
        result = resolve_area_fire(
            _c1_weapon(),
            _C1_AIM,
            [target],
            self._rng,
            now.tick,
            shooter_id="GUN",
            shooter_faction="BLUE",
            rounds=_C1_ROUNDS,
        )
        loss = result.losses.get("INF", 0.0)
        if loss:
            self._hot.update_unit("INF", {"strength": target.current_strength - loss})
        apply_area_suppression(self._hot, result.suppressed, "ARTILLERY")
        return [result.event] if result.event is not None else []


class _C1Decay:
    """每 tick 的衰減與姿態收斂（活執行期跑在 pre_tick，這裡掛 movement 槽）。"""

    def __init__(self, hot: InMemoryHotState) -> None:
        self._hot = hot

    async def step(self, now: SimTime) -> list[LedgerEvent]:
        from app.engine.suppression_wiring import tick_suppression

        tick_suppression(self._hot, now.tick)
        return []


def build_suppression_defense_kernel() -> Kernel:
    from app.adjudication.suppression import Posture
    from app.engine.suppression_wiring import set_posture

    hot = InMemoryHotState()
    hot.put_unit("GUN", {"lat": 24.1, "lng": 121.0, "strength": 6.0})
    hot.put_unit("INF", {"lat": _C1_AIM[0], "lng": _C1_AIM[1], "strength": 120.0})
    set_posture(hot, "INF", Posture.DEFENSE, tick=0)  # tick 30 才就位
    clock = SimClock()
    return Kernel(
        session_id="replay",
        clock=clock,
        order_source=_C1FireOrders(clock),
        adjudicator=_C1FireAdjudicator(hot, DeterministicRNG(MASTER_SEED, "area_fire")),
        movement=_C1Decay(hot),
        sensors=NoOpSensorSystem(),
        comms=NoOpCommsSystem(),
        logistics=NoOpLogisticsSystem(),
        trigger_checker=NoOpTriggerChecker(),
        broadcaster=NoOpBroadcaster(),
        event_sink=NoOpEventSink(),
        hot_state=hot,
        wall_clock=NullMonotonicClock(),
    )


# --------------------------------------------------------------------------
# 任務級下令（WP-A2）。SPEC_V2 原本寫「golden：重錄（新增令型）」——**開工前查證後推翻**：
# 上面四個案例都是手搭的純記憶體 Kernel，不碰 `OrderType`、不碰 DB、不走 `sim_runtime`，
# 新增令型改不到它們的雜湊。改採 WP-C1 用過的做法：**新增一個案例**，
# 讓任務分解有自己的漂移偵測，同時讓那四個雜湊維持成未被污染的歷史基準。
#
# 本案例把分解器與任務執行期一起跑進 stateHash：一個連沿軸線奪佔一個目標，
# 途中在目標圈內遇敵（接戰）、清空後鞏固轉守。
# --------------------------------------------------------------------------

_A2_TICKS = 60
_A2_OBJ = {"lat": 24.02, "lng": 121.0}


class _A2MissionPlanner:
    """把 `mission_runtime.evaluate` 的子令直接套進熱狀態。

    活執行期會把子令送進 `OrderService.submit`（要 DB）；golden 是純記憶體，
    故此處以最小的方式落實子令的效果——**分解與階段轉移的邏輯完全共用生產程式**，
    被釘住的正是那一段。
    """

    def __init__(self, hot: InMemoryHotState) -> None:
        from app.orders.mission import MissionPayload, MissionType
        from app.orders.mission_runtime import ActiveMission, MissionMemory

        self._hot = hot
        self._memory = MissionMemory()
        self._dest: tuple[float, float] | None = None
        self._missions = [
            ActiveMission(
                order_id="m-seize",
                unit_id="INF",
                faction="BLUE",
                payload=MissionPayload(
                    mission_type=MissionType.SEIZE,
                    params={
                        "objective": _A2_OBJ,
                        "axis": [{"lat": 24.01, "lng": 121.0}],
                        "objective_radius_m": 400,
                    },
                ),
            )
        ]

    def _world_view(self, faction: str) -> dict[str, Any]:
        own = dict(self._hot.get_unit("INF") or {})
        own["unit_id"] = "INF"
        enemy = self._hot.get_unit("RED") or {}
        enemies = (
            [{"unit_id": "RED", "lat": enemy.get("lat"), "lng": enemy.get("lng")}]
            if enemy.get("alive")
            else []
        )
        return {"own_units": [own], "known_enemies": enemies}

    def plan(self, now: SimTime) -> list[LedgerEvent]:
        from app.orders.mission_runtime import evaluate

        to_submit, events = evaluate(self._missions, self._memory, self._world_view, now)
        for _mission, orders in to_submit:
            for order in orders:
                self._accept(order)
        self._advance()  # 常駐 MOVE 令每 tick 推進（真實的 movement 子系統就是這樣）
        self._record_phase(now)
        return events

    def _record_phase(self, now: SimTime) -> None:
        """把**各階段首次進入的 tick** 寫進熱狀態，讓 stateHash 蓋到任務的時間軸。

        ⚠ 第一版只靠終狀態（位置 + 姿態）——**那個 golden 抓不到漂移**：
        移動是漸近收斂的，跑滿 60 tick 之後不論容差多少，終點都一樣。
        實測把 `ARRIVAL_TOLERANCE_M` 120→300 之後雜湊**完全沒變**，
        等於那個 hash 檔什麼都沒釘住。要釘的是「任務照這個節奏走過這些階段」，
        所以記的是首次進入時間而不是當下階段（後者也會被終狀態吃掉）。
        """
        state = self._memory.states.get("m-seize")
        if state is None:
            return
        seen = dict(self._hot.get_unit("MISSION") or {})
        key = state.phase.value
        if key not in seen:
            self._hot.update_unit("MISSION", {key: now.tick})

    def _accept(self, order: Any) -> None:
        """收下一道子令。

        ⚠ **MOVE 是常駐令**：分解器在「還在路上」時刻意不重下令（真實系統靠既有的
        VALIDATED/EXECUTING 令持續推進）。第一版的這個假模型只在收令的那一 tick 移動一次，
        於是單位走了 40% 就停住、任務永遠到不了目標——**是實測 60 tick 後的終狀態發現的**，
        不是推理出來的。
        """
        if order.order_type == "MOVE":
            self._dest = (order.payload["to_lat"], order.payload["to_lng"])
        elif order.order_type == "ENGAGE":
            self._hot.update_unit("RED", {"alive": False})
        elif order.order_type == "POSTURE":
            self._hot.update_unit("INF", {"posture": order.payload["posture"]})

    def _advance(self) -> None:
        """朝常駐目的地走一步：每 tick 縮短 40% 距離（確定性，不抽隨機）。"""
        if self._dest is None:
            return
        unit = dict(self._hot.get_unit("INF") or {})
        lat, lng = float(unit.get("lat", 0.0)), float(unit.get("lng", 0.0))
        self._hot.update_unit(
            "INF",
            {
                "lat": round(lat + (self._dest[0] - lat) * 0.4, 9),
                "lng": round(lng + (self._dest[1] - lng) * 0.4, 9),
            },
        )


def build_mission_seize_kernel() -> Kernel:
    hot = InMemoryHotState()
    hot.put_unit("INF", {"lat": 24.0, "lng": 121.0, "posture": "MOVING"})
    hot.put_unit("RED", {"lat": 24.021, "lng": 121.0, "alive": True})
    return Kernel(
        session_id="replay",
        clock=SimClock(),
        order_source=NoOpOrderSource(),
        adjudicator=NoOpAdjudicator(),
        movement=NoOpMovementSystem(),
        sensors=NoOpSensorSystem(),
        comms=NoOpCommsSystem(),
        logistics=NoOpLogisticsSystem(),
        trigger_checker=NoOpTriggerChecker(),
        mission_planner=_A2MissionPlanner(hot),
        broadcaster=NoOpBroadcaster(),
        event_sink=NoOpEventSink(),
        hot_state=hot,
        wall_clock=NullMonotonicClock(),
    )


SCENARIOS: dict[str, ReplayScenario] = {
    "empty_100": ReplayScenario("empty_100", 100, _build_empty),
    "rng_walk_100": ReplayScenario("rng_walk_100", 100, build_rng_walk_kernel),
    "order_replay_60": ReplayScenario("order_replay_60", _ORDER_TICKS, build_order_replay_kernel),
    "suppression_defense_60": ReplayScenario(
        "suppression_defense_60", _C1_TICKS, build_suppression_defense_kernel
    ),
    "mission_seize_60": ReplayScenario("mission_seize_60", _A2_TICKS, build_mission_seize_kernel),
}
