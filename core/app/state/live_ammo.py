"""活模擬期彈藥即時調整命令通道（White Cell / 編裝編輯）。

單一寫入者（single-writer, SPEC_FULL §3.4）：熱狀態只由 Kernel 迴圈的行程寫入，且 RedisHotState
維護 in-process mirror cache——外部行程直寫 Redis 會被 mirror 忽略。故裝備編輯改彈藥時，API 只把
命令 RPUSH 進 Redis list，由 sim 迴圈每 tick 前 drain 並以**自己那顆 hot 實例**套用（同實例→
mirror 一致；同行程→非 single-writer 違反；tick 之間套用→不與 tick 內寫入競態）。

命令格式：{"unit_id": str, "weapon_id": str, "ammo": int}。手動編輯為**權威**：直接覆寫該武器的
ammo_by_weapon（非合併扣減）。無活 sim 時命令留在 list，下次迴圈起來即 drain（或被 seed 蓋過）。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

    from app.state.hot_state import HotStateStore

_LOG = logging.getLogger("app.live_ammo")
_MAX_DRAIN = 256  # 單 tick 最多套用命令數（防呆上限；正常遠低於此）


def ammo_cmd_key(session_id: str) -> str:
    return f"session:{session_id}:ammo_cmds"


def push_ammo_cmd(
    client: redis.Redis, session_id: str, unit_id: str, weapon_id: str, ammo: int
) -> None:
    """把一筆彈藥調整命令排入該 session 的命令 list（供 sim 迴圈 drain）。"""
    client.rpush(
        ammo_cmd_key(session_id),
        json.dumps({"unit_id": unit_id, "weapon_id": weapon_id, "ammo": int(ammo)}),
    )


def drain_ammo_cmds(client: redis.Redis, session_id: str) -> list[dict[str, Any]]:
    """原子取出並清空該 session 的所有待套命令（pipeline: LRANGE + DEL）。"""
    key = ammo_cmd_key(session_id)
    pipe = client.pipeline()
    pipe.lrange(key, 0, _MAX_DRAIN - 1)
    pipe.delete(key)
    raw, _ = pipe.execute()
    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except (ValueError, TypeError):
            _LOG.warning("session %s: 丟棄壞的彈藥命令：%r", session_id, item)
    return out


def apply_ammo_cmds(hot: HotStateStore, cmds: list[dict[str, Any]]) -> int:
    """把彈藥命令套進熱狀態的 ammo_by_weapon（權威覆寫）。回實際套用數。單一寫入者呼叫。"""
    applied = 0
    by_unit: dict[str, dict[str, int]] = {}
    for c in cmds:
        uid = c.get("unit_id")
        wid = c.get("weapon_id")
        ammo = c.get("ammo")
        if isinstance(uid, str) and isinstance(wid, str) and isinstance(ammo, (int, float)):
            by_unit.setdefault(uid, {})[wid] = int(ammo)
    for uid, updates in by_unit.items():
        state = hot.get_unit(uid)
        if state is None:
            continue  # 無此單位熱狀態（sim 尚未 seed）→ 略過，下次由 seed 帶入 DB 值
        abw = dict(state.get("ammo_by_weapon") or {})
        abw.update(updates)
        hot.update_unit(uid, {"ammo_by_weapon": abw})
        applied += len(updates)
    return applied
