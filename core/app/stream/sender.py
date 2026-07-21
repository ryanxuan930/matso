"""背壓有界送出佇列（O4.3，HOW_TO §8）——per-client send queue 上限，禁無限緩衝。

慢 client（消費不及）→ 生產端 offer 溢出 → BackpressureError → WS 層斷線並要求重同步。
"""

from __future__ import annotations

import asyncio
from typing import Any

MAX_QUEUE = 1000  # ws_protocol.md：per-client 上限 1000 則


class BackpressureError(Exception):
    """send queue 溢出（慢 client）——WS 層應斷線並要求全量重同步。"""


class BoundedSender:
    """有界 FIFO：生產端 `offer` 非阻塞（滿即拋 BackpressureError）；消費端 `next` 等待取出。"""

    def __init__(self, maxsize: int = MAX_QUEUE) -> None:
        self._q: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)

    def offer(self, item: Any) -> None:
        """放入一則待送訊息；佇列已滿（慢 client）→ BackpressureError。"""
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise BackpressureError("send queue 溢出（慢 client）") from exc

    async def next(self) -> Any:
        return await self._q.get()

    def pending(self) -> int:
        return self._q.qsize()
