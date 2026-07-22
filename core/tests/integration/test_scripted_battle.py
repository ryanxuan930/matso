"""腳本對戰驗收（O3.6，M3 的 DoD）——此測試綠 = O3 里程碑完成。

純 API 驅動（OrderService/IntelService）+ 真 Kernel 組裝，把 O3.1–O3.5 全接起來：
藍軍移動（O3.4）→ 紅軍偵測到（O3.3）→ 交戰（O3.2）→ 戰損入帳（Ledger）
→ 雙方 intel 視圖各自正確（fog of war）。

以本地 SQLite + 注入的 terrain/weapon/sensor 假件跑（不需 compose/gRPC），故於 CI python job
常駐執行（DoD 必須每次都跑）。物理裁決仍是純函數；假件只提供 LOS/射程/天氣係數（真實由
Kernel 經 terrain client 收集）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import h3
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.adjudication.adjudicator import EngagementAdjudicator, EngageOrderSource
from app.adjudication.engagement import EnvSnapshot
from app.adjudication.weapon import WeaponProfile
from app.engine.clock import SimClock
from app.engine.kernel import Kernel
from app.engine.rng import DeterministicRNG
from app.engine.subsystems import (
    NoOpBroadcaster,
    NoOpCommsSystem,
    NoOpLogisticsSystem,
    NoOpTriggerChecker,
    NullMonotonicClock,
)
from app.intel.seed_sensors import SEED_SENSORS
from app.intel.sensor import DetectionEnv, SensorProfile
from app.intel.sensor_system import SensorSweepSystem
from app.intel.service import IntelService
from app.models import Base
from app.models.enums import IntelFidelity, UnitLevel, UserRole
from app.models.tables import SessionParticipant, TacticalUnit, User, WargameSession
from app.movement.db_store import DbOrderStore
from app.movement.system import MovementSystem
from app.orders.precheck import LosOutcome
from app.orders.schemas import OrderRequest, OrderType
from app.orders.service import OrderService
from app.state.hot_state import InMemoryHotState
from app.state.ledger import LedgerEvent

# 幾何：藍起點、紅陣地（~2.7km，EO_DAY 4km 內、測試武器 5km 內）
_BLUE_LL = (23.75, 121.25)
_RED_LL = (23.77, 121.27)
_BLUE_H3 = h3.latlng_to_cell(*_BLUE_LL, 8)
_RED_H3 = h3.latlng_to_cell(*_RED_LL, 8)
_BLUE_PATH = h3.grid_path_cells(_BLUE_H3, _RED_H3)[:-1]  # 朝紅前進，停在紅前一格

# 測試武器：ph=1.0（必中，讓 DoD 的傷害鏈確定性通過；命中率真實性由 O3.2 property 驗）
_WEAPON = WeaponProfile.from_base_stats(
    {
        "max_range_m": 5000,
        "ph_by_range_band": [[100, 1.0], [5000, 1.0]],
        "damage_by_armor_class": {"INFANTRY": 40},
        "ammo_types": ["X"],
    }
)
_SENSOR = SensorProfile.from_base_stats(SEED_SENSORS["EO_DAY"])
_EARTH_R_M = 6_371_000.0


class CapturingSink:
    """in-memory EventSink，供斷言「戰損入帳」。"""

    def __init__(self) -> None:
        self.events: list[LedgerEvent] = []
        self._seq = -1

    def append(self, session_id: str, events: Sequence[LedgerEvent]) -> list[str]:
        ids = []
        for e in events:
            self._seq += 1
            self.events.append(e)
            ids.append(str(self._seq))
        return ids

    def tip_seq(self, session_id: str) -> int:
        return self._seq


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin(math.radians(lat2 - lat1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * _EARTH_R_M * math.asin(math.sqrt(a))


def _seed(db: Session) -> tuple[str, str, str, str]:
    """建 session + 藍/紅 PLATOON + 藍方 COMMANDER。回 (session, blue, red, blue_issuer)。"""
    session = WargameSession(name="battle", master_seed=7, current_weather={})
    db.add(session)
    db.flush()
    blue = TacticalUnit(
        session_id=session.id,
        designation="B-CO",
        unit_level=UnitLevel.PLATOON,
        faction="BLUE",
        current_lat=_BLUE_LL[0],
        current_lng=_BLUE_LL[1],
    )
    red = TacticalUnit(
        session_id=session.id,
        designation="R-CO",
        unit_level=UnitLevel.PLATOON,
        faction="RED",
        current_lat=_RED_LL[0],
        current_lng=_RED_LL[1],
    )
    user = User(username="cmdr", password_hash="x", role=UserRole.COMMANDER)
    db.add_all([blue, red, user])
    db.flush()
    issuer = SessionParticipant(
        user_id=user.id,
        session_id=session.id,
        faction="BLUE",
        role=UserRole.COMMANDER,
        unit_scope={},
    )
    db.add(issuer)
    db.commit()
    return session.id, blue.id, red.id, issuer.id


class _FakeGateway:  # PhysicsGateway（precheck 用）：路徑可達、有視線
    def path_reachable(self, from_h3: str, to_h3: str, mobility_profile: str) -> tuple[bool, str]:
        return True, "ok"

    def has_los(
        self, observer: tuple[float, float, float], target: tuple[float, float, float]
    ) -> LosOutcome:
        return LosOutcome(True, 100.0)


class _FixedPlanner:
    def plan(self, from_h3: str, to_h3: str, mobility_profile: str) -> list[str]:
        return list(_BLUE_PATH)


async def test_scripted_battle_full_flow() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    session_id, blue_id, red_id, issuer_id = _seed(db)

    # 熱狀態：藍（有彈藥）在起點、紅（滿血）在陣地
    hot = InMemoryHotState()
    hot.put_unit(blue_id, {"h3": _BLUE_H3, "lat": _BLUE_LL[0], "lng": _BLUE_LL[1], "ammo": 10})
    hot.put_unit(
        red_id,
        {
            "h3": _RED_H3,
            "lat": _RED_LL[0],
            "lng": _RED_LL[1],
            "health": 100.0,
            "armor_class": "INFANTRY",
        },
    )

    faction = {blue_id: "BLUE", red_id: "RED"}

    def engage_env(shooter_id: str, target_id: str) -> EnvSnapshot:
        s, t = hot.get_unit(shooter_id), hot.get_unit(target_id)
        assert s is not None and t is not None
        return EnvSnapshot(
            range_m=_haversine_m(s["lat"], s["lng"], t["lat"], t["lng"]), los_clear=True
        )

    sink = CapturingSink()
    kernel = Kernel(
        session_id=session_id,
        clock=SimClock(),
        order_source=EngageOrderSource(db, session_id),
        adjudicator=EngagementAdjudicator(
            db, hot, DeterministicRNG(7, "adjudication"), lambda _cmd: _WEAPON, engage_env
        ),
        movement=MovementSystem(session_id, hot, DbOrderStore(db), _FixedPlanner(), speed_hexes=1),
        sensors=SensorSweepSystem(
            db,
            session_id,
            hot,
            DeterministicRNG(7, "sensors"),
            lambda _u: _SENSOR,
            faction.__getitem__,
            lambda _o, _t: DetectionEnv(los_clear=True),
        ),
        comms=NoOpCommsSystem(),
        logistics=NoOpLogisticsSystem(),
        trigger_checker=NoOpTriggerChecker(),
        broadcaster=NoOpBroadcaster(),
        event_sink=sink,
        hot_state=hot,
        wall_clock=NullMonotonicClock(),
    )

    orders = OrderService(db, _FakeGateway())

    # ---- 藍軍移動（API 下 MOVE 令）----
    move = orders.submit(
        session_id,
        OrderRequest(
            unit_id=blue_id,
            order_type=OrderType.MOVE,
            payload={"to_h3": _BLUE_PATH[-1], "mobility_profile": "FOOT"},
        ),
        issuer_id,
    )
    assert move.status.value == "VALIDATED"

    for _ in range(len(_BLUE_PATH) + 1):  # 逐 tick 推進 + 偵測
        await kernel.run_tick()

    # 藍軍確實移動了（位置已非起點）
    assert hot.get_unit(blue_id)["h3"] != _BLUE_H3

    # 紅軍偵測到藍軍；藍軍偵測到紅軍（雙方 intel 視圖各自成立）
    red_view = IntelService(db).visible_contacts(session_id, "RED")
    blue_view = IntelService(db).visible_contacts(session_id, "BLUE")
    assert len(red_view) >= 1  # 紅方看到（藍）敵情
    assert len(blue_view) >= 1  # 藍方看到（紅）敵情

    # ---- 交戰（API 下 ENGAGE 令）----
    engage = orders.submit(
        session_id,
        OrderRequest(
            unit_id=blue_id,
            order_type=OrderType.ENGAGE,
            payload={"target_unit_id": red_id},
        ),
        issuer_id,
    )
    assert engage.status.value == "VALIDATED"

    for _ in range(2):
        await kernel.run_tick()

    # 戰損入帳：紅軍血量下降 + ENGAGEMENT_RESOLVED 事件寫入 Ledger
    assert hot.get_unit(red_id)["health"] == pytest.approx(60.0)  # 100 − 40
    # 戰損也持久化到 DB healthStatus（GET /units 權威、重連/重啟保留）
    assert db.get(TacticalUnit, red_id).health_status == pytest.approx(60.0)  # type: ignore[union-attr]
    resolved = [e for e in sink.events if e.event_type == "ENGAGEMENT_RESOLVED"]
    assert resolved and resolved[0].damage_calc == pytest.approx(40.0)
    assert resolved[0].target_id == red_id

    # ENGAGE order 落庫 COMPLETED
    from app.models.tables import Order

    assert db.get(Order, engage.id).status.value == "COMPLETED"  # type: ignore[union-attr]

    # fog of war 隔離：紅方視圖拿不到 BLUE ground truth（無 target_unit_id、DETECTED 去識別化）
    for view in red_view:
        assert "target_unit_id" not in view.model_dump()
        if view.fidelity is IntelFidelity.DETECTED:
            assert view.designation is None

    db.close()
    engine.dispose()
