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
from app.aar.replay import replay_summary, state_frames
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


@router.get("/{session_id}/aar/replay/states")
def get_replay_states(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[type-arg]
    """地圖重播的狀態流（WP-D6.1）：靜態底本 + 逐 tick 差異，整場一次給。

    **tick 0 基準位置是近似值，這是帳本的限制不是實作偷懶**：帳本裡沒有「部署」事件，
    白軍在「地圖狀態編輯」拖動單位也不落帳（`reposition_unit` 只寫 DB + 命令通道）。
    故基準取法為：
      * 該單位在帳本中**最早一筆有座標的事件** → 用它（離 tick 0 最近的已記錄真相；
        誤差最多一個移動步長，且僅影響它第一次移動之前的畫面）；
      * 完全沒有座標事件（從沒動過）→ 用 DB 現值（沒動過＝現值即初始，精確）。
    """
    require_aar_access(db, user, session_id)
    events = read_events(db, session_id)

    rows = (
        db.execute(
            select(
                TacticalUnit.id,
                TacticalUnit.designation,
                TacticalUnit.faction,
                TacticalUnit.unit_level,
                TacticalUnit.is_fixed,
                TacticalUnit.authorized_strength,
                TacticalUnit.current_lat,
                TacticalUnit.current_lng,
            ).where(TacticalUnit.session_id == session_id)
        )
        .tuples()
        .all()
    )
    authorized = {r[0]: float(r[5]) for r in rows if r[5] is not None}
    frames = state_frames(events, authorized)

    # 每個單位最早一筆有座標的事件（見 docstring 的基準取法）。
    first_pos: dict[str, tuple[float, float]] = {}
    for e in events:
        src = e.detail if ("lat" in e.detail and "lng" in e.detail) else e.ai_decision
        if e.initiator_id and e.initiator_id not in first_pos and "lat" in src and "lng" in src:
            first_pos[e.initiator_id] = (float(src["lat"]), float(src["lng"]))

    units = []
    for uid, designation, faction, unit_level, is_fixed, auth, cur_lat, cur_lng in rows:
        base = first_pos.get(uid)
        units.append(
            {
                "id": uid,
                "designation": designation,
                "faction": faction,
                "unit_level": unit_level.value,
                "is_fixed": bool(is_fixed),
                "authorized_strength": float(auth) if auth is not None else None,
                "base_lat": base[0] if base else (float(cur_lat) if cur_lat is not None else None),
                "base_lng": base[1] if base else (float(cur_lng) if cur_lng is not None else None),
                "base_health": 100.0,
            }
        )
    return {
        "units": units,
        "frames": [
            {
                "tick": f.tick,
                "event_types": f.event_types,
                "changes": [
                    {
                        k: v
                        for k, v in (
                            ("unit_id", c.unit_id),
                            ("lat", c.lat),
                            ("lng", c.lng),
                            ("health", c.health),
                            ("strength", c.strength),
                        )
                        if v is not None
                    }
                    for c in f.changes
                ],
            }
            for f in frames
        ],
        "max_tick": frames[-1].tick if frames else 0,
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
