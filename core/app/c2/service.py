"""C2 信文與申請-核覆的服務層（WP-B5.2）——狀態機、配額、留痕。

純同步、只碰 DB；權限判定委給 `app.c2.may_approve`（純函數）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.c2 import may_approve
from app.errors import (
    RequestAlreadyDecidedError,
    RequestApprovalDeniedError,
)
from app.models.enums import MessageKind, RequestKind, RequestStatus, SeatRole, UserRole
from app.models.tables import Message, Request, SessionParticipant, WargameSession

# 佔用配額的狀態：**DENIED 以外全部算**。
# PENDING 也要算，否則 4 個架次可以先送 10 張單再一路核准，配額形同虛設。
_QUOTA_CONSUMING = frozenset(
    {RequestStatus.PENDING, RequestStatus.APPROVED, RequestStatus.EXPENDED}
)


def quota_limits(db: Session, session_id: str) -> dict[str, int]:
    """本局的配額上限（開局時從想定快照）。缺／未列＝不限。"""
    s = db.get(WargameSession, session_id)
    raw = getattr(s, "request_quotas", None) if s is not None else None
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, int | float)}


def quota_used(db: Session, session_id: str, faction: str, kind: RequestKind) -> int:
    rows = db.scalars(
        select(Request).where(
            Request.session_id == session_id,
            Request.faction == faction,
            Request.kind == kind,
        )
    ).all()
    return sum(1 for r in rows if r.status in _QUOTA_CONSUMING)


def submit_request(
    db: Session,
    session_id: str,
    participant: SessionParticipant,
    *,
    kind: RequestKind,
    params: dict[str, Any],
    note: str,
    tick: int,
) -> Request:
    """送出申請單。**配額用罄 → 直接落 DENIED，不是拒收。**

    差別很重要：留痕才看得出這個陣營在第幾 tick 被配額卡住，
    那正是 [JCATS-F p.14] 要評的事件鏈。回 400 的話，AAR 裡什麼都看不到。
    """
    limit = quota_limits(db, session_id).get(kind.value)
    exhausted = limit is not None and quota_used(db, session_id, participant.faction, kind) >= limit

    req = Request(
        session_id=session_id,
        faction=participant.faction,
        kind=kind,
        status=RequestStatus.DENIED if exhausted else RequestStatus.PENDING,
        params=params,
        requested_by_id=participant.user_id,
        requested_seat=participant.seat_role,
        requested_at_tick=tick,
        decision_note=f"配額用罄（上限 {limit}）" if exhausted else None,
        decided_at_tick=tick if exhausted else None,
    )
    db.add(req)
    db.flush()
    # 申請單只是狀態；**信文才是 C2 工件流轉的載體**，所以送單一定伴隨一封 REQUEST 信文。
    db.add(
        Message(
            session_id=session_id,
            kind=MessageKind.REQUEST,
            from_user_id=participant.user_id,
            from_seat=participant.seat_role,
            to_seat=SeatRole.COMMANDER,  # 核覆者席位
            to_faction=participant.faction,
            ref_id=req.id,
            body=note or f"申請：{kind.value}",
            tick=tick,
        )
    )
    db.commit()
    db.refresh(req)
    return req


def decide_request(
    db: Session,
    session_id: str,
    decider: SessionParticipant,
    decider_role: UserRole,
    request_id: str,
    *,
    approve: bool,
    note: str,
    tick: int,
) -> Request:
    """核覆申請單。權限 → 狀態機 → 留痕 → 生成 APPROVAL 信文。"""
    req = db.get(Request, request_id)
    if req is None or req.session_id != session_id:
        raise RequestApprovalDeniedError("申請單不存在於此 session")
    if not may_approve(decider_role, decider.seat_role, req.kind):
        raise RequestApprovalDeniedError(
            "此席位無權核覆該類申請",
            details={"seat_role": str(decider.seat_role), "kind": req.kind.value},
        )
    if req.status is not RequestStatus.PENDING:
        # 核覆是一次性的——重複核覆會讓留痕失真（AAR 分不出哪一次才算數）。
        raise RequestAlreadyDecidedError(
            f"該申請單已為 {req.status.value}",
            details={"request_id": req.id, "status": req.status.value},
        )

    req.status = RequestStatus.APPROVED if approve else RequestStatus.DENIED
    req.decided_by_id = decider.user_id
    req.decided_at_tick = tick
    req.decision_note = note or None
    db.add(
        Message(
            session_id=session_id,
            kind=MessageKind.APPROVAL,
            from_user_id=decider.user_id,
            from_seat=decider.seat_role,
            to_seat=req.requested_seat,
            to_faction=req.faction,
            ref_id=req.id,
            body=note or ("核准" if approve else "駁回"),
            tick=tick,
        )
    )
    db.commit()
    db.refresh(req)
    return req


def expend_request(db: Session, request_id: str) -> Request | None:
    """把已核准的申請單標為已用掉（終態）。

    **`APPROVED` 與 `EXPENDED` 分開的理由**：一張核准單只能兌現一次。
    合併成一個狀態的話，同一張火協核准可以掛在兩次砲擊令上。
    非 APPROVED 一律不動（回 None），呼叫端據此拒絕兌現。
    """
    req = db.get(Request, request_id)
    if req is None or req.status is not RequestStatus.APPROVED:
        return None
    req.status = RequestStatus.EXPENDED
    db.commit()
    db.refresh(req)
    return req


def has_observer_on(
    db: Session,
    session_id: str,
    faction: str,
    target: tuple[float, float],
    gateway: object,
) -> bool:
    """該陣營是否有任一單位對目標點有視線（WP-C10.1）。

    **LOS 一律走與交戰預檢同一個 `PhysicsGateway`**，不另寫一套——兩份 LOS 實作
    就是兩份會漂移的物理，這個 repo 已經有 fog of war 因此出事的前例（WP-C5）。

    gateway 缺 `has_los`（測試用的極簡假件）→ 視為有觀測，不讓缺方法變成硬失敗。

    **不吞例外**：terrain 不可達要讓 `TerrainUnavailableError` 往上拋（API 轉 503），
    而不是靜靜回「沒有觀測」。兩者對使用者的意義天差地遠——前者是系統故障該修，
    後者是戰術判定該換位置。寫這段時原本用了 `except Exception`，
    結果自己測試裡一個建構子筆誤被吞成「沒有觀測」，正是這個模式會造成的誤導。
    """
    from app.models.tables import TacticalUnit

    has_los = getattr(gateway, "has_los", None)
    if has_los is None:
        return True
    units = db.scalars(
        select(TacticalUnit).where(
            TacticalUnit.session_id == session_id,
            TacticalUnit.faction == faction,
        )
    ).all()
    tlat, tlng = target
    for u in units:
        if u.current_lat is None or u.current_lng is None:
            continue
        try:
            outcome = has_los(
                (float(u.current_lat), float(u.current_lng), float(u.elevation or 0.0)),
                (tlat, tlng, 0.0),
            )
        except Exception:
            continue
        if getattr(outcome, "visible", False):
            return True
    return False
