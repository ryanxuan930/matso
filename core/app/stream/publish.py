"""發佈 EVENT 到 session Redis stream（O4.6）——與 RedisBroadcaster 同機制的通用事件推送。

kernel 真正裁決後應由 event_sink/broadcaster 產出 EVENT；本 helper 供 E2E stub 模式在下令
成功後同步發一則裁決事件，以驗證「下令 → 事件 → WS → UI」全鏈路。
"""

from __future__ import annotations

import json
from typing import Any

import redis

from app.state.broadcaster import RING_CAPACITY


def publish_event(
    redis_client: redis.Redis,
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    faction: str | None = None,
) -> int:
    """指派 seq（INCR）→ 推 ring buffer → PUBLISH。回傳 seq。與 STATE_DIFF 共用計數器/頻道。"""
    env: dict[str, Any] = {
        "v": 1,
        "seq": 0,
        "type": "EVENT",
        "payload": {"event_type": event_type, **payload},
    }
    if faction is not None:
        env["faction"] = faction
    seq = int(redis_client.incr(f"session:{session_id}:broadcast_seq"))
    env["seq"] = seq
    data = json.dumps(env)
    pipe = redis_client.pipeline()
    pipe.rpush(f"session:{session_id}:ring", data)
    pipe.ltrim(f"session:{session_id}:ring", -RING_CAPACITY, -1)
    pipe.publish(f"session:{session_id}:stream", data)
    pipe.execute()
    return seq
