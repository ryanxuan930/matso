"""活模擬期單位座標即時調整命令通道（White Cell「地圖狀態編輯」用）。

與 [live_ammo](live_ammo.py) 同紀律（single-writer, SPEC_FULL §3.4）：熱狀態只由 Kernel 迴圈行程
寫入，且 RedisHotState 有 in-process mirror cache——外部行程直寫 Redis 會被忽略。故拖放編輯單位
座標時，API 只把命令 RPUSH 進 Redis list，由 sim 迴圈每 tick 前 drain、以**自己那顆 hot 實例**套用。

地圖狀態編輯通常在**暫停**下進行 → 命令留在 list，開始兵推（RESUME）後第一個 tick 前 drain 生效；
座標同時寫入 DB（權威，供 COP 顯示 / reconnect / seed_combat_state）。

命令格式：{"unit_id": str, "lat": float, "lng": float}。後到覆寫（同單位取最後一筆）。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

    from app.state.hot_state import HotStateStore

_LOG = logging.getLogger("app.live_position")
_MAX_DRAIN = 512  # 單 tick 最多套用命令數（防呆上限）


def pos_cmd_key(session_id: str) -> str:
    return f"session:{session_id}:pos_cmds"


def push_pos_cmd(
    client: redis.Redis, session_id: str, unit_id: str, lat: float, lng: float
) -> None:
    """把一筆座標調整命令排入該 session 的命令 list（供 sim 迴圈 drain）。"""
    client.rpush(
        pos_cmd_key(session_id),
        json.dumps({"unit_id": unit_id, "lat": float(lat), "lng": float(lng)}),
    )


def drain_pos_cmds(client: redis.Redis, session_id: str) -> list[dict[str, Any]]:
    """原子取出並清空該 session 的所有待套座標命令（pipeline: LRANGE + DEL）。"""
    key = pos_cmd_key(session_id)
    pipe = client.pipeline()
    pipe.lrange(key, 0, _MAX_DRAIN - 1)
    pipe.delete(key)
    raw, _ = pipe.execute()
    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except (ValueError, TypeError):
            _LOG.warning("session %s: 丟棄壞的座標命令：%r", session_id, item)
    return out


def apply_pos_cmds(hot: HotStateStore, cmds: list[dict[str, Any]]) -> int:
    """把座標命令套進熱狀態的 lat/lng（權威覆寫，同單位取最後）。回實際套用數。單一寫入者呼叫。"""
    latest: dict[str, tuple[float, float]] = {}
    for c in cmds:
        uid, lat, lng = c.get("unit_id"), c.get("lat"), c.get("lng")
        if isinstance(uid, str) and isinstance(lat, int | float) and isinstance(lng, int | float):
            latest[uid] = (float(lat), float(lng))
    applied = 0
    for uid, (lat, lng) in latest.items():
        if hot.get_unit(uid) is None:
            continue  # 無此單位熱狀態（sim 尚未 seed）→ 略過，下次由 seed 帶入 DB 值
        hot.update_unit(uid, {"lat": lat, "lng": lng})
        applied += 1
    return applied
