"""狀態檢查點、回滾與崩潰復原（SPEC_FULL §3.4、§18；儲存策略見 ADR 002）。

- Checkpoint：每 N ticks 把完整單位熱狀態 zstd 壓縮存入 SimCheckpoint（inline LONGBLOB），
  並記錄快照當下的 ledger tip seq（`ledgerSeq`）。
- Recover：崩潰（Redis 清空）後，由「最近」checkpoint 還原熱狀態。
- Rollback：還原到指定 checkpoint、**刪除較晚的 checkpoint**、寫入 ROLLBACK Ledger 事件。

時間軸身分（O1.7/R2/R3）：rollback 後 ledger 的 tick 非單調（新世代事件的 tick 會重複），
**單調的 ledger seq 才是時間軸身分**——「最近的 checkpoint」依 ledgerSeq 排序，
「checkpoint 之後的事件」以 seq 計數。checkpoint 是狀態快取而非證據（證據在 append-only
ledger），故 rollback 刪除被棄世代的快照是正當的；tick ≤ target 的舊 checkpoint 是
新舊世代的共同前綴，保留。

**復原範圍**：本模組保證「還原到 checkpoint 當下」。checkpoint 之後的 Ledger 尾段
（mid-interval 崩潰）由 `app.state.resume` 以事件投影前滾。recover 回傳
events_after_checkpoint 供上層判斷是否需前滾。

**快照內容版本（WP-E1）**：blob 自 v2 起是一個信封
`{"v": 2, "units": {...}, "rng": {...}, "orders": {...}}`——除熱狀態外一併保存
RNG 各 stream 的位置（否則重啟後會重播同一段隨機序列）與 Order 狀態（rollback 要能
把「已裁決」的令退回未裁決）。v1（裸的 `{unit_id: state}`）仍可讀，由 `_split_snapshot`
以「有沒有 `v` 這個整數鍵」辨識——單位狀態一律是 dict，故不可能與之混淆。

`stateHash` **只涵蓋 units 子樹**，不含信封：驗收比對的「狀態雜湊」與 golden replay 的
`compute_state_hash(hot_state.get_all())` 必須是同一個東西，把 RNG/Order 混進去會讓兩者脫鉤。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

import zstandard
from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.errors import CheckpointTooLargeError, RollbackTargetNotFoundError
from app.models import Order, SimCheckpoint, TacticalEventLog
from app.state.hot_state import HotStateStore, UnitState
from app.state.ledger import LedgerEvent, LedgerWriter, canonical_json

# ADR 002：壓縮後快照大小護欄，安全低於 MariaDB max_allowed_packet（16MB）。
MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024

# 快照信封版本（WP-E1）。v1 = 裸的 {unit_id: state}；v2 = {"v":2,"units":…,"rng":…,"orders":…}
SNAPSHOT_VERSION = 2
_RESERVED_KEYS = frozenset({"v", "units"})

_COMPRESSOR = zstandard.ZstdCompressor()
_DECOMPRESSOR = zstandard.ZstdDecompressor()

_LOG = logging.getLogger("app.checkpoint")


def serialize_state(state: Mapping[str, Any]) -> bytes:
    """完整狀態 → canonical JSON → zstd 壓縮 bytes。"""
    raw = canonical_json(state).encode("utf-8")
    return _COMPRESSOR.compress(raw)


def deserialize_state(blob: bytes) -> dict[str, Any]:
    raw = _DECOMPRESSOR.decompress(blob)
    loaded: dict[str, Any] = json.loads(raw)
    return loaded


def compute_state_hash(state: Mapping[str, Any]) -> str:
    """完整狀態的 canonical hash（復原驗證 / golden replay 用）。"""
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


def build_snapshot(
    units: Mapping[str, UnitState], extras: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """組 v2 信封。`extras` 的保留鍵（v/units）會被忽略——呼叫端不得覆寫骨架。"""
    envelope: dict[str, Any] = {"v": SNAPSHOT_VERSION, "units": dict(units)}
    for key, value in (extras or {}).items():
        if key not in _RESERVED_KEYS:
            envelope[key] = value
    return envelope


def split_snapshot(loaded: Mapping[str, Any]) -> tuple[dict[str, UnitState], dict[str, Any]]:
    """信封 → (units, extras)。**v1 的裸 units map 也吃**（既有快照不需遷移）。

    辨識法：頂層有整數的 `v` 鍵才是信封。單位狀態一律是 dict，故不可能有單位叫 `v`
    而值是整數——這個判別不會誤判。
    """
    if not isinstance(loaded.get("v"), int):
        return dict(loaded), {}
    units = loaded.get("units")
    return (
        dict(units) if isinstance(units, dict) else {},
        {k: v for k, v in loaded.items() if k not in _RESERVED_KEYS},
    )


def orders_snapshot(db: Session, session_id: str) -> dict[str, Any]:
    """本局所有 Order 的狀態快照——rollback 要能把「已裁決」的令退回未裁決。

    指令本身早已持久化於 DB（`EngageOrderSource` 每 tick 從 DB 撈、就地改 status），
    所以**這裡存的不是「pending queue」而是「當時的狀態」**：崩潰復原不需要它（DB 就是
    權威），但回滾需要——否則回到 tick T 後，T 之後被打完的交戰令仍是 COMPLETED，
    那場交戰再也不會發生。
    """
    rows = db.scalars(select(Order).where(Order.session_id == session_id)).all()
    return {
        row.id: {
            "status": str(row.status.value if hasattr(row.status, "value") else row.status),
            "payload": row.payload or {},
            "issued_at_tick": int(row.issued_at_tick or 0),
            "resolved_at_tick": row.resolved_at_tick,
        }
        for row in rows
    }


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    tick: int
    ledger_seq: int
    state: dict[str, UnitState]
    state_hash: str
    # v2 信封的非 units 區段（rng / orders）。v1 快照為空 dict。
    extras: dict[str, Any] = field(default_factory=dict)

    def rng_states(self) -> dict[str, Any]:
        raw = self.extras.get("rng")
        return dict(raw) if isinstance(raw, dict) else {}

    def order_states(self) -> dict[str, Any]:
        raw = self.extras.get("orders")
        return dict(raw) if isinstance(raw, dict) else {}


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    restored: bool
    restored_tick: int | None
    restored_ledger_seq: int | None
    events_after_checkpoint: int


@dataclass(frozen=True, slots=True)
class RollbackResult:
    restored: bool
    rolled_back_to_tick: int
    state_hash: str
    checkpoints_discarded: int


@runtime_checkable
class Checkpointer(Protocol):
    """Kernel 依賴的 checkpoint 介面（每 N ticks 呼叫）。ledger_seq = 快照當下鏈尾 seq。"""

    def checkpoint(
        self, session_id: str, tick: int, state: Mapping[str, UnitState], ledger_seq: int
    ) -> None: ...


class CheckpointManager:
    """SimCheckpoint 讀寫。滿足 Checkpointer 介面。

    `extras_provider`（WP-E1）：回傳要一併存入信封的執行期狀態，目前是 `{"rng": {...}}`。
    刻意用注入而非擴充 `Checkpointer` 介面——RNG 實例的持有者是 runner（子系統把它們
    藏成私有欄位，Kernel 拿不到），而擴介面會一路改到 Kernel 與 golden harness。
    Order 狀態不走 provider：它要與寫入同一條 DB session 取樣才一致，故由本類別自取。
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        extras_provider: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._extras_provider = extras_provider

    def _extras(self) -> dict[str, Any]:
        if self._extras_provider is None:
            return {}
        try:
            return dict(self._extras_provider())
        except Exception:
            # 拿不到 RNG 狀態不該讓整個快照失敗——少了它仍能復原熱狀態。
            _LOG.exception("checkpoint extras_provider 失敗，本次快照不含執行期狀態")
            return {}

    def checkpoint(
        self, session_id: str, tick: int, state: Mapping[str, UnitState], ledger_seq: int
    ) -> None:
        state_hash = compute_state_hash(state)  # 只 hash units（見模組 docstring）
        extras = self._extras()
        with self._session_factory() as db:
            extras["orders"] = orders_snapshot(db, session_id)
        blob = serialize_state(build_snapshot(state, extras))
        if len(blob) > MAX_CHECKPOINT_BYTES:
            raise CheckpointTooLargeError(
                f"壓縮後快照 {len(blob)} bytes 超過上限 {MAX_CHECKPOINT_BYTES}"
                f"（session={session_id} tick={tick}）；見 ADR 002 Phase 2 物件儲存路徑。"
            )
        with self._session_factory() as db:
            # (session, tick) 唯一：同 tick 重存則覆蓋（含 rollback 後新世代重跑到同一 tick）
            db.execute(
                delete(SimCheckpoint).where(
                    (SimCheckpoint.session_id == session_id) & (SimCheckpoint.tick == tick)
                )
            )
            db.add(
                SimCheckpoint(
                    session_id=session_id,
                    tick=tick,
                    ledger_seq=ledger_seq,
                    state_blob=blob,
                    state_hash=state_hash,
                )
            )
            db.commit()

    def load_latest(self, session_id: str) -> CheckpointRecord | None:
        """最近的 checkpoint——依 ledgerSeq（單調）排序，非 tick（rollback 後非單調）。"""
        with self._session_factory() as db:
            stmt = (
                select(SimCheckpoint)
                .where(SimCheckpoint.session_id == session_id)
                .order_by(SimCheckpoint.ledger_seq.desc())
                .limit(1)
            )
            return _to_record(db.execute(stmt).scalars().first())

    def load_at_tick(self, session_id: str, tick: int) -> CheckpointRecord | None:
        """指定 tick 的 checkpoint（(session, tick) 唯一）。rollback 目標查找用。"""
        with self._session_factory() as db:
            stmt = select(SimCheckpoint).where(
                (SimCheckpoint.session_id == session_id) & (SimCheckpoint.tick == tick)
            )
            return _to_record(db.execute(stmt).scalars().first())


