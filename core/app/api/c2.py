"""C2 信文與申請-核覆 REST 端點（WP-B5.2）。

GET  /api/v1/sessions/{session_id}/messages              收信匣（本人可見）
POST /api/v1/sessions/{session_id}/messages              送信
POST /api/v1/sessions/{session_id}/messages/read         標示已讀（收件方）
GET  /api/v1/sessions/{session_id}/requests              申請單 + 配額用量
POST /api/v1/sessions/{session_id}/requests              送出申請
POST /api/v1/sessions/{session_id}/requests/{rid}/decide 核覆

**收信匣的可見性一律走 `stream.faction_filter.is_visible`**，不在這裡另寫一套判斷。
把每封信文攤成與 WS 相同形狀的 envelope 再交給它——WP-C5 的教訓就是同一套受眾規則
散在兩處實作，最後其中一處漏掉了 fog of war（STATE_DIFF 當時完全沒有受眾標籤）。
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
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
from app.db import default_session_factory
from app.errors import (
    AuthForbiddenError,
    FactionInvalidError,
    RequestNoObserverError,
    SessionNotFoundError,
)
from app.factions import WHITE_CELL, validate_faction_id
from app.factions.session_store import load_session_relations
from app.models import SessionParticipant, User, WargameSession
from app.models.enums import MessageKind, RequestKind, SeatRole, UserRole
from app.models.tables import Message, Request, TacticalUnit
from app.orders.precheck import PhysicsGateway
from app.state.ledger import LedgerEvent, LedgerWriter
from app.stream.faction_filter import is_omniscient, is_visible, is_white_cell
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
    # 已讀時戳。**DB（Message.readAt）與契約（MessageView.read_at）本來就有這一欄，
    # 只有這個 view 漏掉**，而且沒有任何端點寫得進去——於是信文永遠是未讀，
    # 寄件者看不出下級到底收到沒有。牆鐘時戳（非 sim tick）：這是操作員何時看到，
    # 不是戰場上第幾分鐘，兩者不可混用。
    read_at: datetime | None = None


class SendMessageRequest(BaseModel):
    kind: MessageKind = MessageKind.FREE_TEXT
    to_seat: SeatRole | None = None
    to_faction: str | None = None
    ref_id: str | None = None
    body: str = Field(max_length=4000)


class MarkReadRequest(BaseModel):
    """標示已讀。`message_ids` 省略/null＝把所有「寄給我且未讀」的信文一次標掉。"""

    message_ids: list[str] | None = None


class MarkReadResult(BaseModel):
    """**回報實際標到哪幾封**——不是回 204 了事。

    被跳過的（已讀過、非本人收件、自己寄的）不會出現在 marked 裡，呼叫端據此知道
    「我按了但沒生效」，而不是以為成功了卻什麼都沒變。
    """

    marked: list[str] = []
    read_at: datetime | None = None


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


def _live_units(settings: Settings, db: Session, session_id: str, faction: str) -> dict[str, Any]:
    """該陣營單位的熱狀態切片（單次 MGET）。Redis 不可達 → 空 dict（呼叫端退回 DB 列）。"""
    from app.models.tables import TacticalUnit
    from app.state.comms_view import load_comms_view

    ids = list(
        db.scalars(
            select(TacticalUnit.id).where(
                TacticalUnit.session_id == session_id,
                TacticalUnit.faction == faction,
            )
        ).all()
    )
    if not ids:
        return {}
    try:
        return dict(load_comms_view(make_redis(settings.redis_url), session_id, ids).units)
    except Exception:
        return {}


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


def _is_addressee(m: Message, part: SessionParticipant) -> bool:
    """這封信是不是**真的寄給這個人**——刻意不吃全知旁通。

    已讀是「收件方看過了」的事實陳述，AAR 會拿它重建事件鏈。統裁靠全知旁通看得到全場信文，
    若也算收件方，他一開面板就會把所有人的信標成已讀，留痕當場失真。
    """
    seat = part.seat_role.value if part.seat_role is not None else None
    return is_visible(_msg_envelope(m), part.faction, False, seat)


def _session_factions(db: Session, session_id: str) -> set[str]:
    """本局實際存在的陣營（單位 + 參與者）＋ 統裁保留字。供跨陣營發信驗目標。"""
    unit_f = db.scalars(
        select(TacticalUnit.faction).where(TacticalUnit.session_id == session_id).distinct()
    ).all()
    part_f = db.scalars(
        select(SessionParticipant.faction)
        .where(SessionParticipant.session_id == session_id)
        .distinct()
    ).all()
    return {f for f in (*unit_f, *part_f) if f} | {WHITE_CELL}


def _resolve_to_faction(
    db: Session,
    session_id: str,
    part: SessionParticipant,
    role: UserRole,
    requested: str | None,
) -> str:
    """決定收件陣營。省略/同陣營＝寄件者自己的陣營（既有行為不變）。

    **跨陣營發信只有白軍/統裁能做。**收信匣的可見性只看 `to_faction`，所以這個欄位
    等於「把信直接投進哪個陣營的信文匣」——原本無條件採用請求值，任一陣營的指揮官
    都能對敵軍發信（甚至冒充敵方參謀往來）。ADMIN 不在此列：跨陣營投信是統裁的注入
    行為，與 `inject`/`control` 同一條線（那兩處也是 `is_white_cell`），
    ADMIN 是系統管理不是統裁。

    目標陣營必須真的存在於本局：打錯字的話信會落在一個沒有人收得到的陣營——
    送出 201、對方永遠看不到，正是「存得進去、讀得回來、實際沒效果」那種病。
    """
    if not requested or requested == part.faction:
        return part.faction
    if not is_white_cell(role):
        raise AuthForbiddenError(
            "跨陣營發信限白軍/統裁",
            details={"to_faction": requested, "my_faction": part.faction},
        )
    validate_faction_id(requested)
    known = _session_factions(db, session_id)
    if requested not in known:
        raise FactionInvalidError(
            f"本局沒有這個陣營：{requested}",
            details={"to_faction": requested, "known": sorted(known)},
        )
    return requested


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


def _msg_view(db: Session, m: Message) -> MessageView:
    """信文列 → view。**只此一處組裝**（申請單那側的 `_req_view` 同理）。

    原本 list/send 各自手寫一份欄位對應，兩處會漂：`read_at` 就是這樣加在 model 與契約上、
    卻沒人把它接進 view 的。多一個組裝點就多一個會漏欄位的地方。
    """
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
        read_at=m.read_at,
    )


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
        out.append(_msg_view(db, m))
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
        # 收件陣營的決定權集中在 `_resolve_to_faction`（含跨陣營的白軍閘門）。
        to_faction=_resolve_to_faction(db, session_id, part, user.role, req.to_faction),
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
    return _msg_view(db, m)


@router.post("/{session_id}/messages/read", response_model=MarkReadResult)
def mark_messages_read(
    session_id: str,
    req: MarkReadRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MarkReadResult:
    """把指定（或全部）寄給本人的信文標為已讀。

    在此之前 `Message.readAt` 沒有任何寫入端——欄位在 DB、在契約，就是沒有人寫得進去，
    所以「已讀」這件事在系統裡從來沒有發生過。

    三條規則，都不是可有可無的：
    1. **首次已讀為準**：已有時戳就不覆寫。AAR 要問的是「第一次被看到是什麼時候」。
    2. **寄件備份不算已讀**：寄件者的信自己一定看得見，若也計入，每封信送出即已讀。
    3. **必須是真收件方**（`_is_addressee`，不吃全知旁通）：統裁看得到全場，
       但他看過不等於下級看過。

    ⚠ 已知限制：`readAt` 是**每封信一格**，不是每人一格。發給整個陣營的信只要有一位
    參謀標了已讀，全陣營就都算已讀。要做到逐人已讀需要新的關聯表（DB 變更），
    這一輪不動 DB。
    """
    part, _ = _require_member(db, session_id, user)
    if part is None:
        # 全知但未加入本局＝沒有收件身分，不能替任何人宣稱已讀（與發信同一道理）。
        raise AuthForbiddenError("全知角色未加入本局，無法標示已讀")
    # 未讀的才撈（長局的信文匣會很長，沒必要把已讀的也拉進記憶體）；
    # 指定 id 時再收窄一次。受眾判定仍在 Python 側——那是與 WS 共用的同一份規則，
    # 不在這裡另寫一套 SQL 版（兩份受眾實作會漂，WP-C5 已經有前例）。
    wanted = set(req.message_ids) if req.message_ids is not None else None
    stmt = select(Message).where(
        Message.session_id == session_id,
        Message.read_at.is_(None),
        Message.from_user_id != part.user_id,  # 寄件備份不算已讀
    )
    if wanted is not None:
        stmt = stmt.where(Message.id.in_(wanted))
    rows = db.scalars(stmt).all()
    # 牆鐘時戳：這是「操作員何時看到」，屬 API 層事實，不是模擬時間（模擬側一律 SimClock）。
    now = datetime.now(UTC).replace(tzinfo=None)
    marked: list[str] = []
    for m in rows:
        if not _is_addressee(m, part):
            continue
        m.read_at = now
        marked.append(m.id)
    if marked:
        db.commit()
        # 讓寄件者那側也知道信被讀了（收件方的面板自己會重載）。受眾收窄到**寄件席位**，
        # 未指派席位時退回整個收件陣營——推播失敗不影響已讀本身（已落庫，重開面板仍看得到）。
        marked_set = set(marked)
        for m in rows:
            if m.id in marked_set:
                _push(
                    settings,
                    session_id,
                    "C2_MESSAGE_READ",
                    {"message_id": m.id},
                    m.to_faction,
                    m.from_seat.value if m.from_seat is not None else None,
                )
    return MarkReadResult(marked=marked, read_at=now if marked else None)


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
        # 位置與存活一律以**熱狀態**為準：活模擬只寫熱狀態，DB 的 current_lat/lng
        # 停在開局位置——只讀 DB 的話問的是「開局時看得到嗎」。
        if not has_observer_on(
            db,
            session_id,
            part.faction,
            (float(lat), float(lng)),
            gateway,
            live_state=_live_units(settings, db, session_id, part.faction),
            # WP-C10.1 修正：盟軍的前觀也算——SPEC 寫「任一友軍」。
            relations=load_session_relations(db, session_id),
        ):
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
    _ledger(
        session_id,
        LedgerEvent(
            event_type="REQUEST_SUBMITTED",
            tick=r.requested_at_tick or 0,
            ai_decision={
                "request_id": r.id,
                "kind": r.kind.value,
                "faction": r.faction,
                "requested_by": r.requested_by_id,
                "requested_seat": r.requested_seat.value if r.requested_seat else None,
                # 配額用罄會直接落 DENIED（不是拒收）——那一刻要在帳本上看得到。
                "status": r.status.value,
            },
        ),
    )
    return _req_view(db, r)


def _ledger(session_id: str, event: LedgerEvent) -> None:
    """把 C2 工件的流轉落進事件帳本。

    **申請與核覆過去只寫 `Request` 與 `Message` 兩張關聯表**，`/aar/*` 一律讀不到，
    於是 AAR 敘事講不出「這一發是誰批的」——而那正是四席位 CPX 最該評量的一件事。
    落帳失敗不可以擋住核覆本身：C2 的權威在關聯表，帳本是給檢討用的第二份紀錄。
    """
    with contextlib.suppress(Exception):
        LedgerWriter(default_session_factory()).append(session_id, [event])


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
    _ledger(
        session_id,
        LedgerEvent(
            event_type="REQUEST_DECIDED",
            tick=r.decided_at_tick or 0,
            ai_decision={
                "request_id": r.id,
                "kind": r.kind.value,
                "faction": r.faction,
                "status": r.status.value,
                "decided_by": r.decided_by_id,
                "note": (r.decision_note or "")[:200],
            },
        ),
    )
    return _req_view(db, r)
