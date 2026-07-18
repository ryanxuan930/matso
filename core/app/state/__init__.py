"""狀態層：Event Ledger（不可變帳本）、Redis 熱狀態、checkpoint。

Kernel 是 Redis 熱狀態的唯一寫入者（SPEC_FULL §3.4）；Ledger 為 append-only（§15.3）。
"""

from app.state.broadcaster import (
    RING_CAPACITY,
    CollectingBroadcaster,
    RedisBroadcaster,
    build_state_diff_envelope,
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

__all__ = [
    "GENESIS_HASH",
    "RING_CAPACITY",
    "CollectingBroadcaster",
    "HotStateStore",
    "InMemoryHotState",
    "LedgerEvent",
    "LedgerWriter",
    "RedisBroadcaster",
    "RedisHotState",
    "SessionDiff",
    "UnitDiff",
    "UnitState",
    "VerifyResult",
    "build_state_diff_envelope",
    "canonical_json",
    "compute_diff",
    "compute_self_hash",
    "verify_chain",
]
