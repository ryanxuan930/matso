"""Redis client 工廠（熱狀態、broadcaster ring buffer / pub-sub）。

decode_responses=True：回傳 str 而非 bytes，方便直接 json.loads。
"""

from __future__ import annotations

import redis

from app.config import Settings


def make_redis(url: str | None = None) -> redis.Redis:
    resolved = url if url is not None else Settings().redis_url
    return redis.Redis.from_url(resolved, decode_responses=True)
