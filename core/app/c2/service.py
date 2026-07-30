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


def _observer_factions(db: Any, session_id: str, faction: str, relations: Any) -> list[str]:
    """算得上「友軍觀測者」的陣營集（自己 + 盟軍），依名稱排序＝確定性。

    `relations` 為 None → 只回自己（既有行為，既有呼叫端不受影響）。
    """
    if relations is None:
        return [faction]
    from app.adjudication.fratricide import is_friendly
    from app.models.tables import TacticalUnit

    declared = db.scalars(
        select(TacticalUnit.faction).where(TacticalUnit.session_id == session_id).distinct()
    ).all()
    return sorted({f for f in declared if f and is_friendly(relations, faction, f)} | {faction})


def has_observer_on(
    db: Session,
    session_id: str,
    faction: str,
    target: tuple[float, float],
    gateway: object,
    live_state: Mapping[str, Mapping[str, Any]] | None = None,
    relations: Any = None,
) -> bool:
    """該陣營**或其盟軍**是否有任一存活單位對目標點有視線（WP-C10.1）。

    ⚠ **原本只認自己陣營**（`TacticalUnit.faction == faction`），而 SPEC 寫的是
    「任一友軍」、關係矩陣也讓盟軍互相可見——於是聯軍作戰時，盟軍的前觀看得到目標，
    本軍卻叫不動火力。`relations` 未注入時退回只認自己陣營（既有行為）。

    **LOS 一律走與交戰預檢同一個 `PhysicsGateway`**，不另寫一套——兩份 LOS 實作
    就是兩份會漂移的物理，這個 repo 已經有 fog of war 因此出事的前例（WP-C5）。

    gateway 缺 `has_los`（測試用的極簡假件）→ 視為有觀測，不讓缺方法變成硬失敗。

    **例外一律往上拋**（Backlog 清理，2026-07-31 修）：`TerrainUnavailableError` 要讓
    API 轉 503，而不是靜靜回「沒有觀測」——「系統故障」與「戰術上沒人看得到」對使用者的
    意義天差地遠，前者該修系統、後者該換觀測位置。
    docstring 本來就是這麼寫的，但**程式碼裡有一段 `except Exception: continue` 一直在
    做相反的事**。文件說一套、程式做一套，是最難查的那種不一致。

    ⚠ 與 tick 側的 `engine/fire_wiring.observer_verdict` 刻意不同：那裡**必須**吞例外
    （回 UNKNOWN），因為 `run_tick` 沒有防護，一個例外會讓 runner 崩潰後每 3 秒被重建。
    這裡是 API 路徑，失敗大聲一點才對。

    `live_state`＝熱狀態切片（unit_id → state）。**活模擬只寫熱狀態，從不寫
    `TacticalUnit.current_lat/lng`**，所以只讀 DB 的話問的是開局位置，
    單位跑到哪裡去都不影響判定。有給就以它為準，缺鍵才退回 DB 列。
    """
    from app.models.tables import TacticalUnit

    has_los = getattr(gateway, "has_los", None)
    if has_los is None:
        return True
    # 盟軍也算觀測者（見 docstring）。未注入 relations → 只認自己陣營。
    factions = _observer_factions(db, session_id, faction, relations)
    units = db.scalars(
        select(TacticalUnit).where(
            TacticalUnit.session_id == session_id,
            TacticalUnit.faction.in_(factions),
        )
    ).all()
    tlat, tlng = target
    live = live_state or {}
    for u in units:
        state = live.get(u.id) or {}
        # **死掉的單位不是觀測者。**沒有這道過濾的話，一個被打光的前觀還會替全陣營叫火力。
        strength = state.get("strength", u.current_strength)
        if isinstance(strength, (int, float)) and strength <= 0:
            continue
        lat = state.get("lat", u.current_lat)
        lng = state.get("lng", u.current_lng)
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        outcome = has_los(
            (float(lat), float(lng), float(u.elevation or 0.0)),
            (tlat, tlng, 0.0),
        )
        if getattr(outcome, "visible", False):
            return True
    return False
