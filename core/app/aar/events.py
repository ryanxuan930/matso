"""Ledger 事件讀取 + AAR 視圖（SPEC_FULL §14.1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TacticalEventLog
from app.state.ledger import superseded_seqs


@dataclass(frozen=True, slots=True)
class AarEvent:
    """AAR 用的事件視圖（Ledger 投影）。"""

    seq: int
    tick: int
    event_type: str
    initiator_id: str | None
    target_id: str | None
    ai_decision: dict[str, Any] = field(default_factory=dict)
    damage_calc: float | None = None
    reasoning_chain: str | None = None


def _to_aar(row: TacticalEventLog) -> AarEvent:
    return AarEvent(
        seq=row.seq,
        tick=row.tick,
        event_type=row.event_type,
        initiator_id=row.initiator_id,
        target_id=row.target_id,
        ai_decision=dict(row.ai_decision or {}),
        damage_calc=row.damage_calc,
        reasoning_chain=row.reasoning_chain,
    )


def read_events(db: Session, session_id: str) -> list[AarEvent]:
    """依 seq 讀取 session 的 Ledger 事件（append-only，順序即真相）。

    **排除被白軍回滾棄置的世代**（WP-E1／ADR 007）：那些事件仍在帳本裡（證據不刪），
    但它們描述的是一條已經不存在的時間軸——把它們算進 AAR 會讓戰損統計重複計算、
    敘事出現「發生過又沒發生」的段落。稽核要看完整歷史請直接查 `TacticalEventLog`。
    """
    rows = (
        db.execute(
            select(TacticalEventLog)
            .where(TacticalEventLog.session_id == session_id)
            .order_by(TacticalEventLog.seq)
        )
        .scalars()
        .all()
    )
    dead = superseded_seqs(rows)
    return [_to_aar(r) for r in rows if r.seq not in dead]
