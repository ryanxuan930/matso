"""C2 申請-核覆服務層（WP-B5.2）——狀態機、配額、留痕。"""

from __future__ import annotations

import pytest
from _order_fakes import seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.c2.service import (
    decide_request,
    expend_request,
    quota_used,
    submit_request,
)
from app.errors import RequestAlreadyDecidedError, RequestApprovalDeniedError
from app.models.enums import MessageKind, RequestKind, RequestStatus, SeatRole, UserRole
from app.models.tables import Message, Request, SessionParticipant, WargameSession


def _submit(factory, world, *, kind=RequestKind.AIR_RECON, tick=10):  # type: ignore[no-untyped-def]
    with factory() as db:
        p = db.get(SessionParticipant, world.blue_issuer_id)
        assert p is not None
        return submit_request(db, world.session_id, p, kind=kind, params={}, note="", tick=tick).id


def _set_quota(factory, world, quotas) -> None:  # type: ignore[no-untyped-def]
    with factory() as db:
        s = db.get(WargameSession, world.session_id)
        assert s is not None
        s.request_quotas = quotas
        db.commit()


def _decide(  # type: ignore[no-untyped-def]
    factory,
    world,
    rid,
    *,
    approve=True,
    issuer=None,
    role=UserRole.COMMANDER,
    seat=SeatRole.COMMANDER,
):
    with factory() as db:
        p = db.get(SessionParticipant, issuer or world.blue_issuer_id)
        assert p is not None
        p.seat_role = seat
        db.flush()
        return decide_request(
            db, world.session_id, p, role, rid, approve=approve, note="", tick=20
        ).status


def test_submit_creates_pending_and_a_request_message(
    session_factory: sessionmaker[Session],
) -> None:
    """申請單只是狀態；**信文才是 C2 工件流轉的載體**，故送單必伴一封 REQUEST 信文。"""
    world = seed_world(session_factory)
    rid = _submit(session_factory, world)
    with session_factory() as db:
        req = db.get(Request, rid)
        assert req is not None and req.status is RequestStatus.PENDING
        msgs = db.query(Message).filter(Message.ref_id == rid).all()
        assert [m.kind for m in msgs] == [MessageKind.REQUEST]
        assert msgs[0].to_seat is SeatRole.COMMANDER  # 送到核覆者席位


def test_approve_moves_to_approved_and_leaves_a_trail(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    rid = _submit(session_factory, world)
    assert _decide(session_factory, world, rid, approve=True) is RequestStatus.APPROVED
    with session_factory() as db:
        req = db.get(Request, rid)
        assert req is not None
        # 留痕：誰、第幾 tick——AAR 要靠這個重建事件鏈
        assert req.decided_by_id and req.decided_at_tick == 20
        kinds = [m.kind for m in db.query(Message).filter(Message.ref_id == rid).all()]
        assert MessageKind.APPROVAL in kinds


def test_deny_moves_to_denied(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    rid = _submit(session_factory, world)
    assert _decide(session_factory, world, rid, approve=False) is RequestStatus.DENIED


def test_double_decide_rejected(session_factory: sessionmaker[Session]) -> None:
    """核覆是一次性的——重複核覆會讓留痕失真（AAR 分不出哪一次算數）。"""
    world = seed_world(session_factory)
    rid = _submit(session_factory, world)
    _decide(session_factory, world, rid, approve=True)
    with pytest.raises(RequestAlreadyDecidedError):
        _decide(session_factory, world, rid, approve=False)


def test_staff_seat_may_not_decide(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    rid = _submit(session_factory, world)
    with pytest.raises(RequestApprovalDeniedError):
        _decide(session_factory, world, rid, role=UserRole.STAFF, seat=SeatRole.S3_OPS)


def test_quota_exhausted_auto_denies_instead_of_refusing(
    session_factory: sessionmaker[Session],
) -> None:
    """**配額用罄要落 DENIED，不是拒收。**

    留痕才看得出這個陣營在第幾 tick 被配額卡住——那正是 AAR 要評的事件鏈。
    回 400 的話 AAR 裡什麼都看不到。
    """
    world = seed_world(session_factory)
    _set_quota(session_factory, world, {"AIR_RECON": 1})
    first = _submit(session_factory, world)
    second = _submit(session_factory, world, tick=11)
    with session_factory() as db:
        assert db.get(Request, first).status is RequestStatus.PENDING  # type: ignore[union-attr]
        over = db.get(Request, second)
        assert over is not None
        assert over.status is RequestStatus.DENIED  # 有留下來，不是被拒收
        assert over.decided_at_tick == 11
        assert "配額" in (over.decision_note or "")


def test_pending_counts_against_quota(session_factory: sessionmaker[Session]) -> None:
    """PENDING 也佔配額——否則 4 架次可以先送 10 張單再一路核准，配額形同虛設。"""
    world = seed_world(session_factory)
    _set_quota(session_factory, world, {"AIR_RECON": 2})
    _submit(session_factory, world)
    _submit(session_factory, world, tick=11)
    with session_factory() as db:
        assert quota_used(db, world.session_id, "BLUE", RequestKind.AIR_RECON) == 2


def test_denied_does_not_consume_quota(session_factory: sessionmaker[Session]) -> None:
    """被駁回的申請不該佔用架次。"""
    world = seed_world(session_factory)
    _set_quota(session_factory, world, {"AIR_RECON": 2})
    rid = _submit(session_factory, world)
    _decide(session_factory, world, rid, approve=False)
    with session_factory() as db:
        assert quota_used(db, world.session_id, "BLUE", RequestKind.AIR_RECON) == 0


def test_no_quota_declared_means_unlimited(session_factory: sessionmaker[Session]) -> None:
    """未宣告配額＝不限（既有局零變更）。"""
    world = seed_world(session_factory)
    for i in range(5):
        rid = _submit(session_factory, world, tick=10 + i)
        with session_factory() as db:
            assert db.get(Request, rid).status is RequestStatus.PENDING  # type: ignore[union-attr]


def test_approved_can_be_expended_exactly_once(session_factory: sessionmaker[Session]) -> None:
    """**APPROVED 與 EXPENDED 分開的理由**：一張核准單只能兌現一次。

    合併成一個狀態的話，同一張火協核准可以掛在兩次砲擊令上。
    """
    world = seed_world(session_factory)
    rid = _submit(session_factory, world)
    _decide(session_factory, world, rid, approve=True)
    with session_factory() as db:
        assert expend_request(db, rid) is not None
    with session_factory() as db:
        assert expend_request(db, rid) is None  # 第二次兌現不成立
        assert db.get(Request, rid).status is RequestStatus.EXPENDED  # type: ignore[union-attr]


def test_pending_cannot_be_expended(session_factory: sessionmaker[Session]) -> None:
    """沒核准就想兌現＝不成立。"""
    world = seed_world(session_factory)
    rid = _submit(session_factory, world)
    with session_factory() as db:
        assert expend_request(db, rid) is None