def _to_record(row: SimCheckpoint | None) -> CheckpointRecord | None:
    if row is None:
        return None
    units, extras = split_snapshot(deserialize_state(row.state_blob))
    return CheckpointRecord(
        tick=row.tick,
        ledger_seq=row.ledger_seq,
        state=units,
        state_hash=row.state_hash,
        extras=extras,
    )


def _count_events_after_seq(db: Session, session_id: str, seq: int) -> int:
    """checkpoint 之後的事件數——以單調的 seq 計數（tick 在 rollback 後不可靠）。"""
    stmt = select(func.count()).where(
        (TacticalEventLog.session_id == session_id) & (TacticalEventLog.seq > seq)
    )
    return int(db.execute(stmt).scalar_one())


def recover(
    session_factory: sessionmaker[Session],
    session_id: str,
    hot_state: HotStateStore,
    transport_reset: Callable[[], None] | None = None,
) -> RecoveryResult:
    """由最近 checkpoint（依 ledgerSeq）還原熱狀態。

    transport_reset：復原時一併重置廣播傳輸層（ring buffer / broadcast seq，
    見 RedisBroadcaster.reset_stream）——Redis 清空後這些 key 已死，殘留半份會
    誤導重連客戶端（O1.7/R7）。
    """
    manager = CheckpointManager(session_factory)
    record = manager.load_latest(session_id)
    if record is None:
        return RecoveryResult(
            restored=False, restored_tick=None, restored_ledger_seq=None, events_after_checkpoint=0
        )
    hot_state.restore(record.state)
    if transport_reset is not None:
        transport_reset()
    with session_factory() as db:
        events_after = _count_events_after_seq(db, session_id, record.ledger_seq)
    return RecoveryResult(
        restored=True,
        restored_tick=record.tick,
        restored_ledger_seq=record.ledger_seq,
        events_after_checkpoint=events_after,
    )


