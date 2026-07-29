"""C2 信文與申請-核覆 REST 端點（WP-B5.2）。

GET  /api/v1/sessions/{session_id}/messages              收信匣（本人可見）
POST /api/v1/sessions/{session_id}/messages              送信
GET  /api/v1/sessions/{session_id}/requests              申請單 + 配額用量
POST /api/v1/sessions/{session_id}/requests              送出申請
POST /api/v1/sessions/{session_id}/requests/{rid}/decide 核覆

**收信匣的可見性一律走 `stream.faction_filter.is_visible`**，不在這裡另寫一套判斷。
把每封信文攤成與 WS 相同形狀的 envelope 再交給它——WP-C5 的教訓就是同一套受眾規則
散在兩處實作，最後其中一處漏掉了 fog of war（STATE_DIFF 當時完全沒有受眾標籤）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_gateway, get_settings
from app.auth.schemas import CurrentUser
from app.c2.service import (
    decide_request,
    has_observer_on,
    quota_limits,
    quota_used,
    submit_request,
)
from app.cache import make_redis
from app.config import Settings
from app.errors import AuthForbiddenError, RequestNoObserverError, SessionNotFoundError
from app.models import SessionParticipant, User, WargameSession
from app.models.enums import MessageKind, RequestKind, SeatRole
from app.models.tables import Message, Request
from app.orders.precheck import PhysicsGateway
from app.stream.faction_filter import is_omniscient, is_visible
from app.stream.publish import publish_event

router = APIRouter(prefix="/api/v1/sessions", tags=["c2"])


class MessageView(BaseModel):
    id: str
    kind: MessageKind
    from_seat: SeatRole | None = None
    from_username: str = "?"
    to_seat: SeatRole | None = None
    to_faction: str = ""
    ref_id: str | None = None
    body: str = ""
    tick: int = 0


class SendMessageRequest(BaseModel):
    kind: MessageKind = MessageKind.FREE_TEXT
    to_seat: SeatRole | None = None
    to_faction: str | None = None
    ref_id: str | None = None
    body: str = Field(max_length=4000)


class RequestView(BaseModel):
    id: str
    kind: RequestKind
    status: str
    faction: str
    params: dict[str, Any] = {}
    requested_by: str = "?"
    requested_seat: SeatRole | None = None
    requested_at_tick: int = 0
    decided_by: str | None = None
    decided_at_tick: int | None = None
    decision_note: str | None = None


class QuotaView(BaseModel):
    kind: RequestKind
    limit: int | None = None
    used: int = 0


class RequestListView(BaseModel):
    requests: list[RequestView]
    quotas: list[QuotaView]


class SubmitRequestRequest(BaseModel):
    kind: RequestKind
    params: dict[str, Any] = {}
    note: str = Field(default="", max_length=2000)


class DecideRequestRequest(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=2000)


def _participant(db: Session, session_id: str, user: CurrentUser) -> SessionParticipant | None:
    return db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.user_id == user.id,
        )
    )


def _require_member(
    db: Session, session_id: str, user: CurrentUser
) -> tuple[SessionParticipant | None, bool]:
    """回 (參與者記錄, 是否全知)。非參與者且非全知 → 403。"""
    if db.get(WargameSession, session_id) is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")
    omniscient = is_omniscient(user.role)
    part = _participant(db, session_id, user)
    if part is None and not omniscient:
        raise AuthForbiddenError("非本局參與者")
    return part, omniscient


def _live_tick(db: Session, session_id: str) -> int:
    """信文/申請單戳記的 sim tick。以 Ledger 已記錄的最後一筆為準（無活模擬時為 0）。"""
    from app.models.tables import TacticalEventLog

    row = db.scalar(
        select(TacticalEventLog.tick)
        .where(TacticalEventLog.session_id == session_id)
        .order_by(TacticalEventLog.seq.desc())
        .limit(1)
    )
    return int(row) if row is not None else 0


def _msg_envelope(m: Message) -> dict[str, Any]:
    """把信文攤成與 WS 相同形狀的受眾 envelope，交給同一個 `is_visible` 判定。"""
    env: dict[str, Any] = {"faction": m.to_faction}
    if m.to_seat is not None:
        env["seat"] = m.to_seat.value
    return env


def _push(
    settings: Settings,
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    faction: str,
    seat: str | None,
) -> None:
    """WS 即時推播（WP-B5.2）。受眾＝陣營 + 可選席位，**過濾在後端**（紅線 3）。

    推播失敗（redis 不可達）不讓 API 失敗——信文已落庫，重新開面板仍讀得到。
    """
    import contextlib

    with contextlib.suppress(Exception):
        publish_event(
            make_redis(settings.redis_url),
            session_id,
            event_type,
            payload,
            faction=faction,
            seat=seat,
        )


def _username(db: Session, user_id: str | None) -> str:
    if not user_id:
        return "?"
    u = db.get(User, user_id)
    return u.username if u is not None else "?"


@router.get("/{session_id}/messages", response_model=list[MessageView])
def list_messages(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MessageView]:
    part, omniscient = _require_member(db, session_id, user)
    faction = part.faction if part is not None else ""
    seat = part.seat_role.value if part is not None and part.seat_role is not None else None
    rows = db.scalars(
        select(Message).where(Message.session_id == session_id).order_by(Message.tick)
    ).all()
    out: list[MessageView] = []
    for m in rows:
        # 自己寄出的一定看得到；其餘走與 WS 同一套受眾判定。
        mine = part is not None and m.from_user_id == part.user_id
        if not mine and not is_visible(_msg_envelope(m), faction, omniscient, seat):
            continue
        out.append(
            MessageView(
                id=m.id,
                kind=m.kind,
                from_seat=m.from_seat,
                from_username=_username(db, m.from_user_id),
                to_seat=m.to_seat,
                to_faction=m.to_faction,
                ref_id=m.ref_id,
                body=m.body,
                tick=m.tick,
            )
        )
    return out


@router.post(
    "/{session_id}/messages", response_model=MessageView, status_code=status.HTTP_201_CREATED
)
def send_message(
    session_id: str,
    req: SendMessageRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageView:
    part, _ = _require_member(db, session_id, user)
    if part is None:
        raise AuthForbiddenError("全知角色未加入本局，無法以席位身分發信")
    m = Message(
        session_id=session_id,
        kind=req.kind,
        from_user_id=part.user_id,
        from_seat=part.seat_role,
        to_seat=req.to_seat,
        to_faction=req.to_faction or part.faction,
        ref_id=req.ref_id,
        body=req.body,
        tick=_live_tick(db, session_id),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    _push(
        settings,
        session_id,
        "C2_MESSAGE",
        {"message_id": m.id, "kind": m.kind.value},
        m.to_faction,
        m.to_seat.value if m.to_seat is not None else None,
    )
    return MessageView(
        id=m.id,
        kind=m.kind,
        from_seat=m.from_seat,
        from_username=_username(db, m.from_user_id),
        to_seat=m.to_seat,
        to_faction=m.to_faction,
        ref_id=m.ref_id,
        body=m.body,
        tick=m.tick,
    )


def _req_view(db: Session, r: Request) -> RequestView:
    return RequestView(
        id=r.id,
        kind=r.kind,
        status=r.status.value,
        faction=r.faction,
        params=dict(r.params or {}),
        requested_by=_username(db, r.requested_by_id),
        requested_seat=r.requested_seat,
        requested_at_tick=r.requested_at_tick,
        decided_by=_username(db, r.decided_by_id) if r.decided_by_id else None,
        decided_at_tick=r.decided_at_tick,
        decision_note=r.decision_note,
    )


@router.get("/{session_id}/requests", response_model=RequestListView)
def list_requests(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestListView:
    part, omniscient = _require_member(db, session_id, user)
    stmt = select(Request).where(Request.session_id == session_id)
    if not omniscient and part is not None:
        stmt = stmt.where(Request.faction == part.faction)  # 陣營過濾在後端（紅線 3）
    rows = db.scalars(stmt.order_by(Request.requested_at_tick)).all()
    limits = quota_limits(db, session_id)
    faction = part.faction if part is not None else ""
    quotas = [
        QuotaView(
            kind=k,
            limit=limits.get(k.value),
            used=quota_used(db, session_id, faction, k) if faction else 0,
        )
        for k in RequestKind
    ]
    return RequestListView(requests=[_req_view(db, r) for r in rows], quotas=quotas)


@router.post(
    "/{session_id}/requests", response_model=RequestView, status_code=status.HTTP_201_CREATED
)
def post_request(
    session_id: str,
    req: SubmitRequestRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    gateway: PhysicsGateway = Depends(get_gateway),
) -> RequestView:
    part, _ = _require_member(db, session_id, user)
    if part is None:
        raise AuthForbiddenError("全知角色未加入本局，無法以席位身分送出申請")
    # 臨機火力（WP-C10.1）：**沒有觀測就不能叫火力**（[JCATS-F p.12] 觀測所的角色）。
    # 擋在送出端而非核覆端：FSO 看到的申請單都該是觀測上成立的，
    # 不成立的擋在更前面才不會浪費核覆者的注意力。
    if req.kind is RequestKind.CALL_FOR_FIRE:
        lat, lng = req.params.get("target_lat"), req.params.get("target_lng")
        if not isinstance(lat, int | float) or not isinstance(lng, int | float):
            raise RequestNoObserverError("臨機火力申請須指定目標座標（target_lat/target_lng）")
        if not has_observer_on(db, session_id, part.faction, (float(lat), float(lng)), gateway):
            raise RequestNoObserverError(
                "本陣營無任何單位對該目標有視線——沒有觀測就叫不動火力",
                details={"target_lat": float(lat), "target_lng": float(lng)},
            )
    r = submit_request(
        db,
        session_id,
        part,
        kind=req.kind,
        params=req.params,
        note=req.note,
        tick=_live_tick(db, session_id),
    )
    # 申請送到核覆者席位（COMMANDER），與服務層生成的 REQUEST 信文同一受眾。
    _push(settings, session_id, "C2_REQUEST", {"request_id": r.id}, r.faction, "COMMANDER")
    return _req_view(db, r)


@router.post("/{session_id}/requests/{rid}/decide", response_model=RequestView)
def post_decide(
    session_id: str,
    rid: str,
    req: DecideRequestRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RequestView:
    part, _ = _require_member(db, session_id, user)
    if part is None:
        raise AuthForbiddenError("全知角色未加入本局，無法核覆")
    r = decide_request(
        db,
        session_id,
        part,
        user.role,
        rid,
        approve=req.approve,
        note=req.note,
        tick=_live_tick(db, session_id),
    )
    # 申請送到核覆者席位（COMMANDER），與服務層生成的 REQUEST 信文同一受眾。
    _push(settings, session_id, "C2_REQUEST", {"request_id": r.id}, r.faction, "COMMANDER")
    return _req_view(db, r)
