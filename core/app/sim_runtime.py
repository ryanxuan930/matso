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
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.adjudicator import EngagementAdjudicator, EngageOrderSource
from app.ai_loop.orchestrator import autonomy_config_key, start_ai_workers
from app.ai_loop.victory import resolve_victory_conditions, run_victory_monitor
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
from app.engine.logistics import ResupplySystem
from app.engine.movement import UnitMovementSystem
from app.engine.rng import DeterministicRNG
from app.engine.sensor_wiring import SensorResolver, make_detect_env
from app.engine.subsystems import NoOpTriggerChecker
from app.factions.session_store import load_session_relations
from app.intel.sensor_system import SensorSweepSystem
from app.models import WargameSession
from app.movement.params import MOVE_SPEED_KMH, MOVE_TICK_RATE_MS
from app.movement.terrain_sampler import build_terrain_cell_sampler, build_terrain_path_fn
from app.runtime import PerfCounterClock, TickPacer, run_paced
from app.sim_control import session_concluded_key, session_pause_key, session_restart_key
from app.state.broadcaster import RedisBroadcaster
from app.state.hot_state import RedisHotState
from app.state.ledger import LedgerEvent, LedgerWriter
from app.state.live_ammo import apply_ammo_cmds, drain_ammo_cmds
from app.state.live_position import apply_pos_cmds, drain_pos_cmds
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


def _read_live_tick(client: object, session_id: str) -> int:
    """讀 session 當前 sim tick（廣播器每 tick 寫）；供勝負事件戳記與 time 條件。無值→0。"""
    try:
        raw = client.get(f"session:{session_id}:tick")  # type: ignore[attr-defined]
        return int(raw) if raw is not None else 0
    except (ValueError, TypeError):
        return 0


