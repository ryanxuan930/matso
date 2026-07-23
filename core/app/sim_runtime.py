"""Session Kernel 執行期管理（O10.1）——讓 MOVE 指令實際執行、單位移動、STATE_DIFF 廣播。

FastAPI lifespan 啟動 `SimManager.run()`：定期掃描 session，為每個尚無 runner 者起一條 Kernel
背景迴圈。最小可玩版只接 movement 子系統（其餘 no-op）；tick 以 `SimClock` 決定性推進，牆鐘節奏
由 `TickPacer` 控制。Kernel 仍是熱狀態/Ledger 的唯一寫入者（紅線）。

節奏：sim 每 tick = 1 分（`_TICK_RATE_MS`）；真實節奏 `compression` → 約 0.5s/tick，
單位以 `speed_kmh` 可見地朝目標移動。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.adjudicator import EngagementAdjudicator, EngageOrderSource
from app.cache import make_redis
from app.db import default_session_factory
from app.engine.clock import SimClock
from app.engine.comms import CommsSystem
from app.engine.engage_wiring import (
    WeaponResolver,
    make_combined_weapons_for,
    make_engage_env,
    seed_combat_state,
)
from app.engine.kernel import Kernel
from app.engine.movement import UnitMovementSystem
from app.engine.rng import DeterministicRNG
from app.engine.subsystems import (
    NoOpLogisticsSystem,
    NoOpSensorSystem,
    NoOpTriggerChecker,
)
from app.models import WargameSession
from app.movement.params import MOVE_SPEED_KMH, MOVE_TICK_RATE_MS
from app.runtime import PerfCounterClock, TickPacer, run_paced
from app.sim_control import session_pause_key
from app.state.broadcaster import RedisBroadcaster
from app.state.hot_state import RedisHotState
from app.state.ledger import LedgerWriter
from app.weather import WeatherState

_LOG = logging.getLogger("app.sim")

# 與移動預覽端（api/movement）共用單一真相，確保估計與實跑一致。
_TICK_RATE_MS = MOVE_TICK_RATE_MS  # sim time：1 分 / tick
_PACE_COMPRESSION = 120.0  # 真實節奏：60000/1000/120 = 0.5s / tick
_UNIT_SPEED_KMH = MOVE_SPEED_KMH


def _engage_gateway() -> object | None:
    """交戰地形 LOS 用的物理 gateway（Phase 3）——與 submit 端同源（STUB_GATEWAY 時許可式）。

    失敗（無 grpc/服務未起）→ None，make_engage_env 退回 los_clear=True（不阻斷活模擬啟動）。
    """
    try:
        from app.api.deps import get_gateway

        return get_gateway()
    except Exception:
        _LOG.warning("交戰 gateway 建立失敗，LOS 退回可見")
        return None


def _weather_snapshot() -> WeatherState | None:
    """交戰天氣修正用的 WeatherState 快照（Phase 3 STEP2）——session 啟動時取一次（決定性）。

    失敗（無 grpc/服務未起）→ None，make_engage_env 天氣修正退回 1.0（晴天，不阻斷活模擬）。
    v0：整局用啟動快照；逐 weather-tick 刷新列為後續（PROGRESS Backlog）。
    """
    try:
        import grpc

        from app.config import Settings
        from app.plugins.weather_client import WeatherClient

        channel = grpc.insecure_channel(Settings().weather_grpc_target)
        return WeatherClient(channel).fetch_state(0)  # 失敗 → WeatherState.clear()
    except Exception:
        _LOG.warning("交戰 weather 快照建立失敗，天氣修正退回晴天")
        return None


class SimManager:
    """每 session 一條 Kernel 迴圈；scan 迴圈自動接管新 session。"""

    def __init__(self, *, redis_url: str, scan_interval_s: float = 3.0) -> None:
        self._redis_url = redis_url
        self._factory = default_session_factory()
        self._scan_interval = scan_interval_s
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """掃描迴圈：直到 stop() 前，定期為每個 session 確保有 runner。"""
        self._stop.clear()
        _LOG.info("SimManager 啟動（sim tick=%dms）", _TICK_RATE_MS)
        while not self._stop.is_set():
            try:
                for sid in await asyncio.to_thread(self._session_ids):
                    self._ensure(sid)
            except Exception:
                _LOG.exception("session 掃描失敗")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._scan_interval)

    def _session_ids(self) -> list[str]:
        # #31 已封存的推演凍結：不起 Kernel 迴圈（活模擬停擺，移入歷史頁）。
        with self._factory() as db:
            return [
                s.id
                for s in db.execute(
                    select(WargameSession).where(WargameSession.archived_at.is_(None))
                )
                .scalars()
                .all()
            ]

    def _ensure(self, session_id: str) -> None:
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            return
        self._tasks[session_id] = asyncio.create_task(self._run_session(session_id))

    async def _run_session(self, session_id: str) -> None:
        # 交戰裁決需一條長生命期 DB session（O3.6 接線層讀寫 Order + 每 tick commit 以刷新快照）。
        engage_db = self._factory()
        try:
            client = make_redis(self._redis_url)
            hot = RedisHotState(client, session_id)
            # 交戰接線（新 #1）：建武器解析器 + 播戰鬥狀態（血量/裝甲/彈藥/座標）入熱狀態。
            resolver, seed = await asyncio.to_thread(
                self._prepare_engage, engage_db, session_id, hot
            )
            sim_clock = SimClock(tick_rate_ms=_TICK_RATE_MS)
            kernel = Kernel(
                session_id=session_id,
                clock=sim_clock,
                # #33b 通信閘門：OFFLINE/DEGRADED 時 ENGAGE 延後送達（傳 hot + 同一 clock）。
                order_source=EngageOrderSource(engage_db, session_id, hot, sim_clock),
                adjudicator=EngagementAdjudicator(
                    engage_db,
                    hot,
                    DeterministicRNG(seed, "adjudication"),
                    resolver.weapon_for,
                    make_engage_env(hot, _engage_gateway(), _weather_snapshot()),
                    quantity_for=resolver.quantity_for,  # #30 squad 齊射
                    # SPEC_EXTEND P2 聯合兵種：≥2 武器系統 → 武器組合加總（帶熱狀態活彈藥）。
                    combined_weapons_for=make_combined_weapons_for(resolver, hot),
                ),
                movement=UnitMovementSystem(
                    session_id=session_id,
                    session_factory=self._factory,
                    hot_state=hot,
                    tick_rate_ms=_TICK_RATE_MS,
                    speed_kmh=_UNIT_SPEED_KMH,
                    rng=DeterministicRNG(seed, "movement"),  # #28 強穿隨機耗損
                ),
                sensors=NoOpSensorSystem(),
                comms=CommsSystem(  # #33 通訊子系統（取代 NoOp）：每 5 tick 重算鏈路狀態
                    session_id=session_id,
                    session_factory=self._factory,
                    hot_state=hot,
                ),
                logistics=NoOpLogisticsSystem(),
                trigger_checker=NoOpTriggerChecker(),
                broadcaster=RedisBroadcaster(client, session_id),
                event_sink=LedgerWriter(self._factory),
                hot_state=hot,
                wall_clock=PerfCounterClock(),
            )
            pacer = TickPacer(_TICK_RATE_MS, compression=_PACE_COMPRESSION)
            # White Cell 暫停旗標（新 #6）：control 端點 PAUSE 設 Redis 鍵、RESUME 清除；
            # 迴圈輪詢此鍵 → 暫停時凍結活模擬。
            pause_key = session_pause_key(session_id)
            await run_paced(
                kernel,
                pacer,
                should_stop=self._stop.is_set,
                should_pause=lambda: bool(client.exists(pause_key)),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("session %s Kernel 迴圈崩潰", session_id)
        finally:
            engage_db.close()

    def _prepare_engage(
        self, db: Session, session_id: str, hot: RedisHotState
    ) -> tuple[WeaponResolver, int]:
        """（執行緒中）建武器解析器 + 播戰鬥狀態；回 (resolver, master_seed)。皆區域值並行安全。"""
        resolver = WeaponResolver(db, session_id)
        seed_combat_state(db, hot, session_id, resolver)
        row = db.get(WargameSession, session_id)
        return resolver, (int(row.master_seed) if row is not None else 0)

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
