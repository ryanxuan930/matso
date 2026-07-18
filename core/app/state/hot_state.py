"""單位熱狀態（hot state）與 per-tick diff（SPEC_FULL §3.4、§16.2）。

- 全部單位當前狀態存於 Redis，key `session:{session_id}:unit:{unit_id}`。
- **Kernel 為唯一寫入者**（single-writer principle）：其他元件要改狀態必須透過 Kernel。
- 每 tick 累積「只含變動欄位」的 diff（drain_diff），供 broadcaster 產生 STATE_DIFF。

提供兩個 HotStateStore 實作：
- RedisHotState：正式（整合測試連 compose Redis:6379）。
- InMemoryHotState：單元測試不需 Redis，且供 O1.6 確定性 replay（無 I/O）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

import redis

UnitState = dict[str, Any]
UnitDiff = dict[str, Any]
SessionDiff = dict[str, UnitDiff]


def compute_diff(old: Mapping[str, Any], new: Mapping[str, Any]) -> UnitDiff:
    """回傳 new 相對 old 的變動：新增或值不同的欄位（相同值不列入）。

    不處理欄位移除——單位熱狀態使用固定欄位集（lat/lng/health/comms…），欄位不會消失。
    """
    return {k: v for k, v in new.items() if k not in old or old[k] != v}


@runtime_checkable
class HotStateStore(Protocol):
    """Kernel 依賴的熱狀態介面。"""

    def put_unit(self, unit_id: str, state: Mapping[str, Any]) -> None: ...
    def update_unit(self, unit_id: str, changes: Mapping[str, Any]) -> UnitDiff: ...
    def get_unit(self, unit_id: str) -> UnitState | None: ...
    def get_all(self) -> dict[str, UnitState]: ...
    def drain_diff(self) -> SessionDiff: ...


class _BaseHotState:
    """共用 diff 累積邏輯；子類別只需實作實際存取（_read/_write/get_all）。"""

    def __init__(self) -> None:
        self._pending: SessionDiff = {}

    # --- 子類別實作 ---
    def _read(self, unit_id: str) -> UnitState | None:
        raise NotImplementedError

    def _write(self, unit_id: str, state: UnitState) -> None:
        raise NotImplementedError

    def get_all(self) -> dict[str, UnitState]:
        raise NotImplementedError

    # --- 共用 ---
    def put_unit(self, unit_id: str, state: Mapping[str, Any]) -> None:
        """部署/整體設定一個單位。新單位的所有欄位視為 diff（讓 client 學到它）。"""
        snapshot = dict(state)
        self._write(unit_id, snapshot)
        self._pending[unit_id] = dict(snapshot)

    def update_unit(self, unit_id: str, changes: Mapping[str, Any]) -> UnitDiff:
        """套用部分更新（Kernel 唯一寫入者），回傳實際變動的欄位。"""
        current = self._read(unit_id) or {}
        merged = {**current, **changes}
        diff = compute_diff(current, merged)
        if diff:
            self._write(unit_id, merged)
            self._pending.setdefault(unit_id, {}).update(diff)
        return diff

    def get_unit(self, unit_id: str) -> UnitState | None:
        return self._read(unit_id)

    def drain_diff(self) -> SessionDiff:
        """取出並清空本 tick 累積的 per-unit 變動欄位。"""
        drained = self._pending
        self._pending = {}
        return drained


class InMemoryHotState(_BaseHotState):
    """dict-backed 熱狀態，供單元測試與確定性 replay（無 Redis / 無 I/O）。"""

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[str, UnitState] = {}

    def _read(self, unit_id: str) -> UnitState | None:
        state = self._store.get(unit_id)
        return dict(state) if state is not None else None

    def _write(self, unit_id: str, state: UnitState) -> None:
        self._store[unit_id] = dict(state)

    def get_all(self) -> dict[str, UnitState]:
        return {k: dict(v) for k, v in self._store.items()}


class RedisHotState(_BaseHotState):
    """Redis-backed 熱狀態。key: session:{session_id}:unit:{unit_id}。"""

    def __init__(self, redis_client: redis.Redis, session_id: str) -> None:
        super().__init__()
        self._redis = redis_client
        self._session_id = session_id

    def _key(self, unit_id: str) -> str:
        return f"session:{self._session_id}:unit:{unit_id}"

    def _prefix(self) -> str:
        return f"session:{self._session_id}:unit:"

    def _read(self, unit_id: str) -> UnitState | None:
        raw = self._redis.get(self._key(unit_id))
        if raw is None:
            return None
        loaded: UnitState = json.loads(raw)
        return loaded

    def _write(self, unit_id: str, state: UnitState) -> None:
        self._redis.set(self._key(unit_id), json.dumps(state))

    def get_all(self) -> dict[str, UnitState]:
        prefix = self._prefix()
        result: dict[str, UnitState] = {}
        for key in self._redis.scan_iter(match=f"{prefix}*"):
            unit_id = key[len(prefix) :]
            raw = self._redis.get(key)
            if raw is not None:
                result[unit_id] = json.loads(raw)
        return result