class SimManager:
    """每 session 一條 Kernel 迴圈；scan 迴圈自動接管新 session。"""

    def __init__(self, *, redis_url: str, scan_interval_s: float = 3.0) -> None:
        self._redis_url = redis_url
        self._factory = default_session_factory()
        self._scan_interval = scan_interval_s
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop = asyncio.Event()
        self._scan_client = make_redis(redis_url)  # 掃描層唯讀（檢查收場旗標）

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
        # 勝負已定的 session（O11.5）：不再起 runner（避免收場後又被掃描重啟）。
        if self._scan_client is not None and self._scan_client.exists(
            session_concluded_key(session_id)
        ):
            return
        self._tasks[session_id] = asyncio.create_task(self._run_session(session_id))

    def _start_victory_monitor(
        self, session_id: str, hot: RedisHotState, client: object, autonomy_raw: str | bytes
    ) -> asyncio.Task[None]:
        """依指派的 victory（或最後存活預設）起勝負監視器；收場時 emit 事件 + 設旗標。"""
        try:
            autonomy = json.loads(autonomy_raw)
        except (ValueError, TypeError):
            autonomy = {}
        factions = list((autonomy.get("factions") or {}).keys())
        vconds = resolve_victory_conditions(autonomy.get("victory"), factions)
        ledger = LedgerWriter(self._factory)
        concluded_key = session_concluded_key(session_id)

        def _on_conclude(winners: list[str], tick: int) -> None:
            ledger.append(
                session_id,
                [
                    LedgerEvent(
                        event_type="SESSION_CONCLUDED",
                        tick=tick,
                        ai_decision={"winners": winners, "source": "victory"},
                    )
                ],
            )
            client.set(concluded_key, "1")  # type: ignore[attr-defined]

        return asyncio.create_task(
            run_victory_monitor(
                session_id=session_id,
                hot=hot,
                db_factory=self._factory,
                victory_conditions=vconds,
                on_conclude=_on_conclude,
                should_stop=self._stop.is_set,
                tick_source=lambda: _read_live_tick(client, session_id),
            )
        )

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
            # #97 偵測：單位→感測器規格/陣營的解析（一次建好快取，sweep 每 tick 查）。
            sensor_resolver = await asyncio.to_thread(SensorResolver, engage_db, session_id)
            # #98 該局的陣營關係矩陣（未宣告→全 HOSTILE，與過去語義相同）。
            relations = await asyncio.to_thread(load_session_relations, engage_db, session_id)
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
                    terrain_sampler=build_terrain_cell_sampler(),  # #81 地形/坡度調速
                    path_fn=build_terrain_path_fn(),  # #82 A* 繞路（不可達→直線）
                ),
                # #97 偵測（取代 NoOp）：每 tick 掃描 → 落 per-faction contacts
                sensors=SensorSweepSystem(
                    db=engage_db,
                    session_id=session_id,
                    hot_state=hot,
                    rng=DeterministicRNG(seed, "sensors"),
                    sensor_for=sensor_resolver.sensor_for,
                    faction_for=sensor_resolver.faction_for,
                    env_for=make_detect_env(_engage_gateway(), _weather_snapshot()),
                    relations=relations,  # #98 盟軍不互相成為 contact
                ),
                comms=CommsSystem(  # #33 通訊子系統（取代 NoOp）：每 5 tick 重算鏈路狀態
                    session_id=session_id,
                    session_factory=self._factory,
                    hot_state=hot,
                ),
                logistics=ResupplySystem(  # #85 補給：RESUPPLY 令加油（取代 NoOp）
                    session_id=session_id,
                    session_factory=self._factory,
                    hot_state=hot,
                ),
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

            async def _apply_live_edits() -> None:
                # 編裝彈藥即時調整（#52）：drain API 排入的命令，以本迴圈自己的 hot 實例套用
                # （同實例→mirror 一致；同行程→不違反 single-writer；tick 之間→不與 tick 內競態）。
                cmds = await asyncio.to_thread(drain_ammo_cmds, client, session_id)
                if cmds:
                    apply_ammo_cmds(hot, cmds)
                # 地圖狀態編輯（拖放單位座標）：同紀律的座標命令通道。暫停中編輯 → RESUME 後 drain。
                pos = await asyncio.to_thread(drain_pos_cmds, client, session_id)
                if pos:
                    apply_pos_cmds(hot, pos)

            # 自主推演（O11.4）：本 session 有 AI 指派（Redis ai_config）且 #54 AI 非 OFF 時，
            # 每個 AI 陣營起一條獨立 async 決策 worker（固定心跳、非 pre_tick → 不阻塞 tick）。
            # 未指派 → 回 []（既有 session 不受影響）。gateway 沿用交戰同源物理閘門。
            ai_tasks: list[asyncio.Task[None]] = []
            ai_gateway = _engage_gateway()
            autonomy_raw = client.get(autonomy_config_key(session_id))
            if ai_gateway is not None and autonomy_raw:
                ai_tasks = start_ai_workers(
                    session_id=session_id,
                    hot=hot,
                    redis_client=client,
                    db_factory=self._factory,
                    gateway=ai_gateway,  # type: ignore[arg-type]
                    should_stop=self._stop.is_set,
                )
                # 勝負監視器（O11.5）：週期評估物理狀態（非 LLM）→ 有勝方 → SESSION_CONCLUDED
                # + 設收場旗標（runner 停、不再重啟）。條件取自指派的 victory，否則最後存活預設。
                ai_tasks.append(self._start_victory_monitor(session_id, hot, client, autonomy_raw))

            concluded_key = session_concluded_key(session_id)
            # runner 重啟旗標（自主 AI 指派只於起跑時讀取）：起跑先清舊旗標，避免立刻自我結束；
            # 迴圈輪詢此鍵 → 存在即結束本迴圈，由掃描層 _ensure 重建 → 重讀指派、起 AI worker。
            restart_key = session_restart_key(session_id)
            with contextlib.suppress(Exception):
                client.delete(restart_key)
            try:
                await run_paced(
                    kernel,
                    pacer,
                    should_stop=lambda: (
                        self._stop.is_set()
                        or bool(client.exists(concluded_key))
                        or bool(client.exists(restart_key))
                    ),
                    should_pause=lambda: bool(client.exists(pause_key)),
                    pre_tick=_apply_live_edits,
                )
            finally:
                for t in ai_tasks:
                    t.cancel()
                for t in ai_tasks:
                    with contextlib.suppress(asyncio.CancelledError):
                        await t
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
