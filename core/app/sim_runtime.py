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

from app.cache import make_redis
from app.db import default_session_factory
from app.engine.clock import SimClock
from app.engine.kernel import Kernel
from app.engine.movement import UnitMovementSystem
from app.engine.subsystems import (
    NoOpAdjudicator,
    NoOpCommsSystem,
    NoOpLogisticsSystem,
    NoOpOrderSource,
    NoOpSensorSystem,
    NoOpTriggerChecker,
)
from app.models import WargameSession
from app.runtime import PerfCounterClock, TickPacer, run_paced
from app.state.broadcaster import RedisBroadcaster
from app.state.hot_state import RedisHotState
from app.state.ledger import LedgerWriter

_LOG = logging.getLogger("app.sim")

_TICK_RATE_MS = 60_000  # sim time：1 分 / tick
_PACE_COMPRESSION = 120.0  # 真實節奏：60000/1000/120 = 0.5s / tick
_UNIT_SPEED_KMH = 40.0


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
        with self._factory() as db:
            return [s.id for s in db.execute(select(WargameSession)).scalars().all()]

    def _ensure(self, session_id: str) -> None:
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            return
        self._tasks[session_id] = asyncio.create_task(self._run_session(session_id))

    async def _run_session(self, session_id: str) -> None:
        try:
            client = make_redis(self._redis_url)
            hot = RedisHotState(client, session_id)
            kernel = Kernel(
                session_id=session_id,
                clock=SimClock(tick_rate_ms=_TICK_RATE_MS),
                order_source=NoOpOrderSource(),
                adjudicator=NoOpAdjudicator(),
                movement=UnitMovementSystem(
                    session_id=session_id,
                    session_factory=self._factory,
                    hot_state=hot,
                    tick_rate_ms=_TICK_RATE_MS,
                    speed_kmh=_UNIT_SPEED_KMH,
                ),
                sensors=NoOpSensorSystem(),
                comms=NoOpCommsSystem(),
                logistics=NoOpLogisticsSystem(),
                trigger_checker=NoOpTriggerChecker(),
                broadcaster=RedisBroadcaster(client, session_id),
                event_sink=LedgerWriter(self._factory),
                hot_state=hot,
                wall_clock=PerfCounterClock(),
            )
            pacer = TickPacer(_TICK_RATE_MS, compression=_PACE_COMPRESSION)
            await run_paced(kernel, pacer, should_stop=self._stop.is_set)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("session %s Kernel 迴圈崩潰", session_id)

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
