"""活模擬期單位屬性即時調整命令通道（統裁「單位屬性編輯」用）。

與 [live_ammo](live_ammo.py) / [live_position](live_position.py) 同紀律
（single-writer, SPEC_FULL §3.4）：熱狀態只由 Kernel 迴圈那個行程寫入，且 `RedisHotState`
維護 in-process mirror cache——**外部行程直寫 Redis 會被 mirror 忽略**。故 API 改單位屬性時
只把命令 RPUSH 進 Redis list，由 sim 迴圈每 tick 前 drain 並以自己那顆 hot 實例套用。

## 為什麼不是「寫 DB 就好」

`designation` / `branch` 這類純顯示欄位寫 DB 就夠了——`GET /units` 讀的就是 DB。
但**戰力與建制數不是**：它們在開局時被播種進熱狀態，此後裁決層只讀熱狀態。
只改 DB 的話，畫面上人數變了、實際打起來還是舊的編制——那正是這個 repo
反覆出現的那類缺陷（存得進去、讀得回來、實際沒效果）。

## 白名單

只有下列鍵可經本通道寫入熱狀態。**不開放整包 patch**：熱狀態裡還有 `ammo_by_weapon`、
`suppression`、`posture_since_tick` 這些由各子系統維護的欄位，讓 API 任意覆寫等於
在單一寫入者上開一個後門。

命令格式：`{"unit_id": str, "patch": {key: value}}`。同單位後到覆寫（逐鍵合併）。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

    from app.state.hot_state import HotStateStore

_LOG = logging.getLogger("app.live_unit")
_MAX_DRAIN = 512  # 單 tick 最多套用命令數（防呆上限）

# 可經本通道寫入熱狀態的鍵（見模組說明的白名單理由）。
EDITABLE_HOT_KEYS = frozenset(
    {
        "strength",  # 當前戰力（裁決的權威量）
        "authorized_strength",  # 滿編戰力（戰力比的分母）
        "health",  # 作戰效能%（由戰力比導出，隨 strength 一起改）
        "platform_count",  # 建制數（齊射發數、面射擊佔地都讀它）
    }
)


def unit_cmd_key(session_id: str) -> str:
    return f"session:{session_id}:unit_cmds"


def push_unit_cmd(
    client: redis.Redis, session_id: str, unit_id: str, patch: dict[str, Any]
) -> None:
    """把一筆單位屬性調整命令排入該 session 的命令 list（供 sim 迴圈 drain）。

    非白名單的鍵在此就被丟掉——不要讓它進到 Redis 再靠下游過濾，
    那會讓「這個鍵到底能不能改」有兩個答案。
    """
    allowed = {k: v for k, v in patch.items() if k in EDITABLE_HOT_KEYS}
    if not allowed:
        return
    client.rpush(
        unit_cmd_key(session_id),
        json.dumps({"unit_id": unit_id, "patch": allowed}),
    )


def drain_unit_cmds(client: redis.Redis, session_id: str) -> list[dict[str, Any]]:
    """原子取出並清空該 session 的所有待套命令（pipeline: LRANGE + DEL）。"""
    key = unit_cmd_key(session_id)
    pipe = client.pipeline()
    pipe.lrange(key, 0, _MAX_DRAIN - 1)
    pipe.delete(key)
    raw, _ = pipe.execute()
    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except (ValueError, TypeError):
            _LOG.warning("session %s: 丟棄壞的單位屬性命令：%r", session_id, item)
    return out


def apply_unit_cmds(hot: HotStateStore, cmds: list[dict[str, Any]]) -> int:
    """把命令套進熱狀態（逐鍵合併，同單位後到覆寫）。回實際套用的單位數。單一寫入者呼叫。"""
    merged: dict[str, dict[str, Any]] = {}
    for c in cmds:
        uid = c.get("unit_id")
        patch = c.get("patch")
        if not isinstance(uid, str) or not isinstance(patch, dict):
            continue
        clean = {
            k: v
            for k, v in patch.items()
            if k in EDITABLE_HOT_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        if clean:
            merged.setdefault(uid, {}).update(clean)
    applied = 0
    for uid, patch in merged.items():
        if hot.get_unit(uid) is None:
            continue  # sim 尚未 seed 此單位 → 略過（開局時會由 DB 帶入新值）
        hot.update_unit(uid, patch)
        applied += 1
    return applied
