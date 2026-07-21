"""WS 串流層（O4.3，SPEC §16.2）——重連補償 + faction 過濾 + 背壓。"""

from app.stream.backfill import ResumePlan, plan_resume, select_backfill, seq_range
from app.stream.faction_filter import OMNISCIENT_ROLES, is_omniscient, is_visible
from app.stream.identity import WsIdentity, resolve_ws_identity
from app.stream.sender import MAX_QUEUE, BackpressureError, BoundedSender

__all__ = [
    "MAX_QUEUE",
    "OMNISCIENT_ROLES",
    "BackpressureError",
    "BoundedSender",
    "ResumePlan",
    "WsIdentity",
    "is_omniscient",
    "is_visible",
    "plan_resume",
    "resolve_ws_identity",
    "select_backfill",
    "seq_range",
]
