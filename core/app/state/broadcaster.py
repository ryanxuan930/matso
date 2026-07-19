"""狀態廣播（STATE_DIFF）— Redis 落地層（SPEC_FULL §16.2、contracts/ws_protocol.md）。

本層負責把每 tick 的 diff 包成 envelope 並：
1. 指派 per-session 單調 seq（Redis INCR）。
2. 推入 ring buffer（Redis list，capped 5000）供斷線重連補送。
3. PUBLISH 到 pub/sub 頻道。

WebSocket 客戶端 fan-out（訂閱頻道、依 faction 過濾、推給前端）屬 O4.3，不在此。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import redis

from app.state.hot_state import SessionDiff

RING_CAPACITY = 5000  # §16.2：保留最近 5000 條供重連補送


def build_state_diff_envelope(seq: int, tick: int, diff: SessionDiff) -> dict[str, Any]:
    """依 ws_protocol.md 的 envelope + STATE_DIFF payload 格式建構訊息。"""
    return {
        "v": 1,
        "seq": seq,
        "tick": tick,
        "type": "STATE_DIFF",
        "payload": {"units": [{"id": unit_id, **fields} for unit_id, fields in diff.items()]},
    }


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
            return  # 無變動不送 STATE_DIFF（CLOCK 心跳為獨立訊息，O4.3）
        await asyncio.to_thread(self._publish_sync, tick, diff)

    def _publish_sync(self, tick: int, diff: SessionDiff) -> None:
        seq = int(self._redis.incr(self._seq_key()))
        payload = json.dumps(build_state_diff_envelope(seq, tick, diff))
        pipe = self._redis.pipeline()
        pipe.rpush(self._ring_key(), payload)
        pipe.ltrim(self._ring_key(), -RING_CAPACITY, -1)
        pipe.publish(self._channel(), payload)
        pipe.execute()

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

    async def publish(self, tick: int, diff: SessionDiff) -> None:
        self.published.append((tick, dict(diff)))
