"""白軍對 MSEL 的動態取捨命令通道（WP-B2c）。

**為什麼要一條通道**：`MselRuntime` 活在 sim runner 行程裡，而白軍按的按鈕在 API 行程。
API 直接改 runtime 的記憶是做不到的（不同行程），直接寫 Redis 熱狀態也不行
（`RedisHotState` 有 in-process mirror，外部直寫會被忽略——`api/control.py` 已經記過這個教訓）。

故沿用 `live_ammo` / `live_position` 同一套：API 把命令排進 Redis list，
runner 在 `pre_tick` drain 出來套用。**單一寫入者原則因此維持**。

反方向（runner → 白軍看得到「還有哪些狀況待發」）走另一個鍵：runner 每 tick 覆寫
`session:{id}:msel:pending`，API 讀它。那是純顯示，晚一個 tick 無所謂。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

_LOG = logging.getLogger("app.msel.live")
_MAX_DRAIN = 200

FIRE = "FIRE"
SKIP = "SKIP"


def msel_cmd_key(session_id: str) -> str:
    return f"session:{session_id}:msel:cmds"


def msel_pending_key(session_id: str) -> str:
    return f"session:{session_id}:msel:pending"


def push_msel_cmd(client: redis.Redis, session_id: str, action: str, entry_id: str) -> None:
    """白軍扣板機 / 跳過某條 MSEL。由 API 呼叫。"""
    client.rpush(msel_cmd_key(session_id), json.dumps({"action": action, "entry_id": entry_id}))


def drain_msel_cmds(client: redis.Redis, session_id: str) -> list[dict[str, Any]]:
    """原子取出並清空待套命令（LRANGE + DEL）。由 runner 在 pre_tick 呼叫。"""
    key = msel_cmd_key(session_id)
    pipe = client.pipeline()
    pipe.lrange(key, 0, _MAX_DRAIN - 1)
    pipe.delete(key)
    raw, _ = pipe.execute()
    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except (ValueError, TypeError):
            _LOG.warning("session %s: 丟棄壞的 MSEL 命令：%r", session_id, item)
    return out


def apply_msel_cmds(runtime: Any, cmds: list[dict[str, Any]]) -> int:
    """把命令套進 runtime 的記憶。回實際套用數。**單一寫入者（runner）呼叫**。"""
    applied = 0
    for cmd in cmds:
        entry_id = str(cmd.get("entry_id") or "")
        if not entry_id:
            continue
        action = str(cmd.get("action") or "").upper()
        if action == FIRE:
            runtime.fire_manually(entry_id)
        elif action == SKIP:
            runtime.skip(entry_id)
        else:
            continue
        applied += 1
    return applied


def publish_pending(client: redis.Redis, session_id: str, pending: list[dict[str, Any]]) -> None:
    """把「還有哪些狀況待發」寫給白軍控制台看。純顯示，晚一個 tick 無所謂。"""
    try:
        client.set(msel_pending_key(session_id), json.dumps(pending))
    except Exception:  # Redis 抖一下不該讓 tick 受影響
        _LOG.debug("session %s: MSEL 待命清單寫入失敗", session_id)


def read_pending(client: redis.Redis, session_id: str) -> list[dict[str, Any]]:
    """讀待命清單（API 端）。讀不到 → 空清單（該局沒在跑或沒有 MSEL）。"""
    try:
        raw = client.get(msel_pending_key(session_id))
    except Exception:
        return []
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(loaded, list):
        return []
    # 舊格式（純 id 字串）也吃：runner 尚未更新的局，畫面退回只顯示 id 而不是整段消失。
    return [x if isinstance(x, dict) else {"id": str(x), "event_type": ""} for x in loaded]


__all__ = [
    "FIRE",
    "SKIP",
    "apply_msel_cmds",
    "drain_msel_cmds",
    "msel_cmd_key",
    "msel_pending_key",
    "publish_pending",
    "push_msel_cmd",
    "read_pending",
]