def rollback(
    session_factory: sessionmaker[Session],
    ledger_writer: LedgerWriter,
    session_id: str,
    hot_state: HotStateStore,
    target_tick: int,
) -> RollbackResult:
    """還原到指定 checkpoint tick，刪除較晚的 checkpoint，寫入 ROLLBACK Ledger 事件。

    - Ledger 為 append-only 證據，完整保留（含被棄世代的事件）；
      checkpoint 是狀態快取，被棄世代的快照必須刪除，否則之後的 recover 會
      復活被回滾的狀態（O1.7/R2）。
    - ROLLBACK 中繼資料寫入 `detail`（非證據性診斷欄，不入 hash chain）。
    """
    manager = CheckpointManager(session_factory)
    record = manager.load_at_tick(session_id, target_tick)
    if record is None:
        raise RollbackTargetNotFoundError(
            f"session {session_id} 無 tick={target_tick} 的 checkpoint 可回滾"
        )
    with session_factory() as db:
        result = cast(
            "CursorResult[Any]",
            db.execute(
                delete(SimCheckpoint).where(
                    (SimCheckpoint.session_id == session_id)
                    & (SimCheckpoint.ledger_seq > record.ledger_seq)
                )
            ),
        )
        db.commit()
        discarded = int(result.rowcount or 0)
    hot_state.restore(record.state)
    ledger_writer.append(
        session_id,
        [
            LedgerEvent(
                event_type="ROLLBACK",
                tick=target_tick,
                detail={
                    "rolled_back_to": target_tick,
                    "state_hash": record.state_hash,
                    "checkpoints_discarded": discarded,
                },
            )
        ],
    )
    return RollbackResult(
        restored=True,
        rolled_back_to_tick=target_tick,
        state_hash=record.state_hash,
        checkpoints_discarded=discarded,
    )
