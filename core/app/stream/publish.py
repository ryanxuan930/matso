"""發佈 EVENT 到 session Redis stream（O4.6）——與 RedisBroadcaster 同機制的通用事件推送。

kernel 真正裁決後應由 event_sink/broadcaster 產出 EVENT；本 helper 供 E2E stub 模式在下令
成功後同步發一則裁決事件，以驗證「下令 → 事件 → WS → UI」全鏈路。
"""

from __future__ import annotations

from typing import Any

import redis

from app.state.broadcaster import RING_CAPACITY
from app.state.redis_stream import publish_to_stream


def publish_event(
    redis_client: redis.Redis,
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    faction: str | None = None,
) -> int:
    """原子指派 seq → 推 ring buffer → PUBLISH。回傳 seq。與 STATE_DIFF 共用計數器/頻道。

    seq 指派與 ring 寫入原子化（CODE_REVIEW C3），避免與 Kernel broadcaster 併發時順序倒置。
    """
    env: dict[str, Any] = {
        "v": 1,
        "seq": 0,  # 佔位；由 publish_to_stream 原子指派
        "type": "EVENT",
        "payload": {"event_type": event_type, **payload},
    }
    if faction is not None:
        env["faction"] = faction
    return publish_to_stream(
        redis_client,
        seq_key=f"session:{session_id}:broadcast_seq",
        ring_key=f"session:{session_id}:ring",
        channel=f"session:{session_id}:stream",
        envelope=env,
        ring_capacity=RING_CAPACITY,
    )
