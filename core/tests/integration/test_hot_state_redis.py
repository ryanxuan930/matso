"""RedisHotState / RedisBroadcaster 整合測試（連 compose 的 Redis:6379）。

驗收（TASKS.md O1.4）：寫入→讀回 roundtrip；改 3 個欄位 → diff 恰含 3 欄。
compose 未啟動時整個模組 skip。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
import redis

from app.state.broadcaster import RING_CAPACITY, RedisBroadcaster
from app.state.hot_state import RedisHotState

pytestmark = pytest.mark.integration

DEV_REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture(scope="module")
def redis_client() -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(DEV_REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:  # 連不上就整組 skip
        pytest.skip(f"Redis:6379 未就緒（compose 未啟動？）：{exc}")
    yield client
    client.close()


@pytest.fixture
def session_id(redis_client: redis.Redis) -> Iterator[str]:
    sid = f"itest-{uuid.uuid4()}"
    yield sid
    # 清理本 session 的所有 key
    for key in redis_client.scan_iter(match=f"session:{sid}:*"):
        redis_client.delete(key)


def test_write_read_roundtrip(redis_client: redis.Redis, session_id: str) -> None:
    hs = RedisHotState(redis_client, session_id)
    hs.put_unit("u1", {"lat": 25.0, "lng": 121.5, "health": 100})
    assert hs.get_unit("u1") == {"lat": 25.0, "lng": 121.5, "health": 100}
    # 直接驗證 Redis 真的有這個 key（不透過 HotState）
    raw = redis_client.get(f"session:{session_id}:unit:u1")
    assert raw is not None
    assert json.loads(raw)["health"] == 100


def test_change_three_fields_diff_has_three(redis_client: redis.Redis, session_id: str) -> None:
    hs = RedisHotState(redis_client, session_id)
    hs.put_unit("u1", {"lat": 1.0, "lng": 2.0, "health": 100, "comms": "ONLINE"})
    hs.drain_diff()  # 清掉部署 diff
    diff = hs.update_unit("u1", {"lat": 1.5, "lng": 2.5, "health": 80})
    assert diff == {"lat": 1.5, "lng": 2.5, "health": 80}
    assert hs.drain_diff() == {"u1": {"lat": 1.5, "lng": 2.5, "health": 80}}


def test_get_all_scans_session(redis_client: redis.Redis, session_id: str) -> None:
    hs = RedisHotState(redis_client, session_id)
    hs.put_unit("u1", {"health": 100})
    hs.put_unit("u2", {"health": 90})
    assert hs.get_all() == {"u1": {"health": 100}, "u2": {"health": 90}}


async def test_broadcaster_writes_ring_buffer(redis_client: redis.Redis, session_id: str) -> None:
    bc = RedisBroadcaster(redis_client, session_id)
    await bc.publish(tick=0, diff={"u1": {"health": 80}})
    await bc.publish(tick=1, diff={"u1": {"health": 60}})

    ring = redis_client.lrange(f"session:{session_id}:ring", 0, -1)
    assert len(ring) == 2
    env0 = json.loads(ring[0])
    assert env0["type"] == "STATE_DIFF"
    assert env0["tick"] == 0
    assert env0["payload"]["units"] == [{"id": "u1", "health": 80}]
    # seq 單調遞增
    assert json.loads(ring[1])["seq"] > env0["seq"]


async def test_broadcaster_skips_empty_diff(redis_client: redis.Redis, session_id: str) -> None:
    bc = RedisBroadcaster(redis_client, session_id)
    await bc.publish(tick=0, diff={})
    assert redis_client.exists(f"session:{session_id}:ring") == 0


async def test_ring_buffer_capped(redis_client: redis.Redis, session_id: str) -> None:
    bc = RedisBroadcaster(redis_client, session_id)
    for i in range(RING_CAPACITY + 10):
        await bc.publish(tick=i, diff={"u1": {"health": i}})
    assert redis_client.llen(f"session:{session_id}:ring") == RING_CAPACITY
