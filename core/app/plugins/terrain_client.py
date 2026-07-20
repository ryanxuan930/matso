"""Terrain 插件 gRPC 客戶端 + 斷路器 + 健檢監視器（O2.5, SPEC §16.3/§17）。

- **隔離**：每插件獨立 process；gRPC deadline + 斷路器防止插件故障拖垮 Core。
- **健檢預案**：Orchestrator 每 10s 健檢；連續 3 次失敗 → 標記 DOWN → 因 terrain 是物理預檢
  硬依賴，強制 PAUSE 所有 session（`SessionController.pause_all`）。

本模組為執行期基礎設施（非模擬引擎），使用 `time.monotonic` 牆鐘合法（同 app/runtime）；
時鐘/睡眠以參數注入以利確定性測試。
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

import grpc
from matso_sdk import HealthState, from_proto
from matso_sdk._generated import (
    plugin_base_pb2,
    plugin_base_pb2_grpc,
    terrain_pb2,
    terrain_pb2_grpc,
)

from app.errors import TerrainUnavailableError

_LOG = logging.getLogger("app.plugins.terrain")

_DEFAULT_CALL_DEADLINE_S = 0.2  # 領域 RPC；物理預檢 p99<50ms 的裕度內
_DEFAULT_HEALTH_DEADLINE_S = 2.0
_DEFAULT_HEALTH_INTERVAL_S = 10.0  # SPEC §16.3：每 10s
_DEFAULT_HEALTH_THRESHOLD = 3  # SPEC §16.3：連續 3 次失敗 → DOWN
_DEFAULT_BREAKER_THRESHOLD = 5
_DEFAULT_BREAKER_COOLDOWN_S = 5.0


class BreakerState(enum.StrEnum):
    CLOSED = "CLOSED"  # 正常放行
    OPEN = "OPEN"  # 快速失敗（冷卻中）
    HALF_OPEN = "HALF_OPEN"  # 冷卻後試探一次


class CircuitBreaker:
    """連續失敗達門檻 → OPEN 快速失敗；冷卻後 HALF_OPEN 試探，成功即 CLOSED。"""

    def __init__(
        self,
        failure_threshold: int = _DEFAULT_BREAKER_THRESHOLD,
        cooldown_s: float = _DEFAULT_BREAKER_COOLDOWN_S,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_s
        self._now = now
        self._failures = 0
        self._state = BreakerState.CLOSED
        self._opened_at = 0.0

    @property
    def state(self) -> BreakerState:
        return self._state

    def allow(self) -> bool:
        """是否放行本次呼叫。OPEN 冷卻期滿 → 轉 HALF_OPEN 放行一次試探。"""
        if self._state is BreakerState.OPEN and self._now() - self._opened_at >= self._cooldown:
            self._state = BreakerState.HALF_OPEN
        return self._state is not BreakerState.OPEN

    def record_success(self) -> None:
        self._failures = 0
        self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is BreakerState.HALF_OPEN or self._failures >= self._threshold:
            self._state = BreakerState.OPEN
            self._opened_at = self._now()


class SessionController(Protocol):
    """Terrain DOWN 預案的注入介面。真正的 session 暫停於 O3+ 接上。"""

    def pause_all(self, reason: str) -> None: ...
    def resume_all(self, reason: str) -> None: ...


class TerrainClient:
    """Terrain 領域 RPC 的 Core 端封裝：每呼叫加 deadline + 斷路器；失敗轉領域例外。"""

    def __init__(
        self,
        channel: grpc.Channel,
        deadline_s: float = _DEFAULT_CALL_DEADLINE_S,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._stub = terrain_pb2_grpc.TerrainServiceStub(channel)
        self._deadline = deadline_s
        self._breaker = breaker or CircuitBreaker()

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def _guard(self) -> None:
        if not self._breaker.allow():
            raise TerrainUnavailableError("terrain 斷路器開啟（快速失敗）")

    def get_elevation(self, lat: float, lng: float) -> terrain_pb2.GetElevationResponse:
        self._guard()
        req = terrain_pb2.GetElevationRequest(position=terrain_pb2.LatLng(lat=lat, lng=lng))
        return self._invoke(self._stub.GetElevation, req)

    def check_los(
        self,
        obs: tuple[float, float, float],
        tgt: tuple[float, float, float],
    ) -> terrain_pb2.CheckLosResponse:
        self._guard()
        req = terrain_pb2.CheckLosRequest(
            observer=_observer(obs),
            target=_observer(tgt),
        )
        return self._invoke(self._stub.CheckLos, req)

    def get_path(
        self, from_h3: str, to_h3: str, mobility_profile: str
    ) -> terrain_pb2.GetPathResponse:
        self._guard()
        req = terrain_pb2.GetPathRequest(
            from_h3=from_h3, to_h3=to_h3, mobility_profile=mobility_profile
        )
        return self._invoke(self._stub.GetPath, req)

    def _invoke(self, method: Callable[..., object], request: object):  # type: ignore[no-untyped-def]
        try:
            resp = method(request, timeout=self._deadline)
        except grpc.RpcError as exc:
            self._breaker.record_failure()
            code = exc.code() if isinstance(exc, grpc.Call) else None
            raise TerrainUnavailableError(f"terrain RPC 失敗（{code}）") from exc
        self._breaker.record_success()
        return resp


class HealthMonitor:
    """定期健檢 terrain；連續 threshold 次不可達 → DOWN → 觸發 session PAUSE 預案。"""

    def __init__(
        self,
        channel: grpc.Channel,
        controller: SessionController,
        interval_s: float = _DEFAULT_HEALTH_INTERVAL_S,
        failure_threshold: int = _DEFAULT_HEALTH_THRESHOLD,
        deadline_s: float = _DEFAULT_HEALTH_DEADLINE_S,
    ) -> None:
        self._base = plugin_base_pb2_grpc.PluginBaseServiceStub(channel)
        self._controller = controller
        self._interval = interval_s
        self._threshold = failure_threshold
        self._deadline = deadline_s
        self._consecutive_failures = 0
        self._down = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_down(self) -> bool:
        return self._down

    def poll_once(self) -> HealthState:
        """單次健檢。RPC 失敗 → 視為 DOWN（不可達）。回報「本次」觀測狀態。"""
        try:
            resp = self._base.HealthCheck(
                plugin_base_pb2.HealthCheckRequest(), timeout=self._deadline
            )
        except grpc.RpcError:
            return HealthState.DOWN
        return from_proto(resp.state)

    def evaluate(self) -> None:
        """跑一次健檢並更新故障計數 / 觸發預案（供背景迴圈與測試共用）。"""
        state = self.poll_once()
        reachable_healthy = state in (HealthState.HEALTHY, HealthState.DEGRADED)
        if reachable_healthy:
            self._consecutive_failures = 0
            if self._down:
                self._down = False
                _LOG.warning("terrain 恢復（%s）→ resume session", state)
                self._controller.resume_all(f"terrain recovered: {state}")
            return
        # state == DOWN（RPC 失敗或插件自報 DOWN）
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold and not self._down:
            self._down = True
            _LOG.error(
                "terrain 連續 %d 次健檢失敗 → 標記 DOWN，PAUSE 所有 session",
                self._consecutive_failures,
            )
            self._controller.pause_all("terrain DOWN (物理預檢硬依賴)")

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("HealthMonitor 已啟動")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="terrain-health", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.evaluate()
            self._stop.wait(self._interval)


def _observer(triple: tuple[float, float, float]) -> terrain_pb2.Observer:
    lat, lng, agl = triple
    return terrain_pb2.Observer(position=terrain_pb2.LatLng(lat=lat, lng=lng), height_agl_m=agl)
