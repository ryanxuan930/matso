"""原子化 stream 發佈（CODE_REVIEW C3）。

問題：seq 指派（INCR）與 ring 寫入（RPUSH）分兩步，且 O4.6 起有兩個併發寫入者
（Kernel 的 RedisBroadcaster + API 的 publish_event）共用同一 seq 計數器與 ring。交錯時可
「A 取 seq=5、B 取 seq=6，B 先 RPUSH」→ ring 順序與 seq 不一致 → backfill 亂序、client 重覆收。

修法：把 INCR + RPUSH + LTRIM + PUBLISH 包成單一 **Lua script** 原子執行（seq 於 script 內指派
並寫回 envelope）。真 Redis 走 Lua；**fakeredis 無 EVAL** → 退回逐步路徑（測試為單執行緒、
無跨 client 交錯，語義等價）。
"""

from __future__ import annotations

import json
from typing import Any

import redis

# KEYS[1]=seq_key KEYS[2]=ring_key KEYS[3]=channel
# ARGV[1]=envelope_json(含 seq 佔位) ARGV[2]=ring_capacity
_PUBLISH_LUA = """
local seq = redis.call('INCR', KEYS[1])
local env = cjson.decode(ARGV[1])
env['seq'] = seq
local data = cjson.encode(env)
redis.call('RPUSH', KEYS[2], data)
redis.call('LTRIM', KEYS[2], -tonumber(ARGV[2]), -1)
redis.call('PUBLISH', KEYS[3], data)
return seq
"""


def publish_to_stream(
    client: redis.Redis,
    *,
    seq_key: str,
    ring_key: str,
    channel: str,
    envelope: dict[str, Any],
    ring_capacity: int,
) -> int:
    """原子指派 seq → 寫回 envelope['seq'] → RPUSH/LTRIM/PUBLISH。回傳指派的 seq。

    envelope 傳入時的 seq 值僅為佔位，實際值由本函式指派。
    """
    try:
        script = client.register_script(_PUBLISH_LUA)
        return int(
            script(
                keys=[seq_key, ring_key, channel],
                args=[json.dumps(envelope, ensure_ascii=False), ring_capacity],
            )
        )
    except redis.ResponseError:
        # fakeredis / 不支援 EVAL 的後端：單執行緒測試環境，無跨 client 交錯 → 逐步等價。
        seq = int(client.incr(seq_key))
        envelope["seq"] = seq
        data = json.dumps(envelope, ensure_ascii=False)
        pipe = client.pipeline()
        pipe.rpush(ring_key, data)
        pipe.ltrim(ring_key, -ring_capacity, -1)
        pipe.publish(channel, data)
        pipe.execute()
        return seq
