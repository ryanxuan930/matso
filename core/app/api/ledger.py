"""帳本查詢端點（UI-P3）——事後爭議裁決要看得到原始證據。

`GET /api/v1/sessions/{id}/ledger`

## 為什麼要有這個

契約從很早就宣告了這條路徑，**但後端一直是 404**——那是「契約說謊」，
比缺功能難查：前端拿得到型別、按下去吃 404，而所有閘門都是綠的。
同時 DB 裡躺著二十幾萬筆事件，事後有人問「那一發到底打到誰」只能請工程師去撈 DB。

## 與 AAR 的關係：同一套投影，不同的時間軸取捨

**受眾與欄位迷霧整套沿用 `aar/fog.project_events`**，一行都不另寫。
WP-C5 的教訓就是同一套規則散在多處實作，最後其中一處漏掉了 fog of war；
`aar/fog` 的模組說明也把那件事寫在最前面。這裡是第三個消費端，仍然只借不抄。

**唯一刻意的差別是回滾世代**：`aar.read_events` 會排除被白軍回滾棄置的事件
（ADR 007）——AAR 要一條連貫的敘事，把死掉的世代算進去會讓戰損重複計算。
但稽核要的是**完整證據**：回滾本身就可能是爭議的標的（「你是不是把那一發洗掉了」）。
所以這裡兩者都回，以 `superseded` 標示，讓查的人自己決定。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aar.events import AarEvent, _to_aar
from app.aar.fog import project_events
from app.api.aar import _unit_faction, require_aar_access
from app.api.deps import get_current_user, get_db
from app.auth.schemas import CurrentUser
from app.models import TacticalEventLog
from app.state.ledger import superseded_seqs

router = APIRouter(prefix="/api/v1/sessions", tags=["ledger"])

# 單頁上限。二十幾萬筆的帳本不能一次吐——但也不要小到讓稽核翻十萬頁。
_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000


class LedgerEntry(BaseModel):
    seq: int
    tick: int
    event_type: str
    initiator_id: str | None = None
    target_id: str | None = None
    damage_calc: float | None = None
    ai_decision: dict = Field(default_factory=dict)  # type: ignore[type-arg]
    detail: dict = Field(default_factory=dict)  # type: ignore[type-arg]
    superseded: bool = False


class LedgerPage(BaseModel):
    events: list[LedgerEntry]
    next_after_seq: int | None = None
    has_more: bool = False


def _parse_types(raw: str | None) -> set[str] | None:
    """`?types=A,B` → 白名單集合。空/全空白 → None（不篩）。

    **在 SQL 層篩**而不是取回來再過濾：篩掉九成的查詢不該把九成的資料先搬進記憶體。
    """
    if not raw:
        return None
    wanted = {t.strip() for t in raw.split(",") if t.strip()}
    return wanted or None


@router.get("/{session_id}/ledger", response_model=LedgerPage)
def query_ledger(
    session_id: str,
    after_seq: int = Query(default=0, ge=0),
    types: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LedgerPage:
    """帳本分頁查詢（faction-scoped）。

    存取規則與 AAR 完全相同（`require_aar_access`）：參與者、ANALYST、全知。
    回傳的 `viewer_faction is None` ＝不受迷霧限制。
    """
    viewer_faction = require_aar_access(db, user, session_id)

    stmt = (
        select(TacticalEventLog)
        .where(TacticalEventLog.session_id == session_id, TacticalEventLog.seq > after_seq)
        .order_by(TacticalEventLog.seq)
    )
    wanted = _parse_types(types)
    if wanted is not None:
        stmt = stmt.where(TacticalEventLog.event_type.in_(wanted))
    # 多取一筆用來判斷 has_more——比再打一次 COUNT 便宜，而且不會有兩次查詢之間
    # 又寫入新事件造成的不一致（帳本是 append-only，多取一筆永遠安全）。
    rows = list(db.execute(stmt.limit(limit + 1)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        return LedgerPage(events=[], next_after_seq=None, has_more=False)

    # ⚠ `superseded_seqs` 要看**整個 session** 的事件才算得出來（回滾標記可能落在本頁之外），
    # 只餵本頁的話，跨頁的回滾會被漏標——而漏標的方向是「把死掉的世代呈現成有效證據」。
    all_rows = (
        db.execute(
            select(TacticalEventLog)
            .where(TacticalEventLog.session_id == session_id)
            .order_by(TacticalEventLog.seq)
        )
        .scalars()
        .all()
    )
    dead = superseded_seqs(all_rows)

    events: list[AarEvent] = [_to_aar(r) for r in rows]
    if viewer_faction is not None:
        events = project_events(
            events,
            faction=viewer_faction,
            omniscient=False,
            faction_for=_unit_faction(db, session_id),
        )

    return LedgerPage(
        events=[
            LedgerEntry(
                seq=e.seq,
                tick=e.tick,
                event_type=e.event_type,
                initiator_id=e.initiator_id,
                target_id=e.target_id,
                damage_calc=e.damage_calc,
                ai_decision=e.ai_decision,
                detail=e.detail,
                superseded=e.seq in dead,
            )
            for e in events
        ],
        # **游標取自投影前的最後一筆**：迷霧可能把本頁最後幾筆整個剔掉，
        # 拿投影後的最後一筆當游標會讓下一頁從錯的位置開始，永遠翻不完。
        next_after_seq=rows[-1].seq,
        has_more=has_more,
    )
