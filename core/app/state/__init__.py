"""狀態層：Event Ledger（不可變帳本）、Redis 熱狀態、checkpoint。

Kernel 是 Redis 熱狀態的唯一寫入者（SPEC_FULL §3.4）；Ledger 為 append-only（§15.3）。
"""

from app.errors import CheckpointTooLargeError, RollbackTargetNotFoundError
from app.state.broadcaster import (
    RING_CAPACITY,
    CollectingBroadcaster,
    RedisBroadcaster,
    build_state_diff_envelope,
)
from app.state.checkpoint import (
    MAX_CHECKPOINT_BYTES,
    Checkpointer,
    CheckpointManager,
    CheckpointRecord,
    RecoveryResult,
    RollbackResult,
    build_snapshot,
    compute_state_hash,
    deserialize_state,
    recover,
    rollback,
    serialize_state,
    split_snapshot,
)
from app.state.hot_state import (
    HotStateStore,
    InMemoryHotState,
    RedisHotState,
    SessionDiff,
    UnitDiff,
    UnitState,
    compute_diff,
)
from app.state.ledger import (
    GENESIS_HASH,
    LedgerEvent,
    LedgerWriter,
    VerifyResult,
    canonical_json,
    compute_self_hash,
    verify_chain,
)
from app.state.resume import ResumeResult, forward_roll, resume_session, resume_tick

__all__ = [
    "GENESIS_HASH",
    "MAX_CHECKPOINT_BYTES",
    "RING_CAPACITY",
    "CheckpointManager",
    "CheckpointRecord",
    "CheckpointTooLargeError",
    "Checkpointer",
    "CollectingBroadcaster",
    "HotStateStore",
    "InMemoryHotState",
    "LedgerEvent",
    "LedgerWriter",
    "RecoveryResult",
    "RedisBroadcaster",
    "RedisHotState",
    "ResumeResult",
    "RollbackResult",
    "RollbackTargetNotFoundError",
    "SessionDiff",
    "UnitDiff",
    "UnitState",
    "VerifyResult",
    "build_snapshot",
    "build_state_diff_envelope",
    "canonical_json",
    "compute_diff",
    "compute_self_hash",
    "compute_state_hash",
    "deserialize_state",
    "forward_roll",
    "recover",
    "resume_session",
    "resume_tick",
    "rollback",
    "serialize_state",
    "split_snapshot",
    "verify_chain",
]
