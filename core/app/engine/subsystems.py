"""Kernel 的子系統介面（Protocol）與 no-op stub（SPEC_FULL §3.3）。

O1.3 只定義 tick loop 需要的介面並提供 no-op 實作；真實實作於後續里程碑：
- Adjudicator → O3.2/O3.3（純同步裁決）
- MovementSystem → O3.4、SensorSystem → O3.3、CommsSystem → O5.4、LogisticsSystem → O5/O8
- Broadcaster → O1.4（Redis diff）/ O4.3（WebSocket）
- OrderSource → O3.1（pending order queue）

所有會產生事件的子系統統一回傳 `list[LedgerEvent]`，由 Kernel 收集後單批寫入 Ledger。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from app.engine.clock import SimTime
from app.state.hot_state import SessionDiff
from app.state.ledger import LedgerEvent


@runtime_checkable
class MonotonicClock(Protocol):
    """單調牆鐘（奈秒），僅供 tick 效能量測（TICK_OVERRUN 判定）。

    刻意與 SimClock 分離：這是真實時間、非決定性，不參與任何模擬計算。
    真實實作見 core/app/runtime.py（engine 外，維持 engine 無牆鐘依賴）；
    replay/測試注入 NullMonotonicClock 以維持可重現性。
    """

    def now_ns(self) -> int: ...


@runtime_checkable
class OrderSource(Protocol):
    """本 tick 待處理指令的來源（O3.1 實作 pending queue）。drain 順序必須確定。"""

    async def drain(self) -> list[Any]: ...


@runtime_checkable
class Adjudicator(Protocol):
    """確定性裁決（純同步；O3.2/O3.3）。輸入單一 order，回傳其產生的事件。"""

    def resolve(self, order: Any, now: SimTime) -> list[LedgerEvent]: ...


@runtime_checkable
class MovementSystem(Protocol):
    async def step(self, now: SimTime) -> list[LedgerEvent]: ...


@runtime_checkable
class SensorSystem(Protocol):
    async def sweep(self, now: SimTime) -> list[LedgerEvent]: ...


@runtime_checkable
class CommsSystem(Protocol):
    async def evaluate(self, now: SimTime) -> list[LedgerEvent]: ...


@runtime_checkable
class LogisticsSystem(Protocol):
    async def consume(self, now: SimTime) -> list[LedgerEvent]: ...


@runtime_checkable
class TriggerChecker(Protocol):
    """MSEL 觸發器與勝利條件檢查（O7.2）。同步、in-memory。"""

    def check(self, now: SimTime) -> list[LedgerEvent]: ...


@runtime_checkable
class Broadcaster(Protocol):
    """增量狀態推播（O1.4 Redis diff / O4.3 WebSocket）。diff 為本 tick 的 per-unit 變動欄位。"""

    async def publish(self, tick: int, diff: SessionDiff) -> None: ...
    async def publish_events(self, events: Sequence[LedgerEvent]) -> None:
        """把本 tick 的裁決事件推到 WS 事件流（戰況事件 feed）。預設 no-op；Redis 實作覆寫。"""
        return None


@runtime_checkable
class EventSink(Protocol):
    """事件寫入端。app.state.ledger.LedgerWriter 即滿足此介面。

    tip_seq：目前鏈尾 seq（空 session 為 -1），供 checkpoint 錨定 ledgerSeq（O1.7/R3）。
    """

    def append(self, session_id: str, events: Sequence[LedgerEvent]) -> list[str]: ...
    def tip_seq(self, session_id: str) -> int: ...


# --------------------------------------------------------------------------
# no-op stubs：讓 Kernel 能以全 no-op 依賴建構並空跑（供 O1.6 空想定 golden replay）
# --------------------------------------------------------------------------


class NullMonotonicClock:
    """永遠回 0 → 量測 elapsed 恆為 0 → 永不 overrun。用於確定性 replay 與測試。"""

    def now_ns(self) -> int:
        return 0


class NoOpOrderSource:
    async def drain(self) -> list[Any]:
        return []


class NoOpAdjudicator:
    def resolve(self, order: Any, now: SimTime) -> list[LedgerEvent]:
        return []


# --------------------------------------------------------------------------
# 組合器：Kernel 只有一個 order_source / 一個 adjudicator，但令型不只一種
# --------------------------------------------------------------------------


class ChainedOrderSource:
    """依序 drain 多個來源，串成一條序列（WP-C10.2：ENGAGE 之外還有 FIRE_MISSION）。

    順序即建構順序、各來源自身已是確定性排序 → 整串確定性，golden replay 不受影響。
    """

    def __init__(self, *sources: OrderSource) -> None:
        self._sources = sources

    async def drain(self) -> list[Any]:
        out: list[Any] = []
        for source in self._sources:
            out.extend(await source.drain())
        return out


class DispatchingAdjudicator:
    """依命令的型別派給對應裁決器（WP-C10.2）。

    **未登錄的型別回空事件而非拋錯**是刻意的：Kernel 的 tick 迴圈不該因為某個令型忘了接線
    就整局停擺。代價是「忘了接線」會安靜地什麼都不做——故接線本身要有測試釘住。
    """

    def __init__(self, routes: dict[type, Adjudicator]) -> None:
        self._routes = routes

    def resolve(self, order: Any, now: SimTime) -> list[LedgerEvent]:
        adjudicator = self._routes.get(type(order))
        return adjudicator.resolve(order, now) if adjudicator is not None else []


class NoOpMovementSystem:
    async def step(self, now: SimTime) -> list[LedgerEvent]:
        return []


class NoOpSensorSystem:
    async def sweep(self, now: SimTime) -> list[LedgerEvent]:
        return []


class NoOpCommsSystem:
    async def evaluate(self, now: SimTime) -> list[LedgerEvent]:
        return []


class NoOpLogisticsSystem:
    async def consume(self, now: SimTime) -> list[LedgerEvent]:
        return []


class NoOpTriggerChecker:
    def check(self, now: SimTime) -> list[LedgerEvent]:
        return []


class NoOpBroadcaster:
    async def publish(self, tick: int, diff: SessionDiff) -> None:
        return None

    async def publish_events(self, events: Sequence[LedgerEvent]) -> None:
        return None


class NoOpEventSink:
    """丟棄事件的 sink（確定性 replay / 測試用）。"""

    def append(self, session_id: str, events: Sequence[LedgerEvent]) -> list[str]:
        return []

    def tip_seq(self, session_id: str) -> int:
        return -1
