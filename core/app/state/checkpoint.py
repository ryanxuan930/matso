"""狀態檢查點、回滾與崩潰復原（SPEC_FULL §3.4、§18；儲存策略見 ADR 002）。

- Checkpoint：每 N ticks 把完整單位熱狀態 zstd 壓縮存入 SimCheckpoint（inline LONGBLOB）。
- Recover：崩潰（Redis 清空）後，由最近 checkpoint 還原熱狀態。
- Rollback：還原到指定 checkpoint，並寫入 ROLLBACK Ledger 事件。

**復原範圍**：本模組保證「還原到 checkpoint 當下」。若 checkpoint 之後仍有 Ledger 事件
（mid-interval 崩潰），完整前滾需 O1.6 的確定性 replay（從 checkpoint 重跑模擬）或 RNG
狀態序列化（O1.1 backlog）。recover 回傳 events_after_checkpoint 供上層判斷是否需前滾。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import zstandard
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import SimCheckpoint, TacticalEventLog
from app.state.hot_state import HotStateStore, UnitState
from app.state.ledger import LedgerEvent, LedgerWriter, canonical_json

# ADR 002：壓縮後快照大小護欄，安全低於 MariaDB max_allowed_packet（16MB）。
MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024


class CheckpointTooLargeError(RuntimeError):
    """壓縮後快照超過 MAX_CHECKPOINT_BYTES（ADR 002 Phase 2 應改物件儲存）。"""


def serialize_state(state: Mapping[str, Any]) -> bytes:
    """完整狀態 → canonical JSON → zstd 壓縮 bytes。"""
    raw = canonical_json(state).encode("utf-8")
    return zstandard.ZstdCompressor().compress(raw)


def deserialize_state(blob: bytes) -> dict[str, UnitState]:
    raw = zstandard.ZstdDecompressor().decompress(blob)
    loaded: dict[str, UnitState] = json.loads(raw)
    return loaded


def compute_state_hash(state: Mapping[str, Any]) -> str:
    """完整狀態的 canonical hash（復原驗證 / golden replay 用）。"""
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    tick: int
    state: dict[str, UnitState]
    state_hash: str


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    restored: bool
    restored_tick: int | None
    events_after_checkpoint: int


@dataclass(frozen=True, slots=True)
class RollbackResult:
    restored: bool
    rolled_back_to_tick: int
    state_hash: str


@runtime_checkable
class Checkpointer(Protocol):
    """Kernel 依賴的 checkpoint 介面（每 N ticks 呼叫）。"""

    def checkpoint(self, session_id: str, tick: int, state: Mapping[str, UnitState]) -> None: ...


class CheckpointManager:
    """SimCheckpoint 讀寫。滿足 Checkpointer 介面。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def checkpoint(self, session_id: str, tick: int, state: Mapping[str, UnitState]) -> None:
        state_hash = compute_state_hash(state)
        blob = serialize_state(state)
        if len(blob) > MAX_CHECKPOINT_BYTES:
            raise CheckpointTooLargeError(
                f"壓縮後快照 {len(blob)} bytes 超過上限 {MAX_CHECKPOINT_BYTES}"
                f"（session={session_id} tick={tick}）；見 ADR 002 Phase 2 物件儲存路徑。"
            )
        with self._session_factory() as db:
            # (session, tick) 唯一：同 tick 重存則覆蓋（快照為冪等）
            db.execute(
                delete(SimCheckpoint).where(
                    (SimCheckpoint.session_id == session_id) & (SimCheckpoint.tick == tick)
                )
            )
            db.add(
                SimCheckpoint(
                    session_id=session_id,
                    tick=tick,
                    state_blob=blob,
                    state_hash=state_hash,
                )
            )
            db.commit()

    def load_latest(
        self, session_id: str, at_or_before_tick: int | None = None
    ) -> CheckpointRecord | None:
        with self._session_factory() as db:
            stmt = select(SimCheckpoint).where(SimCheckpoint.session_id == session_id)
            if at_or_before_tick is not None:
                stmt = stmt.where(SimCheckpoint.tick <= at_or_before_tick)
            stmt = stmt.order_by(SimCheckpoint.tick.desc()).limit(1)
            row = db.execute(stmt).scalars().first()
            if row is None:
                return None
            return CheckpointRecord(
                tick=row.tick,
                state=deserialize_state(row.state_blob),
                state_hash=row.state_hash,
            )

    def _count_events_after(self, db: Session, session_id: str, tick: int) -> int:
        stmt = select(func.count()).where(
            (TacticalEventLog.session_id == session_id) & (TacticalEventLog.tick > tick)
        )
        return int(db.execute(stmt).scalar_one())


def recover(
    session_factory: sessionmaker[Session],
    session_id: str,
    hot_state: HotStateStore,
) -> RecoveryResult:
    """由最近 checkpoint 還原熱狀態；回報 checkpoint 之後尚有多少 Ledger 事件待前滾。"""
    manager = CheckpointManager(session_factory)
    record = manager.load_latest(session_id)
    if record is None:
        return RecoveryResult(restored=False, restored_tick=None, events_after_checkpoint=0)
    hot_state.restore(record.state)
    with session_factory() as db:
        events_after = manager._count_events_after(db, session_id, record.tick)
    return RecoveryResult(
        restored=True,
        restored_tick=record.tick,
        events_after_checkpoint=events_after,
    )


def rollback(
    session_factory: sessionmaker[Session],
    ledger_writer: LedgerWriter,
    session_id: str,
    hot_state: HotStateStore,
    target_tick: int,
) -> RollbackResult:
    """還原到指定 checkpoint tick，並寫入 ROLLBACK Ledger 事件（append-only 歷史保留）。"""
    manager = CheckpointManager(session_factory)
    record = manager.load_latest(session_id, at_or_before_tick=target_tick)
    if record is None or record.tick != target_tick:
        raise ValueError(f"session {session_id} 無 tick={target_tick} 的 checkpoint 可回滾")
    hot_state.restore(record.state)
    ledger_writer.append(
        session_id,
        [
            LedgerEvent(
                event_type="ROLLBACK",
                tick=target_tick,
                ai_decision={"rolled_back_to": target_tick, "state_hash": record.state_hash},
            )
        ],
    )
    return RollbackResult(
        restored=True, rolled_back_to_tick=target_tick, state_hash=record.state_hash
    )
