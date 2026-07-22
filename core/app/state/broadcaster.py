"""狀態廣播（STATE_DIFF）— Redis 落地層（SPEC_FULL §16.2、contracts/ws_protocol.md）。

本層負責把每 tick 的 diff 包成 envelope 並：
1. 指派 per-session 單調 seq（Redis INCR）。
2. 推入 ring buffer（Redis list，capped 5000）供斷線重連補送。
3. PUBLISH 到 pub/sub 頻道。

WebSocket 客戶端 fan-out（訂閱頻道、依 faction 過濾、推給前端）屬 O4.3，不在此。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import redis

from app.state.hot_state import SessionDiff
from app.state.ledger import LedgerEvent
from app.state.redis_stream import publish_to_stream

RING_CAPACITY = 5000  # §16.2：保留最近 5000 條供重連補送

# 不進 WS 戰況事件 feed 的事件型別：UNIT_MOVED 每 tick 每移動單位一則（過吵，位置改由 STATE_DIFF
# 呈現）；TICK_OVERRUN 為診斷。其餘（ENGAGEMENT_RESOLVED / UNIT_ARRIVED / 注入 / 觸發…）皆推。
_FEED_EXCLUDE = frozenset({"UNIT_MOVED", "TICK_OVERRUN"})


def build_event_envelope(event: LedgerEvent) -> dict[str, Any]:
    """把 LedgerEvent 壓成精簡的 EVENT envelope payload（供戰況 feed；ID→番號由前端對映）。"""
    payload: dict[str, Any] = {"event_type": event.event_type, "tick": event.tick}
    if event.initiator_id:
        payload["initiator_id"] = event.initiator_id
    if event.target_id:
        payload["target_id"] = event.target_id
    if event.damage_calc is not None:
        payload["damage"] = event.damage_calc
    # #33 comms 狀態轉移的 from/to 也帶出（供戰況 feed 顯示「通聯 X→Y」）。
    for k in ("status", "reason", "target_health_after", "from", "to"):
        if isinstance(event.ai_decision, dict) and k in event.ai_decision:
            payload[k] = event.ai_decision[k]
    return {"v": 1, "seq": 0, "type": "EVENT", "payload": payload}


def build_state_diff_envelope(seq: int, tick: int, diff: SessionDiff) -> dict[str, Any]:
    """依 ws_protocol.md 的 envelope + STATE_DIFF payload 格式建構訊息。"""
    return {
        "v": 1,
        "seq": seq,
        "tick": tick,
        "type": "STATE_DIFF",
        "payload": {"units": [{"id": unit_id, **fields} for unit_id, fields in diff.items()]},
    }


def build_clock_envelope(seq: int, tick: int) -> dict[str, Any]:
    """CLOCK 心跳 envelope（頂層 tick）——閒置（無 STATE_DIFF）時仍讓前端牆鐘不凍結。"""
    return {"v": 1, "seq": seq, "tick": tick, "type": "CLOCK", "payload": {}}


# CLOCK 心跳節流：閒置時每 N tick 送一次（避免灌爆 ring；有活動時 STATE_DIFF 已逐 tick 更新）。
_CLOCK_EVERY_TICKS = 5


class RedisBroadcaster:
    """把 STATE_DIFF 寫入 Redis ring buffer 並 publish。滿足 Kernel 的 Broadcaster 介面。

    - redis-py 為同步 driver：publish 內以 asyncio.to_thread 執行，不阻塞 event loop
      （HOW_TO §3.1；O1.7/R9）。
    - seq 語意（O1.7/R7）：broadcast seq 是「傳輸層計數器」，存於 Redis、**不耐 Redis 清空**。
      Redis 遺失 = ring buffer 同時遺失 → 所有客戶端必須全量重同步；復原流程應呼叫
      reset_stream() 讓新串流從乾淨狀態開始（契約見 contracts/ws_protocol.md）。
    """

    def __init__(self, redis_client: redis.Redis, session_id: str) -> None:
        self._redis = redis_client
        self._session_id = session_id

    def _seq_key(self) -> str:
        return f"session:{self._session_id}:broadcast_seq"

    def _ring_key(self) -> str:
        return f"session:{self._session_id}:ring"

    def _channel(self) -> str:
        return f"session:{self._session_id}:stream"

    async def publish(self, tick: int, diff: SessionDiff) -> None:
        if not diff:
            # 閒置無變動：節流送 CLOCK 心跳，讓前端牆鐘不凍結（否則 idle session tick 停在 T—）。
            if tick % _CLOCK_EVERY_TICKS == 0:
                await asyncio.to_thread(self._publish_clock_sync, tick)
            return
        await asyncio.to_thread(self._publish_sync, tick, diff)

    def _publish_clock_sync(self, tick: int) -> None:
        publish_to_stream(
            self._redis,
            seq_key=self._seq_key(),
            ring_key=self._ring_key(),
            channel=self._channel(),
            envelope=build_clock_envelope(0, tick),  # seq 佔位，由 publish_to_stream 指派
            ring_capacity=RING_CAPACITY,
        )

    async def publish_events(self, events: Sequence[LedgerEvent]) -> None:
        """把裁決事件推到 WS 事件流（戰況 feed）。與 STATE_DIFF 共用 seq/ring/channel（原子）。"""
        feed = [e for e in events if e.event_type not in _FEED_EXCLUDE]
        if feed:
            await asyncio.to_thread(self._publish_events_sync, feed)

    def _publish_events_sync(self, events: list[LedgerEvent]) -> None:
        for e in events:
            publish_to_stream(
                self._redis,
                seq_key=self._seq_key(),
                ring_key=self._ring_key(),
                channel=self._channel(),
                envelope=build_event_envelope(e),
                ring_capacity=RING_CAPACITY,
            )

    def _publish_sync(self, tick: int, diff: SessionDiff) -> None:
        # 原子指派 seq + 寫 ring + publish（CODE_REVIEW C3）——與 API 端 publish_event 共用同一
        # 原子路徑，避免兩個寫入者交錯造成 ring 順序與 seq 不一致。
        envelope = build_state_diff_envelope(0, tick, diff)  # seq 佔位，由 publish_to_stream 指派
        publish_to_stream(
            self._redis,
            seq_key=self._seq_key(),
            ring_key=self._ring_key(),
            channel=self._channel(),
            envelope=envelope,
            ring_capacity=RING_CAPACITY,
        )

    def reset_stream(self) -> None:
        """清除傳輸層狀態（seq 計數器 + ring buffer），供崩潰復原後重啟乾淨串流。

        呼叫後 seq 從 1 重新起算；WS 層（O4.3）看到客戶端 last_seq 超出 ring 範圍
        時回 RESYNC_REQUIRED（含 seq 倒退情形），客戶端走全量重同步。
        """
        self._redis.delete(self._seq_key(), self._ring_key())


class CollectingBroadcaster:
    """測試用 broadcaster：記錄每次 publish 的 (tick, diff)，不接 Redis。"""

    def __init__(self) -> None:
        self.published: list[tuple[int, SessionDiff]] = []
        self.published_events: list[LedgerEvent] = []

    async def publish(self, tick: int, diff: SessionDiff) -> None:
        self.published.append((tick, dict(diff)))

    async def publish_events(self, events: Sequence[LedgerEvent]) -> None:
        self.published_events.extend(events)
