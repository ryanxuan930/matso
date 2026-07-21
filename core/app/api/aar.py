"""AAR REST 端點（O8.1–O8.4，SPEC_FULL §14）——重播/統計/敘事/匯出。

存取：參與者、ANALYST（僅 AAR）、全知（統裁/管理）。其餘 → 403。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aar import read_events
from app.aar.export import export_csv, export_json
from app.aar.narrative import generate_narrative, verify_citations
from app.aar.replay import replay_summary
from app.aar.stats import compute_metrics
from app.api.deps import get_current_user, get_db
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError
from app.models import SessionParticipant, TacticalUnit
from app.models.enums import UserRole
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["aar"])


def require_aar_access(db: Session, user: CurrentUser, session_id: str) -> None:
    """AAR 存取：全知 / ANALYST / 本 session 參與者。其餘 → 403。"""
    if is_omniscient(user.role) or user.role is UserRole.ANALYST:
        return
    participant = db.execute(
        select(SessionParticipant).where(
            SessionParticipant.user_id == user.id,
            SessionParticipant.session_id == session_id,
        )
    ).scalar_one_or_none()
    if participant is None:
        raise AuthForbiddenError("無 AAR 存取權（非參與者/ANALYST/統裁）")


def _unit_faction(db: Session, session_id: str) -> dict[str, str]:
    rows = (
        db.execute(
            select(TacticalUnit.id, TacticalUnit.faction).where(
                TacticalUnit.session_id == session_id
            )
        )
        .tuples()
        .all()
    )
    return dict(rows)


@router.get("/{session_id}/aar/replay")
def get_replay(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[type-arg]
    require_aar_access(db, user, session_id)
    s = replay_summary(read_events(db, session_id))
    return {
        "frames": [{"tick": f.tick, "event_types": f.event_types} for f in s.frames],
        "bookmarks": [{"seq": b.seq, "tick": b.tick, "label": b.label} for b in s.bookmarks],
        "total_events": s.total_events,
        "max_tick": s.max_tick,
    }


@router.get("/{session_id}/aar/stats")
def get_stats(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[type-arg]
    require_aar_access(db, user, session_id)
    m = compute_metrics(read_events(db, session_id), _unit_faction(db, session_id))
    return {
        "total_events": m.total_events,
        "engagements": m.engagements,
        "hit_rate": m.hit_rate,
        "total_damage": m.total_damage,
        "guardrail_blocks": m.guardrail_blocks,
        "damage_by_faction": m.damage_by_faction,
        "event_counts": m.event_counts,
    }


@router.get("/{session_id}/aar/report")
def get_report(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[type-arg]
    require_aar_access(db, user, session_id)
    events = read_events(db, session_id)
    narrative = generate_narrative(events)
    invalid = verify_citations(narrative, events)
    return {
        "summary": narrative.summary,
        "paragraphs": [{"text": p.text, "cited_seqs": p.cited_seqs} for p in narrative.paragraphs],
        "lessons": narrative.lessons,
        "citations": {"valid": not invalid, "invalid_seqs": invalid},
    }


@router.get("/{session_id}/aar/export")
def get_export(
    session_id: str,
    fmt: str = Query("json", pattern="^(json|csv)$"),
    anonymize: bool = Query(False),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    require_aar_access(db, user, session_id)
    events = read_events(db, session_id)
    if fmt == "csv":
        return Response(export_csv(events, anonymize=anonymize), media_type="text/csv")
    return Response(export_json(events, anonymize=anonymize), media_type="application/json")
